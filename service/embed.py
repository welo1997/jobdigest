"""Embeddings for retrieval — the one definition of what gets vectorised and how.

**What this replaces, and what it deliberately does not.** `query_shortlist_meta` admits rows
via `role_category = any(...) OR search_tsv @@ plainto_tsquery(...)` and orders them by
`first_seen_at desc`. Both halves are broken in ways no amount of taxonomy work fixes: the
keyword surface reads English (`Systemutvikler`, `Dataingeniør`, `Produktsjef` all classify
`uncategorised`, and `Sikkerhetsanalytiker` classifies as `data_analysis`, which is wrong), and
`first_seen_at` ordered a 12 390-row tie by physical scan order. Vector similarity replaces
*those two things*.

It replaces nothing else, and that is measured rather than assumed. On 2026-08-08 a mid-level
data profile scored `Head of Data Engineering - 12+ years, team of 20` at **+0.633**, above
`Backend Engineer - Python, PostgreSQL. Prague` at **+0.585**. The model does not encode
seniority. So the structured gates stay (`geo`, `eligibility`, `education`, `work_mode`,
`seniorities`), and the AI matcher stays as what actually decides which jobs are emailed and
writes the reason line. Cosine's job is to narrow 96 583 candidates to a couple of dozen good
ones so the model reads 25 instead of 120 — not to rank the final list.

**Nulls are always valid.** A posting with no vector keeps its place via the existing
`first_seen_at` ordering. ~5% of active postings carry no description, embedding runs on a
schedule that can be skipped, and a missing vector must never remove a job from a digest — the
same rule as `work_mode`, `education_min` and an unresolved city.

Model choice, footprint and the quality measurement: `notes/2026-08-08-embedding-model-selection.md`.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable, Sequence

logger = logging.getLogger("service.embed")

# --- the model identity -------------------------------------------------------
#: Vector width. Pinned to `halfvec(384)` in migration 015; changing this is a migration plus a
#: full re-embed, not a config tweak.
DIM = 384

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

#: Exact fastembed version this space was produced by. Pinned in requirements-worker.txt.
FASTEMBED_VERSION = "0.8.0"

#: What lands in `postings.embedding_model` / `profiles.embedding_model`.
#:
#: **The library version is part of the identity, not decoration.** fastembed 0.8.0 warns that
#: this model "now uses mean pooling instead of CLS embedding" — i.e. a library upgrade moved the
#: embedding space while the model name stayed byte-identical. Its output is also not
#: L2-normalised (measured norm 3.489) unlike the reference sentence-transformers model, which
#: corroborates that the wrapper does not reproduce upstream behaviour. Recording only the model
#: name would leave a pooling change invisible, and mixed pooling in one column means cosine
#: distance quietly compares vectors from two different spaces. That is the failure
#: `backfill_geo` and `backfill_education` exist to prevent for their columns, arriving through
#: the one axis a model name cannot show.
EMBEDDING_MODEL_ID = f"{MODEL_NAME.split('/')[-1]}@fastembed-{FASTEMBED_VERSION}"

# --- text budgets -------------------------------------------------------------
#: Characters of description fed to the model. This is a *throughput* lever as much as a quality
#: one: measured 13.9 docs/s at 5 292 characters against 79 docs/s at title length, and the
#: corpus averages 4 861 characters, so an untruncated backfill of 96 583 rows is ~116 minutes.
#: The model's own input window truncates well before this anyway; what the extra characters buy
#: is throughput cost, not signal.
DESC_CHARS = int(os.environ.get("EMBED_DESC_CHARS", "1000"))

#: Batch handed to onnxruntime at once. Larger batches cost RAM, and the box has no swap.
BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "16"))

#: onnxruntime threads. The VPS has 2 vCPU and Postgres is on the same box.
THREADS = int(os.environ.get("EMBED_THREADS", "2"))

_WS = re.compile(r"\s+")
_HTML = re.compile(r"<[^>]+>")

_model = None


def _load():
    """Load the model once, lazily.

    Lazy on purpose: importing this module must stay free so `service.webapp` can import
    anything alongside it without pulling ~550 MiB of weights into the always-on `api`
    container. Only the embed step ever calls this.
    """
    global _model
    if _model is None:
        from fastembed import TextEmbedding  # imported here, see above

        logger.info("loading %s (threads=%d)", MODEL_NAME, THREADS)
        _model = TextEmbedding(MODEL_NAME, threads=THREADS)
    return _model


def clean(text: str | None) -> str:
    """Collapse whitespace and strip tags. Cheap, and the corpus needs it.

    Several sources ship HTML in `description` (and one ships an `<img>` benefits icon that
    already fooled the education classifier once). Tags carry no meaning for a sentence
    embedder and eat the input window, so they go.
    """
    if not text:
        return ""
    return _WS.sub(" ", _HTML.sub(" ", text)).strip()


def posting_text(row: dict) -> str:
    """The text that represents a posting in vector space.

    Order matters: title first, because it is the highest-signal field and the only one present
    on every row, and the model's input window truncates from the end. A row with no description
    therefore still produces a title embedding — weaker (bare two-word titles measured *not*
    separable from unrelated ones), which is exactly why cosine narrows rather than decides.
    """
    parts = [
        clean(row.get("title")),
        clean(row.get("company")),
        clean(row.get("location")),
        clean(row.get("description"))[:DESC_CHARS],
    ]
    return " | ".join(p for p in parts if p)


def profile_text(profile: dict) -> str:
    """The query side of the same space.

    Deliberately excludes anything a structured gate already handles — countries, cities,
    work modes, education levels, seniorities. Encoding a hard filter into a soft similarity
    score is how a filter stops being hard: the vector would trade a location match against a
    skills match, and `geo.location_predicate` exists precisely so that trade never happens.
    What goes in is subject matter: what they call the work, what they know, what they studied.
    """
    parts: list[str] = [clean(profile.get("label"))]

    for cat in (profile.get("role_categories") or []):
        parts.append(str(cat).replace("_", " "))
    parts.extend(str(s) for s in (profile.get("stack") or []))
    parts.extend(str(s) for s in (profile.get("sectors") or []))

    if profile.get("education_field"):
        parts.append(clean(profile["education_field"]))
    if profile.get("cv_summary"):
        parts.append(clean(profile["cv_summary"]))

    return " | ".join(p for p in parts if p)


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """Embed and **L2-normalise**. Returns one 384-float list per input, in order.

    Normalising on write is not cosmetic. fastembed's output for this model is not unit-length
    (measured 3.489), so without it the stored magnitudes vary by text length, `<#>` (inner
    product) and `<=>` (cosine) disagree, and any future switch between the two operators
    silently changes the ranking. Normalised vectors make them equivalent up to a sign.
    """
    if not texts:
        return []
    import numpy as np

    model = _load()
    vecs = np.asarray(list(model.embed(list(texts), batch_size=BATCH_SIZE)), dtype=np.float32)
    if vecs.shape[1] != DIM:
        # Loud rather than silently writing a wrong-width vector the column would reject
        # row-by-row halfway through a backfill.
        raise RuntimeError(
            f"{MODEL_NAME} produced {vecs.shape[1]} dims, expected {DIM}. "
            f"The model or fastembed version changed; migration 015 pins halfvec({DIM})."
        )
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0                     # an empty string embeds to ~0; do not divide by it
    return (vecs / norms).tolist()


def to_pgvector(vec: Iterable[float]) -> str:
    """Render a vector as the literal pgvector accepts for `halfvec` ('[0.1,0.2,...]').

    A string literal rather than the `pgvector` Python adapter so the worker image needs one
    fewer dependency, and so this stays readable in a log when a row is rejected.
    """
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
