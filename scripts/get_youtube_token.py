#!/usr/bin/env python3
"""Get a YouTube refresh token for one channel (run on your own computer, once per channel).

  python scripts/get_youtube_token.py --client-id XXX --client-secret YYY

A browser opens: sign in, then pick the CHANNEL this token is for. The refresh token is printed;
paste it into the repo secret (e.g. DEEP_YT_REFRESH_TOKEN). Stdlib only."""
import argparse, http.server, json, secrets, urllib.parse, urllib.request, webbrowser

ap = argparse.ArgumentParser()
ap.add_argument("--client-id", required=True)
ap.add_argument("--client-secret", required=True)
ap.add_argument("--port", type=int, default=8765)
a = ap.parse_args()
redirect = f"http://127.0.0.1:{a.port}/"
state = secrets.token_urlsafe(16)
url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": a.client_id, "redirect_uri": redirect, "response_type": "code", "access_type": "offline",
    "prompt": "consent", "state": state,
    "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"})
got = {}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        got.update({k: v[0] for k, v in q.items()})
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Done. You can close this tab.")

    def log_message(self, *x):
        pass


print("Opening:", url)
webbrowser.open(url)
srv = http.server.HTTPServer(("127.0.0.1", a.port), H)
while "code" not in got and "error" not in got:
    srv.handle_request()
if got.get("state") != state or "code" not in got:
    raise SystemExit(f"sign-in failed: {got.get('error', 'state mismatch')}")
data = urllib.parse.urlencode({"code": got["code"], "client_id": a.client_id, "client_secret": a.client_secret,
                               "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
tok = json.loads(urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data).read())
print("\nREFRESH TOKEN (keep it secret):\n" + tok["refresh_token"])
