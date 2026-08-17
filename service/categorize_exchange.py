"""Model classification for the residue — metered API on the box, file exchange retained.

What neither the title patterns nor the publishers' occupation codes reach is a long
multilingual tail: **17 770 active postings across 15 139 distinct titles** (measured
2026-08-17 — 1.17 postings per title, a pure long tail), in Swedish, Czech, Norwegian and
the private vocabulary of individual employers. A model reads those titles trivially.

**The scheduled path is `classify` — one metered `ANTHROPIC_API_KEY` call per batch of
titles, on the box.** The three-phase Drive exchange below it was built on 2026-08-09 for
exactly one recorded reason — `notes/categorization/PLAN.md` item 4, *"No API key. Decided
2026-08-09"* — and that premise expired on 2026-08-17 when the matcher moved to a metered
key (`deploy/matcher-routine.md`). The exchange code is **retained, not deleted**: it is the
audited trust boundary, the fallback, and the thing the validation is shared with. It is
simply no longer scheduled — the same shape the matcher's retirement took.

    classify uncategorised titles -> Claude (metered) -> validate -> `title_categories`

    export   uncategorised titles           -> titles.json   -> Drive     (retained, unscheduled)
    routine  claude.ai reads titles.json    -> categories.json            (no DB, no key)
    import   validate every row against the taxonomy -> `title_categories`
    ingest   consults the cache where patterns and codes both decline

**Both paths run the same validation, in one function** (`_validated_rows`). That is the
point of the refactor rather than a tidiness preference: the guarantees below were written
against a claude.ai routine, and a returned value is untrusted input *whoever* produced it —
an API response no less than a file off cloud storage. Two copies of the check would drift,
and the drift would be silent.

**It suits titles better than it suits matching.** A title is ~10 tokens with no
description, the answer is a flat map, the work is idempotent, and the payload carries **no
personal data whatsoever**: public job titles and integer indices, nothing else.
`test_categorize_exchange.py` asserts that of the export file and
`test_categorize_metered.py` of the API request body.

**Titles go out with an index and answers come back by index**, exactly as candidates do in
the matcher. A returned index that was never asked about is dropped; a returned category
that is not in `taxonomy.CATEGORIES` is dropped and counted. This is the 2026-08-08 hint
guard arriving by a new route: 14 135 postings once sat in categories no query could name,
because a third-party string was trusted to be a category. A model's answer is a
third-party string too.

**Nothing here is on the digest's critical path, and that did not change with the key.** The
digest runs at 05:00 and is what real people are waiting on; an enrichment added to that
window does not make a digest late, it makes it *miss*. This runs on its own weekly timer, a
failed batch costs that batch and not the run, and a failed run costs a slower-shrinking
residue and nothing else.

    python -m service.categorize_exchange classify [--limit N] [--batch N] [--dry-run]

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
from collections import Counter
from datetime import datetime, timezone

from service import store, taxonomy

logger = logging.getLogger(__name__)

#: How many distinct titles one export may carry. The one-off residue was ~33k distinct
#: titles ≈ 340k tokens, which fits a single context; steady state after that is ~2 400 new
#: titles a day. The default is deliberately above the steady state and below the residue, so
#: the first few runs drain it in slices and every later run is complete — and a truncated run
#: says what it dropped rather than looking finished.
DEFAULT_LIMIT = int(os.environ.get("CATEGORIZE_EXPORT_LIMIT", "8000"))

# --- the metered path's knobs ---------------------------------------------------------
#
# Mirrors `service.matcher`'s so there is one shape to learn: a model env var with a
# documented default, an output ceiling, and a run-level abort budget.

#: The classifier model. Mirrors `MATCHER_MODEL`, same default and the same reasoning —
#: Haiku 4.5 is well-calibrated on this class of work at ~10x lower cost than Sonnet, and a
#: title is ~10 tokens with no description, which is the easiest thing in this repo to read.
#: Raising it is safe: `_answers_from_response` takes the first *text* block, so a model that
#: thinks before answering does not silently return zero answers (the 2026-08-17 matcher bug).
MODEL = os.environ.get("CATEGORIZE_MODEL", "claude-haiku-4-5")

#: Titles per API call. Sized from both ends of the workload rather than picked: the one-off
#: backlog is ~15k titles (75 calls at 200) and steady state ~2.4k/day (12 calls), so the
#: batch has to be big enough that neither is a per-title round trip and small enough that a
#: failed or truncated batch costs a fifth of a percent of the backlog rather than all of it.
#: Input is ~2.5k tokens per call, output ~4k — comfortably inside Haiku 4.5's 64k ceiling.
BATCH_SIZE = int(os.environ.get("CATEGORIZE_BATCH_SIZE", "200"))

#: How many distinct titles one metered run may classify. Unlike the export's limit this is
#: not a context bound — the run batches — so it is purely a cost ceiling, and it is set well
#: above steady state so an ordinary week is never truncated. The backlog drains over the
#: first few runs, commonest-titles-first, exactly as the export's did.
DEFAULT_CLASSIFY_LIMIT = int(os.environ.get("CATEGORIZE_LIMIT", "8000"))

#: Output ceiling per call. 0 means "derive it from the batch size", which is the default and
#: the right answer: one answer is `{"i": 123, "category": "manufacturing_production"},` —
#: ~18 tokens — so a fixed number would be either wasteful on a small batch or a truncation
#: bug on a large one. **A ceiling, not a reservation**: output is billed per token actually
#: generated, so headroom is free. Truncation is named explicitly in the log because a reply
#: cut off mid-object is invalid JSON and would otherwise surface as "bad JSON" — the exact
#: misdiagnosis `matcher` fixed on 2026-08-17.
MAX_OUTPUT_TOKENS = int(os.environ.get("CATEGORIZE_MAX_OUTPUT_TOKENS", "0"))

#: Run-level abort budget, mirroring `MATCHER_MAX_TOKENS`. 0 = no ceiling (the default).
#: When >0 the run stops starting new batches once accumulated tokens cross it, so a bug that
#: inflates the residue query can never run up unbounded spend before anyone notices. A
#: *safety cap*, not a cost target — the whole 15k backlog is well under a dollar.
MAX_RUN_TOKENS = int(os.environ.get("CATEGORIZE_MAX_TOKENS", "0"))

#: Haiku 4.5 list price ($/token), for the end-of-run cost line only — never a gate. Same
#: constants and same purpose as `matcher._PRICE_*`; if the model knob moves, the line becomes
#: an underestimate and says so by naming the model beside it.
_PRICE_IN, _PRICE_OUT, _PRICE_CACHE_READ = 1.0e-6, 5.0e-6, 0.10e-6

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


def _pending_titles(limit: int) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    """`(kept, dropped)` distinct titles nothing has answered for, commonest first.

    One definition, shared by the export and the metered classify path — the residue is the
    same question however the answer is obtained, and a second copy of this grouping would be
    a second `normalise_title` call site that could fall out of step about what "already
    asked" means. Grouping happens here in Python rather than in SQL for the reason at the top
    of `store`'s `title_categories` section: the key has one definition and it is not this
    module's to re-express.

    `dropped` is returned rather than discarded so the caller can *say* what it left behind. A
    silent cap reads as "we covered everything", which is the failure `CLAUDE.md` names for
    every other bounded step in this repo.
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
    return ranked[:limit], ranked[limit:]


