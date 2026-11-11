"""Load scenarios. Usage: python run_load.py [--only name ...] [--source auto]   (results appended to results.csv)"""
import argparse, asyncio, csv, json, random, time, sys, aiohttp, pathlib
from relay.config import *
from relay.pipeline import Pipeline, Req
from relay.llm import Client, now
from relay.evalset import QS, grade

CSV = ROOT / "results.csv"
COLS = ["request_id", "scenario", "source", "start_time", "first_token_ms", "total_ms", "status", "retries", "tokens_in", "tokens_out",
        "retrieval_setting", "answer_grade",
        "qid", "path", "mode", "cache_hit", "queue_ms", "work_ms", "retry_ms", "other_ms", "llm_ttft_ms", "calls", "n429", "cost_usd", "loops"]
HOT = ["q01", "q10", "q16", "q02", "q11"]
ADMIN = BASE_URL.rsplit("/v1", 1)[0]
IS_REAL = "groq.com" in BASE_URL

def arrivals(kind, dur, rate=0, n=0, at=0, spread=3, bg=0, rng=None):
    ts = []
    if kind == "poisson":
        t = 0
        while True:
            t += rng.expovariate(rate / 60)
            if t > dur: break
            ts.append(t)
    elif kind == "burst":
        ts = [at + rng.random() * spread for _ in range(n)]
        t = 0
        while bg and True:
            t += rng.expovariate(bg / 60)
            if t > dur: break
            ts.append(t)
    return sorted(ts)

# name: (duration_s, arrival kwargs, dup_rate, admin events [(t, config)], policy, adaptive, cache, standin_reset_config)
SCEN = {
 "steady":          dict(dur=120, arr=dict(kind="poisson", rate=4), dup=0.15),
 "burst":           dict(dur=90, arr=dict(kind="burst", n=60, at=10, spread=4, bg=3), dup=0.5),
 "overload":        dict(dur=120, arr=dict(kind="poisson", rate=30), dup=0.15),
 "rpm_halved":      dict(dur=120, arr=dict(kind="poisson", rate=5), dup=0.15,
                         events=[(40, {"models": {BIG: dict(rpm=15, tpm=4000), SMALL: dict(rpm=15, tpm=4000)}})]),
 "daily_exhausted": dict(dur=100, arr=dict(kind="poisson", rate=12), dup=0.15, setup={"models": {BIG: dict(rpd=18)}}),
 "overload_naive_retry": dict(dur=120, arr=dict(kind="poisson", rate=30), dup=0.15, policy="naive", adaptive=False),
 "overload_no_cache": dict(dur=120, arr=dict(kind="poisson", rate=30), dup=0.0, cache=False),
 # real-service runs (small, because the free tier is the budget)
 "real_steady":     dict(dur=90, arr=dict(kind="poisson", rate=4), dup=0.0, real=True),
 "real_daily_exhausted": dict(dur=60, arr=dict(kind="poisson", rate=10), dup=0.0, real=True),
 "real_burst":      dict(dur=45, arr=dict(kind="burst", n=14, at=2, spread=1, bg=0), dup=0.0, real=True),
}

async def admin(sess, path, body=None):
    async with sess.post(ADMIN + path, json=body or {}) as r: return await r.json()

PRE = ["quick question:", "urgent, prod is down and I need this:", "for my team's review,", "I'm confused about this:", "looking at the paper again,",
       "sorry if basic:", "my manager asked me:", "can you confirm:", "need this for a postmortem:", "reading the library now,", "before the standup,", "double-checking:"]
def pick(rng, dup):
    """Return (qid, text). A 'dup' request repeats a hot question verbatim (the incident-burst case); others get a personal prefix."""
    if rng.random() < dup:
        q = rng.choice(HOT); return q, QS[q]["q"]
    q = rng.choice(list(QS)); return q, rng.choice(PRE) + " " + QS[q]["q"][0].lower() + QS[q]["q"][1:]

