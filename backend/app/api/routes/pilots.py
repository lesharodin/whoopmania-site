# backend/app/api/routes/pilots.py

import time

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models.pilot import Pilot
from ...models.event import Event
from ...models.qualification import QualificationResult
from ...models.bracket import BracketRace, BracketRaceResult
from ...utils.formatting import format_ms

router = APIRouter(prefix="/pilots", tags=["pilots"])

templates = Jinja2Templates(directory="backend/app/templates")
templates.env.filters["format_ms"] = format_ms


@router.get("", include_in_schema=False, name="pilots")
async def pilots_list(
    request: Request,
    db: Session = Depends(get_db),
):
    stmt = select(Pilot).order_by(Pilot.nickname.asc())
    pilots = db.scalars(stmt).all()

    return templates.TemplateResponse(
        "pilots_list.html",
        {"request": request, "pilots": pilots},
    )


@router.get("/{pilot_id}", include_in_schema=False, name="pilot_detail")
async def pilot_detail(
    pilot_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    pilot = db.get(Pilot, pilot_id)
    if not pilot:
        raise HTTPException(status_code=404, detail="Pilot not found")

    # все участия пилота в событиях по таблице квалификации
    stmt = (
        select(QualificationResult, Event)
        .join(Event, QualificationResult.event_id == Event.id)
        .where(QualificationResult.pilot_id == pilot_id)
        .order_by(Event.date.desc(), QualificationResult.rank.asc())
    )
    rows = db.execute(stmt).all()

    participations = []
    for q, e in rows:
        participations.append(
            {
                "event": e,
                "qual": q,
            }
        )

    awards_stmt = (
        select(BracketRaceResult, BracketRace, Event)
        .join(BracketRace, BracketRaceResult.bracket_race_id == BracketRace.id)
        .join(Event, BracketRace.event_id == Event.id)
        .where(
            BracketRaceResult.pilot_id == pilot_id,
            BracketRace.bracket_side == "final",
            BracketRaceResult.final_position.is_not(None),
            BracketRaceResult.final_position <= 4,
        )
        .order_by(Event.date.desc())
    )
    final_results = [result for result, _, _ in db.execute(awards_stmt).all()]
    place_counts = [
        {
            "place": place,
            "count": count,
        }
        for place in range(1, 5)
        if (count := sum(result.final_position == place for result in final_results))
    ]

    ranked_results = [item["qual"] for item in participations if item["qual"].rank]
    qualification_wins = sum(q.rank == 1 for q in ranked_results)

    return templates.TemplateResponse(
        "pilot_detail.html",
        {
            "request": request,
            "pilot": pilot,
            "participations": participations,
            "place_counts": place_counts,
            "qualification_wins": qualification_wins,
            "cache_bust": int(time.time()),
        },
    )
