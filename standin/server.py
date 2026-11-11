"""Stand-in for the model provider (OpenAI-compatible /v1/chat/completions, SSE streaming).
Can be slow, fail, refuse at RPM/TPM/RPD/TPD limits (Groq-style 429 + headers), slow down under load,
succeed slowly, hang mid-stream, and stream a piece at a time. Params live in params.json and can be
changed live via POST /admin/config. Content is generated extractively from the passages in the prompt,
so it tests plumbing and grounding checks, NOT real-model answer quality."""
import asyncio, json, math, random, re, sys, time, pathlib
from aiohttp import web

HERE = pathlib.Path(__file__).parent
DEFAULT = dict(
    seed=1,
    models={
        "openai/gpt-oss-20b": dict(rpm=30, tpm=8000, rpd=1000, tpd=200000, ttft_ms=180, ttft_sigma=0.45, tok_per_s=650),
        "openai/gpt-oss-120b": dict(rpm=30, tpm=8000, rpd=1000, tpd=200000, ttft_ms=280, ttft_sigma=0.5, tok_per_s=260),
    },
    prompt_ttft_ms_per_1k=40,      # extra time to first token per 1k prompt tokens
    inflight_cap=0,                # 0 = no hard concurrency cap
    knee=6, alpha=1.2,             # service slows by (1 + alpha*(inflight-knee)/knee) beyond the knee
    slow_p=0.03, slow_mult=8.0,    # "succeeds slowly": fraction of calls that take slow_mult x longer
    err_p=0.01,                    # fast 5xx
    hang_p=0.005, hang_s=30,       # accepts, streams a few tokens, then stalls
    bad_rate=0.04,                 # model emits an unsupported sentence (exercises the verifier)
    count_rejected=True,           # pessimistic ASSUMPTION: a 429d request still consumes RPM (real behaviour is probed separately)
    day_len_s=86400,
    tpm_counts_max_tokens=True,
)
P = json.loads(json.dumps(DEFAULT))
if (HERE / "params.json").exists():
    P.update(json.loads((HERE / "params.json").read_text()))
rng = random.Random(P["seed"])

class Bucket:
    def __init__(s, cap, per_min):
        s.cap, s.rate, s.v, s.t = cap, per_min / 60.0, cap, time.monotonic()
    def refill(s):
        n = time.monotonic(); s.v = min(s.cap, s.v + (n - s.t) * s.rate); s.t = n
    def take(s, x):
        s.refill()
        if s.v >= x:
            s.v -= x; return 0.0
        return (x - s.v) / s.rate
    def retune(s, per_min):
        s.refill(); s.cap, s.rate = per_min, per_min / 60.0; s.v = min(s.v, s.cap)

class M:  # per-model state
    def __init__(s, name, c):
        s.name, s.c = name, c
        s.rpm = Bucket(c["rpm"], c["rpm"]); s.tpm = Bucket(c["tpm"], c["tpm"])
        s.day_start = time.monotonic(); s.req_day = 0; s.tok_day = 0
    def day_left(s): return max(1, int(P["day_len_s"] - (time.monotonic() - s.day_start)))

STATE = {}; STATS = dict(inflight=0, peak_inflight=0, n=0, n429=0, n5xx=0, hung=0)
def init():
    STATE.clear()
    for k, c in P["models"].items(): STATE[k] = M(k, c)
    for k in STATS: STATS[k] = 0
init()

def hdrs(m):
    m.rpm.refill(); m.tpm.refill()
    return {"x-ratelimit-limit-requests": str(m.c["rpd"]), "x-ratelimit-limit-tokens": str(m.c["tpm"]),
            "x-ratelimit-remaining-requests": str(max(0, m.c["rpd"] - m.req_day)),
            "x-ratelimit-remaining-tokens": str(int(m.tpm.v)),
            "x-ratelimit-reset-requests": f"{m.day_left()}s",
            "x-ratelimit-reset-tokens": f"{max(0.0, (m.c['tpm'] - m.tpm.v) / m.tpm.rate):.2f}s"}

def err429(m, kind, retry, msg):
    STATS["n429"] += 1
    h = hdrs(m); h["retry-after"] = str(max(1, math.ceil(retry)))
    body = {"error": {"message": f"Rate limit reached for model `{m.name}` on {msg}. Please try again in {retry:.1f}s.",
                      "type": kind, "code": "rate_limit_exceeded"}}
    return web.json_response(body, status=429, headers=h)

STOPW = set("what which how does did the a an of to in is are was were and for on with by at from that this do".split())
def words(s): return [w for w in re.findall(r"[a-z0-9.]+", s.lower()) if w not in STOPW]

def generate(msgs):
    sysm = msgs[0]["content"]; user = msgs[-1]["content"]
    if "[task:understand]" in sysm:
        return json.dumps({"rewrite": user.strip(), "paper": "any"})
    passages = re.findall(r"^\[((?:att|mos|hnet)-p\d+-\d+)\] (.+)$", user, re.M)
    q = user.rsplit("Question:", 1)[-1]
    qw = set(words(q)); best = []
    for pid, text in passages:
        for s in re.split(r"(?<=[.!?])\s+", text):
            sw = set(words(s))
            n = len(s.split()); alpha = sum(ch.isalpha() or ch == ' ' for ch in s) / max(1, len(s))
            if len(sw) < 4 or n > 45 or alpha < 0.8: continue
            best.append((len(qw & sw) / (len(qw) or 1) - 0.002 * n, s.strip().rstrip("."), pid))
    best.sort(key=lambda x: -x[0])
    if not best or best[0][0] < 0.3:
        return "NOT_IN_PAPERS"
    out = [f"{s} [{pid}]." for _, s, pid in best[:2]]
    if rng.random() < P["bad_rate"]:
        out.append("It was also trained on 4096 TPU chips for 9 weeks [att-p1-0].")
    return " ".join(out)

