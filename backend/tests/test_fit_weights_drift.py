"""Drift guard — DEFAULT_WEIGHTS (Python) vs DEFAULT_FIT_WEIGHTS (TypeScript).

The frontend Settings page ships a hint per weight in
`frontend/src/app/(app)/settings/fit-weight-meta.ts`. If a weight is added or removed
on the backend without updating that file, the UI shows the raw key + no
tooltip. This test parses the TS module to extract the DEFAULT_FIT_WEIGHTS keys
and asserts they match Python one-to-one — catches the drift at CI time, not
in a browser.

No `pnpm` required: we regex the source. Cheap and stable enough because the
literal is an object literal, not a computed expression.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.scoring.fit_score import DEFAULT_WEIGHTS

_META_FILE = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "app" / "(app)" / "settings" / "fit-weight-meta.ts"
)


def _extract_ts_default_keys() -> set[str]:
    text = _META_FILE.read_text(encoding="utf-8")
    m = re.search(
        r"DEFAULT_FIT_WEIGHTS\s*:\s*Record<string,\s*number>\s*=\s*\{([^}]*)\}",
        text,
        re.DOTALL,
    )
    assert m, "Could not locate DEFAULT_FIT_WEIGHTS object literal in fit-weight-meta.ts"
    body = m.group(1)
    # Keys are bare identifiers followed by `:`; skip commented lines just in case.
    keys = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body, re.MULTILINE))
    return keys


def _extract_ts_meta_keys() -> set[str]:
    text = _META_FILE.read_text(encoding="utf-8")
    # Slice from the FIT_WEIGHT_META declaration to the next `export ` on a new
    # line (which is FIT_WEIGHT_KEYS). Top-level entry keys are at 2-space
    # indent and end with `: {` — nested keys are at 4+ spaces so they're safe
    # to exclude with a `^  <key>: {$` anchor.
    start = text.index("export const FIT_WEIGHT_META")
    tail = text[start:]
    end = tail.index("\nexport const FIT_WEIGHT_KEYS")
    body = tail[:end]
    keys = set(re.findall(r"^  ([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{\s*$", body, re.MULTILINE))
    return keys


def test_default_weights_match_frontend_defaults():
    py_keys = set(DEFAULT_WEIGHTS.keys())
    ts_keys = _extract_ts_default_keys()
    assert py_keys == ts_keys, (
        f"Frontend DEFAULT_FIT_WEIGHTS drifted from backend DEFAULT_WEIGHTS.\n"
        f"only in backend: {sorted(py_keys - ts_keys)}\n"
        f"only in frontend: {sorted(ts_keys - py_keys)}\n"
        f"Fix: update {_META_FILE} to add/remove the drifted key(s) + tooltip copy."
    )


def test_every_weight_has_a_meta_entry():
    """A weight without a META entry falls back to the raw key in the UI — a
    quiet regression. Force the label + hint + example to exist for every key."""
    py_keys = set(DEFAULT_WEIGHTS.keys())
    meta_keys = _extract_ts_meta_keys()
    missing = py_keys - meta_keys
    assert not missing, (
        f"FIT_WEIGHT_META is missing entries for: {sorted(missing)}. "
        f"Add label/trigger/hint/example/auto_outreach_effect in {_META_FILE}."
    )