def export_uncategorised(path: str, limit: int = DEFAULT_LIMIT) -> int:
    """Write the titles the routine should read. Returns how many it asked about.

    **Retained but no longer scheduled** (2026-08-17) — `classify_uncategorised` is the live
    path. This stays as the fallback and as the audited file-exchange boundary; see the module
    docstring and `deploy/categorize-routine.md`.

    Ordered by how many active postings share the title, so a truncated run answers for the
    titles that stand for the most inventory first — and logs what it left behind.
    """
    kept, dropped = _pending_titles(limit)

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


def _validated_rows(entries: object,
                    asked: dict[int, str]) -> tuple[dict[str, tuple[str, str, str]], Counter]:
    """`({title_key: (key, category, title)}, counts)` — the trust boundary, one definition.

    **Called by both paths, deliberately.** `import_categories` reads a file written by a
    claude.ai routine and travelling via cloud storage; `classify_uncategorised` reads an API
    response. Those are different transports and the *same* class of input: a returned value
    is untrusted whoever produced it, which is the 2026-08-08 hint-guard lesson arriving by a
    third route (five adapters trusted a third-party string to be a category and 14 135 active
    postings ended up somewhere no subscriber could select and no query could match). Two
    copies of these four rules would drift, and the drift would be silent — a wrong category
    raises nothing, it just files a job in a stranger's digest.

    Nothing here is taken on faith:

    - an **index nobody asked about is dropped** — neither a file nor a model can invent a
      title to classify, and the index is checked against what was actually sent. A string
      index is not an index and a bool is not an int (`True == 1` in Python, so an unguarded
      `isinstance(idx, int)` would let `{"i": true}` answer for title 1),
    - a **category not in `taxonomy.CATEGORIES` is dropped and counted**,
    - a **malformed record is skipped**, not fatal: one bad row must not cost the batch,
    - **`uncategorised` is stored** as a recorded decline, or the same unreadable titles are
      re-asked every run for ever.

    Returns rows keyed by `title_key`, so a model answering twice for one title yields one
    row rather than two conflicting writes. The caller does the storing and the logging —
    this function touches no database and no I/O, which is what makes it cheap to test.
    """
    rows: dict[str, tuple[str, str, str]] = {}
    counts: Counter = Counter()
    if not isinstance(entries, list):
        counts["not_a_list"] += 1
        return rows, counts
    for entry in entries:
        if not isinstance(entry, dict):
            counts["malformed"] += 1
            continue
        idx, category = entry.get("i"), entry.get("category")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx not in asked:
            counts["unknown_index"] += 1
            continue
        if not isinstance(category, str) or category not in taxonomy.CATEGORIES:
            counts["invalid_category"] += 1
            continue
        title = asked[idx]
        key = normalise_title(title)
        if key:
            rows[key] = (key, category, title)
    return rows, counts


