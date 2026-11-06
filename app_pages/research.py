import streamlit as st

from graph import CFG, QUESTIONS, research
from ui import render_run, runs

st.caption("코디네이터가 문서 카드만 보고 목차를 짜고, 리서처들이 자기 구역의 문서만 읽고 절을 씁니다. "
           f"모델: {CFG['모델']} · 한 번에 약 1분, 30~50회 호출(약 200원).")

with st.form("ask"):
    preset = st.selectbox("질문 세트", [f"{q['id']} · {q['유형']} — {q['질문']}" for q in QUESTIONS] + ["직접 입력"], key="preset")
    custom = st.text_area("직접 입력할 질문", key="custom", placeholder="예: 성인 학습자에게 거꾸로 수업은 효과가 있나?", height=80)
    st.markdown("**장치 스위치** — 끄면 그 장치 없이 돌아요")
    with st.container(horizontal=True):
        sw = {k: st.toggle(k, value=True, key=f"sw_{k}", help=CFG["_스위치설명"][k]) for k in CFG["스위치"]}
    go = st.form_submit_button("조사 시작", icon=":material/play_arrow:", type="primary")

if preset != "직접 입력":
    why = QUESTIONS[[f"{q['id']} · {q['유형']} — {q['질문']}" for q in QUESTIONS].index(preset)]["왜 나눌 만한가"]
    st.caption(f"왜 나눌 만한가: {why}")

if go:
    question = custom.strip() if preset == "직접 입력" else preset.split(" — ", 1)[1]
    if not question:
        st.warning("질문을 입력해 주세요.")
        st.stop()
    off = [k for k, v in sw.items() if not v]
    with st.status("조사 중…", expanded=True) as status:
        def show(node, val, state):
            for line in val.get("log", []):
                st.write(line)
        try:
            run = research(question, {k: False for k in off}, label="기본" if not off else "끔-" + "-".join(off),
                           stream_cb=show, meta={"source": "app"})
        except Exception as exc:                     # 크레딧 부족·키 없음 등은 그대로 보여 준다
            status.update(label="조사 실패", state="error")
            st.error(f"{type(exc).__name__}: {exc}", icon=":material/error:")
            st.stop()
        status.update(label=f"완료 — 보고서 {len(run['report']):,}자 · 경보 {run['metrics']['경보합']}", state="complete", expanded=False)
    runs.clear()
    st.session_state.last_run = run

if "last_run" in st.session_state:
    render_run(st.session_state.last_run, key="last")
