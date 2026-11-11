"""Recompute every number in the write-up from results.csv (+ raw/scenario_summary.jsonl, raw/probe_summary.json)."""
import csv, json, collections, statistics as st, numpy as np, pathlib
from relay.config import ROOT, RAW, SETTINGS
rows = list(csv.DictReader(open(ROOT / "results.csv")))
summ = {}
for l in (RAW / "scenario_summary.jsonl").read_text().splitlines() if (RAW / "scenario_summary.jsonl").exists() else []:
    d = json.loads(l); summ[(d["scenario"], d["source"])] = d
def f(x): return float(x) if x not in ("", None) else None
def pct(v, q): return "n/a" if not v else f"{np.percentile(v, q):.0f}"
def trip(v): return " / ".join(pct(v, q) for q in (50, 95, 99))
by = collections.defaultdict(list)
for r in rows: by[(r["scenario"], r["source"])].append(r)
order = ["steady", "burst", "overload", "rpm_halved", "daily_exhausted", "overload_naive_retry", "overload_no_cache", "real_steady", "real_burst"]
keys = sorted([k for k in by if not k[0].startswith("retrieval_")], key=lambda k: order.index(k[0]) if k[0] in order else 99)
out = []
out.append("### Table 2a: Latency (ms). Percentiles are linear-interpolated; first token = first *verified* text shown to the person; computed over requests that received text\n")
out.append("| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |\n|---|---|---|---|---|---|---|")
for k in keys:
    R = by[k]; s = summ.get(k, {}); ft = [f(r["first_token_ms"]) for r in R if f(r["first_token_ms"]) is not None]
    tt = [f(r["total_ms"]) for r in R if r["status"] == "ok"]
    out.append(f"| {k[0]} | {k[1]} | {len(R)} | {s.get('duration_s','not measured')} | {s.get('offered_per_min','not measured')} | {trip(ft)} (n={len(ft)}) | {trip(tt)} (n={len(tt)}) |")
out.append("\n### Table 2b: Outcomes\n")
out.append("| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer (all / model-path only) | Cost per 1,000 answers (list-price USD) |\n|---|---|---|---|---|---|---|---|")
extra = []
for k in keys:
    R = by[k]; n = len(R); ok = [r for r in R if r["status"] == "ok"]
    c = collections.Counter(r["status"] for r in R)
    mp = [r for r in ok if int(r["calls"]) > 0]
    tk = lambda L: f"{np.mean([int(r['tokens_in']) + int(r['tokens_out']) for r in L]):.0f}" if L else "n/a"
    cost = sum(float(r["cost_usd"]) for r in R) / len(ok) * 1000 if ok else 0
    s = summ.get(k, {})
    out.append(f"| {k[0]} | {100*c['ok']/n:.1f} | {100*c['rate_limited']/n:.1f} | {100*(c['timeout']+c['error'])/n:.1f} | {np.mean([int(r['retries']) for r in R]):.2f} | {s.get('peak_queue','not measured')} | {tk(ok)} / {tk(mp)} | ${cost:.2f} |")
    pc = collections.Counter(r["path"] for r in R)
    g = collections.Counter(r["answer_grade"] for r in ok)
    extra.append(f"- **{k[0]}**: paths {dict(pc)}; any-429 seen by {100*sum(1 for r in R if int(r['n429'])>0)/n:.1f}% of requests; grades {dict(g)}")
out.append("\nPath and grade breakdown per scenario (from the `path` and `answer_grade` columns):\n" + "\n".join(extra))
out.append("\n### Table 3: Retrieval settings (one question at a time, no load, cache off)\n")
out.append("| Setting (piece words, pieces fetched, re-rank) | Source | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |\n|---|---|---|---|---|---|---|---|---|")
for sname, (size, ov, kk, rr) in SETTINGS.items():
    for src in ("real", "stand-in"):
        R = by.get((f"retrieval_{sname}", src), [])
        if not R: continue
        n = len(R); g = collections.Counter(r["answer_grade"] for r in R)
        ft = [f(r["first_token_ms"]) for r in R if f(r["first_token_ms"]) is not None]; tt = [f(r["total_ms"]) for r in R if r["status"] == "ok"]
        tk = np.mean([int(r["tokens_in"]) + int(r["tokens_out"]) for r in R])
        out.append(f"| {size} words, k={kk}, rerank={'yes' if rr else 'no'} | {src} | {n} | {100*g['correct']/n:.1f} | {100*g['wrong']/n:.1f} | {100*g['refused_correctly']/n:.1f} | {tk:.0f} | {pct(ft,50)} / {pct(ft,95)} | {pct(tt,50)} / {pct(tt,95)} |")
# Table 1
out.append("\n### Table 1: Limits found in the real service\n")
ps = RAW / "probe_summary.json"
if ps.exists():
    P = json.loads(ps.read_text()); out.append("```json\n" + json.dumps(P, indent=1)[:2500] + "\n```")
else:
    out.append("| Limit | Value you found | How you found it | Confidence |\n|---|---|---|---|")
    for l in ["Requests per minute", "Tokens per minute", "Requests per day", "Tokens per day", "Requests in flight at once", "What it returns when a limit is hit", "Time until it recovers"]:
        out.append(f"| {l} | not measured | probe_real.py was not run (no GROQ_API_KEY available) | n/a |")
(ROOT / "tables.md").write_text("\n".join(out) + "\n"); print("\n".join(out))
