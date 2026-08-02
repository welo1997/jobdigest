"""Tests for the unsubscribe flow and the /event abuse bounds.

The unsubscribe tests exist for one reason: **a GET must not change state.** Mail clients,
link previewers and corporate security scanners fetch URLs found in messages to check them,
so a GET that unsubscribes will unsubscribe people who never clicked. That matters more now
that an unsubscribed profile is erased 30 days later — the bug used to cost someone their
digest, and would now be on a path to destroying their settings.

The /event tests pin the abuse bounds. That endpoint is unauthenticated by necessity (it
fires before anyone has a token), so nothing but these counters stops the table growing
without limit.

The /matches tests pin that paging reaches every match. `count` is the total and `jobs` is
one page, and for as long as nothing walked the offsets the page showed 25 of a subscriber's
127 under a headline built from the total — no error, nothing in the logs, and the missing
rows were the sub-EMAIL_MIN_SCORE picks the page exists to show.
"""

import pytest
from fastapi.testclient import TestClient

from service import links, webapp

TOKEN = "a-valid-manage-token"
EMAIL = "person@example.com"
PROFILE_ID = "11111111-1111-1111-1111-111111111111"


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
        # subscribe path
        self.created: list[str] = []
        self.live_emails: set[str] = set()   # addresses that already have a live subscription
        self.suppressed: set[str] = set()
        # manage-link path: addresses with an active/paused sub, and a per-address cooldown flag
        self.manageable: dict[str, str] = {}   # email -> manage_token
        self.manage_link_cooldown: set[str] = set()  # emails currently inside the cooldown window
        # session path (persisted magic-link login)
        self.sessions: dict[str, str] = {}     # raw cookie token -> profile_id
        self.expired: set[str] = set()         # raw tokens forced past their idle timeout
        self.revoked_profiles: list[str] = []  # profile_ids whose sessions were bulk-revoked
        self.updates: list[dict] = []          # preference changes applied via update_subscription
        self.signup_intents: dict[str, str] = {}   # raw token -> Google-verified email
        self.matches: list[dict] = []          # /matches paging
        self.hidden: set[str] = set()          # posting_ids the subscriber hid

    # -- subscribe path --
    def is_suppressed(self, email):
        return email.strip().lower() in self.suppressed

    def live_subscription_exists(self, email):
        return email.strip().lower() in self.live_emails

    def recent_signup_exists(self, email, within_minutes):
        return False

    def create_email_subscription(self, email, data, confirmed=False):
        # The real insert is rejected by the unique index when a live row already exists;
        # model that so the "already subscribed" guard can be tested end to end.
        e = email.strip().lower()
        if e in self.live_emails:
            return None
        self.created.append(e)
        self.live_emails.add(e)
        return {"id": "new-id", "email": e,
                "confirm_token": None if confirmed else "ctok",
                "manage_token": "mtok",
                "status": "active" if confirmed else "pending"}

    def request_manage_link(self, email, cooldown_min):
        # Models the atomic claim-and-set in store.request_manage_link: return the profile only
        # for an active/paused address that isn't already inside the cooldown window, and mark
        # it in-cooldown so an immediate second call returns None (no second email).
        e = email.strip().lower()
        if e not in self.manageable or e in self.manage_link_cooldown:
            return None
        self.manage_link_cooldown.add(e)
        return {"id": "mid", "email": e, "manage_token": self.manageable[e]}

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

    def update_subscription(self, manage_token, data):
        if manage_token != TOKEN:
            return None
        self.updates.append(data)
        return {"id": PROFILE_ID, "email": EMAIL, "manage_token": TOKEN, **data}

    # -- session path --
    SESSION_TTL_DAYS = 30

    def create_session(self, profile_id, ttl_days=30):
        raw = f"sess-{len(self.sessions)}-{profile_id}"
        self.sessions[raw] = profile_id
        return raw

    def session_profile(self, raw, ttl_days=30):
        if not raw or raw in self.expired or raw not in self.sessions:
            return None
        return {"id": self.sessions[raw], "email": EMAIL, "manage_token": TOKEN}

    def revoke_session(self, raw):
        self.sessions.pop(raw, None)

    def revoke_profile_sessions(self, profile_id):
        n = len([r for r, p in self.sessions.items() if p == profile_id])
        self.sessions = {r: p for r, p in self.sessions.items() if p != profile_id}
        self.revoked_profiles.append(profile_id)
        return n

    def get_live_profile_by_email(self, email):
        if email.strip().lower() == EMAIL.lower():
            return {"id": PROFILE_ID, "email": EMAIL, "manage_token": TOKEN}
        return None

    # -- Google-verified signup intents --
    SIGNUP_INTENT_TTL_MIN = 30

    def create_signup_intent(self, email, ttl_min=30):
        raw = f"intent-{len(self.signup_intents)}-{email.strip().lower()}"
        self.signup_intents[raw] = email.strip().lower()
        return raw

    def signup_intent_email(self, raw):
        return self.signup_intents.get(raw)

    def consume_signup_intent(self, raw):
        return self.signup_intents.pop(raw, None)

    def record_event(self, name, **kw):
        self.events.append({"name": name, **kw})

    # -- /matches paging --
    def seed_matches(self, n):
        """n synthetic matches, already in the endpoint's sort order."""
        self.matches = [
            {"posting_id": f"p{i:03d}", "title": f"Job {i}", "company": "Co",
             "url": "https://example.com/j", "score": 10 - (i % 7), "summary": "why"}
            for i in range(n)
        ]

    def matched_jobs(self, profile_id, limit=50, offset=0, hidden=False):
        rows = [j for j in self.matches if (j["posting_id"] in self.hidden) == hidden]
        return rows[offset:offset + limit]

    def match_count(self, profile_id, hidden=False):
        return len([j for j in self.matches
                    if (j["posting_id"] in self.hidden) == hidden])

    def set_matches_hidden(self, profile_id, posting_ids, hidden):
        """Mirrors the real query's shape: ids that aren't this profile's live matches change
        nothing, and only a row whose state actually flips counts as changed."""
        mine = {j["posting_id"] for j in self.matches}
        changed = 0
        for pid in {str(p) for p in posting_ids}:
            if pid not in mine or (pid in self.hidden) == hidden:
                continue
            self.hidden.add(pid) if hidden else self.hidden.discard(pid)
            changed += 1
        return changed


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(webapp, "store", fake)
    # TestClient talks plain http://testserver; a Secure cookie would never be resent, so the
    # session flow can't be exercised. Turn the flag off for tests only (prod stays Secure).
    monkeypatch.setattr(webapp, "SESSION_COOKIE_SECURE", False)
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


