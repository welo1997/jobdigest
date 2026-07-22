"""Tests for the unsubscribe flow and the /event abuse bounds.

The unsubscribe tests exist for one reason: **a GET must not change state.** Mail clients,
link previewers and corporate security scanners fetch URLs found in messages to check them,
so a GET that unsubscribes will unsubscribe people who never clicked. That matters more now
that an unsubscribed profile is erased 30 days later — the bug used to cost someone their
digest, and would now be on a path to destroying their settings.

The /event tests pin the abuse bounds. That endpoint is unauthenticated by necessity (it
fires before anyone has a token), so nothing but these counters stops the table growing
without limit.
"""

import pytest
from fastapi.testclient import TestClient

from service import webapp

TOKEN = "a-valid-manage-token"
EMAIL = "person@example.com"


CONFIRM = "a-valid-confirm-token"


class _FakeStore:
    """Minimal stand-in for service.store covering only what these paths touch."""

    CONFIRM_TOKEN_TTL_DAYS = 7

    def __init__(self):
        self.unsubscribed: list[str] = []
        self.events: list[dict] = []
        self.token_lookups = 0
        self.confirm_calls = 0
        self.confirm_consumed = False        # the real query nulls the token on first use

    def confirm_subscription(self, token):
        self.confirm_calls += 1
        # Single-use: the confirming UPDATE nulls confirm_token, so a second click with the
        # same token matches no row. Model that here rather than returning an "already
        # confirmed" profile the way the pre-single-use code did.
        if token != CONFIRM or self.confirm_consumed:
            return None, False
        self.confirm_consumed = True
        return {"id": "1", "email": EMAIL, "manage_token": TOKEN}, True

    def init_pool(self, *a, **k):
        pass

    def count_active(self) -> int:
        return 0

    def get_by_manage_token(self, token):
        self.token_lookups += 1
        if token != TOKEN:
            return None
        return {"id": "11111111-1111-1111-1111-111111111111",
                "email": EMAIL, "manage_token": TOKEN}

    def unsubscribe(self, token):
        if token != TOKEN:
            return None
        self.unsubscribed.append(token)
        return EMAIL

    def record_event(self, name, **kw):
        self.events.append({"name": name, **kw})


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(webapp, "store", fake)
    # Reset the process-local rate-limit window so tests don't leak into each other.
    monkeypatch.setattr(webapp, "_event_window_start", 0.0)
    monkeypatch.setattr(webapp, "_event_total", 0)
    webapp._event_counts.clear()
    return fake


@pytest.fixture
def client(store):
    with TestClient(webapp.app) as c:
        yield c


# ------------------------------------------------------------------ unsubscribe --

def test_get_unsubscribe_does_not_unsubscribe(client, store):
    """The whole point: fetching the link must have no effect."""
    r = client.get("/unsubscribe", params={"token": TOKEN})

    assert r.status_code == 200
    assert store.unsubscribed == []          # <- nothing happened
    assert "<form" in r.text and 'method="post"' in r.text
    assert EMAIL in r.text                    # shows whose subscription it is


def test_get_unsubscribe_with_bad_token_404s(client, store):
    r = client.get("/unsubscribe", params={"token": "nope"})
    assert r.status_code == 404
    assert store.unsubscribed == []


def test_post_with_confirm_unsubscribes_and_returns_a_page(client, store):
    r = client.post("/unsubscribe", data={"token": TOKEN, "confirm": "1"})

    assert r.status_code == 200
    assert store.unsubscribed == [TOKEN]
    assert "unsubscribed" in r.text.lower()
    assert "text/html" in r.headers["content-type"]


def test_post_without_confirm_is_rfc8058_one_click(client, store):
    """Mail clients POST from List-Unsubscribe-Post and expect a plain 200."""
    r = client.post("/unsubscribe", data={"token": TOKEN})

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert store.unsubscribed == [TOKEN]


def test_one_click_with_unknown_token_still_returns_200(client, store):
    """Never signal failure to a mail client — it would retry forever."""
    r = client.post("/unsubscribe", data={"token": "nope"})

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert store.unsubscribed == []


