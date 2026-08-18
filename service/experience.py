"""Years-of-experience requirements — the fifth axis, same shape as the fourth.

The posting side is ``postings.experience_min`` — the *lowest* number of years an applicant
must already have, or ``None`` when the ad never said. The subscriber side is the existing
``profiles.years_experience`` (typed on the form or detected from a CV): a posting demanding
more years than the subscriber has is not a fit, exactly as a posting demanding a master's is
not a fit for a bachelor — and everything `service/education.py`'s docstring says about that
gate applies here unchanged. In particular:

  * ``experience_min`` is ``None`` for the overwhelming majority of rows and the SQL gate
    keeps every one of them. The Czech/Slovak inventory has almost no description text, so
    it can never produce anything but null.
  * A profile with no ``years_experience`` at all gets **no filter** — silence widens.
  * The classifier is conservative in one direction only: when in doubt it returns ``None``
    and the posting is shown. A false negative costs precision; a false positive deletes a
    job from someone's digest for a requirement the ad never stated.

**What is different from education, and why.** The education patterns look for a noun
("bachelor", "Ausbildung"); here the shape is *number + years-word*, and that shape is
everywhere in ad copy that has nothing to do with the applicant: "25+ years on the market"
(employer blurb — with the literal word "experience" in it), "2-year contract" (duration),
"18 years or older" (age), "3 years of warranty". So a mention only counts when an
**experience word stands next to the years phrase** (`_NEAR`, a tight window), and the blurb
guard looks for first-person markers ("we", "our", "company") rather than degree-programme
words. Both lists were built from a random sample of live production descriptions the same
way education's were — see `service/tests/test_experience.py`, where every false-positive
case is real text that would have classified wrongly without its guard.

**A range demands its floor.** "3-5 years" is satisfied by 3; "up to 5 years" states no
minimum at all and classifies as ``None``. Same argument as the generic degree reading
`bachelor`: taking the higher number would exclude subscribers on a demand the ad never made.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from service.education import normalise

# The highest minimum worth believing. Demands above this are almost always a parse of
# something else — an employer's age ("we have 25 years of experience") wearing the exact
# grammar of a requirement. Postings genuinely demanding more than 15 years of experience
# are effectively absent from this corpus, while 20+, 25+ and 30+ blurbs are everywhere, so
# the cap is itself a blurb guard, not just input hygiene.
MAX_YEARS = 15

#: The words that make a number mean tenure. One alternation, written against `normalise`d
#: text (diacritics folded, punctuation collapsed): "années d'expérience" arrives as
#: "annees d experience". Folding is also why Swedish/Norwegian "år" is spelled "ar" here —
#: and why it is anchored to the experience word, because bare "ar" is a syllable of half of
#: Swedish. `experien` covers en/fr/es (experience/expérience/experiencia), `esperienza` it,
#: `erfahrung` de, `ervaring` nl, `doswiadczen` pl, `praxe`/`zkusenost` cs, `prax` sk,
#: `experiencia`/`experiência` pt, `erfarenhet` sv, `erfaring` no/da.
_EXP_WORD = (r"(?:experien\w*|esperienza\w*|erfahrung\w*|ervaring\w*|doswiadczen\w*|"
             r"praxe|praxi|prax[ei]?\w*|zkusenost\w*|skusenost\w*|experiencia\w*|"
             r"erfarenhet\w*|erfaring\w*|berufserfahrung\w*|track\s+record)")

#: A number followed by a years word: "3+ years", "3 or more years", "3-5 years", "five
#: years". Written-out numbers stop at ten — beyond that ads use digits. The years word
#: carries every catalogue language: years/yrs (en), jahre/jahren (de), ans/annees (fr),
#: anos (es/pt, folded), anni (it), lat/lata (pl), let/roky/rok/roku (cs/sk), jaar/jaren
#: (nl), ar/aren (sv/no/da, folded år).
_YEARS_WORD = (r"(?:years?|yrs?|jahren?|jahres?|ans|annees?|anos?|anni|lat|lata|let|"
               r"roky|roku|rok|jaar|jaren|aar|ar)")

#: `normalise` folds every non-alphanumeric to a space, so "3+ years" arrives as "3 years"
#: and "5-7 years" as "5 7 years" — which is why the range separator here is *whitespace or
#: a to-word*, and why there is no "+" arm: it cannot survive normalisation to be matched.
_NUMBER = (r"(?:(?P<lo>\d{1,2})(?:\s+(?:to|az|bis|a|or|od|von))?(?:\s+(?P<hi>\d{1,2}))?"
           r"|(?P<word>one|two|three|four|five|six|seven|eight|nine|ten))")

_WORD_VALUES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

#: number + years-word, e.g. "3 5 let", "5 years", "three years". `min`/`mindestens`/
#: `alespon`/`minimalne` before the number are allowed but not required — the experience-word
#: proximity check is what decides, not the qualifier.
_YEARS_PHRASE = re.compile(
    rf"\b(?:min(?:imum|imalne|imalni)?|mindestens|alespon|nejmene|at\s+least)?\s*"
    rf"{_NUMBER}\s*{_YEARS_WORD}\b")

#: How close (in characters of normalised text) the experience word has to stand to the
#: years phrase. Tight compared to education's 140 — a wide window would bridge two bullet
#: points and let "experience" from a neighbouring line validate a contract duration — but
#: wide enough for real modifier chains: "6 years of professional software engineering
#: experience" puts the word 44 characters past the phrase, and 45 truncated it mid-word.
_NEAR = 60

#: The other tenure shapes: a preposition ("8 years as an HRBP", "2 years in technical
#: support", "5 Jahre als Teamleiter") or a gerund ("4 years selling to banks", "10 years
#: shipping production software") directly after the years word — requirements that never
#: say "experience". Measured on the validation sample: without these arms roughly a third
#: of genuine requirements were declined. "In a row", market phrases and strategy horizons
#: are the guards' problem, not this pattern's.
_TENURE_SHAPE = re.compile(
    rf"{_YEARS_WORD}\s+(?:(?:of\s+)?(?:as|in|with|als|jako|v|ve|dans|en|nella?|w|\w{{3,}}ing)"
    rf"|of\s+(?:relevant|professional|previous|prior|proven|progressive|related|hands|solid|"
    rf"demonstrated|demonstrable|practical|work|working|industry|combined|equivalent|"
    rf"full\s+time))\b")

#: An employer talking about itself, split by where the give-away stands. Immediately
#: *before* the number: a possessive or first person ("our 25 years", "we have 25 years",
#: "blickt auf 30 Jahre"). *After* the phrase: a market/history complement ("years on the
#: market", "years in business", "25 let na trhu"). The windows are deliberately asymmetric
#: and tight — the first draft searched `\bour\b`, `\bbusiness\b`, `\bteam\b` and
#: `\boperat\w+\b` in a 90-character window and wrongly declined "12 years of experience in
#: healthcare operations", "8 years in project management business development" and "2 years
#: as the primary technical leader for a team", all real postings from the validation
#: sample. `in business` needs the lookahead or it swallows "4 years in business
#: intelligence", which is a job, not a boast.
_BLURB_BEFORE = re.compile(
    r"(?:\bwe\s+have\b|\bwe\s+ve\b|\bour\b|\bits\b|\bwith\s+over\b|\bboast\w*\b|\bblickt\b|"
    r"\bwir\s+haben\b|\bunser\w*\b|\bjsme\b|\bnase\b|\bmame\b|\bnotre\b|\bnos\b|"
    r"\bnuestr\w+\b|\bnostr\w+\b|\bnasza\b|\bonze\b|\bvart\b|\bvara\b|\bvi\s+har\b)"
    r"\s*(?:\w+\s+)?$", re.I)
_BLURB_AFTER = re.compile(
    r"\bon\s+the\s+market\b|\bin\s+(?:the\s+)?business(?!\s+(?:intelligence|development|"
    r"administration|analytics?|analysis|operations|consulting))\b|\bof\s+history\b|"
    r"\bmarket\s+leader\b|\bindustry\s+leader\b|\bago\b|\bin\s+a\s+row\b|\bfounded\b|"
    r"\bestablished\b|\bam\s+markt\b|\bna\s+trhu\b|\bpusobime\b|\bsur\s+le\s+marche\b|"
    r"\ben\s+el\s+mercado\b|\bsul\s+mercato\b|\bna\s+rynku\b|\bop\s+de\s+markt\b|"
    r"\bpa\s+marknaden\b|\bi\s+branschen\b", re.I)
_BLURB_LEAD, _BLURB_TAIL = 18, 50

#: A ceiling is not a floor: "up to 5 years" and "in the next 3 years" state no minimum.
#: Checked immediately before the number, where those phrases live.
_CEILING_BEFORE = re.compile(
    r"(?:\bup\s+to\b|\bbis\s+zu\b|\bhasta\b|\bfino\s+a\b|\baz\s+do\b|\bnext\b|"
    r"\bnachsten\b|\bpristich\b|\bwithin\b|\bfirst\b|\blast\b|\bover\s+the\b)\s*$", re.I)

#: A forward-looking horizon, attributive: "the 3-5 year technical strategy", "a 2 year
#: roadmap". Adjacency only — "8 years of experience in GTM strategy" and "operations
#: strategy" are fields of work, and a windowed `\bstrategy\b` wrongly declined both.
_HORIZON_AFTER = re.compile(
    rf"^\s*(?:\w+\s+){{0,2}}(?:strategy|roadmap|vision|plan|horizon)\b", re.I)

#: Phrases that turn a stated tenure into a preference — shared vocabulary with education's
#: softener where the languages overlap, kept separately because the education list also
#: carries degree-specific arms ("or diploma") that would be noise here.
_SOFTENER = re.compile(
    r"\bpreferred\b|\bpreferable\b|\bplus\b|\badvantage\b|\bnice\s+to\s+have\b|\bideally\b|"
    r"\bwelcome\b|\bbonus\b|\bnot\s+(?:strictly\s+)?(?:required|necessary|essential|a\s+must)\b|"
    r"\bor\s+(?:the\s+|an?\s+)?equivalent\b|"
    r"\bvyhodou\b|\bvon\s+vorteil\b|\bwunschenswert\b|\bidealerweise\b|"
    r"\bneni\s+podminkou\b|\bnevyzadujeme\b|\bnemusis\b|\bnemusite\b|"
    r"\bun\s+plus\b|\bsouhaite\w*\b|\bidealement\b|\bvalorad\w+\b|\bdeseable\b|"
    r"\bgradita\b|\bmile\s+widziane\b|\bwenselijk\b|\bmeriterande\b",
    re.I)

#: A requirements header standing right before the phrase overrides the softener check.
#: Greenhouse's boilerplate reads "…is a bonus, not a requirement. Minimum requirements:
#: 5 years of…" — both "bonus" and "not a requirement" land inside any reasonable softener
#: window while belonging to the *previous* bullet, and the header is the proof of that.
_REQ_HEADER = re.compile(
    r"\b(?:minimum\s+requirements?|requirements?|must\s+haves?|qualifications|required|"
    r"pozadujeme|anforderungen|requisitos|requisiti|wymagania|vereisten|krav)\s*$", re.I)

#: Things a number of years measures that are not the applicant's tenure, checked in the
#: tight window: guarantees, contract durations, age limits.
_NOT_TENURE = re.compile(
    r"\bwarrant\w*\b|\bguarantee\w*\b|\bcontract\b|\bfixed\s+term\b|"
    r"\byears?\s+old\b|\bage\s+of\b|\bor\s+older\b|\bfunding\b|"
    r"\bzaruk\w*\b|\bsmlouv\w*\b|\bbefristet\w*\b|\bvertrag\w*\b",
    re.I)

#: Same bound as education, for the same pathological-input reason.
_MAX_SCAN = 20_000


def classify_requirement(description: Optional[str], title: Optional[str] = None) -> Optional[int]:
    """The minimum years of experience this posting requires, or ``None`` when it does not say.

    Three rules, mirroring `education.classify_requirement` arm for arm:

      * **A years phrase counts only with an experience word beside it** (`_NEAR`). The
        number-plus-"years" shape appears constantly in ad copy about contracts, warranties,
        ages and company history; the tenure word is what disambiguates, so its absence is a
        decline, not a guess.
      * **A softened, first-person or non-tenure mention does not count.** "5+ years
        preferred" demands nothing; "our 25 years of experience" is the employer's history.
      * **The lowest surviving number wins.** "3-5 years" is satisfied by 3, and an ad naming
        several tenures ("2 years with Python, 5 with Kubernetes") is entered at the lowest —
        the same floor-not-ceiling reading as the generic degree.
    """
    text = normalise(f"{title or ''} {(description or '')[:_MAX_SCAN]}")
    if not text:
        return None

    best: Optional[int] = None
    for m in _YEARS_PHRASE.finditer(text):
        years = _value(m)
        if years is None or not (1 <= years <= MAX_YEARS):
            continue
        if not _binding(text, m.start(), m.end()):
            continue
        if best is None or years < best:
            best = years
    return best


def _value(m: "re.Match[str]") -> Optional[int]:
    """The *minimum* the matched phrase demands: the low end of a range, the word's value."""
    if m.group("word"):
        return _WORD_VALUES.get(m.group("word"))
    try:
        return int(m.group("lo"))
    except (TypeError, ValueError):
        return None


