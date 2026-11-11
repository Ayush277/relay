"""24 questions I wrote (18 answerable, 6 the papers cannot answer) with machine-checkable gold.
ev = (paper, regex that locates the evidence page(s) in the PDF text); ans = regexes that must ALL match the answer."""
import re
from .config import ROOT, PAPERS

Q = [
 # id, question, paper, evidence regex, answer regexes
 ("q01", "How many identical layers does the Transformer encoder stack have?", "att", r"stack of N = 6", [r"\b(6|six)\b"]),
 ("q02", "What BLEU score did the big Transformer reach on WMT 2014 English-to-German?", "att", r"28\.4", [r"28\.4"]),
 ("q03", "What BLEU score did the Transformer reach on WMT 2014 English-to-French?", "att", r"41\.8", [r"41\.8"]),
 ("q04", "Which optimizer was used to train the Transformer, and what were its beta2 and epsilon?", "att", r"β2 = 0\.98", [r"adam", r"0\.98"]),
 ("q05", "What value of warmup_steps was used in the learning rate schedule?", "att", r"warmup_steps = 4000", [r"4000|4,000"]),
 ("q06", "How long were the big Transformer models trained, and for how many steps?", "att", r"3\.5 days", [r"3\.5 days", r"300,?000"]),
 ("q07", "What label smoothing value did the Transformer authors use?", "att", r"label smoothing of value", [r"0\.1"]),
 ("q08", "What beam size was used during inference for translation?", "att", r"beam size of 4", [r"\b(4|four)\b"]),
 ("q09", "Why did the authors choose sinusoidal positional encodings instead of learned ones?", "att", r"extrapolate", [r"extrapolat|longer"]),
 ("q10", "What is Moshi's theoretical latency, and what is it in practice?", "mos", r"theoretical latency of 160ms", [r"160", r"200"]),
 ("q11", "What frame rate and bitrate does the Mimi codec operate at?", "mos", r"1\.1kbps", [r"12\.5", r"1\.1"]),
 ("q12", "How many parameters does the Helium text model have and how many tokens was it pretrained on?", "mos", r"7B-parameter text LLM", [r"7 ?b|7 billion", r"2\.1 ?t"]),
 ("q13", "How many quantizers does Mimi use and what is each codebook's size?", "mos", r"Q = 8 quantizers", [r"\b8\b|eight", r"2048"]),
 ("q14", "What does Moshi's Inner Monologue do?", "mos", r"Inner Monologue decomposes", [r"text", r"semantic|acoustic"]),
 ("q15", "What GPUs were Moshi's models trained on?", "mos", r"trained on H100 GPUs", [r"h100"]),
 ("q16", "What are the two main components of dynamic chunking?", "hnet", r"routing module which predicts", [r"routing", r"smoothing"]),
 ("q17", "How does the routing module decide where chunk boundaries go?", "hnet", r"cosine similarity between adjacent", [r"cosine|similarity"]),
 ("q18", "What does the smoothing module do and why is it needed?", "hnet", r"smoothing module which interpolates", [r"interpolat|gradient|discrete"]),
 # unanswerable from the three papers
 ("u01", "What BLEU score does the Transformer get on WMT 2016 English-to-Romanian?", None, None, None),
 ("u02", "How many parameters does GPT-4 have?", None, None, None),
 ("u03", "How many H100 GPUs were used to train the H-Net models?", None, None, None),
 ("u04", "What dollar cost did it take to train the H-Net?", None, None, None),
 ("u05", "What top-1 accuracy does the Transformer achieve on ImageNet?", None, None, None),
 ("u06", "Which Japanese-language benchmarks is Moshi evaluated on?", None, None, None),
]

def _pages():
    out = {}
    for code, (_, _, stem) in PAPERS.items():
        out[code] = (ROOT / "papers" / f"{stem}.raw.txt").read_text(errors="ignore").replace("\n", " ").split("\f")
    return out

def build():
    pg = _pages(); qs = {}
    for qid, q, paper, evrx, ans in Q:
        d = dict(id=qid, q=q, paper=paper, answerable=paper is not None, ans=ans, pages=set())
        if paper:
            rx = re.compile(evrx.replace(" ", r"\s+"))
            d["pages"] = {i + 1 for i, p in enumerate(pg[paper]) if rx.search(re.sub(r"\s+", " ", p))}
            assert d["pages"], (qid, "no evidence page found")
        qs[qid] = d
    return qs

QS = build()

def grade(qid, text, cited_ids, refused, setting=None):
    from .retrieval import get_index
    from .config import DEFAULT_SETTING
    ix = get_index(setting or DEFAULT_SETTING)
    g = QS[qid]
    if not g["answerable"]:
        return "refused_correctly" if refused else "wrong"
    if refused:
        return "refused_wrongly"
    ok_text = all(re.search(r, text, re.I) for r in g["ans"])
    # grader v2: a citation is right if it is on the gold paper and either sits on a gold evidence page or the cited passage itself
    # contains at least one of the gold answer terms (the papers restate facts on several pages; v1 rejected those).
    def cite_ok(i):
        if i.split("-")[0] != g["paper"] or i not in ix.by_id: return False
        return int(i.split("-")[1][1:]) in g["pages"] or any(re.search(r, ix.by_id[i]["text"], re.I) for r in g["ans"])
    ok_cite = any(cite_ok(i) for i in cited_ids)
    return "correct" if (ok_text and ok_cite) else "wrong"

def retrieval_hit(qid, chunks):
    g = QS[qid]
    return g["answerable"] and any(c["paper"] == g["paper"] and c["page"] in g["pages"] for c in chunks)
