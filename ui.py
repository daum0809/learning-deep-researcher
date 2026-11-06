"""화면 조각 — 여러 페이지가 함께 쓴다. 숫자만이 아니라 '절마다 누가 무엇을 읽고 무엇을 썼는지'를 보여 주는 것이 요점."""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

import metrics as M
from graph import CFG, DOCS, META, accepted, better, irrelevant, load_runs

CITE = re.compile(r"«([^»]+)»")


@st.cache_data(ttl=5)
def runs() -> list[dict]:
    return load_runs()


def run_title(r: dict) -> str:
    m = r["metrics"]
    alarm = "🚨" if m.get("경보합") else "✅"
    return f"{alarm} {r['id'][:15]} · {r['label']} · {r.get('qid') or r['question'][:22]}"


def kind_of(title: str) -> str:
    m = META.get(title, {})
    return "논문" if m.get("type") == "paper" else ("영어 위키" if m.get("lang") == "en" else "위키")


def cite_md(text: str, read: set[str]) -> str:
    """«인용»에 색을 입힌다: 실제로 읽은 문서는 파란 배경, 읽지 않았거나 코퍼스에 없는 제목은 빨간 배경."""
    def one(m: re.Match) -> str:
        t = m.group(1).replace("[", "(").replace("]", ")")
        if m.group(1) in read:
            return f":blue-background[«{t}»]"
        return f":red-background[«{t}» ⚠ {'안 읽음' if m.group(1) in DOCS else '없는 제목'}]"
    return CITE.sub(one, text.replace("$", "\\$"))


