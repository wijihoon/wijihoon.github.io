"""자율 트렌드 블로그 팀 v7 — 이미지(스톡)·영어 채널·글로벌 수익화 추가"""
import html
import json
import os
import re
import requests
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

from channels import (inject_monetize, inject_monetize_en, fetch_image,
                      publish_blogger, publish_blogger_en, publish_naver,
                      publish_devto, publish_hashnode, telegram_broadcast)

KST = timezone(timedelta(hours=9))
GROQ_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "3"))
GEOS = [g.strip() for g in os.environ.get("GEO", "KR,US").split(",") if g.strip()]
SITE_URL = "https://wijihoon.github.io"
EN_ENABLED = os.environ.get("EN_POSTS", "1") == "1"  # 영어 포스트 on/off

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
    lambda p, m: _groq("llama-3.3-70b-versatile", p, m),
    lambda p, m: _groq("llama-3.1-8b-instant", p, m),
    lambda p, m: _gemini("gemini-2.0-flash", p, m),
    lambda p, m: _gemini("gemini-2.0-flash-lite", p, m),
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
    slug = slugify(topic)
    if not os.path.isdir("_posts"):
        return False
    return any(slug in f for f in os.listdir("_posts"))


def clean_line(prefix, line, fallback=""):
    v = re.sub(rf"^{prefix}\s*[:：]\s*", "", line.strip())
    v = re.sub(r"[#*`=_\n\r]+", " ", v)
    return re.sub(r"\s{2,}", " ", v).strip().strip('"') or fallback


def parse_meta(final, topic):
    """TITLE/DESC/TAGS/IMGQ/---/본문 형식 파싱."""
    head, _, body = final.partition("---")
    title = desc = tags = imgq = ""
    for ln in head.splitlines():
        s = ln.strip()
        u = s.upper()
        if u.startswith("TITLE"):
            title = clean_line("TITLE", s)
        elif u.startswith("DESC"):
            desc = clean_line("DESC", s)
        elif u.startswith("TAGS"):
            tags = clean_line("TAGS", s)
        elif u.startswith("IMGQ"):
            imgq = clean_line("IMGQ", s)
    body = body.strip() or final
    if not (5 <= len(title) <= 70):
        title = topic[:40]
    if not desc:
        desc = re.sub(r"[#*`\n]+", " ", body)[:100].strip()
    tags = ", ".join(x.strip() for x in tags.split(",") if x.strip())[:80]
    return title, desc, tags, imgq, body


# ── Writer(한국어): 초안 → 퇴고+메타(+영문 이미지 키워드) ──
def write_post(t):
    draft = llm(
        f"토픽: {t['topic']}\n배경: {t['snippet']}\n카테고리: {t['category']}\n\n"
        "한국 독자용 고품질 블로그 글 초안을 마크다운으로 작성하라.\n"
        "- 분량 1200~1800자\n"
        "- 구성: 독자의 궁금증을 짚는 도입 2~3문장 → '**3줄 요약**' 리스트 →\n"
        "  ## 소제목 3~4개(구체적 정보·수치·맥락) → ## 자주 묻는 질문(Q&A 3개) → 전망 마무리\n"
        "- 확인 안 된 사실 단정 금지('~로 알려졌다/보인다')\n"
        "- 같은 문장 반복 금지, 광고체 금지", 2500)
    time.sleep(15)
    final = llm(
        "너는 10년차 시니어 에디터다. 아래 초안을 퇴고하라:\n"
        "- 문장을 자연스럽고 간결하게, 정보 밀도를 높이고 중복 제거\n"
        "- 소제목이 검색 키워드를 포함하도록 다듬기\n"
        "- 출력 형식(이 형식 외 다른 말 금지):\n"
        "TITLE: 검색 키워드가 앞에 오는 제목(25~35자, 마크다운 금지)\n"
        "DESC: 검색결과용 요약(70~110자, 1문장)\n"
        "TAGS: 관련 키워드 3~5개(쉼표 구분)\n"
        "IMGQ: 이 주제를 대표하는 사진 검색용 영어 키워드 2~3단어\n"
        "---\n"
        "(퇴고된 본문 전체, 마크다운 유지)\n\n" + draft, 2500)
    time.sleep(15)
    return parse_meta(final, t["topic"])


