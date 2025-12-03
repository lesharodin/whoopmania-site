# backend/app/api/routes/pilots.py

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models.pilot import Pilot
from ...models.event import Event
from ...models.qualification import QualificationResult
from ...utils.formatting import format_ms

router = APIRouter(prefix="/pilots", tags=["pilots"])

templates = Jinja2Templates(directory="backend/app/templates")
templates.env.filters["format_ms"] = format_ms


@router.get("/", include_in_schema=False)
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


@router.get("/{pilot_id}", include_in_schema=False)
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

    return templates.TemplateResponse(
        "pilot_detail.html",
        {
            "request": request,
            "pilot": pilot,
            "participations": participations,
        },
    )
