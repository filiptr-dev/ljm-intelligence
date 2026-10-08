"""Backfill loads.broker_lead_id for unlinked loads

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-08

Link-loads-to-brokers plan. One-shot, deterministic: for every load with
``broker_lead_id IS NULL`` apply the same ladder as
``app.prospecting.broker_matching`` (exact email -> non-freemail domain ->
normalized name -> phone; ambiguous = skip). Matching is inlined on purpose:
migrations must not import app code. Never overwrites an existing link, so it
is idempotent. Downgrade is a no-op (links are data, not schema).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FREE = frozenset(
    "gmail.com googlemail.com yahoo.com yahoo.co.uk hotmail.com outlook.com live.com "
    "msn.com aol.com icloud.com me.com mac.com protonmail.com proton.me".split()
)
_SUFFIXES = frozenset({"llc", "inc", "corp", "co", "ltd"})


def _norm(s: str | None) -> str:
    if not s:
        return ""
    t = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return " ".join(w for w in t.split() if w not in _SUFFIXES)


def backfill(bind) -> int:
    leads = bind.execute(sa.text("SELECT id, name, domain, primary_email, phone FROM leads")).fetchall()
    by_email, by_domain, by_name, by_phone = (defaultdict(list) for _ in range(4))
    for lid, name, domain, email, phone in leads:
        if email:
            by_email[email.strip().lower()].append(lid)
        if domain:
            by_domain[domain.strip().lower()].append(lid)
        if _norm(name):
            by_name[_norm(name)].append(lid)
        if phone:
            by_phone[phone].append(lid)

    def one(bucket: dict, key) -> str | None:
        hit = bucket.get(key) if key else None
        return hit[0] if hit and len(hit) == 1 else None

    rows = bind.execute(
        sa.text("SELECT id, broker_email, broker_phone, broker_name FROM loads WHERE broker_lead_id IS NULL")
    ).fetchall()
    n = 0
    for load_id, email, phone, name in rows:
        e = email.strip().lower() if email else ""
        dom = e.rsplit("@", 1)[1] if "@" in e else ""
        if dom in _FREE:
            dom = ""
        lid = one(by_email, e) or one(by_domain, dom) or one(by_name, _norm(name)) or one(by_phone, phone)
        if lid:
            bind.execute(
                sa.text("UPDATE loads SET broker_lead_id = :l WHERE id = :i AND broker_lead_id IS NULL"),
                {"l": lid, "i": load_id},
            )
            n += 1
    return n


def upgrade() -> None:
    backfill(op.get_bind())


def downgrade() -> None:
    pass
