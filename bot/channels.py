"""다채널 게시 + 수익화 + 이미지 v9 — 브라우저 UA(Cloudflare 1010 해결), dev.to/Hashnode 광고 제거"""
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Cloudflare가 파이썬 기본 UA를 차단(error 1010) → 모든 요청에 브라우저 UA
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _req(url, data=None, headers=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    return urllib.request.Request(url, data=data, headers=h)


def _post(url, data=None, headers=None, form=False):
    if isinstance(data, dict) and not form:
        data = json.dumps(data).encode()
    elif isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    try:
        return json.loads(urllib.request.urlopen(_req(url, data, headers), timeout=30).read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} @ {url[:60]}: {e.read().decode()[:200]}", flush=True)
        raise


def _get(url, headers=None):
    try:
        return json.loads(urllib.request.urlopen(_req(url, None, headers), timeout=20).read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} @ {url[:60]}: {e.read().decode()[:200]}", flush=True)
        raise


def md2html(md: str) -> str:
    h = md
    h = re.sub(r"!\[(.*?)\]\((.*?)\)", r'<img src="\2" alt="\1" style="max-width:100%">', h)
    h = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', h)
    h = re.sub(r"^### (.*)$", r"<h3>\1</h3>", h, flags=re.M)
    h = re.sub(r"^## (.*)$", r"<h2>\1</h2>", h, flags=re.M)
    h = re.sub(r"^# (.*)$", r"<h2>\1</h2>", h, flags=re.M)
    h = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)
    h = re.sub(r"^\- (.*)$", r"<li>\1</li>", h, flags=re.M)
    h = re.sub(r"(<li>.*</li>\n?)+", lambda m: "<ul>" + m.group(0) + "</ul>", h)
    paras = [p if p.lstrip().startswith("<") else f"<p>{p}</p>"
             for p in h.split("\n\n") if p.strip()]
    return "\n".join(paras)