# ── Writer(영어): 한국 토픽을 글로벌 독자용으로 재작성 (호출 1회) ──
def write_post_en(t, body_kr):
    final = llm(
        "You are a senior editor for a global trends blog.\n"
        f"Topic (trending in Korea/world): {t['topic']}\n"
        "Rewrite the following Korean article as an original English blog post for "
        "international readers (not a literal translation — adapt context, add brief "
        "background a non-Korean reader needs).\n"
        "800-1200 words is NOT needed; 500-800 words. Use ## subheadings and a short FAQ.\n"
        "Do not state unverified facts as certain.\n"
        "Output format (nothing else):\n"
        "TITLE: SEO-friendly English title\n"
        "DESC: one-sentence meta description\n"
        "TAGS: 3-5 keywords, comma-separated\n"
        "IMGQ: 2-3 English words for a stock photo search\n"
        "---\n"
        "(full English article, markdown)\n\n" + body_kr[:4000], 2200)
    time.sleep(15)
    return parse_meta(final, t["topic"])


def qa_ok(body):
    return (len(body) >= 800 and body.count("##") >= 3
            and not any(b in body for b in ["죄송", "도와드릴 수 없", "AI 언어 모델", "TITLE:"]))


def qa_ok_en(body):
    return len(body) >= 500 and body.count("##") >= 2 and "TITLE:" not in body


def add_image(body_md, imgq, topic):
    """본문 도입부 뒤에 스톡 이미지+출처 삽입. (url 반환 — og:image용)"""
    url, credit = fetch_image(imgq or topic)
    if not url:
        return body_md, ""
    paras = body_md.split("\n\n")
    img_block = f"![{imgq or topic}]({url})\n{credit}"
    paras.insert(min(1, len(paras)), img_block)
    return "\n\n".join(paras), url


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


def publish_github(title, desc, tags, image_url, body_md, t, now):
    slug = slugify(t["topic"])
    fname = f"_posts/{now:%Y-%m-%d}-{slug}.md"
    img_line = f'image: "{image_url}"\n' if image_url else ""
    front = ("---\n"
             f'layout: post\ntitle: "{title.replace(chr(34), "")}"\n'
             f'description: "{desc.replace(chr(34), "")}"\n'
             f"{img_line}"
             f'categories: [{t["category"]}]\ntags: [{tags}]\n'
             f"date: {now:%Y-%m-%d %H:%M:%S} +0900\n---\n\n")
    os.makedirs("_posts", exist_ok=True)
    with open(fname, "w", encoding="utf-8") as f:
        f.write(front + body_md + related_links(t["category"], slug) + "\n")
    return f"{SITE_URL}/{slug}/"


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
                time.sleep(20)
            try:
                # ── 한국어 파이프라인 ──
                title, desc, tags, imgq, body = write_post(t)
                if not qa_ok(body):
                    title, desc, tags, imgq, body = write_post(t)
                if not qa_ok(body):
                    notify(f"⚠️ 품질 미달 제외: {t['topic']}")
                    continue

                body, image_url = add_image(body, imgq, t["topic"])
                body_m = inject_monetize(body, t["category"], t["topic"])
                slug = slugify(t["topic"])

                chans = ["GitHub"]
                gh_url = publish_github(title, desc, tags, image_url, body_m, t, now)

                body_ext = body_m + related_links(t["category"], slug, absolute=True)
                for name, fn in (("Blogger", publish_blogger), ("Naver", publish_naver)):
                    try:
                        if fn(title, body_ext, t["category"]):
                            chans.append(name)
                    except Exception as e:
                        print(f"{name} 실패: {type(e).__name__}", flush=True)

                # ── 영어 파이프라인 (글로벌 채널) ──
                if EN_ENABLED:
                    try:
                        etitle, edesc, etags, eimgq, ebody = write_post_en(t, body)
                        if qa_ok_en(ebody):
                            ebody, _ = add_image(ebody, eimgq, t["topic"])
                            ebody = inject_monetize_en(ebody, eimgq or t["topic"])
                            ebody += f"\n\n---\n*Originally covered on [Daily Trend Blog]({gh_url})*"
                            for name, fn in (("Blogger-EN", publish_blogger_en),
                                             ("dev.to", publish_devto),
                                             ("Hashnode", publish_hashnode)):
                                try:
                                    if fn(etitle, ebody, t["category"]):
                                        chans.append(name)
                                except Exception as e:
                                    print(f"{name} 실패: {type(e).__name__}", flush=True)
                    except Exception as e:
                        print("EN 파이프라인 스킵:", type(e).__name__, flush=True)

                telegram_broadcast(f"📰 {title}\n{gh_url}")
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
