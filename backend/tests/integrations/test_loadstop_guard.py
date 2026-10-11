"""LoadStop ABP client — triangulated write-guard (AC5) + SSRF (AC10).

These are the load-bearing safety contracts for the connector. A missed
block here is money out the door (LJM trucks posted publicly to DAT).
Every assertion is intentionally paranoid.
"""

from __future__ import annotations

import pytest

from app.integrations.adapters.loadboard.loadstop.http import (
    AbpClient,
    LoadstopSSRFBlocked,
    LoadstopWriteBlocked,
    _assert_allowed_host,
    _is_read_only,
)


def test_read_only_predicate_accepts_plain_reads():
    for m in ("GetAll", "GetAllTrucks", "GetById", "ListUsers", "SearchLoads", "ExportCustomers", "Count"):
        assert _is_read_only(m), m


def test_read_only_predicate_rejects_write_verbs():
    # Explicit denylist (both allowlist-regex-failing AND denylist entries).
    for m in (
        "CreateTruck",
        "UpdateTruck",
        "DeleteCustomer",
        "PostTruck",
        "PostTruckToDat",
        "TruckPosting",
        "SyncLoads",
        "SubmitPost",
        "Negotiate",
        "AssignDriver",
    ):
        assert not _is_read_only(m), m


def test_read_only_predicate_rejects_denylist_renames_that_pass_allowlist():
    """A Get* name that embeds a write verb must still be blocked (AC5 triangulation)."""
    # These all MATCH the regex (start with Get/List) but contain a write
    # marker — the denylist / substring check catches them.
    for sneaky in ("GetAndDelete", "GetAndUpdate", "ListAndCreate", "GetAndPostTruck", "SearchAndSubmit"):
        assert not _is_read_only(sneaky), sneaky


async def test_client_call_raises_before_opening_socket():
    """call() must raise LoadstopWriteBlocked WITHOUT touching httpx."""

    class ExplodingHttp:
        async def post(self, *a, **kw):  # pragma: no cover — must not run
            raise AssertionError("socket opened for a blocked write")

    client = AbpClient(ExplodingHttp())  # type: ignore[arg-type]

    for bad in ("CreateTruck", "TruckPosting", "SyncLoads", "PostTruckToDat", "GetAndDelete"):
        with pytest.raises(LoadstopWriteBlocked):
            await client.call("trucks", bad)


def test_assert_read_only_classmethod_is_the_same_gate():
    AbpClient.assert_read_only("GetAllTrucks")
    with pytest.raises(LoadstopWriteBlocked):
        AbpClient.assert_read_only("CreateTruck")
    with pytest.raises(LoadstopWriteBlocked):
        AbpClient.assert_read_only("GetAndDelete")


def test_ssrf_allowlist_blocks_tracking_subdomain():
    _assert_allowed_host("https://secure.loadstop.com/foo")
    with pytest.raises(LoadstopSSRFBlocked):
        _assert_allowed_host("https://tracking.loadstop.com/anything")
    with pytest.raises(LoadstopSSRFBlocked):
        _assert_allowed_host("https://evil.example/evilpath")
