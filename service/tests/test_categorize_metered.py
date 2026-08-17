"""The metered title classifier: a model's answer is untrusted input, and a batch is the
unit of failure.

Written in the shape of `test_matcher_exchange.py`, because it guards the same boundary
through a different transport. The file exchange's answers arrived on cloud storage; these
arrive from `client.messages.create`. **The transport changed on 2026-08-17 and the class of
input did not** — a returned value is untrusted whoever produced it, which is the 2026-08-08
hint-guard lesson arriving by a third route (five adapters trusted a third-party string to be
a category and 14 135 active postings landed somewhere no subscriber could select and no
query could match, with nothing reporting it).

Four guarantees, each mutation-checked against the code that provides it:

* an answer naming a **category outside `taxonomy.CATEGORIES`** never reaches the DB,
* an answer for a **title that was not in this batch** is dropped,
* a **malformed record** costs its own row, not the batch,
* a **failed or unusable batch** costs that batch, not the run — what other batches
  validated is still stored.

Plus the payload discipline: the request body carries titles and integer indices only.

No real API calls — `_FakeClient` returns a fixed content-block list and records the kwargs
it was given, exactly as `test_matcher_metered.py`'s does.
"""

from __future__ import annotations

import json

import pytest

from service import categorize_exchange as cx
from service import taxonomy


# ------------------------------------------------------------------- the fake client ---

class _Block:
    """One response content block: `type` plus whatever text it carries."""

    def __init__(self, type_: str, text: str = ""):
        self.type = type_
        self.text = text


class _FakeClient:
    """Returns one scripted reply per call; records the kwargs each call got.

    `replies` may hold a string (rendered as a single text block), a list of `_Block`s, or an
    Exception instance, which is raised — that last one is how a transient API failure is
    exercised without a network.
    """

    def __init__(self, *replies, stop_reason: str | None = "end_turn"):
        self.replies = list(replies)
        self.stop_reason = stop_reason
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else '{"categories": []}'
        if isinstance(reply, Exception):
            raise reply
        blocks = [_Block("text", reply)] if isinstance(reply, str) else reply
        return type("Resp", (), {"content": blocks, "usage": None,
                                 "stop_reason": self.stop_reason})()


def _batch(*titles: str) -> list[tuple[str, dict]]:
    """A batch in the shape `_pending_titles` returns: [(title_key, {title, n})]."""
    return [(cx.normalise_title(t), {"title": t, "n": 1}) for t in titles]


_TWO = _batch("Ställare inom formsprutning", "Extrajobb")


# ------------------------------------------------------------ what leaves the machine ---

def test_the_request_carries_titles_and_integer_indices_and_nothing_else():
    """Asserted against the serialised request body, not the dict the code built — the point
    is what leaves the machine. These are public job titles, but the discipline is the same one
    `shortlists.json` earned the hard way: a company, a URL or a posting id appearing in a
    payload that leaves the box is a change to what we disclose, not a field."""
    client = _FakeClient('{"categories": []}')

    cx.classify_batch(client, _TWO)

    body = client.calls[0]["messages"][0]["content"]
    assert json.loads(body) == {"titles": [{"i": 0, "title": "Ställare inom formsprutning"},
                                           {"i": 1, "title": "Extrajobb"}]}
    for banned in ("@", "posting_id", "profile_id", "company", "http"):
        assert banned not in body


def test_the_model_is_offered_the_real_vocabulary_and_the_routine_s_own_instructions():
    """The prompt is one text with the routine's, not a paraphrase of it. A second wording is
    how the two paths would start answering differently with nothing to show it — and a
    category list that drifts from the taxonomy is how a model returns something reasonable
    that this system then has to throw away."""
    client = _FakeClient('{"categories": []}')

    cx.classify_batch(client, _TWO)

    system = client.calls[0]["system"][0]["text"]
    assert cx.ROUTINE_INSTRUCTIONS in system
    for category in taxonomy.CATEGORIES:
        assert category in system
    assert taxonomy.UNCATEGORISED in system