def _binding(text: str, start: int, end: int) -> bool:
    """Is the years phrase at ``[start:end)`` a tenure requirement on the applicant?

    Positive first: the phrase has to *mean* tenure — an experience word within `_NEAR`, or
    a tenure shape ("N years as/in/doing …"). Then the guards, each in the window where its
    give-away actually stands: ceilings and possessives immediately before the number,
    horizons and market boasts right after the phrase, softeners a clause either side — and
    a requirements header directly before the phrase beats the softeners, because it proves
    they belong to the previous bullet (see `_REQ_HEADER`).
    """
    near = text[max(0, start - _NEAR):end + _NEAR]
    if not (re.search(_EXP_WORD, near) or _TENURE_SHAPE.search(text[start:end + 16])):
        return False
    lead = text[max(0, start - _BLURB_LEAD):start]
    if _BLURB_BEFORE.search(lead) or _CEILING_BEFORE.search(lead):
        return False
    if _BLURB_AFTER.search(text[end:end + _BLURB_TAIL]) or _HORIZON_AFTER.match(text[end:end + 40]):
        return False
    if _NOT_TENURE.search(text[max(0, start - _BLURB_LEAD):end + _BLURB_TAIL]):
        return False
    if _REQ_HEADER.search(text[max(0, start - 40):start]):
        return True
    return not _SOFTENER.search(text[max(0, start - 70):end + 40])


