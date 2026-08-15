"""NVA (Latvia): the contact block never reaches the corpus, and the register is gated.

Two things carry risk here and both are tested to fail rather than to pass:

  - **Personal data.** Every record has `kontaktpersona` / `kontaktpersonas_epasts` /
    `kontaktpersonas_talrunis` naming a person and their address. Those fields are never read,
    and `_scrub` removes what employers paste into the description body.
  - **Dilution.** The register is Latvia's whole labour market — general labourer, shop
    assistant, truck driver, cook, nurse are its five commonest professions. `KEEP_FIELDS`
    bounds it, and the widened shortlist path (which drops the recall predicate) is what makes
    that load-bearing rather than tidy.

Not to be confused with `test_nav.py`, which is Norway.
"""

from __future__ import annotations

from ingestion.sources.nva import (
    FIELD_CATEGORIES,
    JOB_URL,
    KEEP_FIELDS,
    NvaSource,
    _city,
    _scrub,
    _text,
)

IT = "Informācijas tehnoloģijas / Telekomunikācijas"
NURSING = "Veselības aprūpe / Sociālā aprūpe"


def _record(**over) -> dict:
    rec = {
        "id": 461007832,
        "profesija": "PROGRAMMĒTĀJS",
        "uznemums": "SIA Starfish",
        "darb_joma": IT,
        "darba_apraksts": "<p>Izstrādāt <strong>Python</strong> risinājumus.</p>",
        "adrese": "LATVIJA, Brīvības iela 1, Rīga",
        "alga_no_lidz": "2000-2500",
        "publicesanas_datums": "2026-08-14",
        "kontaktpersona": "CV sūtīt uz: darbs@starfish.lv",
        "kontaktpersonas_epasts": "darbs@starfish.lv",
        "kontaktpersonas_talrunis": "+371 20123456",
        "ir_attalinati_veicams_darbs": None,
    }
    rec.update(over)
    return rec


def _norm(**over):
    posts = NvaSource().normalize([_record(**over)])
    return posts[0] if posts else None


# -- personal data --------------------------------------------------------------------------


def test_the_structured_contact_block_is_never_read():
    """Mutation check: append `item.get("kontaktpersona")` to the description and this reddens."""
    posting = _norm()
    blob = " ".join(filter(None, [posting.description, posting.title, posting.company,
                                  posting.location, posting.salary_raw]))
    assert "darbs@starfish.lv" not in blob
    assert "20123456" not in blob
    assert "CV sūtīt uz" not in blob


def test_a_contact_pasted_into_the_description_is_scrubbed():
    """The half the field-level omission cannot reach."""
    posting = _norm(darba_apraksts="<p>Sīkāk: CV sūtīt uz info@starfish.lv vai 67021206.</p>")
    assert "info@starfish.lv" not in posting.description
    assert "67021206" not in posting.description
    assert "[kontakti noņemti]" in posting.description


def test_scrub_leaves_ordinary_numbers_and_text_alone():
    text = "Alga 2000-2500 EUR, 40 stundas nedēļā, sākot ar 2026. gadu."
    assert _scrub(text) == text


# -- the dilution gate ----------------------------------------------------------------------


def test_a_vacancy_outside_the_kept_fields_is_dropped():
    """A nurse is a real job and not one this corpus can match. Dropped at normalize."""
    assert NvaSource().normalize([_record(darb_joma=NURSING, profesija="MĀSA")]) == []


def test_every_kept_field_is_a_string_nva_actually_emits():
    """A typo here silently keeps nothing — the failure mode is an empty source, not an error."""
    for value in KEEP_FIELDS:
        assert isinstance(value, str) and value.strip() == value
        assert "/" in value or value == "Ražošana" or " " in value


def test_the_gate_is_injectable_so_a_check_run_can_widen_it():
    src = NvaSource(keep_fields=frozenset({NURSING}))
    assert len(src.normalize([_record(darb_joma=NURSING)])) == 1
    assert src.normalize([_record(darb_joma=IT)]) == []


def test_category_hints_are_real_taxonomy_members():
    """`classify` discards an unknown hint, so a typo means uncategorised, not an error."""
    from service import taxonomy
    for value in FIELD_CATEGORIES.values():
        assert value in taxonomy.CATEGORIES, f"{value!r} is not a taxonomy category"


# -- the link -------------------------------------------------------------------------------


def test_the_url_carries_the_fragment_route():
    """`cvvp` is an AngularJS app with no html5Mode: without `#/pub/vakances/` it resolves to
    the app's own empty page, which is the MPSV `?id=` failure exactly."""
    posting = _norm()
    assert posting.url == "https://cvvp.nva.gov.lv/#/pub/vakances/461007832"
    assert "#/pub/vakances/" in JOB_URL


def test_posting_id_is_stable_and_per_vacancy():
    a, b = _norm(id=1), _norm(id=2)
    assert a.posting_id != b.posting_id
    assert a.posting_id == _norm(id=1).posting_id


# -- fields ---------------------------------------------------------------------------------


def test_fields_are_normalised():
    posting = _norm()
    assert posting.title == "PROGRAMMĒTĀJS"
    assert posting.company == "SIA Starfish"
    assert posting.country_code == "LV"
    assert posting.salary_raw == "2000-2500"
    assert posting.currency == "EUR"
    assert posting.posted_at.isoformat() == "2026-08-14"
    assert posting.source_category == "engineering"
    assert posting.description == "Izstrādāt Python risinājumus."


def test_city_drops_the_country_token_and_leaves_the_rest_to_geo():
    assert _city("LATVIJA, Jumaras iela 195, Valmiera, Valmieras nov.") == \
        "Jumaras iela 195, Valmiera, Valmieras nov."
    assert _city("Rīga") == "Rīga"
    assert _city(None) is None


def test_remote_is_claimed_only_when_the_register_says_so():
    assert _norm().remote_signal is None
    assert _norm(ir_attalinati_veicams_darbs=False).remote_signal is None
    assert _norm(ir_attalinati_veicams_darbs=True).remote_signal is True
    # partially remote is hybrid, which is not remote
    assert _norm(ir_daleji_attalinati_veicams_darbs=True).remote_signal is None


def test_no_salary_means_no_currency():
    posting = _norm(alga_no_lidz=None)
    assert posting.salary_raw is None and posting.currency is None


def test_a_record_without_a_title_is_dropped():
    assert NvaSource().normalize([_record(profesija="")]) == []
    assert NvaSource().normalize([_record(id=None)]) == []


def test_text_flattens_word_pasted_html():
    assert _text("<div><p>A</p><ul><li>B</li></ul></div>").replace("\n", " ") == "A • B"
    assert _text(None) is None
    assert _text("") is None
