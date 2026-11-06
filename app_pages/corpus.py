import json

import pandas as pd
import streamlit as st

from graph import CFG, DOCS, LINKS, META, QUESTIONS, ROOT

tc = json.loads((ROOT / "data" / "token_count.json").read_text(encoding="utf-8"))
with st.container(horizontal=True):
    st.metric("문서", f"{tc['docs']}건", border=True)
    st.metric("글자", f"{tc['total_chars']:,}", border=True)
    st.metric("토큰 (실측)", f"{tc['total_tokens']:,}", border=True, help=f"{tc['model']} 토크나이저로 count_tokens API 로 셌어요")
    st.metric("창 대비", f"{tc['ratio_to_window']}배", border=True, help=f"{tc['model']} 창 {tc['window_tokens']:,}토큰")
st.caption("자료 전체가 모델 창 하나에 들어가지 않아서 나눠 읽을 이유가 있습니다. 코디네이터는 원문 대신 문서마다 제목 + 앞 "
           f"{CFG['카드글자']}자 카드만 봅니다.")

kinds = pd.DataFrame([{"묶음": k, "문서": v["docs"], "토큰": v["tokens"]} for k, v in tc["by_kind"].items()])
st.bar_chart(kinds, x="묶음", y="토큰", horizontal=True, height=180, alt="자료 묶음별 토큰 수")

inbound = {}
for a, ts in LINKS.items():
    for b in ts:
        inbound[b] = inbound.get(b, 0) + 1
table = pd.DataFrame([{"문서": t, "종류": "논문" if META.get(t, {}).get("type") == "paper" else f"위키({META.get(t, {}).get('lang', 'ko')})",
                       "글자": len(x), "나가는 링크": len(LINKS.get(t, [])), "들어오는 링크": inbound.get(t, 0),
                       "출처": META.get(t, {}).get("url")} for t, x in DOCS.items()])
st.markdown("**문서 목록** — 링크가 0인 문서는 탐색으로는 닿지 못하고 코디네이터의 배정이 유일한 길입니다")
st.dataframe(table, hide_index=True, alt="문서 목록과 링크 수",
             column_config={"출처": st.column_config.LinkColumn("출처"), "글자": st.column_config.ProgressColumn("글자", max_value=int(table["글자"].max()), format="%d")})

st.markdown("**질문 세트**")
st.dataframe(pd.DataFrame(QUESTIONS), hide_index=True, alt="질문 세트와 나눌 만한 이유")
