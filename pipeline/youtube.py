"""Upload a Short to YouTube as private with `publishAt`, so YouTube publishes it at the exact
slot time even if our scheduler runs late. Stdlib only.

Each channel uses its own refresh token: <PREFIX>_YT_REFRESH_TOKEN. The OAuth client may be
shared (YT_CLIENT_ID / YT_CLIENT_SECRET) or per channel (<PREFIX>_YT_CLIENT_ID ...). Note the
YouTube Data API quota is per Google Cloud project: 10,000 units/day, one upload = 1,600, so
one project covers 6 uploads a day. The project must have passed YouTube's API audit, or every
API upload is locked to private."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .config import Channel


def _post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def access_token(ch: Channel) -> str:
    cid, sec, rt = ch.secret("YT", "CLIENT_ID"), ch.secret("YT", "CLIENT_SECRET"), ch.secret("YT", "REFRESH_TOKEN")
    if not (cid and sec and rt):
        raise RuntimeError(f"missing YouTube secrets for {ch.id} ({ch.youtube_prefix}_YT_REFRESH_TOKEN etc.)")
    return _post_form("https://oauth2.googleapis.com/token", {"client_id": cid, "client_secret": sec,
                                                             "refresh_token": rt, "grant_type": "refresh_token"})["access_token"]


def build_body(meta: dict, publish_at: str) -> dict:
    tags = [t.lstrip("#") for t in meta.get("hashtags", []) if t]
    if "shorts" not in tags:
        tags = ["shorts"] + tags
    desc = (meta.get("description", "").strip() + "\n\n" + " ".join("#" + t for t in tags[:5])).strip()
    return {"snippet": {"title": meta["title"][:100], "description": desc[:4900], "tags": tags[:15],
                        "categoryId": meta.get("category_id", "27")},   # 27 = Education
            "status": {"privacyStatus": "private", "publishAt": publish_at, "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": False}}


def upload(ch: Channel, video_path: str, meta: dict, publish_at: str) -> dict:
    tok = access_token(ch)
    body = build_body(meta, publish_at)
    size = os.path.getsize(video_path)
    init = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(init, timeout=60) as r:
            loc = r.headers["Location"]
        with open(video_path, "rb") as f:
            put = urllib.request.Request(loc, data=f.read(), method="PUT", headers={"Content-Type": "video/mp4"})
        with urllib.request.urlopen(put, timeout=900) as r:
            resp = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"YouTube upload failed: {e.code} {e.read().decode()[:400]}") from None
    vid = resp["id"]
    return {"id": vid, "url": f"https://youtu.be/{vid}", "privacy": resp.get("status", {}).get("privacyStatus"),
            "publish_at": resp.get("status", {}).get("publishAt")}
