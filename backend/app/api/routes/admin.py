# backend/app/api/routes/admin.py

import json
import os
import secrets
from datetime import date
from typing import Any, Dict, List

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    File,
    UploadFile,
    HTTPException,
)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models.event import Event, EventType
from ...models.pilot import Pilot
from ...models.qualification import QualificationResult
from ...models.bracket import BracketRace, BracketRaceResult
from ...utils.formatting import format_ms


# --- простая Basic-авторизация admin/admin ------------------------

security = HTTPBasic()


def admin_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, "admin")

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(admin_auth)],
)

templates = Jinja2Templates(directory="backend/app/templates")
templates.env.filters["format_ms"] = format_ms


# ----------------------------------------------------------------
# helpers
# ----------------------------------------------------------------

def get_or_create_pilot(db: Session, nickname: str) -> Pilot:
    stmt = select(Pilot).where(Pilot.nickname == nickname)
    p = db.scalar(stmt)
    if p:
        return p
    p = Pilot(nickname=nickname)
    db.add(p)
    db.flush()
    return p


# ----------------------------------------------------------------
# views
# ----------------------------------------------------------------

@router.get("/", include_in_schema=False, name="admin_index")
async def admin_index(request: Request, db: Session = Depends(get_db)):
    stmt = select(Event).order_by(Event.date.desc())
    events = db.scalars(stmt).all()

    return templates.TemplateResponse(
        "admin_index.html",
        {"request": request, "events": events},
    )


@router.get("/events/new", include_in_schema=False, name="admin_new_event_form")
async def admin_new_event_form(request: Request):
    return templates.TemplateResponse(
        "admin_event_form.html",
        {"request": request},
    )


@router.post("/events/new", include_in_schema=False, name="admin_create_event")
async def admin_create_event(
    request: Request,
    name: str = Form(...),
    date_str: str = Form(...),
    location: str | None = Form(None),
    description: str | None = Form(None),
    event_type: str = Form("race"),
    poster: UploadFile | None = File(None),
    rh_json_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    try:
        y, m, d = map(int, date_str.split("-"))
        event_date = date(y, m, d)
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректная дата")

    try:
        etype = EventType(event_type)
    except ValueError:
        etype = EventType.RACE

    event = Event(
        name=name,
        date=event_date,
        location=location,
        description=description,
        event_type=etype,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    return RedirectResponse(
        url=request.url_for("event_detail", event_id=event.id),
        status_code=303,
    )


@router.get("/events/{event_id}/edit", include_in_schema=False, name="admin_edit_event")
async def admin_edit_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    stmt = (
        select(QualificationResult)
        .where(QualificationResult.event_id == event_id)
        .order_by(QualificationResult.rank.asc())
    )
    qualification = db.scalars(stmt).all()

    return templates.TemplateResponse(
        "admin_event_edit.html",
        {
            "request": request,
            "event": event,
            "qualification": qualification,
        },
    )


@router.post("/events/{event_id}/edit", include_in_schema=False, name="admin_update_event")
async def admin_update_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # логика обновления оставлена без изменений
    form = await request.form()
    name = form.get("name") or event.name
    event.name = name
    db.commit()

    return RedirectResponse(
        url=request.url_for("admin_edit_event", event_id=event.id),
        status_code=303,
    )


@router.post(
    "/events/{event_id}/create_bracket",
    include_in_schema=False,
    name="admin_create_bracket",
)
async def admin_create_bracket(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    existing = db.scalar(
        select(BracketRace).where(BracketRace.event_id == event_id)
    )
    if existing:
        return RedirectResponse(
            url=request.url_for("admin_edit_event", event_id=event_id),
            status_code=303,
        )

    config = [
        (1, "upper_1_16", "1/16"),
        (2, "upper_1_16", "1/16"),
        (3, "upper_1_16", "1/16"),
        (4, "upper_1_16", "1/16"),
        (5, "lower_1_16", "1/16"),
        (6, "upper_1_8", "1/8"),
        (7, "lower_1_16", "1/16"),
        (8, "upper_1_8", "1/8"),
        (9, "lower_1_8", "1/8"),
        (10, "lower_1_8", "1/8"),
        (11, "upper_1_4", "1/4"),
        (12, "lower_1_4", "1/4"),
        (13, "semi", "Полуфинал"),
        (14, "final", "Финал"),
    ]

    for number, stage, short_label in config:
        side = "final" if stage == "final" else ("upper" if "upper" in stage else "lower")
        race = BracketRace(
            event_id=event_id,
            number=number,
            name=f"Гонка {number}",
            stage=stage,
            short_label=short_label,
            bracket_side=side,
        )
        db.add(race)

    db.commit()

    return RedirectResponse(
        url=request.url_for("admin_edit_event", event_id=event_id),
        status_code=303,
    )
