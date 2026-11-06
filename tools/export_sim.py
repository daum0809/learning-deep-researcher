"""저장된 실행(output/runs.jsonl)을 3D 여행 시뮬레이터의 이벤트로 바꿔 static/sim/data/ 에 쓴다. 모델 호출 없음.

실행 기록에는 절 · 바퀴 · 읽은 문서 · 메모 · 원고 · 두 번째 원고 결정이 모두 남아 있어서 여정을 다시 그릴 수 있다.
다만 원래 시각과 '고르기' 단계의 후보 목록은 기록하지 않았으므로 — 시각은 순서만 뜻하고, 고르기는 고른 문서만 보여 준다.
보낸 글자 수는 실제 프롬프트 길이를 어림한 값이다(원문은 60,000자에서 자른 길이 + 지시문).

    python tools/export_sim.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from graph import CFG, CORPUS, DOCS, LINKS, META, cards, irrelevant, load_runs  # noqa: E402

OUT = ROOT / "static" / "sim" / "data"
PROMPT = 450        # 지시문 몫 어림 (글자)


def corpus_view() -> dict:
    tc = json.loads((ROOT / "data" / "token_count.json").read_text(encoding="utf-8"))
    ratio = tc["total_chars"] / tc["total_tokens"]
    return {
        "summary": f"문서 {len(DOCS)}건 · {sum(len(x) for x in DOCS.values()):,}자 · 링크 {sum(len(v) for v in LINKS.values())}개",
        "docs": [{"title": t, "chars": len(x), "card": x[:CFG["카드글자"]], "kind": META.get(t, {}).get("type", "wiki"),
                  "lang": META.get(t, {}).get("lang", "ko"), "url": META.get(t, {}).get("url")} for t, x in DOCS.items()],
        "links": LINKS,
        "window_chars": int(CFG["창_토큰"] * ratio),
        "window_note": f"{CFG['모델']} 창 {CFG['창_토큰']:,}토큰 × 실측 {ratio:.2f}자",
    }


class Timeline:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, kind: str, section=None, **data) -> None:
        self.events.append({"seq": len(self.events), "t": len(self.events) * 700, "kind": kind, "section": section,
                            "thread": "코디네이터" if section is None else f"리서처-{section + 1}", **data})


def read_chars(doc: str) -> int:
    return min(len(DOCS.get(doc, "")), CFG["원문상한글자"])


def researcher_round(tl: Timeline, secs: list[dict], prev_reads: dict[int, int]) -> None:
    """한 바퀴: 리서처들이 동시에 도는 모습을 흉내 내려고 읽기 단계를 돌아가며 섞는다."""
    for s in secs:
        tl.emit("node_start", s["번호"], node="researcher", round=s["바퀴"],
                task={k: s.get(k) for k in ("번호", "절", "역할", "지시", "시작문서", "피하기")} | {"예산": CFG["절예산"]})
    queues = {}
    for s in secs:
        start = prev_reads.get(s["번호"], 0)
        queues[s["번호"]] = [(i, d, n) for i, (d, n) in enumerate(s["메모"]) if i >= start]
    step = 0
    while any(queues.values()):
        for s in secs:
            q = queues[s["번호"]]
            if not q:
                continue
            i, doc, note = q.pop(0)
            already = [d for d, _ in s["메모"][:i]]
            is_seed = i == 0 and doc == s.get("시작문서")
            if not is_seed:
                tl.emit("call_start", s["번호"], step="pick", candidates=[], already=already, section_title=s["절"],
                        sent_chars=PROMPT + 60 * 30, user_chars=0, truncated=False, reconstructed=True)
                tl.emit("call_end", s["번호"], step="pick", reply=json.dumps({"문서": doc}, ensure_ascii=False),
                        reply_chars=0, ms=0)
            tl.emit("call_start", s["번호"], step="read", doc=doc, user_chars=len(DOCS.get(doc, "")),
                    sent_chars=read_chars(doc) + PROMPT, truncated=len(DOCS.get(doc, "")) > CFG["원문상한글자"], brief=s["지시"])
            tl.emit("call_end", s["번호"], step="read", doc=doc, reply=note, reply_chars=len(note), ms=0,
                    irrelevant=irrelevant(note))
        step += 1
    for s in secs:
        material = sum(len(n) for _, n in s["메모"] if not irrelevant(n))
        tl.emit("call_start", s["번호"], step="write", notes_from=[d for d, n in s["메모"] if not irrelevant(n)],
                sent_chars=material + PROMPT * 2, user_chars=material, truncated=False)
        tl.emit("call_end", s["번호"], step="write", reply=json.dumps({"본문": s["본문"]}, ensure_ascii=False),
                reply_chars=len(s["본문"]), ms=0)
        tl.emit("node_end", s["번호"], node="researcher", round=s["바퀴"],
                result={"본문": s["본문"], "인용": s["인용"], "읽은문서": s["읽은문서"], "충분": s.get("충분"), "부족": s.get("부족")},
                log=[f"③ 절{s['번호'] + 1} {s['바퀴']}바퀴 · {len(s['본문'])}자 · 인용 {len(s['인용'])}곳 · "
                     + ("충분" if s.get("충분") else f"부족({(s.get('부족') or '')[:30]})")])


def to_events(run: dict, card_chars: int) -> list[dict]:
    tl = Timeline()
    m = run["metrics"]
    tl.emit("run_start", run_id=run["id"], mode="replay", model=f"{run['model']} · 저장된 기록",
            question=run["question"], label=run["label"], run_kind=run["kind"], corpus=corpus_view()["summary"],
            settings={"max_sections": run.get("settings", {}).get("절수", 1), "budget": run.get("settings", {}).get("절예산") or run.get("settings", {}).get("예산"),
                      "use_roles": run.get("switches", {}).get("역할", True), "assign_seeds": run.get("switches", {}).get("배정", True),
                      "respect_zones": run.get("switches", {}).get("구역", True)},
            switches=run.get("switches", {}), incident=None, reconstructed=True)
    if run["kind"] == "single":
        notes = run.get("notes", [])
        sec = {"번호": 0, "바퀴": 1, "절": "보고서 전체 (단일 에이전트)", "역할": "단일 에이전트", "지시": run["question"],
               "시작문서": run.get("settings", {}).get("첫문서", ""), "피하기": [], "메모": notes,
               "본문": run["report"], "인용": re.findall(r"«([^»]+)»", run["report"]), "읽은문서": [d for d, _ in notes],
               "충분": True, "부족": ""}
        tl.emit("node_start", node="plan", cards_chars=card_chars, single=True)
        tl.emit("node_end", node="plan", plan={"제목": run["plan"].get("제목", run["question"]),
                                                "목차": [{k: sec[k] for k in ("절", "역할", "지시", "시작문서")}]},
                log=["단일 에이전트 — 코디네이터 없음, 혼자 카드를 보고 첫 문서를 고름"])
        tl.emit("dispatch", tasks=[{k: sec[k] for k in ("번호", "절", "역할", "지시", "시작문서", "피하기")} | {"예산": len(notes)}], round=1)
        researcher_round(tl, [sec], {})
    else:
        secs = run["sections"]
        r1 = sorted([s for s in secs if s["바퀴"] == 1], key=lambda s: s["번호"])
        tl.emit("node_start", node="plan", cards_chars=card_chars)
        tl.emit("call_start", step="plan", sent_chars=card_chars + PROMPT * 3, user_chars=card_chars, truncated=False,
                user_preview=f"[질문] {run['question']}\n[문서 카드 {len(DOCS)}장] …")
        toc = [{"절": s["절"], "역할": s["역할"], "지시": s["지시"], "시작문서": s.get("시작문서", "")} for s in r1]
        tl.emit("call_end", step="plan", reply=json.dumps({"제목": run["plan"].get("제목"), "목차": toc}, ensure_ascii=False),
                reply_chars=0, ms=0)
        tl.emit("node_end", node="plan", plan={"제목": run["plan"].get("제목"), "목차": toc},
                log=[l for l in run.get("log", []) if l.startswith("①")])
        prev: dict[int, int] = {}
        rounds = sorted({s["바퀴"] for s in secs})
        for r in rounds:
            batch = sorted([s for s in secs if s["바퀴"] == r], key=lambda s: s["번호"])
            tl.emit("dispatch", round=r, tasks=[{k: s.get(k) for k in ("번호", "절", "역할", "지시", "시작문서", "피하기")}
                                                | {"예산": CFG["절예산"]} for s in batch])
            researcher_round(tl, batch, prev)
            for d in [d for d in run.get("decisions", []) if d["바퀴"] == r]:
                tl.emit("decision", d["번호"], round=r, accepted=d["받음"], reason=d["사유"])
            nxt = sorted({s["번호"] for s in secs if s["바퀴"] == r + 1})
            tl.emit("review", round=r, gaps=nxt,
                    log=[l for l in run.get("log", []) if l.startswith("④")][rounds.index(r): rounds.index(r) + 1])
            for s in batch:
                prev[s["번호"]] = len(s["메모"])
        coord_chars = run.get("usage", {}).get("코디네이터", {}).get("chars", 0)
        tl.emit("node_start", node="synthesize", sections=len({s["번호"] for s in secs}))
        tl.emit("call_start", step="edit", sent_chars=max(coord_chars - card_chars - PROMPT * 3, 800), user_chars=0,
                truncated=False, user_preview="[절 목록] 절마다 제목과 첫머리 90자")
        tl.emit("call_end", step="edit", reply="(머리말·맺음말)", reply_chars=0, ms=0)
        tl.emit("node_end", node="synthesize", report=run["report"], log=[l for l in run.get("log", []) if l.startswith("⑤")])
    tl.emit("metrics", metrics={k: m.get(k) for k in ("경보합", "허위인용", "코퍼스밖인용", "빈절", "근거율", "중복률", "인용편중",
                                                     "읽고안쓴", "예산소진율", "코디네이터창", "리서처최대창", "호출")})
    tl.emit("run_end", report=run["report"], log=run.get("log", []))
    return tl.events


def main() -> None:
    (OUT / "events").mkdir(parents=True, exist_ok=True)
    (OUT / "corpus.json").write_text(json.dumps(corpus_view(), ensure_ascii=False), encoding="utf-8")
    (OUT / "docs.json").write_text(json.dumps({t: x[:4000] for t, x in DOCS.items()}, ensure_ascii=False), encoding="utf-8")
    card_chars = len(cards())
    index = []
    for run in load_runs():
        ev = to_events(run, card_chars)
        (OUT / "events" / f"{run['id']}.json").write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
        index.append({"id": run["id"], "label": run["label"], "kind": run["kind"], "qid": run.get("qid"),
                      "question": run["question"], "events": len(ev), "alarms": run["metrics"].get("경보합", 0),
                      "note": run.get("note")})
    (OUT / "runs.json").write_text(json.dumps(list(reversed(index)), ensure_ascii=False), encoding="utf-8")
    print(f"시뮬레이터 데이터: 실행 {len(index)}개 → {OUT}")


if __name__ == "__main__":
    main()
