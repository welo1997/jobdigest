"""Tests for the matcher's file exchange — the one place untrusted input reaches the DB.

`picks.json` is written by a claude.ai routine and travels via cloud storage, so it sits
outside our trust boundary: whoever can write that file is proposing what lands in a
subscriber's inbox. `import_picks` is what bounds the damage, and these tests pin the
guarantees it makes, so a refactor can't quietly widen them:

    - a pick can only be attributed to a real subscriber   (profile_id validated)
    - a pick can only reference a real, active posting     (posting_id validated)
    - scores are clamped and the floor is enforced
    - one malformed or failing entry cannot cost everyone else their digest

Plus the matching export guarantee: `shortlists.json` carries no email addresses.

The store is faked rather than mocked loosely — the fake enforces the same "is this row
real?" semantics Postgres would, so a test passing here means the validation logic works,
not just that a mock was called.
"""

import json

import pytest

from service import geo, matcher

REAL = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
GHOST = "99999999-9999-9999-9999-999999999999"   # well-formed uuid, not a subscriber


class _FakeCursor:
    """Doubles as the context manager and the cursor, matching `with store.cursor() as cur`."""

    def __init__(self, active_postings: set[str]):
        self._active = active_postings
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        # The only query import_picks issues: which of these posting_ids are active?
        requested = list(params[0])
        self._rows = [{"posting_id": p} for p in requested if p in self._active]

    def fetchall(self):
        return self._rows


class _FakeStore:
    """Stands in for service.store — records writes instead of touching Postgres."""

    def __init__(self, profiles: set[str], active_postings: set[str]):
        self._profiles = profiles
        self._active = active_postings
        self.written: list[tuple] = []
        self.fail_on_profile: str | None = None
        self.profiles_out: list[dict] = []
        self.shortlist_out: list[dict] = []
        self.runs: list[dict] = []
        # Profiles today's export put candidates in front of the matcher for. Empty by
        # default so the coverage check is a no-op for every test that isn't about it.
        self.exported: set[str] = set()

    # -- used by import_picks --
    def existing_profile_ids(self, ids):
        return {str(i) for i in ids if str(i) in self._profiles}

    def cursor(self, commit: bool = False):
        return _FakeCursor(self._active)

    def upsert_match(self, profile_id, posting_id, score, summary):
        if profile_id == self.fail_on_profile:
            raise RuntimeError("simulated DB failure")
        self.written.append((profile_id, posting_id, score, summary))

    # -- used by export_shortlists --
    def sendable_profiles(self):
        return self.profiles_out

    def query_shortlist(self, profile, limit=120):
        return self.shortlist_out

    def query_shortlist_meta(self, profile, limit=120):
        return self.shortlist_out, {"n": len(self.shortlist_out),
                                    "n_narrow": len(self.shortlist_out), "widened": False}

    def already_sent_ids(self, profile_id):
        return set()

    # -- diagnostics (digest_runs); recorded so a test can assert it was written --
    def record_digest_run(self, profile_id, **fields):
        self.runs.append({"profile_id": profile_id, **fields})

    def exported_profile_ids(self):
        return set(self.exported)


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore(profiles={REAL, OTHER}, active_postings={"real-a", "real-b", "real-c"})
    monkeypatch.setattr(matcher, "store", fake)
    # Pin the floor so an env override can't change what these tests assert.
    monkeypatch.setattr(matcher, "MATCH_FLOOR", 4)
    return fake


def _write(tmp_path, payload) -> str:
    path = tmp_path / "picks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------- import_picks --

def test_drops_picks_for_a_profile_that_does_not_exist(store, tmp_path):
    """The core guarantee: picks cannot be attributed to an invented subscriber."""
    path = _write(tmp_path, {"picks": [
        {"profile_id": GHOST, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "x"}]},
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "ok"}]},
    ]})

    assert matcher.import_picks(path) == 1
    assert store.written == [(REAL, "real-a", 9, "ok")]


