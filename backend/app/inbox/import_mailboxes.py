"""One-off historical import across every allowlisted mailbox.

Run as a module so the orchestrator can invoke it inside the live API
container without rebuilding or restarting anything::

    docker exec -w /app -e PYTHONPATH=/app ljm-local-api-1 \\
        python -m app.inbox.import_mailboxes --hours 2 --dry-run

Flags:

* ``--hours N`` / ``--days N`` — one-shot window of the last N hours (or
  N*24 hours). Walks every real allowlisted mailbox in parallel.
* ``--mailboxes a,b`` — restrict to a comma-separated subset. Non-allowlisted
  entries are dropped with a clear log line.
* ``--expand`` — walk the stepped ladder (2h → 12h → 24h → 3d → 7d → 30d →
  90d) outward from each mailbox's current ``backfilled_through_at`` cursor.
  Each invocation advances every mailbox by ONE step. Pair with
  ``--until-hours X`` to run the ladder until coverage reaches X hours.
* ``--concurrency N`` — bounded fan-out across mailboxes (default 4).
  Per-mailbox rate limiting is still honored by ``MailboxLimiter``.
* ``--dry-run`` — resolve the per-mailbox window + current cursor and print
  what WOULD run; no Gmail I/O, no DB writes.

Resumability lives in ``mail_cursors.backfilled_through_at`` (migration
0017). The gradual-import step already writes the OLDER edge of each
walked window, so this command composes with ``inbox.mail_backfill_step``
and the nightly scheduler fan-out without a new table.

Design notes:

* No AI calls during bulk import. The ingest path only triggers AI in the
  nightly ``analysis.nightly`` run — ``mail_backfill`` already gates the
  first-backfill trigger behind an existing ``PredictionRun`` row so this
  command cannot stampede the AI budget.
* One mailbox failing never stops the others — gather with
  ``return_exceptions=True`` and log each outcome.
* Per-mailbox ``MailboxLimiter`` (``settings.gmail.per_mailbox_rps``) is
  built into ``GmailMailbox``; no extra limiter here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("app.inbox.import_mailboxes")


# The user-approved expansion ladder. Each number is the TOTAL coverage
# the next step aims for, in hours.
LADDER_HOURS: tuple[int, ...] = (2, 12, 24, 72, 168, 720, 2160)


@dataclass
class StepPlan:
    mailbox: str
    older_than: datetime
    newer_than: datetime

    @property
    def width_hours(self) -> float:
        return (self.newer_than - self.older_than).total_seconds() / 3600.0


def _next_step_hours(current_hours: float) -> int | None:
    """Pick the next ladder rung strictly above ``current_hours`` (0 = never
    imported). Returns None once the top is reached."""
    for h in LADDER_HOURS:
        if h > current_hours + 1e-6:
            return h
    return None


async def _plan_mailbox(
    sessionmaker: Any,
    mailbox: str,
    *,
    hours: int | None,
    expand: bool,
    now: datetime,
) -> StepPlan | None:
    """Resolve the window for one mailbox. Returns ``None`` if the mailbox
    already covers the requested span (so the caller can skip the Gmail call
    entirely)."""
    from sqlalchemy import select

    from app.inbox.models import MailCursor

    async with sessionmaker() as s:
        cur = (
            await s.execute(select(MailCursor).where(MailCursor.mailbox == mailbox))
        ).scalar_one_or_none()
    through = cur.backfilled_through_at if cur and cur.backfilled_through_at else None
    if through is not None and through.tzinfo is None:
        through = through.replace(tzinfo=UTC)

    if expand:
        covered_hours = 0.0 if through is None else max(
            0.0, (now - through).total_seconds() / 3600.0
        )
        nxt = _next_step_hours(covered_hours)
        if nxt is None:
            return None
        older_than = now - timedelta(hours=nxt)
        newer_than = through if through is not None else now
        if newer_than <= older_than:
            # Shouldn't happen, but guard against a jittery clock.
            return None
        return StepPlan(mailbox=mailbox, older_than=older_than, newer_than=newer_than)

    assert hours is not None and hours > 0
    older_than = now - timedelta(hours=hours)
    # When a prior cursor already reaches further back, the "fetch N hours"
    # path narrows to the gap between the current coverage and ``now`` so a
    # second invocation never re-walks the same mail.
    newer_than = now
    if through is not None and through < older_than:
        # Already covered.
        return None
    return StepPlan(mailbox=mailbox, older_than=older_than, newer_than=newer_than)


async def _run_one(
    sessionmaker: Any,
    settings: Any,
    plan: StepPlan,
) -> dict:
    """Backfill one mailbox for the computed window."""
    from app.integrations.mail_service import backfill as svc_backfill

    log.info(
        "import_mailboxes: %s window=(%s .. %s) hours=%.1f",
        plan.mailbox,
        plan.older_than.isoformat(),
        plan.newer_than.isoformat(),
        plan.width_hours,
    )
    try:
        stats = await svc_backfill(
            sessionmaker,
            settings,
            mailbox=plan.mailbox,
            months=0,
            window=(plan.older_than, plan.newer_than),
        )
    except Exception as exc:
        log.exception("import_mailboxes: %s failed", plan.mailbox)
        return {"mailbox": plan.mailbox, "status": "error", "error": str(exc)}
    return {
        "mailbox": plan.mailbox,
        "status": stats.status,
        "read": stats.read,
        "upserted": stats.upserted,
        "skipped": stats.skipped,
        "error": stats.error,
        "window_hours": round(plan.width_hours, 2),
    }


def _resolve_mailboxes(
    settings: Any, override: list[str] | None
) -> list[str]:
    from app.integrations.adapters.email.credentials import resolve_mail_read_allowlist

    allow = resolve_mail_read_allowlist(settings)
    if allow is None:
        log.warning("import_mailboxes: no read-allowlist set — refusing to walk every mailbox")
        return []
    if not override:
        return sorted(allow)
    chosen = [m.strip().lower() for m in override if m.strip()]
    kept = [m for m in chosen if m in allow]
    dropped = [m for m in chosen if m not in allow]
    if dropped:
        log.warning("import_mailboxes: dropped (not in allowlist): %s", dropped)
    return kept


async def _one_pass(
    sessionmaker: Any,
    settings: Any,
    *,
    mailboxes: list[str],
    hours: int | None,
    expand: bool,
    concurrency: int,
    dry_run: bool,
) -> list[dict]:
    now = datetime.now(UTC)
    plans: list[StepPlan] = []
    for mbx in mailboxes:
        plan = await _plan_mailbox(
            sessionmaker, mbx, hours=hours, expand=expand, now=now,
        )
        if plan is None:
            log.info("import_mailboxes: %s already covers requested span — skip", mbx)
            continue
        plans.append(plan)

    if dry_run:
        return [
            {
                "mailbox": p.mailbox,
                "status": "dry_run",
                "older_than": p.older_than.isoformat(),
                "newer_than": p.newer_than.isoformat(),
                "window_hours": round(p.width_hours, 2),
            }
            for p in plans
        ]

    sem = asyncio.Semaphore(max(1, concurrency))

    async def go(p: StepPlan) -> dict:
        async with sem:
            return await _run_one(sessionmaker, settings, p)

    results = await asyncio.gather(*(go(p) for p in plans), return_exceptions=True)
    out: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            out.append({"status": "error", "error": str(r)})
        else:
            out.append(r)
    return out


async def run(
    *,
    hours: int | None,
    days: int | None,
    mailboxes: list[str] | None,
    expand: bool,
    until_hours: int | None,
    concurrency: int,
    dry_run: bool,
) -> list[dict]:
    """Programmatic entry point — exposed so tests + jobs can call it."""
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.integrations.adapters.email.credentials import ensure_vault_primed

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        await ensure_vault_primed(sm)

        chosen = _resolve_mailboxes(settings, mailboxes)
        if not chosen:
            log.warning("import_mailboxes: nothing to do (no mailboxes resolved)")
            return []

        if days is not None and hours is None:
            hours = days * 24

        if expand:
            # One step for every mailbox, or walk the ladder until coverage
            # reaches ``until_hours`` for all of them.
            all_results: list[dict] = []
            while True:
                pass_results = await _one_pass(
                    sm, settings,
                    mailboxes=chosen, hours=None, expand=True,
                    concurrency=concurrency, dry_run=dry_run,
                )
                all_results.extend(pass_results)
                if until_hours is None or dry_run or not pass_results:
                    break
                # Did every mailbox reach ``until_hours``? If so, stop.
                reached = True
                from sqlalchemy import select

                from app.inbox.models import MailCursor

                async with sm() as s:
                    for mbx in chosen:
                        cur = (
                            await s.execute(
                                select(MailCursor).where(MailCursor.mailbox == mbx)
                            )
                        ).scalar_one_or_none()
                        through = cur.backfilled_through_at if cur else None
                        if through is None:
                            reached = False
                            break
                        if through.tzinfo is None:
                            through = through.replace(tzinfo=UTC)
                        covered_h = (
                            datetime.now(UTC) - through
                        ).total_seconds() / 3600.0
                        if covered_h < until_hours:
                            reached = False
                            break
                if reached:
                    break
            return all_results

        return await _one_pass(
            sm, settings,
            mailboxes=chosen, hours=hours, expand=False,
            concurrency=concurrency, dry_run=dry_run,
        )
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="app.inbox.import_mailboxes")
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--days", type=int, default=None,
                   help="Sugar for --hours N*24.")
    p.add_argument("--mailboxes", default=None,
                   help="Comma-separated subset of allowlisted mailboxes.")
    p.add_argument("--expand", action="store_true",
                   help="Walk the ladder outward from each mailbox's cursor.")
    p.add_argument("--until-hours", type=int, default=None,
                   help="With --expand, keep stepping until every mailbox "
                        "covers at least this many hours of history.")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if not args.expand and args.hours is None and args.days is None:
        p.error("one of --hours / --days / --expand is required")
    if args.until_hours is not None and not args.expand:
        p.error("--until-hours only makes sense with --expand")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)
    mailboxes = (
        [m.strip() for m in args.mailboxes.split(",") if m.strip()]
        if args.mailboxes else None
    )
    results = asyncio.run(
        run(
            hours=args.hours,
            days=args.days,
            mailboxes=mailboxes,
            expand=args.expand,
            until_hours=args.until_hours,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
        )
    )
    # Per-mailbox summary at the end — single-line rows so a 31-row report
    # scrolls cleanly in the orchestrator log.
    for r in results:
        print(f"[{r.get('status','?'):8}] {r.get('mailbox','-'):40} " + ", ".join(
            f"{k}={v}" for k, v in r.items()
            if k not in ("mailbox", "status") and v is not None
        ))
    bad = [r for r in results if r.get("status") == "error"]
    return 2 if bad else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
