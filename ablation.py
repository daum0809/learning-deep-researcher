"""스위치를 하나씩 끄고, 단일 에이전트 대조군과도 견준다. 결과: output/ablation.json

- 같은 질문 · 같은 시행 번호마다 '기본'을 먼저 돌리고, 그 기본이 실제로 읽은 횟수를 대조군의 읽기 예산으로 준다.
- 한 번 돌린 결과로 방향을 단정하지 않는다 — 같은 설정도 시행마다 십몇 %p씩 움직인다. 시행 n 번의 평균과 범위(최소~최대)를 낸다.
- 스위치를 껐다고 로그만 찍고 코드 경로는 그대로인 사고를 막으려고, 실행마다 '경로 확인'을 한다
  (예: 구역 끔 → 실제로 보낸 피하기 목록 0건, 재위임 끔 → 2바퀴 원고 0건).

    python ablation.py                                  # 기본: Q1 Q4 Q9 × 3회 × (기본 + 끔 5종 + 단일)
    python ablation.py --questions Q4 --trials 2 --parallel 2
    python ablation.py --summary                        # 이미 돈 결과로 표만 다시
"""
from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

from baseline import single
from graph import CFG, OUT, load_runs, question_by_id, research

DONE = {r["label"]: r for r in load_runs() if r["label"].startswith("abl-")}   # 이어 하기: 이미 저장된 실행은 다시 돌리지 않는다


def cached(label: str, make):
    """같은 라벨의 실행이 runs.jsonl 에 있으면 그것을 쓴다 (크레딧이 떨어져 멈춘 뒤 이어 돌릴 때 돈을 두 번 쓰지 않게)."""
    if label in DONE:
        return DONE[label]
    return make()

CONFIGS = {
    "기본": {},
    "배정 끔": {"배정": False},
    "구역 끔": {"구역": False},
    "역할 끔": {"역할": False},
    "재위임 끔": {"재위임": False},
    "넓히기 끔": {"넓히기": False},
}
SHOW = ["경보합", "근거율", "중복률", "인용편중", "읽고안쓴", "예산소진율", "보고서자수", "읽기횟수", "호출", "리서처최대창"]
PATH = OUT / "ablation.json"


def path_check(config: str, run: dict) -> tuple[bool, str]:
    """스위치가 실제로 코드 경로를 바꿨는지 실행 기록으로 확인한다."""
    p, plan, secs = run["path"], run.get("plan", {}), run.get("sections", [])
    if config == "배정 끔":
        seeds = [t["시작문서"] for t in plan.get("목차", [])]
        return all(s == "" for s in seeds), f"시작문서 {seeds}"
    if config == "구역 끔":
        return p.get("보낸피하기", 0) == 0, f"보낸 피하기 {p.get('보낸피하기')}건"
    if config == "역할 끔":
        roles = sorted({t["역할"] for t in plan.get("목차", [])})
        return roles == [CFG["기본역할"]], f"역할 {roles}"
    if config == "재위임 끔":
        rounds = max((s["바퀴"] for s in secs), default=1)
        return rounds == 1, f"최대 바퀴 {rounds}"
    if config == "넓히기 끔":
        return p.get("넓힌횟수", 0) == 0, f"넓힌 횟수 {p.get('넓힌횟수')}"
    if config == "단일":
        return run["metrics"]["예산소진율"] == 100.0, f"예산 소진 {run['metrics']['예산소진율']}% · 강제 {run['settings'].get('강제선택')}"
    return True, "-"


def row(qid: str, trial: int, config: str, run: dict) -> dict:
    ok, why = path_check(config, run)
    m = run["metrics"]
    return {"qid": qid, "trial": trial, "config": config, "run_id": run["id"], "경로확인": ok, "경로근거": why,
            **{k: (len(m[k]) if isinstance(m.get(k), list) else m.get(k)) for k in SHOW},
            "허위인용": m["허위인용"], "빈절": m["빈절"], "path": run["path"]}


