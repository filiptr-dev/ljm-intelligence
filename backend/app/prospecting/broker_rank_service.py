"""SQL-backed broker ranking + keyset pagination.

Hot-path replacement for the Python ``load → compute → sort → slice`` dance
in ``brokers_service.list_brokers`` and ``overview_service.get_today``'s
to-call-today tile. One query shape serves both: ``rank_brokers()`` returns
one page already ordered + paged, ``count_brokers_by_action()`` returns a
scalar count over the same CASE expression.

The 10-rule next-action table lives in
``app.pipeline.broker_next_action.compute`` — this module mirrors the
``kind`` branch of that table in SQL for sort + count, and re-runs
``compute()`` in Python over the paged rows (<= limit) so the one-screen
rule-table-with-reason-strings stays single-sourced.

Decisions baked in (see plan 2026-10-01-brokers-keyset-sql-sort):

* **ASCII lower()** — PG's ``lower()`` is locale-dependent; broker names are
  ASCII in LJM, so parity holds. ICU collations are not required.
* **Null fit sinks** — mirrors Python's ``None → +1`` mapping: a fit_score
  of NULL sorts strictly after every real fit score.
* **Keyset over OFFSET** — opaque base64-JSON cursor of the full ordering
  tuple ``(priority, fit_sort, lower_name, id)``; strict row-value compare
  for the next-page predicate.
* **RLS does the tenant filter** — this module never adds an explicit
  ``tenant_id = …`` to the query. The session's bound ``app.tenant_id``
  flows through the RLS policy; the composite ``(tenant_id, kind, …)``
  index still matches when PG re-writes the policy as a WHERE.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text

from app.pipeline.broker_next_action import (
    NextAction,
    NextActionInput,
    compute,
)
from app.prospecting.brokers_service import BrokerRowData, ContactField, NextActionRow

# ---------- filters + cursor -----------------------------------------------


@dataclass(frozen=True)
class RankFilters:
    """What ``GET /brokers`` can filter by. All optional."""

    state: str | None = None
    min_fit: int | None = None
    has_email: bool | None = None
    has_phone: bool | None = None
    next_action: str | None = None  # "call" | "email" | "follow_up" | "wait"
    q: str | None = None


@dataclass(frozen=True)
class Cursor:
    """Keyset position — exactly the SQL ORDER BY tuple (minus the final id).

    ``fit_sort`` matches the Python mapping: ``-fit_score`` for real values,
    ``+1`` for NULL. One monotonic integer; no NULLs; no row-value NULL trap.
    """

    priority: int  # 1..4
    fit_sort: int  # -fit_score or +1 for null
    lower_name: str
    id: str

    def encode(self) -> str:
        payload = json.dumps(
            {"p": self.priority, "f": self.fit_sort, "n": self.lower_name, "id": self.id},
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, s: str | None) -> Cursor | None:
        if not s:
            return None
        try:
            padded = s + "=" * (-len(s) % 4)
            d = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return cls(
                priority=int(d["p"]),
                fit_sort=int(d["f"]),
                lower_name=str(d["n"]),
                id=str(d["id"]),
            )
        except Exception:  # noqa: BLE001 — caller translates to a 400
            return None


@dataclass
class RankedPage:
    rows: list[BrokerRowData]
    next_cursor: str | None
    total: int


# ---------- SQL ------------------------------------------------------------


# The 10-rule CASE in SQL. Mirrors ``broker_next_action.compute`` for the
# ``kind`` branch only; reason strings are composed in Python so the rule
# table has one home. ``:now`` is a tz-aware timestamp; ``:today`` is a date.
_NEXT_ACTION_SQL = """
CASE
  -- Rule 1 — callback due today.
  WHEN pending_cb_logged_at IS NOT NULL THEN 'call'
  -- Rule 2 — booked, newest, within 30 days.
  WHEN last_call_outcome = 'booked'
       AND call_days IS NOT NULL
       AND (sent_days IS NULL OR call_days <= sent_days)
       AND call_days <= 30 THEN 'wait'
  -- Rule 3 — not_interested, newest, within 90 days.
  WHEN last_call_outcome = 'not_interested'
       AND call_days IS NOT NULL
       AND (sent_days IS NULL OR call_days <= sent_days)
       AND call_days <= 90 THEN 'wait'
  -- Rule 4 — emailed within 7 days, no reply.
  WHEN sent_days IS NOT NULL AND sent_days <= 7 AND last_sent_replied_at IS NULL
       THEN 'wait'
  -- Rule 5 — replied within 30 days (follow-up window).
  WHEN sent_days IS NOT NULL AND sent_days <= 30 AND last_sent_replied_at IS NOT NULL
       THEN 'follow_up'
  -- Rule 6 — 7–21d since email, still no reply, sendable email on file.
  WHEN sent_days IS NOT NULL AND sent_days > 7 AND sent_days <= 21
       AND last_sent_replied_at IS NULL AND has_sendable_email
       THEN 'email'
  -- Rule 7 — no_answer within 7 days, has phone.
  WHEN last_call_outcome = 'no_answer' AND call_days IS NOT NULL
       AND call_days <= 7 AND has_phone THEN 'call'
  -- Rule 8 — phone + never contacted.
  WHEN has_phone AND total_calls = 0 AND total_sent = 0 THEN 'call'
  -- Rule 9 — never contacted + sendable email.
  WHEN total_calls = 0 AND total_sent = 0 AND has_sendable_email THEN 'email'
  -- Rule 10 — dormant catch-all.
  ELSE 'follow_up'
