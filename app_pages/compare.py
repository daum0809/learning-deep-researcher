import pandas as pd
import streamlit as st

from ui import render_run, run_title, runs

st.caption("같은 질문으로 만든 보고서 두 편을 나란히 읽습니다. 지표는 명백한 실패를 거르는 데만 쓰고, 어느 쪽이 나은지는 직접 읽고 판단하세요.")
all_runs = list(reversed(runs()))
questions = sorted({r["question"] for r in all_runs})
if not questions:
    st.info("저장된 실행이 없어요.", icon=":material/info:")
    st.stop()
q = st.selectbox("질문", questions, key="cmp_q")
pool = [r for r in all_runs if r["question"] == q]
if len(pool) < 2:
    st.warning("이 질문으로 저장된 실행이 2개 이상 있어야 비교할 수 있어요.")
    st.stop()

left, right = st.columns(2)
with left:
    a = st.selectbox("왼쪽", range(len(pool)), format_func=lambda i: run_title(pool[i]), key="cmp_a")
with right:
    default_b = next((i for i, r in enumerate(pool) if r["kind"] != pool[a]["kind"]), 1 if a == 0 else 0)
    b = st.selectbox("오른쪽", range(len(pool)), index=default_b, format_func=lambda i: run_title(pool[i]), key="cmp_b")

keys = ["경보합", "근거율", "중복률", "인용편중", "읽기횟수", "예산소진율", "보고서자수", "인용수", "호출", "리서처최대창"]
val = lambda r, k: len(r["metrics"][k]) if isinstance(r["metrics"].get(k), list) else r["metrics"].get(k)
st.dataframe(pd.DataFrame({"지표": keys, "왼쪽": [val(pool[a], k) for k in keys], "오른쪽": [val(pool[b], k) for k in keys]}),
             hide_index=True, alt="두 실행의 지표 비교 표")

c1, c2 = st.columns(2)
with c1:
    render_run(pool[a], key="L")
with c2:
    render_run(pool[b], key="R")