def test_drops_postings_that_are_not_active(store, tmp_path):
    path = _write(tmp_path, {"picks": [{"profile_id": REAL, "jobs": [
        {"posting_id": "real-a", "score": 9, "reason": "ok"},
        {"posting_id": "invented", "score": 9, "reason": "not in the DB"},
    ]}]})

    assert matcher.import_picks(path) == 1
    assert [w[1] for w in store.written] == ["real-a"]


def test_clamps_score_and_enforces_the_floor(store, tmp_path):
    path = _write(tmp_path, {"picks": [{"profile_id": REAL, "jobs": [
        {"posting_id": "real-a", "score": 99, "reason": "clamped down"},
        {"posting_id": "real-b", "score": matcher.MATCH_FLOOR - 1, "reason": "below floor"},
        {"posting_id": "real-c", "score": -5, "reason": "clamped up, still below floor"},
    ]}]})

    assert matcher.import_picks(path) == 1
    assert store.written == [(REAL, "real-a", 10, "clamped down")]


def test_truncates_the_reason(store, tmp_path):
    path = _write(tmp_path, {"picks": [{"profile_id": REAL, "jobs": [
        {"posting_id": "real-a", "score": 9, "reason": "x" * 500},
    ]}]})

    matcher.import_picks(path)
    assert len(store.written[0][3]) == 280


@pytest.mark.parametrize("jobs", [
    "not-a-list",
    [{"posting_id": "real-a", "score": "not-a-number"}],
    [{"score": 9, "reason": "no posting_id"}],
    ["not-a-dict"],
    [],
])
def test_malformed_jobs_are_skipped_not_fatal(store, tmp_path, jobs):
    path = _write(tmp_path, {"picks": [
        {"profile_id": REAL, "jobs": jobs},
        {"profile_id": OTHER, "jobs": [{"posting_id": "real-b", "score": 8, "reason": "ok"}]},
    ]})

    # The healthy entry must still land — one bad record cannot cost everyone their digest.
    assert matcher.import_picks(path) == 1
    assert store.written == [(OTHER, "real-b", 8, "ok")]


def test_a_failing_profile_does_not_abort_the_rest(store, tmp_path):
    """A DB error mid-import is contained; later subscribers still get their picks."""
    store.fail_on_profile = REAL
    path = _write(tmp_path, {"picks": [
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "boom"}]},
        {"profile_id": OTHER, "jobs": [{"posting_id": "real-b", "score": 7, "reason": "ok"}]},
    ]})

    assert matcher.import_picks(path) == 1
    assert store.written == [(OTHER, "real-b", 7, "ok")]


def test_accepts_a_bare_list_payload(store, tmp_path):
    path = _write(tmp_path, [
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 6, "reason": "ok"}]},
    ])

    assert matcher.import_picks(path) == 1


def test_rejects_a_payload_that_is_not_a_list(store, tmp_path):
    path = _write(tmp_path, {"picks": {"profile_id": REAL}})

    assert matcher.import_picks(path) == 0
    assert store.written == []


# ------------------------------------------------------------- picks coverage ---
# The failure these pin is not a malformed file — it is a *complete* one that covers only
# some subscribers. `import_picks` iterates whatever entries exist, so it imports cleanly and
# exits 0; the uncovered profiles get no picks_n, no email, and no error. The freshness and
# existence checks in jobdigest-match.sh both pass on a truncated file, and the watchdog needs
# three days *and* excludes profiles younger than that — so a new subscriber's first three days
# of silence are invisible by design. This is the one check that can see it on the day.


def test_a_profile_missing_from_the_picks_file_is_reported(store, tmp_path):
    store.exported = {REAL, OTHER}
    path = _write(tmp_path, {"picks": [
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "ok"}]},
    ]})

    assert matcher.coverage_gap(path) == [OTHER]
    assert matcher.report_coverage(path) == 1