END
"""


# Base SELECT. The CTEs are chained so ``next_action_kind`` and the sort key
# are reusable in both WHERE (filter / keyset) and ORDER BY without
# recomputing. ``:now`` and ``:today`` are bound params — never f-stringed.
_BASE_CTE_SQL = f"""
WITH ranked AS (
  SELECT
    l.id,
    l.name,
    l.mc,
    l.dot,
    l.state,
    l.city,
    l.phone,
    l.phone_source,
    l.primary_email,
    l.primary_email_source,
    l.fit_score,
    lc.outcome        AS last_call_outcome,
    lc.logged_at      AS last_call_logged_at,
    ls.sent_at        AS last_sent_at,
    ls.replied_at     AS last_sent_replied_at,
    pc.logged_at      AS pending_cb_logged_at,
    pc.callback_at    AS pending_cb_callback_at,
    -- ``total_calls`` / ``total_sent`` are only read by Rules 8 and 9 and
    -- only to tell "never contacted" (==0) from "has history" (>0). The
    -- LATERAL above returns no row ⇔ there are no rows ⇔ count is 0, so we
    -- can derive the 0/>=1 bit for free and skip the per-row COUNT scans
    -- that dominated the 25 k perf case. Any downstream caller that needs
    -- real counts should pull them separately.
    (CASE WHEN lc.logged_at IS NULL THEN 0 ELSE 1 END)::int                AS total_calls,
    (CASE WHEN ls.sent_at IS NULL THEN 0 ELSE 1 END)::int                  AS total_sent,
    (l.phone IS NOT NULL AND l.phone <> '')                               AS has_phone,
    EXISTS (
      SELECT 1 FROM lead_contacts lx
      WHERE lx.lead_id = l.id
        AND lx.email IS NOT NULL
        AND lx.pipeline_status <> 'bounced'
        AND lx.email NOT IN (SELECT email FROM suppression)
    ) AS has_sendable_email
  FROM leads l
  LEFT JOIN LATERAL (
    SELECT outcome, logged_at
    FROM call_outcomes
    WHERE lead_id = l.id
    ORDER BY logged_at DESC
    LIMIT 1
  ) lc ON TRUE
  LEFT JOIN LATERAL (
    SELECT sent_at, replied_at
    FROM sent_log
    WHERE lead_id = l.id
    ORDER BY sent_at DESC
    LIMIT 1
  ) ls ON TRUE
  LEFT JOIN LATERAL (
    SELECT logged_at, callback_at
    FROM call_outcomes
    WHERE lead_id = l.id
      AND outcome = 'callback'
      AND callback_at IS NOT NULL
      AND callback_at <= CAST(:today AS date)
    ORDER BY logged_at ASC
    LIMIT 1
  ) pc ON TRUE
  WHERE l.kind = 'Broker'
    {{extra_lead_filters}}
),
with_days AS (
  SELECT
    r.*,
    CASE WHEN last_call_logged_at IS NULL THEN NULL
         ELSE GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - last_call_logged_at)) / 86400.0))::int
    END AS call_days,
    CASE WHEN last_sent_at IS NULL THEN NULL
         ELSE GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - last_sent_at)) / 86400.0))::int
    END AS sent_days
  FROM ranked r
),
with_action AS (
  SELECT
    w.*,
    ({_NEXT_ACTION_SQL}) AS next_action_kind
  FROM with_days w
),
scored AS (
  SELECT
    wa.*,
    CASE next_action_kind
      WHEN 'call' THEN 1
      WHEN 'email' THEN 2
      WHEN 'follow_up' THEN 3
      ELSE 4
    END AS action_priority,
    -- Match the Python ranker's "-(fit if not None else -1)" mapping exactly:
    -- real fit maps to a negative integer; NULL maps to +1 and sorts last.
    CASE WHEN fit_score IS NULL THEN 1 ELSE -fit_score END AS fit_sort,
    COALESCE(lower(name), '') AS lower_name,
    GREATEST(
      COALESCE(last_call_logged_at, 'epoch'::timestamptz),
      COALESCE(last_sent_at, 'epoch'::timestamptz)
    ) AS last_activity_at_raw,
    (last_call_logged_at IS NOT NULL OR last_sent_at IS NOT NULL) AS has_activity
  FROM with_action wa
)
SELECT a.* FROM scored a
"""


def _build_lead_filter_clauses(f: RankFilters, params: dict[str, Any]) -> str:
    """Return extra WHERE clauses that go on the ``leads`` scan directly.

    Everything that can be pushed down to the base ``leads`` select lives
    here — tightens the row set before the four LATERALs fire.
    """
    bits: list[str] = []
    if f.state:
        bits.append("AND l.state = :f_state")
        params["f_state"] = f.state.upper()
    if f.min_fit is not None:
        bits.append("AND l.fit_score >= :f_min_fit")
        params["f_min_fit"] = f.min_fit
    if f.has_phone is True:
        bits.append("AND l.phone IS NOT NULL AND l.phone <> ''")
    elif f.has_phone is False:
        bits.append("AND (l.phone IS NULL OR l.phone = '')")
    if f.has_email is True:
        bits.append("AND l.primary_email IS NOT NULL")
    elif f.has_email is False:
        bits.append("AND l.primary_email IS NULL")
    if f.q:
        bits.append(
            "AND (l.name ILIKE :f_q OR l.mc ILIKE :f_q OR l.dot ILIKE :f_q)"
        )
        params["f_q"] = f"%{f.q.strip()}%"
    return "\n    ".join(bits)


# ---------- row -> dataclass ----------------------------------------------


def _field(value: str | None, source: str | None = None) -> ContactField:
    return ContactField(value=value or None, source=source)


def _row_to_broker(r: Any, now: datetime, today: date) -> BrokerRowData:
    """Translate a SQL row into ``BrokerRowData``, re-running ``compute()``
    for the single rule-table-driven reason string. ``kind`` is the SQL CASE
    output; running ``compute()`` here would re-derive it anyway, so we
    just use its ``.reason`` + ``.due_at``.
    """
    pending_days: int | None = None
    if r.pending_cb_logged_at is not None:
        pcb_date = r.pending_cb_logged_at.date() if isinstance(r.pending_cb_logged_at, datetime) else r.pending_cb_logged_at
        pending_days = max(0, (today - pcb_date).days)

    inp = NextActionInput(
        latest_call_outcome=r.last_call_outcome,
        latest_call_logged_at=r.last_call_logged_at,
        pending_callback_today=r.pending_cb_logged_at is not None,
        pending_callback_days_ago=pending_days,
        pending_callback_due=r.pending_cb_callback_at,
        latest_sent_at=r.last_sent_at,
        latest_sent_replied_at=r.last_sent_replied_at,
        has_phone=bool(r.has_phone),
        has_sendable_email=bool(r.has_sendable_email),
        total_call_outcomes=int(r.total_calls or 0),
        total_sent=int(r.total_sent or 0),
    )
    action: NextAction = compute(inp, now)

    # Last activity is the newer of (last call, last sent), or None if neither.
    last_activity_at: str | None = None
    if r.has_activity:
        la = r.last_activity_at_raw
        if isinstance(la, datetime):
            last_activity_at = la.isoformat()

    return BrokerRowData(
        id=r.id,
        name=r.name,
        mc=r.mc,
        dot=r.dot,
        state=r.state,
        city=r.city,
        phone=_field(r.phone, r.phone_source),
        primary_email=_field(r.primary_email, r.primary_email_source),
        fit_score=r.fit_score,
        next_action=NextActionRow(
            kind=action.kind,
            reason=action.reason,
            due_at=action.due_at.isoformat() if action.due_at else None,
        ),
        last_activity_at=last_activity_at,
    )


# ---------- public API -----------------------------------------------------


async def _dialect_is_postgres(sessionmaker: Any) -> bool:
    """One tiny peek at the engine bound to this sessionmaker. SQLite has no
    ``LEFT JOIN LATERAL`` (used all over this module); the fallback path
    delegates to the Python ranker so the sqlite test harness keeps working
    unchanged. PG16 (prod + the perf/parity tests) always takes the SQL path.
    """
    async with sessionmaker() as s:
        bind = s.get_bind() if hasattr(s, "get_bind") else None
        name = getattr(getattr(bind, "dialect", None), "name", "")
        return name == "postgresql"


async def rank_brokers(
    sessionmaker: Any,
    *,
    filters: RankFilters | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> RankedPage:
    """One ranked page in SQL. See module docstring for the ordering contract.

    Returns ``(rows, next_cursor, total)``. ``total`` counts the filtered
    broker set — unaffected by the cursor (so UIs can show
    "showing 1–50 of N").
    """
    filters = filters or RankFilters()
    if not await _dialect_is_postgres(sessionmaker):
        return await _rank_brokers_fallback(
            sessionmaker, filters=filters, limit=limit, cursor=cursor
        )
    params: dict[str, Any] = {}
    now = datetime.now(UTC)
    today = now.date()
    params["now"] = now
    params["today"] = today

    extra = _build_lead_filter_clauses(filters, params)
    base = _BASE_CTE_SQL.format(extra_lead_filters=extra)

    page_where_bits: list[str] = ["WHERE 1=1"]
    if filters.next_action:
        page_where_bits.append("AND a.next_action_kind = :p_next_action")
        params["p_next_action"] = filters.next_action
    cur = Cursor.decode(cursor)
    if cur is not None:
        # Strict row-value compare. ``fit_sort`` is non-null; lower_name
        # falls back to '' via COALESCE in the base — safe to compare.
        page_where_bits.append(
            "AND (a.action_priority, a.fit_sort, a.lower_name, a.id) "
            "> (:c_p, :c_f, :c_n, :c_id)"
        )
        params["c_p"] = cur.priority
        params["c_f"] = cur.fit_sort
        params["c_n"] = cur.lower_name
        params["c_id"] = cur.id

    # Page query: filter + sort + LIMIT. We deliberately skip ``COUNT(*)
    # OVER ()`` here because the window aggregate forces PG to materialize
    # the whole sorted set — the opposite of a keyset win. ``total`` is
    # computed by a sibling SELECT below, cheaply when the filter is
    # lead-level only.
    page_where_bits: list[str] = ["WHERE 1=1"]
    if filters.next_action:
        page_where_bits.append("AND a.next_action_kind = :p_next_action")
    if cur is not None:
        page_where_bits.append(
            "AND (a.action_priority, a.fit_sort, a.lower_name, a.id) "
            "> (:c_p, :c_f, :c_n, :c_id)"
        )
    page_sql = (
        base
        + "\n"
        + "\n".join(page_where_bits)
        + "\nORDER BY a.action_priority ASC, a.fit_sort ASC, a.lower_name ASC, a.id ASC"
        + "\nLIMIT :limit"
    )
    params["limit"] = limit + 1  # +1 so we know if a next page exists

    # Total: when the request filters by ``next_action`` we must evaluate the
    # full CASE table to count; otherwise the total is the lead-level count
    # (``kind='Broker'`` + any state/fit/phone/email/q bits), which is a
    # dirt-cheap one-column scan — no LATERALs, no window.
    if filters.next_action:
        total_sql = f"SELECT COUNT(*) FROM (\n{base}\n WHERE a.next_action_kind = :p_next_action\n) t"
    else:
        where_bits = ["WHERE l.kind = 'Broker'"]
        if filters.state:
            where_bits.append("AND l.state = :f_state")
        if filters.min_fit is not None:
            where_bits.append("AND l.fit_score >= :f_min_fit")
        if filters.has_phone is True:
            where_bits.append("AND l.phone IS NOT NULL AND l.phone <> ''")
        elif filters.has_phone is False:
            where_bits.append("AND (l.phone IS NULL OR l.phone = '')")
        if filters.has_email is True:
            where_bits.append("AND l.primary_email IS NOT NULL")
        elif filters.has_email is False:
            where_bits.append("AND l.primary_email IS NULL")
        if filters.q:
            where_bits.append(
                "AND (l.name ILIKE :f_q OR l.mc ILIKE :f_q OR l.dot ILIKE :f_q)"
            )
        total_sql = "SELECT COUNT(*) FROM leads l\n" + "\n".join(where_bits)

    async with sessionmaker() as s:
        res = await s.execute(text(page_sql), params)
        rows = res.fetchall()
        total_res = await s.execute(text(total_sql), params)
        total = int(total_res.scalar_one() or 0)

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    broker_rows = [_row_to_broker(r, now, today) for r in page_rows]

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = Cursor(
            priority=int(last.action_priority),
            fit_sort=int(last.fit_sort),
            lower_name=str(last.lower_name),
            id=str(last.id),
        ).encode()

    return RankedPage(rows=broker_rows, next_cursor=next_cursor, total=total)


async def _rank_brokers_fallback(
    sessionmaker: Any,
    *,
    filters: RankFilters,
    limit: int,
    cursor: str | None,
) -> RankedPage:
    """SQLite fallback — delegates to the pre-existing Python ranker.

    Not perf-critical (sqlite is test-only). Keeps the response shape
    identical, including the opaque base64 cursor the SQL path emits.
    """
    from app.prospecting.brokers_service import list_brokers as _py_list

    res = await _py_list(
        sessionmaker,
        state=filters.state,
        min_fit=filters.min_fit,
        has_email=filters.has_email,
        has_phone=filters.has_phone,
        next_action=filters.next_action,
        q=filters.q,
    )
    # Translate the Python ranker's sort_keys into our opaque cursor shape so
    # callers can round-trip pages identically on either backend.
    cur = Cursor.decode(cursor)
    start = 0
    if cur is not None:
        for i, k in enumerate(res.sort_keys):
            # sort_keys shape is (priority, -fit_or_+1, lower_name, id).
            # Match on id — the stable tiebreak — then skip past it.
            if k[3] == cur.id:
                start = i + 1
                break
    page = res.items[start : start + limit]
    next_cursor = None
    if start + limit < len(res.items) and page:
        k = res.sort_keys[start + limit - 1]
        next_cursor = Cursor(
            priority=int(k[0]),
            fit_sort=int(k[1]),
            lower_name=str(k[2]),
            id=str(k[3]),
        ).encode()
    return RankedPage(rows=page, next_cursor=next_cursor, total=len(res.items))


async def count_brokers_by_action(
    sessionmaker: Any,
    *,
    filters: RankFilters | None = None,
    kind: str,
) -> int:
    """Cheap aggregate: ``COUNT(*)`` over the same CASE expression as the
    ranker. Used by ``/overview/today``'s ``to_call_today`` tile.

    One query, no row materialization — PG keeps this well under the
    150 ms budget even at 25 k leads.
    """
    filters = filters or RankFilters()
    if not await _dialect_is_postgres(sessionmaker):
        # Fallback — reuse the Python ranker and count. Slow at scale but
        # sqlite is test-only.
        page = await _rank_brokers_fallback(
            sessionmaker,
            filters=RankFilters(
                state=filters.state,
                min_fit=filters.min_fit,
                has_email=filters.has_email,
                has_phone=filters.has_phone,
                next_action=kind,
                q=filters.q,
            ),
            limit=10**9,
            cursor=None,
        )
        return page.total
    params: dict[str, Any] = {}
    now = datetime.now(UTC)
    params["now"] = now
    params["today"] = now.date()
    params["p_next_action"] = kind
    extra = _build_lead_filter_clauses(filters, params)
    base = _BASE_CTE_SQL.format(extra_lead_filters=extra)
    sql = (
        f"SELECT COUNT(*) FROM (\n  {base}\n  WHERE a.next_action_kind = :p_next_action\n) t"
    )
    async with sessionmaker() as s:
        res = await s.execute(text(sql), params)
        return int(res.scalar_one() or 0)


__all__ = [
    "Cursor",
    "RankFilters",
    "RankedPage",
    "count_brokers_by_action",
    "rank_brokers",
]
