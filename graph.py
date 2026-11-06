"""교육법 딥리서처 — 코디네이터 1 + 리서처 N.  ① 기획 → ② 배치 ⇉ ③ 조사(동시) → ④ 점검(↺ 재위임) → ⑤ 종합 → ⑥ 평가

    from graph import research
    run = research("학원에서 시험 대비 수업을 할 때 연구가 권하는 공부법은?")
    print(run["report"]); print(run["metrics"])

    python graph.py "질문"                 # 터미널에서 한 번
    python graph.py --q Q4 --off 재위임    # 질문 세트의 Q4, 재위임 장치 끄고

도메인 값(절수·예산·역할 명단·목차 지침)은 config.json 에 있다. 이 파일은 주제를 모른다.
"""
from __future__ import annotations

import argparse
import json
import operator
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict

import anthropic
from dotenv import dotenv_values
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import metrics as M

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
CORPUS = json.loads((ROOT / "data" / "corpus.json").read_text(encoding="utf-8"))
DOCS: dict[str, str] = CORPUS["docs"]
LINKS: dict[str, list[str]] = CORPUS["links"]
META: dict[str, dict] = CORPUS.get("meta", {})
QUESTIONS = json.loads((ROOT / CFG["질문파일"]).read_text(encoding="utf-8"))
OUT = ROOT / "output"
IRRELEVANT = "관련 없음"


def irrelevant(note: str) -> bool:
    """'관련 없음' 판정. 모델이 '# 검토 결과 … **관련 없음**'처럼 머리말을 붙이기도 해서 앞부분 안에 있는지 본다."""
    lines = [re.sub(r"[#*\s:：]", "", line) for line in note.splitlines() if line.strip()][:4]
    return any(line.startswith("관련없음") for line in lines)


# ── LLM: 호출 한 번 = 누가(코디네이터/절 n) 몇 글자를 보냈나를 함께 센다 ─────────────────────
class LLM:
    def __init__(self, model: str | None = None) -> None:
        key = dotenv_values(ROOT / ".env").get("ANTHROPIC_API_KEY") or None   # 없으면 SDK 가 환경 변수에서 찾는다
        self.client = anthropic.Anthropic(api_key=key, timeout=120, max_retries=5)
        self.model = model or CFG["모델"]
        self.lock = threading.Lock()
        self.usage: dict[str, dict[str, int]] = {}         # agent → {calls, chars, in_tok, out_tok}
        self.refusals = 0

    def __call__(self, agent: str, system: str, user: str, max_tokens: int | None = None) -> str:
        user = user[: CFG["원문상한글자"]]
        r = self.client.messages.create(model=self.model, max_tokens=max_tokens or CFG["답_최대토큰"], system=system,
                                        messages=[{"role": "user", "content": user}])
        with self.lock:
            u = self.usage.setdefault(agent, {"calls": 0, "chars": 0, "in_tok": 0, "out_tok": 0})
            u["calls"] += 1
            u["chars"] += len(system) + len(user)
            u["in_tok"] += r.usage.input_tokens
            u["out_tok"] += r.usage.output_tokens
            if r.stop_reason == "refusal":
                self.refusals += 1
        if r.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in r.content if b.type == "text")


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z가-힣]+", " ", s.lower()).split())


def canon_cites(text: str, read: list[str]) -> tuple[str, int]:
    """인용 정규화 — 리서처가 '실제로 읽은' 문서의 제목을 줄여 쓴 경우만 원래 제목으로 바로잡는다.
    예: «Test-Enhanced Learning» → «Roediger 외 (2006) Test-Enhanced Learning» (논문 제목에서 '저자 (연도)'를 뺀 경우).
    읽지 않은 문서는 건드리지 않는다 — 그대로 허위 인용으로 잡혀야 하므로. 단일 대조군에도 똑같이 적용한다."""
    fixed = 0

    def one(m: re.Match) -> str:
        nonlocal fixed
        c = m.group(1)
        if c in DOCS:
            return m.group(0)
        nc = _norm(c)
        hits = [t for t in read if nc and (_norm(t) == nc or _norm(re.sub(r"^.{1,40}?\(\d{4}\)\s*", "", t)) == nc
                                            or _norm(t).endswith(" " + nc))]
        if len(set(hits)) == 1:
            fixed += 1
            return f"«{hits[0]}»"
        return m.group(0)

    return re.sub(r"«([^»]+)»", one, text), fixed


