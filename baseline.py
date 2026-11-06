"""단일 에이전트 대조군 — 공정하게 맞춘다.

같은 것: 모델 · 자료(corpus.json) · 도구(read_one 읽기 · pick 고르기 · 넓히기) · 첫 문서를 고를 때 보는 카드 목록 · 읽기 예산.
다른 것: 코디네이터가 없다(목차·역할·구역 없음). 한 에이전트가 혼자 읽고, 혼자 전체 보고서를 쓴다.

예산은 짝이 되는 다중 에이전트 실행이 '실제로 읽은 횟수'와 똑같이 준다. 그리고 끝까지 다 쓰게 한다:
고르기에서 그만두려 하면 다음 후보를 강제로 읽게 하고, 그 횟수(강제선택)를 기록한다.
중간에 멈춘 대조군을 이기는 건 실험이 아니라 상대 손발을 묶은 것이다.

    python baseline.py --q Q4            # 예산 = 절수 × 절예산
    python baseline.py --q Q4 --budget 16
"""
from __future__ import annotations

import argparse
import re
import time
import uuid
from datetime import datetime

import metrics as M
from graph import CFG, DOCS, Team, canon_cites, cards, irrelevant, jload, question_by_id, QUESTIONS, save_run


def single(question: str, budget: int | None = None, save: bool = True, label: str = "대조군-단일", log=print,
           meta: dict | None = None) -> dict:
    team = Team()                                    # 같은 LLM 래퍼·같은 도구·같은 스위치(넓히기 포함)
    agent = "단일"
    budget = budget or CFG["절수"] * CFG["절예산"]
    t0 = time.time()
    task = {"절": "보고서 전체", "지시": question, "역할": team.cfg["기본역할"], "번호": 0}

    # 첫 문서: 코디네이터와 똑같이 카드 목록을 보고 고른다
    raw = team.llm(agent, "너는 리서처다. 질문에 답하는 긴 보고서를 혼자 쓰려고 한다. 문서 카드 목록을 보고 가장 먼저 읽을 문서를 하나 고른다.\n"
                          'JSON으로만: {"문서":"카드에 있는 제목 그대로"}', f"[질문] {question}\n[문서 카드 {len(DOCS)}장]\n{cards()}")
    first = str(jload(raw, {}).get("문서") or "")
    read, notes, forced, widened = [], [], 0, 0
    nxt = first if first in DOCS else None
    while len(read) < budget:
        if nxt is None:
            widen = team.sw["넓히기"] and bool(notes) and irrelevant(notes[-1][1])
            widened += widen
            nxt = team.pick(agent, task, read, [], widen)
        was_forced = False
        if nxt is None:                               # 그만두려 함 → 예산을 다 쓰게 다음 후보를 강제로 읽힌다
            cand = team.candidates(read, [], True)
            if not cand:
                break
            nxt, forced, was_forced = cand[0], forced + 1, True
        notes.append((nxt, team.read_one(agent, nxt, question)))
        read.append(nxt)
        log(f"   단일 {len(read)}/{budget} «{nxt[:40]}»" + (" (강제)" if was_forced else ""))
        nxt = None

    material = "\n\n".join(f"[{d}]\n{n}" for d, n in notes if not irrelevant(n)) or "(쓸 만한 자료 없음)"
    n_sec = CFG["절수"]
    raw = team.llm(agent,
                   "너는 리서처다. 아래 자료만 근거로 질문에 답하는 보고서를 혼자 써라. "
                   f"제목(# ) 한 줄, 머리말 두세 문장, 내용 단위로 나눈 절 {n_sec}개(## 1. 절 제목), 맺음말(## 맺음말). "
                   "절마다 여섯 문장 이상, 600자 이상. 문장마다 마침표 바로 뒤에 근거 문서 제목을 «문서명»으로 붙여라(제목은 자료의 [ ] 안 그대로). "
                   "자료에 없는 내용은 쓰지 마라. 마크다운 보고서만 출력한다.",
                   f"[질문] {question}\n[모은 자료]\n{material}", max_tokens=8000)
    report = (raw or "").strip()
    report = re.sub(r"^```(markdown)?\s*|\s*```$", "", report)
    report, n_fixed = canon_cites(report, read)          # 다중 에이전트와 똑같은 인용 정규화
    team.bump("인용정규화", n_fixed)

    # 절별로 잘라 '빈 절' 경보를 다중 에이전트와 같은 잣대로 센다
    secs = {}
    for i, chunk in enumerate(re.split(r"\n(?=## )", report)):
        if chunk.startswith("## ") and "맺음말" not in chunk[:12]:
            cites = re.findall(r"«([^»]+)»", chunk)
            secs[len(secs)] = {"번호": len(secs), "바퀴": 1, "절": chunk.splitlines()[0][3:].strip(), "본문": chunk,
                               "인용": cites, "유효인용": [c for c in cites if c in read], "허위인용": [c for c in cites if c not in read],
                               "읽은문서": read}
    team.path.update({"강제선택": forced, "넓힌횟수": widened})
    m = M.compute(report=report, sections=list(secs.values()), accepted=secs, reads=read, docs=DOCS,
                  usage=team.llm.usage, path=team.path, budget=budget)
    log(M.one_line(m).replace("⑥ 평가", "단일 평가"))
    run = {"id": datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "kind": "single", "label": label,
           "question": question, "model": team.llm.model, "switches": {"넓히기": team.sw["넓히기"]},
           "settings": {"예산": budget, "첫문서": first, "강제선택": forced}, "seconds": round(time.time() - t0, 1),
           "plan": {"제목": report.splitlines()[0].lstrip("# ") if report else question, "목차": []},
           "sections": list(secs.values()), "decisions": [], "visited": [[0, 1, d] for d in read],
           "notes": [list(n) for n in notes], "report": report, "metrics": m, "usage": team.llm.usage, "path": team.path,
           "log": [f"단일 에이전트 · 예산 {budget}건 · 읽음 {len(read)}건 · 강제선택 {forced}"], **(meta or {})}
    if save:
        save_run(run)
    return run


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="단일 에이전트 대조군")
    ap.add_argument("question", nargs="*")
    ap.add_argument("--q")
    ap.add_argument("--budget", type=int)
    a = ap.parse_args()
    q = question_by_id(a.q) if a.q else (" ".join(a.question) or QUESTIONS[0]["질문"])
    r = single(q, a.budget)
    print("\n" + r["report"])