def test_no_gap_when_every_exported_profile_is_mentioned(store, tmp_path):
    store.exported = {REAL, OTHER}
    path = _write(tmp_path, {"picks": [
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "ok"}]},
        {"profile_id": OTHER, "jobs": [{"posting_id": "real-b", "score": 8, "reason": "ok"}]},
    ]})

    assert matcher.coverage_gap(path) == []


def test_a_profile_the_model_rejected_counts_as_covered(store, tmp_path):
    """The distinction the whole check exists to make. OTHER was considered and every
    candidate scored below the floor, so nothing is written to `matches` — that is the
    matcher doing its job on a thin day, not a subscriber who was skipped. Conflating the
    two is what makes `picks_n` useless for this: the column is `not null default 0`, so
    both cases store 0."""
    store.exported = {REAL, OTHER}
    path = _write(tmp_path, {"picks": [
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "ok"}]},
        {"profile_id": OTHER, "jobs": [
            {"posting_id": "real-b", "score": matcher.MATCH_FLOOR - 1, "reason": "weak"}]},
    ]})

    assert matcher.import_picks(path) == 1          # nothing stored for OTHER
    assert matcher.coverage_gap(path) == []         # ...but they were not skipped


def test_an_unreadable_file_reports_the_whole_export_as_uncovered(store, tmp_path):
    """Truncated mid-write, or never written. Reporting "no gap" here would be the worst
    possible answer — it is the case where nobody got anything."""
    store.exported = {REAL, OTHER}
    path = tmp_path / "picks.json"
    path.write_text('{"picks": [{"profile_id": ', encoding="utf-8")

    assert matcher.coverage_gap(str(path)) == sorted([REAL, OTHER])


def test_a_missing_file_reports_the_whole_export_as_uncovered(store, tmp_path):
    store.exported = {REAL}
    assert matcher.coverage_gap(str(tmp_path / "absent.json")) == [REAL]


def test_a_coverage_gap_does_not_reduce_what_is_imported(store, tmp_path):
    """The gap is an alert, never a veto: the covered subscribers must still get their
    digest. `jobdigest-match.sh` runs under `set -e`, which is why the exit code lives in a
    separate step placed after the send rather than in the import."""
    store.exported = {REAL, OTHER, GHOST}
    path = _write(tmp_path, {"picks": [
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "ok"}]},
    ]})

    assert matcher.import_picks(path) == 1
    assert store.written == [(REAL, "real-a", 9, "ok")]
    assert len(matcher.coverage_gap(path)) == 2


def test_a_profile_in_the_file_but_not_in_todays_export_is_not_a_gap(store, tmp_path):
    """A stale file names yesterday's subscribers. That is the freshness check's job (and
    `existing_profile_ids`' for invented ones); this check only ever reports the direction
    that silently loses a digest."""
    store.exported = {REAL}
    path = _write(tmp_path, {"picks": [
        {"profile_id": REAL, "jobs": [{"posting_id": "real-a", "score": 9, "reason": "ok"}]},
        {"profile_id": GHOST, "jobs": [{"posting_id": "real-b", "score": 9, "reason": "x"}]},
    ]})

    assert matcher.coverage_gap(path) == []


# ------------------------------------------------------------ export_shortlists --

def test_export_carries_no_email_address(store, tmp_path):
    """shortlists.json leaves the box; nothing downstream reads the address, so it must
    not be in the file. Asserted on the raw text, not just the dict, so a nested copy
    somewhere else in the payload would fail too."""
    store.profiles_out = [{
        "id": REAL, "email": "subscriber@example.com", "label": "My digest",
        "role_categories": ["data_engineering"], "stack": ["dbt"], "seniorities": ["mid"],
        "regions": ["cz"], "work_types": ["permanent"], "sectors": [],
        "years_experience": 3, "cv_summary": "Detected: data engineering, dbt",
    }]
    store.shortlist_out = [{
        "posting_id": "real-a", "title": "Analytics Engineer", "company": "Acme",
        "location": "Prague", "region": "cz", "seniority": "mid",
        "work_type": "permanent", "salary_raw": None, "description": "dbt and Snowflake",
    }]
    path = str(tmp_path / "shortlists.json")

    assert matcher.export_shortlists(path) == 1

    raw = open(path, encoding="utf-8").read()
    assert "subscriber@example.com" not in raw
    assert "@" not in raw

    payload = json.loads(raw)
    entry = payload["profiles"][0]
    assert "email" not in entry
    assert entry["profile_id"] == REAL          # the pseudonymous handle is still there
    assert entry["candidates"][0]["posting_id"] == "real-a"


