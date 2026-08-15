"""Remote in Europe: the multi-country scope is the point, and `;` is what makes it readable.

This adapter exists for `schema-loc` — a JSON array of countries, which almost nothing else in
the stack emits and which is exactly what `postings.reach_countries` and the EU-International
row are short of. The tests below are written to fail if that path breaks:

  - the country list is joined with **`;`**, because `geo.countries_in_scope` splits on it and
    resolves each entry independently. A comma is already spoken for *inside* one entry, and
    joining with one is the Printful bug: five countries collapse to whichever is named
    outright and the other four are dropped as merely implied.
  - the list never reaches `location`. `geo.resolve_location` takes the first country n-gram it
    finds, so folding it in re-homes the posting to whatever sorts first.
  - `country_code` is set only when the posting names exactly one country — it holds one value
    by construction, and guessing which of 42 to keep would be worse than leaving it empty.
"""

from __future__ import annotations

import json

from ingestion.sources.remoteineurope import (
    RemoteInEuropeSource,
    _countries,
    _fields,
    _parse_date,
    _text,
)


def _page(countries: list[str], **over) -> str:
    loc = json.dumps([{"@type": "Country", "name": c} for c in countries])
    fields = {
        "schema-title": "Account Executive",
        "schema-desc": "&lt;p&gt;Sell &lt;strong&gt;things&lt;/strong&gt;.&lt;/p&gt;",
        "schema-loc": loc,
        "schema-date": "May 15, 2026",
        "schema-valid": "Oct 02, 2026",
        "schema-company": "Typeform",
        "schema-url": "https://remoteineurope.com/job/account-executive",
    }
    fields.update(over)
    divs = "".join(f'<div id="{k}">{v}</div>' for k, v in fields.items())
    return f"<html><body>{divs}</body></html>"


def _norm(countries: list[str], url: str = "https://remoteineurope.com/job/x", **over):
    item = _fields(_page(countries, **over))
    item["_url"] = url
    return RemoteInEuropeSource().normalize([item])[0]


# -- the reason this source exists ---------------------------------------------------------


def test_several_countries_are_joined_with_a_semicolon():
    """Mutation check: change the join to ", " and this goes red — which is the Printful bug."""
    posting = _norm(["Germany", "Ireland", "Netherlands", "Portugal", "Spain"])
    assert posting.scope_raw == "Germany; Ireland; Netherlands; Portugal; Spain"
    assert ";" in posting.scope_raw
    # each entry must survive splitting on ';' as a standalone country name
    assert [p.strip() for p in posting.scope_raw.split(";")] == [
        "Germany", "Ireland", "Netherlands", "Portugal", "Spain"
    ]


def test_the_country_list_never_reaches_location():
    """`resolve_location` takes the first country n-gram, so this field must stay empty."""
    posting = _norm(["France", "Germany", "Italy"])
    assert posting.location is None


def test_country_code_is_set_only_when_exactly_one_country_is_named():
    assert _norm(["United Kingdom"]).country_code == "GB"
    assert _norm(["Germany"]).country_code == "DE"
    # several named → the column cannot hold them; scope_raw carries the list instead
    multi = _norm(["Germany", "Italy", "Malta"])
    assert multi.country_code is None
    assert multi.scope_raw == "Germany; Italy; Malta"


def test_an_unmapped_country_falls_through_rather_than_guessing():
    """`geo` is the one definition of geography; this map only answers "is it exactly one"."""
    posting = _norm(["Kazakhstan"])
    assert posting.country_code is None
    assert posting.scope_raw == "Kazakhstan"


def test_a_42_country_scope_survives_intact():
    """The real shape: Agorapulse and Supabase both publish the whole of Europe."""
    countries = [f"Country{i}" for i in range(42)]
    posting = _norm(countries)
    assert len(posting.scope_raw.split(";")) == 42


# -- parsing -------------------------------------------------------------------------------


def test_countries_tolerates_junk():
    assert _countries(None) == []
    assert _countries("") == []
    assert _countries("not json") == []
    assert _countries('{"name":"France"}') == []          # object, not a list
    assert _countries('[{"name":"France"},{"name":"France"}]') == ["France"]  # deduped


def test_dates_parse_from_the_display_format():
    assert _parse_date("May 22, 2026").isoformat() == "2026-05-22"
    assert _parse_date("November 02, 2026").isoformat() == "2026-11-02"
    assert _parse_date("2026-05-22").isoformat() == "2026-05-22"
    assert _parse_date("not a date") is None
    assert _parse_date(None) is None


def test_the_description_is_unescaped_then_stripped():
    """The divs store HTML as entities so a script can inject it; one unescape, then strip."""
    posting = _norm(["Spain"])
    assert posting.description == "Sell things."


def test_text_keeps_list_structure():
    assert "•" in _text("<ul><li>One</li><li>Two</li></ul>")


def test_remote_signal_is_claimed_because_the_board_is_remote_only():
    assert _norm(["Spain"]).remote_signal is True


def test_a_page_without_a_title_is_dropped():
    src = RemoteInEuropeSource()
    item = _fields(_page(["Spain"], **{"schema-title": ""}))
    item["_url"] = "https://remoteineurope.com/job/x"
    assert src.normalize([item]) == []
