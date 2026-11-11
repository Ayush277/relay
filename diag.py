import asyncio, sys
from relay.config import *
from relay.pipeline import Pipeline, Req
from relay.evalset import QS, grade
from relay.llm import Client
async def main(setting):
    async with Client() as c:
        p = Pipeline(c, setting, cache=False)
        for qid in QS:
            o = await p.answer(Req(qid, QS[qid]["q"], first_ms=120000, total_ms=180000))
            g = grade(qid, o.text, o.cited, o.refused)
            if g != "refused_correctly" and g != "correct" or "-v" in sys.argv:
                print(f"--- {qid} {g} path={o.path}\nQ: {QS[qid]['q']}\nA: {o.text[:300]}\n" + "\n".join(f"   {t}" for _, t in o.trace if 'fail' in t or 'refus' in t or 'retrieved' in t), flush=True)
asyncio.run(main(sys.argv[1]))
