# GhostClipper — Launch Runbook

Everything code-side is ready. This is the exact order to go live. Nothing
here needs development work except pasting values into `config.json` / env vars.

---

## 0. Before you start (one-time secrets)

Generate two random secrets (keep them private; they are the account-signing
and payment-webhook keys):

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(48))"   # TOKEN_SECRET
python3 -c "import secrets;print(secrets.token_urlsafe(48))"   # WEBHOOK_KEY
```

**IMPORTANT:** the desktop app's `core/auth.py:TOKEN_SECRET` must match
`TOKEN_SECRET`. If you change it, rebuild both apps (`bash build_two_apps.sh`).
On a managed host, set env vars `TOKEN_SECRET` / `WEBHOOK_KEY` — they override
`config.json` so local dev secrets never ship.

---

## 1. Public hosting + domain

You have two ready-to-use deploy files in this folder:

- **Render** — `render.yaml` (connect the repo, it builds via `Dockerfile`)
- **Fly.io** — `fly.toml` (`fly launch` then `fly deploy`)

Steps:
1. Buy `ghostclipper.app`.
2. Deploy the folder. The app listens on `PORT` (default 8080 in Docker).
3. Set env `PUBLIC_URL=https://ghostclipper.app` (or your Render/Fly URL first).
4. Point the domain DNS at the host; confirm HTTPS works.

> **Persistence warning:** `data/users.json` + `data/credits.json` are files
> inside the container. On a free tier they can reset on redeploy. Before
> taking money, add a persistent disk (Render) or a volume (Fly), or migrate
> to a managed Postgres. Until then, treat accounts as disposable.

Verify: `curl https://YOUR_URL/api/health` → `{"ok":true,...}`

---

## 2. Google sign-in verification

1. Google Cloud Console → your OAuth client.
2. Authorized redirect URI: `https://YOUR_URL/api/auth/google/callback`
3. Add test users while verification is pending (otherwise `403 access_denied`).
4. Set env `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (or config.json).

Verify: open `https://YOUR_URL/api/auth/google/start` → 302 to Google.

---

## 3. Stripe (activates payments + saved cards)

1. Stripe account → **test mode** first.
2. Create 3 **payment links**: Starter $4.99, Creator $12.99, Studio $19.99.
3. Create a **billing portal** configuration (saved cards / invoices).
4. Webhook → endpoint URL `https://YOUR_URL/api/auth/webhook`.
   - Events: `checkout.session.completed`, `customer.subscription.deleted`.
   - Signing secret → env `STRIPE_WEBHOOK_SECRET`.
5. In `config.json` (or env):
   - `payments_enabled: true`
   - `checkout.starter/creator/studio` = the payment link URLs
   - `billing_portal_url` = the portal URL
6. Send a test `checkout.session.completed` event; confirm the account's tier
   flips to `pro` and credits become 200.

> The webhook accepts Stripe's signed `Stripe-Signature` header when
> `stripe_webhook_secret` is set; otherwise it falls back to the shared
> `webhook_key` in the JSON body. Card data never touches our server.

---

## 4. App updates (forced-update link)

1. Host the two `.app` bundles (e.g. a release page or CDN).
2. Set `update_url` in `config.json` to the download page.
3. When you ship a new version, bump `min_app_version` here AND the app's
   version constant; outdated apps then show the update screen with the link.

---

## 5. Rebuild the desktop apps with production secrets (if TOKEN_SECRET changed)

```bash
cd ~/Desktop/GhostClipper
bash build_two_apps.sh
# then quit and relaunch both apps (kill old processes first — single-instance guard)
```

Users need the NEW customer app build (`GhostClipper.app`) — the dev build is
yours only (unlimited credits, no watermark).

---

## 6. Pre-launch verification (run on the PUBLIC url)

- [ ] `audit_website.py` → 31/31
- [ ] Sign up on the live site → sign in on the live site → sign out → sign in again
- [ ] Sign in inside the desktop app → clip a YouTube link → credits drop by
      1/minute, watermark appears on the free clip
- [ ] Upgrade via Stripe test webhook → sign in again → 200 credits, no watermark
- [ ] Log in from a second Mac → first Mac's credits calls return 403 (device binding)
- [ ] Locked endpoints (YouTube, Auto) return 403 on the customer app
- [ ] Legal pages load: `/terms`, `/privacy`, `/disclaimer`
- [ ] Force an update: bump `min_app_version`, confirm the app blocks with the link

---

## 7. Go live

- [ ] Switch Stripe from **test** to **live** keys + payment links + webhook
- [ ] Final audit against production
- [ ] Announce

---

## What is already done (no action needed)

Server-authoritative credits · device binding · rate limiting · offline gate ·
forced updates · dev/customer builds · smart clipping (audio + transcript +
visual; 30/70 facecam layout) · watermark · in-app sign-in/checkout ·
dashboards · pricing page · legal pages · deploy files · 31/31 audit.
