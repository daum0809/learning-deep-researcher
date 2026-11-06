"""정답표도 판정 모델도 쓰지 않는 지표. 만들어진 글과 실제로 읽은 문서만 본다.

경보(alarm) — 0 이어야 한다. 0 이 아니면 볼 것도 없이 실패.
신호(signal) — 설정끼리 높낮이를 견준다. 순위표가 아니라 '어느 장치를 볼지' 가리키는 손가락.
설명이 한 줄로 안 되는 지표는 넣지 않았다(예: 인용 문서와 문장의 어휘 겹침 — 기준값을 정할 근거가 없다).
"""
from __future__ import annotations

import re
from collections import Counter

MIN_BODY = 100
CITE = re.compile(r"«([^»]+)»")

# 이름: (종류, 한 줄 설명, 이 숫자가 나쁘면 볼 장치)
INFO = {
    "허위인용":   ("경보", "아무도 읽지 않은 문서를 인용한 수", "③ 리서처 집필 프롬프트"),
    "코퍼스밖인용": ("경보", "코퍼스에 없는 제목(지어낸 제목)을 인용한 수", "③ 집필 · 제목 표기"),
    "빈절":       ("경보", "보고서에 실린 절 가운데 실제로 읽은 문서 인용이 0인 절 수", "① 배정 · ③ 넓히기 · ④ 재위임"),
    "근거율":     ("신호", "보고서 문장 중 실제 문서 «인용»이 붙은 문장의 비율(%)", "③ 절예산 · 메모 품질"),
    "중복률":     ("신호", "읽기 횟수 중 이미 다른 절이 읽은 문서를 또 읽은 몫(%)", "② 구역(피하기 목록) · ① 배정"),
    "인용편중":   ("신호", "인용이 가장 많은 문서 한 건의 몫(%)", "① 배정 · ② 구역"),
    "읽고안쓴":   ("신호", "읽었지만 보고서에 한 번도 인용되지 않은 문서 수", "① 배정 · ③ 고르기 · 넓히기"),
    "예산소진율": ("신호", "실제 읽은 횟수 ÷ (절수 × 절예산) (%). 대조군과 같은 양을 읽었는지 확인", "③ 고르기(일찍 그만둠)"),
    "코디네이터창": ("신호", "코디네이터가 본 입력 토큰 합 ÷ 창(%) — 원문을 안 보니 작아야 한다", "격리 설계"),
    "리서처최대창": ("신호", "리서처 한 명이 본 입력 토큰 합의 최댓값 ÷ 창(%)", "③ 절예산 · 격리"),
}


def sentences(text: str) -> list[str]:
    """문장 자르기 규칙(고정): 마침표 뒤에 붙은 «인용»은 앞 문장 몫으로 옮긴 뒤 자른다.
    이 규칙이 바뀌면 근거율이 바뀐다 — 지표가 좋아진 줄 알았는데 자르는 규칙이 바뀐 것인 사고를 막으려고 고정해 둔다."""
    moved = re.sub(r"([.!?。])\s*((?:«[^»]+»\s*)+)", lambda m: " " + m.group(2).strip() + m.group(1) + "\n", text)
    out = []
    for line in re.split(r"(?<=[.!?。])\s+|\n", moved):
        line = line.strip()
        if len(line) > 15 and not line.startswith("#"):
            out.append(line)
    return out


def compute(report: str, sections: list[dict], accepted: dict, reads: list[str], docs: dict, usage: dict,
            path: dict | None = None, budget: int | None = None, window: int = 200_000) -> dict:
    sents = sentences(report)
    cited_all = CITE.findall(report)
    cited = [c for c in cited_all if c in docs]
    read_set = set(reads)
    backed = [x for x in sents if any(c in docs for c in CITE.findall(x))]
    counts = Counter(cited)
    coord = usage.get("코디네이터", {}).get("in_tok", 0)
    researchers = {k: v.get("in_tok", 0) for k, v in usage.items() if k != "코디네이터"}
    empty = [s["절"] for s in accepted.values() if not [c for c in s.get("유효인용", []) if c in read_set]]
    m = {
        # 경보
        "허위인용": sorted({c for c in cited if c not in read_set}),
        "코퍼스밖인용": sorted({c for c in cited_all if c not in docs}),
        "빈절": empty,
        # 신호
        "근거율": round(100 * len(backed) / max(len(sents), 1), 1),
        "중복률": round(100 * (1 - len(read_set) / len(reads)), 1) if reads else 0.0,
        "인용편중": round(100 * counts.most_common(1)[0][1] / len(cited), 1) if cited else 0.0,
        "최다인용문서": counts.most_common(1)[0][0] if cited else None,
        "읽고안쓴": sorted(read_set - set(cited)),
        "예산소진율": round(100 * len(reads) / budget, 1) if budget else None,
        "코디네이터창": round(100 * coord / window, 1),
        "리서처최대창": round(100 * max(researchers.values(), default=0) / window, 1),
        # 참고 수치
        "문장수": len(sents), "근거문장": len(backed), "인용수": len(cited), "읽기횟수": len(reads), "읽은문서": len(read_set),
        "보고서자수": len(report), "절수": len(accepted), "원고수": len(sections),
        "재위임절": (path or {}).get("재위임절", 0), "경로": path or {},
        "토큰": {"코디네이터": coord, **researchers},
        "호출": sum(v.get("calls", 0) for v in usage.values()),
        "입력토큰": sum(v.get("in_tok", 0) for v in usage.values()),
        "출력토큰": sum(v.get("out_tok", 0) for v in usage.values()),
    }
    m["경보합"] = len(m["허위인용"]) + len(m["코퍼스밖인용"]) + len(m["빈절"])
    return m


def one_line(m: dict) -> str:
    alarm = "경보 0" if m["경보합"] == 0 else f"🚨경보 {m['경보합']} (허위 {len(m['허위인용'])} · 밖 {len(m['코퍼스밖인용'])} · 빈절 {len(m['빈절'])})"
    return (f"⑥ 평가   {alarm} · 근거율 {m['근거율']}% · 중복 {m['중복률']}% · 편중 {m['인용편중']}% · "
            f"읽고안씀 {len(m['읽고안쓴'])} · 예산 {m['예산소진율']}% · 창 코디 {m['코디네이터창']}%/리서처 최대 {m['리서처최대창']}% · "
            f"호출 {m['호출']}회")
