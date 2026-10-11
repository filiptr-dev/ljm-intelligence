"""Customers + contacts ingest — LoadStop ``customers`` / ``contacts``.

LoadStop customers are brokers / shippers / forwarders. We don't have a
dedicated ``brokers`` table in this codebase — brokers ARE rows in
``leads`` (``kind='Broker'``, with ``mc`` / ``dot`` / ``primary_email``
/ ``phone`` / ``city`` / ``state`` / ``address``). We match on

  1. MC   → ``leads.mc`` (partial-unique)
  2. DOT  → ``leads.dot`` (partial-unique)
  3. normalised legal name (``broker_matching.normalize_name``)

**Match-first, create-last.** When a match hits, we write-if-null on
contact/address fields so an operator-edited lead isn't silently
clobbered. LoadStop-specific metadata (customer id, credit status,
address block, raw) lives in ``leads.raw['loadstop']`` — additive, no
schema change, read by the broker-detail panel.

When no match hits, we create a new ``leads`` row with
``raw={'loadstop': …, 'review_needed': True}`` so the operator confirms
before outreach — the trust-break rule.

Contacts upsert into ``lead_contacts`` keyed on (lead_id, email_norm or
name_norm) using the existing dedupe helper.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.prospecting.broker_matching import normalize_name
from app.prospecting.contacts_repository import norm_email, norm_name
from app.prospecting.models import Lead, LeadContact, LeadSource
from app.shared.orm import LJM_TENANT_ID


LOG = logging.getLogger(__name__)
SOURCE = "loadstop"
_MC_RE = re.compile(r"[^0-9]")


class _ClientLike(Protocol):
    async def paged(self, service, method, *, page_size=200, extra=None): ...


def _clean_mc(mc: Any) -> str | None:
    if mc is None:
        return None
    digits = _MC_RE.sub("", str(mc))
    return digits or None


def _clean_dot(dot: Any) -> str | None:
    return _clean_mc(dot)


def _lead_id(mc: str | None, dot: str | None, name: str) -> str:
    """Mirror the id scheme the rest of the pipeline uses — MC → DOT → random."""
    if mc:
        return f"MC-{mc}"
    if dot:
        return f"DOT-{dot}"
    norm = normalize_name(name) or "unknown"
    return f"LOADSTOP-{norm[:40]}-{secrets.token_hex(4)}"


def _project(row: dict[str, Any]) -> dict[str, Any]:
    addr = row.get("address") or {}
    return {
        "loadstop_id": str(row.get("id") or ""),
        "name": str(row.get("name") or row.get("legalName") or "").strip(),
        "mc": _clean_mc(row.get("mcNumber") or row.get("mc")),
        "dot": _clean_dot(row.get("dotNumber") or row.get("dot")),
        "kind": (row.get("type") or "Broker"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "city": addr.get("city") or row.get("city"),
        "state": (addr.get("state") or row.get("state") or "")[:2].upper() or None,
        "address": addr.get("line1") or (row.get("address") if isinstance(row.get("address"), str) else None),
        "credit_status": row.get("creditStatus") or row.get("credit"),
        "credit_updated_at": row.get("creditUpdatedAt"),
    }


async def _match(session: AsyncSession, tenant: str, p: dict[str, Any]) -> Lead | None:
    """MC → DOT → normalised-name match. Returns None on no hit."""
    if p["mc"]:
        hit = (await session.execute(select(Lead).where(Lead.tenant_id == tenant, Lead.mc == p["mc"]))).scalar_one_or_none()
        if hit:
            return hit
    if p["dot"]:
        hit = (await session.execute(select(Lead).where(Lead.tenant_id == tenant, Lead.dot == p["dot"]))).scalar_one_or_none()
        if hit:
            return hit
    name_norm = normalize_name(p["name"])
    if name_norm:
        rows = (
            await session.execute(
                select(Lead).where(Lead.tenant_id == tenant, Lead.name.isnot(None))
            )
        ).scalars().all()
        for r in rows:
            if normalize_name(r.name) == name_norm:
                return r
    return None


async def ingest_customers(
    session: AsyncSession,
    client: _ClientLike,
    *,
    tenant_id: str = LJM_TENANT_ID,
) -> dict[str, int]:
    rows_in = 0
    matched = 0
    created = 0
    async for page in client.paged("customers", "GetAll", page_size=200):
        for raw in page:
            rows_in += 1
            p = _project(raw)
            if not p["name"]:
                continue
            match = await _match(session, tenant_id, p)
            loadstop_block = {
                "customer_id": p["loadstop_id"],
                "credit_status": p["credit_status"],
                "credit_updated_at": p["credit_updated_at"],
                "address": {"city": p["city"], "state": p["state"], "line1": p["address"]},
            }
            if match is None:
                lead = Lead(
                    tenant_id=tenant_id,
                    id=_lead_id(p["mc"], p["dot"], p["name"]),
                    mc=p["mc"],
                    dot=p["dot"],
                    name=p["name"],
                    kind=p["kind"][:32],
                    state=(p["state"] or "")[:2],
                    city=p["city"],
                    address=p["address"],
                    phone=p["phone"],
                    primary_email=p["email"],
                    raw={"loadstop": loadstop_block, "review_needed": True, "source": SOURCE},
                )
                session.add(lead)
                session.add(
                    LeadSource(
                        tenant_id=tenant_id,
                        lead_id=lead.id,
                        source=SOURCE,
                        source_ref=p["loadstop_id"] or lead.id,
                        payload={"name": p["name"]},
                    )
                )
                created += 1
            else:
                # Write-if-null — never clobber operator-edited fields.
                if not match.phone and p["phone"]:
                    match.phone = p["phone"]
                if not match.primary_email and p["email"]:
                    match.primary_email = p["email"]
                if not match.city and p["city"]:
                    match.city = p["city"]
                if not match.address and p["address"]:
                    match.address = p["address"]
                if not match.mc and p["mc"]:
                    match.mc = p["mc"]
                if not match.dot and p["dot"]:
                    match.dot = p["dot"]
                existing_raw = dict(match.raw or {})
                existing_raw["loadstop"] = loadstop_block
                match.raw = existing_raw
                matched += 1
    await session.flush()
    return {"rows_in": rows_in, "rows_upserted": matched + created, "matched": matched, "created": created}


async def ingest_contacts(
    session: AsyncSession,
    client: _ClientLike,
    *,
    tenant_id: str = LJM_TENANT_ID,
) -> dict[str, int]:
    """Pull LoadStop contacts; attach to any lead whose raw.loadstop.customer_id matches."""
    rows_in = 0
    upserted = 0
    # Build an index customer_id → lead.id so we don't re-query per contact.
    rows = (
        await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))
    ).scalars().all()
    customer_to_lead: dict[str, str] = {}
    for r in rows:
        cid = ((r.raw or {}).get("loadstop") or {}).get("customer_id")
        if cid:
            customer_to_lead[str(cid)] = r.id
    async for page in client.paged("contacts", "GetAll", page_size=200):
        for raw in page:
            rows_in += 1
            cust_id = str(raw.get("customerId") or raw.get("CustomerId") or "")
            lead_id = customer_to_lead.get(cust_id)
            if not lead_id:
                continue
            email = raw.get("email")
            name = raw.get("name") or raw.get("fullName")
            email_n = norm_email(email)
            name_n = norm_name(name)
            # Dedupe by email first, then name within the lead.
            existing = None
            if email_n:
                existing = (
                    await session.execute(
                        select(LeadContact).where(
                            LeadContact.tenant_id == tenant_id,
                            LeadContact.lead_id == lead_id,
                            LeadContact.email_norm == email_n,
                        )
                    )
                ).scalar_one_or_none()
            if existing is None and name_n:
                existing = (
                    await session.execute(
                        select(LeadContact).where(
                            LeadContact.tenant_id == tenant_id,
                            LeadContact.lead_id == lead_id,
                            LeadContact.email_norm.is_(None),
                            LeadContact.name_norm == name_n,
                        )
                    )
                ).scalar_one_or_none()
            if existing is None:
                session.add(
                    LeadContact(
                        tenant_id=tenant_id,
                        lead_id=lead_id,
                        name=name,
                        title=raw.get("title"),
                        email=email,
                        phone=raw.get("phone"),
                        source=SOURCE,
                        email_norm=email_n,
                        name_norm=name_n,
                    )
                )
            else:
                if not existing.email and email:
                    existing.email = email
                    existing.email_norm = email_n
                if not existing.phone and raw.get("phone"):
                    existing.phone = raw.get("phone")
                if not existing.title and raw.get("title"):
                    existing.title = raw.get("title")
            upserted += 1
    await session.flush()
    return {"rows_in": rows_in, "rows_upserted": upserted}
