"""코퍼스 전체가 모델 창에 들어가는지 '실제 토큰 수'로 센다 (글자 수 어림이 아니라). 토큰 세기 API 는 무료다.

    python tools/count_tokens.py                       # 기본: claude-haiku-4-5, 창 200,000
결과: data/token_count.json
"""
import json
import sys
from pathlib import Path

import anthropic
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-4-5"
WINDOW = 200_000

key = dotenv_values(ROOT / ".env").get("ANTHROPIC_API_KEY")
client = anthropic.Anthropic(api_key=key)
corpus = json.loads((ROOT / "data" / "corpus.json").read_text(encoding="utf-8"))
meta = corpus.get("meta", {})


def tokens(text: str) -> int:
    return client.messages.count_tokens(model=MODEL, messages=[{"role": "user", "content": text}]).input_tokens


base = tokens("x")                       # 메시지 틀이 차지하는 몫은 뺀다
rows = []
for title, text in corpus["docs"].items():
    m = meta.get(title, {})
    rows.append({"title": title, "kind": m.get("type", "wiki"), "lang": m.get("lang", "ko"),
                 "chars": len(text), "tokens": tokens(text) - base})
total_c = sum(r["chars"] for r in rows)
total_t = sum(r["tokens"] for r in rows)
out = {"model": MODEL, "window_tokens": WINDOW, "docs": len(rows), "total_chars": total_c, "total_tokens": total_t,
       "ratio_to_window": round(total_t / WINDOW, 2),
       "by_kind": {k: {"docs": sum(1 for r in rows if (r["kind"], r["lang"]) == k_),
                       "chars": sum(r["chars"] for r in rows if (r["kind"], r["lang"]) == k_),
                       "tokens": sum(r["tokens"] for r in rows if (r["kind"], r["lang"]) == k_)}
                   for k, k_ in {"위키(한국어)": ("wiki", "ko"), "위키(영어)": ("wiki", "en"), "논문(영어)": ("paper", "en")}.items()},
       "rows": sorted(rows, key=lambda r: -r["tokens"])}
(ROOT / "data" / "token_count.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"문서 {len(rows)}건 · {total_c:,}자 · {total_t:,}토큰 → 창({WINDOW:,})의 {total_t / WINDOW:.2f}배")
for k, v in out["by_kind"].items():
    print(f"  {k}: {v['docs']}건 · {v['chars']:,}자 · {v['tokens']:,}토큰")
