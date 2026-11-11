"""Rate-limit-aware streaming client: per-model governor (RPM/TPM buckets, concurrency, deadline-ordered queue,
load shedding, daily-budget tracking) plus a retry policy. Works against Groq or the stand-in unchanged."""
import asyncio, heapq, json, math, random, re, time, itertools, aiohttp
from dataclasses import dataclass, field
from .config import BASE_URL, API_KEY, DEFAULT_LIMITS, PRICES, ROOT

def now(): return time.monotonic()

class Shed(Exception): """Would not be served before the deadline (or queue refused it)."""
class DailyExhausted(Exception):
    def __init__(s, retry_after): s.retry_after = retry_after
class CallFailed(Exception):
    def __init__(s, kind, detail=""): super().__init__(f"{kind}: {detail}"); s.kind = kind

def parse_dur(x):
    """'2m59.56s' / '7.66s' / '120ms' -> seconds"""
    if x is None: return None
    tot = 0.0
    for v, u in re.findall(r"([\d.]+)(ms|h|m|s)", x):
        tot += float(v) * dict(ms=0.001, s=1, m=60, h=3600)[u]
    return tot if tot else (float(x) if re.fullmatch(r"[\d.]+", x) else None)

@dataclass
class CallStats:
    queue_ms: float = 0; work_ms: float = 0; retry_ms: float = 0; llm_ttft_ms: float | None = None
    retries: int = 0; n429: int = 0; tokens_in: int = 0; tokens_out: int = 0; cost: float = 0.0; calls: int = 0
    def add(s, o):
        for f in ("queue_ms", "work_ms", "retry_ms", "retries", "n429", "tokens_in", "tokens_out", "cost", "calls"):
            setattr(s, f, getattr(s, f) + getattr(o, f))
        if s.llm_ttft_ms is None: s.llm_ttft_ms = o.llm_ttft_ms

class RetryBudget:
    """Retries may add at most `ratio` extra load on top of first attempts (plus a small floor)."""
    def __init__(s, ratio=0.2, cap=10.0, start=3.0): s.ratio, s.cap, s.v = ratio, cap, start
    def first_attempt(s): s.v = min(s.cap, s.v + s.ratio)
    def try_spend(s):
        if s.v >= 1: s.v -= 1; return True
        return False

