### Table 2a: Latency (ms). Percentiles are linear-interpolated; first token = first *verified* text shown to the person; computed over requests that received text

| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
|---|---|---|---|---|---|---|
| steady | stand-in | 8 | 108.4 | 4.0 | 1204 / 4827 / 4974 (n=8) | 1249 / 4875 / 5016 (n=8) |
| burst | stand-in | 63 | 77.5 | 42.0 | 2 / 1547 / 2651 (n=63) | 51 / 1624 / 2734 (n=63) |
| overload | stand-in | 54 | 119.8 | 27.0 | 633 / 2586 / 5856 (n=54) | 662 / 2630 / 5995 (n=54) |
| rpm_halved | stand-in | 11 | 119.2 | 5.5 | 1238 / 3615 / 5241 (n=11) | 1280 / 3678 / 5313 (n=11) |
| daily_exhausted | stand-in | 17 | 86.5 | 10.2 | 1075 / 1640 / 1967 (n=16) | 1123 / 1721 / 2060 (n=16) |
| overload_naive_retry | stand-in | 54 | 119.8 | 27.0 | 862 / 1793 / 5421 (n=52) | 940 / 1838 / 5509 (n=52) |
| overload_no_cache | stand-in | 54 | 120.2 | 27.0 | 665 / 2454 / 5865 (n=54) | 690 / 2522 / 6005 (n=54) |
| real_steady | real | 5 | 80.9 | 3.3 | 584 / 789 / 792 (n=5) | 618 / 822 / 823 (n=5) |
| real_burst | real | 14 | 3.5 | 18.7 | 9 / 1011 / 1120 (n=14) | 52 / 1037 / 1143 (n=14) |
| contaminated_retrieval_s250_k4_plain | real | 24 | not measured | not measured | 10720 / 17982 / 28348 (n=24) | 10743 / 17994 / 28349 (n=24) |
| contaminated_retrieval_s250_k4_rerank | real | 24 | not measured | not measured | 1 / 11 / 453 (n=24) | 1 / 11 / 453 (n=24) |
| contaminated_retrieval_s500_k3_rerank | real | 24 | not measured | not measured | 2 / 13 / 278 (n=24) | 2 / 13 / 278 (n=24) |
| real_daily_exhausted | real | 11 | 58.9 | 11.0 | 16 / 1834 / 2104 (n=11) | 52 / 1859 / 2122 (n=11) |
| contaminated_real_steady | real | 5 | not measured | not measured | 6 / 448 / 536 (n=5) | 51 / 458 / 540 (n=5) |
| contaminated_real_burst | real | 14 | not measured | not measured | 9 / 548 / 553 (n=14) | 51 / 559 / 564 (n=14) |

### Table 2b: Outcomes

| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer (all / model-path only) | Cost per 1,000 answers (list-price USD) |
|---|---|---|---|---|---|---|---|
| steady | 100.0 | 0.0 | 0.0 | 0.12 | 0 | 595 / 595 | $0.11 |
| burst | 100.0 | 0.0 | 0.0 | 0.02 | 0 | 116 / 814 | $0.02 |
| overload | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 418 / 706 | $0.07 |
| rpm_halved | 100.0 | 0.0 | 0.0 | 0.00 | 0 | 642 / 706 | $0.11 |
| daily_exhausted | 94.1 | 0.0 | 5.9 | 0.06 | 0 | 585 / 624 | $0.10 |
| overload_naive_retry | 96.3 | 3.7 | 0.0 | 0.33 | 0 | 489 / 620 | $0.09 |
| overload_no_cache | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 446 / 651 | $0.08 |
| real_steady | 100.0 | 0.0 | 0.0 | 0.00 | 0 | 589 / 589 | $0.12 |
| real_burst | 100.0 | 0.0 | 0.0 | 0.00 | 0 | 247 / 866 | $0.05 |
| contaminated_retrieval_s250_k4_plain | 100.0 | 0.0 | 0.0 | 0.04 | not measured | 1209 / 1935 | $0.21 |
| contaminated_retrieval_s250_k4_rerank | 100.0 | 0.0 | 0.0 | 0.00 | not measured | 9 / 222 | $0.00 |
| contaminated_retrieval_s500_k3_rerank | 100.0 | 0.0 | 0.0 | 0.00 | not measured | 9 / 222 | $0.00 |
| real_daily_exhausted | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 176 / 388 | $0.03 |
| contaminated_real_steady | 100.0 | 0.0 | 0.0 | 0.00 | not measured | 40 / 199 | $0.01 |
| contaminated_real_burst | 100.0 | 0.0 | 0.0 | 0.00 | not measured | 50 / 176 | $0.01 |

