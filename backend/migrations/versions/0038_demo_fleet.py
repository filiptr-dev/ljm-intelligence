"""Fleet (Kamioni): trucks + Inspectio-style records, nullable freight_runs.truck_id, demo seed.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-09

Five tenant-scoped tables (+ RLS, same policy shape as 0034): ``trucks`` (power
units *and* trailers via ``kind``), ``truck_inspections``, ``truck_defects``,
``truck_maintenance``, ``truck_documents``. Every row carries ``source`` so the
demo set can be told apart from a real vendor feed later.

``freight_runs.truck_id`` is **nullable** with ``ON DELETE SET NULL``: existing
rows and any future non-demo run are untouched, and a deleted truck never
deletes history.

Seed (LJM tenant, ``source='demo'``, deterministic ``random.Random(20261010)``
relative to the day the migration runs): 20 trucks + 8 trailers, inspections,
defects (>=2 trucks with an open *critical* defect), maintenance (>=3 jobs due
within 7 days) and documents (>=3 expiring within 14 days) so the alerts strip
has something true to say. Then the demo ``freight_runs`` are assigned to
trucks of the same equipment, never overlapping on one truck, so per-truck
revenue and cost-per-mile are real numbers on day one.

Idempotent: the seed is skipped when the LJM tenant already has a demo truck.
Downgrade purges the demo rows, unlinks the runs, drops the FK/column/tables.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LJM_TENANT_ID = "01LJMORGLJM00000000000000A"
_TABLES = ("trucks", "truck_inspections", "truck_defects", "truck_maintenance", "truck_documents")

# ---- seed vocabulary --------------------------------------------------------

_HOMES = [
    ("Chicago", "IL", 41.8781, -87.6298), ("Dallas", "TX", 32.7767, -96.7970),
    ("Atlanta", "GA", 33.7490, -84.3880), ("Columbus", "OH", 39.9612, -82.9988),
    ("Memphis", "TN", 35.1495, -90.0490), ("Phoenix", "AZ", 33.4484, -112.0740),
    ("Harrisburg", "PA", 40.2732, -76.8867), ("Charlotte", "NC", 35.2271, -80.8431),
]
_DRIVERS = [
    "Marcus Hale", "Dana Whitfield", "Luis Ortega", "Priya Nair", "Tom Kessler", "Andre Boyd", "Rachel Moon",
    "Viktor Petrov", "Sam Delgado", "Keisha Ward", "Owen Fraser", "Mia Castellano", "Jerome Pike", "Nina Volkov",
    "Carl Brandt", "Ana Ribeiro", "Hector Salas", "Beth Calloway", "Dmitri Orlov", "Grace Lindqvist",
]
_INSPECTORS = ["R. Whitaker", "J. Moreno", "S. Patel", "T. Lindgren"]
_TRUCK_MODELS = [
    ("Freightliner", "Cascadia"), ("Kenworth", "T680"), ("Peterbilt", "579"),
    ("Volvo", "VNL 860"), ("International", "LT"), ("Mack", "Anthem"),
]
_TRAILER_MODELS = {
    "van": ("Great Dane", "Champion 53'"), "reefer": ("Utility", "3000R 53'"),
    "flatbed": ("Fontaine", "Revolution 48'"), "stepdeck": ("Dorsey", "Stepdeck 48'"),
}
_DEFECTS = {
    "minor": [
        ("Marker light out", "Clearance light on the driver side is not working."),
        ("Cracked mud flap", "Rear mud flap is cracked and flapping."),
        ("Worn wiper blades", "Wipers smear in rain; replace both blades."),
        ("Cab door seal worn", "Wind noise at highway speed from the passenger door seal."),
    ],
    "major": [
        ("Air leak at brake chamber", "Audible leak at the rear axle brake chamber; pressure drops overnight."),
        ("Steer tyre tread low", "Front-right tyre is at about 4/32. Replace before the next long haul."),
        ("Coolant seep at water pump", "Small coolant weep, level drops about a litre a week."),
        ("ABS warning light on", "ABS lamp comes on intermittently above 40 mph."),
    ],
    "critical": [
        ("Brake line chafed through", "Brake hose is rubbing on the frame and is close to failing. Do not run."),
        ("Steering box leaking", "Power-steering fluid is leaking at the box and assist is fading. Do not run."),
    ],
}
_TRAILER_DEFECTS = {
    "minor": [("Marker light out", "Clearance light is not working."), ("Cracked mud flap", "Mud flap is cracked.")],
    "major": [("Air leak at brake chamber", "Leak at the axle brake chamber."), ("Landing gear stiff", "Landing gear is hard to crank.")],
}
_MAINT = {
    "service": ("Preventive maintenance service", (450, 1100)),
    "repair": ("Repair", (900, 4200)),
    "tyre": ("Tyre replacement", (1200, 2600)),
}


def _vin(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(17))


def generate_fleet(now: datetime) -> dict[str, list[dict[str, Any]]]:
    """Pure + deterministic: same ``now`` -> same fleet. Children carry ``unit_number``."""
    rng = random.Random(20261010)
    today = now.date()

    def days_ago(n: int, hour: int = 9) -> datetime:
        d = today - timedelta(days=n)
        return datetime(d.year, d.month, d.day, hour, 0, tzinfo=UTC)

    # --- units
    truck_eq = ["van"] * 10 + ["reefer"] * 6 + ["flatbed"] * 3 + ["stepdeck"]
    truck_status = ["available"] * 11 + ["on_load"] * 5 + ["in_shop"] * 3 + ["out_of_service"]
    rng.shuffle(truck_status)
    trailer_eq = ["van"] * 4 + ["reefer"] * 2 + ["flatbed", "stepdeck"]
    trailer_status = ["available"] * 5 + ["on_load"] * 2 + ["in_shop"]
    rng.shuffle(trailer_status)

    units: list[dict[str, Any]] = []
    drivers = _DRIVERS[:]
    rng.shuffle(drivers)
    for i, (eq, st) in enumerate(zip(truck_eq, truck_status, strict=True)):
        make, model = rng.choice(_TRUCK_MODELS)
        city, state, _, _ = rng.choice(_HOMES)
        units.append({
            "kind": "truck", "unit_number": f"T-{101 + i}", "vin": _vin(rng), "make": make, "model": model,
            "year": rng.randint(2017, 2025), "plate": f"{state}-{rng.randint(1000, 9999)}", "equipment": eq,
            "status": st, "odometer_miles": rng.randrange(110_000, 720_000, 100),
            "home_base_city": city, "home_base_state": state,
            "assigned_driver_name": None if st == "out_of_service" else drivers[i % len(drivers)],
        })
    for i, (eq, st) in enumerate(zip(trailer_eq, trailer_status, strict=True)):
        make, model = _TRAILER_MODELS[eq]
        city, state, _, _ = rng.choice(_HOMES)
        units.append({
            "kind": "trailer", "unit_number": f"TR-{201 + i}", "vin": _vin(rng), "make": make, "model": model,
            "year": rng.randint(2016, 2025), "plate": f"{state}-{rng.randint(1000, 9999)}", "equipment": eq,
            "status": st, "odometer_miles": None,
            "home_base_city": city, "home_base_state": state, "assigned_driver_name": None,
        })
    for u in units:
        u["source_ref"] = u["unit_number"]
    trucks = [u for u in units if u["kind"] == "truck"]
    trailers = [u for u in units if u["kind"] == "trailer"]
    # Trucks that are down are the ones carrying the critical defects.
    down = [u["unit_number"] for u in trucks if u["status"] in ("in_shop", "out_of_service")]
    critical_units = set(down[:2])

    inspections: list[dict[str, Any]] = []
    defects: list[dict[str, Any]] = []
    maintenance: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []

    for u in units:
        un, is_truck = u["unit_number"], u["kind"] == "truck"
        # --- inspections, oldest -> newest, odometer never decreasing
        n_insp = rng.randint(3, 6) if is_truck else rng.randint(2, 3)
        ages = [rng.randint(10, 80)]
        for _ in range(n_insp - 1):
            ages.append(ages[-1] + rng.randint(75, 100))
        ages.sort(reverse=True)  # oldest first
        # newest first while we walk back in time, then flip to oldest -> newest
        cur = u["odometer_miles"] - rng.randint(300, 6000) if is_truck else None
        odos: list[int | None] = []
        for _ in ages:
            odos.append(cur)
            if cur is not None:
                cur -= rng.randint(6000, 14000)
        odos.reverse()
        insp_rows: list[dict[str, Any]] = []
        for age, o in zip(ages, odos, strict=True):
            r = rng.random()
            result = "pass" if r < 0.78 else "conditional" if r < 0.97 else "fail"
            insp_rows.append({
                "unit_number": un, "inspected_at": days_ago(age), "inspector_name": rng.choice(_INSPECTORS),
                "result": result, "odometer_at_inspection": o,
                "notes": {"pass": "No findings.", "conditional": "Minor findings, fix at next service.",
                          "fail": "Out-of-service findings, re-inspect after repair."}[result],
            })
        latest = insp_rows[-1]

        # --- defects
        cat = _DEFECTS if is_truck else _TRAILER_DEFECTS
        open_defs: list[tuple[str, tuple[str, str]]] = []
        if un in critical_units:
            open_defs.append(("critical", rng.choice(_DEFECTS["critical"])))
        for _ in range(rng.choices([0, 1, 2, 3], weights=[40, 30, 20, 10])[0] if un not in critical_units else rng.randint(0, 1)):
            sev = rng.choices(["minor", "major"], weights=[65, 35])[0]
            open_defs.append((sev, rng.choice(cat[sev])))
        for sev, (title, desc) in open_defs:
            linked = rng.random() < 0.6 or sev == "critical"
            reported = latest["inspected_at"] if linked else days_ago(rng.randint(1, 30), 14)
            defects.append({
                "unit_number": un, "severity": sev, "title": title, "description": desc, "status": "open",
                "reported_at": reported, "resolved_at": None,
                "inspected_at": latest["inspected_at"] if linked else None,
            })
            if linked:
                latest["result"] = "fail" if sev == "critical" else ("conditional" if latest["result"] == "pass" else latest["result"])
        for _ in range(rng.randint(0, 2)):
            if len(insp_rows) < 2:
                break
            src = rng.choice(insp_rows[:-1])
            sev = rng.choice(["minor", "major"])
            title, desc = rng.choice(cat[sev])
            rep = src["inspected_at"]
            defects.append({
                "unit_number": un, "severity": sev, "title": title, "description": desc, "status": "resolved",
                "reported_at": rep, "resolved_at": rep + timedelta(days=rng.randint(2, 14)),
                "inspected_at": src["inspected_at"],
            })
        inspections.extend(insp_rows)

        # --- maintenance: 1-2 done, 1 scheduled
        for _ in range(rng.randint(1, 2) if is_truck else 1):
            kind = rng.choices(["service", "repair", "tyre"], weights=[60, 25, 15])[0]
            label, (lo, hi) = _MAINT[kind]
            age = rng.randint(20, 200)
            maintenance.append({
                "unit_number": un, "kind": kind, "title": label, "scheduled_for": today - timedelta(days=age + 3),
                "completed_at": today - timedelta(days=age),
                "odometer_at": None if not is_truck else max(0, u["odometer_miles"] - age * rng.randint(250, 450)),
                "cost_usd": round(rng.uniform(lo, hi), 2), "notes": None,
            })
        maintenance.append({
            "unit_number": un, "kind": "service", "title": "Preventive maintenance service",
            "scheduled_for": today + timedelta(days=rng.randint(9, 75)), "completed_at": None,
            "odometer_at": None, "cost_usd": None, "notes": "Scheduled by the shop.",
        })

        # --- documents
        kinds = ["registration", "insurance", "inspection_cert"] if is_truck else ["registration", "inspection_cert"]
        for k in kinds:
            exp = today + timedelta(days=rng.randint(45, 340))
            documents.append({
                "unit_number": un, "kind": k, "number": f"{k[:3].upper()}-{rng.randint(100000, 999999)}",
                "issued_on": exp - timedelta(days=180 if k == "inspection_cert" else 365), "expires_on": exp,
            })

    # --- alert guarantees: pin a few rows so the alerts strip is true by construction
    by_unit = {u["unit_number"]: u for u in units}

    def pin_doc(unit: str, kind: str, in_days: int) -> None:
        for d in documents:
            if d["unit_number"] == unit and d["kind"] == kind:
                d["expires_on"] = today + timedelta(days=in_days)
                d["issued_on"] = d["expires_on"] - timedelta(days=180 if kind == "inspection_cert" else 365)
                return

    pin_doc(trucks[4]["unit_number"], "insurance", 3)
    pin_doc(trucks[9]["unit_number"], "registration", 8)
    pin_doc(trailers[1]["unit_number"], "inspection_cert", 12)
    pin_doc(trucks[14]["unit_number"], "inspection_cert", 21)  # amber, not red
    pin_doc(trucks[18]["unit_number"], "insurance", 27)
    due = {trucks[i]["unit_number"]: d for i, d in zip((2, 7, 12, 17), (1, 3, 5, 6), strict=True)}
    for m in maintenance:
        if m["completed_at"] is None and m["unit_number"] in due:
            m["scheduled_for"] = today + timedelta(days=due[m["unit_number"]])
    assert len(by_unit) == 28
    return {"units": units, "inspections": inspections, "defects": defects,
            "maintenance": maintenance, "documents": documents}


def assign_runs(
    runs: Sequence[dict[str, Any]], trucks: Sequence[dict[str, Any]], now: datetime
) -> dict[int, dict[str, Any]]:
    """Give each run to a truck of the same equipment that is free at pickup.

    Round-robin, so work spreads over the fleet; a run nobody is free for stays
    unassigned (it went on a partner carrier). Trucks that are in the shop or
    out of service take no work in the last three weeks.
    Returns ``{run_id: {"truck_id", ...}}``.
    """
    by_eq: dict[str, list[dict[str, Any]]] = {}
    for t in trucks:
        by_eq.setdefault(t["equipment"], []).append(t)
    free_at: dict[int, datetime] = {}
    ptr: dict[str, int] = {}
    recent_cut = now - timedelta(days=21)
    out: dict[int, dict[str, Any]] = {}
    for r in sorted(runs, key=lambda r: (r["pickup_at"], r["id"])):
        cands = by_eq.get(r["equipment"] or "")
        if not cands:
            continue
        n, start = len(cands), ptr.get(r["equipment"], 0)
        for k in range(n):
            t = cands[(start + k) % n]
            if t["status"] in ("in_shop", "out_of_service") and r["pickup_at"] >= recent_cut:
                continue
            if free_at.get(t["id"], datetime.min.replace(tzinfo=UTC)) + timedelta(hours=4) <= r["pickup_at"]:
                out[r["id"]] = {"truck_id": t["id"]}
                free_at[t["id"]] = r["delivery_at"]
                ptr[r["equipment"]] = (start + k + 1) % n
                break
    return out


def last_positions(
    units: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]], plan: dict[int, dict[str, Any]], now: datetime
) -> dict[int, tuple[float, float, datetime]]:
    """Park each unit at the origin of its latest run (home base when it never ran).

    ``runs`` carry ``lat``/``lng`` (the run's origin). Returns ``{unit_id: (lat, lng, seen_at)}``.
    """
    homes = {(c, s): (la, lo) for c, s, la, lo in _HOMES}
    latest: dict[int, dict[str, Any]] = {}
    for r in sorted(runs, key=lambda r: (r["pickup_at"], r["id"])):
        if r["id"] in plan and r["pickup_at"] <= now:
            latest[plan[r["id"]]["truck_id"]] = r  # pickup-ordered, so the last write wins
    out: dict[int, tuple[float, float, datetime]] = {}
    for u in units:
        run = latest.get(u["id"])
        lat, lng = (run["lat"], run["lng"]) if run else homes[(u["home_base_city"], u["home_base_state"])]
        out[u["id"]] = (lat, lng, min(now, run["delivery_at"]) if run else now - timedelta(hours=6))
    return out


# ---- table handles for bulk inserts / updates -------------------------------

_J = sa.JSON()


def _tbl(name: str, cols: Sequence[str], json_col: bool = True) -> sa.TableClause:
    return sa.table(name, *[sa.column(c) for c in cols], *([sa.column("raw", _J)] if json_col else []))


_TRUCKS = _tbl("trucks", (
    "tenant_id", "kind", "unit_number", "vin", "make", "model", "year", "plate", "equipment", "status",
    "odometer_miles", "home_base_city", "home_base_state", "assigned_driver_name", "last_lat", "last_lng",
    "last_seen_at", "source", "source_ref"))
_INSP = _tbl("truck_inspections", (
    "tenant_id", "truck_id", "inspected_at", "inspector_name", "result", "odometer_at_inspection", "notes", "source"))
_DEF = _tbl("truck_defects", (
    "tenant_id", "truck_id", "inspection_id", "severity", "title", "description", "status", "reported_at",
    "resolved_at", "source"))
_MNT = _tbl("truck_maintenance", (
    "tenant_id", "truck_id", "kind", "title", "scheduled_for", "completed_at", "odometer_at", "cost_usd", "notes",
    "source"))
_DOC = _tbl("truck_documents", ("tenant_id", "truck_id", "kind", "number", "issued_on", "expires_on", "source"))

_ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _utc(v: Any) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def upgrade() -> None:
    bind = op.get_bind()
    pk = sa.Column("id", _ID_TYPE, primary_key=True, autoincrement=True)

    def tenant() -> sa.Column:
        return sa.Column("tenant_id", sa.String(26), nullable=False, server_default=LJM_TENANT_ID)

    def jraw() -> sa.Column:
        return sa.Column(
            "raw", postgresql.JSONB().with_variant(sa.JSON(), "sqlite"), nullable=False, server_default=sa.text("'{}'")
        )

    def fk(col: str, target: str, ondelete: str, nullable: bool = False) -> sa.Column:
        return sa.Column(col, _ID_TYPE, sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)

    op.create_table(
        "trucks", pk, tenant(),
        sa.Column("kind", sa.String(16), nullable=False, server_default="truck"),
        sa.Column("unit_number", sa.String(32), nullable=False),
        sa.Column("vin", sa.String(32)), sa.Column("make", sa.String(64)), sa.Column("model", sa.String(64)),
        sa.Column("year", sa.Integer()), sa.Column("plate", sa.String(24)), sa.Column("equipment", sa.String(32)),
        sa.Column("status", sa.String(24), nullable=False, server_default="available"),
        sa.Column("odometer_miles", sa.Integer()),
        sa.Column("home_base_city", sa.String(128)), sa.Column("home_base_state", sa.String(8)),
        sa.Column("assigned_driver_name", sa.String(128)),
        sa.Column("last_lat", sa.Numeric(8, 5)), sa.Column("last_lng", sa.Numeric(9, 5)),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("source", sa.String(32), nullable=False), sa.Column("source_ref", sa.String(64)), jraw(),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trucks_tenant_id", "trucks", ["tenant_id"])
    op.create_index("ix_trucks_tenant_source", "trucks", ["tenant_id", "source"])

    op.create_table(
        "truck_inspections", sa.Column("id", _ID_TYPE, primary_key=True, autoincrement=True), tenant(),
        fk("truck_id", "trucks.id", "CASCADE"),
        sa.Column("inspected_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("inspector_name", sa.String(128)), sa.Column("result", sa.String(16), nullable=False),
        sa.Column("odometer_at_inspection", sa.Integer()), sa.Column("notes", sa.Text()),
        sa.Column("source", sa.String(32), nullable=False), jraw(),
    )
    op.create_index("ix_truck_inspections_tenant_id", "truck_inspections", ["tenant_id"])
    op.create_index("ix_truck_inspections_tenant_source", "truck_inspections", ["tenant_id", "source"])
    op.create_index("ix_truck_inspections_truck_at", "truck_inspections", ["truck_id", "inspected_at"])

    op.create_table(
        "truck_defects", sa.Column("id", _ID_TYPE, primary_key=True, autoincrement=True), tenant(),
        fk("truck_id", "trucks.id", "CASCADE"), fk("inspection_id", "truck_inspections.id", "SET NULL", True),
        sa.Column("severity", sa.String(16), nullable=False), sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("reported_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("source", sa.String(32), nullable=False), jraw(),
    )
    op.create_index("ix_truck_defects_tenant_id", "truck_defects", ["tenant_id"])
    op.create_index("ix_truck_defects_tenant_source", "truck_defects", ["tenant_id", "source"])
    op.create_index("ix_truck_defects_truck_status", "truck_defects", ["truck_id", "status"])

    op.create_table(
        "truck_maintenance", sa.Column("id", _ID_TYPE, primary_key=True, autoincrement=True), tenant(),
        fk("truck_id", "trucks.id", "CASCADE"),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("title", sa.String(255)),
        sa.Column("scheduled_for", sa.Date()), sa.Column("completed_at", sa.Date()),
        sa.Column("odometer_at", sa.Integer()), sa.Column("cost_usd", sa.Numeric(10, 2)), sa.Column("notes", sa.Text()),
        sa.Column("source", sa.String(32), nullable=False), jraw(),
    )
    op.create_index("ix_truck_maintenance_tenant_id", "truck_maintenance", ["tenant_id"])
    op.create_index("ix_truck_maintenance_tenant_source", "truck_maintenance", ["tenant_id", "source"])
    op.create_index("ix_truck_maintenance_truck_sched", "truck_maintenance", ["truck_id", "scheduled_for"])

    op.create_table(
        "truck_documents", sa.Column("id", _ID_TYPE, primary_key=True, autoincrement=True), tenant(),
        fk("truck_id", "trucks.id", "CASCADE"),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("number", sa.String(64)),
        sa.Column("issued_on", sa.Date()), sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("file_url", sa.String(512)), sa.Column("source", sa.String(32), nullable=False), jraw(),
    )
    op.create_index("ix_truck_documents_tenant_id", "truck_documents", ["tenant_id"])
    op.create_index("ix_truck_documents_tenant_source", "truck_documents", ["tenant_id", "source"])
    op.create_index("ix_truck_documents_expires_on", "truck_documents", ["expires_on"])
    op.create_index("ix_truck_documents_truck", "truck_documents", ["truck_id"])

    # Nullable, SET NULL: old rows and future non-demo runs are untouched.
    op.add_column("freight_runs", sa.Column("truck_id", _ID_TYPE, sa.ForeignKey("trucks.id", ondelete="SET NULL")))
    op.create_index("ix_freight_runs_truck_id", "freight_runs", ["truck_id", "pickup_at"])

    if bind.dialect.name == "postgresql":
        for t in _TABLES:
            bind.exec_driver_sql(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            bind.exec_driver_sql(
                f"""
                CREATE POLICY {t}_tenant_isolation ON {t}
                USING (
                    tenant_id = current_setting('app.tenant_id', true)
                    OR coalesce(current_setting('app.tenant_id', true), '') = ''
                )
                WITH CHECK (
                    tenant_id = current_setting('app.tenant_id', true)
                    OR coalesce(current_setting('app.tenant_id', true), '') = ''
                )
                """
            )

    _seed(bind)


def _seed(bind: sa.Connection) -> None:
    existing = bind.execute(
        sa.text("SELECT COUNT(*) FROM trucks WHERE tenant_id = :t AND source = 'demo'"), {"t": LJM_TENANT_ID}
    ).scalar() or 0
    if existing:
        return
    now = datetime.now(UTC)
    fleet = generate_fleet(now)
    base = {"tenant_id": LJM_TENANT_ID, "source": "demo"}

    runs = [
        {"id": r[0], "equipment": r[1], "pickup_at": _utc(r[2]), "delivery_at": _utc(r[3]),
         "lat": float(r[4]), "lng": float(r[5])}
        for r in bind.execute(sa.text(
            "SELECT id, equipment, pickup_at, delivery_at, origin_lat, origin_lng FROM freight_runs "
            "WHERE tenant_id = :t AND source = 'demo' ORDER BY pickup_at, id"), {"t": LJM_TENANT_ID})
    ]

    # trucks first (ids are needed by every child + the run backfill)
    op.bulk_insert(_TRUCKS, [{**base, **u, "last_lat": None, "last_lng": None, "last_seen_at": None, "raw": {"demo": True}}
                             for u in fleet["units"]])
    ids = {r[0]: r[1] for r in bind.execute(sa.text(
        "SELECT unit_number, id FROM trucks WHERE tenant_id = :t AND source = 'demo'"), {"t": LJM_TENANT_ID})}

    op.bulk_insert(_INSP, [{**base, "truck_id": ids[i.pop("unit_number")], **i, "raw": {"demo": True}}
                           for i in (dict(x) for x in fleet["inspections"])])
    insp_id = {(r[0], _utc(r[1])): r[2] for r in bind.execute(sa.text(
        "SELECT truck_id, inspected_at, id FROM truck_inspections WHERE tenant_id = :t AND source = 'demo'"),
        {"t": LJM_TENANT_ID})}
    defect_rows = []
    for d in (dict(x) for x in fleet["defects"]):
        tid, at = ids[d.pop("unit_number")], d.pop("inspected_at")
        defect_rows.append({**base, "truck_id": tid, "inspection_id": insp_id.get((tid, at)) if at else None,
                            **d, "raw": {"demo": True}})
    op.bulk_insert(_DEF, defect_rows)
    op.bulk_insert(_MNT, [{**base, "truck_id": ids[m.pop("unit_number")], **m, "raw": {"demo": True}}
                          for m in (dict(x) for x in fleet["maintenance"])])
    op.bulk_insert(_DOC, [{**base, "truck_id": ids[d.pop("unit_number")], **d, "raw": {"demo": True}}
                          for d in (dict(x) for x in fleet["documents"])])

    # backfill demo runs -> trucks, and park each truck at its latest run's origin
    trucks = [{**u, "id": ids[u["unit_number"]]} for u in fleet["units"] if u["kind"] == "truck"]
    plan = assign_runs(runs, trucks, now)
    if plan:
        bind.execute(sa.text("UPDATE freight_runs SET truck_id = :truck_id WHERE id = :id"),
                     [{"id": rid, "truck_id": v["truck_id"]} for rid, v in plan.items()])
    placed = last_positions([{**u, "id": ids[u["unit_number"]]} for u in fleet["units"]], runs, plan, now)
    pos = [{"id": tid, "lat": lat, "lng": lng, "seen": seen} for tid, (lat, lng, seen) in placed.items()]
    bind.execute(sa.text("UPDATE trucks SET last_lat = :lat, last_lng = :lng, last_seen_at = :seen WHERE id = :id"), pos)


def downgrade() -> None:
    bind = op.get_bind()
    # Purge exactly the demo rows first (explicit, so the intent survives a future partial table drop).
    bind.execute(sa.text("UPDATE freight_runs SET truck_id = NULL WHERE truck_id IS NOT NULL"))
    for t in reversed(_TABLES):
        bind.execute(sa.text(f"DELETE FROM {t} WHERE tenant_id = :t AND source = 'demo'"), {"t": LJM_TENANT_ID})
    op.drop_index("ix_freight_runs_truck_id", table_name="freight_runs")
    op.drop_column("freight_runs", "truck_id")
    for t in reversed(_TABLES):
        if bind.dialect.name == "postgresql":
            bind.exec_driver_sql(f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t}")
        op.drop_table(t)
