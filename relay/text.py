import re, math
STOP = set("""a an the of to in on for and or is are was were be been being by with as at from that this these those it its
what which who whom how why when where does do did can could should would will shall may might about into over under than then
there their they them we our you your i me my he she his her not no yes also such per via using use used paper papers authors
author report reports say says said state states describe described mention mentioned give given tell please""".split())
TOK = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")

def stem(t):
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t

def toks(s, keep_stop=False):
    out = []
    for raw in TOK.findall(s.lower().replace(",", "")):
        parts = [raw] + (raw.split("-") if "-" in raw else [])
        for p in parts:
            if not p or (not keep_stop and p in STOP):
                continue
            out.append(stem(p))
    return out

def norm_key(q):
    return " ".join(sorted(set(toks(q))))

GREEK = {'α':'alpha','β':'beta','γ':'gamma','δ':'delta','ε':'epsilon','ϵ':'epsilon','λ':'lambda','μ':'mu','σ':'sigma','τ':'tau','π':'pi'}
def greek(s):
    return ''.join(GREEK.get(c, c) for c in s)

def clean_page(p):
    p = greek(p)
    p = re.sub(r"-\n(?=[a-z])", "", p)
    p = re.sub(r"\s*\n\s*", " ", p)
    return re.sub(r"\s+", " ", p).strip()

SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(])")
def split_sentences(t):
    return [s.strip() for s in SENT_END.split(t) if s.strip()]

CITE = re.compile(r"\[((?:att|mos|hnet)-p\d+-\d+)\]")
NUM = re.compile(r"\d[\d.,]*\d|\d")
def numbers(s):
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(s)}