Path and grade breakdown per scenario (from the `path` and `answer_grade` columns):
- **steady**: paths {'full': 8}; any-429 seen by 0.0% of requests; grades {'refused_wrongly': 2, 'wrong': 2, 'refused_correctly': 2, 'correct': 2}
- **burst**: paths {'full': 20, 'partial': 1, 'coalesced': 9, 'cache': 14, 'extractive': 19}; any-429 seen by 0.0% of requests; grades {'correct': 23, 'wrong': 12, 'refused_correctly': 11, 'refused_wrongly': 17}
- **overload**: paths {'full': 39, 'coalesced': 2, 'extractive': 5, 'cache': 8}; any-429 seen by 0.0% of requests; grades {'wrong': 12, 'refused_correctly': 9, 'correct': 12, 'refused_wrongly': 21}
- **rpm_halved**: paths {'full': 10, 'cache': 1}; any-429 seen by 0.0% of requests; grades {'correct': 5, 'refused_correctly': 4, 'refused_wrongly': 2}
- **daily_exhausted**: paths {'full': 15, 'abandoned': 1, 'cache': 1}; any-429 seen by 0.0% of requests; grades {'refused_correctly': 6, 'correct': 4, 'refused_wrongly': 4, 'wrong': 2}
- **overload_naive_retry**: paths {'full': 41, 'cache': 11, 'failed': 2}; any-429 seen by 3.7% of requests; grades {'wrong': 13, 'refused_correctly': 8, 'correct': 14, 'refused_wrongly': 17}
- **overload_no_cache**: paths {'full': 48, 'extractive': 6}; any-429 seen by 0.0% of requests; grades {'wrong': 8, 'refused_correctly': 12, 'correct': 9, 'refused_wrongly': 25}
- **real_steady**: paths {'full': 5}; any-429 seen by 0.0% of requests; grades {'refused_wrongly': 3, 'refused_correctly': 1, 'correct': 1}
- **real_burst**: paths {'full': 5, 'extractive': 8, 'repaired': 1}; any-429 seen by 0.0% of requests; grades {'correct': 3, 'refused_wrongly': 10, 'refused_correctly': 1}
- **contaminated_retrieval_s250_k4_plain**: paths {'full': 14, 'partial': 1, 'repaired': 1, 'extractive': 8}; any-429 seen by 8.3% of requests; grades {'correct': 14, 'wrong': 5, 'refused_wrongly': 1, 'refused_correctly': 4}
- **contaminated_retrieval_s250_k4_rerank**: paths {'extractive': 22, 'full': 2}; any-429 seen by 4.2% of requests; grades {'correct': 11, 'wrong': 5, 'refused_wrongly': 4, 'refused_correctly': 4}
- **contaminated_retrieval_s500_k3_rerank**: paths {'extractive': 22, 'full': 2}; any-429 seen by 4.2% of requests; grades {'correct': 13, 'wrong': 4, 'refused_wrongly': 2, 'refused_correctly': 5}
- **real_daily_exhausted**: paths {'full': 5, 'extractive': 6}; any-429 seen by 9.1% of requests; grades {'refused_correctly': 3, 'correct': 4, 'refused_wrongly': 4}
- **contaminated_real_steady**: paths {'extractive': 2, 'full': 3}; any-429 seen by 20.0% of requests; grades {'refused_wrongly': 4, 'refused_correctly': 1}
- **contaminated_real_burst**: paths {'extractive': 10, 'full': 4}; any-429 seen by 7.1% of requests; grades {'correct': 5, 'refused_wrongly': 8, 'refused_correctly': 1}

