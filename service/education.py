"""Education requirements — one definition, several consumers.

The subscriber side is a **multi-select of posting requirement levels they will accept**
(`profiles.education_levels`), shaped exactly like `geo.work_modes`: an empty selection widens
to everything, a full selection applies no filter at all, and **a posting whose requirement we
could not read always passes**. The posting side is `postings.education_min` — the *lowest*
formal qualification an applicant must hold, or ``None`` when the ad never said.

**Read this before widening what the gate does.** Measured against production on 2026-08-02,
across 20 763 active postings:

    postings stating any degree requirement        952   (4.6%)
    ... of which soften it ("or equivalent", ...)  286
    binding, readable requirement                 ~666   (3.2%)

and the reason is not that employers are silent — it is that **70% of the corpus has no
description to read at all**. Median description length is 35 characters. `jobscz` (9 639
active postings, the largest source) averages 32 characters and stores scraps like
"70 000 – 80 000 Kč"; `profesia` (4 365) stores the empty string; `cocuma` (319) averages 9.
So the entire Czech/Slovak market — 14 323 postings — carries no text this module can read,
and every binding requirement it will ever find sits in the international sources (ashby,
arbeitnow, jobicy, himalayas, ...).

That shapes the whole design, and it is the same lesson as the On-site chip in CLAUDE.md: a
filter built on a column that is null almost everywhere must **defer**, not guess. So:

  * ``education_min`` is ``None`` for ~97% of rows and the SQL gate keeps every one of them.
  * The preference still reaches the AI matcher (`describe_levels`), which reads the
    description — the same division of labour as an unresolved city or a null `work_mode`.
  * The classifier is deliberately **conservative in one direction only**: when in doubt it
    returns ``None`` and the posting is shown. A false negative costs precision; a false
    positive deletes a job from someone's digest for a qualification the ad never demanded.

Field of study (`profiles.education_field`) is deliberately **not** here as a filter. Whether
a degree is "in a related field" is a judgement, not a predicate — it travels to the matcher
as free text and never reaches SQL.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------- levels ---
# Ordered lowest-first. The order is the order the UI offers them in and the order
# `describe_levels` reads them out; the SQL gate is set membership, so it does not depend on
# it. `vocational` sits above `secondary` because an apprenticeship is a completed
# qualification on top of school — but nothing keys on that, and no comparison is ever made
# between two levels. **This is a set, not a scale**, which is what lets a subscriber tick
# exactly the requirement levels they can satisfy without the code having to rank them.
LEVELS: tuple[str, ...] = ("secondary", "vocational", "bachelor", "master", "doctorate")
DEFAULT_LEVELS = list(LEVELS)

LEVEL_LABELS: dict[str, str] = {
    "secondary": "High school",
    "vocational": "Vocational / apprenticeship",
    "bachelor": "Bachelor's",
    "master": "Master's",
    "doctorate": "Doctorate / PhD",
}

#: There is deliberately no ``"none"`` level. An ad that says "no degree required" states no
#: requirement, so it classifies as ``None`` and passes for everybody — which is the correct
#: behaviour and needs no vocabulary of its own. Offering "none" as a checkbox would also
#: invite confusing it with "the ad never said", which is a different and far larger set.

MAX_FIELD_LEN = 120

# --------------------------------------------------------------- normalisation --


#: Markup is stripped *before* normalising, and that is not cosmetic. Descriptions arrive as
#: raw HTML, and normalising first would flatten a tag's attributes into ordinary prose — which
#: is how a benefits icon, `<img src=".../uploads/ausbildung-1.svg">`, put a vocational-training
#: requirement on three unrelated SAP consultant roles in the validation sample. Anything inside
#: angle brackets is machinery, never a sentence an employer wrote.
_MARKUP = re.compile(r"<[^>]*>|&[a-z]+;|&#\d+;|https?://\S+|\bwww\.\S+", re.I)


def normalise(text: Optional[str]) -> str:
    """Strip markup, lower-case, fold diacritics, reduce everything else to single spaces.

    Shares `geo.normalise`'s shape and for the same reason — descriptions arrive as HTML soup
    in five languages ("<li>Abgeschlossenes Studium</li>", "středoškolské vzdělání") and the
    patterns below should only ever be written once, against one flattened form. Punctuation
    collapsing is why the patterns spell "ph d" and "m sc" alongside "phd" and "msc".
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", _MARKUP.sub(" ", str(text)))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_only = (ascii_only.replace("ł", "l").replace("Ł", "l")
                            .replace("ø", "o").replace("Ø", "o")
                            .replace("đ", "d").replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