# ------------------------------------------------ the export leaves a trace -----

def test_an_empty_shortlist_is_still_recorded(store, tmp_path):
    """The subscriber who gets nothing exported is the one worth alerting on, and skipping
    the write would make the worst outcome the only one leaving no trace. This is the exact
    case that hid on 2026-07-26: a profile whose retrieval collapsed produced no export, no
    picks and no digest, and every timer stayed green."""
    store.profiles_out = [{"id": REAL, "email": "s@example.com", "label": "My digest",
                           "regions": ["cz"]}]
    store.shortlist_out = []
    assert matcher.export_shortlists(str(tmp_path / "shortlists.json")) == 0
    # `shortlist_bytes` is 0 rather than null here, and the distinction is the point: null
    # means "not measured" (a row predating migration 016), 0 means "measured, and this
    # subscriber's export was empty" — which is the alertable case.
    assert store.runs == [{"profile_id": REAL, "shortlist_n": 0, "widened": False,
                           "shortlist_bytes": 0}]


def test_a_widened_shortlist_is_recorded_as_widened(store, tmp_path, monkeypatch):
    """`widened` is the fingerprint of a subscriber whose stated field the taxonomy does not
    model. The fallback rescues their digest; losing the flag would hide why they needed it."""
    monkeypatch.setattr(store, "query_shortlist_meta",
                        lambda p, limit=120: (store.shortlist_out,
                                              {"n": 1, "n_narrow": 0, "widened": True}))
    store.profiles_out = [{"id": REAL, "email": "s@example.com", "label": "My digest",
                           "regions": ["cz"]}]
    store.shortlist_out = [{"posting_id": "real-a", "title": "Sales Manager"}]
    matcher.export_shortlists(str(tmp_path / "shortlists.json"))
    assert store.runs[0]["widened"] is True


# --------------------------------------------- preferences the model must see ---
# A preference stored on the profile but never rendered into the prompt is worse than one
# that was never offered: the form promised it, the subscriber believes it applies, and no
# error is raised anywhere. These pin the two that were being dropped.

def _one_profile_export(store, tmp_path, profile: dict, candidates: list[dict]) -> dict:
    store.profiles_out = [{"id": REAL, "email": "s@example.com", **profile}]
    store.shortlist_out = candidates
    path = str(tmp_path / "shortlists.json")
    assert matcher.export_shortlists(path) == 1
    return json.loads(open(path, encoding="utf-8").read())["profiles"][0]


def test_part_time_only_reaches_the_routine(store, tmp_path):
    """Shipped for months without this: the profile said part-time only and the export said
    nothing, so subscribers were emailed roles with "HPP" (full-time) in the title."""
    entry = _one_profile_export(
        store, tmp_path,
        {"label": "My digest", "part_time_only": True, "regions": ["cz"]},
        [{"posting_id": "real-a", "title": "Grafik", "is_part_time": True},
         {"posting_id": "real-b", "title": "Grafik na HPP", "is_part_time": False}],
    )
    assert entry["profile"]["part_time_only"] is True
    assert entry["candidates"][0]["part_time"] is True
    # Only ever emitted true — the prompt tells the model to *prefer* part_time candidates, so
    # a missing key lands in the same branch `false` did, at no cost per candidate.
    assert "part_time" not in entry["candidates"][1]


