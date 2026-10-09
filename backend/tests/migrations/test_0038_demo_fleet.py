"""Migration 0038: the deterministic fleet seeder (pure) + a Postgres round-trip (opt-in)."""

from __future__ import annotations

import os
from collections import Counter
from datetime import timedelta
from itertools import pairwise

import pytest

from app.fleet.domain import EQUIPMENT, Odometer
from tests.analysis.lanes_fixtures import NOW, TENANT, demo_rows
from tests.fleet.fleet_fixtures import load_migration

mig = load_migration()
TODAY = NOW.date()


@pytest.fixture(scope="module")
def fleet():
    return mig.generate_fleet(NOW)


def test_shape_is_20_trucks_8_trailers_and_deterministic(fleet):
    assert fleet == mig.generate_fleet(NOW)
    trucks = [u for u in fleet["units"] if u["kind"] == "truck"]
    assert len(trucks) == 20 and len(fleet["units"]) - len(trucks) == 8
    assert Counter(u["equipment"] for u in trucks) == {"van": 10, "reefer": 6, "flatbed": 3, "stepdeck": 1}
    assert {u["equipment"] for u in fleet["units"]} <= set(EQUIPMENT)  # same vocab as FreightRun.equipment
    st = Counter(u["status"] for u in trucks)
    assert st["available"] == 11 and st["on_load"] == 5 and st["in_shop"] == 3 and st["out_of_service"] == 1
    assert len({u["unit_number"] for u in fleet["units"]}) == 28


def test_alert_guarantees_hold_by_construction(fleet):
    soon = [d for d in fleet["documents"] if d["expires_on"] <= TODAY + timedelta(days=14)]
    assert len(soon) >= 3
    crit = {d["unit_number"] for d in fleet["defects"] if d["severity"] == "critical" and d["status"] == "open"}
    assert len(crit) >= 2
    due = [m for m in fleet["maintenance"] if m["completed_at"] is None and m["scheduled_for"] <= TODAY + timedelta(days=7)]
    assert len(due) >= 3


def test_odometer_never_goes_down_and_stays_under_the_truck(fleet):
    odo = {u["unit_number"]: u["odometer_miles"] for u in fleet["units"]}
    by_unit: dict[str, list] = {}
    for i in fleet["inspections"]:
        by_unit.setdefault(i["unit_number"], []).append(i)
    for un, rows in by_unit.items():
        rows.sort(key=lambda r: r["inspected_at"])
        if odo[un] is None:
            assert all(r["odometer_at_inspection"] is None for r in rows)
            continue
        cur = Odometer(rows[0]["odometer_at_inspection"])
        for r in rows[1:]:
            cur = cur.advance_to(r["odometer_at_inspection"])  # raises if it ever drops
        assert cur.miles <= odo[un]
    with pytest.raises(ValueError):
        Odometer(100).advance_to(99)


def test_documents_and_defects_are_well_formed(fleet):
    assert all(d["expires_on"] > TODAY - timedelta(days=1) for d in fleet["documents"])
    assert all(d["resolved_at"] is None for d in fleet["defects"] if d["status"] == "open")
    assert all(d["resolved_at"] > d["reported_at"] for d in fleet["defects"] if d["status"] == "resolved")