### Table 3: Retrieval settings (one question at a time, no load, cache off)

| Setting (piece words, pieces fetched, re-rank) | Source | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| 120 words, k=3, rerank=no | real | 24 | 62.5 | 8.3 | 25.0 | 778 | 2562 / 8918 | 2562 / 8918 |
| 250 words, k=4, rerank=no | real | 24 | 62.5 | 8.3 | 25.0 | 1517 | 10490 / 23743 | 10490 / 23756 |
| 250 words, k=4, rerank=yes | real | 24 | 62.5 | 12.5 | 25.0 | 1448 | 10300 / 20805 | 10300 / 20805 |
| 500 words, k=3, rerank=yes | real | 24 | 58.3 | 16.7 | 25.0 | 2655 | 19429 / 43865 | 19430 / 43866 |

### Table 1: Limits found in the real service

```json
{
 "limits": {
  "openai/gpt-oss-20b": {
   "rpd": 1000,
   "tpm": 8000,
   "rpm": 30,
   "rpm_observed": 30
  },
  "openai/gpt-oss-120b": {
   "rpd": 1000,
   "tpm": 8000
  }
 },
 "concurrency": {
  "conc1": [
   200
  ],
  "conc2": [
   200,
   200
  ],
  "conc4": [
   200,
   200,
   200,
   200
  ],
  "conc8": [
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200
  ],
  "conc16": [
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200,
   200
  ]
 },
 "rpm_result": {
  "tag": "rpm_result",
  "ok_before_429": 30,
  "elapsed_s": 11.533594131469727,
  "first429": {
   "tag": "rpm_ramp",
   "model": "openai/gpt-oss-20b",
   "status": 429,
   "t": 1794395839.541552,
   "total_ms": 44.29254203569144,
   "headers": {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-limit-tokens": null,
    "x-ratelimit-remaining-requests": "916",
    "x-ratelimit-remaining-tokens": null,
    "x-ratelimit-reset-requests": "2h0m57.6s",
    "x-ratelimit-reset-tokens": null,
    "retry-after": "2"
   },
   "body": "{\"error\":{\"message\":\"Rate limit reached for model `openai/gpt-oss-20b` in organization `org_01m754ahr9ebptysxcwk1tmc1r` service tier `on_demand` on requests per minute (RPM): Limit 30, Used 30, Requested 1. Please try again in 2s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing\",\"type\":\"requests\",\"code\":\"rate_limit_exceeded\"}}\n"
  }
 },
 "recovery": {
  "tag": "rpm_recovery_result",
  "seconds": 2.409214973449707
 },
 "sample_429": {
  "tag": "rpm_ramp",
  "model": "openai/gpt-oss-20b",
  "status": 429,
  "t": 1794395839.541552,
  "total_ms": 44.29254203569144,
  "headers": {
   "x-ratelimit-limit-requests": "1000",
   "x-ratelimit-limit-tokens": null,
   "x-ratelimit-remaining-requests": "916",
   "x-ratelimit-remaining-tokens": null,
   "x-ratelimit-reset-requests": "2h0m57.6s",
   "x-ratelimit-reset-tokens": null,
   "retry-after": "2"
  },
  "body": "{\"error\":{\"message\":\"Rate limit reached for model `openai/gpt-oss-20b` in organization `org_01m754ahr9ebptysxcwk1tmc1r` service tier `on_demand` on requests per minute (RPM): Limit 30, Used 30, Requested 1. Please try again in 2s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing\",\"type\":\"requests\",\"code\":\"rate_limit_exceeded\"}}\n"
 }
}
```