def test_the_model_and_the_derived_output_ceiling_reach_the_api_call():
    """`CATEGORIZE_MODEL` must be a real knob, and the ceiling must scale with the batch or a
    batch-size change becomes a silent truncation bug (one answer is ~18 tokens)."""
    client = _FakeClient('{"categories": []}', '{"categories": []}')

    cx.classify_batch(client, _TWO)
    assert client.calls[0]["model"] == cx.MODEL
    assert client.calls[0]["max_tokens"] == cx._max_output_tokens(2)

    cx.classify_batch(client, _TWO, model="claude-sonnet-5")
    assert client.calls[1]["model"] == "claude-sonnet-5"

    # It is a *ceiling*, so it must leave real headroom over the answers it has to hold.
    assert cx._max_output_tokens(200) > 200 * 18


# ------------------------------------------------------------------ the trust boundary ---

def test_a_valid_answer_is_stored_under_the_normalised_key():
    client = _FakeClient('{"categories": [{"i": 0, "category": "manufacturing_production"}]}')

    rows = cx.classify_batch(client, _TWO)

    assert rows == {cx.normalise_title("Ställare inom formsprutning"):
                    (cx.normalise_title("Ställare inom formsprutning"),
                     "manufacturing_production", "Ställare inom formsprutning")}


@pytest.mark.parametrize("category,why", [
    ("Industriell tillverkning", "a real SSYK label the model might echo, not a category"),
    ("SALES", "right word, wrong case — `role_category` is matched exactly"),
    ("software", "a plausible near-miss for software_engineering"),
    ("", "empty"),
    (None, "null"),
    (["sales"], "a list where a string belongs"),
])
def test_a_category_that_does_not_exist_never_reaches_the_db(category, why):
    """The 2026-08-08 hint guard, arriving by the metered route. `role_category` must only ever
    hold a value a subscriber can select and a query can match."""
    client = _FakeClient(json.dumps({"categories": [{"i": 0, "category": category}]}))

    assert cx.classify_batch(client, _TWO) == {}, why


@pytest.mark.parametrize("index,why", [
    (99, "past the end of the batch"),
    (-1, "negative"),
    ("0", "a string index is not an index"),
    (True, "a bool is not an int here — True == 1 would answer for title 1"),
    (None, "null"),
    (1.0, "a float is not an index"),
])
def test_an_answer_for_a_title_that_was_not_in_this_batch_is_dropped(index, why):
    """Answers arrive by integer index and the index is checked against what was actually
    sent. The model cannot invent a title to classify, and — because indices are local to the
    batch — it cannot reach a title from a different batch either."""
    client = _FakeClient(json.dumps({"categories": [{"i": index, "category": "sales"}]}))

    assert cx.classify_batch(client, _TWO) == {}, why


def test_a_malformed_record_costs_its_own_row_and_not_the_batch():
    """One bad answer must not cost the other 199 titles in the call."""
    client = _FakeClient(json.dumps({"categories": [
        "not a dict", None, 7, [], {"i": 0}, {"category": "sales"},
        {"i": 1, "category": "logistics_transport"},
    ]}))

    rows = cx.classify_batch(client, _TWO)

    assert list(rows.values()) == [
        (cx.normalise_title("Extrajobb"), "logistics_transport", "Extrajobb")]


def test_a_decline_is_stored_so_the_same_unreadable_title_is_not_re_asked():
    """`uncategorised` is a real answer and the right one for "Extrajobb". Storing it is what
    makes the residue shrink: `asked_title_keys` keeps declines, so a title nothing can read is
    asked about once rather than every run for ever."""
    client = _FakeClient(json.dumps(
        {"categories": [{"i": 1, "category": taxonomy.UNCATEGORISED}]}))

    rows = cx.classify_batch(client, _TWO)

    assert list(rows.values()) == [
        (cx.normalise_title("Extrajobb"), taxonomy.UNCATEGORISED, "Extrajobb")]


def test_the_same_title_answered_twice_yields_one_row():
    """Rows are keyed on the title key, so a model that repeats itself cannot produce two
    conflicting writes for one key. Last answer wins, deterministically."""
    client = _FakeClient(json.dumps({"categories": [
        {"i": 0, "category": "sales"},
        {"i": 0, "category": "manufacturing_production"},
    ]}))

    rows = cx.classify_batch(client, _TWO)

    assert len(rows) == 1
    assert list(rows.values())[0][1] == "manufacturing_production"


def test_both_paths_run_the_same_validation():
    """Not a style assertion: the guarantees above were written against the file exchange, and
    a second copy of them for the API would drift silently — a wrong category raises nothing,
    it just files a job in a stranger's digest. `import_categories` and `classify_batch` must
    reach the DB through one validator."""
    import inspect

    for fn in (cx.import_categories, cx.classify_batch):
        assert "_validated_rows" in inspect.getsource(fn), fn.__name__