def render_metrics(r: dict) -> None:
    m = r["metrics"]
    st.markdown("**경보** — 0이어야 해요")
    with st.container(horizontal=True):
        for k in ("허위인용", "코퍼스밖인용", "빈절"):
            n = len(m[k])
            st.metric(k, n, border=True, help=f"{M.INFO[k][1]} · 볼 곳: {M.INFO[k][2]}", delta="경보" if n else None,
                      delta_color="inverse" if n else "off", delta_arrow="off")
    st.markdown("**신호** — 설정끼리 높낮이를 견줘요 (순위표 아님)")
    rows = []
    for k in ("근거율", "중복률", "인용편중", "읽고안쓴", "예산소진율", "코디네이터창", "리서처최대창"):
        v = m.get(k)
        rows.append({"지표": k, "값": len(v) if isinstance(v, list) else v, "무엇을 세나": M.INFO[k][1], "나쁘면 볼 장치": M.INFO[k][2]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, alt="신호 지표 표")
    tok = pd.DataFrame({"에이전트": list(m["토큰"].keys()), "입력 토큰": list(m["토큰"].values())})
    st.markdown(f"**컨텍스트 격리** — 누가 몇 토큰을 봤나 (창 {CFG['창_토큰']:,}토큰)")
    st.bar_chart(tok, x="에이전트", y="입력 토큰", horizontal=True, height=220, alt="에이전트별 입력 토큰 막대그래프")
    st.caption(f"호출 {m['호출']}회 · 입력 {m['입력토큰']:,} · 출력 {m['출력토큰']:,}토큰 · 경로 {m.get('경로', {})}")


def render_sections(r: dict) -> None:
    """절마다 바퀴별로: 시작 문서 · 피하기 · 읽은 문서와 메모 · 원고 · 자기 신고 · 두 번째 원고를 받았나."""
    secs = r.get("sections", [])
    final = accepted(secs) if r["kind"] == "multi" else {s["번호"]: s for s in secs}
    decisions = {(d["번호"], d["바퀴"]): d for d in r.get("decisions", [])}
    read_all = {v[2] for v in r.get("visited", [])}
    if r["kind"] == "single":
        st.info("단일 에이전트는 절을 나누지 않고 혼자 읽었습니다. 읽은 순서와 메모를 보여 줍니다.", icon=":material/person:")
        for i, (d, note) in enumerate(r.get("notes", []), 1):
            with st.expander(f"{i}. «{d}» · {kind_of(d)} · 원문 {len(DOCS.get(d, '')):,}자 → 메모 {len(note)}자"
                             + (" · 관련 없음" if irrelevant(note) else "")):
                st.markdown(note)
        return
    by = {}
    for s in sorted(secs, key=lambda x: (x["번호"], x["바퀴"])):
        by.setdefault(s["번호"], []).append(s)
    for n, drafts in sorted(by.items()):
        first = drafts[0]
        chosen = final.get(n)
        with st.container(border=True):
            st.markdown(f"#### {n + 1}. {first['절']}")
            st.caption(f"{first['역할']} · 시작 문서 «{first['시작문서'] or '자율'}» · 지시: {first['지시'][:200]}")
            if first.get("피하기"):
                st.caption("피하기(남의 구역): " + " · ".join(f"«{x}»" for x in first["피하기"]))
            for d in drafts:
                is_final = d is chosen
                dec = decisions.get((n, d["바퀴"]))
                reason = re.sub(r"[*_`\[\]]", "", d["부족"][:60]).strip()     # 굵게 표시가 깨지지 않게
                head = f"{d['바퀴']}바퀴 — {len(d['본문'])}자 · 인용 {len(d['인용'])}곳 · " + ("충분" if d["충분"] else f"부족: {reason}")
                badge = ":green-badge[최종본에 실림]" if is_final else ":gray-badge[실리지 않음]"
                if dec:
                    badge += f" :{'blue' if dec['받음'] else 'red'}-badge[{dec['사유']}]"
                st.markdown(f"**{head}** {badge}")
                prev_n = len(drafts[drafts.index(d) - 1]["읽은문서"]) if drafts.index(d) else 0
                cols = st.columns([2, 3])
                with cols[0]:
                    st.markdown("**읽은 문서 → 메모** (원문은 여기 남고 위로는 원고만)")
                    for title, note in d["메모"][prev_n:]:
                        tag = " :red-badge[관련 없음]" if irrelevant(note) else ""
                        with st.expander(f"«{title[:48]}» · {kind_of(title)} · {len(DOCS.get(title, '')):,}자 → {len(note)}자{tag}"):
                            st.markdown(note)
                    if d.get("넓힘"):
                        st.caption(f"넓히기 {d['넓힘']}번 — 직전 메모가 '관련 없음'이라 후보를 링크 바깥으로 넓힘")
                with cols[1]:
                    st.markdown("**올린 원고**")
                    st.markdown(cite_md(d["본문"], read_all) or "_(빈 원고)_")


def render_report(r: dict) -> None:
    read_all = {v[2] for v in r.get("visited", [])}
    with st.container(border=True):
        st.markdown(cite_md(r["report"], read_all))


def render_citation_check(r: dict, key: str) -> None:
    """인용이 제자리에 붙었는지 사람이 대조하는 도구: 문장 ↔ 리서처 메모 ↔ 원문."""
    read_all = {v[2] for v in r.get("visited", [])}
    cited = sorted({c for c in CITE.findall(r["report"]) if c in DOCS})
    if not cited:
        st.caption("인용된 문서가 없어요.")
        return
    doc = st.selectbox("대조할 인용 문서", cited, key=f"cc_{key}")
    sents = [s for s in M.sentences(r["report"]) if f"«{doc}»" in s]
    notes = [n for s in r.get("sections", []) for t, n in s.get("메모", []) if t == doc] or \
            [n for t, n in r.get("notes", []) if t == doc]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**이 문서를 인용한 문장 {len(sents)}개**")
        for s in sents:
            st.markdown(f"- {cite_md(s, read_all)}")
        st.markdown("**리서처가 남긴 메모**")
        for n in notes[:2]:
            st.info(n, icon=":material/edit_note:")
    with c2:
        q = st.text_input("원문에서 찾을 말 (문장의 핵심어)", key=f"kw_{key}", placeholder="예: 인출, spacing, 효과")
        text = DOCS[doc]
        if q:
            hits = [m.start() for m in re.finditer(re.escape(q), text, re.I)][:6]
            st.caption(f"«{doc}» 원문 {len(text):,}자 중 '{q}' {len(hits)}곳")
            for h in hits:
                st.code(text[max(0, h - 160): h + 220], language=None, wrap_lines=True)
        else:
            st.caption(f"«{doc}» 원문 앞부분 · {kind_of(doc)} · {len(text):,}자")
            st.code(text[:1200], language=None, wrap_lines=True)
    st.caption("읽지 않은 문서를 인용한 것은 코드가 잡지만(허위 인용), 읽은 문서를 엉뚱한 문장에 붙인 것(오귀속)은 이렇게 사람이 대조해야 잡힙니다.")


def render_run(r: dict, key: str) -> None:
    m = r["metrics"]
    st.markdown(f"**{r['question']}**")
    sw = ", ".join(k for k, v in r.get("switches", {}).items() if not v)
    st.caption(f"{'다중 에이전트' if r['kind'] == 'multi' else '단일 에이전트 대조군'} · {r['label']} · {r['model']} · "
               f"{r['seconds']}초 · 끈 장치: {sw or '없음'} · 보고서 {m['보고서자수']:,}자 · 읽기 {m['읽기횟수']}회")
    view = st.segmented_control("보기", ["보고서", "절별 추적", "인용 대조", "지표", "로그"], default="보고서",
                                key=f"view_{key}", label_visibility="collapsed")
    if view == "보고서":
        render_report(r)
    elif view == "절별 추적":
        render_sections(r)
    elif view == "인용 대조":
        render_citation_check(r, key)
    elif view == "지표":
        render_metrics(r)
    else:
        st.code("\n".join(r.get("log", [])), language=None, wrap_lines=True)
