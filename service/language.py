"""The language a posting is *written in* — the sixth axis, and a narrower promise than it looks.

The subscriber side is a **multi-select of the languages they can read**
(`profiles.understood_languages`): an empty selection widens to everything, and — the rule that
makes the axis safe — **English always passes and a posting whose language we could not read
always passes**. The posting side is `postings.language`, an ISO-639-1 code detected from the
description, or ``None`` when the text was too short or too ambiguous to be sure.

**Read this before widening what the gate does — the scope is deliberately one-directional.**
"Language the posting is *written in*" is not "language the job *requires*", and only the first
is provable:

  * A description in **Russian** is one a Russian-illiterate subscriber cannot read — a real,
    safe reason to refuse it. This is the case the owner hit on 2026-08-20 (a Russian-language
    Data Analyst ad reaching his `/matches`).
  * A description in **English** says *nothing* about what the job requires — German and Czech
    employers routinely post in English while needing the local language. So **English is never
    a reason to refuse anything**, and inferring "this English ad secretly needs German" is the
    same unprovable trap as on-site detection. We do not attempt it.

So this module answers exactly one question — *can the subscriber read this ad at all?* — and
defers everything else to the AI matcher, the same division of labour as `education`/`experience`.

**Detection is a local, offline library (`py3langid`), by design — zero API cost and fully
backfillable.** It is chosen over the heavier `lingua` for the same reason the codebase runs
`fastembed` and not torch: the VPS has ~3.8 GB and no swap. Measured 2026-08-21: +28 MB RSS,
0.12 ms per call (~12 s over 100k rows), so it is free on the pipeline path and re-runnable over
stored text whenever the thresholds change (`python -m service.backfill_language`).

**The classifier is conservative in one direction only.** When the text is short or the verdict
is not confident it returns ``None`` and the posting is shown — a miss costs precision, a false
positive silently deletes a job from someone's digest for a language they may well read. Two
guards enforce it: a minimum text length (`MIN_CHARS`) and a probability floor (`MIN_CONFIDENCE`).
Closely-confusable pairs (cs/sk, es/pt, the Scandinavian set, ru/uk/bg) are the residual risk —
a confident misread there could refuse a language the subscriber actually reads — which is why
the backfill's printed distribution is the thing to check, and why the default seed and the
`/matches` self-drain both cap the blast radius.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------- languages ---
# The codes this axis both detects and lets a subscriber declare. ISO-639-1, one canonical
# order (used by the UI and by `describe`); the SQL gate is set membership, so it does not
# depend on the order. Focused on the European job-market corpus plus the neighbours that
# actually turn up on these boards (ru/uk are the whole reason the axis exists). `en` is in the
# set for completeness — it is what most rows classify as — but the gate special-cases it so
# that it passes for everyone regardless of whether a subscriber ticked it.
LANGUAGES: tuple[str, ...] = (
    "en", "cs", "sk", "de", "pl", "fr", "es", "it", "pt", "nl",
    "sv", "no", "da", "fi", "ru", "uk", "ro", "hu", "bg", "el",
    "hr", "sl", "sr", "et", "lv", "lt", "tr",
)

#: English names, for the UI chips / i18n / matcher prose. Kept here as the one definition; the
#: TypeScript mirror (`web/lib/language.ts`) and the eight catalogues are drift-tested against it.
LANGUAGE_LABELS: dict[str, str] = {
    "en": "English", "cs": "Czech", "sk": "Slovak", "de": "German", "pl": "Polish",
    "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "nl": "Dutch",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish", "ru": "Russian",
    "uk": "Ukrainian", "ro": "Romanian", "hu": "Hungarian", "bg": "Bulgarian", "el": "Greek",
    "hr": "Croatian", "sl": "Slovenian", "sr": "Serbian", "et": "Estonian", "lv": "Latvian",
    "lt": "Lithuanian", "tr": "Turkish",
}

#: `en` always satisfies the gate — see the module docstring. Named as a constant so the reason
#: is greppable and the gate SQL, the matcher prompt and the tests all reference one thing.
ALWAYS_READABLE = "en"

#: Below this many characters of stripped text, detection is not trustworthy — a 4-word CZ/SK
#: title ("Datový analytik Praha") classifies confidently as Czech but a bare title is not
#: enough to refuse a job on, and English-ish titles ("Data Engineer") land on noise. Short text
#: therefore yields ``None`` and passes. Requirements live in the description anyway, which is
#: where the Russian-ad case had 1 000+ characters.
MIN_CHARS = 200

#: py3langid's normalised probability for the winning language. Its distribution is sharply
#: peaked on clear prose (1.000 on a full description in the 2026-08-21 sample), so this floor
#: mostly rejects genuinely mixed or borderline text rather than trimming good verdicts. A
#: bilingual ad that the detector cannot call falls below it and passes.
MIN_CONFIDENCE = 0.90

# --------------------------------------------------------------- text preparation --
# Deliberately NOT `education.normalise`: that folds to `[a-z0-9]` and would erase every
# Cyrillic, Greek and accented letter — i.e. delete the exact signal the detector reads. This
# strips only machinery (tags, entities, URLs) and collapses whitespace, leaving the script and
# the words intact.
_MARKUP = re.compile(r"<[^>]*>|&[a-z]+;|&#\d+;|https?://\S+|\bwww\.\S+", re.I)
_WS = re.compile(r"\s+")

#: Descriptions are HTML soup and occasionally enormous; detection needs only a representative
#: slice, and the first characters are the ad's own prose before any boilerplate footer.
_MAX_SCAN = 20_000


def _prepare(description: Optional[str], title: Optional[str]) -> str:
    """Title + description, markup stripped, whitespace collapsed — Unicode otherwise intact."""
    raw = f"{title or ''} {(description or '')[:_MAX_SCAN]}"
    return _WS.sub(" ", _MARKUP.sub(" ", raw)).strip()


@lru_cache(maxsize=1)
def _identifier():
    """The py3langid identifier, restricted to `LANGUAGES` and normalising probabilities.

    Loaded lazily and once: importing this module (which `store` does, for the predicate) must
    stay cheap and pull no model, exactly like `embed._load()`. The predicate never touches the
    detector — only `detect()` does, on the ingest/backfill path.
    """
    from py3langid.langid import LanguageIdentifier, MODEL_FILE

    ident = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
    ident.set_languages(list(LANGUAGES))
    return ident


def detect(description: Optional[str], title: Optional[str] = None) -> Optional[str]:
    """The language this posting is written in, or ``None`` when it cannot be read confidently.

    ``None`` is a first-class value and the safe one: the SQL gate keeps it and the AI matcher
    judges the row instead. It is returned whenever the stripped text is under `MIN_CHARS` or the
    detector's confidence is under `MIN_CONFIDENCE` — the two ways "we are not sure" arrive.
    """
    text = _prepare(description, title)
    if len(text) < MIN_CHARS:
        return None
    lang, prob = _identifier().classify(text)
    if prob < MIN_CONFIDENCE:
        return None
    return lang if lang in LANGUAGES else None


# --------------------------------------------------------------- preference values --


def clean_languages(values: Any) -> list[str]:
    """Normalise a subscriber's declared languages to known codes, in `LANGUAGES` order.

    Unlike `education.clean_levels`, an empty or entirely unrecognised selection stays **empty**,
    not "all" — because empty here means *no preference*, which the gate reads as no filter
    (widen). The two modules widen on silence the same way; they only differ in how "silence" is
    spelled, because a language set has no natural "everything" member the way the level ladder
    does. Anything unrecognised is dropped rather than erroring, matching the tolerance the other
    axes show a hand-written client.
    """
    wanted = {str(v).strip().lower() for v in (values or [])}
    return [code for code in LANGUAGES if code in wanted]


def describe(languages: Iterable[str]) -> Optional[str]:
    """One human clause for the matcher prompt, or ``None`` when there is nothing to constrain."""
    picked = clean_languages(languages)
    if not picked:
        return None
    names = [LANGUAGE_LABELS[c] for c in picked]
    joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return (f"reads {joined} — a posting written entirely in another language is not a fit "
            f"(English always counts, and a posting whose language is unstated is fine)")


# ---------------------------------------------------------------- the SQL gate --


def language_predicate(profile: dict, alias: str = "p") -> tuple[str, list[Any]]:
    """SQL fragment restricting postings to languages this subscriber can read.

    Three passes, and every one of them is a *keep*:

      * A profile that declared **no** languages gets **no filter at all** — silence widens, and
        it is the state an existing subscriber sits in until the migration seeds them or they
        pick. Nobody's digest narrows without them choosing it.
      * **A null `postings.language` always passes.** It is null wherever the text was too short
        or too ambiguous to read — most of the CZ/SK title-only corpus, and every genuinely
        mixed ad — and treating unknown as "foreign" would be the education-gate catastrophe in
        a new place.
      * **English always passes**, ticked or not. English is the market's lingua franca and the
        bulk of inventory; refusing it would gut every shortlist, and an English ad is no
        evidence of a foreign requirement anyway (module docstring). Same spirit as eligibility's
        unconditional "verify UK right-to-work".

    So the gate refuses exactly one thing: a posting *confidently detected* in a *non-English*
    language the subscriber did *not* declare.
    """
    langs = clean_languages(profile.get("understood_languages"))
    if not langs:
        return "true", []
    return (f"({alias}.language is null or {alias}.language = %s "
            f"or {alias}.language = any(%s))", [ALWAYS_READABLE, langs])