def strip_html_ads(md: str) -> str:
    """dev.to/Hashnode용: 광고 HTML 제거한 순수 마크다운."""
    md = re.sub(r"<script.*?</script>", "", md, flags=re.S)
    md = re.sub(r"<ins .*?</ins>", "", md, flags=re.S)
    md = re.sub(r"<hr>|</?p[^>]*>", "", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


# ═══════ 이미지: Pexels → Unsplash 폴백 ═══════
def fetch_image(query_en: str):
    px = os.environ.get("PEXELS_API_KEY")
    if px:
        try:
            q = urllib.parse.quote((query_en or "")[:60])
            d = _get(f"https://api.pexels.com/v1/search?query={q}&per_page=3&orientation=landscape",
                     {"Authorization": px})
            ph = (d.get("photos") or [None])[0]
            if ph:
                return (ph["src"]["large"],
                        f"*사진: [{ph['photographer']}]({ph['url']}) / Pexels*")
        except Exception as e:
            print("Pexels 스킵:", type(e).__name__, flush=True)
    un = os.environ.get("UNSPLASH_ACCESS_KEY")
    if un:
        try:
            q = urllib.parse.quote((query_en or "")[:60])
            d = _get(f"https://api.unsplash.com/search/photos?query={q}&per_page=3&client_id={un}")
            r = (d.get("results") or [None])[0]
            if r:
                try:
                    urllib.request.urlopen(
                        _req(r["links"]["download_location"] + f"&client_id={un}"), timeout=10)
                except Exception:
                    pass
                name = r["user"]["name"]
                return (r["urls"]["regular"],
                        f"*Photo by [{name}]({r['user']['links']['html']}) on Unsplash*")
        except Exception as e:
            print("Unsplash 스킵:", type(e).__name__, flush=True)
    return None, ""


# ═══════ 수익화 ═══════
def coupang_box(keyword: str) -> str:
    ak, sk = os.environ.get("COUPANG_ACCESS_KEY"), os.environ.get("COUPANG_SECRET_KEY")
    if not (ak and sk):
        return ""
    try:
        path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
        query = urllib.parse.urlencode({"keyword": keyword, "limit": 3})
        dt = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
        sig = hmac.new(sk.encode(), (dt + "GET" + path + query).encode(), hashlib.sha256).hexdigest()
        auth = f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={dt}, signature={sig}"
        d = _get(f"https://api-gateway.coupang.com{path}?{query}", {"Authorization": auth})
        items = (d.get("data") or {}).get("productData", [])[:3]
        if not items:
            return ""
        rows = "".join(
            f'<li><a href="{p["productUrl"]}" target="_blank" rel="nofollow sponsored">'
            f'{p["productName"][:60]}</a> — {int(p.get("productPrice", 0)):,}원</li>' for p in items)
        return ('\n\n<hr><h3>🛒 함께 보면 좋은 상품</h3><ul>' + rows + "</ul>"
                                                              '<p style="font-size:12px;color:#888">이 포스팅은 쿠팡 파트너스 활동의 일환으로, '
                                                              "이에 따른 일정액의 수수료를 제공받습니다.</p>")
    except Exception as e:
        print("쿠팡 스킵:", type(e).__name__, flush=True)
        return ""


def amazon_box(keyword_en: str) -> str:
    tag = os.environ.get("AMAZON_TAG", "")
    if not tag:
        return ""
    q = urllib.parse.quote((keyword_en or "trending")[:60])
    return ("\n\n---\n"
            f"🛒 [Explore related products on Amazon](https://www.amazon.com/s?k={q}&tag={tag})\n\n"
            "*As an Amazon Associate I earn from qualifying purchases.*")


def adsense_slot() -> str:
    client = os.environ.get("ADSENSE_CLIENT", "")
    if not client:
        return ""
    return (f'<ins class="adsbygoogle" style="display:block" data-ad-client="{client}" '
            'data-ad-format="auto" data-full-width-responsive="true"></ins>'
            "<script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>")


def adfit_slot(unit: str) -> str:
    if not unit:
        return ""
    return (f'<ins class="kakao_ad_area" style="display:none;" data-ad-unit="{unit}" '
            'data-ad-width="160" data-ad-height="600"></ins>'
            '<script type="text/javascript" src="//t1.daumcdn.net/kas/static/ba.min.js" async></script>')


def inject_monetize(body_md: str, category: str, topic: str) -> str:
    paras = body_md.split("\n\n")
    g = adsense_slot()
    k1 = adfit_slot(os.environ.get("ADFIT_UNIT", ""))
    k2 = adfit_slot(os.environ.get("ADFIT_UNIT2", ""))
    if k1:
        paras.insert(max(1, len(paras) * 2 // 3), k1)
    if g:
        paras.insert(max(1, len(paras) // 2), g)
    body_md = "\n\n".join(paras)
    if k2:
        body_md += "\n\n" + k2
    body_md += coupang_box(topic if len(topic) < 20 else category)
    return body_md


def inject_monetize_en(body_md: str, keyword_en: str) -> str:
    paras = body_md.split("\n\n")
    g = adsense_slot()
    if g:
        paras.insert(max(1, len(paras) // 2), g)
    return "\n\n".join(paras) + amazon_box(keyword_en)


# ═══════ 게시 채널 ═══════
def publish_blogger(title, body_md, category, blog_env="BLOGGER_BLOG_ID"):
    cid, csec = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    rtok, blog = os.environ.get("GOOGLE_REFRESH_TOKEN"), os.environ.get(blog_env)
    if not all([cid, csec, rtok, blog]):
        return None
    tok = _post("https://oauth2.googleapis.com/token",
                {"client_id": cid, "client_secret": csec, "refresh_token": rtok,
                 "grant_type": "refresh_token"}, form=True)["access_token"]
    res = _post(f"https://www.googleapis.com/blogger/v3/blogs/{blog}/posts/",
                {"kind": "blogger#post", "title": title,
                 "content": md2html(body_md), "labels": [category]},
                {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    return res.get("url")


def publish_blogger_en(title, body_md, category):
    return publish_blogger(title, body_md, category, blog_env="BLOGGER_BLOG_ID_EN")


def publish_naver(title, body_md, category):
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    rtok = os.environ.get("NAVER_REFRESH_TOKEN")
    if not all([cid, csec, rtok]):
        return None
    q = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": cid,
                                "client_secret": csec, "refresh_token": rtok})
    tok = _get(f"https://nid.naver.com/oauth2.0/token?{q}")["access_token"]
    res = _post("https://openapi.naver.com/blog/writePost.json",
                {"title": title, "contents": md2html(body_md)},
                {"Authorization": f"Bearer {tok}"}, form=True)
    return "네이버 게시 OK" if res else None


def publish_devto(title, body_md, category):
    key = os.environ.get("DEVTO_API_KEY")
    if not key:
        return None
    res = _post("https://dev.to/api/articles",
                {"article": {"title": title, "body_markdown": strip_html_ads(body_md),
                             "published": True, "tags": ["trends", "news"]}},
                {"api-key": key, "Content-Type": "application/json"})
    return res.get("url")


def publish_hashnode(title, body_md, category):
    tok, pub = os.environ.get("HASHNODE_TOKEN"), os.environ.get("HASHNODE_PUB_ID")
    if not (tok and pub):
        return None
    gql = {"query": """mutation($input: PublishPostInput!) {
             publishPost(input: $input) { post { url } } }""",
           "variables": {"input": {"title": title,
                                   "contentMarkdown": strip_html_ads(body_md),
                                   "publicationId": pub}}}
    res = _post("https://gql.hashnode.com", gql,
                {"Authorization": tok, "Content-Type": "application/json"})
    return (((res.get("data") or {}).get("publishPost") or {}).get("post") or {}).get("url")


def telegram_broadcast(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return None
    try:
        _post(f"https://api.telegram.org/bot{tok}/sendMessage",
              {"chat_id": chat, "text": text, "disable_web_page_preview": False})
        return True
    except Exception as e:
        print("Telegram 스킵:", type(e).__name__, flush=True)
        return None