# --------------------------------------------------------- preference values ---


def clean_years(value: Any) -> Optional[int]:
    """Normalise a subscriber's stated years of experience, or ``None``.

    ``None`` is "did not say" and widens — the same silence rule as everywhere else. Values
    are clamped to a sane range rather than rejected: a hand-written client sending 99 means
    "a lot", and 50 filters exactly like 99 does against a corpus capped at `MAX_YEARS`.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        years = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(50, years))


def describe(years: Optional[int]) -> Optional[str]:
    """One human clause for the matcher prompt, or ``None`` when there is nothing to say."""
    cleaned = clean_years(years)
    if cleaned is None:
        return None
    return (f"{cleaned} year{'s' if cleaned != 1 else ''} of professional experience — a "
            f"posting that demands more years than this is not a fit")


# ---------------------------------------------------------------- the SQL gate --


def experience_predicate(profile: dict, alias: str = "p") -> tuple[str, list[Any]]:
    """SQL fragment restricting postings to what this subscriber's tenure satisfies.

    The same two rules as `education.education_predicate`, for the same reasons:

      * A profile that never stated its years gets **no filter at all** — every existing
        subscriber is in that state unless a CV or the form said otherwise, so nobody's
        digest narrows without them having provided the number.
      * **A null `experience_min` always passes.** It is null wherever the ad never stated a
        tenure — the overwhelming majority — and treating unknown as "demands more than you
        have" would empty the digest of every junior. The preference reaches the AI matcher
        for those rows instead.
    """
    years = clean_years(profile.get("years_experience"))
    if years is None:
        return "true", []
    return (f"({alias}.experience_min is null or {alias}.experience_min <= %s)", [years])
