"""OpenAlex 에서 학습법 논문(초록)을 받아 data/corpus.json 에 더한다. 한 번 받으면 data/papers_raw.json 에 저장해 두고 다시 묻지 않는다.

- 문서 하나 = 논문 하나: 제목 · 저자 · 연도 · 학술지 · 피인용 수 · 초록
- 링크 ① 논문끼리 인용 관계(인용함 ↔ 인용됨)  ② 논문 ↔ 위키 문서: 같은 개념 이름이 논문 제목·초록·키워드에 나오면 잇는다
- 다시 돌리면 이전에 더한 논문을 지우고 새로 더한다 (몇 번을 돌려도 같은 결과)

    python tools/add_papers.py            # 캐시가 있으면 캐시로, 없는 제목만 OpenAlex 에 묻는다
    python tools/add_papers.py --refresh  # 캐시를 무시하고 다시 받는다
"""
import difflib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
CORPUS = DATA / "corpus.json"
CACHE = DATA / "papers_raw.json"
UA = {"User-Agent": "learning-deep-researcher/0.1 (course project; python-urllib)"}
FIELDS = "id,doi,title,publication_year,authorships,primary_location,cited_by_count,abstract_inverted_index,referenced_works,keywords,type"
# 너무 넓어서 아무 논문에나 걸리는 개념 이름 — 링크로 잇지 않는다
GENERIC = {"learning", "memory", "education", "evaluation", "feedback", "debate", "teaching", "학습", "기억", "평가", "피드백", "토론"}


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9가-힣 ]", " ", s.lower()).split())


def search(seed: str) -> dict | None:
    """seed = '제목' 또는 '제목 | 검색을 돕는 말(저자 등)'. 맞는지는 제목만으로 따진다."""
    if seed.lower().startswith("doi:"):                     # DOI 를 알면 검색하지 않고 그 논문을 바로 받는다
        url = f"https://api.openalex.org/works/{seed.split()[0]}?select={FIELDS}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            return None
    title, _, extra = (x.strip() for x in seed.partition("|"))
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"search": f"{title} {extra}".strip(), "per_page": 10, "select": FIELDS})
    for attempt in range(5):
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                results = json.load(r)["results"]
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(3 * 2 ** attempt)
    else:
        return None
    # 제목이 가장 닮은 것 중 초록이 있는 것을 고른다 (같은 논문이 여러 판으로 올라 있는 경우가 많다)
    scored = sorted(((difflib.SequenceMatcher(None, norm(title), norm(w.get("title") or "")).ratio(), w) for w in results),
                    key=lambda x: (-x[0], -(x[1].get("cited_by_count") or 0)))
    for score, w in scored:
        if score >= 0.8 and w.get("abstract_inverted_index"):
            return w
    return scored[0][1] | {"_no_match": round(scored[0][0], 2)} if scored else None


def abstract(inv: dict) -> str:
    words = sorted((pos, word) for word, positions in inv.items() for pos in positions)
    return " ".join(word for _, word in words)


def doc_title(w: dict) -> str:
    authors = w.get("authorships") or []
    # 성 = 이름의 마지막 낱말. 단 'Henry L. Roediger III' 처럼 붙는 접미사(III·Jr.)는 건너뛴다
    words = [w for w in (authors[0]["author"]["display_name"].split() if authors else ["Anon"])
             if w.rstrip(".,").lower() not in {"jr", "sr", "ii", "iii", "iv"}]
    first = words[-1] if words else "Anon"
    etal = " 외" if len(authors) > 1 else ""
    title = re.sub(r"\s+", " ", (w.get("title") or "").replace("«", "").replace("»", "")).strip()
    # 모델이 인용할 때 곡선 따옴표·특수 하이픈을 평범한 글자로 옮겨 써서 '코퍼스 밖 인용'이 되는 일을 막는다
    title = title.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "‐": "-", "‑": "-", "–": "-", "—": "-"})).rstrip(".")
    return f"{first}{etal} ({w.get('publication_year')}) {title}"