def lognorm(med, sigma): return med * math.exp(rng.gauss(0, sigma))

async def chat(req):
    body = await req.json(); name = body.get("model")
    if name not in STATE:
        return web.json_response({"error": {"message": f"model {name} not found"}}, status=404)
    m = STATE[name]; STATS["n"] += 1
    msgs = body["messages"]
    ptok = sum(len(x["content"]) for x in msgs) // 4
    mx = int(body.get("max_tokens", 256))
    cost = ptok + (mx if P["tpm_counts_max_tokens"] else 0)
    if time.monotonic() - m.day_start > P["day_len_s"]:
        m.day_start = time.monotonic(); m.req_day = 0; m.tok_day = 0
    if m.req_day >= m.c["rpd"]:
        return err429(m, "requests", m.day_left(), "requests per day (RPD): Limit %d, Used %d" % (m.c["rpd"], m.req_day))
    if m.tok_day >= m.c["tpd"]:
        return err429(m, "tokens", m.day_left(), "tokens per day (TPD): Limit %d, Used %d" % (m.c["tpd"], m.tok_day))
    w = m.rpm.take(1)
    if w > 0:
        if P["count_rejected"]: m.rpm.v -= 1
        return err429(m, "requests", w, "requests per minute (RPM): Limit %d" % m.c["rpm"])
    w = m.tpm.take(cost)
    if w > 0:
        m.rpm.v += 1
        return err429(m, "tokens", w, "tokens per minute (TPM): Limit %d, Requested %d" % (m.c["tpm"], cost))
    if P["inflight_cap"] and STATS["inflight"] >= P["inflight_cap"]:
        return err429(m, "requests", 1, "concurrent requests")
    m.req_day += 1
    STATS["inflight"] += 1; STATS["peak_inflight"] = max(STATS["peak_inflight"], STATS["inflight"])
    try:
        load = 1 + P["alpha"] * max(0, STATS["inflight"] - P["knee"]) / P["knee"]
        r = rng.random()
        if r < P["err_p"]:
            await asyncio.sleep(0.05 + rng.random() * 0.2); STATS["n5xx"] += 1
            return web.json_response({"error": {"message": "internal error", "type": "server_error"}}, status=503)
        slow = P["slow_mult"] if r < P["err_p"] + P["slow_p"] else 1.0
        hang = rng.random() < P["hang_p"]
        text = generate(msgs)
        n_out = max(1, len(text) // 4)
        m.tok_day += ptok + n_out
        m.tpm.v += max(0, mx - n_out) if P["tpm_counts_max_tokens"] else 0   # unused reservation refunded
        resp = web.StreamResponse(headers={"content-type": "text/event-stream", **hdrs(m)})
        await resp.prepare(req)
        ttft = lognorm(m.c["ttft_ms"], m.c["ttft_sigma"]) + P["prompt_ttft_ms_per_1k"] * ptok / 1000
        await asyncio.sleep(ttft * load * slow / 1000)
        tpot = 1000.0 / m.c["tok_per_s"]
        step = 4  # tokens per SSE piece
        pieces = [text[i:i + step * 4] for i in range(0, len(text), step * 4)] or [""]
        for i, pc in enumerate(pieces):
            if hang and i == min(2, len(pieces) - 1):
                STATS["hung"] += 1; await asyncio.sleep(P["hang_s"])
            await resp.write(("data: " + json.dumps({"choices": [{"delta": {"content": pc}}]}) + "\n\n").encode())
            await asyncio.sleep(tpot * step * load * min(slow, 2.0) / 1000)
        await resp.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": ptok, "completion_tokens": n_out}}) + "\n\n").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp
    except (asyncio.CancelledError, ConnectionResetError):
        raise
    finally:
        STATS["inflight"] -= 1

async def admin_config(req):
    d = await req.json()
    for k, v in d.items():
        if k == "models":
            for mn, mc in v.items():
                P["models"][mn].update(mc); s = STATE[mn]; s.c = P["models"][mn]
                if "rpm" in mc: s.rpm.retune(mc["rpm"])
                if "tpm" in mc: s.tpm.retune(mc["tpm"])
        else:
            P[k] = v
    return web.json_response({"ok": True})
async def admin_reset(req):
    init(); return web.json_response({"ok": True})
async def admin_stats(req): return web.json_response(dict(STATS, **{k: dict(req_day=v.req_day, tok_day=v.tok_day) for k, v in STATE.items()}))
async def models(req): return web.json_response({"data": [{"id": k} for k in STATE]})

app = web.Application()
app.add_routes([web.post("/v1/chat/completions", chat), web.get("/v1/models", models),
                web.post("/admin/config", admin_config), web.post("/admin/reset", admin_reset), web.get("/admin/stats", admin_stats)])
if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8808, print=None)
