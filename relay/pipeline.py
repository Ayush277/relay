"""understand -> retrieve -> (tool) -> draft (sentence-gated verify) -> [repair loop] -> extractive fallback.
Design rule: nothing reaches the user unless it passed the grounding check; under pressure we degrade to a
cheaper *verified* answer (cited excerpt), never to an unchecked one."""
import asyncio, json, re, time
from dataclasses import dataclass, field
from .config import *
from .text import toks, norm_key, CITE, numbers, split_sentences
from .retrieval import get_index, exact_terms, exact_search
from .llm import Client, Shed, DailyExhausted, CallFailed, CallStats, now

VERIFY_COV = 0.5      # share of an answer sentence's content words that must appear in its cited passages
REFUSE_COV = 0.40     # retrieval gate: best passage must cover this much (idf-weighted) of the question, else don't spend tokens
EXTRACT_COV = 0.60    # stricter bar for the no-LLM excerpt path, because nothing else filters irrelevant excerpts
CITE_RUN = re.compile(r"(?:\[(?:att|mos|hnet)-p\d+-\d+\]\s*)+\.?")
BOUND = re.compile(r"[.!?]\s+(?=[A-Z])")

SYS_DRAFT = ("[task:draft] You answer questions using ONLY the numbered passages. Rules: (1) at most 3 short sentences; "
             "(2) end every sentence with the id of the passage that supports it, like [att-p7-1]; (3) copy numbers exactly as "
             "written; (4) if the passages do not contain the answer, reply exactly NOT_IN_PAPERS. No outside knowledge.")
SYS_UNDERSTAND = ('[task:understand] Return JSON only: {"rewrite": "<standalone search query using the papers\' own terms>", '
                  '"paper": "att|mos|hnet|any"}. att=Attention Is All You Need (Transformer), mos=Moshi speech model, hnet=dynamic chunking H-Net.')

@dataclass
class Req:
    qid: str; question: str; t0: float = field(default_factory=now); t0_wall: float = field(default_factory=time.time)
    dl_first: float = 0; dl_total: float = 0; o: object = None; first_ms: float = FIRST_TOKEN_DEADLINE_MS; total_ms: float = TOTAL_DEADLINE_MS
    def __post_init__(s):
        s.dl_first = s.t0 + s.first_ms / 1000; s.dl_total = s.t0 + s.total_ms / 1000

@dataclass
class Outcome:
    qid: str; setting: str; t0_wall: float = 0; status: str = "error"; path: str = ""; text: str = ""; refused: bool = False
    first_token_ms: float | None = None; total_ms: float = 0; cited: list = field(default_factory=list)
    st: CallStats = field(default_factory=CallStats); other_ms: float = 0; trace: list = field(default_factory=list)
    loops: int = 0; mode: str = ""; cache_hit: bool = False; fail_kind: str = ""