# --------------------------------------------------------------------- sessions --
# Session-persisted magic links: clicking a magic link once mints a session cookie so the
# browser stays logged in. The system is still passwordless — the manage_token is the only
# credential typed. Each test breaks if its guard is removed (mutation-checked):
#   * a magic-link token still authenticates every read/write (email links must never break);
#   * a cookie-authed *write* is refused without the CSRF header (the CSRF defense);
#   * logout and expiry actually stop the cookie from authenticating.


def test_session_login_sets_cookie_and_whoami_works(client, store):
    login = client.post("/session", json={"token": TOKEN})
    assert login.status_code == 200
    assert login.json()["email"] == EMAIL
    assert webapp.SESSION_COOKIE in login.cookies          # a session cookie was set
    # The TestClient jar now carries it; whoami resolves the browser to the subscriber.
    who = client.get("/session")
    assert who.status_code == 200
    assert who.json()["email"] == EMAIL


def test_session_login_rejects_an_unknown_token(client, store):
    r = client.post("/session", json={"token": "nope"})
    assert r.status_code == 404
    assert webapp.SESSION_COOKIE not in r.cookies          # no session minted for a bad token
    assert store.sessions == {}


def test_whoami_401s_without_a_session(client, store):
    assert client.get("/session").status_code == 401