def _log_drops(what: str, counts: Counter) -> None:
    """One warning line for whatever the validator refused. Shared so both paths say it."""
    if counts["unknown_index"] or counts["invalid_category"] or counts["malformed"]:
        logger.warning("%s: dropped %d answers for titles never asked about, %d naming a "
                       "category that does not exist, %d malformed", what,
                       counts["unknown_index"], counts["invalid_category"],
                       counts["malformed"])


def import_categories(path: str, asked_path: str) -> int:
    """Read the routine's answers, validate every one, store them. Returns rows written.

    **Retained but no longer scheduled** (2026-08-17) — see the module docstring. It is still
    the audited fallback, and it runs exactly the validation the metered path runs, because
    both call `_validated_rows`.

    Accepts `{"categories": [{"i": 0, "category": "sales"}, ...]}` or a bare list.

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

    rows, counts = _validated_rows(entries, asked)
    if counts["not_a_list"]:
        logger.warning("categorize import: expected a list of answers, got %s",
                       type(entries).__name__)
        return 0

    written = store.upsert_title_categories(list(rows.values()))
    declined = sum(1 for _k, c, _t in rows.values() if c == taxonomy.UNCATEGORISED)
    unanswered = len(asked) - len(rows)
    _log_drops("categorize import", counts)
    if unanswered > 0:
        logger.warning("categorize import: %d of %d exported titles got no answer — they "
                       "will be asked about again", unanswered, len(asked))
    logger.info("categorize import: stored %d titles (%d of them recorded declines) from %s",
                written, declined, path)
    return written


# --- the metered path (live) -----------------------------------------------------------
# One Claude call per batch of titles, on the box, with `ANTHROPIC_API_KEY`. Everything
# above the `_validated_rows` boundary is transport; everything below it is shared.


def _run_cost_usd(u: dict) -> float:
    return u["input"] * _PRICE_IN + u["output"] * _PRICE_OUT + u["cache_read"] * _PRICE_CACHE_READ


def _accumulate_usage(acc: dict | None, resp) -> None:
    """Fold one response's token usage into the run accumulator (in place, never raises).

    Same shape and the same caveat as `matcher._accumulate_usage`: `cache_read` is 0 on Haiku
    4.5 because its minimum cacheable prefix is 4 096 tokens and the system block here is a
    few hundred, so the `cache_control` below writes no entry and reports none — silently,
    with no error to notice. It is kept anyway because the minimum is not monotonic across
    models (512 on Opus 5, 1 024 on Sonnet 5), so the same block starts caching the moment
    `CATEGORIZE_MODEL` moves up, and the log keeps `input` and `cache_read` separate so that
    transition is visible rather than inferred.
    """
    if acc is None:
        return
    u = getattr(resp, "usage", None)
    if u is None:
        return
    acc["input"] += getattr(u, "input_tokens", 0) or 0
    acc["output"] += getattr(u, "output_tokens", 0) or 0
    acc["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    acc["calls"] += 1


def _system_prompt() -> str:
    """The classifier instructions plus the vocabulary, as the model reads them.

    `ROUTINE_INSTRUCTIONS` is reused verbatim rather than paraphrased: the routine's prompt
    and this one must be one text, or the two paths quietly answer differently and the only
    symptom is a category distribution that shifts when the schedule changes. The category
    list is appended from `taxonomy.CATEGORIES` for the same reason the export embeds it — a
    vocabulary that drifts from the taxonomy is how a model returns something reasonable that
    this system then has to discard.
    """
    return (ROUTINE_INSTRUCTIONS + "\n\nThe allowed categories are exactly these; copy one "
            "verbatim and never invent, reword or re-case one:\n"
            + ", ".join(taxonomy.CATEGORIES))


def _batch_payload(batch: list[tuple[str, dict]]) -> str:
    """The user message: integer indices and titles, nothing else.

    Indices are **local to the batch** (0..n-1) and are what the answer is checked against, so
    a model cannot answer for a title it was not shown. No posting id, no company, no URL, no
    subscriber, no email — these are public job titles, and keeping the payload to titles and
    integers is the discipline that makes this the cheapest boundary in the repo to defend.
    `test_categorize_metered.py` asserts it against the serialised request body.
    """
    return json.dumps(
        {"titles": [{"i": i, "title": entry["title"]} for i, (_key, entry) in enumerate(batch)]},
        ensure_ascii=False)


def _max_output_tokens(n: int) -> int:
    """Output ceiling for a batch of `n` titles, unless the env var pins one.

    One answer is ~18 tokens; 48 per title plus 512 of slack is roughly 2.5x headroom, and
    headroom is free (output is billed per token generated). Deriving it is what stops a batch
    size change from becoming a silent truncation bug.
    """
    return MAX_OUTPUT_TOKENS or (512 + 48 * n)


def _answers_from_response(resp, model: str, max_tokens: int) -> list | None:
    """The answer list out of a Claude response, or `None` if there isn't one.

    Every defence here is one `service.matcher` paid for in production on 2026-08-17, and each
    failure had the same shape: a plausible-looking empty result that nothing downstream could
    tell from "there was genuinely nothing to do".

    - **The first *text* block, not `content[0]`.** Every model above Haiku 4.5 thinks by
      default and opens its response with a `thinking` block whose text is empty, so indexing
      position 0 parsed `""` and returned zero answers for the whole batch. This is what makes
      `CATEGORIZE_MODEL` mean what it says.
    - **`stop_reason == "max_tokens"` is named as truncation**, before parsing. A reply cut off
      mid-object is invalid JSON, so it otherwise surfaces as "bad JSON from model" and sends
      the next reader to the parser or the prompt rather than to the budget that caused it.
    - **`raw_decode`, not `loads`.** A complete, valid object followed by "I hope this helps!"
      makes `json.loads` reject the *whole* reply on trailing data; `raw_decode` reads the
      first complete value and the answers survive whatever follows.
    - Markdown fences and prose *before* the object are stripped.
    """
    if getattr(resp, "stop_reason", None) == "max_tokens":
        logger.error("categorize classify: model %s hit max_tokens=%d — the reply is truncated "
                     "and will not parse, so this whole batch goes unanswered. Raise "
                     "CATEGORIZE_MAX_OUTPUT_TOKENS (a ceiling, not a reservation — unused "
                     "headroom is free) or lower CATEGORIZE_BATCH_SIZE.", model, max_tokens)
    raw = next((b.text for b in resp.content
                if getattr(b, "type", None) == "text" and getattr(b, "text", None)), "").strip()
    if not raw:
        logger.warning("categorize classify: model %s returned no text block (%s)", model,
                       [getattr(b, "type", "?") for b in resp.content])
        return None
    if raw.startswith("```"):
        raw = "\n".join(l for l in raw.split("\n") if not l.strip().startswith("```")).strip()
    if not raw.startswith("{") and not raw.startswith("["):
        brace = min((i for i in (raw.find("{"), raw.find("[")) if i > 0), default=-1)
        if brace > 0:
            raw = raw[brace:]
    try:
        obj, end = json.JSONDecoder().raw_decode(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("categorize classify: bad JSON from model %s (%s): %s", model, exc,
                       raw[:200])
        return None
    trailing = raw[end:].strip()
    if trailing:
        logger.warning("categorize classify: model %s emitted %d chars after the JSON object; "
                       "the object parsed fine and the extra was ignored: %s", model,
                       len(trailing), trailing[:120])
    if isinstance(obj, dict):
        return obj.get("categories", obj.get("answers"))
    return obj


def classify_batch(client, batch: list[tuple[str, dict]], model: str | None = None,
                   usage_acc: dict | None = None) -> dict[str, tuple[str, str, str]]:
    """Ask the model about one batch and return the rows that survived validation.

    Returns `{}` rather than raising when the reply is unusable — the caller then imports what
    other batches validated and carries on. `model` overrides `MODEL` for one call, which is
    what a comparison harness needs; nothing on the scheduled path passes it.
    """
    max_tokens = _max_output_tokens(len(batch))
    resp = client.messages.create(
        model=model or MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": _system_prompt(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _batch_payload(batch)}],
    )
    _accumulate_usage(usage_acc, resp)
    entries = _answers_from_response(resp, model or MODEL, max_tokens)
    if entries is None:
        return {}
    asked = {i: entry["title"] for i, (_key, entry) in enumerate(batch)}
    rows, counts = _validated_rows(entries, asked)
    if counts["not_a_list"]:
        logger.warning("categorize classify: model %s returned %s, not a list of answers",
                       model or MODEL, type(entries).__name__)
        return {}
    _log_drops("categorize classify", counts)
    unanswered = len(asked) - len(rows)
    if unanswered > 0:
        logger.warning("categorize classify: %d of %d titles in this batch got no usable "
                       "answer — they will be asked about again", unanswered, len(asked))
    return rows


def classify_uncategorised(limit: int = DEFAULT_CLASSIFY_LIMIT,
                           batch_size: int = BATCH_SIZE,
                           dry_run: bool = False) -> int:
    """Classify the residue with the metered API. Returns titles stored.

    The scheduled path since 2026-08-17. Commonest titles first, so a run cut short by the
    limit or the abort budget has answered for the titles standing for the most inventory.

    **A batch is the unit of failure, not the run.** Each batch is stored as soon as it
    validates and an exception inside one is logged and skipped, so a transient API error, a
    truncated reply or one malformed answer costs that slice of the residue and nothing else.
    That is the same rule `import_picks` follows and for the same reason: a partial result is
    strictly better than none, and this job has no deadline anybody is waiting on.

    Raises only if `ANTHROPIC_API_KEY` is unset — a misconfigured box should be loud, and the
    systemd unit's `OnFailure` alerter is what makes it so.
    """
    kept, dropped = _pending_titles(limit)
    if dropped:
        logger.warning("categorize classify: %d distinct titles (%d postings) did NOT fit the "
                       "limit of %d and were not asked about — they stay uncategorised until "
                       "a later run", len(dropped), sum(e["n"] for _k, e in dropped), limit)
    if not kept:
        logger.info("categorize classify: no unanswered titles in the residue — nothing to do")
        return 0

    batches = [kept[i:i + batch_size] for i in range(0, len(kept), batch_size)]
    logger.info("categorize classify: %d titles (%d postings) in %d batch(es) of <=%d, "
                "model %s%s", len(kept), sum(e["n"] for _k, e in kept), len(batches),
                batch_size, MODEL, " [DRY RUN]" if dry_run else "")
    if dry_run:
        print(f"--- SYSTEM ---\n{_system_prompt()}\n--- USER (first batch, 1500 chars) ---")
        print(_batch_payload(batches[0])[:1500])
        return 0

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    usage = {"input": 0, "output": 0, "cache_read": 0, "calls": 0}

    written = declined = done = 0
    for n, batch in enumerate(batches, 1):
        # Abort budget, checked before each call: a bug that inflated the residue stops here
        # rather than after billing the whole corpus. 0 disables it (the default).
        if MAX_RUN_TOKENS and (usage["input"] + usage["output"]) >= MAX_RUN_TOKENS:
            logger.error("categorize classify ABORT BUDGET hit (%d tokens >= "
                         "CATEGORIZE_MAX_TOKENS=%d) after %d/%d batches — stopping to avoid "
                         "unbounded spend", usage["input"] + usage["output"], MAX_RUN_TOKENS,
                         n - 1, len(batches))
            break
        try:
            rows = classify_batch(client, batch, usage_acc=usage)
        except Exception:                          # noqa: BLE001 — see the docstring
            logger.exception("categorize classify: batch %d/%d failed, skipping its %d titles",
                             n, len(batches), len(batch))
            continue
        done += 1
        if not rows:
            continue
        try:
            # `source="api"` is a new *value* in an existing column, not a new column: the
            # provenance of an answer is worth being able to query when a category looks
            # wrong, and 'routine' rows predate the key.
            written += store.upsert_title_categories(list(rows.values()), source="api")
        except Exception:                          # noqa: BLE001 — one batch, not the run
            logger.exception("categorize classify: batch %d/%d validated but could not be "
                             "stored, skipping", n, len(batches))
            continue
        declined += sum(1 for _k, c, _t in rows.values() if c == taxonomy.UNCATEGORISED)

    logger.info("categorize classify: stored %d titles (%d of them recorded declines) over "
                "%d/%d batches; model %s tokens in=%d cache_read=%d out=%d in %d call(s) "
                "~ $%.4f", written, declined, done, len(batches), MODEL, usage["input"],
                usage["cache_read"], usage["output"], usage["calls"], _run_cost_usd(usage))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cl = sub.add_parser("classify",
                        help="LIVE: classify the residue with the metered API (needs "
                             "ANTHROPIC_API_KEY)")
    cl.add_argument("--limit", type=int, default=DEFAULT_CLASSIFY_LIMIT)
    cl.add_argument("--batch", type=int, default=BATCH_SIZE)
    cl.add_argument("--dry-run", action="store_true",
                    help="assemble the first batch's prompt and print it, no API call")
    ex = sub.add_parser("export", help="RETAINED, unscheduled: write titles.json for the "
                                       "claude.ai routine")
    ex.add_argument("path")
    ex.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    im = sub.add_parser("import", help="RETAINED, unscheduled: validate and store the "
                                       "routine's answers")
    im.add_argument("path")
    im.add_argument("--asked", required=True,
                    help="the export file the answers are indexed against")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "classify":
        classify_uncategorised(limit=args.limit, batch_size=args.batch, dry_run=args.dry_run)
    elif args.cmd == "export":
        export_uncategorised(args.path, limit=args.limit)
    else:
        import_categories(args.path, args.asked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
