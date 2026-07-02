"""자율 트렌드 블로그 팀 v6 — 채널별 내부링크(상대/절대) + 멀티GEO + SEO + 고품질 2패스"""
import html
import json
import os
import re
import requests
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

from channels import inject_monetize, publish_blogger, publish_naver, publish_devto

KST = timezone(timedelta(hours=9))
GROQ_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "3"))
GEOS = [g.strip() for g in os.environ.get("GEO", "KR,US").split(",") if g.strip()]
SITE_URL = "https://wijihoon.github.io"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Referer": "https://trends.google.com/",
}


def notify(msg):
    print(msg, flush=True)
    if not WEBHOOK:
        return
    try:
        requests.post(WEBHOOK, json={"content": msg[:1900]}, timeout=10)
    except Exception as e:
        print("알림 실패:", e, flush=True)


# ───────── Collector: 멀티 GEO 수집 (중복 제거) ─────────
def collect_trends():
    out, seen = [], set()
    for geo in GEOS:
        try:
            r = requests.get(f"https://trends.google.com/trending/rss?geo={geo}",
                             headers=HEADERS, timeout=20)
            r.raise_for_status()
            for it in re.findall(r"<item>(.*?)</item>", r.text, re.S):
                t = re.search(r"<title>(.*?)</title>", it, re.S)
                d = re.search(r"<ht:news_item_snippet>(.*?)</ht:news_item_snippet>", it, re.S)
                if not t:
                    continue
                topic = html.unescape(t.group(1).strip())
                if topic.lower() in seen:
                    continue
                seen.add(topic.lower())
                out.append({"topic": topic, "geo": geo,
                            "snippet": html.unescape((d.group(1) if d else "").strip())[:300]})
        except Exception as e:
            print(f"수집 실패({geo}):", type(e).__name__, flush=True)
    return out[:30]


# ───────── LLM 캐스케이드 (모델별 무료 쿼터 분산) ─────────
def _groq(model, prompt, max_tokens):
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          json={"model": model,
                                "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": max_tokens, "temperature": 0.7},
                          headers={"Authorization": f"Bearer {GROQ_KEY}"}, timeout=90)
        if r.status_code != 200:
            print(f"Groq({model}) {r.status_code}", flush=True)
            return None
        return r.json()["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        print(f"Groq({model}) 예외:", type(e).__name__, flush=True)
        return None


def _gemini(model, prompt, max_tokens):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7}},
            timeout=90)
        if r.status_code != 200:
            print(f"Gemini({model}) {r.status_code}", flush=True)
            return None
        cands = r.json().get("candidates") or []
        parts = (cands[0].get("content", {}).get("parts") or []) if cands else []
        return (parts[0].get("text", "").strip() or None) if parts else None
    except Exception as e:
        print(f"Gemini({model}) 예외:", type(e).__name__, flush=True)
        return None


CHAIN = [
    lambda p, m: _groq("llama-3.3-70b-versatile", p, m),   # 1순위: 품질 최고
    lambda p, m: _groq("llama-3.1-8b-instant", p, m),      # 2순위: 별도 쿼터
    lambda p, m: _gemini("gemini-2.0-flash", p, m),        # 3순위
    lambda p, m: _gemini("gemini-2.0-flash-lite", p, m),   # 4순위: 별도 쿼터
]


def llm(prompt, max_tokens=2500):
    for attempt in range(3):
        for call in CHAIN:
            out = call(prompt, max_tokens)
            if out:
                return out
        wait = 30 * (attempt + 1)
        print(f"⏳ 전 모델 한도 — {wait}초 대기 ({attempt + 1}/3)", flush=True)
        time.sleep(wait)
    raise Exception("모든 LLM 한도 — 다음 스케줄에서 재시도")


CATEGORIES = ["IT·테크", "연예·문화", "스포츠", "경제·비즈니스", "사회·이슈", "라이프", "글로벌"]


