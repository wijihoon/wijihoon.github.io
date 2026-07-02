"""자율 트렌드 블로그 팀 v3 — Groq→Gemini 폴백, 호출 33% 절감, 제목 정화"""
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
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


def collect_trends(geo="KR"):
    try:
        r = requests.get(f"https://trends.google.com/trending/rss?geo={geo}",
                         headers=HEADERS, timeout=20)
        r.raise_for_status()
        out = []
        for it in re.findall(r"<item>(.*?)</item>", r.text, re.S):
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            d = re.search(r"<ht:news_item_snippet>(.*?)</ht:news_item_snippet>", it, re.S)
            if t:
                out.append({"topic": html.unescape(t.group(1).strip()),
                            "snippet": html.unescape((d.group(1) if d else "").strip())[:300]})
        return out[:20]
    except Exception as e:
        print("수집 실패:", e, flush=True)
        return []


# ───────── LLM: Groq 1순위 → Gemini 폴백 (어떤 에러든 폴백) ─────────
def _groq(prompt, max_tokens):
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          json={"model": "llama-3.3-70b-versatile",
                                "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": max_tokens, "temperature": 0.7},
                          headers={"Authorization": f"Bearer {GROQ_KEY}"}, timeout=90)
        if r.status_code != 200:
            print(f"Groq {r.status_code}: {r.text[:150]}", flush=True)
            return None
        return r.json()["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        print("Groq 예외:", type(e).__name__, flush=True)
        return None


def _gemini(prompt, max_tokens):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        r = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={key}",
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7}},
            timeout=90)
        if r.status_code != 200:
            print(f"Gemini {r.status_code}: {r.text[:150]}", flush=True)
            return None
        cands = r.json().get("candidates") or []
        parts = (cands[0].get("content", {}).get("parts") or []) if cands else []
        return (parts[0].get("text", "").strip() or None) if parts else None
    except Exception as e:
        print("Gemini 예외:", type(e).__name__, flush=True)
        return None


def llm(prompt, max_tokens=2500):
    for attempt in range(4):
        out = _groq(prompt, max_tokens)
        if out:
            return out
        print("⏳ Groq 실패 → Gemini 폴백", flush=True)
        out = _gemini(prompt, max_tokens)
        if out:
            return out
        wait = 20 * (attempt + 1)
        print(f"⏳ 양쪽 실패 — {wait}초 대기 ({attempt + 1}/4)", flush=True)
        time.sleep(wait)
    raise Exception("모든 LLM 실패 — 다음 스케줄에서 재시도")


CATEGORIES = ["IT·테크", "연예·문화", "스포츠", "경제·비즈니스", "사회·이슈", "라이프"]


def categorize(trends):
    listing = "\n".join(f"{i + 1}. {t['topic']} — {t['snippet'][:80]}"
                        for i, t in enumerate(trends))
    out = llm(f"토픽을 분류하라. 카테고리: {', '.join(CATEGORIES)}\n{listing}\n\n"
              'JSON만: [{"idx":1,"category":"IT·테크"}]', 800)
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
        t.setdefault("category", "사회·이슈")
    return trends


def clean_title(title, fallback):
    """마크다운/개행/본문조각 제거 — 깨진 제목 방지."""
    title = re.sub(r"^TITLE\s*[:：]\s*", "", title.strip())
    title = re.sub(r"[#*`=_\n\r]+", " ", title)
    title = re.sub(r"\s{2,}", " ", title).strip().strip('"').strip()
    if not (5 <= len(title) <= 60):
        return fallback
    return title[:40]


# ── Writer: 호출 2회(초안 → 퇴고+제목 동시) — 기존 3회에서 33% 절감 ──
def write_post(t):
    draft = llm(f"토픽: {t['topic']}\n배경: {t['snippet']}\n카테고리: {t['category']}\n\n"
                "한국 독자용 고품질 블로그 글 초안. 분량 800~1000자 내외. "
                "## 소제목 3개, Q&A 2개 포함. 확인 안 된 사실 단정 금지.")
    time.sleep(15)
    final = llm("아래 초안을 시니어 에디터로서 퇴고하라(문장 자연스럽게, 정보 밀도 높게).\n"
                "출력 형식(다른 말 금지):\n"
                "첫 줄: TITLE: SEO 제목(25자 내외, 마크다운 금지)\n"
                "둘째 줄부터: 퇴고된 본문 전체(마크다운 유지)\n\n" + draft, 2000)
    time.sleep(15)
    first, _, body = final.partition("\n")
    title = clean_title(first, fallback=t["topic"][:40])
    body = body.strip() or final
    return title, body


def qa_ok(body):
    return len(body) >= 600 and not any(
        b in body for b in ["죄송", "도와드릴 수 없", "AI 언어 모델", "TITLE:"])


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
        if not trends:
            notify("⚠️ 수집 실패")
            return
        trends = categorize(trends)

        seen, picked = set(), []
        for t in trends:
            if t["category"] not in seen:
                picked.append(t)
                seen.add(t["category"])
            if len(picked) >= MAX_POSTS:
                break

        report = []
        for i, t in enumerate(picked):
            if i:
                time.sleep(20)                    # 글 사이 간격 → TPM 분산
            try:
                title, body = write_post(t)
                if not qa_ok(body):
                    title, body = write_post(t)
                if not qa_ok(body):
                    notify(f"⚠️ 품질 미달 제외: {t['topic']}")
                    continue                       # 불량 글은 게시 안 함
                body = inject_monetize(body, t["category"], t["topic"])

                chans = ["GitHub"]
                publish_github(title, body, t, now)
                for name, fn in (("Blogger", publish_blogger),
                                 ("Naver", publish_naver),
                                 ("dev.to", publish_devto)):
                    try:
                        if fn(title, body, t["category"]):
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