# --------------------------------------------------------------------- confirm --

@pytest.fixture
def sent(monkeypatch):
    """Capture outbound mail instead of sending it."""
    box: list[tuple] = []
    monkeypatch.setattr(webapp.mailer, "send",
                        lambda to, subj, html, text, **kw: box.append((to, subj)) or "ok")
    monkeypatch.setattr(webapp.transactional, "render_welcome",
                        lambda email, url: ("Welcome", "<p>hi</p>", "hi"))
    return box


def test_first_confirm_sends_the_welcome(client, store, sent):
    r = client.get("/confirm", params={"token": CONFIRM})
    assert r.status_code == 200
    assert len(sent) == 1
    assert TOKEN in r.text                     # the manage link is surfaced on first confirm


def test_confirm_token_is_single_use(client, store, sent):
    """A confirm link works exactly once.

    Two things ride on this. The historical one: every click used to re-send the welcome,
    making the link an unlimited 'email this person' primitive. The bigger one: /confirm
    answers with the subscriber's manage_token (a full-control credential) in the page, so a
    replayed link — leaked, bookmarked, or fetched by a mail/security scanner — must not be
    exchangeable for that token after the real user has confirmed."""
    first = client.get("/confirm", params={"token": CONFIRM})
    assert first.status_code == 200
    assert TOKEN in first.text                 # legit first click gets the manage link

    second = client.get("/confirm", params={"token": CONFIRM})
    assert second.status_code == 404           # token consumed -> no longer active
    assert TOKEN not in second.text            # manage token NOT re-derivable from a spent link
    assert len(sent) == 1                      # welcome sent once, not twice


def test_expired_or_unknown_confirm_token_404s(client, store, sent):
    r = client.get("/confirm", params={"token": "stale"})
    assert r.status_code == 404
    assert sent == []


# ----------------------------------------------------------------------- /event --

def test_unknown_event_names_are_dropped(client, store):
    r = client.post("/event", json={"name": "not_a_real_event"})
    assert r.json() == {"ok": True}           # silent, gives an attacker no signal
    assert store.events == []


def test_event_is_recorded(client, store):
    client.post("/event", json={"name": "landing_view", "session_id": "s1"})
    assert [e["name"] for e in store.events] == ["landing_view"]


def test_per_session_cap_drops_the_overflow(client, store, monkeypatch):
    monkeypatch.setattr(webapp, "EVENT_MAX_PER_SESSION", 3)
    for _ in range(10):
        client.post("/event", json={"name": "landing_view", "session_id": "s1"})
    assert len(store.events) == 3


def test_global_cap_bounds_rotating_session_ids(client, store, monkeypatch):
    """A session id is client-supplied, so the per-session cap alone is worthless against
    someone rotating them. The global cap is what actually bounds table growth."""
    monkeypatch.setattr(webapp, "EVENT_MAX_PER_SESSION", 1000)
    monkeypatch.setattr(webapp, "EVENT_MAX_GLOBAL", 5)
    for i in range(50):
        client.post("/event", json={"name": "landing_view", "session_id": f"s{i}"})
    assert len(store.events) == 5


def test_rate_limit_precedes_the_token_lookup(client, store, monkeypatch):
    """The token lookup is a DB round trip, so it must sit behind the limiter — otherwise
    a flood still costs one query per request."""
    monkeypatch.setattr(webapp, "EVENT_MAX_GLOBAL", 2)
    for _ in range(20):
        client.post("/event", json={"name": "landing_view",
                                    "session_id": "s1", "token": TOKEN})
    assert store.token_lookups <= 2


def test_limiter_memory_is_bounded_by_the_window(store, monkeypatch):
    """The limiter must not become the thing that exhausts the box."""
    monkeypatch.setattr(webapp, "EVENT_WINDOW_SEC", 0)   # every call starts a new window
    for i in range(500):
        webapp._event_allowed(f"session-{i}")
    assert len(webapp._event_counts) == 1                # cleared each window
