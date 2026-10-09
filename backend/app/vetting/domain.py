"""Pure vetting rules — zero I/O, zero DB, deterministic.

A dispatcher should read this file in 10 seconds and know exactly why a
broker got the verdict they did. Same lesson as the next-action table:
a rule set beats a model nobody can audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum


class RedFlag(str, Enum):
    """Discrete, auditable red-flag codes. Reasons live beside them in `vet()`."""

    new_authority_lt_180d = "new_authority_lt_180d"
    no_phone = "no_phone"
    oos_recent = "oos_recent"
    name_mismatch_vs_dba = "name_mismatch_vs_dba"
    authority_revoked = "authority_revoked"
    authority_unverified = "authority_unverified"


class VerdictBand(str, Enum):
    safe = "safe"
    caution = "caution"
    avoid = "avoid"


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    """Shape the domain layer needs — never a raw SODA/snapshot dict."""

    mc: str | None
    dot: str | None
    legal_name: str | None
    dba_name: str | None
    authority_status: str | None  # 'A' (active), 'I' (inactive), 'R' (revoked)
    add_date: date | None
    oos_date: date | None  # most recent out-of-service date, if any
    phone: str | None
    email: str | None
    # Provenance of this snapshot — one of 'fmcsa_live' / 'fmcsa_cache' /
    # 'lead_record'. 'lead_record' means FMCSA never confirmed it; the UI and
    # the verdict both treat it as unverified rather than as a pass.
    source: str | None = None


@dataclass(frozen=True, slots=True)
class PriorContact:
    last_sent_at: datetime | None = None
    booked_count: int = 0
    rejected_count: int = 0


@dataclass(frozen=True, slots=True)
class FlagHit:
    code: RedFlag
    reason: str


@dataclass(frozen=True, slots=True)
class Verdict:
    band: VerdictBand
    red_flags: list[FlagHit] = field(default_factory=list)


def _days_since(d: date | None, today: date) -> int | None:
    if d is None:
        return None
    return (today - d).days


def vet(
    snapshot: BrokerSnapshot,
    prior: PriorContact,
    suppressed: bool,
    *,
    today: date | None = None,
) -> Verdict:
    """Rule table — mutate-free. Precedence: avoid > caution > safe.

    * authority_revoked → avoid (regulated-out, do not haul).
    * oos within 180d  → avoid.
    * no_phone AND no email AND no prior booking → caution (unverifiable).
    * new authority <180d → caution (youth is not a crime, but weigh it).
    * legal/DBA mismatch when both present and differ → caution.
    * everything else, plus booked_count > 0 → safe.
    """
    today = today or datetime.now(UTC).date()
    flags: list[FlagHit] = []

    status = (snapshot.authority_status or "").upper()
    if status in ("R", "V"):
        flags.append(
            FlagHit(
                RedFlag.authority_revoked,
                "FMCSA authority is revoked — do not haul.",
            )
        )
    elif not status:
        # No authority_status means FMCSA never confirmed this carrier for
        # this lookup (lead-only fallback, or an upstream outage with no
        # cache). "Unknown" is NOT "active" — route to caution so a dispatcher
        # verifies manually before hauling. The 2026-10-09 audit found a
        # fabricated status="A" slipping a lead-fallback snapshot into a
        # "safe" verdict; this flag closes that false-safety hole.
        flags.append(
            FlagHit(
                RedFlag.authority_unverified,
                "FMCSA authority not verified for this lookup — treat as unverified.",
            )
        )

    oos_days = _days_since(snapshot.oos_date, today)
    if oos_days is not None and oos_days <= 180:
        flags.append(
            FlagHit(
                RedFlag.oos_recent,
                f"Out-of-service event {oos_days} day(s) ago (within 180d).",
            )
        )

    if not snapshot.phone:
        flags.append(FlagHit(RedFlag.no_phone, "No phone on FMCSA record."))

    age_days = _days_since(snapshot.add_date, today)
    if age_days is not None and age_days < 180:
        flags.append(
            FlagHit(
                RedFlag.new_authority_lt_180d,
                f"Authority is {age_days} day(s) old (<180d).",
            )
        )

    legal = (snapshot.legal_name or "").strip().lower()
    dba = (snapshot.dba_name or "").strip().lower()
    if legal and dba and legal != dba:
        flags.append(
            FlagHit(
                RedFlag.name_mismatch_vs_dba,
                f"Legal name '{snapshot.legal_name}' differs from DBA '{snapshot.dba_name}'.",
            )
        )

    codes = {f.code for f in flags}
    if RedFlag.authority_revoked in codes or RedFlag.oos_recent in codes or suppressed:
        band = VerdictBand.avoid
    elif codes & {RedFlag.new_authority_lt_180d, RedFlag.name_mismatch_vs_dba, RedFlag.authority_unverified} or RedFlag.no_phone in codes and prior.booked_count == 0:
        band = VerdictBand.caution
    else:
        band = VerdictBand.safe

    return Verdict(band=band, red_flags=flags)


def normalize_key(raw: str) -> str | None:
    """Strip MC-/DOT- prefix + non-digits; return None if nothing remains.

    No regex — pydantic + str methods is enough and the rule parallels the
    Laravel-validation-over-regex norm.
    """
    if not raw:
        return None
    key = raw.strip().upper().removeprefix("MC-").removeprefix("DOT-").removeprefix("MC").removeprefix("DOT").strip("- ")
    if not key.isdigit():
        return None
    return key
