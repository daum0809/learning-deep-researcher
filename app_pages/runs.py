import streamlit as st

from ui import render_run, run_title, runs

all_runs = list(reversed(runs()))
if not all_runs:
    st.info("아직 저장된 실행이 없어요. '조사하기'에서 한 번 돌려 보세요.", icon=":material/info:")
    st.stop()

kinds = st.pills("종류", ["다중", "단일 대조군"], selection_mode="multi", default=["다중", "단일 대조군"], key="kinds")
qids = sorted({r.get("qid") or "직접" for r in all_runs})
qsel = st.pills("질문", qids, selection_mode="multi", default=qids, key="qsel")
pool = [r for r in all_runs if (("다중" in kinds and r["kind"] == "multi") or ("단일 대조군" in kinds and r["kind"] == "single"))
        and (r.get("qid") or "직접") in qsel]
if not pool:
    st.stop()
pick = st.selectbox(f"실행 ({len(pool)}개, 최신순)", range(len(pool)), format_func=lambda i: run_title(pool[i]), key="pick")
render_run(pool[pick], key="one")