def jload(raw: str, default):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return default
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return default


def cards() -> str:
    """코디네이터가 보는 것: 문서마다 제목 + 앞 250자. 원문은 보지 않는다."""
    n = CFG["카드글자"]
    return "\n".join(f"- {t}: {re.sub(r'\s+', ' ', x[:n])}" for t, x in DOCS.items())


# ── 상태 ─────────────────────────────────────────────────────────────────
class State(TypedDict, total=False):
    question: str
    plan: dict                                     # ① 목차·역할·배정 / ④ 다시 보낼 절
    sections: Annotated[list, operator.add]        # ③ 절 원고 — 재위임되면 같은 절의 원고가 하나 더 붙는다
    visited: Annotated[list, operator.add]         # ③ [절번호, 바퀴, 문서]
    decisions: Annotated[list, operator.add]       # ④ 두 번째 원고를 받았나 버렸나
    report: str
    metrics: dict
    log: Annotated[list, operator.add]
    task: dict                                     # Send 로 리서처 하나에게만 가는 지시
    prior: dict                                    # 재위임일 때 그 절의 지난 원고


# ── 두 번째 원고 규칙: 무조건 덮어쓰지 않는다 ─────────────────────────────────────
def better(new: dict, old: dict) -> tuple[bool, str]:
    """새 원고가 허위 인용이 없고, 실제로 읽은 문서 인용 수가 줄지 않을 때만 받는다.
    (덮어쓰기만 하면 인용을 빠뜨린 두 번째 원고가 멀쩡한 첫 원고를 지운다)"""
    if new["허위인용"]:
        return False, f"새 원고에 허위 인용 {len(new['허위인용'])}곳 — 버림"
    if len(new["유효인용"]) < len(old["유효인용"]):
        return False, f"유효 인용 {len(old['유효인용'])} → {len(new['유효인용'])}로 줄어 — 버림"
    if len(new["본문"]) < M.MIN_BODY and len(old["본문"]) >= M.MIN_BODY:
        return False, "새 원고가 너무 짧음 — 버림"
    return True, f"유효 인용 {len(old['유효인용'])} → {len(new['유효인용'])} — 받음"


def accepted(sections: list[dict]) -> dict[int, dict]:
    """절마다 받아들인 원고 하나. 바퀴 순서대로 better() 를 적용한다 — 점검과 종합이 같은 규칙을 쓴다."""
    out: dict[int, dict] = {}
    for sec in sorted(sections, key=lambda x: (x["번호"], x["바퀴"])):
        old = out.get(sec["번호"])
        if old is None or better(sec, old)[0]:
            out[sec["번호"]] = sec
    return out


