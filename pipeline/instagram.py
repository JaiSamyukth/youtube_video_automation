"""Publish a Reel to Instagram (Graph API, Instagram account linked to a Facebook Page).

The Graph API cannot schedule Reels, so the publish workflow calls this at the slot time.
The video is sent straight from disk with the resumable upload route (rupload.facebook.com),
so it does not need a public URL and works from a private repo.

Secrets per channel: <PREFIX>_IG_ACCESS_TOKEN (long-lived user token with instagram_basic,
instagram_content_publish, pages_show_list, pages_read_engagement) and optionally
<PREFIX>_IG_USER_ID. The account must match the channel's instagram.username in channels.yaml,
otherwise nothing is posted (protects against posting to the wrong account)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Channel

API = "https://graph.facebook.com/v23.0"
DROP_TAGS = {"shorts", "youtubeshorts", "ytshorts", "short"}


def _call(method, path, token, data=None):
    url = API + path
    body = None
    if method == "GET":
        url += ("&" if "?" in url else "?") + "access_token=" + urllib.parse.quote(token)
    else:
        body = urllib.parse.urlencode({**(data or {}), "access_token": token}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method), timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
        except Exception:
            err = {}
        return e.code, {"error": {"code": err.get("code"), "message": str(err.get("message", ""))[:300]}}


def caption(meta: dict) -> str:
    tags = []
    for t in meta.get("hashtags", []) + meta.get("ig_extra_tags", []):
        t = "".join(c for c in str(t).lower().lstrip("#") if c.isalnum())
        if t and t not in DROP_TAGS and t not in tags:
            tags.append(t)
    line = " ".join("#" + t for t in tags[:8])
    base = (meta.get("title", "") + "\n\n" + meta.get("description", "")).strip()
    return (base[: 2180 - len(line)].rstrip() + ("\n\n" + line if line else ""))


def resolve_account(ch: Channel) -> tuple[str, str]:
    """Returns (ig_user_id, page_token) for the channel's expected Instagram username."""
    token = ch.secret("IG", "ACCESS_TOKEN")
    if not token:
        raise RuntimeError(f"missing {ch.instagram_prefix}_IG_ACCESS_TOKEN")
    if not ch.instagram_username:
        raise RuntimeError(f"channels.yaml has no instagram.username for {ch.id}")
    st, pages = _call("GET", "/me/accounts?fields=name,access_token,instagram_business_account%7Bid,username%7D", token)
    if st != 200:
        raise RuntimeError(f"Instagram account lookup failed: {pages.get('error')}")
    m = [p for p in pages.get("data", []) if p.get("instagram_business_account", {}).get("username") == ch.instagram_username]
    if len(m) != 1:
        raise RuntimeError(f"expected exactly one Page linked to @{ch.instagram_username}, found {len(m)}")
    uid = m[0]["instagram_business_account"]["id"]
    want = ch.secret("IG", "USER_ID")
    if want and want != uid:
        raise RuntimeError("Instagram account id does not match IG_USER_ID")
    return uid, m[0].get("access_token") or token


def publish(ch: Channel, video_path: str, meta: dict) -> dict:
    uid, tok = resolve_account(ch)
    st, cont = _call("POST", f"/{uid}/media", tok, {"media_type": "REELS", "upload_type": "resumable",
                                                    "caption": caption(meta), "share_to_feed": "true"})
    cid = cont.get("id")
    if st != 200 or not cid:
        raise RuntimeError(f"Reel container failed: {cont.get('error')}")
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        req = urllib.request.Request(f"https://rupload.facebook.com/ig-api-upload/v23.0/{cid}", data=f.read(),
                                     method="POST", headers={"Authorization": "OAuth " + tok, "offset": "0",
                                                             "file_size": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            up = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Reel upload failed: {e.code} {e.read().decode()[:300]}") from None
    if not up.get("success", True):
        raise RuntimeError(f"Reel upload not accepted: {up}")
    status = ""
    for _ in range(40):                       # about 10 minutes
        time.sleep(15)
        s, r = _call("GET", f"/{cid}?fields=status_code", tok)
        status = r.get("status_code", "") if s == 200 else "POLL_ERROR"
        if status in ("FINISHED", "ERROR", "EXPIRED"):
            break
    if status != "FINISHED":
        raise RuntimeError(f"Instagram processing ended as {status}")
    st, pub = _call("POST", f"/{uid}/media_publish", tok, {"creation_id": cid})
    mid = pub.get("id")
    if st != 200 or not mid:
        raise RuntimeError(f"Reel publish failed: {pub.get('error')}")
    _, pl = _call("GET", f"/{mid}?fields=permalink", tok)
    return {"id": mid, "url": pl.get("permalink", "")}
