import importlib.util

import streamlit as st

from graph import OUT, ROOT

RUNS = OUT / "runs.jsonl"
INDEX = ROOT / "static" / "sim" / "data" / "runs.json"


@st.cache_resource
def _exporter():
    spec = importlib.util.spec_from_file_location("export_sim", ROOT / "tools" / "export_sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 실행 기록이 새로 생겼으면 시뮬레이터 데이터를 다시 만든다 (모델 호출 없음, 1초 안쪽)
if RUNS.exists() and (not INDEX.exists() or RUNS.stat().st_mtime > INDEX.stat().st_mtime):
    with st.spinner("저장된 실행을 여행 기록으로 바꾸는 중…"):
        _exporter().main()

st.caption("저장된 실행을 3D 세계여행으로 다시 그립니다. 🧭 본사(등대) = 코디네이터 · 🎒 여행자 = 리서처 · 🏙️ 도시 = 위키 문서 · 📄 돔 건물 = 논문 · "
           "✈️ 항공 노선 = 문서 사이 링크 · ✉️ 엽서 = 원고(원문은 현지에 남음). 아래 디버거로 한 단계씩 넘겨 보세요 "
           "(← → 한 단계 · Space 재생 · F5 다음 중단점). 모델을 부르지 않습니다.")
st.iframe("/app/static/sim/index.html", height=880, alt="교육법 딥리서처 3D 여행 시뮬레이터")
st.caption("화면이 좁으면 [새 창에서 크게 보기](/app/static/sim/index.html) · 특정 장면 바로 열기: `/app/static/sim/index.html?run=<실행 id>&at=<단계|end>&tab=event|vars|report|score|log`")