def doc_text(w: dict) -> str:
    authors = [a["author"]["display_name"] for a in (w.get("authorships") or [])]
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "알 수 없음"
    keywords = ", ".join(k["display_name"] for k in (w.get("keywords") or [])[:8])
    return (f"{w.get('title')}\n"
            f"논문 · {w.get('publication_year')}년 · {venue} · 피인용 {w.get('cited_by_count', 0):,}회\n"
            f"저자: {', '.join(authors[:6])}{' 외' if len(authors) > 6 else ''}\n"
            f"키워드: {keywords}\n"
            f"DOI: {w.get('doi') or '-'}\n\n"
            f"[초록]\n{abstract(w['abstract_inverted_index'])}")


def concept_names(title: str, meta: dict) -> list[str]:
    names = [title, meta.get("other_title") or ""]
    out = []
    for n in names:
        n = re.sub(r"\s*\(.*?\)", "", n).strip()            # "Rubric (academic)" → "Rubric"
        if len(n) >= 5 and n.lower() not in GENERIC:
            out.append(n.lower())
    return out


def main() -> None:
    refresh = "--refresh" in sys.argv
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    meta = corpus.setdefault("meta", {})
    for t in [t for t, m in meta.items() if m.get("type") == "paper"]:      # 이전에 더한 논문은 지운다
        corpus["docs"].pop(t, None); corpus["links"].pop(t, None); meta.pop(t)
    for t in corpus["links"]:
        corpus["links"][t] = [x for x in corpus["links"][t] if x in corpus["docs"]]

    cache = {} if refresh or not CACHE.exists() else json.loads(CACHE.read_text(encoding="utf-8"))
    seeds = [l.strip() for l in (HERE / "seeds_papers.txt").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    papers = []
    for q in seeds:
        if q not in cache:
            cache[q] = search(q)
        w = cache[q]
        if not w or w.get("_no_match") is not None:
            print(f"✗ 못 찾음  {q[:70]}  (가장 닮은 것 {w and w.get('_no_match')})")
            continue
        if not w.get("abstract_inverted_index"):
            print(f"✗ 초록 없음 {q[:70]}")
            continue
        papers.append(w)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    id2title = {}
    for w in papers:
        t = doc_title(w)
        if t in corpus["docs"]:
            continue
        corpus["docs"][t] = doc_text(w)
        corpus["links"][t] = []
        meta[t] = {"lang": "en", "type": "paper", "url": w.get("doi") or w["id"], "openalex": w["id"],
                   "year": w.get("publication_year"), "cited_by": w.get("cited_by_count")}
        id2title[w["id"]] = t
        print(f"✓ {t[:90]}  ({len(corpus['docs'][t]):,}자)")

    def link(a: str, b: str) -> None:
        if a != b and b not in corpus["links"].setdefault(a, []):
            corpus["links"][a].append(b)

    n_cite = n_concept = 0
    for w in papers:
        a = id2title.get(w["id"])
        for ref in w.get("referenced_works") or []:
            if a and ref in id2title:
                link(a, id2title[ref]); link(id2title[ref], a); n_cite += 1
    wiki = [(t, concept_names(t, m)) for t, m in meta.items() if m.get("type") != "paper"]
    for w in papers:
        a = id2title.get(w["id"])
        if not a:
            continue
        hay = " " + norm(" ".join([w.get("title") or "", abstract(w["abstract_inverted_index"]),
                                   " ".join(k["display_name"] for k in (w.get("keywords") or []))])) + " "
        for t, names in wiki:
            if any(f" {norm(n)} " in hay for n in names):
                link(a, t); link(t, a); n_concept += 1
    for t in corpus["links"]:
        corpus["links"][t] = sorted(corpus["links"][t])

    corpus["source"] = ("ko.wikipedia.org / en.wikipedia.org (CC BY-SA 4.0) + OpenAlex 논문 메타데이터·초록, "
                        "수집: tools/collect_corpus.py → tools/add_papers.py")
    CORPUS.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    chars = sum(len(x) for x in corpus["docs"].values())
    nlinks = sum(len(v) for v in corpus["links"].values())
    print(f"\n논문 {len(id2title)}편 더함 · 인용 링크 {n_cite}쌍 · 개념 링크 {n_concept}쌍")
    print(f"코퍼스: 문서 {len(corpus['docs'])}건 · {chars:,}자 · 링크 {nlinks}개")
    print("나가는 링크 0개:", [t for t, v in corpus["links"].items() if not v])


if __name__ == "__main__":
    main()