# ------------------------------------------------------- a reply the parser cannot use ---

_ANSWER = '{"categories": [{"i": 0, "category": "manufacturing_production"}]}'


@pytest.mark.parametrize("reply,why", [
    (_ANSWER + "\n\nI hope these are helpful!", "prose after the object — the 2026-08-17 case"),
    ("Here are the categories:\n" + _ANSWER, "prose before the object, unfenced"),
    ("```json\n" + _ANSWER + "\n```", "the markdown form"),
    ('[{"i": 0, "category": "manufacturing_production"}]', "a bare list of answers"),
])
def test_answers_survive_a_model_that_keeps_talking(reply, why):
    """`json.loads` rejects the *entire* reply on trailing data, so a perfectly good answer
    followed by "I hope these are helpful!" would yield nothing for the batch. The prompt says
    JSON only and the model usually obeys; the parser must not stake the batch on it."""
    client = _FakeClient(reply)

    assert len(cx.classify_batch(client, _TWO)) == 1, why


def test_answers_are_read_from_the_first_text_block_not_position_zero():
    """`CATEGORIZE_MODEL` must be safe to point at a model that thinks before it answers. Every
    model above Haiku 4.5 opens its response with a `thinking` block whose text is empty, so
    reading `content[0]` parsed "" and returned zero answers for every batch — a residue that
    stops shrinking, which looks exactly like a taxonomy that has caught everything it can."""
    client = _FakeClient([_Block("thinking", ""), _Block("text", _ANSWER)])

    assert len(cx.classify_batch(client, _TWO)) == 1


@pytest.mark.parametrize("reply", [
    [_Block("thinking", "")],
    "I could not classify any of these titles.",
    '{"categories": {"0": "sales"}}',
    '"sales"',
])
def test_an_unusable_reply_returns_nothing_and_does_not_raise(reply):
    """The other side of the leniency: surrounding prose is tolerated, but a reply that never
    contained a list of answers must not be turned into one."""
    client = _FakeClient(reply)

    assert cx.classify_batch(client, _TWO) == {}


@pytest.mark.parametrize("reply,shape", [
    ('{"categories": {"0": "sales"}}', "dict"),
    ('{"categories": "sales"}', "str"),
])
def test_an_answer_that_is_not_a_list_is_named_as_such(reply, shape, caplog):
    """The empty-rows outcome is the same whether the shape is rejected or silently coerced, so
    the *diagnostic* is the only thing that distinguishes "the model answered in a shape we do
    not read" from "the model had nothing to say". Without it a prompt or model change that
    flipped the answer shape would read as a residue that stopped shrinking — the exact failure
    mode `deploy/categorize-routine.md` says to watch the hit rate for."""
    client = _FakeClient(reply)

    with caplog.at_level("WARNING", logger="service.categorize_exchange"):
        assert cx.classify_batch(client, _TWO) == {}

    assert "not a list of answers" in caplog.text
    assert shape in caplog.text


def test_a_truncated_reply_is_named_as_truncation_not_as_bad_json(caplog):
    """A reply cut off at `max_tokens` is invalid JSON, so without this it surfaces only as
    "bad JSON from model" — which points the next reader at the parser or the prompt, never at
    the budget that caused it. The remedy has to be in the message."""
    truncated = '{"categories": [{"i": 0, "category": "manufacturing_produ'
    client = _FakeClient(truncated, stop_reason="max_tokens")

    with caplog.at_level("ERROR", logger="service.categorize_exchange"):
        assert cx.classify_batch(client, _TWO) == {}

    assert "max_tokens" in caplog.text
    assert "CATEGORIZE_MAX_OUTPUT_TOKENS" in caplog.text
    assert "CATEGORIZE_BATCH_SIZE" in caplog.text


def test_an_ordinary_reply_logs_no_truncation_error(caplog):
    """The other half: a guard that fires on every call is noise, and noise in the one line
    that means "a whole batch went unanswered" is worse than no line."""
    client = _FakeClient(_ANSWER, stop_reason="end_turn")

    with caplog.at_level("ERROR", logger="service.categorize_exchange"):
        assert len(cx.classify_batch(client, _TWO)) == 1

    assert "max_tokens" not in caplog.text


# ------------------------------------------------------------------------- the run ---

