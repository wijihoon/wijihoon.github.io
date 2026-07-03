"""자율 트렌드 블로그 팀 v8 — 제목/본문 정화·관대한 파서·채널 상태 보고"""
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
EN_ENABLED = os.environ.get("EN_POSTS", "1") == "1"

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


def channel_status():
    """활성/미설정 채널을 시작 알림에 표시 — '왜 안 나갔지?' 방지."""
    def on(*keys):
        return all(os.environ.get(k) for k in keys)
    chans = {
        "GitHub": True,
        "Blogger": on("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "BLOGGER_BLOG_ID"),
        "Naver": on("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NAVER_REFRESH_TOKEN"),
        "Blogger-EN": on("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "BLOGGER_BLOG_ID_EN"),
        "dev.to": on("DEVTO_API_KEY"),
        "Hashnode": on("HASHNODE_TOKEN", "HASHNODE_PUB_ID"),
        "Telegram": on("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        "이미지": on("PEXELS_API_KEY") or on("UNSPLASH_ACCESS_KEY"),
        "Amazon": on("AMAZON_TAG"),
        "쿠팡": on("COUPANG_ACCESS_KEY", "COUPANG_SECRET_KEY"),
    }
    ok = [k for k, v in chans.items() if v]
    off = [k for k, v in chans.items() if not v]
    return "✅ " + ", ".join(ok) + (f"\n⚪ 미설정: {', '.join(off)}" if off else "")


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


# ═════════ 정화 로직: 모델이 형식을 어겨도 결과물은 깨끗하게 ═════════
META_WORDS = ("TITLE", "DESC", "TAGS", "IMGQ", "제목", "클릭을 부르는")


def sanitize_body(body):
    """지시문 에코·메타 라인 제거, 붙어버린 헤딩 분리."""
    # '#### 소제목'이 문장 중간에 붙은 경우 → 개행으로 분리
    body = re.sub(r"\s*(#{2,4})\s*", r"\n\n## ", body)      # ####·### → ## 로 통일+분리
    lines = []
    for ln in body.splitlines():
        s = ln.strip()
        # 메타/지시문 에코 라인 제거
        if any(s.upper().startswith(w.upper()) or s.startswith(w) for w in META_WORDS):
            # 단, 실제 소제목(##)은 위 치환에서 이미 처리됨
            if not s.startswith("##"):
                continue
        lines.append(ln)
    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def extract_title(text, topic):
    """어디에 있든 TITLE 계열 라인을 찾아내는 관대한 파서 + 폴백 생성."""
    # 1) TITLE: / 제목: / 클릭을 부르는 제목: 패턴을 전체에서 탐색
    m = re.search(r"^\s*(?:TITLE|제목|클릭을 부르는 제목[^:：]*)\s*[:：]\s*(.+)$",
                  text, re.M | re.I)
    cand = m.group(1) if m else ""
    cand = re.sub(r"[#*`=_\"']+", " ", cand)
    cand = re.sub(r"\s{2,}", " ", cand).strip()
    # 2) 못 찾았거나 이상하면: 첫 문장에서 시도
    if not (8 <= len(cand) <= 45):
        first = re.sub(r"[#*`]+", "", text.strip().split("\n")[0])[:60]
        if 10 <= len(first) <= 45 and ":" not in first[:6]:
            cand = first.strip()
    # 3) 최종 폴백: 토픽 기반 자동 제목 (정적이지 않게)
    if not (8 <= len(cand) <= 45):
        cand = f"{topic}, 지금 검색량이 급증한 이유"[:40]
    return cand[:40]


def parse_meta(final, topic):
    head, sep, body = final.partition("---")
    src = head if sep else final
    title = extract_title(src, topic)
    desc = tags = imgq = ""
    for ln in src.splitlines():
        s = ln.strip()
        u = s.upper()
        if u.startswith("DESC"):
            desc = re.sub(r"^DESC\s*[:：]\s*", "", s, flags=re.I).strip()
        elif u.startswith("TAGS"):
            tags = re.sub(r"^TAGS\s*[:：]\s*", "", s, flags=re.I).strip()
        elif u.startswith("IMGQ"):
            imgq = re.sub(r"^IMGQ\s*[:：]\s*", "", s, flags=re.I).strip()
    body = sanitize_body(body if sep else final)
    if not desc:
        desc = re.sub(r"[#*`\n]+", " ", body)[:100].strip()
    desc = re.sub(r"[#*`\"]+", "", desc)[:120]
    tags = ", ".join(x.strip() for x in tags.split(",") if x.strip())[:80]
    return title, desc, tags, imgq, body


def write_post(t):
    draft = llm(
        f"토픽: {t['topic']}\n배경: {t['snippet']}\n카테고리: {t['category']}\n\n"
        "한국 독자용 고품질 블로그 글 초안을 마크다운으로 작성하라.\n"
        "- 분량 1200~1800자\n"
        "- 구성: 궁금증을 짚는 도입 2~3문장 → '**3줄 요약**' 리스트 →\n"
        "  ## 소제목 3~4개(구체적 정보·수치·맥락) → ## 자주 묻는 질문(Q&A 3개) → 전망 마무리\n"
        "- 확인 안 된 사실 단정 금지('~로 알려졌다/보인다')\n"
        "- 같은 문장 반복 금지, 광고체 금지", 2500)
    time.sleep(15)
    final = llm(
        "너는 10년차 시니어 에디터다. 아래 초안을 퇴고하고, 정확히 아래 형식으로만 출력하라.\n"
        "형식 라벨(TITLE 등)은 그대로 쓰고, 지시문을 되풀이하지 마라.\n\n"
        "TITLE: (제목만 작성. 25~38자. 숫자·호기심 갭·독자 이득 중 1개 사용. 예: '하이닉스 주가가 갑자기 뛴 3가지 이유'. 과장 금지)\n"
        "DESC: (검색결과용 요약 1문장, 70~110자)\n"
        "TAGS: (키워드 3~5개, 쉼표 구분)\n"
        "IMGQ: (사진 검색용 영어 단어 2~3개)\n"
        "---\n"
        "(퇴고된 본문 전체. 소제목은 반드시 '## '로 시작하고 앞뒤 빈 줄. 본문에 TITLE/DESC 등 라벨 금지)\n\n"
        "=== 초안 ===\n" + draft, 2500)
    time.sleep(15)
    return parse_meta(final, t["topic"])


def write_post_en(t, body_kr):
    final = llm(
        "You are a senior editor for a global trends blog.\n"
        f"Topic (trending now): {t['topic']}\n"
        "Rewrite the Korean article below as an original English post for international "
        "readers (adapt, don't translate literally; add brief context non-Korean readers need).\n"
        "500-800 words, ## subheadings, short FAQ. No unverified facts as certain.\n"
        "Output EXACTLY this format, no extra words:\n"
        "TITLE: (title only, 50-65 chars, use a number or curiosity gap, no clickbait)\n"
        "DESC: (one-sentence meta description)\n"
        "TAGS: (3-5 keywords, comma-separated)\n"
        "IMGQ: (2-3 English words for stock photo search)\n"
        "---\n"
        "(full article, markdown, '## ' subheadings on their own lines)\n\n"
        "=== Korean article ===\n" + body_kr[:4000], 2200)
    time.sleep(15)
    return parse_meta(final, t["topic"])


def qa_ok(body):
    return (len(body) >= 800 and body.count("##") >= 3
            and not any(b in body for b in ["죄송", "도와드릴 수 없", "AI 언어 모델", "TITLE:", "클릭을 부르는"]))


def qa_ok_en(body):
    return len(body) >= 500 and body.count("##") >= 2 and "TITLE:" not in body


def add_image(body_md, imgq, topic):
    url, credit = fetch_image(imgq or topic)
    if not url:
        return body_md, ""
    paras = body_md.split("\n\n")
    paras.insert(min(1, len(paras)), f"![{imgq or topic}]({url})\n{credit}")
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
    notify(f"🚀 **InsightDaily 봇 시작** — {now:%Y-%m-%d %H:%M} KST · GEO={','.join(GEOS)}\n"
           + channel_status())
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
                        else:
                            print("EN 품질 미달 — 영어 게시 생략", flush=True)
                    except Exception as e:
                        notify(f"⚠️ EN 파이프라인 실패({t['topic']}): {type(e).__name__}")

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