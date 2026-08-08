"""MPSV register: no personal data escapes, and the streaming parser cannot desynchronise.

The privacy tests here are the point of the file. MPSV's own licence metadata declares
`obsahuje-osobní-údaje`, and every record carries a named human with a direct email and
phone in `prvniKontaktSeZamestnavatelem`. JobDigest's privacy policy is a specification
(CLAUDE.md security rule 4) and promises nothing about third-party recruiter contacts, so
that data must never reach `postings` — not because storing it would break a test, but
because it would make the published policy false.

The parser tests exist because a hand-rolled incremental JSON reader that loses sync does
not crash; it yields *wrong records*, or silently stops early, and the symptom downstream is
a source that returned a plausible number.
"""

from __future__ import annotations

import json

from ingestion.base import make_posting_id
from ingestion.sources.mpsv import (
    ISCO_MAJOR_KEEP,
    _CHUNK,
    MpsvSource,
    _iter_objects,
    _isco_major,
    _scrub,
)


def _chunks(text: str, size: int) -> list[bytes]:
    raw = text.encode("utf-8")
    return [raw[i:i + size] for i in range(0, len(raw), size)]


def _doc(items: list[dict]) -> str:
    return json.dumps({"nejakyKlic": 1, "polozky": items}, ensure_ascii=False)


# --- personal data -------------------------------------------------------------------

def test_contact_block_never_reaches_a_posting():
    """The named contact, their email and their phone must not survive normalisation."""
    item = {
        "portalId": 123,
        "pozadovanaProfese": {"cs": "Vývojář softwaru"},
        "zamestnavatel": {"nazev": "Acme s.r.o.", "ico": "12345678"},
        "upresnujiciInformace": {"cs": "Hledáme vývojáře."},
        "profeseCzIsco": {"id": "CzIsco/25120"},
        "prvniKontaktSeZamestnavatelem": {
            "komuSeHlasit": {
                "jmeno": "Michaela", "prijmeni": "Hermanová",
                "email": "michaela.hermanova@acme.cz", "telefon": "608123456",
            },
        },
    }
    posting = MpsvSource().normalize([item])[0]
    blob = json.dumps(posting.__dict__, ensure_ascii=False, default=str)
    for secret in ("Michaela", "Hermanová", "michaela.hermanova@acme.cz", "608123456"):
        assert secret not in blob, f"personal data leaked into the posting: {secret}"


def test_description_contact_details_are_scrubbed():
    """Employers paste recruiter emails and mobiles into the free-text field."""
    text = ("V případě zájmu volejte na 608 123 456 nebo pište na jan.novak@firma.cz, "
            "případně +420 777 888 999.")
    out = _scrub(text)
    assert "608 123 456" not in out
    assert "jan.novak@firma.cz" not in out
    assert "777 888 999" not in out
    assert "V případě zájmu" in out, "scrubbing must not destroy the surrounding text"


def test_scrub_leaves_ordinary_numbers_alone():
    """A salary or a year is not a phone number — over-scrubbing would gut descriptions."""
    out = _scrub("Nástupní mzda 45000 Kč, firma založena 1998.")
    assert "45000" in out and "1998" in out


# --- ISCO filter ---------------------------------------------------------------------

def test_only_white_collar_isco_groups_are_kept():
    keep = [{"profeseCzIsco": {"id": "CzIsco/25120"}},   # 2 professionals
            {"profeseCzIsco": {"id": "CzIsco/13300"}},   # 1 managers
            {"profeseCzIsco": {"id": "CzIsco/35210"}}]   # 3 technicians
    drop = [{"profeseCzIsco": {"id": "CzIsco/91120"}},   # 9 elementary
            {"profeseCzIsco": {"id": "CzIsco/51200"}},   # 5 service
            {"profeseCzIsco": {}}]                       # unknown
    assert all(_isco_major(i) in ISCO_MAJOR_KEEP for i in keep)
    assert not any(_isco_major(i) in ISCO_MAJOR_KEEP for i in drop)


def test_posting_without_title_or_id_is_skipped():
    """`make_posting_id` needs a URL, and a titleless row is not a job ad."""
    items = [{"portalId": 1, "pozadovanaProfese": {"cs": ""}},
             {"pozadovanaProfese": {"cs": "Analytik"}}]
    assert MpsvSource().normalize(items) == []


# --- streaming parser ----------------------------------------------------------------

def test_parser_yields_every_record_across_chunk_boundaries():
    items = [{"portalId": i, "text": "x" * 40} for i in range(50)]
    for size in (7, 64, 512, 100_000):
        got = list(_iter_objects(iter(_chunks(_doc(items), size))))
        assert [g["portalId"] for g in got] == list(range(50)), f"chunk size {size}"


def test_parser_survives_braces_and_quotes_inside_strings():
    """A `}` inside a description must not close the record early."""
    items = [
        {"portalId": 1, "upresnujiciInformace": {"cs": 'Používáme {json} a "uvozovky"'}},
        {"portalId": 2, "upresnujiciInformace": {"cs": 'Cesta C:\\\\dir\\\\ a } navíc'}},
        {"portalId": 3, "upresnujiciInformace": {"cs": "escaped quote: \" then }"}},
    ]
    got = list(_iter_objects(iter(_chunks(_doc(items), 13))))
    assert [g["portalId"] for g in got] == [1, 2, 3]
    assert got[0]["upresnujiciInformace"]["cs"] == 'Používáme {json} a "uvozovky"'


