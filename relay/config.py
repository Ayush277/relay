"""Central settings. Everything tunable lives here or in env vars so runs are reproducible."""
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
RAW.mkdir(exist_ok=True)

PAPERS = {  # code -> (title, arXiv id+version, local file stem)
    "att":  ("Attention Is All You Need", "1706.03762v7", "attention"),
    "mos":  ("Moshi", "2410.00037v1", "moshi"),
    "hnet": ("Dynamic Chunking (H-Net)", "2507.07955v2", "dchunk"),
}

BASE_URL = os.getenv("RELAY_BASE_URL", "http://127.0.0.1:8808/v1")
API_KEY = os.getenv("GROQ_API_KEY", "standin-key")
SMALL = os.getenv("RELAY_SMALL", "openai/gpt-oss-20b")
BIG = os.getenv("RELAY_BIG", "openai/gpt-oss-120b")

# Groq list prices in USD per 1M tokens (in, out). From memory of the public pricing page: UNVERIFIED.
# The free tier costs $0; these give a "what would this cost on a paid plan" figure.
PRICES = {SMALL: (0.075, 0.30), BIG: (0.15, 0.60)}   # gpt-oss-20b / 120b, from memory: UNVERIFIED

# Limits the *client* believes in. Overwritten by raw/limits.json if the real probe has been run.
DEFAULT_LIMITS = {   # first-contact values seen in x-ratelimit headers on 2026-11-11; raw/limits.json overrides after probe_real.py
    SMALL: dict(rpm=30, tpm=8000, rpd=1000, tpd=200000, inflight=8),
    BIG:   dict(rpm=30, tpm=8000, rpd=1000, tpd=200000, inflight=8),
}

# User-patience assumptions (ms). Assumption, not measured: see report section 8.
FIRST_TOKEN_DEADLINE_MS = 8000
TOTAL_DEADLINE_MS = 25000

# Retrieval settings: name -> (chunk words, overlap words, pieces fetched, rerank)
SETTINGS = {
    "s120_k3_plain":  (120, 20, 3, False),
    "s250_k4_plain":  (250, 40, 4, False),
    "s250_k4_rerank": (250, 40, 4, True),
    "s500_k3_rerank": (500, 60, 3, True),
}
DEFAULT_SETTING = "s120_k3_plain"

REFUSAL = ("The three papers (Attention Is All You Need v7, Moshi v1, Dynamic Chunking v2) "
           "don't say. I'd rather tell you that than guess.")
