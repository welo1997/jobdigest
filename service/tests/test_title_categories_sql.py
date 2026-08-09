"""The title cache, executed against a real Postgres.

`test_categorize_exchange.py` pins the trust boundary against a fake store, which proves what
is dropped and nothing about what is stored. Three things can only be wrong in SQL:

- **`title_category_map` must exclude recorded declines and `asked_title_keys` must keep
  them.** They are the same table read for two different questions — "do we know?" and "have
  we asked?" — and collapsing them either re-asks about every unreadable title forever or
  carries tens of thousands of null answers into every ingest.
- **Re-answering a title updates it rather than raising**, so a re-import after a taxonomy
  change is an import and not a manual delete.
- **`uncategorised_titles` must return only the live residue**, ordered by how much inventory
  each title stands for — that ordering is what makes a truncated export honest.

Same harness and reason as `test_geo_sql.py` — see its docstring for the throwaway database
recipe. Skipped when TEST_DATABASE_URL is unset, and the CI job fails if these skip.
"""

from __future__ import annotations

import os

import pytest

from service import categorize_exchange as cx
from service import store, taxonomy

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "titlecache-"


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        cur.execute("delete from title_categories where title_key like %s", (f"{PREFIX}%",))
        cur.execute("delete from postings where posting_id like %s", (f"{PREFIX}%",))
        # Three postings share one title, one has another, one is already classified: the
        # residue query must see the first two and not the third.
        for pid, title, category in [
            (f"{PREFIX}1", "Ställare", taxonomy.UNCATEGORISED),
            (f"{PREFIX}2", "Ställare", taxonomy.UNCATEGORISED),
            (f"{PREFIX}3", "Ställare", taxonomy.UNCATEGORISED),
            (f"{PREFIX}4", "Extrajobb", taxonomy.UNCATEGORISED),
            (f"{PREFIX}5", "Data Analyst", "data_analysis"),
        ]:
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, region, "
                "  eligibility, seniority, work_type, role_category, dedup_key, is_active) "
                "values (%s,'test',%s,%s,'Test','se','eligible','mid','permanent',%s,%s,true) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://example.com/{pid}", title, category, pid))
    yield
    with store.cursor(commit=True) as cur:
        cur.execute("delete from title_categories where title_key like %s", (f"{PREFIX}%",))
        cur.execute("delete from postings where posting_id like %s", (f"{PREFIX}%",))
    if previous_dsn:
        os.environ["DATABASE_URL"] = previous_dsn
    store._POOL = None


def _rows(*pairs):
    return [(f"{PREFIX}{k}", c, k) for k, c in pairs]


def test_a_decline_is_remembered_but_never_answered_with():
    """The two questions this table answers are different, and reading it one way for both is
    how the residue either stops shrinking or bloats every ingest."""
    store.upsert_title_categories(_rows(("known", "sales"), ("declined", taxonomy.UNCATEGORISED)))

    answers = store.title_category_map()
    assert answers.get(f"{PREFIX}known") == "sales"
    assert f"{PREFIX}declined" not in answers, "a decline is not an answer"

    asked = store.asked_title_keys()
    assert {f"{PREFIX}known", f"{PREFIX}declined"} <= asked, "a decline was still asked about"


def test_re_answering_a_title_updates_it_rather_than_raising():
    store.upsert_title_categories(_rows(("moved", "sales")))
    store.upsert_title_categories(_rows(("moved", "marketing")))
    assert store.title_category_map()[f"{PREFIX}moved"] == "marketing"
    with store.cursor() as cur:
        cur.execute("select count(*) as n from title_categories where title_key = %s",
                    (f"{PREFIX}moved",))
        assert cur.fetchone()["n"] == 1


def test_the_residue_query_returns_live_uncategorised_titles_commonest_first():
    residue = {r["title"]: r["n"] for r in store.uncategorised_titles()}
    assert residue.get("Ställare") == 3
    assert residue.get("Extrajobb") == 1
    assert "Data Analyst" not in residue, "an already-classified posting is not residue"

    ordering = [r["title"] for r in store.uncategorised_titles()]
    assert ordering.index("Ställare") < ordering.index("Extrajobb")


def test_the_export_asks_about_a_title_once_and_never_twice(tmp_path):
    """End to end against the real table: the export's exclusion is the stored key, so a
    normalisation change on one side and not the other shows up here rather than as a cache
    that quietly stops hitting."""
    with store.cursor(commit=True) as cur:
        cur.execute("delete from title_categories where title_key like %s", (f"{PREFIX}%",))
        cur.execute("delete from title_categories where title_key in %s",
                    ((cx.normalise_title("Ställare"), cx.normalise_title("Extrajobb")),))

    path = tmp_path / "titles.json"
    first = cx.export_uncategorised(str(path))
    assert first >= 2

    # Written the way a validated import writes them: one answer, one recorded decline.
    store.upsert_title_categories([
        (cx.normalise_title("Ställare"), "manufacturing_production", "Ställare"),
        (cx.normalise_title("Extrajobb"), taxonomy.UNCATEGORISED, "Extrajobb"),
    ])
    second = cx.export_uncategorised(str(path))
    assert second == first - 2, "an answered title, and a declined one, are both done"
