"""Table 3: quality / tokens / TTFT per retrieval setting, one question at a time (no load), cache off, long deadlines."""
import asyncio, csv, sys, random, aiohttp, os
from relay.config import *
from relay.pipeline import Pipeline, Req
from relay.evalset import QS, grade, retrieval_hit
from relay.retrieval import get_index
import run_load
from relay.llm import Client, now

async def main(reps=1):
    big = {m: dict(rpm=100000, tpm=10**9, rpd=10**9, tpd=10**9, inflight=8) for m in DEFAULT_LIMITS}
    rows = []
    for setting in SETTINGS:
        ix = get_index(setting); size, ov, k, rr = SETTINGS[setting]
        hits = sum(retrieval_hit(q, ix.search(QS[q]["q"], k, rr)[0]) for q in QS if QS[q]["answerable"])
        print(f"{setting}: evidence in retrieved set for {hits}/{sum(d['answerable'] for d in QS.values())} answerable questions", flush=True)
        async with aiohttp.ClientSession() as sess:
            if not run_load.IS_REAL: await run_load.admin(sess, "/admin/reset")
            async with Client(limits=None if run_load.IS_REAL else big) as c:
                p = Pipeline(c, setting, cache=False, adaptive=True); p.force_mode = os.getenv('RELAY_FORCE_MODE') or None
                for rep in range(reps):
                    for qid in QS:
                        req = Req(qid, QS[qid]["q"], first_ms=120000, total_ms=180000)
                        o = await p.answer(req); st = o.st
                        rows.append({"request_id": f"retr-{setting}-{rep}-{qid}", "scenario": f"retrieval_{setting}", "source": "real" if run_load.IS_REAL else "stand-in",
                            "start_time": f"{req.t0_wall:.3f}", "first_token_ms": "" if o.first_token_ms is None else f"{o.first_token_ms:.1f}", "total_ms": f"{o.total_ms:.1f}",
                            "status": o.status, "retries": st.retries, "tokens_in": st.tokens_in, "tokens_out": st.tokens_out, "retrieval_setting": setting,
                            "answer_grade": grade(qid, o.text, o.cited, o.refused, setting) if o.status == "ok" else "", "qid": qid, "path": o.path, "mode": o.mode,
                            "cache_hit": 0, "queue_ms": f"{st.queue_ms:.1f}", "work_ms": f"{st.work_ms:.1f}", "retry_ms": f"{st.retry_ms:.1f}", "other_ms": f"{o.other_ms:.1f}",
                            "llm_ttft_ms": "" if st.llm_ttft_ms is None else f"{st.llm_ttft_ms:.1f}", "calls": st.calls, "n429": st.n429, "cost_usd": f"{st.cost:.6f}", "loops": o.loops})
    new = not run_load.CSV.exists()
    with run_load.CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, run_load.COLS)
        if new: w.writeheader()
        w.writerows(rows)
if __name__ == "__main__": asyncio.run(main())
