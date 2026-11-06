"""교육법 딥리서처 데모 — streamlit run app.py"""
import streamlit as st

st.set_page_config(page_title="교육법 딥리서처", page_icon=":material/school:", layout="wide")

page = st.navigation(
    [
        st.Page("app_pages/research.py", title="조사하기", icon=":material/travel_explore:", default=True,
                help="질문을 넣고 코디네이터와 리서처 팀이 자료를 나눠 읽어 보고서를 쓰는 과정을 실시간으로 봅니다."),
        st.Page("app_pages/runs.py", title="기록 보기", icon=":material/history:",
                help="저장된 실행 하나를 열어 보고서, 절마다 누가 무엇을 읽고 무엇을 썼는지, 인용 대조, 지표를 봅니다."),
        st.Page("app_pages/compare.py", title="나란히 비교", icon=":material/compare:",
                help="같은 질문으로 만든 보고서 두 편(기본·장치 끔·단일 대조군)을 나란히 놓고 읽습니다."),
        st.Page("app_pages/experiments.py", title="실험 결과", icon=":material/science:",
                help="스위치를 하나씩 끈 실험(ablation)과 단일 에이전트 대조군의 지표를 시행 평균과 범위로 봅니다."),
        st.Page("app_pages/corpus.py", title="자료", icon=":material/library_books:",
                help="위키백과·논문 119건, 토큰 수가 창을 넘는지, 문서 사이 링크, 질문 세트를 봅니다."),
    ],
    position="top",
)
st.title(page.title, icon=page.icon)
page.run()