# ------------------------------------------------------------------- patterns ---
# One pattern per level. Written against the normalised form, so "Ph.D." is "ph d" and
# "M.Sc." is "m sc". Every one of these had to survive a sample of real postings — see the
# module docstring for where the sample came from.
#
# The bare word "master" is deliberately absent: "Master Data Management", "Scrum Master" and
# "mastery of SQL" are all common in exactly the roles this product matches, and matching them
# would put a master's requirement on jobs that ask for none. Every `master` rule below is
# anchored to a degree word or an abbreviation.
_LEVEL_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # `phd` must be followed by "in", "degree" or "or" — i.e. it has to be naming a
    # qualification rather than a person. Bare `\bphd\b` matched "220 veterinarians, PhD
    # nutritionists", "no PhD program teaches", and "leading PhD programs like Stanford" in
    # the validation sample: three employer blurbs out of nine doctorate hits.
    ("doctorate", re.compile(
        r"\bph\s?d\s+(?:in|degree|or)\b|\bdoctorate\b|\bdoctoral\s+degree\b|"
        r"\bdoktorat\b|\bdoktorsk\w*|\bdr\s+rer\b", re.I)),
    # No bare `\bmba\b`: "access a mini MBA via the academy" is a *benefit*, and it appeared
    # as one in the sample. A genuine MBA requirement almost always also says "degree".
    ("master", re.compile(
        r"\bmaster\s?s?\s+degree\b|\bmaster\s+of\s+(?:science|arts|engineering)\b|"
        r"\bm\s?sc\b|\bmba\s+degree\b|\bmagistersk\w*|\bmagister\b|\bmgr\b|"
        r"\bdiplom\s?ingenieur\b|\bmaster\s+s\b|\bms\s+(?:in|or)\s+(?:ph\s?d\b|\w)", re.I)),
    # `bs` only inside a degree list ("BS/MS/PhD in Computer Science"), never alone.
    ("bachelor", re.compile(
        r"\bbachelor\w*|\bb\s?sc\b|\bundergraduate\s+degree\b|\bbakalarsk\w*|\bbakalar\b|"
        r"\bbs\s+(?:ms|m\s?s|ph\s?d)\b", re.I)),
    # Bare `\bausbildung\b` is dropped: in German ads it far more often names training the
    # employer *offers* ("Aus- und Weiterbildung") than a qualification it demands.
    ("vocational", re.compile(
        r"\bapprenticeship\b|\bberufsausbildung\b|\babgeschlossene[rn]?\s+ausbildung\b|"
        r"\bausbildung\s+als\b|\bvocational\s+(?:training|qualification|education|degree|school)\b|"
        r"\bvyucen\w*|\bvyucn\w*|\bstredni\s+odborn\w*|\bstredne\s+odborn\w*", re.I)),
    # `maturit` needs the negative lookahead or it swallows English "maturity" — "drive it to
    # maturity", "maturity of process", "customers ... from early stage startups to
    # established global enterprises" were three of the sample's secondary hits.
    # "post secondary education" is North American for *tertiary* — the opposite of what the
    # words look like — so the lookbehind is load-bearing, not defensive.
    ("secondary", re.compile(
        r"\bhigh\s+school\b|(?<!post\s)\bsecondary\s+(?:education|school)\b|\bhochschulreife\b|"
        r"\babitur\b|\bstredoskolsk\w*|\bmaturit(?!y)\w*|\bsrednie\s+wyksztalcenie\b", re.I)),
)

# "A degree in Computer Science", "abgeschlossenes Studium", "vysokoškolské vzdělání" — a
# university qualification with no level named. Read as `bachelor`, because bachelor is the
# **floor** such a sentence demands: an ad that wants "a degree" is satisfied by one. Reading
# it as master would exclude every bachelor-holding subscriber on a guess the ad never made.
_GENERIC_DEGREE = re.compile(
    r"\buniversity\s+degree\b|\bacademic\s+degree\b|\bcollege\s+degree\b|"
    r"\bdegree\s+in\b|\bdegree\s+or\b|\bdegreed\b|"
    r"\babgeschlossenes\s+studium\b|\babgeschlossenes\s+hochschulstudium\b|"
    r"\bhochschulabschluss\b|\bstudienabschluss\b|"
    r"\bvysokoskolsk\w*|\bvysoka\s+skola\b|\bvs\s+vzdelani\b|"
    r"\bwyzsze\s+wyksztalcenie\b", re.I)

