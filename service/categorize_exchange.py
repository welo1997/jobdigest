"""Model classification for the residue, over the file exchange — no API key.

What neither the title patterns nor the publishers' occupation codes reach is a long
multilingual tail: ~33k active postings on 2026-08-09, in Swedish, Czech, Norwegian and the
private vocabulary of individual employers. A model reads those titles trivially. Paying an
API to do it is a billing decision this project has already made against (`CLAUDE.md`: no
API key on the JobDigest side), so this takes the route the matcher already takes — a JSON
file to a private Drive folder, a claude.ai routine on the owner's own subscription, a JSON
file back.

    export   uncategorised titles           -> titles.json   -> Drive
    routine  claude.ai reads titles.json    -> categories.json           (no DB, no key)
    import   validate every row against the taxonomy -> `title_categories`
    ingest   consults the cache where patterns and codes both decline

**It suits titles better than it suits matching.** A title is ~10 tokens with no
description, the answer is a flat map, the work is idempotent, and — unlike
`shortlists.json` — the file carries **no personal data whatsoever**: public job titles and
integer indices, nothing else. `test_categorize_exchange.py` asserts that rather than
trusting it.

**Titles go out with an index and answers come back by index**, exactly as candidates do in
the matcher. A returned index that was never asked about is dropped; a returned category
that is not in `taxonomy.CATEGORIES` is dropped and counted. This is the 2026-08-08 hint
guard arriving by a new route: 14 135 postings once sat in categories no query could name,
because a third-party string was trusted to be a category. A model's answer is a
third-party string too.

**Nothing here is on the digest's critical path.** The export's deadline is the ~06:00
routine (`CLAUDE.md`), and adding N+1 work to that window is how a subscriber silently gets
yesterday's file. This runs on its own weekly timer, against its own Drive files, and a
failure costs a slower-shrinking residue and nothing else.

    python -m service.categorize_exchange export /exchange/titles.json [--limit N]
    python -m service.categorize_exchange import /exchange/categories.json \\
        --asked /exchange/titles.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

from service import store, taxonomy

logger = logging.getLogger(__name__)

#: How many distinct titles one export may carry. The one-off residue was ~33k distinct
#: titles ≈ 340k tokens, which fits a single context; steady state after that is ~2 400 new
#: titles a day. The default is deliberately above the steady state and below the residue, so
#: the first few runs drain it in slices and every later run is complete — and a truncated run
#: says what it dropped rather than looking finished.
DEFAULT_LIMIT = int(os.environ.get("CATEGORIZE_EXPORT_LIMIT", "8000"))

ROUTINE_INSTRUCTIONS = (
    "You are JobDigest's title classifier. Below is a list of job titles, each with an "
    "integer index `i`. For EACH title, decide which ONE role category from `categories` "
    "best describes the job, and return it by index. "
    "The titles are multilingual (Swedish, Czech, Slovak, Norwegian, German, English) — "
    "translate as needed. Many are compound nouns; read the head noun. "
    "**If no category genuinely fits, answer \"uncategorised\".** That is a real answer and "
    "the right one for a title that names no profession (\"Extrajobb\", \"Är du den vi "
    "söker?\", a bare company or city name) — do not stretch to the nearest category, "
    "because a wrong category puts the job in a stranger's digest, while `uncategorised` "
    "only leaves it where it already is. "
    "Answer for every index exactly once. "
    "Return JSON only, no prose: "
    "{\"categories\": [{\"i\": 0, \"category\": \"sales\"}, ...]}"
)

#: Trailing junk that says nothing about the role. Stripped from the *ends* only — a hyphen
#: inside "Front-end" carries meaning, one at the end of "Butiksmedarbetare -" does not.
_EDGE_JUNK = " \t\r\n-–—•|,;:/\\!?.*·"


def normalise_title(title: str | None) -> str:
    """The cache key: one definition, used by the export, the import and the ingest lookup.

    Case-folded, NFKC-normalised, whitespace collapsed, edge punctuation stripped. It does
    **not** try to strip the employer out ("Butiksmedarbetare, Willys Helsingborg Berga"),
    and that restraint is the point: a normalisation that merges two titles which are not the
    same job files one of them under the other's category, and nothing downstream can tell.
    Conservative keys cost cache misses; aggressive keys cost wrong answers, and only one of
    those two is recoverable.

    Truncated at 300 characters so a pathological title cannot become a pathological key.
    """
    text = unicodedata.normalize("NFKC", title or "").casefold()
    text = re.sub(r"\s+", " ", text).strip(_EDGE_JUNK).strip()
    return text[:300]


def export_uncategorised(path: str, limit: int = DEFAULT_LIMIT) -> int:
    """Write the titles the routine should read. Returns how many it asked about.

    Ordered by how many active postings share the title, so a truncated run answers for the
    titles that stand for the most inventory first — and logs what it left behind. A silent
    cap reads as "we covered everything", which is the failure `CLAUDE.md` names for every
    other bounded step in this repo.
    """
    asked = store.asked_title_keys()
    grouped: dict[str, dict] = {}
    for row in store.uncategorised_titles():
        key = normalise_title(row["title"])
        if not key or key in asked:
            continue
        entry = grouped.setdefault(key, {"title": row["title"], "n": 0})
        entry["n"] += int(row["n"])

    ranked = sorted(grouped.items(), key=lambda kv: (-kv[1]["n"], kv[0]))
    kept, dropped = ranked[:limit], ranked[limit:]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instructions": ROUTINE_INSTRUCTIONS,
        "categories": list(taxonomy.CATEGORIES),
        # Index, then title. No posting id, no company, no URL, no subscriber, no email —
        # the whole file is public job text, which is what makes this exchange cheap to
        # defend compared with the matcher's.
        "titles": [{"i": i, "title": entry["title"]}
                   for i, (_key, entry) in enumerate(kept)],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    if dropped:
        logger.warning("categorize export: %d distinct titles (%d postings) did NOT fit the "
                       "limit of %d and were not asked about — they stay uncategorised until "
                       "a later run", len(dropped), sum(e["n"] for _k, e in dropped), limit)
    logger.info("categorize export: asked about %d titles (%d postings) -> %s",
                len(kept), sum(e["n"] for _k, e in kept), path)
    return len(kept)


def _asked_titles(path: str) -> dict[int, str]:
    """index -> title, read back from the file the export wrote."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    titles = data.get("titles", data) if isinstance(data, dict) else data
    out: dict[int, str] = {}
    if not isinstance(titles, list):
        return out
    for item in titles:
        if isinstance(item, dict) and isinstance(item.get("i"), int) and item.get("title"):
            out[item["i"]] = str(item["title"])
    return out


