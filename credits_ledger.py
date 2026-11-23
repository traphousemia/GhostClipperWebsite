"""
Server-authoritative credit ledger. Only this process may change balances.

- Balances stored server-side in data/credits.json (never sent to clients).
- authorize() atomically reserves credits under a lock with an idempotency key.
- finalize() confirms the reservation after rendering; abort() refunds on failure.
- All decisions are recorded in an append-only audit log.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DATA = Path(__file__).parent / "data"
LEDGER = DATA / "credits.json"
AUDIT = DATA / "credit_audit.jsonl"
_LOCK = threading.RLock()

FREE_CREDITS = 60
TIER_CREDITS = {"trial": 60, "pro": 200, "team": 1000, "lifetime": 1000000}


def _load() -> dict:
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text())
        except Exception:
            pass
    return {}


def _save(d: dict):
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.replace(tmp, LEDGER)  # atomic rename


def _log(entry: dict):
    with open(AUDIT, "a") as f:
        f.write(json.dumps({"t": time.time(), **entry}) + "\n")


def account(email: str) -> dict:
    """Get or lazily create a ledger row for an account."""
    with _LOCK:
        d = _load()
        row = d.get(email)
        if not row:
            row = {"balance": FREE_CREDITS, "month": _month(), "reserved": {},
                   "total_spent": 0}
            d[email] = row
            _save(d)
        if row.get("month") != _month():  # monthly reset to tier allowance
            tier = row.get("tier", "trial")
            row["balance"] = TIER_CREDITS.get(tier, FREE_CREDITS)
            row["month"] = _month()
            row["reserved"] = {}
            _save(d)
        return row


def _month() -> str:
    t = time.localtime()
    return f"{t.tm_year}-{t.tm_mon:02d}"


def set_tier(email: str, tier: str, expires: float = 0):
    """Called from the payment webhook — updates allowance metadata."""
    with _LOCK:
        d = _load()
        row = d.setdefault(email, {"balance": FREE_CREDITS, "reserved": {},
                                   "total_spent": 0, "month": _month()})
        row["tier"] = tier
        row["expires"] = expires
        if tier in ("pro", "team", "lifetime") and row.get("month") != _month():
            row["balance"] = TIER_CREDITS[tier]
        _save(d)
        _log({"email": email, "event": "tier", "tier": tier})


def authorize(email: str, cost: int, job_id: str, reason: str = "url clip") -> tuple[bool, str, dict]:
    """Atomically reserve `cost` credits if available. Idempotent on job_id."""
    cost = max(1, int(cost))
    with _LOCK:
        d = _load()
        row = d.setdefault(email, {"balance": FREE_CREDITS, "reserved": {},
                                   "total_spent": 0, "month": _month()})
        if row.get("month") != _month():
            row["balance"] = TIER_CREDITS.get(row.get("tier", "trial"), FREE_CREDITS)
            row["month"] = _month()
            row["reserved"] = {}
        reserved = row.setdefault("reserved", {})
        if job_id in reserved:  # retry / replay: return SAME decision, no new charge
            remaining = int(row.get("balance", 0))
            return True, "already authorized", {"balance": remaining,
                                                 "reserved": reserved[job_id], "replay": True}
        if int(row.get("balance", 0)) < cost:
            _log({"email": email, "event": "denied", "cost": cost, "reason": reason})
            return False, "Not enough credits.", {"balance": row.get("balance", 0)}
        row["balance"] = int(row["balance"]) - cost
        reserved[job_id] = cost
        row["total_spent"] = int(row.get("total_spent", 0)) + cost
        _save(d)
        _log({"email": email, "event": "authorize", "cost": cost, "job_id": job_id,
              "reason": reason, "balance": row["balance"]})
        return True, "authorized", {"balance": row["balance"], "reserved": cost}


def finalize(email: str, job_id: str) -> dict:
    """Commit a reservation (called after successful render)."""
    with _LOCK:
        d = _load()
        row = d.get(email, {})
        reserved = row.get("reserved", {})
        if job_id in reserved:
            del reserved[job_id]
            row["total_clips"] = int(row.get("total_clips", 0)) + 1
            _save(d)
            _log({"email": email, "event": "finalize", "job_id": job_id,
                  "total_clips": row["total_clips"]})
        return {"ok": True, "balance": row.get("balance", 0)}


def abort(email: str, job_id: str) -> dict:
    """Refund a reservation if rendering failed."""
    with _LOCK:
        d = _load()
        row = d.get(email, {})
        reserved = row.get("reserved", {})
        cost = reserved.pop(job_id, 0)
        if cost:
            row["balance"] = int(row.get("balance", 0)) + cost
            row["total_spent"] = max(0, int(row.get("total_spent", 0)) - cost)
            _save(d)
            _log({"email": email, "event": "abort", "job_id": job_id, "refund": cost})
        return {"ok": True, "balance": row.get("balance", 0)}


def history(email: str, limit: int = 25) -> list[dict]:
    """Recent account activity from the append-only audit log (newest first)."""
    out = []
    try:
        if AUDIT.exists():
            for line in AUDIT.read_text().splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("email") == email:
                    out.append({k: v for k, v in e.items() if k != "email"})
    except Exception:
        pass
    return list(reversed(out))[:max(1, min(100, limit))]


def snapshot(email: str, tier: str = "trial", expires: float = 0) -> dict:
    with _LOCK:
        row = account(email)
        return {"ok": True, "balance": row.get("balance", 0),
                "lifetime": TIER_CREDITS.get(tier, FREE_CREDITS),
                "spent": row.get("total_spent", 0),
                "total_clips": row.get("total_clips", 0),
                "tier": tier,
                "watermark": tier not in ("pro", "team", "lifetime"),
                "per_dollar": 30,
                "unit": "1 credit = 1 minute of uploaded video"}