# Phrases that turn a stated qualification into a preference. Checked **only in a window
# around the mention**, never across the whole document — "is a plus" and "not required"
# appear in almost every job ad about something else entirely, so a document-wide check would
# soften everything and leave the filter inert.
#
# 286 of the 952 postings that state a degree soften it this way (30%). Treating those as
# binding is the failure mode that matters: "Bachelor's degree or equivalent working
# experience" excludes nobody, and filtering someone out of it would be wrong.
_SOFTENER = re.compile(
    # "or equivalent" and its cousins
    # "or the equivalent combination of other post secondary education" is a real posting, and
    # the article is why `or\s+equivalent` alone was not enough.
    r"\bor\s+(?:the\s+|an?\s+)?equivalent\b|\bequivalent\s+combination\b|"
    r"\bequivalent\s+(?:practical\s+|work(?:ing)?\s+)?experience\b|"
    r"\bor\s+(?:an?\s+)?(?:comparable|similar)\b|\bcomparable\s+qualification\b|"
    r"\bvergleichbar\w*|\boder\s+(?:eine[rn]?\s+)?ahnlich\w*|\bahnliche\s+qualifikation\b|"
    r"\bnebo\s+ekvivalent\w*|\bor\s+diploma\b|"
    # stated as a preference rather than a requirement
    r"\bpreferred\b|\bpreferable\b|\bplus\b|\badvantage\b|\bnice\s+to\s+have\b|"
    r"\bwelcome\b|\bideally\b|\bvyhodou\b|\bvon\s+vorteil\b|\bwunschenswert\b|"
    r"\bwould\s+be\s+(?:a\s+)?(?:plus|benefit)\b|"
    # explicitly denied. German and Czech ads say this often and say it plainly — "ein
    # bestimmter Studienabschluss ist für uns nicht entscheidend" and "nemusíš mít doktorát
    # z matiky" both classified as *requirements* before these were added.
    r"\bnot\s+(?:strictly\s+)?(?:required|necessary|mandatory|a\s+must)\b|\bno\s+degree\b|"
    r"\bnicht\s+(?:zwingend\s+)?(?:erforderlich|notwendig|entscheidend|voraussetzung)\b|"
    r"\bkeine?\s+(?:zwingende\s+)?voraussetzung\b|"
    r"\bnemusis\b|\bnemusite\b|\bnevyzadujeme\b|\bneni\s+podminkou\b|\bnutne\s+neni\b|"
    # currently studying is a different claim from having finished — these are working-student
    # and apprentice ads, where the level named is what you are enrolled in, not a floor.
    r"\bcurrently\s+(?:enrolled|pursuing|studying)\b|\bstudying\s+towards\b|"
    r"\bworking\s+student\b|\bwerkstudent\w*|\bimmatrikuliert\b", re.I)

# Contexts where a qualification word names something other than a requirement. These are not
# hypothetical: in a 16-posting sample of real matches, **three** were an employer describing
# itself — a university advertising its "top-ranked bachelor's, master's, and doctoral degree
# programs", and two companies boasting that their team includes PhDs. Classifying those as
# requirements would delete an ordinary job from the digest of everyone without a doctorate.
#: `program(?:me)?s` and not `programme?s` — the latter matches "programmes" and "programms"
#: but *not* the American "programs", which is the spelling the university blurb actually used.
_BLURB = re.compile(
    r"\bdegree\s+program(?:me)?s\b|\bdegree\s+courses\b|\bph\s?d\s+program(?:me)?s?\b|"
    r"\bpost\s?docs?\b|\bour\s+team\b|\bwe\s+are\s+a\b|\bnationalities\b|\balumni\b|"
    r"\bstudents\b|\bgraduate\s+program(?:me)?\b|\buniversity\s+of\b|"
    r"\bteam\s+(?:includes|hails|ranging)\b|\bscientists\s+working\b", re.I)

#: How far either side of a mention the softener/blurb check looks. Wide enough to catch
#: "Bachelor's degree in Construction Management, Engineering, Architecture, or a related
#: technical discipline, or equivalent" — where the softener is 90 characters past the word —
#: and narrow enough not to swallow the next bullet point.
_WINDOW = 140

#: Descriptions are HTML soup and occasionally enormous. Requirements sit in a "Qualifications"
#: section that is often past the 2 000-character mark `geo` scans to, so this cap is much
#: higher; it exists only to bound a pathological input.
_MAX_SCAN = 20_000