class Gate:
    """One per model. Owns the local view of that model's limits."""
    def __init__(s, model, lim, margin=0.92):
        s.model, s.lim, s.margin = model, dict(lim), margin; s.scale = 1.0; s.last429 = 0.0
        s.rpm_v = s.R() * margin; s.tpm_v = s.T() * margin; s.t = now()
        s.inflight = 0; s.heap = []; s.seq = itertools.count(); s.pump_task = None
        s.pause_until = 0.0; s.day_blocked_until = 0.0; s.committed = 0.0   # tokens promised to plans not yet finished
        s.used_req = 0; s.used_tok = 0; s.peak_queue = 0; s.shed = 0
        s.remote_remaining = {}  # latest x-ratelimit-remaining-* seen
        s.ewma_service = 1.5; s.scale = 1.0; s.last429 = 0.0
    def R(s): return s.lim["rpm"] * s.scale
    def T(s): return s.lim["tpm"] * s.scale
    def refill(s):
        n = now(); dt = n - s.t; s.t = n
        if s.scale < 1.0 and n - s.last429 > 10: s.scale = min(1.0, s.scale + 0.05 * dt / 1.0 * 0.1)  # slow additive recovery
        s.rpm_v = min(s.R() * s.margin, s.rpm_v + dt * s.R() / 60)
        s.tpm_v = min(s.T() * s.margin, s.tpm_v + dt * s.T() / 60)
    def wait_for(s, cost):
        s.refill(); w = max(0.0, s.pause_until - now())
        if s.rpm_v < 1: w = max(w, (1 - s.rpm_v) * 60 / s.R())
        if s.tpm_v < cost: w = max(w, (min(cost, s.T() * s.margin) - s.tpm_v) * 60 / s.T())
        if s.inflight >= s.lim["inflight"]: w = max(w, 0.05)
        return w
    def estimate_wait(s, cost):
        """Seconds a new request would wait: its own deficit plus the cost already queued ahead of it."""
        s.refill(); ahead_cost = max(sum(h[2] for h in s.heap), s.committed); ahead_n = max(len(s.heap), int(s.committed // 2000))
        w_t = max(0.0, (ahead_cost + cost - s.tpm_v)) * 60 / s.T()
        w_r = max(0.0, (ahead_n + 1 - s.rpm_v)) * 60 / s.R()
        return max(w_t, w_r, s.pause_until - now())
    def day_fraction_left(s):
        return min(1 - s.used_req / s.lim["rpd"], 1 - s.used_tok / s.lim["tpd"])
    async def acquire(s, cost, deadline):
        n = now()
        if s.day_blocked_until > n: raise DailyExhausted(s.day_blocked_until - n)
        if s.used_req >= s.lim["rpd"] or s.used_tok + cost > s.lim["tpd"]:
            raise DailyExhausted(86400)
        if deadline - n < 0.2: s.shed += 1; raise Shed("no time left")
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(s.heap, (deadline, next(s.seq), cost, fut))
        s.peak_queue = max(s.peak_queue, len(s.heap))
        if s.pump_task is None or s.pump_task.done(): s.pump_task = asyncio.create_task(s._pump())
        try:
            await fut
        except asyncio.CancelledError:
            fut.cancel(); raise
    async def _pump(self):
        s = self
        while s.heap:
            dl, _, cost, fut = s.heap[0]
            if fut.done(): heapq.heappop(s.heap); continue
            if s.day_blocked_until > now():
                heapq.heappop(s.heap); fut.set_exception(DailyExhausted(s.day_blocked_until - now())); continue
            w = s.wait_for(cost)
            if now() + w > dl - 0.3:      # head cannot be served in time: shed it, degrade instead of waiting uselessly
                heapq.heappop(s.heap); s.shed += 1; fut.set_exception(Shed("deadline")); continue
            if w <= 0:
                heapq.heappop(s.heap); s.rpm_v -= 1; s.tpm_v -= cost; s.inflight += 1; s.used_req += 1
                fut.set_result(True); continue
            await asyncio.sleep(min(w, 0.05))
    def release(s, est_cost, actual_tokens):
        s.inflight = max(0, s.inflight - 1)
        s.tpm_v = min(s.T() * s.margin, s.tpm_v + max(0, est_cost - actual_tokens))
        s.used_tok += actual_tokens
    def note_429(s, retry_after, text):
        n = now(); s.rpm_v = min(s.rpm_v, 0); s.tpm_v = min(s.tpm_v, 0)
        if not re.search(r"per day|\(RPD\)|\(TPD\)", text or ""):
            s.scale = max(0.3, s.scale * 0.7); s.last429 = n   # multiplicative decrease: the limit may have been cut
        if retry_after and retry_after > 600 or re.search(r"per day|\(RPD\)|\(TPD\)", text or ""):
            s.day_blocked_until = n + max(retry_after or 0, 300)   # TPD is a rolling window: 'try again in 31s' frees ~70 tokens, not a whole answer
        else:
            s.pause_until = max(s.pause_until, n + (retry_after or 1.0))
    def note_headers(s, h):
        for k in ("remaining-requests", "remaining-tokens", "limit-requests", "limit-tokens", "reset-requests", "reset-tokens"):
            v = h.get("x-ratelimit-" + k)
            if v is not None: s.remote_remaining[k] = v
        rem = h.get("x-ratelimit-remaining-tokens")
        if rem is not None and float(rem) < s.tpm_v:   # trust the server when it says we have less than we think
            s.tpm_v = float(rem)

def estimate_tokens(messages): return sum(len(m["content"]) for m in messages) // 3 + 8   # conservative (chars/3)

class Client:
    def __init__(self, policy="smart", limits=None, base_url=BASE_URL, api_key=API_KEY):
        lim = limits or DEFAULT_LIMITS
        lf = ROOT / "raw" / "limits.json"
        if limits is None and lf.exists(): lim = {**lim, **json.loads(lf.read_text())}
        self.gates = {m: Gate(m, l) for m, l in lim.items()}
        self.policy, self.base, self.key = policy, base_url.rstrip("/"), api_key
        self.budget = RetryBudget(); self.session = None
        self.retry_log = []   # (t, model, attempt, reason, backoff)
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0)); return self
    async def __aexit__(self, *a): await self.session.close()

    async def call(self, model, messages, max_tokens, deadline, **kw):
        st = CallStats()
        try:
            text = await self._call(st, model, messages, max_tokens, deadline, **kw)
            return text, st
        except (asyncio.CancelledError, Exception) as e:
            e.stats = st; raise

    async def _call(self, st, model, messages, max_tokens, deadline, on_delta=None, stop_check=None,
                    ttft_timeout=8.0, stall_timeout=5.0, temperature=0.0, trace=None):
        g = self.gates[model]; text = ""
        tr = trace or (lambda *_: None)
        est = estimate_tokens(messages) + max_tokens // 2
        naive = self.policy == "naive"
        max_attempts = 1 if self.policy == "none" else (6 if naive else 3)
        self.budget.first_attempt()
        for attempt in range(max_attempts):
            t_q = now(); tr(f'{model.split("-")[1]} attempt {attempt+1}: queued')
            if not naive:
                await g.acquire(est, deadline)            # may raise Shed / DailyExhausted
            else:
                g.inflight += 1; g.used_req += 1
            st.queue_ms += (now() - t_q) * 1000; tr(f'{model.split("-")[1]} attempt {attempt+1}: sent')
            t_a = now(); first = None; fail = None; retry_after = None; ok = False; tin = est; tout = 0
            try:
                body = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
                            stream=True, stream_options=dict(include_usage=True))
                if 'gpt-oss' in model: body.update(reasoning_effort='low', include_reasoning=False)
                to = aiohttp.ClientTimeout(total=max(1.0, deadline - now()), sock_connect=3)
                async with self.session.post(self.base + "/chat/completions", json=body, timeout=to,
                                             headers={"Authorization": "Bearer " + self.key}) as r:
                    g.note_headers(r.headers)
                    if r.status == 429:
                        txt = await r.text(); retry_after = parse_dur(r.headers.get("retry-after")); fail = "rate_limited"
                        g.note_429(retry_after, txt)
                    elif r.status >= 500: fail = "error"; await r.read()
                    elif r.status != 200: raise CallFailed("error", f"HTTP {r.status} {(await r.text())[:200]}")
                    else:
                        buf = b""; text = ""
                        it = r.content.__aiter__()
                        while True:
                            tmo = (ttft_timeout if first is None else stall_timeout)
                            tmo = min(tmo, max(0.05, deadline - now()))
                            try: line = await asyncio.wait_for(it.__anext__(), tmo)
                            except StopAsyncIteration: break
                            except asyncio.TimeoutError: fail = "timeout"; break
                            line = line.decode().strip()
                            if not line.startswith("data:"): continue
                            d = line[5:].strip()
                            if d == "[DONE]": ok = True; break
                            j = json.loads(d)
                            u = j.get("usage") or (j.get("x_groq") or {}).get("usage")
                            if u: tin, tout = u.get("prompt_tokens", tin), u.get("completion_tokens", tout)
                            for ch in j.get("choices", []):
                                dl = (ch.get("delta") or {}).get("content")
                                if dl:
                                    if first is None:
                                        first = now(); st.llm_ttft_ms = st.llm_ttft_ms or (first - t_a) * 1000
                                    text += dl
                                    if on_delta: on_delta(text)
                                    if stop_check and stop_check(text):
                                        ok = True; fail = None; tout = tout or len(text) // 4
                                        raise _Stop()
            except _Stop:
                pass
            except asyncio.CancelledError:
                st.work_ms += (now() - t_a) * 1000; g.release(est, est); raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                fail = fail or ("timeout" if isinstance(e, asyncio.TimeoutError) else "error")
            finally:
                dur = (now() - t_a) * 1000
                if not tout and text: tout = len(text) // 4
                if not naive: g.release(est, (tin or est) + tout if (ok or text) else 0)
                else: g.inflight = max(0, g.inflight - 1)
            st.calls += 1; st.tokens_in += tin if (ok or text) else 0; st.tokens_out += tout
            pi, po = PRICES.get(model, (0, 0)); st.cost += (tin * pi + tout * po) / 1e6 if (ok or text) else 0
            if ok:
                st.work_ms += dur; g.ewma_service = 0.8 * g.ewma_service + 0.2 * dur / 1000; return text
            st.retry_ms += dur            # a failed attempt is time spent on retrying, not working
            # ---- retry policy ----
            if fail is None: fail = "error"
            if fail == "rate_limited": st.n429 += 1
            if attempt + 1 >= max_attempts: raise CallFailed(fail, "attempts exhausted")
            if naive: backoff = 0.0
            else:
                if fail == "rate_limited" and (g.day_blocked_until > now()): raise DailyExhausted(g.day_blocked_until - now())
                base = retry_after if retry_after else min(8.0, 0.5 * 2 ** attempt)
                backoff = base * (1.0 + 0.2 * random.random()) if retry_after else random.uniform(0, base)
                if now() + backoff + g.ewma_service > deadline: raise CallFailed(fail, "no time to retry")
                if not self.budget.try_spend(): raise CallFailed(fail, "retry budget empty")
            tr(f'{model.split("-")[1]} attempt {attempt+1} failed: {fail}, retry-after={retry_after}, backoff={backoff:.2f}s')
            st.retries += 1; self.retry_log.append((time.time(), model, attempt + 1, fail, backoff))
            t_b = now(); await asyncio.sleep(backoff); st.retry_ms += (now() - t_b) * 1000
        raise CallFailed("error", "unreachable")

class _Stop(Exception): pass
