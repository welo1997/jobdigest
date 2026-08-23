"""The timer enablement manifest is the deployed schedule, so drift in it is silent.

`deploy/deploy.sh` copies unit files into `/etc/systemd/system` and, since 2026-08-22,
reconciles *which* of them are enabled from `deploy/jobdigest-timers.enabled`. Before that it
only copied, and the cost was measured: `jobdigest-categorize.timer` — the metered title
classifier — shipped on 2026-08-17 and was still `disabled` five days later, while the two
RETIRED Drive timers it replaced kept firing weekly into a folder no routine reads.
`jobdigest-categorization-watchdog.timer`, whose entire job is to notice that the residue
stopped shrinking, was disabled on the same box for the same reason. `title_categories` held
250 rows against a residue of 18 679 postings over 15 854 distinct titles.

**Nothing raised, and nothing could have.** A residue that stops shrinking looks exactly like
a taxonomy that has caught everything it can. The unit's own install comment even named the
hazard — "deploy.sh copies units but never enables/disables" — and listed the two commands to
run. Naming a manual step is not performing it, which is why enablement is now data and why
these tests exist: a manifest nobody checks is the same failure with an extra file.

So the guarantees here are deliberately boring and structural:

- every timer the repo ships is *decided* — enabled by the manifest, or named in it as
  retained-and-not-enabled. A new timer that is in neither is the 2026-08-17 failure exactly:
  it ships, it never runs, and the only symptom is an absence.
- a name the manifest enables exists, and is a unit systemd can actually enable (a timer with
  no `[Install]` section fails to enable, and a deploy that enables nothing looks like a
  deploy that worked).
- the metered categorizer and its Drive pair are never both enabled — one job, one table, one
  schedule slot.
- `deploy.sh` still reads the manifest. Same technique as the `_validated_rows` both-callers
  test: the guarantee is worthless if the caller quietly stops calling.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
MANIFEST = DEPLOY / "jobdigest-timers.enabled"
DEPLOY_SH = DEPLOY / "deploy.sh"

#: The metered classifier and the file-exchange pair are the same job over two transports.
EXCLUSIVE = ("jobdigest-categorize.timer",
             ("jobdigest-categorize-export.timer", "jobdigest-categorize-import.timer"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def enabled_timers() -> list[str]:
    """The manifest's payload: one unit name per line, `#` comments stripped."""
    out = []
    for line in _text(MANIFEST).splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            out.append(name)
    return out


def shipped_timers() -> list[str]:
    return sorted(p.name for p in DEPLOY.glob("jobdigest-*.timer"))


def test_manifest_exists_and_is_not_empty():
    """An empty manifest would disable every timer on the box — deploy.sh refuses it, and so
    does this, because the refusal is only useful if someone notices before shipping."""
    assert MANIFEST.exists(), f"{MANIFEST} is the deployed schedule; it cannot be missing"
    assert enabled_timers(), "manifest enables nothing — that is a silent full stop, not a config"


def test_every_shipped_timer_is_decided():
    """Enabled, or named as deliberately-not-enabled. Never merely absent.

    This is the test that would have caught 2026-08-17: `jobdigest-categorize.timer` was added
    to `deploy/` and nothing anywhere recorded whether it should run.
    """
    text = _text(MANIFEST)
    enabled = set(enabled_timers())
    undecided = [t for t in shipped_timers() if t not in enabled and t not in text]
    assert not undecided, (
        "these timers are shipped but the manifest neither enables them nor records why not: "
        f"{undecided}. A timer in neither state ships disabled and its absence is the only symptom."
    )


def test_enabled_timers_exist_in_the_repo():
    """deploy.sh iterates the units it ships, so a manifest typo enables nothing at all."""
    missing = [t for t in enabled_timers() if not (DEPLOY / t).exists()]
    assert not missing, f"manifest enables units this repo does not ship: {missing}"


@pytest.mark.parametrize("timer", enabled_timers())
def test_enabled_timer_is_installable(timer: str):
    """`systemctl enable` on a unit with no `[Install]` fails; the deploy would look fine."""
    body = _text(DEPLOY / timer)
    assert "[Install]" in body, f"{timer} is enabled by the manifest but has no [Install] section"
    assert re.search(r"^WantedBy=", body, re.M), f"{timer} has [Install] but no WantedBy="
    # Monotonic and calendar timers are both real: the healthcheck is OnBootSec/OnUnitActiveSec
    # (every 5 minutes, because a monitor that can only fire once fails at "you were looking"),
    # everything else is OnCalendar. A timer with neither is enabled and never fires.
    assert re.search(r"^On(Calendar|BootSec|UnitActiveSec|ActiveSec|StartupSec)=", body, re.M), (
        f"{timer} declares no schedule — enabling it would be a no-op"
    )


def test_the_exclusive_pair_is_not_both_enabled():
    """One residue, one `title_categories`, one Sunday 09:30 slot. Two writers reads, from
    outside, exactly like a routine that stopped."""
    enabled = set(enabled_timers())
    metered, drive = EXCLUSIVE
    both = [d for d in drive if d in enabled]
    assert not (metered in enabled and both), (
        f"{metered} and {both} are the same job over two transports; enable exactly one"
    )


def test_deploy_sh_still_reads_the_manifest():
    """A manifest the deploy script ignores is decoration, and the drift would be invisible."""
    body = _text(DEPLOY_SH)
    assert MANIFEST.name in body, (
        f"deploy.sh no longer references {MANIFEST.name} — enablement has gone back to being "
        "a manual step, which is how the metered categorizer sat disabled for five days"
    )
    assert "systemctl enable" in body and "systemctl disable" in body, (
        "deploy.sh must both enable what the manifest lists and disable what it does not; "
        "half a reconciliation leaves a retired timer firing"
    )


def test_retired_units_are_named_with_a_reason():
    """Retirement is a deleted line plus a recorded why. Without the why, the next session
    re-enables it — the file exchange was retired twice for want of that sentence."""
    lines = _text(MANIFEST).splitlines()
    for timer in shipped_timers():
        if timer in set(enabled_timers()):
            continue
        # The name has to appear at all (test_every_shipped_timer_is_decided); here we check it
        # is explained. The reason wraps onto the following lines — the pair is documented as a
        # pair — so the window is the naming line plus the two after it.
        hits = [i for i, l in enumerate(lines) if timer in l]
        window = "\n".join(l for i in hits for l in lines[i:i + 3])
        assert re.search(r"RETIRED|not enabled|fallback|superseded", window, re.I), (
            f"{timer} is not enabled and the manifest does not say why; a bare name reads as "
            "an oversight and gets 'fixed' by the next person"
        )