def import_categories(path: str, asked_path: str) -> int:
    """Read the routine's answers, validate every one, store them. Returns rows written.

    Accepts `{"categories": [{"i": 0, "category": "sales"}, ...]}` or a bare list.

    This crosses a trust boundary and nothing in it is taken on faith:

    - an index that was never exported is dropped (the file cannot invent a title),
    - a category that is not in `taxonomy.CATEGORIES` is dropped and counted — writing it
      would put postings in a category no subscriber can select and no query can match,
      which is precisely the 14 135-row failure of 2026-08-08,
    - a malformed record is skipped rather than aborting the import, so one bad row does not
      cost the whole run,
    - `uncategorised` is *stored*, because "asked, no answer" has to be remembered or the
      export asks again forever.

    A malformed *file* still raises: that is a broken routine, and it should be loud.
    """
    asked = _asked_titles(asked_path)
    if not asked:
        logger.error("categorize import: %s names no titles — cannot validate answers "
                     "against anything, refusing to import", asked_path)
        return 0

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("categories", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        logger.warning("categorize import: expected a list of answers, got %s",
                       type(entries).__name__)
        return 0

    rows: dict[str, tuple[str, str, str]] = {}
    unknown_index = invalid_category = malformed = 0
    for entry in entries:
        if not isinstance(entry, dict):
            malformed += 1
            continue
        idx, category = entry.get("i"), entry.get("category")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx not in asked:
            unknown_index += 1
            continue
        if not isinstance(category, str) or category not in taxonomy.CATEGORIES:
            invalid_category += 1
            continue
        title = asked[idx]
        key = normalise_title(title)
        if key:
            rows[key] = (key, category, title)

    written = store.upsert_title_categories(list(rows.values()))
    declined = sum(1 for _k, c, _t in rows.values() if c == taxonomy.UNCATEGORISED)
    unanswered = len(asked) - len(rows)
    if unknown_index or invalid_category or malformed:
        logger.warning("categorize import: dropped %d answers for titles never asked about, "
                       "%d naming a category that does not exist, %d malformed",
                       unknown_index, invalid_category, malformed)
    if unanswered > 0:
        logger.warning("categorize import: %d of %d exported titles got no answer — they "
                       "will be asked about again", unanswered, len(asked))
    logger.info("categorize import: stored %d titles (%d of them recorded declines) from %s",
                written, declined, path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write the titles the routine should read")
    ex.add_argument("path")
    ex.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    im = sub.add_parser("import", help="validate and store the routine's answers")
    im.add_argument("path")
    im.add_argument("--asked", required=True,
                    help="the export file the answers are indexed against")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "export":
        export_uncategorised(args.path, limit=args.limit)
    else:
        import_categories(args.path, args.asked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
