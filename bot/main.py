"""자율 트렌드 블로그 팀 v2 — 다채널 게시 + 수익화 자동 삽입, 사람 개입 0"""
import os, sys, json, re, html, traceback
from datetime import datetime, timezone, timedelta
import urllib.request
from channels import inject_monetize, publish_blogger, publish_naver, publish_devto

KST = timezone(timedelta(hours=9))
GROQ_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "3"))

def notify(msg):
    print(msg)
    if not WEBHOOK: return
    try:
        # User-Agent 헤더 추가
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        req = urllib.request.Request(WEBHOOK, data=json.dumps({"content": msg[:1900]}, headers=headers).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("알림 실패:", e)

def collect_trends(geo="KR"):
    req = urllib.request.Request(f"https://trends.google.com/trending/rss?geo={geo}",
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"})
    xml = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        d = re.search(r"<ht:news_item_snippet>(.*?)</ht:news_item_snippet>", it, re.S)
        if t: out.append({"topic": html.unescape(t.group(1).strip()),
                          "snippet": html.unescape((d.group(1) if d else "").strip())[:300]})
    return out[:20]

def llm(prompt, max_tokens=2500):
    body = json.dumps({"model": "llama-3.3-70b-versatile",
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": 0.7}).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=90).read()
                      )["choices"][0]["message"]["content"].strip()

CATEGORIES = ["IT·테크", "연예·문화", "스포츠", "경제·비즈니스", "사회·이슈", "라이프"]

def categorize(trends):
    listing = "\n".join(f"{i+1}. {t['topic']} — {t['snippet'][:80]}" for i, t in enumerate(trends))
    out = llm(f"토픽을 분류하라. 카테고리: {', '.join(CATEGORIES)}\n{listing}\n\n"
              'JSON만: [{"idx":1,"category":"IT·테크"}]', 800)
    m = re.search(r"\[.*\]", out, re.S)
    try: mapping = json.loads(m.group(0)) if m else []
    except Exception: mapping = []
    for row in mapping:
        i = row.get("idx", 0) - 1
        if 0 <= i < len(trends): trends[i]["category"] = row.get("category", "사회·이슈")
    for t in trends: t.setdefault("category", "사회·이슈")
    return trends

# ── Writer: 2패스(초안 → 에디터 퇴고)로 퀄리티 강화 ──
def write_post(t):
    draft = llm(
        f"토픽: {t['topic']}\n배경: {t['snippet']}\n카테고리: {t['category']}\n\n"
        "한국 독자용 고품질 블로그 글 초안을 마크다운으로 작성.\n"
        "- 1200~1800자\n- 구성: 흥미로운 도입 → ## 소제목 3개(각각 구체적 내용) → ## 자주 묻는 질문(Q&A 2개) → 마무리 전망\n"
        "- 확인 안 된 사실 단정 금지('~로 알려졌다/보인다')\n- 제목 없이 본문만")
    final = llm(
        "너는 시니어 에디터다. 아래 블로그 초안을 퇴고하라:\n"
        "- 문장 자연스럽게, 중복 제거, 정보 밀도 높이기\n- 과장/광고체 제거, 신뢰감 있는 톤\n"
        "- 마크다운 구조(##, 리스트) 유지\n- 퇴고된 전체 글만 출력\n\n" + draft, 2500)
    title = llm(f"이 글의 SEO 최적화 한국어 제목 1개만 출력(25자 내외, 따옴표 없이):\n{final[:500]}", 60)
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
    notify(f"🚀 **트렌드 블로그 봇 시작** — {now:%Y-%m-%d %H:%M} KST")
    try:
        trends = collect_trends()
        if not trends:
            notify("⚠️ 트렌드 수집 실패 — 종료"); return
        notify(f"🔎 수집 {len(trends)}건 → 분류·작성")
        trends = categorize(trends)

        seen, picked = set(), []
        for t in trends:
            if t["category"] not in seen:
                picked.append(t); seen.add(t["category"])
            if len(picked) >= MAX_POSTS: break

        report = []
        for t in picked:
            try:
                title, body = write_post(t)
                if not qa_ok(body):
                    title, body = write_post(t)
                if not qa_ok(body):
                    notify(f"⚠️ 품질 미달로 제외: {t['topic']}"); continue
                body = inject_monetize(body, t["category"], t["topic"])

                chans = ["GitHub"]
                publish_github(title, body, t, now)
                for name, fn in (("Blogger", publish_blogger),
                                 ("Naver", publish_naver), ("dev.to", publish_devto)):
                    try:
                        if fn(title, body, t["category"]): chans.append(name)
                    except Exception as e:
                        notify(f"⚠️ {name} 게시 실패({t['topic']}): {type(e).__name__}")
                report.append(f"· [{t['category']}] {title} → {', '.join(chans)}")
            except Exception as e:
                notify(f"⚠️ '{t['topic']}' 실패: {type(e).__name__}")

        notify(("✅ **게시 완료 " + str(len(report)) + "건**\n" + "\n".join(report))
               if report else "⚠️ 게시된 글 없음")
    except Exception:
        notify("❌ **봇 실패**\n```" + traceback.format_exc()[-800:] + "```"); sys.exit(1)
    notify(f"🏁 종료 — {datetime.now(KST):%H:%M} KST")

if __name__ == "__main__":
    main()
