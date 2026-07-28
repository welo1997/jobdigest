"""Jobs.cz work-setup tags: hybrid must never be read as fully remote.

`remote_signal` is not a cosmetic field. `geo.location_predicate` exempts fully-remote
postings from the location gate and matches them on `remote_scope` instead, so a posting
wrongly flagged remote reaches subscribers who explicitly ruled that place out — the exact
failure the city-level preferences were built to prevent.

Jobs.cz spells the work setup as a graded Czech phrase, and the original check looked only
for the substring "z domova", which every grade contains. On live inventory (2026-07-28)
that made the flag wrong on 3 744 of 3 744 postings that carried it.
"""

from ingestion.sources.jobscz import _remote_from_body


def test_occasional_home_office_is_not_remote():
    # The single most common jobs.cz tag, and 3 463 live postings on the day this was found.
    assert _remote_from_body("Možnost občasné práce z domova") is False


def test_predominantly_from_home_is_not_remote():
    # "Predominantly" still expects on-site presence, so it must not bypass the city gate.
    # It stays retrievable for someone who chose that city — it just stops being exempt.
    assert _remote_from_body("Práce převážně z domova") is False


def test_qualifier_wins_over_remote_keyword_in_a_combined_tag_string():
    # Body tags arrive joined with "; ", so a qualified phrase shares the string with other
    # tags. A qualifier anywhere must lose the posting its exemption.
    assert _remote_from_body(
        "50 000 – 70 000 Kč; Odpověď do 2 týdnů; Možnost občasné práce z domova") is False


def test_unqualified_remote_still_counts():
    # The fix must not swing the other way: genuine remote work stays remote, or subscribers
    # with remote_scope set lose the postings the scope exists to find.
    assert _remote_from_body("Práce z domova") is True
    assert _remote_from_body("Home office") is True
    assert _remote_from_body("Remote") is True


def test_missing_tags_are_not_remote():
    assert _remote_from_body(None) is False
    assert _remote_from_body("") is False