def test_logout_revokes_the_session(client, store):
    client.post("/session", json={"token": TOKEN})
    assert store.sessions                                   # logged in
    out = client.post("/logout")
    assert out.status_code == 200
    assert store.sessions == {}                            # server-side session gone
    assert client.get("/session").status_code == 401        # cookie no longer authenticates


def test_expired_session_is_rejected(client, store):
    client.post("/session", json={"token": TOKEN})
    store.expired = set(store.sessions)                    # force the session past its idle timeout
    assert client.get("/session").status_code == 401


def test_cookie_write_requires_the_csrf_header(client, store):
    client.post("/session", json={"token": TOKEN})
    # Cookie is sent automatically by the jar; without the CSRF header the write is refused.
    blocked = client.post("/preferences", json={"label": "New name"})
    assert blocked.status_code == 403
    assert store.updates == []                             # nothing was written
    # With the header it goes through.
    ok = client.post("/preferences", json={"label": "New name"},
                     headers={"X-JobDigest-Auth": "1"})
    assert ok.status_code == 200
    assert store.updates == [{"label": "New name"}]


def test_cookie_read_needs_no_csrf_header(client, store):
    client.post("/session", json={"token": TOKEN})
    r = client.get("/preferences")                          # cookie only, no header
    assert r.status_code == 200
    assert r.json()["email"] == EMAIL


def test_magic_link_token_still_authenticates_without_a_session(client, store):
    """Email links carry the manage_token and must keep working with no cookie at all."""
    assert client.get("/preferences", params={"token": TOKEN}).status_code == 200
    upd = client.post("/preferences", json={"token": TOKEN, "label": "X"})   # no CSRF header needed
    assert upd.status_code == 200
    assert store.updates == [{"label": "X"}]


# ------------------------------------------------------- role_category validation --
# `role_categories` used to be an unvalidated list[str], and the preferences form slugified
# free-text chips straight into it. A live subscriber ended up with `social_media_specialist`:
# no posting carries that value, so `role_category = any(...)` never fired and they held a
# filter that matched nothing, with no error on any side. A stored value that cannot match
# is worse than a rejected request, because only the request is visible to the user.

def test_unknown_role_category_is_rejected(client, store):
    r = client.post("/preferences", json={"token": TOKEN,
                                          "role_categories": ["social_media_specialist"]})
    assert r.status_code == 422
    assert "social_media_specialist" in r.text
    assert store.updates == []                # nothing was written


def test_known_role_categories_are_accepted(client, store):
    r = client.post("/preferences", json={"token": TOKEN,
                                          "role_categories": ["social_media", "design"]})
    assert r.status_code == 200
    assert store.updates == [{"role_categories": ["social_media", "design"]}]


def test_uncategorised_is_not_selectable_as_a_preference(client, store):
    """A real stored value, but as a *search* it means "postings we failed to classify"."""
    r = client.post("/preferences", json={"token": TOKEN, "role_categories": ["uncategorised"]})
    assert r.status_code == 422
    assert store.updates == []


def test_signup_rejects_an_unknown_role_category_too(client, store):
    """Four request models carry role_categories; a guard on only the one you remembered
    leaves the other three as the way in."""
    r = client.post("/subscribe", json={"email": "new@example.com",
                                        "role_categories": ["not_a_category"]})
    assert r.status_code == 422


def test_cookie_unsubscribe_revokes_sessions_and_needs_csrf(client, store):
    client.post("/session", json={"token": TOKEN})
    # Without the CSRF header the logged-in unsubscribe is refused and nothing happens.
    refused = client.post("/unsubscribe")
    assert refused.status_code == 403
    assert store.unsubscribed == [] and store.sessions
    # With it: the subscription is dropped and every session for that profile is torn down.
    done = client.post("/unsubscribe", headers={"X-JobDigest-Auth": "1"})
    assert done.status_code == 200
    assert store.unsubscribed == [TOKEN]
    assert PROFILE_ID in store.revoked_profiles
    assert store.sessions == {}