@pytest.fixture
def stub_run(monkeypatch):
    """Wire `classify_uncategorised` to an in-memory residue and record what it stores."""
    stored: list[tuple] = []
    sources: list[str] = []

    def _upsert(rows, source="routine"):
        stored.extend(rows)
        sources.append(source)
        return len(list(rows))

    monkeypatch.setattr(cx.store, "asked_title_keys", lambda: set())
    monkeypatch.setattr(cx.store, "upsert_title_categories", _upsert)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return stored, sources


def _residue(*titles_and_counts):
    return lambda: [{"title": t, "n": n} for t, n in titles_and_counts]


def _install_client(monkeypatch, client):
    """Make `classify_uncategorised`'s `anthropic.Anthropic(...)` return `client`."""
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: client)


def test_a_failed_batch_costs_that_batch_and_not_the_run(stub_run, monkeypatch, caplog):
    """The guarantee that matters most operationally. A transient API error on one batch must
    not throw away what the others validated — that is `import_picks`' rule, and this job has
    even less reason to be all-or-nothing: nobody is waiting on it, and a partial answer means
    the residue shrinks a bit less rather than not at all."""
    stored, _sources = stub_run
    monkeypatch.setattr(cx.store, "uncategorised_titles",
                        _residue(("Ställare", 9), ("Extrajobb", 5), ("Svetsare", 1)))
    _install_client(monkeypatch, _FakeClient(
        '{"categories": [{"i": 0, "category": "manufacturing_production"}]}',   # batch 1 ok
        RuntimeError("connection reset"),                                        # batch 2 dies
        '{"categories": [{"i": 0, "category": "skilled_trades"}]}',              # batch 3 ok
    ))

    with caplog.at_level("ERROR", logger="service.categorize_exchange"):
        written = cx.classify_uncategorised(batch_size=1)

    assert written == 2
    assert {row[1] for row in stored} == {"manufacturing_production", "skilled_trades"}
    assert "batch 2/3 failed" in caplog.text