def test_part_time_only_is_omitted_when_false(store, tmp_path):
    entry = _one_profile_export(
        store, tmp_path, {"label": "My digest", "part_time_only": False},
        [{"posting_id": "real-a", "title": "Grafik"}],
    )
    assert "part_time_only" not in entry["profile"]


def test_part_time_only_is_also_in_the_api_prompt(store):
    """The API path (`match_profile`) and the routine path share nothing but this module —
    a preference added to one export is easy to forget in the other."""
    assert "PART-TIME ONLY" in matcher._profile_block({"part_time_only": True})
    assert "PART-TIME" not in matcher._profile_block({"part_time_only": False})
    block, _ = matcher._candidates_block([{"posting_id": "x", "title": "Grafik",
                                           "is_part_time": True}])
    assert "part_time=yes" in block


def test_a_narrowed_work_setup_reaches_the_routine(store, tmp_path):
    """The SQL gate keeps every posting whose `work_mode` is null, which is most of them — so
    the matcher is the only thing that can apply this preference to the majority of the
    shortlist. If the export drops it, the subscriber's choice does almost nothing and nothing
    anywhere reports that."""
    entry = _one_profile_export(
        store, tmp_path,
        {"label": "My digest", "regions": ["cz"], "work_modes": ["hybrid", "remote"]},
        [{"posting_id": "real-a", "title": "Analytik", "work_mode": "hybrid"},
         {"posting_id": "real-b", "title": "Analytik II", "work_mode": None}],
    )
    assert entry["profile"]["work_modes"] == ["hybrid", "remote"]
    assert entry["profile"]["work_setup"] == "hybrid or fully remote roles only"
    assert entry["candidates"][0]["work_mode"] == "hybrid"
    # An unstated arrangement is *absent*, not smoothed into a guess — the routine is told a
    # missing field means "we don't know" and to read the description. What must never happen
    # is the export inventing 'onsite' here: one unticked checkbox would delete most of the
    # inventory. Absence and null differ only in bytes; both refuse to guess.
    assert "work_mode" not in entry["candidates"][1]


def test_an_unnarrowed_work_setup_is_omitted(store, tmp_path):
    """All three is the default and the overwhelming majority. Emitting it on every profile
    would be noise the model reads past, and its presence is what means "this one has an
    opinion"."""
    entry = _one_profile_export(
        store, tmp_path,
        {"label": "My digest", "work_modes": ["onsite", "hybrid", "remote"]},
        [{"posting_id": "real-a", "title": "Analytik"}],
    )
    assert "work_modes" not in entry["profile"]
    assert "work_setup" not in entry["profile"]


def test_the_work_setup_is_also_in_the_api_prompt(store):
    """Same reason as the part-time test above: the two export paths share only this module."""
    assert "Work setup: hybrid or fully remote roles only" in \
        matcher._profile_block({"work_modes": ["hybrid", "remote"]})
    assert "Work setup" not in matcher._profile_block({"work_modes": list(geo.WORK_MODES)})
    assert "Work setup" not in matcher._profile_block({})
    block, _ = matcher._candidates_block([
        {"posting_id": "x", "title": "Analytik", "work_mode": "hybrid"},
        {"posting_id": "y", "title": "Analytik II", "work_mode": None},
    ])
    assert "setup=hybrid" in block
    assert "setup=unstated" in block