# ── 팀 ──────────────────────────────────────────────────────────────────
class Team:
    def __init__(self, switches: dict | None = None, llm: LLM | None = None, **overrides) -> None:
        self.cfg = {**CFG, **overrides}
        self.sw = {**CFG["스위치"], **(switches or {})}
        self.llm = llm or LLM(self.cfg["모델"])
        self.roster: dict[str, str] = self.cfg["역할명단"]
        # 스위치가 실제로 코드 경로를 바꿨는지의 증거 + 조용한 실패 계량기
        self.path = {"보정된배정": 0, "보낸피하기": 0, "넓힌횟수": 0, "일찍멈춤": 0, "재위임절": 0, "고르기보정": 0, "고르기실패": 0,
                     "인용정규화": 0}
        self.lock = threading.Lock()

    def bump(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.path[key] += n

    # ① 기획 ─────────────────────────────────────────────
    def plan(self, s: State) -> dict:
        roster = "\n".join(f"- {k}: {v}" for k, v in self.roster.items())
        raw = self.llm(
            "코디네이터",
            "너는 리서치 팀의 코디네이터다. 질문에 답하는 보고서의 목차를 짜고, 각 절을 리서처 하나에게 맡긴다. "
            f"절은 최대 {self.cfg['절수']}개. 질문이 단순해 문서 한두 건으로 답이 나오면 절을 적게 짜도 된다.\n"
            f"{self.cfg['목차_지침']}\n"
            "절마다 그 절을 쓰는 데 가장 먼저 읽어야 할 문서를 문서 카드 목록에서 하나 골라 '시작문서'에 제목을 그대로 적어라. "
            "절끼리 다른 문서를 주어라. 지시는 그 시작 문서에서 실제로 찾을 수 있는 것과 어긋나지 않게 써라.\n"
            f"절의 성격에 맞는 역할을 명단에서 골라 붙여라.\n[역할 명단]\n{roster}\n"
            'JSON으로만: {"제목":"보고서 제목","목차":[{"절":"절 제목","지시":"이 절에서 밝혀야 할 것 한두 문장",'
            '"역할":"명단의 이름 그대로","시작문서":"카드에 있는 제목 그대로"}]}',
            f"[질문] {s['question']}\n[문서 카드 {len(DOCS)}장]\n{cards()}")
        obj = jload(raw, {})
        toc, taken, fixes = [], set(), []
        for item in (obj.get("목차") or [])[: self.cfg["절수"]]:
            seed = str(item.get("시작문서") or "").strip()
            if not self.sw["배정"]:
                seed = ""
            elif seed not in DOCS or seed in taken:        # 모델은 그럴듯한 제목을 지어낸다 — 코드가 실제 문서인지 검사한다
                fixes.append(seed)
                seed = self._closest(seed, taken)
                self.bump("보정된배정")
            if seed:
                taken.add(seed)
            role = item.get("역할", "")
            toc.append({"절": item.get("절") or "무제", "지시": item.get("지시") or s["question"],
                        "역할": role if self.sw["역할"] and role in self.roster else self.cfg["기본역할"],
                        "시작문서": seed, "예산": self.cfg["절예산"]})
        if not toc:
            toc = [{"절": "개요", "지시": s["question"], "역할": self.cfg["기본역할"], "시작문서": "", "예산": self.cfg["절예산"]}]
        plan = {"제목": obj.get("제목") or s["question"], "목차": toc, "배치": list(range(len(toc))), "바퀴": 1, "보정": fixes}
        seeds = " · ".join(f"{t['역할']}→«{t['시작문서'] or '자율'}»" for t in toc)
        return {"plan": plan, "log": [f"① 기획   목차 {len(toc)}절 · {seeds}" + (f" · 배정 보정 {len(fixes)}건" if fixes else "")]}

    @staticmethod
    def _closest(proposed: str, taken: set) -> str:
        """지어낸 제목이면 가장 닮은 실제 문서로 바꾼다. 닮은 게 없으면 아직 안 쓴 첫 문서."""
        import difflib
        pool = [t for t in DOCS if t not in taken]
        hit = difflib.get_close_matches(proposed, pool, n=1, cutoff=0.5)
        return hit[0] if hit else (pool[0] if pool else "")

    # ② 배치 ─────────────────────────────────────────────
    def dispatch(self, s: State) -> dict:
        p = s["plan"]
        return {"log": [f"② 배치   {p.get('바퀴', 1)}바퀴 · 리서처 {len(p.get('배치', []))}명 동시 파견"]}

    def fanout(self, s: State):
        p = s["plan"]
        if not p.get("배치"):
            return "review"
        latest = accepted(s.get("sections", []))
        toc = p["목차"]
        sends = []
        for i in p["배치"]:
            avoid = {t["시작문서"] for j, t in enumerate(toc) if j != i and t["시작문서"]}
            avoid |= {d for n, sec in latest.items() if n != i for d in sec["읽은문서"]}   # 2바퀴부터: 남이 실제로 읽은 문서까지
            avoid = sorted(avoid) if self.sw["구역"] else []
            self.bump("보낸피하기", len(avoid))
            sends.append(Send("researcher", {"question": s["question"], "task": {**toc[i], "번호": i, "바퀴": p.get("바퀴", 1),
                                                                                  "피하기": avoid, "남의절": [t["절"] for j, t in enumerate(toc) if j != i]},
                                             "prior": latest.get(i, {})}))
        return sends

    # ③ 조사 ─────────────────────────────────────────────
    def role_line(self, t: dict) -> str:
        return f"너는 {t['역할']}이다. {self.roster.get(t['역할'], self.roster[self.cfg['기본역할']])}."

    def read_one(self, agent: str, title: str, brief: str) -> str:
        """도구 ① — 문서를 통째로 읽고 지시에 맞는 대목만 간추린다. 원문은 이 함수 밖으로 나가지 않는다."""
        return self.llm(agent,
                        "너는 자료 조사 담당이다. 아래 문서를 읽고 지시를 쓰는 데 쓸 재료만 여섯 문장 이내로 간추려라. "
                        "지시를 그대로 다룬 문서는 드물다 — 지시의 일부에라도 쓸 수 있는 개념·연구 결과·방법·절차·조건·한계가 있으면 그것을 간추린다. "
                        f"문서 주제가 지시와 완전히 무관할 때만 첫 줄에 '{IRRELEVANT}'이라고만 쓴다. 머리말 없이 바로 쓴다. "
                        f"문서가 영어면 한국어로 간추린다.\n[지시] {brief}",
                        f"[문서: {title}]\n{DOCS[title]}")

    def candidates(self, read: list, avoid: list, widen: bool) -> list:
        used = set(read) | set(avoid)
        linked = sorted({x for d in read for x in LINKS.get(d, []) if x in DOCS} - used)
        rest = [x for x in DOCS if x not in used]
        if widen:                                     # 넓히기: 링크 후보 뒤에 코퍼스 전체를 붙인다
            return linked + [x for x in rest if x not in linked]
        return linked or rest or [x for x in DOCS if x not in read]

    def pick(self, agent: str, t: dict, read: list, avoid: list, widen: bool) -> str | None:
        """도구 ② — 다음에 읽을 문서를 후보 중 하나 고른다. 없으면 None(그만 읽음)."""
        cand = self.candidates(read, avoid, widen)[: self.cfg["후보상한"]]
        if not cand:
            return None
        raw = self.llm(agent,
                       f"{self.role_line(t)} 맡은 절을 쓰기 위해 다음에 읽을 문서를 후보 중에서 정확히 하나 고른다. "
                       "제목만 보고 지시에 가장 쓸모 있을 문서를 고른다. 더 읽을 것이 없으면 그만둔다.\n"
                       'JSON으로만: {"문서":"후보에 있는 제목 그대로"} 또는 {"문서":null}',
                       f"[맡은 절] {t['절']}\n[지시] {t['지시']}\n[이미 읽음] {', '.join(read) or '없음'}\n"
                       f"[후보 {len(cand)}건 — 한 줄에 하나]\n" + "\n".join(f"- {c}" for c in cand))
        obj = jload(raw, {})
        choice = str(obj.get("문서") or "").strip().strip("-«» ")
        if not choice:
            return None                                   # 모델이 '그만 읽음'을 고른 것
        if choice in cand:
            return choice
        # 제목을 조금 틀리게 옮기거나 두 제목을 붙여 답한 경우 — 후보 안에서 바로잡는다 (바로잡은 횟수는 따로 센다)
        import difflib
        parts = [p.strip() for p in re.split(r"\s*\|\s*|\n", choice) if p.strip()]
        for p in parts:
            hit = p if p in cand else (difflib.get_close_matches(p, cand, n=1, cutoff=0.85) or [None])[0]
            if hit:
                self.bump("고르기보정")
                return hit
        self.bump("고르기실패")
        return None

    def write(self, agent: str, t: dict, notes: list, prior: dict) -> dict:
        material = "\n\n".join(f"[{d}]\n{n}" for d, n in notes if not irrelevant(n)) or "(쓸 만한 자료 없음)"
        again = (f"\n[지난 원고 — 부족 사유: {prior.get('부족') or '-'}]\n{prior.get('본문', '')}" if prior else "")
        raw = self.llm(agent,
                       f"{self.role_line(t)} 아래 자료만 근거로 보고서의 한 절을 써라. 여섯 문장 이상 열두 문장 이하, 600자 이상. "
                       "문장마다 마침표 바로 뒤에 근거 문서 제목을 «문서명»으로 붙여라(제목은 자료의 [ ] 안 그대로). "
                       "자료에 없는 내용은 쓰지 마라. 다른 절이 다룰 내용은 쓰지 마라. "
                       "지시를 자료로 다 채웠으면 충분을 true, 못 채웠으면 false 로 하고 무엇이 비었는지 한 문장으로 적어라.\n"
                       'JSON으로만: {"본문":"...","충분":true,"부족":""}',
                       f"[맡은 절] {t['절']}\n[지시] {t['지시']}\n[다른 절(쓰지 말 것)] {', '.join(t.get('남의절', [])) or '없음'}\n"
                       f"[모은 자료]\n{material}{again}", max_tokens=self.cfg["답_최대토큰"])
        obj = jload(raw, None)
        if obj is None:                                  # JSON 이 깨졌을 때만 원문에서 건진다 (일부러 빈 본문을 낸 경우는 그대로 둔다)
            m = re.search(r'"본문"\s*:\s*"(.*?)"\s*,\s*"충분"', raw or "", re.S)
            obj = {"본문": m.group(1) if m else "", "충분": False, "부족": "원고 JSON 이 깨짐"}
        body = str(obj.get("본문") or "")
        return {"본문": body, "충분": bool(obj.get("충분")), "부족": str(obj.get("부족") or "")}

    def researcher(self, s: State) -> dict:
        t, prior = s["task"], s.get("prior") or {}
        agent = f"절{t['번호'] + 1}"
        read = list(prior.get("읽은문서", []))
        notes = [tuple(x) for x in prior.get("메모", [])]
        avoid = t.get("피하기") or []
        nxt = t["시작문서"] if t.get("시작문서") in DOCS and t["시작문서"] not in read else None
        widened = 0
        for _ in range(t["예산"]):                       # 예산 = 이번 바퀴에 새로 읽을 문서 수
            if nxt is None:
                widen = self.sw["넓히기"] and bool(notes) and irrelevant(notes[-1][1])
                widened += widen
                nxt = self.pick(agent, t, read, avoid, widen)
            if nxt is None:
                self.bump("일찍멈춤")                     # 예산이 남았는데 그만 읽음
                break
            notes.append((nxt, self.read_one(agent, nxt, t["지시"])))
            read.append(nxt)
            nxt = None
        self.bump("넓힌횟수", widened)
        draft = self.write(agent, t, notes, prior)
        draft["본문"], n_fixed = canon_cites(draft["본문"], read)
        self.bump("인용정규화", n_fixed)
        cites = re.findall(r"«([^»]+)»", draft["본문"])
        sec = {"번호": t["번호"], "바퀴": t["바퀴"], "절": t["절"], "역할": t["역할"], "지시": t["지시"],
               "시작문서": t["시작문서"], "피하기": avoid, "본문": draft["본문"], "충분": draft["충분"], "부족": draft["부족"],
               "읽은문서": read, "메모": [list(n) for n in notes], "인용": cites,
               "유효인용": [c for c in cites if c in read], "허위인용": sorted({c for c in cites if c not in read}),
               "넓힘": widened}
        mark = "충분" if sec["충분"] else f"부족({sec['부족'][:24]})"
        new_reads = read[len(prior.get("읽은문서", [])):]
        return {"sections": [sec], "visited": [[t["번호"], t["바퀴"], d] for d in new_reads],
                "log": [f"   ③ {agent} {t['역할']} «{t['절'][:16]}» {t['바퀴']}바퀴 {len(new_reads)}건 읽고 "
                        f"{len(sec['본문'])}자 · 인용 {len(cites)}곳 · {mark}" + (f" · 넓힘 {widened}" if widened else "")]}

    # ④ 점검 (모델 호출 없음) ──────────────────────────────────
    def review(self, s: State) -> dict:
        p, toc = s["plan"], s["plan"]["목차"]
        wheel = p.get("바퀴", 1)
        decisions = []
        for sec in s["sections"]:                       # 이번 바퀴에 들어온 두 번째 이후 원고 — 받을지 버릴지
            if sec["바퀴"] == wheel and wheel > 1:
                older = accepted([x for x in s["sections"] if x["번호"] == sec["번호"] and x["바퀴"] < wheel])
                ok, why = better(sec, older[sec["번호"]]) if older else (True, "첫 원고")
                decisions.append({"번호": sec["번호"], "바퀴": wheel, "받음": ok, "사유": why})
        latest = accepted(s["sections"])
        gaps = []
        if self.sw["재위임"] and wheel < self.cfg["최대바퀴"]:
            gaps = [i for i in range(len(toc)) if not latest.get(i, {}).get("충분", False)]
        if not gaps:
            why = "재위임 끔" if not self.sw["재위임"] else ("바퀴 상한" if wheel >= self.cfg["최대바퀴"] else "모두 충분")
            return {"plan": {**p, "배치": [], "종료": why}, "decisions": decisions,
                    "log": [f"④ 점검   {len(toc)}절 · 다시 보낼 절 없음 ({why})"]}
        self.bump("재위임절", len(gaps))
        newtoc = [dict(t) for t in toc]
        for i in gaps:
            prev = latest.get(i, {})
            newtoc[i]["지시"] = (f"{toc[i]['지시']} (재위임: 지난번에 «{'», «'.join(prev.get('읽은문서', [])) or '없음'}»를 읽었지만 "
                                 f"'{prev.get('부족') or '근거가 모자랐다'}'. 그 빈 곳을 채울 아직 안 읽은 문서를 찾아라)")
        return {"plan": {**p, "목차": newtoc, "배치": gaps, "바퀴": wheel + 1}, "decisions": decisions,
                "log": [f"④ 점검   {len(toc)}절 중 부족 {len(gaps)}절 → «{'», «'.join(toc[i]['절'][:12] for i in gaps)}» 재위임"]}

    def route(self, s: State) -> str:
        return "more" if s["plan"].get("배치") else "done"

    # ⑤ 종합 ─────────────────────────────────────────────
    def synthesize(self, s: State) -> dict:
        order = [sec for _, sec in sorted(accepted(s["sections"]).items())]
        outline = "\n".join(f"{i + 1}. {x['절']}: {x['본문'][:90]}…" for i, x in enumerate(order))
        obj = jload(self.llm("코디네이터",
                             "너는 보고서를 마무리하는 편집자다. 아래는 리서처들이 각자 쓴 절의 제목과 첫머리다. "
                             "본문은 고치지 말고, 전체를 여는 머리말과 닫는 맺음말만 써라. 각각 세 문장 이내. 새 사실을 더하지 마라.\n"
                             'JSON으로만: {"머리말":"...","맺음말":"..."}',
                             f"[질문] {s['question']}\n[보고서 제목] {s['plan']['제목']}\n[절 목록]\n{outline}"), {})
        parts = [f"# {s['plan']['제목']}", str(obj.get("머리말") or "")]
        parts += [f"## {i + 1}. {x['절']}  _({x['역할']})_\n\n{x['본문']}" for i, x in enumerate(order)]
        parts.append(f"## 맺음말\n\n{obj.get('맺음말') or ''}")
        report = "\n\n".join(p for p in parts if p.strip())
        return {"report": report, "log": [f"⑤ 종합   {len(order)}절 이어붙임 → 보고서 {len(report):,}자 (편집자는 절 첫머리만 봤다)"]}

    # ⑥ 평가 (모델 호출 없음) ──────────────────────────────────
    def evaluate(self, s: State) -> dict:
        m = M.compute(report=s["report"], sections=s["sections"], accepted=accepted(s["sections"]),
                      reads=[v[2] for v in s["visited"]], docs=DOCS, usage=self.llm.usage, path=self.path,
                      budget=self.cfg["절수"] * self.cfg["절예산"])
        return {"metrics": m, "log": [M.one_line(m)]}

    def build(self):
        g = StateGraph(State)
        for name in ("plan", "dispatch", "researcher", "review", "synthesize", "evaluate"):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "plan")
        g.add_edge("plan", "dispatch")
        g.add_conditional_edges("dispatch", self.fanout, ["researcher", "review"])     # ②⇉③ 절마다 Send
        g.add_edge("researcher", "review")
        g.add_conditional_edges("review", self.route, {"more": "dispatch", "done": "synthesize"})   # ④→② 재위임
        g.add_edge("synthesize", "evaluate")
        g.add_edge("evaluate", END)
        return g.compile()


