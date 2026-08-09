"""Migration 018 (the other_tech_function split), executed against a real Postgres.

The migration mutates *subscriber data* — `profiles.role_categories`, a stored preference — so
being wrong here is not a stale dataset, it is a real person's digest silently losing or gaining
categories they never chose. The three ways it can be wrong are each pinned below:

  - **not additive** — dropping `other_tech_function` would silently remove the residual bucket a
    subscriber currently matches on (the standing "nothing may vanish" rule);
  - **grant-all instead of chip-faithful** — adding anything beyond `marketing` and
    `finance_accounting` hands a subscriber categories the old chips never mapped to;
  - **not idempotent** — a migration that duplicates on re-run, or that a re-apply keeps
    changing, cannot be safely replayed on the box.

The test applies the real migration *file*, not a re-typed copy, so a future edit to the SQL is
what is under test. Same throwaway-Postgres harness as `test_geo_sql.py`; skipped when
TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

MIGRATION = (
    Path(__file__).resolve().parents[1] / "db" / "migration_018_other_tech_function_split.sql"
)

PREFIX = "rolemig-test-"
# (email suffix, starting role_categories, expected role_categories after migration)
CASES = [
    # Holds other_tech_function alone-ish: gains both successors, keeps otf and social_media.
    ("a", ["other_tech_function", "social_media"],
     {"other_tech_function", "social_media", "marketing", "finance_accounting"}),
    # A tech profile that does NOT hold otf: must be left completely untouched.
    ("b", ["product", "data_analysis"],
     {"product", "data_analysis"}),
    # Already holds marketing: gains only finance_accounting, marketing not duplicated.
    ("c", ["other_tech_function", "marketing"],
     {"other_tech_function", "marketing", "finance_accounting"}),
]


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        for suffix, start, _ in CASES:
            cur.execute(
                "insert into profiles (email, role_categories) values (%s, %s)",
                (f"{PREFIX}{suffix}@test", start),
            )
    try:
        yield
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from profiles where email like %s", (PREFIX + "%",))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def _apply_migration() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    with store.cursor(commit=True) as cur:
        cur.execute(sql)


def _categories() -> dict[str, list[str]]:
    # store.cursor() yields RealDictCursor rows, so columns are keyed by name.
    with store.cursor() as cur:
        cur.execute(
            "select email, role_categories from profiles where email like %s",
            (PREFIX + "%",),
        )
        return {row["email"]: row["role_categories"] for row in cur.fetchall()}


def test_migration_is_chip_faithful_and_additive():
    _apply_migration()
    got = _categories()
    for suffix, _, expected in CASES:
        email = f"{PREFIX}{suffix}@test"
        assert set(got[email]) == expected, f"{email}: {got[email]!r} != {expected!r}"
        # No duplicates introduced (array append is not a set operation).
        assert len(got[email]) == len(set(got[email])), f"{email} has duplicate categories"


def test_migration_is_idempotent():
    # A migration applied on the box may be re-run; a second pass must change nothing.
    _apply_migration()
    first = _categories()
    _apply_migration()
    second = _categories()
    for email, cats in first.items():
        assert sorted(second[email]) == sorted(cats), f"{email} changed on re-apply"
        assert len(second[email]) == len(set(second[email])), f"{email} gained a duplicate"


def test_non_holders_are_untouched():
    _apply_migration()
    got = _categories()
    # Profile "b" never held other_tech_function and must not have gained marketing/finance.
    b = set(got[f"{PREFIX}b@test"])
    assert "marketing" not in b and "finance_accounting" not in b
    assert b == {"product", "data_analysis"}
