"""Idempotent writer for ``settings.mail_read_allowlist``.

Keeps the Settings UI free of a 31-row paste box — the orchestrator can
feed the real-mailbox list from the Gmail probe straight into the DB:

    docker exec -w /app -e PYTHONPATH=/app ljm-local-api-1 \\
        python -m app.inbox.set_read_allowlist \\
            --from-file /tmp/real_mailboxes.txt --dry-run

Behaviour:

* Reads the list from ``--from-file`` (one address per line, blanks + ``#``
  comments ignored) OR a comma-separated ``--emails`` flag.
* Compares to the current ``SettingsRow.mail_read_allowlist`` — no-op (and
  clear log line) when they already match, so a cron can run it safely.
* With ``--dry-run`` prints the diff and exits without writing.
* On a real write, refreshes the process-local vault cache via
  ``set_vault_allowlist_cache`` so the running API picks up the new list
  without a restart. The procrastinate worker reprimes lazily through
  ``ensure_vault_primed`` on its next job.

The schema the ORM expects is a JSON list of lowercased emails
(``identity.models.SettingsRow.mail_read_allowlist``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

log = logging.getLogger("app.inbox.set_read_allowlist")


def _read_file(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.lower())
    return out


async def run(*, emails: list[str], dry_run: bool) -> dict:
    from sqlalchemy import select

    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.identity.models import SettingsRow
    from app.integrations.adapters.email.credentials import set_vault_allowlist_cache

    settings = get_settings()
    # Dedupe + normalise before touching the DB so a repeated flag never
    # writes a dirty list.
    normalised = sorted({e.strip().lower() for e in emails if e.strip()})

    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with sm() as s:
            row = (
                await s.execute(select(SettingsRow).where(SettingsRow.id == 1))
            ).scalar_one_or_none()
            current: list[str] = (
                sorted({str(e).strip().lower() for e in (row.mail_read_allowlist or [])})
                if row is not None else []
            )
            added = sorted(set(normalised) - set(current))
            removed = sorted(set(current) - set(normalised))
            changed = bool(added or removed)

            result = {
                "current_count": len(current),
                "next_count": len(normalised),
                "added": added,
                "removed": removed,
                "changed": changed,
                "dry_run": dry_run,
            }
            if dry_run or not changed:
                return result

            if row is None:
                row = SettingsRow(id=1)
                s.add(row)
            row.mail_read_allowlist = normalised
            await s.commit()

        # Refresh the in-process cache for the API worker that ran us.
        # Other workers reprime via ``ensure_vault_primed`` on their next job.
        set_vault_allowlist_cache(normalised)
        return result
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="app.inbox.set_read_allowlist")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-file", type=Path, default=None)
    src.add_argument("--emails", default=None,
                     help="Comma-separated list of emails.")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)
    if args.from_file:
        emails = _read_file(args.from_file)
    else:
        emails = [e.strip() for e in (args.emails or "").split(",") if e.strip()]
    if not emails:
        print("set_read_allowlist: refusing to write an empty allowlist")
        return 3
    result = asyncio.run(run(emails=emails, dry_run=args.dry_run))
    verb = "would set" if args.dry_run else "set"
    print(
        f"set_read_allowlist: {verb} {result['next_count']} mailboxes "
        f"(current={result['current_count']}, "
        f"added={len(result['added'])}, removed={len(result['removed'])}, "
        f"changed={result['changed']})"
    )
    if result["added"]:
        print("  + " + ", ".join(result["added"]))
    if result["removed"]:
        print("  - " + ", ".join(result["removed"]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
