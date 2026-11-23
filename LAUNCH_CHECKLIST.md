# GhostClipper — LAUNCH CHECKLIST

Everything in the app and website is built, linked, and payment-ready.
This is the list of what YOU need to provide/do to actually launch.

────────────────────────────────────────────
## 1. WEBSITE HOSTING + DOMAIN  ← DO THIS FIRST
────────────────────────────────────────────
Right now the site only runs on your Mac at http://127.0.0.1:8899.
Customers can't reach it. You need:
  [ ] A domain (e.g. ghostclipper.com)
  [ ] Hosting for the Python website (Render, Fly.io, or a small VPS)
  [ ] HTTPS on that domain
  [ ] Give me the public URL — I'll switch the app + Google + Stripe to it

────────────────────────────────────────────
## 2. GOOGLE SIGN-IN (OAuth)
────────────────────────────────────────────
Google code is built. Needed from Google Console:
  [ ] In your OAuth client, add Authorized redirect URI:
        https://YOUR-DOMAIN/api/auth/google/callback
      (keep http://127.0.0.1:8899/api/auth/google/callback for local dev)
  [ ] Complete Google verification (OAuth consent screen)
      — until approved, only added Test users can sign in
  [ ] Add your own email (getmoneyboyzx@gmail.com) as a Test user

────────────────────────────────────────────
## 3. STRIPE (PAYMENTS + SAVED CARDS)
────────────────────────────────────────────
Payment UI is ready in BOTH the app and website; it lights up automatically
once you add these to GhostClipperWebsite/config.json:
  [ ] Create a Stripe account (use Test mode first)
  [ ] Stripe publishable key  → "stripe_publishable_key"
  [ ] Stripe secret key       → "stripe_secret_key"
  [ ] Create 3 recurring products/prices ($4.99 / $12.99 / $19.99)
  [ ] Create 3 payment links (or checkout links) → "checkout": {starter, creator, studio}
  [ ] Create a Customer Portal configuration → "billing_portal_url"
  [ ] Set "webhook_key" to a strong random secret (change from default!)
  [ ] Create a Stripe webhook pointing to:
        https://YOUR-DOMAIN/api/auth/webhook
      sending event: checkout.session.completed (and invoice.paid for renewals)
  [ ] Set "payments_enabled": true
  [ ] Test with Stripe test card 4242 4242 4242 4242 → confirm app unlocks

The card is stored by STRIPE, never in the app or your server. Users get
one-tap "pay with Visa •••• 4242" and automatic renewals.

────────────────────────────────────────────
## 4. PRICING SCOPE (current decision)
────────────────────────────────────────────
  [ ] Only URL Clipping is sold for now (customer app is in url_only mode)
  [ ] YouTube + Auto Clipping stay locked until you flip the mode later
  [ ] Prices: Starter $4.99 / 200 credits, Creator $12.99 / 600, Studio $19.99 / 1000
      (edit freely in config.json "plans")

────────────────────────────────────────────
## 5. SECURITY BEFORE REAL MONEY
────────────────────────────────────────────
  [ ] Replace "token_secret" in config.json AND app core/auth.py TOKEN_SECRET
      with the SAME strong random value (they must match)
  [ ] Replace "webhook_key" with a strong random secret
  [ ] Never commit/share config.json or google_client.json with secrets

────────────────────────────────────────────
## 6. FINAL UI POLISH (we'll do together)
────────────────────────────────────────────
  [ ] Clean up any spacing/colors you want on auth pages / dashboard / home
  [ ] Re-run audit: python3 ~/Desktop/GhostClipperWebsite/audit_website.py
  [ ] Final end-to-end: signup → pay (test) → app unlock → clip → download

────────────────────────────────────────────
## WHERE THINGS LIVE
────────────────────────────────────────────
App source:     ~/Desktop/GhostClipper/
Website source: ~/Desktop/GhostClipperWebsite/
  config.json  ← all launch keys/links go here (Stripe, Google, payments)
  server.py    ← website + auth + webhook
  site/        ← HTML/CSS pages
Rebuild apps:   bash ~/Desktop/GhostClipper/build_two_apps.sh
Re-run audit:   python3 ~/Desktop/GhostClipperWebsite/audit_website.py

Current verified state:
  • Site 8899, Dev app 8767, Customer app 8766 — all healthy
  • 31/31 website audit checks passed
  • In-app login ↔ website sync verified end-to-end
  • Usage dashboards (credits + clips saved) live in BOTH app and website
  • Payment buttons present but correctly dormant until Stripe is configured

────────────────────────────────────────────
## ANTI-HACK / CREDIT PROTECTION (DONE)
────────────────────────────────────────────
Server-authoritative credits — the customer app can NOT grant itself credits:
  • Credits ledger lives ONLY on the website (data/credits.json, server-side)
  • Every clip job asks the server to authorize the cost (atomic, locked)
  • Local credits.json is display-cache only, never used for decisions
  • job_id is an idempotency key — replaying a request can't charge twice
  • Failed jobs refund the reservation (abort); success finalizes it
  • Overspend → HTTP 402; unsigned/fake token → 401
  • Customer app's local grant/refill/redeem/buy_tier endpoints are DISABLED
  • Dev-only unlimited flag is COMPILED INTO the dev binary (GC_DEV_UNLIMITED);
    the customer binary contains no such code path (verified via strings)
  • Watermark decision also comes from the server, not a local file
  • Audit log: data/credit_audit.jsonl records every authorize/finalize/abort

Verified:
  • Dev app: 999999 credits, no watermark, clips freely
  • Customer app signed out: clip job BLOCKED ("Sign in with your account")
  • Customer hack attempts (grant/refill/redeem/buy_tier): all rejected
  • Replay same job_id: no double charge; overspend: 402; fake token: 401

Still your job at launch (see section 5): rotate token_secret + webhook_key,
add Stripe signature verification, rate-limit the authorize endpoint.
Note: this protects your SERVER and paid features. A determined user can always
run ffmpeg themselves on a file they already have — that's unavoidable; credits
are account-level fair-use metering, not DRM over their own machine.

────────────────────────────────────────────
## WATERMARK (DONE)
────────────────────────────────────────────
Free-tier clips get an animated GhostClipper.app promo:
  • core/watermark.py renders a ghost that pops in (bounce) then the text
    "GhostClipper.app" unfolds letter-by-letter out of it, with a glow underline
  • core/editor.py composites it as a transparent ProRes overlay in a 2nd pass
  • Only applied when watermark=True (free tier); paid/dev clips are clean
  • Verified: real free clip had 24,145 bright watermark pixels in the
    center region vs 0 in a control region — promo is definitely burned in

LATEST AUDIT: 31/31 website checks passed; all 3 services healthy.

────────────────────────────────────────────
## COST ECONOMICS (how it actually flows)
────────────────────────────────────────────
Launch: $0 on free tiers (domain ~$12/yr is the only upfront cost).
Paying users cover all costs, but the money flows through YOUR account:
  • Stripe collects payment → ~2.9% + 30¢ fee → rest lands in YOUR bank
  • Hosting/database bills charge YOUR card monthly
  • Not an automatic pipe — you pay the bill, customers funded it
One Starter customer ($4.99) nets ~$4.54 after Stripe → covers hosting
from about 2-7 customers onward. At 100 customers (~$499/mo net),
a $25-30/mo backend is ~6% of revenue. Economics improve with scale.
Thresholds: free until ~100 users → always-on ~$25-30/mo at ~500 users
→ ~$50-100/mo at 2,000+ (pooling + log archive). No code rewrites.
Honest note: no truly $0 secure product exists — always-on checks need a
server. A cracked app is useless without server-issued credits, which is
the realistic anti-piracy goal.

────────────────────────────────────────────
## SAFETY GATES (DONE)
────────────────────────────────────────────
Offline gate: full-screen "Connect to the internet" screen appears whenever
the server cannot be reached; auto-dismisses on reconnect; re-checked every
30s. Server-side /api/vod/start also refuses to start when offline (503).
Forced updates: website config.json "min_app_version" and "maintenance" are
served live from disk at /api/app/config; app shows an "Update required"
screen and server refuses jobs with 426 when app is outdated. Dev build is
exempt (compiled-in flag). To force an update: bump min_app_version.
Creator/dev access is now ONLY from the compiled-in GHOSTCLIPPER_DEV flag —
a local "creator" file can no longer grant privileges. Verified: dev binary
contains the flag strings, customer binary contains none.
Customer entitlement: no local token verification, no offline grace — the
server verifies every session; offline/invalid = locked out.
Verified: offline gate shows/hides correctly; forced update blocks customer
(426) while dev keeps working; audit 31/31.