def test_parser_does_not_corrupt_czech_characters_split_across_chunks():
    """Chunk-at-a-time `bytes.decode` mangles a codepoint straddling the boundary."""
    items = [{"portalId": 1, "pozadovanaProfese": {"cs": "Příliš žluťoučký kůň úpěl ďábelské ódy"}}]
    doc = _doc(items)
    for size in range(1, 12):                    # every boundary through the multi-byte run
        got = list(_iter_objects(iter(_chunks(doc, size))))
        assert got[0]["pozadovanaProfese"]["cs"] == "Příliš žluťoučký kůň úpěl ďábelské ódy", size
        assert "\ufffd" not in json.dumps(got, ensure_ascii=False)


def test_parser_is_not_quadratic():
    """Coarse performance smoke test: 25 000 records must parse quickly.

    **Weaker than the other guards in this file, and labelled so deliberately.** The first
    implementation of `_iter_objects` did not finish a real 39 000-record run inside ten
    minutes, and the rewrite does it in 35 seconds — but three attempts to reintroduce that
    behaviour as a mutation all stayed green here (two were linear after all, one was a
    behavioural no-op), so this does *not* demonstrably catch the original bug. It pins the
    order of magnitude and nothing more. Treat a failure as real; do not read a pass as proof
    the parser is still linear.
    """
    import time
    items = [{"portalId": i, "pad": "y" * 200} for i in range(25_000)]
    doc = _doc(items)
    started = time.monotonic()
    got = list(_iter_objects(iter(_chunks(doc, _CHUNK))))
    assert len(got) == 25_000
    assert time.monotonic() - started < 10.0, "parser has gone quadratic again"


def test_fetch_applies_the_isco_filter(tmp_path, monkeypatch):
    """The filter must bite in `fetch`, not merely be computable by `_isco_major`.

    Asserting on the helper alone left the real path untested: deleting the condition in
    `fetch` kept every test green while the source loaded all 39 063 rows, manual work
    included.
    """
    doc = _doc([
        {"portalId": 1, "pozadovanaProfese": {"cs": "Vývojář"},
         "profeseCzIsco": {"id": "CzIsco/25120"}},                      # keep
        {"portalId": 2, "pozadovanaProfese": {"cs": "Pomocný kuchař"},
         "profeseCzIsco": {"id": "CzIsco/94120"}},                      # drop
        {"portalId": 3, "pozadovanaProfese": {"cs": "Uklízeč"},
         "profeseCzIsco": {"id": "CzIsco/91120"}},                      # drop
    ])
    path = tmp_path / "vm.json"
    path.write_text(doc, encoding="utf-8")
    monkeypatch.setattr(MpsvSource, "_download", lambda self: str(path))
    monkeypatch.setattr("ingestion.sources.mpsv.politeness.robots_allows", lambda url: True)

    kept = MpsvSource().fetch()
    assert [k["portalId"] for k in kept] == [1]


# --- normalisation -------------------------------------------------------------------

def test_country_is_czechia_and_salary_is_czk():
    item = {"portalId": 9, "pozadovanaProfese": {"cs": "Datový analytik"},
            "zamestnavatel": {"nazev": "Firma a.s."},
            "profeseCzIsco": {"id": "CzIsco/25120"},
            "mesicniMzdaOd": 50000, "mesicniMzdaDo": 70000,
            "datumVlozeni": "2026-07-01T00:00:00.000Z"}
    p = MpsvSource().normalize([item])[0]
    assert p.country_code == "CZ"
    assert p.currency == "CZK" and "50000" in p.salary_raw
    assert p.posted_at.isoformat() == "2026-07-01"
    assert p.posting_id


def test_url_is_the_fragment_route_because_the_query_form_is_not_a_route_at_all():
    """`?id=` renders the portal's empty search page — the app routes on the fragment.

    This adapter emitted `?id={portal_id}` from the day it was written, on the strength of
    the id being stable and unique, with the comment saying in as many words that whether
    the app honours it was **unverified**. It does not: `up.gov.cz` is client-rendered and
    its router reads `#/volna-mista-detail/{id}`. Every MPSV link ever emailed was dead, and
    nothing downstream could see it — a stored URL is never fetched again, so it surfaced
    only when a subscriber clicked one on 2026-08-08 and landed nowhere.
    """
    p = MpsvSource().normalize([{"portalId": 67251104,
                                 "pozadovanaProfese": {"cs": "Datový analytik"}}])[0]
    assert p.url == "https://up.gov.cz/volna-mista-v-cr#/volna-mista-detail/67251104"


def test_posting_id_still_hashes_the_id_only_url_so_the_link_fix_churns_nothing():
    """Byte-identical to what every run before the fix stored.

    `posting_id = md5(url)`, so hashing the corrected link would re-create all ~7 300 MPSV
    rows under fresh ids — two active copies of every Czech vacancy competing for shortlist
    slots until the originals age out, which is the startupjobs 2026-08-06 churn brought on
    deliberately. Hashing the id-only form instead lets `upsert_postings` repair `url` in
    place on the next ingest and change nothing else.
    """
    p = MpsvSource().normalize([{"portalId": 67251104,
                                 "pozadovanaProfese": {"cs": "Datový analytik"}}])[0]
    assert p.posting_id == make_posting_id(
        "https://up.gov.cz/volna-mista-v-cr?id=67251104")


def test_remote_signal_is_never_invented():
    """The register has no remote field; guessing one is the 2026-07-28 bug again."""
    item = {"portalId": 3, "pozadovanaProfese": {"cs": "Vývojář"},
            "upresnujiciInformace": {"cs": "Možnost občasné práce z domova."},
            "profeseCzIsco": {"id": "CzIsco/25120"}}
    assert MpsvSource().normalize([item])[0].remote_signal is None