def one_trial(qid: str, trial: int, configs: list[str], with_single: bool) -> list[dict]:
    q = question_by_id(qid)
    meta = lambda c: {"qid": qid, "trial": trial, "config": c}
    base = cached(f"abl-{qid}-t{trial}-기본", lambda: research(q, {}, label=f"abl-{qid}-t{trial}-기본", meta=meta("기본")))
    rows = [row(qid, trial, "기본", base)]
    print(f"[{qid} t{trial}] 기본 — 읽기 {base['metrics']['읽기횟수']}회 · 경보 {base['metrics']['경보합']} · 근거율 {base['metrics']['근거율']}%", flush=True)
    for c in configs:
        if c == "기본":
            continue
        lab = f"abl-{qid}-t{trial}-{c.replace(' ', '')}"
        r = cached(lab, lambda: research(q, CONFIGS[c], label=lab, meta=meta(c)))
        rows.append(row(qid, trial, c, r))
        print(f"[{qid} t{trial}] {c} — 경로확인 {rows[-1]['경로확인']} ({rows[-1]['경로근거']}) · 근거율 {r['metrics']['근거율']}%", flush=True)
    if with_single:
        lab = f"abl-{qid}-t{trial}-단일"
        s = cached(lab, lambda: single(q, budget=base["metrics"]["읽기횟수"], label=lab, log=lambda *_: None, meta=meta("단일")))
        rows.append(row(qid, trial, "단일", s))
        print(f"[{qid} t{trial}] 단일 — 예산 {s['settings']['예산']} · {rows[-1]['경로근거']} · 근거율 {s['metrics']['근거율']}%", flush=True)
    return rows


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r["qid"], {}).setdefault(r["config"], []).append(r)
    summary = {}
    for qid, by in out.items():
        summary[qid] = {}
        for c, rs in by.items():
            summary[qid][c] = {"n": len(rs), "경로확인": all(x["경로확인"] for x in rs)}
            for k in SHOW:
                vals = [x[k] for x in rs if isinstance(x.get(k), (int, float))]
                if vals:
                    summary[qid][c][k] = {"평균": round(statistics.mean(vals), 1), "최소": min(vals), "최대": max(vals)}
    return summary


def print_table(summary: dict) -> None:
    cols = ["경보합", "근거율", "중복률", "인용편중", "읽고안쓴", "예산소진율", "보고서자수"]
    for qid, by in summary.items():
        print(f"\n■ {qid} — {question_by_id(qid)}")
        print(f"{'설정':<8} n  경로 " + " ".join(f"{c:>16}" for c in cols))
        for c, v in by.items():
            cells = []
            for k in cols:
                s = v.get(k)
                cells.append(f"{s['평균']:>6} [{s['최소']}~{s['최대']}]".rjust(16) if s else " " * 16)
            print(f"{c:<8} {v['n']}  {'✓' if v['경로확인'] else '✗'}   " + " ".join(cells))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", nargs="*", default=["Q1", "Q4", "Q9"])
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--configs", nargs="*", default=list(CONFIGS))
    ap.add_argument("--no-single", action="store_true")
    ap.add_argument("--parallel", type=int, default=3, help="동시에 돌릴 (질문, 시행) 묶음 수")
    ap.add_argument("--summary", action="store_true", help="새로 돌리지 않고 저장된 결과로 표만")
    a = ap.parse_args()
    rows = []
    if a.summary:                                   # 저장된 실행만으로 표를 다시 만든다
        for lab, r in DONE.items():
            if r.get("config"):
                rows.append(row(r["qid"], r["trial"], r["config"], r))
    else:
        jobs = [(q, t) for q in a.questions for t in range(1, a.trials + 1)]
        with ThreadPoolExecutor(a.parallel) as pool:
            futures = [pool.submit(one_trial, q, t, a.configs, not a.no_single) for q, t in jobs]
            for f in as_completed(futures):
                rows.extend(f.result())
                PATH.write_text(json.dumps({"model": CFG["모델"], "rows": rows, "summary": summarize(rows)},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    summary = summarize(rows)
    PATH.write_text(json.dumps({"model": CFG["모델"], "rows": rows, "summary": summary}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print_table(summary)
    bad = [r for r in rows if not r["경로확인"]]
    if bad:
        print(f"\n⚠️ 경로 확인 실패 {len(bad)}건 — 스위치가 코드 경로를 바꾸지 못했다:", [(r['qid'], r['config'], r['경로근거']) for r in bad])


if __name__ == "__main__":
    main()
