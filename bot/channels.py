"""다채널 게시 + 수익화 삽입. 시크릿 없는 채널은 자동 스킵(안 죽음)."""
import hashlib
import hmac
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _post(url, data=None, headers=None, form=False):
    if isinstance(data, dict) and not form:
        data = json.dumps(data).encode()
    elif isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def md2html(md: str) -> str:
    h = md
    h = re.sub(r"^### (.*)$", r"<h3>\1</h3>", h, flags=re.M)
    h = re.sub(r"^## (.*)$", r"<h2>\1</h2>", h, flags=re.M)
    h = re.sub(r"^# (.*)$", r"<h2>\1</h2>", h, flags=re.M)
    h = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)
    h = re.sub(r"^\- (.*)$", r"<li>\1</li>", h, flags=re.M)
    h = re.sub(r"(<li>.*</li>\n?)+", lambda m: "<ul>" + m.group(0) + "</ul>", h)
    paras = [p if p.startswith("<") else f"<p>{p}</p>" for p in h.split("\n\n") if p.strip()]
    return "\n".join(paras)


def coupang_box(keyword: str) -> str:
    ak, sk = os.environ.get("COUPANG_ACCESS_KEY"), os.environ.get("COUPANG_SECRET_KEY")
    if not (ak and sk):
        return ""
    try:
        path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
        query = urllib.parse.urlencode({"keyword": keyword, "limit": 3})
        dt = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
        msg = dt + "GET" + path + query
        sig = hmac.new(sk.encode(), msg.encode(), hashlib.sha256).hexdigest()
        auth = f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={dt}, signature={sig}"
        req = urllib.request.Request(f"https://api-gateway.coupang.com{path}?{query}",
                                     headers={"Authorization": auth})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        items = (data.get("data") or {}).get("productData", [])[:3]
        if not items:
            return ""
        rows = "".join(
            f'<li><a href="{p["productUrl"]}" target="_blank" rel="nofollow sponsored">'
            f'{p["productName"][:60]}</a> — {int(p.get("productPrice", 0)):,}원</li>' for p in items)
        return ('\n\n<hr><h3>🛒 함께 보면 좋은 상품</h3><ul>' + rows + "</ul>"
                                                              '<p style="font-size:12px;color:#888">이 포스팅은 쿠팡 파트너스 활동의 일환으로, '
                                                              "이에 따른 일정액의 수수료를 제공받습니다.</p>")
    except Exception as e:
        print("쿠팡 스킵:", type(e).__name__)
        return ""


def adfit_slot(unit: str) -> str:
    if not unit:
        return ""
    return (f'<ins class="kakao_ad_area" style="display:none;" data-ad-unit="{unit}" '
            'data-ad-width="160" data-ad-height="600"></ins>'
            '<script type="text/javascript" src="//t1.daumcdn.net/kas/static/ba.min.js" async></script>')


def adsense_slot() -> str:
    client = os.environ.get("ADSENSE_CLIENT", "")
    if not client:
        return ""
    return (f'<ins class="adsbygoogle" style="display:block" data-ad-client="{client}" '
            'data-ad-format="auto" data-full-width-responsive="true"></ins>'
            "<script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>")

def adfit_slot() -> str:
    unit = os.environ.get("ADFIT_UNIT", "")
    if not unit:
        return ""
    return (f'<ins class="kakao_ad_area" style="display:none;" data-ad-unit="{unit}" '
            'data-ad-width="320" data-ad-height="100"></ins>'
            '<script async src="https://t1.daumcdn.net/kas/static/ba.min.js"></script>')


def inject_monetize(body_md: str, category: str, topic: str) -> str:
    """애드센스=본문 중간, 애드핏1=본문 2/3, 애드핏2=본문 끝, 쿠팡=최하단."""
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


def publish_blogger(title, body_md, category):
    cid, csec = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    rtok, blog = os.environ.get("GOOGLE_REFRESH_TOKEN"), os.environ.get("BLOGGER_BLOG_ID")
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


def publish_naver(title, body_md, category):
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    rtok = os.environ.get("NAVER_REFRESH_TOKEN")
    if not all([cid, csec, rtok]):
        return None
    q = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": cid,
                                "client_secret": csec, "refresh_token": rtok})
    tok = json.loads(urllib.request.urlopen(
        f"https://nid.naver.com/oauth2.0/token?{q}", timeout=20).read().decode())["access_token"]
    res = _post("https://openapi.naver.com/blog/writePost.json",
                {"title": title, "contents": md2html(body_md)},
                {"Authorization": f"Bearer {tok}"}, form=True)
    return "네이버 게시 OK" if res else None


def publish_devto(title, body_md, category):
    key = os.environ.get("DEVTO_API_KEY")
    if not key:
        return None
    res = _post("https://dev.to/api/articles",
                {"article": {"title": title, "body_markdown": body_md,
                             "published": True, "tags": ["trends"]}},
                {"api-key": key, "Content-Type": "application/json"})
    return res.get("url")