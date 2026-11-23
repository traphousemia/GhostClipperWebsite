#!/usr/bin/env python3
"""
GhostClipper website + auth server.

Stdlib only — no pip installs. Serves the public site and implements the
account API that the desktop app calls (see core/auth.py for the contract).

Token format matches the app: base64url(JSON payload) + "." + base64url(HMAC-SHA256).
The signing secret MUST match the app's GHOSTCLIPPER_TOKEN_SECRET (both default
to the dev value below — change both together for production).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
import time
import urllib.parse
import urllib.request
import credits_ledger as ledger
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
USERS_FILE = DATA / "users.json"
CONFIG_FILE = ROOT / "config.json"

DEFAULT_CONFIG = {
    "port": 8899,
    # public_url MUST be set via config.json or PUBLIC_URL env on any real host.
    "public_url": os.environ.get("PUBLIC_URL", "http://127.0.0.1:8899"),
    # Secrets come from the environment on managed hosts; dev defaults are only
    # for local testing. TOKEN_SECRET and WEBHOOK_KEY must be set in production.
    "token_secret": os.environ.get("TOKEN_SECRET", "ghostclipper-dev-token-secret"),
    "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
    "google_client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    "webhook_key": os.environ.get("WEBHOOK_KEY", "change-me-before-taking-payments"),
    "min_app_version": "1.0.0",
    "maintenance": False,
    "maintenance_message": "",
    "update_url": "",
    # Stripe: add these at launch. Checkout links are created in the Stripe
    # dashboard (or via API) and pasted here per plan. When set, all UIs light up.
    "payments_enabled": False,
    "stripe_publishable_key": "",
    "stripe_secret_key": "",
    "stripe_webhook_secret": "",
    "checkout": {
        "starter": "",   # e.g. https://buy.stripe.com/test_xxxx
        "creator": "",
        "studio": "",
    },
    "billing_portal_url": "",  # Stripe customer portal (manage card / invoices)
    "plans": [
        {"id": "starter", "name": "Starter", "usd": 4.99, "credits": 200, "tier": "pro", "blurb": "1 streamer, 24/7"},
        {"id": "creator", "name": "Creator", "usd": 12.99, "credits": 600, "tier": "pro", "blurb": "6 streamers, 24/7"},
        {"id": "studio", "name": "Studio", "usd": 19.99, "credits": 1000, "tier": "team", "blurb": "Unlimited streams"},
    ],
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass
    # In production, environment variables ALWAYS win over config.json so
    # local dev secrets can never leak onto a hosted deploy.
    for env, key in (("TOKEN_SECRET", "token_secret"), ("WEBHOOK_KEY", "webhook_key"),
                     ("PUBLIC_URL", "public_url"), ("GOOGLE_CLIENT_ID", "google_client_id"),
                     ("GOOGLE_CLIENT_SECRET", "google_client_secret"),
                     ("STRIPE_WEBHOOK_SECRET", "stripe_webhook_secret")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


CFG = load_config()
SECRET = CFG["token_secret"].encode()

# --- lightweight in-memory rate limiter (per-IP + per-path) ----------------
# Stops brute-force login attempts and credit-verify spam. Resets on restart;
# that's fine for a single-process stdlib server.
import threading as _threading
_rl_lock = _threading.Lock()
_rl_hits: dict[str, list[float]] = {}

def rate_ok(key: str, limit: int, window: int = 60) -> bool:
    now = time.time()
    with _rl_lock:
        hits = _rl_hits.setdefault(key, [])
        hits[:] = [t for t in hits if now - t < window]
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(payload: dict) -> str:
    body = b64e(json.dumps(payload, separators=(",", ":")).encode())
    return body + "." + b64e(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())


def verify_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(b64e(hmac.new(SECRET, body.encode(), hashlib.sha256).digest()), sig):
            return None
        return json.loads(b64d(body))
    except Exception:
        return None


def load_users() -> dict:
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, indent=2))


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return b64e(salt) + "." + b64e(dk)


def check_password(pw: str, stored: str) -> bool:
    try:
        salt_s, dk_s = stored.split(".", 1)
        salt = b64d(salt_s)
        dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(b64e(dk), dk_s)
    except Exception:
        return False


def token_for(user: dict, device_id: str = "") -> str:
    return sign_token({
        "email": user["email"],
        "name": user.get("name", ""),
        "tier": user.get("tier", "trial"),
        "expires": float(user.get("expires", 0)),
        "dev": device_id or str(user.get("active_device_id", "")),
        "iat": time.time(),
    })


def device_valid(p: dict | None, u: dict) -> bool:
    """A signed token is only valid on the device it was issued to.

    Tokens issued before device binding (no 'dev' claim) are accepted so
    existing sessions aren't broken. A bound token whose device no longer
    matches the account is rejected — that's the kicked-out old session.
    """
    if not p:
        return False
    dev = str(p.get("dev", ""))
    if not dev:
        return True
    return dev == str(u.get("active_device_id", ""))


class Handler(BaseHTTPRequestHandler):
    server_version = "GhostClipperSite/1.0"

    def log_message(self, fmt, *args):
        print("[web]", self.address_string(), fmt % args, flush=True)

    # ---------- helpers ----------
    def send(self, obj, code=200, ctype="application/json"):
        raw = (json.dumps(obj).encode() if isinstance(obj, (dict, list)) else str(obj).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
            self.raw_body = self.rfile.read(n) if n else b"{}"
            return json.loads(self.raw_body or b"{}")
        except Exception:
            self.raw_body = b"{}"
            return {}

    def raw_request_body(self) -> bytes:
        return getattr(self, "raw_body", b"{}")

    def file(self, rel: str, ctype: str):
        p = SITE / rel.lstrip("/")
        if not p.exists() or not p.is_file():
            self.send({"error": "not found"}, 404)
            return
        raw = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    # ---------- routes ----------
    def do_OPTIONS(self):
        self.send({}, 200)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/app/config":
            # Live app policy: min version / maintenance. The desktop app checks
            # this at launch — bump min_app_version here to force everyone to update.
            # Read fresh from disk so changing config.json takes effect immediately.
            live = load_config()
            return self.send({
                "min_app_version": live.get("min_app_version", "1.0.0"),
                "maintenance": bool(live.get("maintenance", False)),
                "message": str(live.get("maintenance_message", "")),
                "update_url": str(live.get("update_url", "")),
            })
        if path == "/api/health":
            return self.send({"ok": True, "service": "GhostClipper", "time": time.time()})
        if path == "/api/plans":
            plans = []
            for p in CFG["plans"]:
                q = dict(p)
                q["checkout_ready"] = bool(CFG.get("payments_enabled") and
                                           CFG.get("checkout", {}).get(p["id"]))
                plans.append(q)
            return self.send({"plans": plans,
                              "payments_enabled": bool(CFG.get("payments_enabled")),
                              "billing_ready": bool(CFG.get("billing_portal_url")),
                              "billing_url": CFG.get("billing_portal_url", "")})
        if path == "/api/usage":
            # Usage stats from the desktop app if it's running on this machine.
            try:
                with urllib.request.urlopen("http://127.0.0.1:8767/api/usage", timeout=3) as r:
                    return self.send(json.loads(r.read()))
            except Exception:
                return self.send({"credits_balance": 0, "credits_lifetime": 0,
                                  "credits_spent": 0, "clips_saved": 0,
                                  "app_running": False})
        if path == "/api/payment/checkout":
            # Returns the hosted Stripe checkout URL for a plan. Never accepts
            # or stores card data here — Stripe handles all of that securely.
            # Requires a signed-in account so Stripe can attach the customer
            # email and remember the payment method for future checkouts.
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"ok": False,
                                  "error": "Sign in before checking out."}, 401)
            if not CFG.get("payments_enabled"):
                return self.send({"ok": False, "error": "Payments are not enabled yet."}, 403)
            pid = str(b.get("plan", ""))
            url = CFG.get("checkout", {}).get(pid, "")
            if not url:
                return self.send({"ok": False, "error": "No checkout link configured for that plan."}, 404)
            # Attach customer email to Stripe's prefilled checkout (client_reference_id
            # + prefilled_email) so the card is saved to THIS account.
            email = p.get("email", "")
            if email:
                sep = "&" if "?" in url else "?"
                url += sep + urllib.parse.urlencode({
                    "prefilled_email": email,
                    "client_reference_id": email,
                })
            return self.send({"ok": True, "url": url, "email": email})
        if path == "/api/auth/google/start":
            cid = CFG.get("google_client_id", "")
            if not cid:
                return self.send({"error": "Google sign-in is not configured yet."}, 400)
            redirect = CFG["public_url"].rstrip("/") + "/api/auth/google/callback"
            url = ("https://accounts.google.com/o/oauth2/v2/auth?"
                   "response_type=code&scope=openid%20email%20profile&"
                   f"client_id={urllib.parse.quote(cid)}&redirect_uri="
                   f"{urllib.parse.quote(redirect)}&access_type=offline&prompt=consent")
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            return
        if path == "/api/auth/google/callback":
            return self.google_callback()
        if path in ("/", "/index.html"):
            return self.file("index.html", "text/html; charset=utf-8")
        if path == "/signup":
            return self.file("signup.html", "text/html; charset=utf-8")
        if path == "/dashboard":
            return self.file("dashboard.html", "text/html; charset=utf-8")
        if path == "/pricing":
            return self.file("pricing.html", "text/html; charset=utf-8")
        if path in ("/terms", "/privacy", "/disclaimer"):
            return self.file(path.lstrip("/") + ".html", "text/html; charset=utf-8")
        ct = {".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml",
              ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon",
              ".woff2": "font/woff2"}.get(
            Path(path).suffix, "text/plain")
        return self.file(path, ct + "; charset=utf-8" if ct.startswith("text") or "javascript" in ct else ct)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        b = self.body()

        if path == "/api/payment/checkout":
            # Hosted Stripe checkout — same contract as GET but via POST so the
            # desktop app can proxy it same-origin. Card data never touches us.
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"ok": False, "error": "Sign in before checking out."}, 401)
            if not CFG.get("payments_enabled"):
                return self.send({"ok": False, "error": "Payments are not enabled yet."}, 403)
            pid = str(b.get("plan", ""))
            url = CFG.get("checkout", {}).get(pid, "")
            if not url:
                return self.send({"ok": False, "error": "No checkout link configured for that plan."}, 404)
            email = p.get("email", "")
            if email:
                sep = "&" if "?" in url else "?"
                url += sep + urllib.parse.urlencode({
                    "prefilled_email": email, "client_reference_id": email})
            return self.send({"ok": True, "url": url, "email": email})

        if path == "/api/credits/authorize":
            ip = self.client_address[0] if self.client_address else "?"
            if not rate_ok(f"authorize:{ip}", 40, 60):
                return self.send({"ok": False, "error": "Too many requests. Slow down."}, 429)
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"ok": False, "error": "Sign in required."}, 401)
            u = load_users().get(p.get("email", ""), {})
            if not device_valid(p, u):
                return self.send({"ok": False, "error": "Signed in on another device. Sign in again here."}, 403)
            email = p.get("email", "")
            ok, msg, info = ledger.authorize(
                email, int(b.get("cost", 1) or 1), str(b.get("job_id", "") or uuid.uuid4().hex),
                str(b.get("reason", "url clip"))[:60])
            return self.send({"ok": ok, "error": "" if ok else msg, "message": msg, **info}, 200 if ok else 402)
        if path == "/api/credits/finalize":
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"ok": False}, 401)
            return self.send(ledger.finalize(p.get("email", ""), str(b.get("job_id", ""))))
        if path == "/api/credits/abort":
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"ok": False}, 401)
            return self.send(ledger.abort(p.get("email", ""), str(b.get("job_id", ""))))
        if path == "/api/credits/status":
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"ok": False, "error": "Sign in required."}, 401)
            u = load_users().get(p.get("email", ""), {})
            return self.send(ledger.snapshot(p.get("email", ""),
                                             u.get("tier", "trial"), u.get("expires", 0)))
        if path == "/api/signup":
            ip = self.client_address[0] if self.client_address else "?"
            if not rate_ok(f"signup:{ip}", 8, 60):
                return self.send({"ok": False, "error": "Too many accounts created. Try again later."}, 429)
            email = str(b.get("email", "")).strip().lower()
            pw = str(b.get("password", ""))
            name = str(b.get("name", "")).strip()
            users = load_users()
            if "@" not in email or len(pw) < 6:
                return self.send({"ok": False, "error": "Enter a valid email and a password of at least 6 characters."}, 400)
            if email in users:
                return self.send({"ok": False, "error": "An account with that email already exists."}, 400)
            users[email] = {
                "email": email, "name": name or email.split("@")[0],
                "pass": hash_password(pw), "tier": "trial", "expires": 0,
                "created": time.time(), "order": "",
            }
            save_users(users)
            return self.send({"ok": True, "token": token_for(users[email]), "name": users[email]["name"]})

        if path == "/api/auth/login":
            ip = self.client_address[0] if self.client_address else "?"
            if not rate_ok(f"login:{ip}", 12, 60):
                return self.send({"ok": False, "error": "Too many sign-in attempts. Try again in a minute."}, 429)
            email = str(b.get("email", "")).strip().lower()
            pw = str(b.get("password", ""))
            device_id = str(b.get("device_id", "")).strip()[:64]
            users = load_users()
            u = users.get(email)
            if not u or not check_password(pw, u.get("pass", "")):
                return self.send({"ok": False, "error": "Email or password is incorrect."}, 401)
            u = users[email]
            # Device binding: a fresh login MOVES the account to this device
            # and kicks the old session (per the spec). Embed the device id in
            # the token so every credit call can be checked against it.
            if device_id:
                u["active_device_id"] = device_id
                u["bound_at"] = time.time()
                save_users(users)
            return self.send({"token": token_for(u, device_id), "tier": u.get("tier", "trial"),
                              "expires": u.get("expires", 0), "name": u.get("name", "")})

        if path == "/api/auth/refresh":
            # Re-issue a token reflecting the CURRENT tier — the app calls this
            # after an upgrade so a cached trial token doesn't stay stale.
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"ok": False, "error": "invalid token"}, 401)
            u = load_users().get(p.get("email", ""), {})
            if not u:
                return self.send({"ok": False, "error": "account not found"}, 404)
            return self.send({"ok": True, "token": token_for(u), "tier": u.get("tier", "trial"),
                              "expires": u.get("expires", 0), "name": u.get("name", "")})

        if path == "/api/auth/status":
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"valid": False, "reason": "invalid token"})
            users = load_users()
            u = users.get(p.get("email", ""))
            if not u:
                return self.send({"valid": False, "reason": "account not found"})
            tier = u.get("tier", "trial")
            exp = float(u.get("expires", 0))
            paid = tier in ("pro", "team", "lifetime") and (exp == 0 or exp > time.time())
            # valid = authenticated session (free users are valid too!).
            # paid  = active paid subscription (controls watermark / pro features).
            return self.send({"valid": True, "paid": paid, "tier": tier, "expires": exp,
                              "reason": "ok" if paid else "free account",
                              "name": u.get("name", "")})

        if path == "/api/account":
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"error": "not signed in"}, 401)
            u = load_users().get(p.get("email", ""), {})
            return self.send({"email": u.get("email"), "name": u.get("name"),
                              "tier": u.get("tier"), "expires": u.get("expires", 0),
                              "order": u.get("order", ""),
                              # Saved payment method is stored by Stripe; we keep
                              # only a display label (e.g. "Visa •••• 4242").
                              "payment_method": u.get("payment_method", "")})

        if path == "/api/account/history":
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"error": "not signed in"}, 401)
            return self.send({"events": ledger.history(p.get("email", ""))})

        if path == "/api/auth/webhook":
            # Payment provider calls this. Two auth paths:
            #   1. Stripe signature header (production, when webhook_secret set)
            #   2. shared webhook_key in body (manual tests / simple providers)
            payload_raw = self.raw_request_body()
            try:
                sig_hdr = str(self.headers.get("Stripe-Signature", ""))
                secret = str(CFG.get("stripe_webhook_secret", ""))
                if sig_hdr and secret:
                    # Verify Stripe's t=...,v1=... HMAC-SHA256 over the RAW body.
                    parts = dict(p.split("=", 1) for p in sig_hdr.split(",") if "=" in p)
                    ts, v1 = parts.get("t", ""), parts.get("v1", "")
                    signed = (ts + "." + payload_raw.decode("utf-8", "ignore")).encode()
                    expect = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
                    if not (ts.isdigit() and abs(time.time() - int(ts)) < 300 and
                            hmac.compare_digest(expect, v1)):
                        return self.send({"ok": False, "error": "bad signature"}, 401)
                    try: b = json.loads(payload_raw)
                    except Exception: return self.send({"ok": False, "error": "bad body"}, 400)
                else:
                    key = str(b.get("webhook_key", ""))
                    if not hmac.compare_digest(key, CFG.get("webhook_key", "")):
                        return self.send({"ok": False, "error": "unauthorized"}, 401)
            except Exception:
                return self.send({"ok": False, "error": "unauthorized"}, 401)
            email = str(b.get("email", "")).strip().lower()
            users = load_users()
            if email not in users:
                return self.send({"ok": False, "error": "unknown account"}, 404)
            users[email]["tier"] = str(b.get("tier", "pro"))
            users[email]["expires"] = time.time() + float(b.get("expires_days", 30)) * 86400
            users[email]["order"] = str(b.get("order_id", ""))
            if b.get("payment_method"):
                users[email]["payment_method"] = str(b.get("payment_method", ""))[:60]
            save_users(users)
            ledger.set_tier(email, users[email]["tier"], users[email]["expires"])
            return self.send({"ok": True})

        if path == "/api/auth/google/complete":
            # Called by the app after the browser flow with ?token=... (pasted or deep link)
            p = verify_token(str(b.get("token", "")))
            if not p:
                return self.send({"valid": False})
            return self.send({"valid": True, "tier": p.get("tier", "trial")})

        return self.send({"error": "not found"}, 404)

    def google_callback(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (q.get("code") or [""])[0]
        if not code:
            return self.send({"error": "no code from Google"}, 400)
        cid, sec = CFG.get("google_client_id", ""), CFG.get("google_client_secret", "")
        redirect = CFG["public_url"].rstrip("/") + "/api/auth/google/callback"
        try:
            data = urllib.parse.urlencode({
                "code": code, "client_id": cid, "client_secret": sec,
                "redirect_uri": redirect, "grant_type": "authorization_code",
            }).encode()
            req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
            tok = json.loads(urllib.request.urlopen(req, timeout=15).read())
            idtok = tok.get("id_token", "")
            payload = json.loads(b64d(idtok.split(".")[1]))
            email = payload.get("email", "").lower()
            users = load_users()
            if email and email not in users:
                users[email] = {"email": email, "name": payload.get("name", ""),
                                "pass": "", "tier": "trial", "expires": 0,
                                "created": time.time(), "order": ""}
                save_users(users)
            if not email or email not in users:
                raise ValueError("no email from Google")
            token = token_for(users[email])
            html = f"""<!doctype html><html><head><meta charset='utf-8'><title>Signed in</title></head>
            <body style='font-family:system-ui;background:#0b0c14;color:#fff;padding:40px;max-width:600px;margin:auto'>
            <h2>You're signed in, {users[email].get('name','')}.</h2>
            <p>Copy this sign-in token and paste it into GhostClipper:</p>
            <textarea id='t' rows='4' style='width:100%;padding:10px;border-radius:8px'>{token}</textarea>
            <button onclick='navigator.clipboard.writeText(document.getElementById("t").value);this.textContent="Copied!"'
              style='padding:10px 16px;margin-top:10px;border-radius:8px;border:0;background:#8b6cff;color:#fff;cursor:pointer'>Copy token</button>
            </body></html>"""
            self.send(html, 200, "text/html; charset=utf-8")
        except Exception as e:
            self.send({"error": "Google sign-in failed: " + str(e)}, 400)


def main():
    # Host binds 0.0.0.0 by default so Render/Fly can reach it; PORT comes from
    # the environment on managed hosts. Locally it still defaults to 8899.
    port = int(os.environ.get("PORT", CFG.get("port", 8899)))
    host = os.environ.get("HOST", "0.0.0.0")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"GhostClipper site on http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