# --------------------------------------------------------------- google sign-in --
# "Sign in with Google" logs in an *existing* subscriber by their Google-verified email. It is
# dormant unless GOOGLE_CLIENT_ID/SECRET are set. Each test breaks if its guard is removed:
#   * unconfigured -> the endpoints don't exist (404), so a box with no client is inert;
#   * the OAuth `state` cookie must match what Google echoes back (login-CSRF defense);
#   * only a *verified* email that maps to a *live* subscription mints a session.


def _enable_google(monkeypatch):
    monkeypatch.setattr(webapp, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(webapp, "GOOGLE_CLIENT_SECRET", "test-secret")


def test_google_start_404s_when_unconfigured(client, store):
    assert client.get("/auth/google/start", follow_redirects=False).status_code == 404


def test_google_start_redirects_to_google_with_state(client, store, monkeypatch):
    _enable_google(monkeypatch)
    r = client.get("/auth/google/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(webapp.GOOGLE_AUTH_ENDPOINT)
    assert "test-client-id" in loc and "state=" in loc
    assert webapp.OAUTH_STATE_COOKIE in r.cookies       # state stashed for the callback to check


def test_google_callback_logs_in_existing_subscriber(client, store, monkeypatch):
    _enable_google(monkeypatch)
    monkeypatch.setattr(webapp, "_google_verify_code",
                        lambda code: {"email": EMAIL, "email_verified": True})
    r = client.get("/auth/google/callback", params={"code": "x", "state": "S"},
                   cookies={webapp.OAUTH_STATE_COOKIE: "S"}, follow_redirects=False)
    assert r.status_code == 302
    # Language-prefixed: the static export has no bare /preferences/ page any more.
    assert r.headers["location"].endswith(f"/{links.SITE_LOCALE}/preferences/")
    assert webapp.SESSION_COOKIE in r.cookies           # a session cookie was set
    assert store.sessions                                # and a server-side session created


def test_google_callback_state_mismatch_is_refused(client, store, monkeypatch):
    _enable_google(monkeypatch)
    monkeypatch.setattr(webapp, "_google_verify_code",
                        lambda code: {"email": EMAIL, "email_verified": True})
    r = client.get("/auth/google/callback", params={"code": "x", "state": "EVIL"},
                   cookies={webapp.OAUTH_STATE_COOKIE: "S"}, follow_redirects=False)
    assert r.status_code == 302 and "google=error" in r.headers["location"]
    assert store.sessions == {}                          # no login when state doesn't match


def test_google_callback_unverified_email_is_refused(client, store, monkeypatch):
    _enable_google(monkeypatch)
    monkeypatch.setattr(webapp, "_google_verify_code",
                        lambda code: {"email": EMAIL, "email_verified": False})
    r = client.get("/auth/google/callback", params={"code": "x", "state": "S"},
                   cookies={webapp.OAUTH_STATE_COOKIE: "S"}, follow_redirects=False)
    assert "google=error" in r.headers["location"]
    assert store.sessions == {}


def test_google_callback_new_email_starts_a_signup(client, store, monkeypatch):
    """A verified address with no subscription is sent into the wizard with a signup intent —
    NOT logged in, and NOT auto-subscribed."""
    _enable_google(monkeypatch)
    monkeypatch.setattr(webapp, "_google_verify_code",
                        lambda code: {"email": "stranger@example.com", "email_verified": True})
    r = client.get("/auth/google/callback", params={"code": "x", "state": "S"},
                   cookies={webapp.OAUTH_STATE_COOKIE: "S"}, follow_redirects=False)
    assert r.status_code == 302 and "google=signup" in r.headers["location"]
    assert webapp.SIGNUP_COOKIE in r.cookies              # an intent cookie was issued
    assert "stranger@example.com" in store.signup_intents.values()
    assert store.sessions == {}                           # not logged in yet
    assert store.created == []                            # and nothing subscribed yet


def test_google_callback_suppressed_email_is_refused(client, store, monkeypatch):
    """A previously-unsubscribed address must not be silently re-signed-up via Google."""
    _enable_google(monkeypatch)
    store.suppressed.add("gone@example.com")
    monkeypatch.setattr(webapp, "_google_verify_code",
                        lambda code: {"email": "gone@example.com", "email_verified": True})
    r = client.get("/auth/google/callback", params={"code": "x", "state": "S"},
                   cookies={webapp.OAUTH_STATE_COOKIE: "S"}, follow_redirects=False)
    assert "google=suppressed" in r.headers["location"]
    assert store.signup_intents == {} and store.created == []


# ------------------------------------------------------------- google signup ----
# The intent bridges "Google verified this email" and "wizard submitted". The email is taken
# from the server-side intent, never the request body, and creating an active subscription
# skips the confirm email because Google already proved the address.


def test_google_pending_returns_the_verified_email_with_the_cookie(client, store):
    raw = store.create_signup_intent("newbie@example.com")
    r = client.get("/auth/google/pending", cookies={webapp.SIGNUP_COOKIE: raw})
    assert r.status_code == 200 and r.json()["email"] == "newbie@example.com"


def test_google_pending_401s_without_an_intent(client, store):
    assert client.get("/auth/google/pending").status_code == 401


def test_subscribe_google_creates_active_sub_and_logs_in(client, store, sent):
    raw = store.create_signup_intent("newbie@example.com")
    r = client.post("/subscribe/google", json={"stack": ["python"]},
                    cookies={webapp.SIGNUP_COOKIE: raw})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert store.created == ["newbie@example.com"]        # a subscription was created
    assert webapp.SESSION_COOKIE in r.cookies             # and they're logged in
    assert store.sessions                                 # server-side session exists
    assert store.signup_intents == {}                     # intent consumed (single-use)
    assert len(sent) == 1                                 # welcome email (not a confirm email)


def test_subscribe_google_without_intent_is_refused(client, store, sent):
    r = client.post("/subscribe/google", json={"stack": ["python"]})
    assert r.status_code == 400
    assert store.created == [] and store.sessions == {}


def test_subscribe_google_email_comes_from_intent_not_body(client, store, sent):
    """The subscribed address is the Google-verified one, even if the body says otherwise."""
    raw = store.create_signup_intent("verified@example.com")
    client.post("/subscribe/google",
                json={"stack": ["python"], "email": "attacker@example.com"},
                cookies={webapp.SIGNUP_COOKIE: raw})
    assert store.created == ["verified@example.com"]      # body email ignored entirely


# ------------------------------------------------------------------- subscribe --
# One address = one subscription. The bug (2026-07-23): re-submitting an already-subscribed
# email created a second pending row, and confirming it double-sent the digest. These pin the
# guard — a live address is a silent no-op — and that the generic reply never leaks status.

def test_subscribe_creates_a_row_and_sends_one_confirm(client, store, sent):
    r = client.post("/subscribe", json={"email": "new@example.com"})
    assert r.status_code == 200
    assert store.created == ["new@example.com"]
    assert len(sent) == 1                             # exactly one confirmation email


def test_subscribe_is_a_noop_for_an_already_live_address(client, store, sent):
    store.live_emails.add("taken@example.com")
    r = client.post("/subscribe", json={"email": "taken@example.com"})
    assert r.status_code == 200
    assert r.json()["status"] == "pending"           # same generic reply — no status leak
    assert store.created == []                        # NO duplicate row
    assert sent == []                                 # and NO second confirm email


def test_subscribe_noop_for_suppressed_address(client, store, sent):
    store.suppressed.add("gone@example.com")
    r = client.post("/subscribe", json={"email": "gone@example.com"})
    assert r.status_code == 200
    assert store.created == [] and sent == []


# ------------------------------------------------------------------ manage-link --
# The passwordless "email me my settings link" recovery path. Three properties are load-bearing
# and each test breaks if the corresponding guard is removed: the link reaches only the owner's
# inbox, a repeat request can't flood it, and the response can't be used to enumerate subscribers.

def test_manage_link_emails_the_settings_link_to_a_subscriber(client, store, sent):
    store.manageable["me@example.com"] = TOKEN
    r = client.post("/manage-link", json={"email": "me@example.com"})
    assert r.status_code == 200
    assert len(sent) == 1                                  # exactly one email
    to, _subj, html_body, text = sent[0]
    assert to == "me@example.com"                          # only ever to the owner's own inbox
    assert TOKEN in html_body and TOKEN in text            # the manage link (the credential) is in it


def test_manage_link_second_request_is_rate_limited(client, store, sent):
    """Knowing an address must not let you flood its inbox — the per-address cooldown caps it."""
    store.manageable["me@example.com"] = TOKEN
    first = client.post("/manage-link", json={"email": "me@example.com"})
    second = client.post("/manage-link", json={"email": "me@example.com"})
    assert first.json() == second.json()                  # identical reply both times
    assert len(sent) == 1                                 # but only ONE email actually went out


def test_manage_link_does_not_enumerate_subscribers(client, store, sent):
    """The response for an unknown/never-subscribed address must be byte-identical to a real
    one, or the endpoint becomes an oracle for 'is this person a subscriber?'."""
    store.manageable["me@example.com"] = TOKEN
    subscribed = client.post("/manage-link", json={"email": "me@example.com"})
    unknown = client.post("/manage-link", json={"email": "nobody@example.com"})
    assert unknown.status_code == subscribed.status_code
    assert unknown.json() == subscribed.json()            # same message, no status leak
    assert len(sent) == 1                                 # nothing sent for the unknown address


# --------------------------------------------------------------------- preview --
# The instant post-signup preview: read-only, keyword-ranked, no score, no writes. These pin
# the two guarantees that matter — a mismatched-seniority role can't slip in, and the endpoint
# never claims an AI "score" it didn't compute.

def _cand(pid, title, desc="", seniority="mid", region="eu"):
    return {"posting_id": pid, "title": title, "company": "Co", "url": "https://ex.com/" + pid,
            "location": "Remote", "region": region, "seniority": seniority,
            "work_type": "permanent", "salary_raw": None, "description": desc}


def test_preview_ranks_by_keyword_overlap_and_carries_no_score(client, store):
    store.query_shortlist = lambda profile, limit=60: [
        _cand("a", "Data role", "we use pandas"),
        _cand("b", "Python Engineer", "python and sql all day"),
        _cand("c", "Analyst", "excel only"),
    ]
    r = client.post("/preview", json={"stack": ["python", "sql"], "seniorities": ["mid"]})
    assert r.status_code == 200
    body = r.json()
    ids = [j["posting_id"] for j in body["jobs"]]
    assert ids[0] == "b"                              # two term hits ranks first
    assert set(ids) == {"a", "b", "c"}
    assert all("score" not in j for j in body["jobs"])   # keyword preview never claims a score
    assert body["jobs"][0]["why"].lower().startswith("mentions")


def test_preview_applies_seniority_hard_filter_but_keeps_unknown(client, store):
    store.query_shortlist = lambda profile, limit=60: [
        _cand("jr", "Junior", seniority="junior"),
        _cand("sr", "Senior", seniority="senior"),
        _cand("uk", "Unknown", seniority=None),
    ]
    r = client.post("/preview", json={"stack": [], "seniorities": ["junior"]})
    ids = {j["posting_id"] for j in r.json()["jobs"]}
    assert "jr" in ids and "uk" in ids               # target level + unknown-level both pass
    assert "sr" not in ids                            # clearly-mismatched senior is dropped


def test_preview_is_read_only_and_bounded(client, store):
    store.query_shortlist = lambda profile, limit=60: [_cand(str(i), "Role") for i in range(30)]
    r = client.post("/preview", json={"stack": ["python"]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 30                        # honest total
    assert len(body["jobs"]) == webapp.PREVIEW_LIMIT  # but only a handful are returned


# --------------------------------------------------------------------- confirm --

@pytest.fixture
def sent(monkeypatch):
    """Capture outbound mail instead of sending it. Records the full (to, subject, html, text)
    so a test can assert what actually reached the body, not just that a send happened."""
    box: list[tuple] = []
    monkeypatch.setattr(webapp.mailer, "send",
                        lambda to, subj, html, text, **kw: box.append((to, subj, html, text)) or "ok")
    # `lang` is passed positionally by the API (migration 013), so the stub has to accept it —
    # capture it too, so a test can assert the welcome went out in the subscriber's language.
    monkeypatch.setattr(webapp.transactional, "render_welcome",
                        lambda email, url, lang="en": (f"Welcome[{lang}]", "<p>hi</p>", "hi"))
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


# ------------------------------------------------------------------- /matches paging --
# `count` is the unbounded total while `jobs` is one page, so a caller that renders `jobs`
# under a headline built from `count` silently drops the difference. That is exactly what
# shipped: every subscriber had 76-131 matches against a 25-row page, so the page claimed
# "127 matches ... this is the full list" and rendered 25. The rows below the cut are the
# sub-EMAIL_MIN_SCORE picks this page exists to show, and /matches being the complete record
# is what makes never-email-twice suppression safe. These pin that paging can reach all of it.

def test_matches_first_page_is_capped_but_reports_the_true_total(client, store):
    store.seed_matches(127)
    r = client.get("/matches", params={"token": TOKEN})

    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 127                      # the honest total, not the page size
    assert len(body["jobs"]) == webapp.MATCHES_PAGE_LIMIT
    assert body["offset"] == 0
    assert body["limit"] == webapp.MATCHES_PAGE_LIMIT


def test_paging_reaches_every_match_exactly_once(client, store):
    """Walk the offsets the way the page does and expect the whole list back, no row
    duplicated and none skipped."""
    store.seed_matches(127)

    seen: list[str] = []
    offset = 0
    while True:
        body = client.get("/matches", params={"token": TOKEN, "offset": offset}).json()
        if not body["jobs"]:
            break
        seen.extend(j["posting_id"] for j in body["jobs"])
        offset = len(seen)
        assert offset <= body["count"], "paging walked past the reported total"

    assert len(seen) == 127
    assert len(set(seen)) == 127                     # nothing served twice
    assert seen == [f"p{i:03d}" for i in range(127)]  # and in the ranked order


def test_offset_past_the_end_is_an_empty_page_not_an_error(client, store):
    """A hand-edited offset should end the list, not break someone's matches."""
    store.seed_matches(30)
    body = client.get("/matches", params={"token": TOKEN, "offset": 999}).json()
    assert body["jobs"] == [] and body["count"] == 30


def test_negative_offset_is_clamped_to_the_first_page(client, store):
    store.seed_matches(30)
    body = client.get("/matches", params={"token": TOKEN, "offset": -5}).json()
    assert body["offset"] == 0
    assert [j["posting_id"] for j in body["jobs"]][:3] == ["p000", "p001", "p002"]


def test_matches_still_requires_a_valid_token(client, store):
    store.seed_matches(5)
    assert client.get("/matches", params={"token": "nope"}).status_code == 404


# ------------------------------------------------------------- hiding jobs (/matches/hide) --
# Hiding is a *subscriber-visible promise with two halves*: the job leaves the matches page
# and the daily email, and it is still there to be unhidden. Both halves are load-bearing —
# a hide that quietly deleted the row would make "unhide" a lie, and a hide that only
# repainted the page would keep emailing a job someone already applied to. These pin both,
# plus the two guards that matter on a write endpoint: it must be a POST (link scanners
# fetch URLs found in email — the same reason /unsubscribe confirms), and one call must not
# be able to rewrite an unbounded number of rows.

def test_hidden_jobs_leave_the_matches_list_but_are_still_there(client, store):
    store.seed_matches(10)
    ids = ["p000", "p003"]

    r = client.post("/matches/hide", json={"token": TOKEN, "posting_ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] == 2
    assert body["visible_count"] == 8 and body["hidden_count"] == 2

    visible = client.get("/matches", params={"token": TOKEN}).json()
    assert visible["count"] == 8
    assert [j["posting_id"] for j in visible["jobs"]] == [
        p for p in (f"p{i:03d}" for i in range(10)) if p not in ids
    ]
    # Nothing was deleted: the other view is the complete record of what was hidden.
    hidden = client.get("/matches", params={"token": TOKEN, "hidden": "true"}).json()
    assert hidden["hidden"] is True
    assert [j["posting_id"] for j in hidden["jobs"]] == ids
    # Both counts travel with both views, so each page can link to the other.
    assert visible["hidden_count"] == 2 and hidden["hidden_count"] == 2


def test_unhide_puts_a_job_back_where_it_was(client, store):
    store.seed_matches(5)
    client.post("/matches/hide", json={"token": TOKEN, "posting_ids": ["p001"]})

    r = client.post("/matches/unhide", json={"token": TOKEN, "posting_ids": ["p001"]})
    assert r.json()["visible_count"] == 5 and r.json()["hidden_count"] == 0
    body = client.get("/matches", params={"token": TOKEN}).json()
    assert "p001" in [j["posting_id"] for j in body["jobs"]]


def test_hiding_a_job_that_isnt_yours_changes_nothing(client, store):
    """The ids come from a browser. Scoping the update to the profile is the authorisation,
    so an id belonging to someone else is a no-op rather than a cross-account write."""
    store.seed_matches(3)
    r = client.post("/matches/hide",
                    json={"token": TOKEN, "posting_ids": ["not-mine", "p001"]})
    assert r.json()["changed"] == 1
    assert store.hidden == {"p001"}


def test_hiding_needs_a_real_credential(client, store):
    store.seed_matches(3)
    assert client.post("/matches/hide",
                       json={"token": "nope", "posting_ids": ["p000"]}).status_code == 404
    assert store.hidden == set()


def test_a_get_cannot_hide_anything(client, store):
    """Same rule as /unsubscribe: mail clients and security scanners fetch links they find."""
    store.seed_matches(3)
    assert client.get("/matches/hide", params={"token": TOKEN, "posting_ids": "p000"}
                      ).status_code == 405
    assert store.hidden == set()


def test_one_request_cannot_rewrite_an_unbounded_number_of_rows(client, store):
    store.seed_matches(3)
    too_many = [f"x{i}" for i in range(webapp.MAX_HIDE_IDS + 1)]
    r = client.post("/matches/hide", json={"token": TOKEN, "posting_ids": too_many})
    # Refused whole, not silently truncated: a partial write would leave the page and the
    # database disagreeing about what is hidden.
    assert r.status_code == 400
    assert store.hidden == set()


def test_cookie_authed_hide_still_needs_the_csrf_header(client, store):
    """A cookie-authed write is reachable cross-origin without it — the same guard every
    other cookie-authed write in this app carries."""
    store.seed_matches(3)
    client.post("/session", json={"token": TOKEN})        # cookie now in the jar

    blocked = client.post("/matches/hide", json={"posting_ids": ["p000"]})
    assert blocked.status_code == 403
    assert store.hidden == set()                          # nothing was written

    ok = client.post("/matches/hide", json={"posting_ids": ["p000"]},
                     headers={"X-JobDigest-Auth": "1"})
    assert ok.status_code == 200
    assert store.hidden == {"p000"}
