"""Deterministic plain-language alert sentences (same tone as ``analysis/lanes_text``).

Pure functions over rows that are already loaded: no I/O, no AI. They read the
same on a laptop with no Gemini key, and can never disagree with the numbers.
"""

from __future__ import annotations

from app.fleet.schemas import AlertStatement, DefectRef, DocRef, MaintRef

DOC_WINDOW_DAYS = 14
MAINT_WINDOW_DAYS = 7
_MAX_NAMED = 3

_DOC_LABEL = {
    "registration": "registration", "insurance": "insurance", "inspection_cert": "inspection certificate",
    "adr": "ADR certificate", "tacho_calibration": "tacho calibration",
}


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def when(days: int) -> str:
    if days < 0:
        return f"{-days} day{_plural(-days, '', 's')} overdue"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def _named(items: list[str]) -> str:
    shown = items[:_MAX_NAMED]
    extra = len(items) - len(shown)
    return ", ".join(shown) + (f" and {extra} more" if extra else "")


def doc_label(kind: str) -> str:
    return _DOC_LABEL.get(kind, kind.replace("_", " "))


def statements(docs: list[DocRef], defects: list[DefectRef], jobs: list[MaintRef]) -> list[AlertStatement]:
    n = len(docs)
    if n:
        names = _named([f"{d.unit_number} {doc_label(d.kind)} ({when(d.days_left)})" for d in docs])
        doc_text = f"{n} document{_plural(n, ' expires', 's expire')} in the next {DOC_WINDOW_DAYS} days: {names}."
    else:
        doc_text = f"No document expires in the next {DOC_WINDOW_DAYS} days."

    m = len(defects)
    units = len({d.truck_id for d in defects})
    if m:
        names = _named([f"{d.unit_number} {d.title.lower()}" for d in defects])
        def_text = (
            f"{m} open critical defect{_plural(m, '', 's')} across {units} unit{_plural(units, '', 's')}: {names}. "
            "These should not be dispatched until fixed."
        )
    else:
        def_text = "No open critical defects. Nothing is blocking dispatch."

    k = len(jobs)
    if k:
        names = _named([f"{j.unit_number} ({when(j.days_until)})" for j in jobs])
        job_text = f"{k} maintenance job{_plural(k, ' is', 's are')} due within {MAINT_WINDOW_DAYS} days: {names}."
    else:
        job_text = f"No maintenance is due in the next {MAINT_WINDOW_DAYS} days."

    return [
        AlertStatement(key="docs", title="Documents", text=doc_text),
        AlertStatement(key="defects", title="Critical defects", text=def_text),
        AlertStatement(key="maintenance", title="Maintenance", text=job_text),
    ]


def _money(v: float) -> str:
    return f"${v:,.0f}"


def unit_summary(u, kpis, open_defects: int, critical: int) -> str:
    """One paragraph at the top of the drawer: what this unit is and how it has been doing."""
    what = " ".join(str(x) for x in (u.year, u.make, u.model) if x) or "Unit"
    eq = f" {u.equipment}" if u.equipment else ""
    head = f"{u.unit_number} is a {what}{eq} {u.kind}, {u.status.replace('_', ' ')}."
    if u.kind == "trailer" or not kpis.runs_count:
        run = f"No hauls are recorded for it in the last {kpis.window_days} days."
    else:
        margin = f"{_money(kpis.margin_usd)} margin" if kpis.margin_usd >= 0 else f"{_money(-kpis.margin_usd)} below cost"
        run = (
            f"In the last {kpis.window_days} days it ran {kpis.runs_count} haul{_plural(kpis.runs_count, '', 's')}, "
            f"{kpis.miles:,} miles, {_money(kpis.revenue_usd)} revenue ({margin})."
        )
    if critical:
        health = f" It has {critical} open critical defect{_plural(critical, '', 's')} and should stay parked."
    elif open_defects:
        health = f" {open_defects} minor or major defect{_plural(open_defects, ' is', 's are')} still open."
    else:
        health = " No defects are open."
    return head + " " + run + health
