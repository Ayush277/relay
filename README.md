# The Relay

Question-answering over three arXiv papers (Attention Is All You Need v7, Moshi v1, Dynamic Chunking v2) through a
rate-limit-aware pipeline, plus a stand-in model service, load runner and the tooling that regenerates every table.

```
papers/            pinned PDFs + extracted text (pdftotext)        relay/pipeline.py   understand -> retrieve -> tool -> draft+verify -> repair -> extractive
relay/retrieval.py chunking, BM25, lexical re-rank, exact tool     relay/llm.py        governor (RPM/TPM/day/in-flight, EDF queue, shed) + retry policy
relay/evalset.py   24 questions with machine-checkable gold        standin/server.py   stand-in provider (slow/fail/429/load-slowdown/streaming)
run_load.py        load scenarios -> results.csv                   probe_real.py       measure the real Groq tier (needs your key)
eval_retrieval.py  Table 3                                         calibrate.py        fit stand-in from real probe -> standin/params.json
make_tables.py     recompute all tables from results.csv           raw/                raw traces, scenario summaries, probe output
```

## Reproduce
```bash
python3 -m pip install aiohttp numpy            # + poppler (pdftotext)
python3 standin/server.py 8808 &                # stand-in
python3 run_load.py                             # stand-in scenarios (~15 min) -> results.csv
python3 eval_retrieval.py                       # Table 3 on the stand-in
python3 make_tables.py                          # tables.md

# real service (your own free key; never committed)
export GROQ_API_KEY=...  RELAY_BASE_URL=https://api.groq.com/openai/v1
python3 probe_real.py && python3 calibrate.py   # Table 1, raw/probe_real.jsonl, standin/params.json, raw/limits.json
python3 eval_retrieval.py                       # Table 3 on the real model
python3 run_load.py --only real_steady real_burst
```