class Pipeline:
    def __init__(self, client: Client, setting=DEFAULT_SETTING, cache=True, adaptive=True):
        self.c, self.setting, self.use_cache, self.adaptive = client, setting, cache, adaptive
        self.index = get_index(setting); self.cache = {}; self.inflight = {}; self.force_mode = None

    # ---- planning: who gets the full pipeline, who gets a cheaper verified answer ---------------
    def plan(self, req):
        if self.force_mode: return self.force_mode, ''
        if not self.adaptive: return "full", ""
        g = self.c.gates[BIG]
        if g.day_blocked_until > now() or g.day_fraction_left() <= 0.0: return "extractive", "daily"
        remaining = req.dl_first - now(); est = g.estimate_wait(2000)
        if est > 0.7 * remaining: return "extractive", "load"
        if est > 0.25 * remaining or g.day_fraction_left() < 0.2:
            g.committed += 2000; return "lean", "load" if est > 0.25 * remaining else "daily-low"
        g.committed += 2000
        return "full", ""

    async def answer(self, req: Req) -> Outcome:
        o = Outcome(req.qid, self.setting, t0_wall=req.t0_wall); req.o = o
        tr = lambda e: o.trace.append((round((now() - req.t0) * 1000, 1), e))
        emit_state = dict(shown="")
        def emit(text):
            if o.first_token_ms is None: o.first_token_ms = (now() - req.t0) * 1000; tr("first token shown to user")
            emit_state["shown"] = text; o.text = text
        key = norm_key(req.question)
        try:
            if self.use_cache and key in self.cache:
                o.cache_hit = True; o.path = "cache"; self._fill(o, self.cache[key]); emit(o.text); o.status = "ok"; return o
            if self.use_cache and key in self.inflight:        # single-flight: a burst of identical questions costs one answer
                tr("coalesced onto in-flight identical question")
                try:
                    res = await asyncio.wait_for(asyncio.shield(self.inflight[key]), max(0.1, req.dl_first - now()))
                    if res is not None: o.cache_hit = True; o.path = "coalesced"; self._fill(o, res); emit(o.text); o.status = "ok"; return o
                except asyncio.TimeoutError: pass
            fut = asyncio.get_running_loop().create_future() if self.use_cache and key not in self.inflight else None
            if fut: self.inflight[key] = fut
            try:
                await self._run(req, o, emit, tr)
            finally:
                if fut:
                    self.inflight.pop(key, None)
                    good = o.status == "ok" and o.path in ("full", "repaired") and not o.refused or (o.refused and o.path in ("full",))
                    if good: self.cache[key] = dict(text=o.text, cited=o.cited, refused=o.refused)
                    if not fut.done(): fut.set_result(self.cache.get(key) if good else None)
        except asyncio.CancelledError:
            o.status = "timeout"; o.path = o.path or "abandoned"; tr("user gave up"); raise
        finally:
            if getattr(o, 'reserved', False): self.c.gates[BIG].committed = max(0.0, self.c.gates[BIG].committed - 2000); o.reserved = False
            o.total_ms = (now() - req.t0) * 1000
            if o.status == "timeout" and not o.path: o.path = "abandoned"
            o.other_ms = o.total_ms - (o.st.queue_ms + o.st.work_ms + o.st.retry_ms)
        return o

    def _fill(self, o, r): o.text, o.cited, o.refused = r["text"], r["cited"], r["refused"]

    async def _run(self, req, o, emit, tr):
        mode, why = self.plan(req); o.reserved = mode in ('full', 'lean') and self.adaptive; o.mode = f"{mode}:{why}" if why else mode; tr(f"plan={o.mode}")
        rewrite, hint = None, None
        # 1. understand
        if mode == "full":
            try:
                txt, st = await self.c.call(SMALL, [dict(role="system", content=SYS_UNDERSTAND), dict(role="user", content=req.question)],
                                            80, min(req.dl_first - 3.0, req.dl_total), trace=tr)
                o.st.add(st)
                m = re.search(r"\{.*\}", txt, re.S)
                if m:
                    j = json.loads(m.group(0)); rewrite = j.get("rewrite"); hint = j.get("paper")
            except (Shed, DailyExhausted, CallFailed, json.JSONDecodeError) as e:
                if hasattr(e, "stats"): o.st.add(e.stats)
                tr(f"understand skipped ({type(e).__name__})")
        # 2. retrieve
        t = now(); size, ov, k, rr = SETTINGS[self.setting]
        if mode != "full" and self.adaptive: k = min(k, 3)
        chunks, info = self.index.search(req.question, k, rr, hint=hint, extra=rewrite)
        tr(f"retrieved {[c['id'] for c in chunks]} cov={info['cov']:.2f}")
        # 3. tool (branch): literal search for numbers/acronyms/quoted strings; skip silently if it is slow or fails
        terms = exact_terms(req.question)
        if terms:
            try:
                extra = await exact_search(self.index, terms)
                ids = {c["id"] for c in chunks}
                add = [c for c in extra if c["id"] not in ids][:1]
                if add: chunks = chunks[:max(1, k - len(add))] + add
                tr(f"tool exact_search({terms}) +{[c['id'] for c in add]}")
            except Exception as e:
                tr(f"tool failed ({type(e).__name__}); continuing without it")
        o.st.work_ms += (now() - t) * 1000
        allowed = {c["id"] for c in chunks}
        # 4. cheap refusal gate: don't spend tokens when nothing in the library resembles the question
        if not chunks or info["cov"] < REFUSE_COV:
            return self._refuse(o, emit, tr, "retrieval gate")
        if mode == "extractive":
            return await self._extractive(req, o, emit, tr, chunks, why)
        # 5. draft with sentence-gated verification (+ one repair loop)
        for attempt in range(2):
            ok = await self._draft(req, o, emit, tr, chunks, allowed, strict=attempt == 1, mode=mode)
            if ok: return
            if attempt == 0 and self.adaptive and not o.fail_kind and req.dl_total - now() > 5 and self.c.gates[BIG].day_fraction_left() > 0.05:
                o.loops += 1; tr("verification failed before anything was shown -> repair loop (3 passages, strict)")
                chunks = chunks[:3]; allowed = {c["id"] for c in chunks}; continue
            break
        if o.status == "ok": return
        if not self.adaptive:      # baseline has no degraded path: the user just gets the failure
            o.status = o.fail_kind or "error"; o.path = "failed"; return
        await self._extractive(req, o, emit, tr, chunks, "verify")

    def _refuse(self, o, emit, tr, why):
        o.refused = True; o.status = "ok"; o.path = o.path or "full"; emit(REFUSAL); o.cited = []; tr(f"refused ({why})")

    async def _draft(self, req, o, emit, tr, chunks, allowed, strict, mode):
        ctx = "\n".join(f"[{c['id']}] {c['text']}" for c in chunks)
        sysm = SYS_DRAFT + (" Quote the supporting words exactly." if strict else "")
        msgs = [dict(role="system", content=sysm), dict(role="user", content=f"Passages:\n{ctx}\n\nQuestion: {req.question}")]
        S = dict(done=0, shown=[], failed=False, refused=False)
        def ok_sentence(s):
            ids = CITE.findall(s)
            if not ids or any(i not in allowed for i in ids): return False
            body = CITE.sub("", s); ct = " ".join(self.index.by_id[i]["text"] for i in ids)
            st = [t for t in toks(body) if len(t) > 2]; cset = set(toks(ct))
            if st and sum(t in cset for t in st) / len(st) < VERIFY_COV: return False
            return numbers(body) <= numbers(ct)
        def feed(text, final=False):
            text = text.replace('【', '[').replace('】', ']').replace('\u202f', ' ').replace('\u00a0', ' ')
            if S["failed"] or S["refused"]: return True
            if text.lstrip().startswith("NOT_IN_PAPERS"): S["refused"] = True; return True
            if not final and len(text.strip()) < 13 and "NOT_IN_PAPERS".startswith(text.strip()): return False
            while True:
                tail = text[S["done"]:]
                if not tail.strip(): break
                m = CITE_RUN.search(tail); b = BOUND.search(tail)
                if m and (not b or m.start() <= b.start() + 1):
                    if m.end() >= len(tail.rstrip()) and not final and not tail.rstrip().endswith("."): break
                    end = m.end()
                elif b: end = b.start() + 1
                elif final: end = len(tail)
                else: break
                sent = tail[:end].strip(); S["done"] += end
                if not sent: continue
                if not ok_sentence(sent): S["failed"] = True; tr(f"sentence failed verification: {sent[:70]!r}"); return True
                S["shown"].append(sent); emit(" ".join(S["shown"]))
            return False
        try:
            txt, st = await self.c.call(BIG, msgs, 220, req.dl_total, stop_check=feed, trace=tr,
                                        ttft_timeout=max(1.0, min(8.0, req.dl_first - now() + 4)))
            o.st.add(st); feed(txt, final=True)
        except (Shed, DailyExhausted, CallFailed) as e:
            if hasattr(e, "stats"): o.st.add(e.stats)
            tr(f"draft call failed: {type(e).__name__} {e}")
            o.fail_kind = e.kind if isinstance(e, CallFailed) else ('rate_limited' if isinstance(e, DailyExhausted) else 'timeout')
            if isinstance(e, DailyExhausted): o.mode += "+daily"
            return False
        if S["refused"]:
            self._refuse(o, emit, tr, "model said NOT_IN_PAPERS"); return True
        if S["failed"]:
            if S["shown"]:
                emit(" ".join(S["shown"]) + " (I stopped here: the next claim could not be checked against the papers.)")
                o.status = "ok"; o.path = "partial"; o.cited = [i for s in S["shown"] for i in CITE.findall(s)]; return True
            return False
        if not S["shown"]: return False
        o.status = "ok"; o.path = "repaired" if strict else "full"; o.cited = [i for s in S["shown"] for i in CITE.findall(s)]
        return True

    async def _extractive(self, req, o, emit, tr, chunks, why):
        """No-LLM answer: best matching sentences, quoted with citations. Verified by construction."""
        best = self.index.best_sentences(req.question, chunks, 2)
        if not best or best[0][0] < EXTRACT_COV:
            o.path = "extractive"; return self._refuse(o, emit, tr, f"extractive confidence low ({why})")
        head = {"daily": "Today's model budget is used up, so I can't write a full answer right now. ",
                "load": "I'm under heavy load, so instead of a written answer here is the closest text from the papers. ",
                }.get(why, "I couldn't produce a checked written answer, so here is the closest text from the papers. ")
        o.path = "extractive"; o.status = "ok"; o.cited = [b[3] for b in best]
        emit(head + " ".join(f"“{b[2]}” [{b[3]}]" for b in best)); tr(f"extractive answer ({why})")
