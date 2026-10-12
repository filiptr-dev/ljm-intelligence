"""Parser-only tests for the keyless EIA public-page scraper.

No network, no DB — we feed `_parse_latest` a trimmed sample of the real
page so the regexes stay honest if the markup drifts.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.integrations.adapters.eia.scraper import _parse_latest

# Trimmed from https://www.eia.gov/dnav/pet/pet_pri_gnd_dcus_r1x_w.htm —
# six Series5 headers plus one Diesel row with five DataB cells and the
# newest-week Current2 cell. Keeps the test tight; no need for 20 KB.
_SAMPLE = """
<tr>
  <th class="Series5">08/31/26</th>
  <th class="Series5">09/07/26</th>
  <th class="Series5">09/14/26</th>
  <th class="Series5">09/21/26</th>
  <th class="Series5">09/28/26</th>
  <th class="Series5">10/05/26</th>
</tr>
<tr class="DataRow">
  <td class="DataStub1"><B>Diesel (On-Highway) - All Types<B></td>
  <td class="DataB">5.736</td>
  <td class="DataB">5.990</td>
  <td class="DataB">6.202</td>
  <td class="DataB">6.517</td>
  <td class="DataB">6.510</td>
  <td class="Current2">6.476</td>
</tr>
"""


def test_parses_latest_week_and_price() -> None:
    got = _parse_latest(_SAMPLE)
    assert got is not None
    week_of, price = got
    assert week_of == date(2026, 10, 5)
    assert price == Decimal("6.476")


def test_missing_diesel_row_returns_none() -> None:
    html = '<tr><th class="Series5">10/05/26</th></tr>'
    assert _parse_latest(html) is None


def test_bogus_price_cell_returns_none() -> None:
    bad = _SAMPLE.replace(">6.476<", ">N/A<")
    assert _parse_latest(bad) is None
