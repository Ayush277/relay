"""Fit stand-in parameters from raw/probe_real.jsonl -> standin/params.json. Run after probe_real.py."""
import json, math, statistics as st
from relay.config import *
recs = [json.loads(l) for l in (RAW / "probe_real.jsonl").read_text().splitlines()]
params = {"models": {}}
for m in (SMALL, BIG):
    L = [r for r in recs if r.get("tag") == "latency" and r["model"] == m and r["status"] == 200 and r.get("ttft_ms")]
    if len(L) < 8: print("not enough real samples for", m); continue
    tt = [r["ttft_ms"] for r in L]; med = st.median(tt)
    logs = [math.log(t / med) for t in tt if t < 3 * med]       # body of the distribution, tail handled separately
    sig = st.pstdev(logs)
    tps = [r["usage"]["completion_tokens"] / max(0.05, (r["total_ms"] - r["ttft_ms"]) / 1000) for r in L if r.get("usage") and r["usage"].get("completion_tokens", 0) > 20]
    slow = [t for t in tt if t >= 3 * med]
    params["models"][m] = dict(ttft_ms=med, ttft_sigma=round(sig, 3), tok_per_s=round(st.median(tps), 1) if tps else 300)
    if slow: params["slow_p"] = round(len(slow) / len(tt), 3); params["slow_mult"] = round(st.mean(slow) / med, 1)
conc = {}
for r in recs:
    if str(r.get("tag", "")).startswith("conc"): conc.setdefault(int(r["tag"][4:]), []).append(r["status"])
cap = [c for c, s in sorted(conc.items()) if any(x == 429 for x in s)]
if cap: params["inflight_cap"] = cap[0] - 1
lim = json.loads((RAW / "limits.json").read_text()) if (RAW / "limits.json").exists() else {}
for m, l in lim.items():
    params["models"].setdefault(m, {}).update({k: l[k] for k in ("rpm", "tpm", "rpd", "tpd") if k in l})
(ROOT / "standin" / "params.json").write_text(json.dumps(params, indent=1)); print(json.dumps(params, indent=1))