def classify_requirement(description: Optional[str], title: Optional[str] = None) -> Optional[str]:
    """The lowest qualification this posting requires, or ``None`` when it does not say.

    ``None`` is the answer for ~97% of live postings and is a first-class value — the SQL gate
    keeps it and the AI matcher is handed the preference instead. Returning ``None`` when
    unsure is always the safe direction here: the posting is shown, and the model reads the
    description. The unsafe direction is inventing a requirement, which silently removes a job
    from a subscriber's digest.

    Three rules do the work, and each exists because of something real in the corpus:

      * **A softened mention does not count.** "Bachelor's degree or equivalent working
        experience" requires no degree, and 30% of the postings that name one soften it.
      * **A blurb mention does not count.** An employer listing its own degree programmes or
        boasting that its team includes PhDs is not stating a requirement.
      * **The lowest surviving level wins.** An ad naming several ("Bachelor's or Master's")
        is satisfied by the lowest, and taking the lowest also caps the damage of any blurb
        this misses — the university sample would land on `bachelor` rather than `doctorate`.
    """
    text = normalise(f"{title or ''} {(description or '')[:_MAX_SCAN]}")
    if not text:
        return None

    found: set[str] = set()
    for level, pattern in _LEVEL_PATTERNS:
        for m in pattern.finditer(text):
            if _binding(text, m.start(), m.end()):
                found.add(level)
                break  # one binding mention is enough for this level
    # A generic "a degree in ..." is a bachelor-level floor, but only if nothing more specific
    # already survived — otherwise it would drag a doctorate-only ad down to bachelor.
    if not found:
        for m in _GENERIC_DEGREE.finditer(text):
            if _binding(text, m.start(), m.end()):
                found.add("bachelor")
                break
    if not found:
        return None
    return next(lv for lv in LEVELS if lv in found)


def _binding(text: str, start: int, end: int) -> bool:
    """Is the mention at ``[start:end)`` an actual requirement?

    False when a softener or a self-description sits within `_WINDOW` characters of it. The
    window is what makes this usable at all: checked document-wide, "plus" and "preferred"
    match nearly every job ad and every requirement would read as optional.
    """
    window = text[max(0, start - _WINDOW):end + _WINDOW]
    return not (_SOFTENER.search(window) or _BLURB.search(window))


# --------------------------------------------------------- preference values ---


def clean_levels(values: Any) -> list[str]:
    """Normalise an education selection, in `LEVELS` order.

    An empty or entirely unrecognised selection becomes **all of them**, not none — the same
    rule as `geo.clean_work_modes`, for the same reason. A subscriber who clears every box has
    expressed no preference, and reading that as "no posting is acceptable" would empty their
    digest without a single error. Widening is the only safe reading of silence.
    """
    wanted = {str(v).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
              for v in (values or [])}
    # Spellings a hand-written API client, or an older build of the site, might send.
    aliases = {
        "highschool": "secondary", "high": "secondary", "maturita": "secondary",
        "secondaryschool": "secondary", "secondaryeducation": "secondary",
        "apprenticeship": "vocational", "ausbildung": "vocational", "trade": "vocational",
        "bachelors": "bachelor", "bsc": "bachelor", "undergraduate": "bachelor",
        "masters": "master", "msc": "master", "mba": "master", "graduate": "master",
        "phd": "doctorate", "doctoral": "doctorate", "doctor": "doctorate",
    }
    wanted = {aliases.get(w, w) for w in wanted}
    out = [lv for lv in LEVELS if lv in wanted]
    return out or list(DEFAULT_LEVELS)


def clean_field(value: Any) -> Optional[str]:
    """Normalise the free-text field of study, or ``None``.

    Kept as the subscriber typed it apart from whitespace and a length cap: it is read by the
    AI matcher, not compared to anything, so folding case or diacritics would only make it
    read worse in the prompt. It never reaches SQL — see the module docstring.
    """
    text = " ".join(str(value or "").split())
    return text[:MAX_FIELD_LEN] or None


def describe_levels(levels: Iterable[str], field: Optional[str] = None) -> str:
    """One human sentence for the matcher prompt.

    e.g. "will take roles asking for at most a Bachelor's (field of study: Economics)".
    """
    picked = clean_levels(levels)
    if len(picked) == len(LEVELS):
        phrase = "any education requirement"
    else:
        names = [LEVEL_LABELS[lv] for lv in picked]
        joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
        phrase = f"only roles requiring {joined}"
    studied = clean_field(field)
    return f"{phrase} (studied: {studied})" if studied else phrase


# ---------------------------------------------------------------- the SQL gate --


def education_predicate(profile: dict, alias: str = "p") -> tuple[str, list[Any]]:
    """SQL fragment (plus params) restricting postings to a profile's accepted requirements.

    Two rules, and the second is the one that matters:

      * A profile accepting every level gets **no filter at all** — the default, and the state
        every existing subscriber is migrated into, so nobody's digest narrows without them
        choosing it.
      * **A null `education_min` always passes.** It is null for ~97% of live postings and for
        100% of the Czech and Slovak inventory, which has no description text at all. Treating
        unknown as "requires a degree" would delete almost the entire corpus from the digest of
        anyone who did not tick every box — the largest possible version of the failure this
        codebase keeps meeting. The preference reaches the AI matcher instead.

    So the gate is exact where the data is provable (~3.2% of postings) and silent everywhere
    else, which is a much smaller promise than the UI should make. Say so in the UI copy.
    """
    levels = clean_levels(profile.get("education_levels"))
    if len(levels) == len(LEVELS):
        return "true", []
    return f"({alias}.education_min is null or {alias}.education_min = any(%s))", [levels]