def research(question: str, switches: dict | None = None, label: str = "기본", save: bool = True,
             stream_cb=None, meta: dict | None = None, **overrides) -> dict:
    """그래프를 끝까지 돌리고, 실행 기록(output/runs.jsonl)과 보고서(output/reports/)를 남긴다."""
    team = Team(switches, **overrides)
    start = {"question": question, "plan": {}, "sections": [], "visited": [], "decisions": [], "report": "",
             "metrics": {}, "log": []}
    t0 = time.time()
    state: dict = dict(start)
    for update in team.build().stream(start, stream_mode="updates"):
        for node, val in update.items():
            if not isinstance(val, dict):
                continue
            for k, v in val.items():
                state[k] = state.get(k, []) + v if k in ("sections", "visited", "decisions", "log") else v
            if stream_cb:
                stream_cb(node, val, state)
    run = {"id": datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "kind": "multi", "label": label,
           "question": question, "model": team.llm.model, "switches": team.sw,
           "settings": {k: team.cfg[k] for k in ("절수", "절예산", "최대바퀴")}, "seconds": round(time.time() - t0, 1),
           "plan": state["plan"], "sections": state["sections"], "decisions": state["decisions"], "visited": state["visited"],
           "report": state["report"], "metrics": state["metrics"], "usage": team.llm.usage, "path": team.path, "log": state["log"],
           **(meta or {})}
    if save:
        save_run(run)
    return run


def save_run(run: dict) -> None:
    (OUT / "reports").mkdir(parents=True, exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")
    (OUT / "reports" / f"{run['id']}_{run['label']}.md").write_text(
        f"<!-- {run['kind']} · {run['label']} · {run['model']} · {run['question']} -->\n\n{run['report']}\n", encoding="utf-8")


def load_runs() -> list[dict]:
    path = OUT / "runs.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def question_by_id(qid: str) -> str:
    return next(q["질문"] for q in QUESTIONS if q["id"] == qid)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="교육법 딥리서처")
    ap.add_argument("question", nargs="*")
    ap.add_argument("--q", help="질문 세트 id (예: Q4)")
    ap.add_argument("--off", nargs="*", default=[], help="끌 장치: 배정 구역 역할 재위임 넓히기")
    a = ap.parse_args()
    q = question_by_id(a.q) if a.q else (" ".join(a.question) or QUESTIONS[0]["질문"])
    res = research(q, {k: False for k in a.off}, label="기본" if not a.off else "끔-" + "-".join(a.off),
                   stream_cb=lambda node, val, st: [print(l) for l in val.get("log", [])])
    print("\n" + res["report"])
