"""위키백과에서 학습법 코퍼스를 모아 data/corpus.json 을 만든다.

- 한국어 문서(seeds_ko.txt)와, 한국어판에 없는 핵심 개념을 채우는 영어 문서(seeds_en.txt)를 모은다.
- 링크는 '코퍼스 안의 문서끼리'만 남긴다. 언어가 달라도 위키백과의 언어 간 링크(langlinks)로
  같은 개념이면 이어 준다. 예: «Andragogy» 안의 Lifelong learning 링크 → «평생교육».
- 본문 끝의 '각주·참고 문헌·외부 링크' 같은 꼬리는 잘라 낸다(근거로 쓸 수 없는 목록이라서).

    python tools/collect_corpus.py
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data" / "corpus.json"
UA = {"User-Agent": "learning-deep-researcher/0.1 (course project; python-urllib)"}
TAIL = {
    "ko": ["같이 보기", "각주", "참고 문헌", "참고문헌", "외부 링크", "내용주", "출처", "더 읽을거리", "참조"],
    "en": ["See also", "References", "Notes", "External links", "Further reading", "Bibliography",
           "Sources", "Citations", "Footnotes"],
}


def api(lang: str, **params) -> dict:
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "format": "json", "formatversion": 2, **params})
    for attempt in range(6):
        time.sleep(1.0)                                   # 위키백과 API 예절: 요청 사이를 띄운다
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            wait = int(e.headers.get("retry-after") or 0) if e.code == 429 else 0
            time.sleep(max(wait, 5 * 2 ** attempt))
        except Exception:
            time.sleep(5 * 2 ** attempt)
    raise RuntimeError(f"위키백과 API 실패: {url[:120]}")


def clean(text: str, lang: str) -> str:
    """꼬리 절을 자르고 빈 머리글('== X ==' 아래 내용 없음)과 빈 줄을 정리한다."""
    for head in TAIL[lang]:
        m = re.search(rf"\n==+\s*{re.escape(head)}\s*==+", text)
        if m:
            text = text[: m.start()]
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_page(lang: str, title: str) -> dict:
    d = api(lang, prop="extracts|langlinks|info", explaintext=1, redirects=1, titles=title,
            lllang="en" if lang == "ko" else "ko", lllimit="max", inprop="url")
    page = d["query"]["pages"][0]
    if page.get("missing"):
        return {}
    other = (page.get("langlinks") or [{}])[0].get("title")
    return {"title": page["title"], "lang": lang, "url": page["fullurl"],
            "text": clean(page.get("extract", ""), lang), "other_title": other}


def fetch_links(lang: str, title: str) -> list[tuple[str, str | None]]:
    """문서가 가리키는 (대상 제목, 대상의 다른 언어 제목) 목록. 넘겨주기(redirect)는 풀어서 받는다."""
    out, cont = [], {}
    other_lang = "en" if lang == "ko" else "ko"
    while True:
        d = api(lang, generator="links", titles=title, gplnamespace=0, gpllimit="max", redirects=1,
                prop="langlinks", lllang=other_lang, lllimit="max", **cont)
        for p in d.get("query", {}).get("pages", []):
            ll = (p.get("langlinks") or [{}])[0].get("title")
            out.append((p["title"], ll))
        if "continue" not in d:
            break
        cont = d["continue"]
    return out


def main() -> None:
    seeds = [("ko", t) for t in (HERE / "seeds_ko.txt").read_text(encoding="utf-8").split("\n") if t.strip()]
    seeds += [("en", t) for t in (HERE / "seeds_en.txt").read_text(encoding="utf-8").split("\n") if t.strip()]
    pages = []
    for lang, title in seeds:
        page = fetch_page(lang, title.strip())
        if not page:
            print(f"없는 문서라 건너뜀: {lang}:{title}")
            continue
        # 한국어판이 있는 영어 문서는 중복이다 — 같은 개념이 두 번 들어가지 않게 막는다
        if lang == "en" and page["other_title"] and any(p["title"] == page["other_title"] for p in pages):
            print(f"건너뜀(한국어판 있음): {page['title']} = {page['other_title']}")
            continue
        page["links_raw"] = fetch_links(lang, page["title"])
        pages.append(page)
        print(f"{lang}  {page['title']:<30} {len(page['text']):>7,}자  링크후보 {len(page['links_raw'])}")

    # 같은 개념이면 어느 언어 제목으로 가리켜도 코퍼스 문서로 이어지게 한다
    alias = {}
    for p in pages:
        alias[(p["lang"], p["title"])] = p["title"]
        if p["other_title"]:
            alias[("en" if p["lang"] == "ko" else "ko", p["other_title"])] = p["title"]
    links = {}
    for p in pages:
        other_lang = "en" if p["lang"] == "ko" else "ko"
        targets = set()
        for t, ll in p["links_raw"]:
            hit = alias.get((p["lang"], t)) or (alias.get((other_lang, ll)) if ll else None)
            if hit and hit != p["title"]:
                targets.add(hit)
        links[p["title"]] = sorted(targets)

    corpus = {
        "docs": {p["title"]: p["text"] for p in pages},
        "links": links,
        "meta": {p["title"]: {"lang": p["lang"], "url": p["url"], "other_title": p["other_title"]} for p in pages},
        "source": "ko.wikipedia.org / en.wikipedia.org (CC BY-SA 4.0), 수집: tools/collect_corpus.py",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    chars = sum(len(t) for t in corpus["docs"].values())
    nlinks = sum(len(v) for v in links.values())
    inbound = {t for v in links.values() for t in v}
    print(f"\n문서 {len(pages)}건 · {chars:,}자 · 링크 {nlinks}개")
    print("나가는 링크 0개:", [t for t, v in links.items() if not v])
    print("들어오는 링크 0개:", [t for t in links if t not in inbound])


if __name__ == "__main__":
    main()
