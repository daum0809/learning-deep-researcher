import json

import pandas as pd
import streamlit as st

from graph import OUT, question_by_id

path = OUT / "ablation.json"
if not path.exists():
    st.info("아직 실험 결과가 없어요. 터미널에서 `python ablation.py` 를 돌리세요.", icon=":material/info:")
    st.stop()
data = json.loads(path.read_text(encoding="utf-8"))
rows = pd.DataFrame(data["rows"])
st.caption(f"모델 {data['model']} · 실행 {len(rows)}개. 같은 설정도 시행마다 출렁이니 평균만 보지 말고 범위(최소~최대)를 함께 보세요. "
           "'경로 확인'은 스위치가 실제로 코드 경로를 바꿨는지(예: 구역 끔 → 보낸 피하기 0건) 실행 기록으로 확인한 결과입니다.")

bad = rows[~rows["경로확인"]]
if len(bad):
    st.error(f"경로 확인 실패 {len(bad)}건 — 스위치가 코드 경로를 바꾸지 못한 실행이 있어요.", icon=":material/warning:")
    st.dataframe(bad[["qid", "trial", "config", "경로근거"]], hide_index=True, alt="경로 확인 실패 목록")

metric = st.segmented_control("지표", ["근거율", "중복률", "인용편중", "읽고안쓴", "경보합", "보고서자수", "예산소진율"],
                              default="근거율", key="abl_metric")
order = ["기본", "배정 끔", "구역 끔", "역할 끔", "재위임 끔", "넓히기 끔", "단일"]
for qid in sorted(rows["qid"].unique()):
    sub = rows[rows["qid"] == qid]
    with st.container(border=True):
        st.markdown(f"**{qid}** — {question_by_id(qid)}")
        agg = (sub.groupby("config")[metric].agg(["mean", "min", "max", "count"]).reindex([c for c in order if c in set(sub["config"])])
               .rename(columns={"mean": "평균", "min": "최소", "max": "최대", "count": "시행"}).round(1).reset_index())
        c1, c2 = st.columns([3, 2])
        with c1:
            st.scatter_chart(sub, x="config", y=metric, color="trial", height=260, alt=f"{qid} 설정별 {metric} 시행별 점")
        with c2:
            st.dataframe(agg, hide_index=True, alt=f"{qid} 설정별 {metric} 평균과 범위")

with st.expander("모든 실행"):
    st.dataframe(rows.drop(columns=["path"]), hide_index=True, alt="실험 실행 전체 표")
