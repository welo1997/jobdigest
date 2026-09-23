"""Backup retention broke production twice, and each time it looked like configuration.

On 2026-09-06 and again on 2026-09-20 the VPS root filesystem hit 100%, Postgres PANICked on
its end-of-recovery checkpoint and crash-looped for ~24 h the second time. Neither failure was
a bug in the backup logic — the dumps were valid, the prune ran, the units were installed.
What was wrong was arithmetic nobody had done against the actual media:

- the unit did not set `JOBDIGEST_BACKUP_LOCAL_KEEP_DAYS`, so the script's own default took
  over and kept 30 days on a 38 GB disk;
- `KEEP_DAYS` was 30 against a Drive free tier with room for ~9 dumps, so the remote prune had
  never deleted a single file and the account wedged at its quota;
- the prune ran *after* the upload, so it could only ever free space for a copy that had
  already succeeded or already failed.

These are all one-line reorderings and one-word values, which is exactly the kind of thing a
later refactor undoes while everything still looks green — the symptom is two weeks away. So
the guarantees are asserted structurally, against the shipped files.

See `docs/jobdigest.md`, "Retention is bounded by the medium, not by the config".
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP_SH = ROOT / "deploy" / "jobdigest-backup.sh"
BACKUP_UNIT = ROOT / "deploy" / "jobdigest-backup.service"
HEALTHCHECK_SH = ROOT / "deploy" / "jobdigest-healthcheck.sh"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_remote_prune_runs_before_the_upload():
    """Ordering is the whole guarantee: the upload is the step that needs free space.

    Pruning behind it frees space for a transfer whose outcome is already decided, which is
    how the Drive account reached 516 MB free against an 845 MB dump.
    """
    body = _text(BACKUP_SH)
    # Match the command and its age filter separately rather than as one literal string: the
    # prune legitimately carries other flags between them (see the --drive-use-trash guard
    # below), and a literal match turns "a flag was added" into "the prune is gone".
    prune_m = re.search(r"rclone delete\b[^\n]*--min-age", body)
    upload = body.find("rclone copy")
    assert prune_m, "the remote prune is gone; the off-box folder now grows without bound"
    prune = prune_m.start()
    assert upload != -1, "the off-box upload is gone"
    assert prune < upload, (
        "rclone delete must run BEFORE rclone copy — pruning after the upload cannot free "
        "space for that upload, which is how a 30-day window held 10 days of history and "
        "then stopped accepting new dumps entirely"
    )


def test_remote_prune_actually_reclaims_quota():
    """A prune that trashes has not pruned. Drive bills trashed objects for 30 days.

    This is the third distinct way this one line has failed, and the first two fixes made it
    *worse*. `rclone delete` maps to Drive's trash, so every dump the prune "removed" kept
    consuming the quota — and lowering KEEP_DAYS 30 -> 7 (#118) simply trashed more, faster.

    Measured on the live account 2026-09-23, after that fix had shipped: 15 GiB total, 517 MiB
    free against an 845 MiB dump, with 4.40 GiB in the trash and all six objects in there
    jobdigest-*.dump.gpg files this prune had already deleted. Uploads had failed with
    storageQuotaExceeded every night since 2026-09-21 while the prune logged success each time.

    So the assertion is on the OUTCOME, not the presence of a prune: the delete must be one
    that reclaims space. Mutation check — drop the flag and this goes red while
    `test_remote_prune_runs_before_the_upload` stays green, which is precisely the gap that
    let three weeks of backups silently not exist.
    """
    body = _text(BACKUP_SH)
    prune = re.search(r"rclone delete\b[^\n]*", body)
    assert prune, "the remote prune is gone"
    assert "--drive-use-trash=false" in prune.group(0), (
        "the remote prune deletes to Drive's trash, where the objects keep counting against "
        "the 15 GiB quota for 30 days. The prune will report success every night and free "
        "nothing, and the upload will fail with storageQuotaExceeded — which is exactly what "
        "happened from 2026-09-21. Pass --drive-use-trash=false."
    )
    # The scoped delete is the right tool; `rclone cleanup` is not. It empties the WHOLE
    # account trash, and this remote lives in the owner's personal Drive beside their own
    # files — a backup script must never permanently destroy something a human put in the bin.
    # Read CODE, not prose: the script explains in a comment why it does not call cleanup, and
    # a bare substring check would trip over that explanation. Same trap this file already
    # documents for DISK_PCT_MAX — match the invocation, not the name.
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    assert "rclone cleanup" not in code, (
        "rclone cleanup empties the entire Drive account's trash, including the owner's own "
        "deleted files. Scope the deletion with --drive-use-trash=false instead."
    )


def test_unit_sets_both_retention_knobs_explicitly():
    """The script's defaults must never be what governs production.

    PR #104 added `LOCAL_KEEP_DAYS` to the script and the unit, and only the script half
    reached the box — so the default (KEEP_DAYS, 30) silently governed for fifteen days and
    refilled the disk. A knob that production relies on is a knob production must state.
    """
    unit = _text(BACKUP_UNIT)
    for var in ("JOBDIGEST_BACKUP_KEEP_DAYS", "JOBDIGEST_BACKUP_LOCAL_KEEP_DAYS"):
        assert re.search(rf"^Environment={var}=\d+", unit, re.M), (
            f"{var} is not set explicitly in jobdigest-backup.service; production would fall "
            "back to the script default, which is the 2026-09-20 outage exactly"
        )


def test_offbox_retention_fits_the_free_tier():
    """`KEEP_DAYS` is bounded by what the remote can physically hold, not by what we want.

    Measured 2026-09-21: a 15 GiB Drive free tier with ~6.9 GiB of other content holds about
    9 dumps at ~845 MB. A window wider than the medium is not retention — the age filter never
    fires and the folder simply fills. 9 is the hard ceiling; the assert leaves headroom
    because the dump grows ~10 MB/day.
    """
    unit = _text(BACKUP_UNIT)
    keep = int(re.search(r"^Environment=JOBDIGEST_BACKUP_KEEP_DAYS=(\d+)", unit, re.M).group(1))
    assert keep <= 8, (
        f"KEEP_DAYS={keep} exceeds what the Drive free tier holds (~9 dumps). Raising it "
        "means checking the medium first: `rclone about gdrive:`. Until the quota grows, a "
        "larger number does not buy history — it buys a failed upload."
    )


def test_healthcheck_watches_disk_on_both_percent_and_bytes():
    """Percent alone is the wrong unit for the thing that actually breaks.

    Postgres needs a few MB to checkpoint; a nightly dump needs ~850 MB and the off-box
    ciphertext briefly needs another. A large disk sitting at a calm percentage can still be
    unable to take a backup, so the byte floor has to be there alongside the ratio.
    """
    body = _text(HEALTHCHECK_SH)
    # Match the ASSIGNMENT, not the name. A bare substring check passes even with the
    # threshold deleted, because the name survives in the comparison that reads it — which is
    # how this test first failed to notice its own mutation.
    for var in ("DISK_PCT_MAX", "DISK_FREE_MIN_MB"):
        assert re.search(rf"^{var}=\d+", body, re.M), (
            f"{var} is not set in the healthcheck; it no longer tests disk on both axes, and "
            "it went red only after Postgres had already crash-looped for hours on 2026-09-20"
        )
    assert re.search(r"\bdf\b", body), "the disk check no longer reads df"
    # -P and --output are mutually exclusive in GNU coreutils; the combination fails outright
    # and the guard silently degrades to 'no reading, no alarm'.
    assert not re.search(r"df\s+-[A-Za-z]*P[A-Za-z]*\s+--output", body), (
        "df -P cannot be combined with --output; this guard would print an error and the "
        "check would read as healthy"
    )
