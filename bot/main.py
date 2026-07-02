"""자율 트렌드 블로그 팀 v2 — 다채널 게시 + 수익화 자동 삽입, 사람 개입 0"""
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

# 설정
KST = timezone(timedelta(hours=9))
GROQ_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "3"))

# 브라우저 위장 헤더 (403 방지)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://trends.google.com/"
}


def notify(msg):
    print(msg)
    if not WEBHOOK: return
    try:
        requests.post(WEBHOOK, json={"content": msg[:1900]}, headers={"Content-Type": "application/json"}, timeout=10)
    except Exception as e:
        print("알림 실패:", e)


def collect_trends(geo="KR"):
    try:
        response = requests.get(f"https://trends.google.com/trending/rss?geo={geo}", headers=HEADERS, timeout=20)
        response.raise_for_status()
        xml = response.text
        out = []
        for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            d = re.search(r"<ht:news_item_snippet>(.*?)</ht:news_item_snippet>", it, re.S)
            if t:
                out.append({"topic": html.unescape(t.group(1).strip()),
                            "snippet": html.unescape((d.group(1) if d else "").strip())[:300]})
        return out[:20]
    except Exception as e:
        print("수집 실패:", e)
        return []


def llm(prompt, max_tokens=2500):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    for attempt in range(6):  # 최대 6회 재시도
        resp = requests.post(url, json=payload, headers=headers, timeout=90)

        if resp.status_code == 429:  # 분당 한도 → 기다렸다 재시도
            m = re.search(r"try again in ([\d.]+)s", resp.text)
            wait = float(m.group(1)) + 1 if m else 15 * (attempt + 1)
            print(f"⏳ 한도 도달 — {wait:.0f}초 대기 후 재시도 ({attempt + 1}/6)")
            time.sleep(min(wait, 60))
            continue

        if resp.status_code != 200:
            print(f"LLM API 에러 ({resp.status_code}): {resp.text}")
            raise Exception(f"Groq API 오류: {resp.text}")

        data = resp.json()
        if "choices" not in data:
            raise KeyError(f"API 응답 구조 이상: {data}")
        return data["choices"][0]["message"]["content"].strip()

    raise Exception("Groq 한도 재시도 6회 초과 — 다음 실행에서 재시도")


CATEGORIES = ["IT·테크", "연예·문화", "스포츠", "경제·비즈니스", "사회·이슈", "라이프"]


def categorize(trends):
    listing = "\n".join(f"{i + 1}. {t['topic']} — {t['snippet'][:80]}" for i, t in enumerate(trends))
    out = llm(f"토픽을 분류하라. 카테고리: {', '.join(CATEGORIES)}\n{listing}\n\n"
              'JSON만: [{"idx":1,"category":"IT·테크"}]', 800)
    m = re.search(r"\[.*\]", out, re.S)
    try:
        mapping = json.loads(m.group(0)) if m else []
    except Exception:
        mapping = []
    for row in mapping:
        i = row.get("idx", 0) - 1
        if 0 <= i < len(trends): trends[i]["category"] = row.get("category", "사회·이슈")
    for t in trends: t.setdefault("category", "사회·이슈")
    return trends


def write_post(t):
    # 초안 생성
    draft = llm(f"토픽: {t['topic']}\n배경: {t['snippet']}\n카테고리: {t['category']}\n\n"
                "한국 독자용 고품질 블로그 글 초안. 분량 800~1000자 내외. ## 소제목 3개, Q&A 2개 포함.")

    # 2초 휴식
    time.sleep(2)

    # 퇴고
    final = llm("아래 초안을 시니어 에디터로서 퇴고하라 (문장 자연스럽게, 전체 글만 출력):\n" + draft, 1500)

    # 2초 휴식
    time.sleep(2)

    title = llm(f"제목 1개(25자 내외):\n{final[:500]}", 60)
    return title.strip().strip('"'), final


def qa_ok(body):
    return len(body) >= 600 and not any(b in body for b in ["죄송", "도와드릴 수 없", "AI 언어 모델"])


def slugify(s):
    return re.sub(r"[^\w가-힣]+", "-", s).strip("-")[:40] or "post"


def publish_github(title, body_md, t, now):
    fname = f"_posts/{now:%Y-%m-%d}-{slugify(t['topic'])}.md"
    front = (f'---\nlayout: post\ntitle: "{title.replace(chr(34), "")}"\n'
             f'categories: [{t["category"]}]\ndate: {now:%Y-%m-%d %H:%M:%S} +0900\n---\n\n')
    os.makedirs("_posts", exist_ok=True)
    with open(fname, "w", encoding="utf-8") as f:
        f.write(front + body_md + "\n")
    return fname


def main():
    now = datetime.now(KST)
    notify(f"🚀 **InsightDaily 봇 시작** — {now:%Y-%m-%d %H:%M} KST")
    try:
        trends = collect_trends()
        if not trends: notify("⚠️ 수집 실패"); return
        trends = categorize(trends)

        seen, picked = set(), []
        for t in trends:
            if t["category"] not in seen:
                picked.append(t);
                seen.add(t["category"])
            if len(picked) >= MAX_POSTS: break

        report = []
        for t in picked:
            title, body = write_post(t)
            if not qa_ok(body): title, body = write_post(t)
            body = inject_monetize(body, t["category"], t["topic"])

            chans = ["GitHub"]
            publish_github(title, body, t, now)
            # 채널별 게시 시도
            for name, fn in (("Blogger", publish_blogger), ("Naver", publish_naver), ("dev.to", publish_devto)):
                try:
                    if fn(title, body, t["category"]): chans.append(name)
                except:
                    pass
            report.append(f"· [{t['category']}] {title} → {', '.join(chans)}")
        notify(f"✅ **게시 완료 {len(report)}건**\n" + "\n".join(report))
    except Exception:
        notify("❌ **봇 실패**\n```" + traceback.format_exc()[-800:] + "```");
        sys.exit(1)
    notify(f"🏁 종료 — {datetime.now(KST):%H:%M} KST")


if __name__ == "__main__":
    main()
