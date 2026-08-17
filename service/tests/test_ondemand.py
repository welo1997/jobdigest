"""POST /digest/run — the on-demand "run my digest now" endpoint.

Each run spends a metered Anthropic API call and sends an email, so the endpoint's job is to
make sure that only happens when it should:

* **A GET must never trigger it.** Link scanners, mail clients and prefetchers fetch URLs —
  a GET that matched + sent would let any of them run up cost and mail a subscriber. It is
  POST-only (security rule 3), same reason /unsubscribe is.
* **It acts only on the authenticated caller's own profile** — never another subscriber's.
* **The cooldown is claimed before any spend**, and a misconfigured box (no key) 503s
  *before* the cooldown is claimed, so a server fault never burns the subscriber's window.
* **On cooldown, nothing is matched or sent.**

`matcher.match_one` and `pipeline.send_one` are replaced with spies — no real API call, no
real email — so the test asserts *whether and with what* they were invoked.
"""

import pytest
from fastapi.testclient import TestClient

from service import matcher, pipeline, webapp

TOKEN = "tok-A"
PROFILE_A = {"id": "aaaaaaaa", "email": "a@example.com", "manage_token": TOKEN}


class _FakeStore:
    def __init__(self):
        self.claims: list[str] = []
        self.claim_ok = True                 # flip to False to model an active cooldown

    # lifespan hooks
    def init_pool(self, *a, **k): pass
    def count_active(self) -> int: return 1

    # auth
    def get_by_manage_token(self, token):
        return PROFILE_A if token == TOKEN else None

    def session_profile(self, raw, ttl_days=30):
        return None                          # no cookie session in these tests

    # cooldown claim
    def claim_ondemand_run(self, profile_id, cooldown_min):
        self.claims.append(profile_id)
        return self.claim_ok


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(webapp, "store", fake)
    monkeypatch.setattr(webapp, "SESSION_COOKIE_SECURE", False)
    return fake


@pytest.fixture
def spies(monkeypatch):
    """Spy on the two expensive calls the endpoint drives; a real box would bill + email here."""
    calls = {"match": [], "send": []}
    monkeypatch.setattr(matcher, "match_one", lambda p, **k: (calls["match"].append(p["id"]), 3)[1])
    monkeypatch.setattr(pipeline, "send_one",
                        lambda p, **k: (calls["send"].append(p["id"]), {"sent": True, "n": 2})[1])
    return calls


@pytest.fixture
def client(store):
    with TestClient(webapp.app) as c:
        yield c


def test_get_does_not_run(client, store, spies, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    r = client.get("/digest/run")
    assert r.status_code == 405                       # POST-only; a scanner's GET cannot spend
    assert spies["match"] == [] and store.claims == []


def test_unauthenticated_is_refused_and_spends_nothing(client, store, spies, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    r = client.post("/digest/run", json={})
    assert r.status_code == 404
    assert store.claims == [] and spies["match"] == [] and spies["send"] == []


def test_missing_key_503s_without_claiming_the_cooldown(client, store, spies, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/digest/run", json={"token": TOKEN})
    assert r.status_code == 503
    # The subscriber's 6h window is NOT consumed by a server misconfiguration.
    assert store.claims == [] and spies["match"] == []


def test_happy_path_matches_and_sends_the_callers_own_profile(client, store, spies, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    r = client.post("/digest/run", json={"token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["matched"] == 3 and body["sent"] and body["jobs"] == 2
    # Claimed once, and both expensive calls ran against the token-owner's id — never another's.
    assert store.claims == ["aaaaaaaa"]
    assert spies["match"] == ["aaaaaaaa"] and spies["send"] == ["aaaaaaaa"]


def test_cooldown_blocks_a_second_run(client, store, spies, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    store.claim_ok = False                            # the atomic claim reports still-in-window
    r = client.post("/digest/run", json={"token": TOKEN})
    assert r.status_code == 429
    assert spies["match"] == [] and spies["send"] == []   # nothing matched or sent on cooldown