def categorize(trends):
    listing = "\n".join(f"{i + 1}. [{t['geo']}] {t['topic']} — {t['snippet'][:70]}"
                        for i, t in enumerate(trends))
    out = llm(f"토픽을 분류하라. 카테고리: {', '.join(CATEGORIES)}\n{listing}\n\n"
              'JSON만: [{"idx":1,"category":"IT·테크"}]', 900)
    m = re.search(r"\[.*\]", out, re.S)
    try:
        mapping = json.loads(m.group(0)) if m else []
    except Exception:
        mapping = []
    for row in mapping:
        i = row.get("idx", 0) - 1
        if 0 <= i < len(trends):
            trends[i]["category"] = row.get("category", "사회·이슈")
    for t in trends:
        t.setdefault("category", "글로벌" if t["geo"] != "KR" else "사회·이슈")
    return trends


def slugify(s):
    return re.sub(r"[^\w가-힣]+", "-", s).strip("-")[:40] or "post"


def already_posted(topic):
    """과거에 같은 토픽으로 쓴 글이 있으면 스킵 (중복 콘텐츠 SEO 감점 방지)."""
    slug = slugify(topic)
    if not os.path.isdir("_posts"):
        return False
    return any(slug in f for f in os.listdir("_posts"))


def clean_line(prefix, line, fallback=""):
    v = re.sub(rf"^{prefix}\s*[:：]\s*", "", line.strip())
    v = re.sub(r"[#*`=_\n\r]+", " ", v)
    return re.sub(r"\s{2,}", " ", v).strip().strip('"') or fallback


# ───────── Writer: 고품질 2패스 + SEO 메타(제목·설명·태그) 동시 생성 ─────────
def write_post(t):
    draft = llm(
        f"토픽: {t['topic']}\n배경: {t['snippet']}\n카테고리: {t['category']}\n\n"
        "한국 독자용 고품질 블로그 글 초안을 마크다운으로 작성하라.\n"
        "- 분량 1200~1800자\n"
        "- 구성: 독자의 궁금증을 짚는 도입 2~3문장 → '**3줄 요약**' 리스트 →\n"
        "  ## 소제목 3~4개(각각 구체적 정보·수치·맥락) → ## 자주 묻는 질문(Q&A 3개) → 전망 마무리\n"
        "- 사실 확인이 안 된 내용은 단정하지 말 것('~로 알려졌다/보인다')\n"
        "- 같은 문장 반복 금지, 광고체 금지", 2500)
    time.sleep(15)
    final = llm(
        "너는 10년차 시니어 에디터다. 아래 초안을 퇴고하라:\n"
        "- 문장을 자연스럽고 간결하게, 정보 밀도를 높이고 중복 제거\n"
        "- 소제목이 검색 키워드를 포함하도록 다듬기\n"
        "- 출력 형식(이 형식 외 다른 말 금지):\n"
        "TITLE: 검색 키워드가 앞에 오는 매력적인 제목(25~35자, 마크다운 금지)\n"
        "DESC: 검색결과에 보일 요약 설명(70~110자, 1문장)\n"
        "TAGS: 관련 키워드 3~5개(쉼표 구분)\n"
        "---\n"
        "(퇴고된 본문 전체, 마크다운 유지)\n\n" + draft, 2500)
    time.sleep(15)

    head, _, body = final.partition("---")
    title = desc = tags = ""
    for ln in head.splitlines():
        s = ln.strip()
        if s.upper().startswith("TITLE"):
            title = clean_line("TITLE", s)
        elif s.upper().startswith("DESC"):
            desc = clean_line("DESC", s)
        elif s.upper().startswith("TAGS"):
            tags = clean_line("TAGS", s)
    body = body.strip() or final
    if not (5 <= len(title) <= 60):
        title = t["topic"][:40]
    if not desc:
        desc = re.sub(r"[#*`\n]+", " ", body)[:100].strip()
    tags = ", ".join(x.strip() for x in tags.split(",") if x.strip())[:80] or t["category"]
    return title, desc, tags, body


def qa_ok(body):
    return (len(body) >= 800 and body.count("##") >= 3
            and not any(b in body for b in ["죄송", "도와드릴 수 없", "AI 언어 모델", "TITLE:"]))