def test_run_assignment_matches_equipment_and_never_overlaps(fleet):
    runs = [
        {"id": i, "equipment": r["equipment"], "pickup_at": r["pickup_at"], "delivery_at": r["delivery_at"]}
        for i, r in enumerate(demo_rows(NOW), start=1)
    ]
    trucks = [{**u, "id": 1000 + i} for i, u in enumerate(u for u in fleet["units"] if u["kind"] == "truck")]
    plan = mig.assign_runs(runs, trucks, NOW)
    by_id = {t["id"]: t for t in trucks}
    run_by_id = {r["id"]: r for r in runs}
    assert len(plan) > 1000  # most history lands on a truck
    per_truck: dict[int, list] = {}
    for rid, v in plan.items():
        assert by_id[v["truck_id"]]["equipment"] == run_by_id[rid]["equipment"]
        per_truck.setdefault(v["truck_id"], []).append(run_by_id[rid])
    for rows in per_truck.values():
        rows.sort(key=lambda r: r["pickup_at"])
        assert all(a["delivery_at"] <= b["pickup_at"] for a, b in pairwise(rows))
    # a truck that is down takes no recent work
    down = {t["id"] for t in trucks if t["status"] in ("in_shop", "out_of_service")}
    cut = NOW - timedelta(days=21)
    assert not [1 for rid, v in plan.items() if v["truck_id"] in down and run_by_id[rid]["pickup_at"] >= cut]


pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="set DATABASE_URL_TEST_PG=postgresql+psycopg://... for the PG round-trip",
)


@pg
def test_pg_up_down_up_leaves_other_rows_alone():
    import psycopg
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL_TEST_PG"]
    sync = url.replace("postgresql+psycopg://", "postgresql://")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False

    def one(sql: str) -> int:
        with psycopg.connect(sync) as c, c.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()[0]

    def has_col() -> bool:
        return bool(one(
            "SELECT count(*) FROM information_schema.columns WHERE table_name='freight_runs' AND column_name='truck_id'"
        ))

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert one("SELECT count(*) FROM trucks WHERE source='demo' AND kind='truck'") == 20
    assert one("SELECT count(*) FROM trucks WHERE source='demo' AND kind='trailer'") == 8
    assigned = one("SELECT count(*) FROM freight_runs WHERE truck_id IS NOT NULL AND source='demo'")
    assert assigned > 1000
    assert one("SELECT count(*) FROM trucks WHERE last_lat IS NOT NULL") == 28
    command.upgrade(cfg, "head")  # no-op: idempotent
    assert one("SELECT count(*) FROM trucks") == 28

    # a non-demo run (no truck) and a non-demo truck must survive nothing but the downgrade of their own tables
    with psycopg.connect(sync, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO freight_runs (tenant_id, source, origin_city, origin_state, dest_city, dest_state,"
            " origin_lat, origin_lng, dest_lat, dest_lng, miles, pickup_at, delivery_at, revenue_usd,"
            " cost_fuel_usd, cost_driver_usd, cost_load_usd, cost_dispatch_usd) VALUES"
            " (%s,'real','A','TX','B','TX',1,1,1,1,100,now(),now(),1,1,1,1,1)", (TENANT,),
        )
    n_runs = one("SELECT count(*) FROM freight_runs")
    command.downgrade(cfg, "0037")
    assert not has_col()
    assert one("SELECT count(*) FROM freight_runs") == n_runs  # no run was deleted
    assert one("SELECT count(*) FROM freight_runs WHERE source='real'") == 1
    assert not one("SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'truck%'")
    command.upgrade(cfg, "head")
    assert has_col() and one("SELECT count(*) FROM trucks WHERE source='demo'") == 28
    assert one("SELECT count(*) FROM freight_runs WHERE truck_id IS NOT NULL AND source='real'") == 0
    # deleting a truck unlinks its runs (SET NULL), never deletes them
    with psycopg.connect(sync, autocommit=True) as c, c.cursor() as cur:
        cur.execute("DELETE FROM trucks WHERE id = (SELECT truck_id FROM freight_runs WHERE truck_id IS NOT NULL LIMIT 1)")
    assert one("SELECT count(*) FROM freight_runs") == n_runs
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")




def test_an_inspection_with_findings_is_never_a_clean_pass(fleet):
    linked = {(d["unit_number"], d["inspected_at"]) for d in fleet["defects"] if d["inspected_at"]}
    for i in fleet["inspections"]:
        if (i["unit_number"], i["inspected_at"]) in linked:
            assert i["result"] != "pass" and i["notes"] != "No findings."
