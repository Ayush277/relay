"""Measure the REAL Groq service. Needs GROQ_API_KEY (your own free key) in the environment; never writes it anywhere.
Spends roughly 150 requests on the small model and ~35 on the big one (well under 1,000 RPD). Output: raw/probe_real.jsonl,
raw/limits.json (used by the client), raw/probe_summary.json (Table 1)."""
import asyncio, json, os, re, sys, time, statistics as stats, aiohttp
from relay.config import *
from relay.llm import parse_dur
from relay.pipeline import SYS_DRAFT
from relay.retrieval import get_index
from relay.evalset import QS

if "groq.com" not in BASE_URL or not os.getenv("GROQ_API_KEY"):
    sys.exit("Set RELAY_BASE_URL=https://api.groq.com/openai/v1 and GROQ_API_KEY=<your free key> first.")
OUT = RAW / "probe_real.jsonl"
H = {"Authorization": "Bearer " + API_KEY}
RL = ("x-ratelimit-limit-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens",
      "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "retry-after")
def log(**k): 
    with OUT.open("a") as f: f.write(json.dumps(k) + "\n")

def realistic_prompt(qid):
    ix = get_index(DEFAULT_SETTING); ch, _ = ix.search(QS[qid]["q"], 4, True)
    ctx = "\n".join(f"[{c['id']}] {c['text']}" for c in ch)
    return [dict(role="system", content=SYS_DRAFT), dict(role="user", content=f"Passages:\n{ctx}\n\nQuestion: {QS[qid]['q']}")]

async def one(sess, model, msgs, max_tokens=120, tag="", stream=True):
    t0 = time.perf_counter(); ttft = None; txt = ""; usage = None
    body = dict(model=model, messages=msgs, max_tokens=max_tokens, temperature=0, stream=stream, stream_options=dict(include_usage=True) if stream else None)
    if 'gpt-oss' in model: body.update(reasoning_effort='low', include_reasoning=False)
    body = {k: v for k, v in body.items() if v is not None}
    try:
        async with sess.post(BASE_URL + "/chat/completions", json=body, headers=H, timeout=aiohttp.ClientTimeout(total=60)) as r:
            hd = {k: r.headers.get(k) for k in RL}
            if r.status != 200:
                b = await r.text(); rec = dict(tag=tag, model=model, status=r.status, t=time.time(), total_ms=(time.perf_counter() - t0) * 1000, headers=hd, body=b[:600])
                log(**rec); return rec
            async for line in r.content:
                line = line.decode().strip()
                if not line.startswith("data:") or line.endswith("[DONE]"): continue
                j = json.loads(line[5:])
                u = j.get("usage") or (j.get("x_groq") or {}).get("usage")
                if u: usage = u
                for ch in j.get("choices", []):
                    d = (ch.get("delta") or {}).get("content")
                    if d:
                        if ttft is None: ttft = (time.perf_counter() - t0) * 1000
                        txt += d
    except Exception as e:
        rec = dict(tag=tag, model=model, status="exc", t=time.time(), total_ms=(time.perf_counter() - t0) * 1000, body=repr(e)); log(**rec); return rec
    rec = dict(tag=tag, model=model, status=200, t=time.time(), ttft_ms=ttft, total_ms=(time.perf_counter() - t0) * 1000, usage=usage, headers=hd, out=txt[:80])
    log(**rec); return rec

async def main():
    OUT.write_text("")
    async with aiohttp.ClientSession() as s:
        async with s.get(BASE_URL + "/models", headers=H) as r:
            ms = (await r.json()).get("data", []); log(tag="models", ids=[m["id"] for m in ms])
            print("models:", [m["id"] for m in ms][:30])
        # 1. latency distribution, realistic prompts, spaced so we stay under RPM
        for model, n in ((SMALL, 25), (BIG, 20)):
            qids = list(QS)
            for i in range(n):
                rec = await one(s, model, realistic_prompt(qids[i % len(qids)]), tag="latency"); 
                print(model, i, rec.get("status"), round(rec.get("ttft_ms") or 0), round(rec["total_ms"]), flush=True)
                await asyncio.sleep(13)
        # 2. concurrency ramp on the small model with tiny prompts
        for c in (1, 2, 4, 8, 16):
            recs = await asyncio.gather(*[one(s, SMALL, [dict(role="user", content="Say ok")], 5, tag=f"conc{c}") for _ in range(c)])
            print("concurrency", c, [r["status"] for r in recs]); await asyncio.sleep(35)
        # 3. RPM: fire tiny requests back-to-back until the first 429; then poll recovery
        t_first = time.time(); ok = 0; first429 = None
        for i in range(120):
            rec = await one(s, SMALL, [dict(role="user", content="Say ok")], 2, tag="rpm_ramp")
            if rec["status"] == 429: first429 = rec; break
            ok += 1
        log(tag="rpm_result", ok_before_429=ok, elapsed_s=time.time() - t_first, first429=first429)
        print("RPM probe: ok before first 429 =", ok, first429 and first429.get("body"))
        t_rec = time.time(); rec_after = None
        while time.time() - t_rec < 90:
            await asyncio.sleep(1.0)
            rec = await one(s, SMALL, [dict(role="user", content="Say ok")], 2, tag="rpm_recovery")
            if rec["status"] == 200: rec_after = time.time() - t_rec; break
        log(tag="rpm_recovery_result", seconds=rec_after); print("recovered after", rec_after)
        # 4. TPM: one oversized request (> TPM) to read the limit message
        await asyncio.sleep(65)
        big = "word " * 9000
        rec = await one(s, SMALL, [dict(role="user", content=big + "\nSay ok")], 5, tag="tpm_oversize"); print("oversize:", rec.get("status"), rec.get("body"))
        # 5. does a rejected request still count against RPM? (burst to 429, wait exactly retry-after, test again)
    summarize()

def summarize():
    recs = [json.loads(l) for l in OUT.read_text().splitlines()]
    lim = {}; summ = {}
    for r in recs:
        if r.get("status") == 200 and r.get("headers"):
            h = r["headers"]; m = r["model"]
            d = lim.setdefault(m, {})
            if h.get("x-ratelimit-limit-requests"): d["rpd"] = int(h["x-ratelimit-limit-requests"])
            if h.get("x-ratelimit-limit-tokens"): d["tpm"] = int(h["x-ratelimit-limit-tokens"])
    for r in recs:
        if r.get("status") == 429:
            b = r.get("body", ""); m = r["model"]; d = lim.setdefault(m, {})
            for key, rx in (("rpm", r"\(RPM\): Limit (\d+)"), ("tpm", r"\(TPM\): Limit (\d+)"), ("rpd", r"\(RPD\): Limit (\d+)"), ("tpd", r"\(TPD\): Limit (\d+)")):
                mm = re.search(rx, b)
                if mm: d[key] = int(mm.group(1))
    rr = next((r for r in recs if r.get("tag") == "rpm_result"), {})
    if rr and SMALL in lim: lim[SMALL].setdefault("rpm_observed", rr["ok_before_429"])
    conc = {}
    for r in recs:
        if str(r.get("tag", "")).startswith("conc"): conc.setdefault(r["tag"], []).append(r["status"])
    summ = dict(limits=lim, concurrency=conc, rpm_result=rr, recovery=next((r for r in recs if r.get("tag") == "rpm_recovery_result"), {}),
                sample_429=next((r for r in recs if r.get("status") == 429), {}))
    (RAW / "probe_summary.json").write_text(json.dumps(summ, indent=1, default=str))
    # limits.json holds only what the client needs, with a 'rpm' from the RPM message if seen
    out = {}
    for m, d in lim.items():
        o = dict(DEFAULT_LIMITS.get(m, {}))
        for k in ("rpm", "tpm", "rpd", "tpd"):
            if k in d: o[k] = d[k]
        if "rpm" not in d and "rpm_observed" in d: o["rpm"] = d["rpm_observed"]
        out[m] = o
    (RAW / "limits.json").write_text(json.dumps(out, indent=1)); print(json.dumps(summ, indent=1, default=str)[:3000])

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "summarize": summarize()
    else: asyncio.run(main())