# ───────── 내부/외부 링크: 같은 카테고리 최신 글 3개 ─────────
# absolute=False → GitHub 글용 상대경로(/슬러그/)
# absolute=True  → 외부 채널(Blogger 등)용 전체 URL — 메인 블로그로 트래픽 순환
def related_links(category, current_slug, absolute=False):
    if not os.path.isdir("_posts"):
        return ""
    base = SITE_URL if absolute else ""
    links = []
    for f in sorted(os.listdir("_posts"), reverse=True):
        if current_slug in f or not f.endswith(".md"):
            continue
        try:
            head = open(os.path.join("_posts", f), encoding="utf-8").read(400)
        except Exception:
            continue
        if f"categories: [{category}]" not in head:
            continue
        m = re.search(r'title:\s*"(.*?)"', head)
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", f)[:-3]
        if m:
            links.append(f"- [{m.group(1)}]({base}/{slug}/)")
        if len(links) >= 3:
            break
    if not links:
        return ""
    return "\n\n---\n\n### 📚 함께 읽으면 좋은 글\n" + "\n".join(links)


def publish_github(title, desc, tags, body_md, t, now):
    slug = slugify(t["topic"])
    fname = f"_posts/{now:%Y-%m-%d}-{slug}.md"
    front = ("---\n"
             f'layout: post\ntitle: "{title.replace(chr(34), "")}"\n'
             f'description: "{desc.replace(chr(34), "")}"\n'
             f'categories: [{t["category"]}]\ntags: [{tags}]\n'
             f"date: {now:%Y-%m-%d %H:%M:%S} +0900\n---\n\n")
    os.makedirs("_posts", exist_ok=True)
    with open(fname, "w", encoding="utf-8") as f:
        f.write(front + body_md + related_links(t["category"], slug) + "\n")
    return fname


def main():
    now = datetime.now(KST)
    notify(f"🚀 **InsightDaily 봇 시작** — {now:%Y-%m-%d %H:%M} KST · GEO={','.join(GEOS)}")
    try:
        trends = collect_trends()
        if not trends:
            notify("⚠️ 수집 실패")
            return
        trends = categorize(trends)

        seen, picked = set(), []
        for t in trends:
            if already_posted(t["topic"]):
                continue
            if t["category"] not in seen:
                picked.append(t)
                seen.add(t["category"])
            if len(picked) >= MAX_POSTS:
                break

        report = []
        for i, t in enumerate(picked):
            if i:
                time.sleep(20)                    # 글 사이 간격 → 분당 한도 분산
            try:
                title, desc, tags, body = write_post(t)
                if not qa_ok(body):
                    title, desc, tags, body = write_post(t)
                if not qa_ok(body):
                    notify(f"⚠️ 품질 미달 제외: {t['topic']}")
                    continue

                body_m = inject_monetize(body, t["category"], t["topic"])
                slug = slugify(t["topic"])

                # ① GitHub: 상대경로 내부링크 (publish_github 내부에서 추가)
                chans = ["GitHub"]
                publish_github(title, desc, tags, body_m, t, now)

                # ② 외부 채널: 절대주소 링크 → 메인 블로그로 트래픽 순환 + 백링크
                body_ext = body_m + related_links(t["category"], slug, absolute=True)
                for name, fn in (("Blogger", publish_blogger),
                                 ("Naver", publish_naver),
                                 ("dev.to", publish_devto)):
                    try:
                        if fn(title, body_ext, t["category"]):
                            chans.append(name)
                    except Exception as e:
                        print(f"{name} 실패: {type(e).__name__}", flush=True)
                report.append(f"· [{t['category']}] {title} → {', '.join(chans)}")
            except Exception as e:
                notify(f"⚠️ '{t['topic']}' 실패: {type(e).__name__}")

        notify((f"✅ **게시 완료 {len(report)}건**\n" + "\n".join(report))
               if report else "⚠️ 게시된 글 없음")
    except Exception:
        notify("❌ **봇 실패**\n```" + traceback.format_exc()[-800:] + "```")
        sys.exit(1)
    notify(f"🏁 종료 — {datetime.now(KST):%H:%M} KST")


if __name__ == "__main__":
    main()