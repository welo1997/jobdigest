"""FastAPI app for the job-matching service.

Endpoints (Phase 1):
    GET  /health
    GET  /search           ad-hoc filtered postings (no stored profile)
    POST /profiles         create a search profile
    GET  /profiles/{id}
    GET  /matches          scored + ranked matches for a profile
    POST /matches/{profile_id}/{posting_id}/status   save | dismiss | applied

Run:  DATABASE_URL=... uvicorn service.api:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from service import store

# A fixed dev user so the API is usable before Supabase auth is wired in (Phase 3).
DEV_USER_ID = "00000000-0000-0000-0000-000000000001"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_pool()
    yield


app = FastAPI(title="Job & Freelance Matcher", version="0.1.0", lifespan=lifespan)


class ProfileIn(BaseModel):
    label: str = "My search"
    stack: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=lambda: ["junior", "mid"])
    regions: list[str] = Field(default_factory=lambda: ["cz", "eu", "worldwide"])
    role_categories: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=lambda: ["permanent", "freelance/contract"])
    part_time_only: bool = False
    eligible_only: bool = True
    sectors: list[str] = Field(default_factory=list)
    min_score: int = 6


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "active_postings": store.count_active()}


@app.get("/search")
def search(
    region: Optional[list[str]] = Query(None),
    seniority: Optional[list[str]] = Query(None),
    work_type: Optional[list[str]] = Query(None),
    role_category: Optional[list[str]] = Query(None),
    part_time_only: bool = False,
    eligible_only: bool = True,
    limit: int = 60,
) -> dict:
    """Ad-hoc query without a saved profile — powers the public/anonymous search."""
    profile = {
        "regions": region, "seniorities": seniority, "work_types": work_type,
        "role_categories": role_category, "part_time_only": part_time_only,
        "eligible_only": eligible_only,
    }
    rows = store.query_candidates(profile, limit=limit)
    return {"count": len(rows), "results": rows}


@app.post("/profiles")
def create_profile(body: ProfileIn) -> dict:
    return store.create_profile(DEV_USER_ID, body.model_dump())


@app.get("/profiles/{profile_id}")
def get_profile(profile_id: str) -> dict:
    prof = store.get_profile(profile_id)
    if not prof:
        raise HTTPException(404, "profile not found")
    return prof


@app.get("/matches")
def matches(profile_id: str, limit: int = 30) -> dict:
    """Return the AI matcher's picks for this profile (best fit first).

    Read-only view of the `matches` table — the ranking was produced in batch by
    `service.matcher`; this endpoint does no scoring or inference of its own."""
    profile = store.get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile not found")
    results = store.matched_jobs(profile_id, limit=limit)
    return {"count": len(results), "results": results}


class StatusIn(BaseModel):
    status: Literal["new", "saved", "dismissed", "applied"]


@app.post("/matches/{profile_id}/{posting_id}/status")
def set_status(profile_id: str, posting_id: str, body: StatusIn) -> dict:
    store.set_match_status(profile_id, posting_id, body.status)
    return {"ok": True, "status": body.status}