def test_a_batch_that_cannot_be_stored_does_not_abort_the_run(stub_run, monkeypatch, caplog):
    """The other half of the same rule: a DB hiccup on one batch is not the run's problem
    either. `classify_batch` succeeding and the write failing are different failures and both
    have to be survivable."""
    _stored, _sources = stub_run
    calls = {"n": 0}

    def _flaky(rows, source="routine"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("deadlock detected")
        return len(list(rows))

    monkeypatch.setattr(cx.store, "upsert_title_categories", _flaky)
    monkeypatch.setattr(cx.store, "uncategorised_titles",
                        _residue(("Ställare", 9), ("Extrajobb", 5)))
    _install_client(monkeypatch, _FakeClient(
        '{"categories": [{"i": 0, "category": "manufacturing_production"}]}',
        '{"categories": [{"i": 0, "category": "logistics_transport"}]}',
    ))

    with caplog.at_level("ERROR", logger="service.categorize_exchange"):
        written = cx.classify_uncategorised(batch_size=1)

    assert written == 1
    assert "could not be stored" in caplog.text


def test_the_commonest_titles_are_asked_about_first(stub_run, monkeypatch):
    """A run cut short by the limit or the budget should have answered for the titles standing
    for the most inventory — the same policy the export's truncation follows."""
    _stored, _sources = stub_run
    monkeypatch.setattr(cx.store, "uncategorised_titles",
                        _residue(("Rare title", 1), ("Common title", 400)))
    client = _FakeClient('{"categories": []}')
    _install_client(monkeypatch, client)

    cx.classify_uncategorised(limit=1, batch_size=10)

    assert "Common title" in client.calls[0]["messages"][0]["content"]
    assert "Rare title" not in client.calls[0]["messages"][0]["content"]


def test_a_truncated_run_says_what_it_left_behind(stub_run, monkeypatch, caplog):
    """A silent cap reads as "we covered everything"."""
    _stored, _sources = stub_run
    monkeypatch.setattr(cx.store, "uncategorised_titles",
                        _residue(("Common title", 400), ("Rare title", 1)))
    _install_client(monkeypatch, _FakeClient('{"categories": []}'))

    with caplog.at_level("WARNING", logger="service.categorize_exchange"):
        cx.classify_uncategorised(limit=1, batch_size=10)

    assert "did NOT fit the limit" in caplog.text


def test_a_title_already_answered_is_never_asked_again(stub_run, monkeypatch):
    """Including a recorded decline — that is what makes the residue shrink rather than being
    re-read every week. Shared with the export via `_pending_titles`, so it cannot be true of
    one path and false of the other."""
    _stored, _sources = stub_run
    monkeypatch.setattr(cx.store, "asked_title_keys",
                        lambda: {cx.normalise_title("Extrajobb")})
    monkeypatch.setattr(cx.store, "uncategorised_titles",
                        _residue(("Extrajobb", 9), ("Ställare", 5)))
    client = _FakeClient('{"categories": []}')
    _install_client(monkeypatch, client)

    cx.classify_uncategorised()

    body = client.calls[0]["messages"][0]["content"]
    assert json.loads(body) == {"titles": [{"i": 0, "title": "Ställare"}]}


def test_the_abort_budget_stops_the_run_before_billing_the_whole_corpus(stub_run, monkeypatch,
                                                                       caplog):
    """A bug that inflated the residue query must not be able to run up unbounded spend before
    anyone notices. Checked before each call, so it bounds spend to roughly the ceiling plus
    one batch. Same shape and same reasoning as `MATCHER_MAX_TOKENS`."""
    _stored, _sources = stub_run
    monkeypatch.setattr(cx.store, "uncategorised_titles",
                        _residue(("A", 3), ("B", 2), ("C", 1)))
    monkeypatch.setattr(cx, "MAX_RUN_TOKENS", 500)

    class _Usage:
        input_tokens, output_tokens, cache_read_input_tokens = 1000, 1, 0

    class _Billing(_FakeClient):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            return type("Resp", (), {"content": [_Block("text", '{"categories": []}')],
                                     "usage": _Usage(), "stop_reason": "end_turn"})()

    client = _Billing()
    _install_client(monkeypatch, client)

    with caplog.at_level("ERROR", logger="service.categorize_exchange"):
        cx.classify_uncategorised(batch_size=1)

    assert len(client.calls) == 1, "aborted after the first batch"
    assert "ABORT BUDGET" in caplog.text


def test_answers_are_stored_with_their_provenance(stub_run, monkeypatch):
    """`source='api'` is a new *value* in an existing column, not a new field. It is worth
    having: when a category looks wrong, "which path answered this" is the first question."""
    _stored, sources = stub_run
    monkeypatch.setattr(cx.store, "uncategorised_titles", _residue(("Ställare", 9)))
    _install_client(monkeypatch, _FakeClient(
        '{"categories": [{"i": 0, "category": "manufacturing_production"}]}'))

    cx.classify_uncategorised()

    assert sources == ["api"]


def test_an_empty_residue_makes_no_api_call_and_needs_no_key(stub_run, monkeypatch):
    """The steady state some weeks, once the backlog has drained. Paying for a call that asks
    about nothing is the kind of thing that only ever shows up on a bill.

    The key is deliberately *removed* here rather than left set: with nothing to ask about, the
    run must return before it constructs a client at all. Asserting only "no calls were made"
    would be untestable — an empty batch list means the loop body never runs, so the assertion
    would hold with or without the early return."""
    _stored, _sources = stub_run
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cx.store, "uncategorised_titles", lambda: [])
    client = _FakeClient()
    _install_client(monkeypatch, client)

    assert cx.classify_uncategorised() == 0
    assert client.calls == []


def test_a_dry_run_makes_no_api_call_and_needs_no_key(monkeypatch, capsys):
    """The knob you reach for when a category looks wrong: see the exact prompt without
    spending anything, and without a key on the machine you are reading it from."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cx.store, "asked_title_keys", lambda: set())
    monkeypatch.setattr(cx.store, "uncategorised_titles", _residue(("Ställare", 9)))
    monkeypatch.setattr(cx.store, "upsert_title_categories",
                        lambda rows, **kw: pytest.fail("nothing may be written"))

    assert cx.classify_uncategorised(dry_run=True) == 0
    assert "Ställare" in capsys.readouterr().out


def test_a_run_without_a_key_raises(monkeypatch):
    """A misconfigured box must fail loudly — the unit's OnFailure alerter is what makes it
    so — rather than quietly leaving the residue where it is, which is indistinguishable from
    a taxonomy that has caught everything it can."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cx.store, "asked_title_keys", lambda: set())
    monkeypatch.setattr(cx.store, "uncategorised_titles", _residue(("Ställare", 9)))

    with pytest.raises(KeyError):
        cx.classify_uncategorised()
