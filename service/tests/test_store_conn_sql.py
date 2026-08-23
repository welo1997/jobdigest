"""`store.cursor()` must survive a connection the DB server dropped while it sat idle.

**The bug, in production on 2026-08-23.** The daily pipeline's liveness sweep reads its target
list (the ~500 matched + shortlist URLs), then probes them over HTTP for minutes, then writes
the confidently-closed ones back with `deactivate_postings`. The DB container restarted in that
minutes-long gap, so the pooled connection the write reused had been terminated by the server.
`ThreadedConnectionPool` never checks liveness on `getconn`, so it handed the dead connection
straight back; `deactivate_postings` raised `server closed the connection unexpectedly`; and
because the sweep is deliberately non-fatal, it logged the error and deactivated **0 of 34**
correctly-detected closed postings. One of them — an expired startupjobs listing whose own page
said "the offer is no longer valid" — then reached a subscriber's digest.

The subtlety this test has to honour: after a *server-side* termination the client's
`conn.closed` stays 0 until the next I/O, so psycopg2's own pool keeps the corpse and hands it
back. A connection the *client* closed is already discarded by `putconn`, so closing one here
would prove nothing (it was the first draft of this test, and it passed against the unfixed
code). The only faithful reproduction is to kill the backend from a second connection and leave
the pooled client believing it is healthy — which is exactly what a DB restart does.

`cursor()` now pings each borrowed connection and drops the dead ones. Reverting that fix turns
the final block into `psycopg2.OperationalError: server closed the connection unexpectedly`.

Skipped when TEST_DATABASE_URL is unset; CI stands up a Postgres and fails if it goes back to
skipping. Same harness as the other *_sql tests.
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")


@pytest.fixture(autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None
    try:
        yield
    finally:
        if store._POOL is not None:
            try:
                store._POOL.closeall()
            except Exception:
                pass
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def _kill_backend(pid: int) -> None:
    """Terminate one backend from a separate connection — a DB restart in miniature."""
    killer = psycopg2.connect(TEST_DSN)
    try:
        killer.autocommit = True
        with killer.cursor() as kc:
            kc.execute("select pg_terminate_backend(%s)", (pid,))
    finally:
        killer.close()


def test_cursor_heals_a_server_terminated_connection():
    store.init_pool()

    # Borrow a connection, learn its backend pid, and return it to the pool IDLE and — as far as
    # the client is concerned — healthy.
    with store.cursor() as cur:
        cur.execute("select pg_backend_pid() as pid")
        pid = cur.fetchone()["pid"]

    # Kill that backend from the outside. The pooled client still reports conn.closed == 0, so a
    # naive getconn hands this corpse straight to the next caller.
    _kill_backend(pid)

    # The heal: cursor() must ping, find the corpse, discard it, open a fresh connection, and
    # serve the query — not raise 'server closed the connection unexpectedly'.
    with store.cursor(commit=True) as cur:
        cur.execute("select 42 as n")
        assert cur.fetchone()["n"] == 42

    # And it must keep working afterwards — the replacement connection is healthy and pooled.
    with store.cursor() as cur:
        cur.execute("select 7 as n")
        assert cur.fetchone()["n"] == 7