@pytest.mark.parametrize("title,stored,expected", [
    ("Senior Data Engineer", "senior", "senior"),
    ("Junior Data Engineer", "junior", "junior"),
    ("Head of Data", "lead", "lead"),
    ("Werkstudent Data Analytics", "intern", "intern"),
    ("Graduate Data Engineer", "entry_level", "entry_level"),
    ("2D Grafik marketing", None, "unstated"),   # the title names no level: NULL, not 'mid'
])
def test_unstated_seniority_is_not_reported_as_mid(store, tmp_path, title, stored, expected):
    """A posting whose title names no level must reach the model as *unstated*, never as a
    confident level. Sending a default as a fact to a model told seniority is a HARD filter
    makes it either drop unlabelled postings or treat them as licence to email mid-level roles
    to a junior-only subscriber. Neither is what the column means.

    Before 2026-08-12 the guarantee was a *recomputation*: the column stored 'mid' for both a
    stated mid-level role and the 70% of titles naming nothing, so the exporter re-read the
    title to tell them apart. The column now stores NULL for the second kind, so what this
    pins is that the exporter passes the distinction through rather than flattening it — the
    failure it would catch today is a `or "mid"` creeping into either export path."""
    entry = _one_profile_export(
        store, tmp_path, {"label": "My digest", "seniorities": ["junior"]},
        [{"posting_id": "real-a", "title": title, "seniority": stored}],
    )
    if expected == "unstated":
        # The file exchange says "unstated" by saying nothing — one rule for every field.
        assert "seniority" not in entry["candidates"][0]
    else:
        assert entry["candidates"][0]["seniority"] == expected
    # The API path keeps the explicit word because its format has no way to omit a key. The
    # two encodings differ; what must not differ is that neither invents a level.
    block, _ = matcher._candidates_block([{"posting_id": "real-a", "title": title,
                                           "seniority": stored}])
    assert f"seniority={expected}" in block


# ------------------------------------------- what a candidate costs to describe ---


def test_a_candidate_states_only_what_the_posting_said(store, tmp_path):
    """A posting that stated nothing ships nothing but its identity.

    Against production distributions, five of the old fourteen keys carried their default on
    the *majority* of rows — `education_min` null on 90.6%, `salary` on ~65%, `work_mode` on
    most, `remote`/`part_time` false on most. Writing them anyway cost ~100-125 bytes per
    candidate, 117 candidates per subscriber, every day, to say "we don't know". This is the
    guard that they stay unwritten; `scripts/scaling_budget.py` is what measures the result.
    """
    entry = _one_profile_export(
        store, tmp_path, {"label": "My digest"},
        [{"posting_id": "real-a", "title": "Grafik"}],
    )
    assert entry["candidates"][0] == {"posting_id": "real-a", "title": "Grafik"}


def test_the_prompt_teaches_absence_rather_than_null():
    """The encoding and the instructions that read it have to move together.

    A model told `"education_min":null` means "the requirement was never read", handed a file
    that omits the key instead, has no rule for the field it cannot see — and on that field the
    safe reading (do not guess, do not exclude) is *not* the obvious one. Absence is now one
    rule covering every field, including ones nobody has added yet.

    **`deploy/matcher-routine.md` carries a third copy and is the one the live routine
    actually runs.** This test cannot reach it, and that copy has drifted from Python before
    (2026-07-26, the part-time and unstated-seniority rules). Changing either means changing
    all three.
    """
    text = matcher.ROUTINE_INSTRUCTIONS
    assert "a missing field" in text.lower()
    # Nothing in the export is null any more, so nothing in the prompt should teach null.
    assert "null" not in text.lower()


# ------------------------------------------------------------------- safe_url ---

@pytest.mark.parametrize("url,expected", [
    ("https://jobs.example.com/1", "https://jobs.example.com/1"),
    ("http://jobs.example.com/1", "http://jobs.example.com/1"),
    ("HTTPS://Jobs.Example.com/1", "HTTPS://Jobs.Example.com/1"),
    ("javascript:alert(1)", "#"),
    ("data:text/html,<script>alert(1)</script>", "#"),
    ("  javascript:alert(1)", "#"),
    ("//evil.example.com", "#"),
    ("", "#"),
    (None, "#"),
])
def test_safe_url_allows_only_http_schemes(url, expected):
    """Posting URLs come from third-party feeds and land in an email href. html.escape
    prevents attribute breakout but says nothing about the scheme."""
    from service.digest import safe_url
    assert safe_url(url) == expected
