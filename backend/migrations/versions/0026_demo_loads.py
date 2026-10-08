"""Seed ~25 demo loads so /loads is useful before the scraper lands.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-08

Idempotent: only inserts when the LJM tenant has zero `loads` rows, so a
re-run never duplicates. Every demo row carries `source='demo'` and
`raw.demo=True` — the ``/loads`` page shows a "Demo" chip and the purge
path (``DELETE FROM loads WHERE source='demo'`` or `/loads/demo` DELETE
endpoint) wipes them cleanly once real intake flows.

Brokers tie back to existing ``leads`` rows by (name, state) where possible;
new broker names are left unlinked (``broker_lead_id = NULL``) — the next
inbox / scraper run will link them via the standard email → phone →
(name, origin_state) ladder in ``loads_service._resolve_broker_lead``.

Lanes are Eastern-US; equipment van/reefer/flatbed/stepdeck; pickup dates
spread across the next 7 days; a few lanes repeat across two "sources"
(demo + demo2) so the dedupe view and multi-source badges exercise.
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LJM_TENANT_ID = "01LJMORGLJM00000000000000A"


def _norm(v: object) -> str:
    return str(v or "").strip().lower()


def _group_hash(broker: str, o_state: str, d_state: str, pickup: str, equipment: str) -> str:
    key = "|".join([_norm(broker), _norm(o_state), _norm(d_state), pickup[:10], _norm(equipment)])
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# 25 lanes — some duplicated across two sources so the dedupe view fires.
_BROKERS = [
    ("Blue Ridge Logistics", "VA", "804-555-0121", "dispatch@blueridgelog.com", "MC-412881"),
    ("Keystone Freight Group", "PA", "717-555-0144", "ops@keystonefg.com", "MC-556702"),
    ("Peach State Haulers", "GA", "404-555-0188", "loads@peachstateh.com", "MC-311903"),
    ("Great Lakes Express", "OH", "216-555-0199", "team@glexpress.com", "MC-220514"),
    ("Carolina Dispatch Co", "NC", "704-555-0100", None, "MC-980211"),
    ("Hudson Valley Reefer", "NY", "518-555-0177", "reefers@hvreefer.com", "MC-108822"),
    ("Delta Flatbed Partners", "TN", "615-555-0144", "flat@deltafbp.com", "MC-662200"),
    ("Appalachian Logistics", "WV", "304-555-0166", "ops@applog.com", "MC-770011"),
]

_LANES = [
    # (origin_city, origin_state, dest_city, dest_state, equipment, rate_usd, miles)
    ("Richmond", "VA", "Atlanta", "GA", "van", 1850, 525),
    ("Harrisburg", "PA", "Charlotte", "NC", "van", 1700, 540),
    ("Columbus", "OH", "Nashville", "TN", "reefer", 2300, 420),
    ("Buffalo", "NY", "Jacksonville", "FL", "reefer", 3100, 1150),
    ("Pittsburgh", "PA", "Memphis", "TN", "flatbed", 2050, 720),
    ("Charlotte", "NC", "Boston", "MA", "van", 2400, 870),
    ("Savannah", "GA", "Baltimore", "MD", "reefer", 2200, 720),
    ("Knoxville", "TN", "Cleveland", "OH", "stepdeck", 1950, 470),
    ("Charleston", "WV", "Philadelphia", "PA", "flatbed", 1600, 490),
    ("Albany", "NY", "Charlotte", "NC", "van", 2100, 790),
    ("Greensboro", "NC", "Pittsburgh", "PA", "van", 1550, 460),
    ("Raleigh", "NC", "Boston", "MA", "reefer", 2600, 780),
    ("Atlanta", "GA", "Richmond", "VA", "van", 1800, 530),
    ("Nashville", "TN", "Columbus", "OH", "reefer", 2250, 410),
    ("Jacksonville", "FL", "Buffalo", "NY", "reefer", 3050, 1160),
    ("Memphis", "TN", "Pittsburgh", "PA", "flatbed", 2100, 730),
    ("Boston", "MA", "Charlotte", "NC", "van", 2500, 865),
    ("Baltimore", "MD", "Savannah", "GA", "reefer", 2150, 710),
    ("Cleveland", "OH", "Knoxville", "TN", "stepdeck", 1900, 475),
    ("Philadelphia", "PA", "Charleston", "WV", "flatbed", 1650, 495),
    # three duplicate lanes to exercise dedupe + multi-source badges
    ("Richmond", "VA", "Atlanta", "GA", "van", 1900, 525),       # dup of #0
    ("Columbus", "OH", "Nashville", "TN", "reefer", 2250, 420),  # dup of #2
    ("Charlotte", "NC", "Boston", "MA", "van", 2350, 870),       # dup of #5
    ("Raleigh", "NC", "Boston", "MA", "reefer", 2550, 780),      # dup of #11
    ("Atlanta", "GA", "Richmond", "VA", "van", 1750, 530),       # dup of #12
]

_STATUSES = ("new", "new", "new", "new", "contacted")  # mostly new


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT COUNT(*) FROM loads WHERE tenant_id = :t"),
        {"t": LJM_TENANT_ID},
    ).scalar() or 0
    if existing:
        return

    rng = random.Random(20261008)
    now = datetime.now(timezone.utc)

    # Preload leads so broker_lead_id can be attached when the demo broker's
    # name+state matches an existing row. Pure best-effort; missing matches
    # leave NULL — the real crawler fills them in later.
    lead_lookup: dict[tuple[str, str], str] = {}
    try:
        rows = bind.execute(
            sa.text("SELECT id, name, state FROM leads WHERE tenant_id = :t"),
            {"t": LJM_TENANT_ID},
        ).fetchall()
        for r in rows:
            lead_lookup[(_norm(r[1]), _norm(r[2]))] = r[0]
    except Exception:
        lead_lookup = {}

    insert_sql = sa.text(
        "INSERT INTO loads ("
        "  tenant_id, source, source_ref, broker_name, broker_email, broker_phone,"
        "  origin_city, origin_state, dest_city, dest_state, equipment, rate_usd, miles,"
        "  pickup_date, posted_at, raw, status, status_at, broker_lead_id, dedupe_group_hash"
        ") VALUES ("
        "  :tenant_id, :source, :source_ref, :broker_name, :broker_email, :broker_phone,"
        "  :origin_city, :origin_state, :dest_city, :dest_state, :equipment, :rate_usd, :miles,"
        "  :pickup_date, :posted_at, CAST(:raw AS JSON), :status, :status_at, :broker_lead_id, :dedupe_group_hash"
        ")"
    )

    for i, lane in enumerate(_LANES):
        o_city, o_state, d_city, d_state, equip, rate, miles = lane
        broker = _BROKERS[i % len(_BROKERS)]
        broker_name, broker_state, phone, email, mc = broker
        pickup_day = now + timedelta(days=rng.randint(0, 6), hours=rng.randint(8, 18))
        posted = now - timedelta(hours=rng.randint(1, 48))
        status = _STATUSES[i % len(_STATUSES)]
        # Last five lanes are duplicates: write them under source='demo2'
        source = "demo2" if i >= 20 else "demo"
        gh = _group_hash(broker_name, o_state, d_state, pickup_day.isoformat(), equip)
        lead_id = lead_lookup.get((_norm(broker_name), _norm(broker_state)))
        bind.execute(
            insert_sql,
            {
                "tenant_id": LJM_TENANT_ID,
                "source": source,
                "source_ref": f"demo:{secrets.token_hex(6)}",
                "broker_name": broker_name,
                "broker_email": email,
                "broker_phone": phone,
                "origin_city": o_city,
                "origin_state": o_state,
                "dest_city": d_city,
                "dest_state": d_state,
                "equipment": equip,
                "rate_usd": rate,
                "miles": miles,
                "pickup_date": pickup_day,
                "posted_at": posted,
                "raw": json.dumps({"demo": True, "mc": mc}),
                "status": status,
                "status_at": None,
                "broker_lead_id": lead_id,
                "dedupe_group_hash": gh,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM loads WHERE tenant_id = :t AND source IN ('demo','demo2')"),
        {"t": LJM_TENANT_ID},
    )
