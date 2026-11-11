"""Chunking, BM25, optional lexical re-rank, paper routing, and the exact-match tool."""
import math, re, random, asyncio
from collections import Counter, defaultdict
from .config import ROOT, PAPERS, SETTINGS
from .text import toks, clean_page, STOP, stem

_ROUTE = {
    "mos": r"moshi|mimi|helium|inner monologue|full-duplex|duplex|speech|audio|kyutai|codec|latency|dialogue|quantiz|codebook",
    "hnet": r"h-net|hnet|dynamic chunking|dechunk|routing module|smoothing module|hierarch|byte-level|tokeni[sz]|chunk",
    "att": r"transformer|attention is all|bleu|wmt|multi-head|label smoothing|warmup|beam|sinusoid|positional|encoder|decoder|p100|adam",
}

def route_scores(q):
    ql = q.lower()
    return {p: len(re.findall(rx, ql)) for p, rx in _ROUTE.items()}

class Index:
    def __init__(self, size, overlap):
        self.chunks, self.postings, self.df = [], defaultdict(list), Counter()
        for code, (title, ver, stem_) in PAPERS.items():
            pages = (ROOT / "papers" / f"{stem_}.raw.txt").read_text(errors="ignore").split("\f")
            for pno, page in enumerate(pages, 1):
                words = clean_page(page).split()
                if len(words) < 12:
                    continue
                step = max(1, size - overlap); j = 0
                for i in range(0, len(words), step):
                    piece = words[i:i + size]
                    if len(piece) < 15 and j > 0:
                        break
                    text = " ".join(piece)
                    self.chunks.append(dict(id=f"{code}-p{pno}-{j}", paper=code, page=pno, text=text,
                                            toks=toks(text), nwords=len(piece)))
                    j += 1
                    if i + size >= len(words):
                        break
        for ci, c in enumerate(self.chunks):
            tf = Counter(c["toks"]); c["tf"] = tf
            for t, n in tf.items():
                self.postings[t].append((ci, n)); self.df[t] += 1
        self.N = len(self.chunks)
        self.avgdl = sum(len(c["toks"]) for c in self.chunks) / self.N
        self.by_id = {c["id"]: c for c in self.chunks}

    def idf(self, t):
        d = self.df.get(t, 0)
        return math.log(1 + (self.N - d + 0.5) / (d + 0.5))

    def bm25(self, q, k1=1.2, b=0.75):
        sc = defaultdict(float)
        for t in set(q):
            idf = self.idf(t)
            for ci, tf in self.postings.get(t, ()):
                dl = len(self.chunks[ci]["toks"])
                sc[ci] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / self.avgdl))
        return sc

    def coverage(self, qterms, ctoks):
        s = set(ctoks); tot = sum(self.idf(t) for t in qterms) or 1e-9
        return sum(self.idf(t) for t in qterms if t in s) / tot

    def search(self, question, k, rerank, hint=None, extra=None, pool=25):
        q = toks(question)
        if extra:
            q = q + [t for t in toks(extra) if t not in q]
        qset = list(dict.fromkeys(q))
        sc = self.bm25(qset)
        if not sc:
            return [], dict(cov=0.0)
        top = sorted(sc.items(), key=lambda x: -x[1])[: (pool if rerank else k)]
        if not rerank:
            ch = [self.chunks[ci] for ci, _ in top[:k]]
            return ch, dict(cov=self.coverage(qset, ch[0]["toks"]))
        mx = top[0][1] or 1.0
        route = route_scores(question)
        if hint in route:
            route[hint] += 1
        best = max(route, key=route.get) if max(route.values()) > 0 else None
        qb = set(zip(q, q[1:]))
        nums = [t for t in qset if any(c.isdigit() for c in t)]
        rer = []
        for ci, s in top:
            c = self.chunks[ci]
            cov = self.coverage(qset, c["toks"])
            bg = len(qb & set(zip(c["toks"], c["toks"][1:]))) / (len(qb) or 1)
            nm = (sum(1 for n in nums if n in c["tf"]) / len(nums)) if nums else 0.0
            v = 0.5 * s / mx + 0.3 * cov + 0.1 * bg + 0.1 * nm
            if best and c["paper"] == best:
                v *= 1.25
            rer.append((v, c))
        rer.sort(key=lambda x: -x[0])
        ch = [c for _, c in rer[:k]]
        return ch, dict(cov=self.coverage(qset, ch[0]["toks"]), route=best)

    def best_sentences(self, question, chunks, n=2):
        """Extractive fallback: the sentences with highest idf-weighted query coverage."""
        from .text import split_sentences
        qset = list(dict.fromkeys(toks(question)))
        cands = []
        for c in chunks:
            for s in split_sentences(c["text"]):
                st = toks(s)
                if len(st) < 5:
                    continue
                cands.append((self.coverage(qset, st), len(s), s, c["id"]))
        cands.sort(key=lambda x: (-x[0], x[1]))
        return cands[:n]

_cache = {}
def get_index(setting):
    size, ov, k, rr = SETTINGS[setting]
    if (size, ov) not in _cache:
        _cache[(size, ov)] = Index(size, ov)
    return _cache[(size, ov)]

# ---- the tool: literal search for numbers / acronyms / quoted strings ---------------------------
EXACT = re.compile(r'"([^"]+)"|\b([A-Z][A-Za-z]*-?[A-Z0-9][A-Za-z0-9\-]*)\b|\b(\d+(?:\.\d+)?[A-Za-z%]*)\b')
def exact_terms(question):
    out = []
    for m in EXACT.finditer(question):
        t = next(g for g in m.groups() if g)
        if t.lower() not in STOP and len(t) > 1:
            out.append(t)
    return out[:3]

class ToolFaults:
    """Simulated tool misbehaviour (the real tool is local and never misbehaves)."""
    slow_p = 0.0; err_p = 0.0; slow_s = 3.0; rng = random.Random(7)

async def exact_search(index, terms, timeout_s=0.6):
    async def run():
        r = ToolFaults.rng.random()
        if r < ToolFaults.err_p:
            raise RuntimeError("tool error")
        if r < ToolFaults.err_p + ToolFaults.slow_p:
            await asyncio.sleep(ToolFaults.slow_s)
        await asyncio.sleep(0)
        hits = []
        for c in index.chunks:
            n = sum(c["text"].lower().count(t.lower()) for t in terms)
            if n:
                hits.append((n, c))
        hits.sort(key=lambda x: -x[0])
        return [c for _, c in hits[:2]]
    return await asyncio.wait_for(run(), timeout_s)