async def run(name, spec, seed=11, setting=DEFAULT_SETTING):
    rng = random.Random(seed); rows = []; N = 0
    async with aiohttp.ClientSession() as sess:
        if not IS_REAL:
            await admin(sess, "/admin/reset")
            await admin(sess, "/admin/config", {"models": {m: dict(rpm=l["rpm"], tpm=l["tpm"], rpd=l["rpd"], tpd=l["tpd"]) for m, l in DEFAULT_LIMITS.items()}})
            if spec.get("setup"): await admin(sess, "/admin/config", spec["setup"])
        async with Client(policy=spec.get("policy", "smart")) as c:
            pipe = Pipeline(c, setting, cache=spec.get("cache", True), adaptive=spec.get("adaptive", True))
            ts = arrivals(rng=rng, dur=spec["dur"], **spec["arr"]); t_start = now(); peak_q = 0; stop = False
            async def sampler():
                nonlocal peak_q
                while not stop:
                    peak_q = max(peak_q, sum(len(g.heap) for g in c.gates.values())); await asyncio.sleep(0.05)
            async def events():
                for t, cfg in spec.get("events", []):
                    await asyncio.sleep(max(0, t_start + t - now())); await admin(sess, "/admin/config", cfg)
            async def user(i, qid, text):
                req = Req(qid, text); task = asyncio.create_task(pipe.answer(req))
                while not task.done():
                    await asyncio.sleep(0.05)
                    if (req.o and req.o.first_token_ms is None and now() > req.dl_first) or now() > req.dl_total:
                        task.cancel(); break      # the person gives up
                try: o = await task
                except asyncio.CancelledError: o = req.o; o.status = "timeout"
                o.total_ms = (now() - req.t0) * 1000
                rid = f"{name}-{i:04d}"
                st = o.st
                grd = grade(qid, o.text, o.cited, o.refused, setting) if o.status == "ok" else ""
                rows.append({"request_id": rid, "scenario": name, "source": "real" if IS_REAL else "stand-in", "start_time": f"{req.t0_wall:.3f}",
                    "first_token_ms": "" if o.first_token_ms is None else f"{o.first_token_ms:.1f}", "total_ms": f"{o.total_ms:.1f}", "status": o.status,
                    "retries": st.retries, "tokens_in": st.tokens_in, "tokens_out": st.tokens_out, "retrieval_setting": setting, "answer_grade": grd,
                    "qid": qid, "path": o.path, "mode": o.mode, "cache_hit": int(o.cache_hit), "queue_ms": f"{st.queue_ms:.1f}", "work_ms": f"{st.work_ms:.1f}",
                    "retry_ms": f"{st.retry_ms:.1f}", "other_ms": f"{o.other_ms:.1f}", "llm_ttft_ms": "" if st.llm_ttft_ms is None else f"{st.llm_ttft_ms:.1f}",
                    "calls": st.calls, "n429": st.n429, "cost_usd": f"{st.cost:.6f}", "loops": o.loops})
                (RAW / f"trace_{name}.jsonl").open("a").write(json.dumps({"id": rid, "q": qid, "path": o.path, "trace": o.trace}) + "\n")
            (RAW / f"trace_{name}.jsonl").write_text("")
            bg = [asyncio.create_task(sampler()), asyncio.create_task(events())]
            users = []
            for t in ts:
                await asyncio.sleep(max(0, t_start + t - now()))
                users.append(asyncio.create_task(user(N, *pick(rng, spec["dup"])))); N += 1
            await asyncio.gather(*users); stop = True; dur = now() - t_start
            for b in bg: b.cancel()
            server = {}
            if not IS_REAL:
                async with sess.get(ADMIN + "/admin/stats") as r: server = await r.json()
            summ = dict(scenario=name, source="real" if IS_REAL else "stand-in", sent=N, duration_s=round(dur, 1), peak_queue=peak_q,
                        offered_per_min=round(N / spec["dur"] * 60, 1), shed={m: g.shed for m, g in c.gates.items()}, scale={m: round(g.scale, 2) for m, g in c.gates.items()},
                        server=server, retry_log_n=len(c.retry_log))
            with (RAW / "scenario_summary.jsonl").open("a") as f: f.write(json.dumps(summ) + "\n")
            print(json.dumps(summ), flush=True)
    new = not CSV.exists()
    with CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, COLS)
        if new: w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["request_id"]))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--only", nargs="*"); ap.add_argument("--setting", default=DEFAULT_SETTING)
    a = ap.parse_args()
    names = a.only or [n for n, s in SCEN.items() if bool(s.get("real")) == IS_REAL]
    for n in names:
        print("== running", n, flush=True); asyncio.run(run(n, SCEN[n], setting=a.setting))
