# backend/app/api/routes/admin.py

import json
import logging
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
from sqlalchemy.sql import exists

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
maintenance_router = APIRouter(
    tags=["admin"],
    dependencies=[Depends(admin_auth)],
)

templates = Jinja2Templates(directory="backend/app/templates")
templates.env.filters["format_ms"] = format_ms

logger = logging.getLogger("whoopmania.admin")


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


def parse_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def extract_qual_rows(rh_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(rh_json.get("event_leaderboard"), dict):
        rows = rh_json["event_leaderboard"].get("by_consecutives")
        if isinstance(rows, list):
            return rows

    if isinstance(rh_json.get("leaderboard"), dict):
        rows = rh_json["leaderboard"].get("by_consecutives")
        if isinstance(rows, list):
            return rows

    raise ValueError(
        "No 'by_consecutives' leaderboard found in RH JSON "
        "(event_leaderboard/leaderboard)"
    )


def import_qualification_rows(
    db: Session,
    event_id: int,
    rows: List[Dict[str, Any]],
) -> int:
    db.query(QualificationResult).filter(
        QualificationResult.event_id == event_id
    ).delete(synchronize_session=False)

    imported = 0
    for row in rows:
        nickname = str(row.get("callsign") or "Unknown").strip() or "Unknown"
        pilot = get_or_create_pilot(db, nickname)

        q = QualificationResult(
            event_id=event_id,
            pilot_id=pilot.id,
            rank=parse_optional_int(row.get("position")),
            best_lap_ms=parse_optional_int(row.get("fastest_lap_raw")),
            best3_avg_ms=parse_optional_int(row.get("consecutives_raw")),
            laps_total=parse_optional_int(row.get("laps")),
            attempts_count=parse_optional_int(row.get("starts")),
            consecutives_count=parse_optional_int(row.get("consecutives_base")),
        )
        db.add(q)
        imported += 1

    return imported


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

    if rh_json_file and rh_json_file.filename:
        try:
            payload = await rh_json_file.read()
            rh_json = json.loads(payload.decode("utf-8"))
            rows = extract_qual_rows(rh_json)
            imported = import_qualification_rows(db, event_id=event.id, rows=rows)
            db.commit()
            logger.info(
                "RH qualification imported on create for event_id=%s, rows=%s, filename=%s",
                event.id,
                imported,
                rh_json_file.filename,
            )
        except Exception:
            logger.exception(
                "Failed to import RH qualification on create for event_id=%s, filename=%s",
                event.id,
                rh_json_file.filename,
            )
            raise HTTPException(status_code=400, detail="Ошибка импорта RH JSON")

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

    form = await request.form()
    logger.info("Admin update started for event_id=%s", event_id)

    name = (form.get("name") or event.name or "").strip()
    event.name = name or event.name

    date_str = form.get("date_str")
    if date_str:
        try:
            y, m, d = map(int, date_str.split("-"))
            event.date = date(y, m, d)
        except Exception:
            logger.warning(
                "Invalid date in admin update for event_id=%s: %s",
                event_id,
                date_str,
            )
            raise HTTPException(status_code=400, detail="Некорректная дата")

    event.location = (form.get("location") or "").strip() or None
    event.description = (form.get("description") or "").strip() or None

    event_type = form.get("event_type")
    if event_type:
        try:
            event.event_type = EventType(event_type)
        except ValueError:
            logger.warning(
                "Invalid event_type in admin update for event_id=%s: %s",
                event_id,
                event_type,
            )

    rh_json_file = form.get("rh_json_file")
    if isinstance(rh_json_file, UploadFile) and rh_json_file.filename:
        try:
            payload = await rh_json_file.read()
            rh_json = json.loads(payload.decode("utf-8"))
            rows = extract_qual_rows(rh_json)
            imported = import_qualification_rows(db, event_id=event_id, rows=rows)
            logger.info(
                "RH qualification imported for event_id=%s, rows=%s, filename=%s",
                event_id,
                imported,
                rh_json_file.filename,
            )
        except json.JSONDecodeError:
            logger.warning(
                "Invalid JSON file uploaded for event_id=%s, filename=%s",
                event_id,
                rh_json_file.filename,
            )
            raise HTTPException(status_code=400, detail="Некорректный JSON")
        except ValueError as exc:
            logger.warning(
                "Unsupported RH JSON structure for event_id=%s, filename=%s: %s",
                event_id,
                rh_json_file.filename,
                exc,
            )
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            logger.exception(
                "Failed to import RH qualification for event_id=%s, filename=%s",
                event_id,
                rh_json_file.filename,
            )
            raise HTTPException(status_code=500, detail="Ошибка импорта RH JSON")

    db.commit()
    logger.info("Admin update completed for event_id=%s", event_id)

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


@maintenance_router.post("/pilots/dedupe", include_in_schema=False, name="admin_dedupe_pilots")
async def admin_dedupe_pilots(
    request: Request,
    db: Session = Depends(get_db),
):
    nicknames = db.scalars(
        select(Pilot.nickname).where(Pilot.nickname.is_not(None)).distinct()
    ).all()

    for nickname in nicknames:
        pilots = db.scalars(
            select(Pilot).where(Pilot.nickname == nickname).order_by(Pilot.id.asc())
        ).all()
        if len(pilots) < 2:
            continue

        primary = pilots[0]
        duplicates = pilots[1:]

        for dup in duplicates:
            db.query(QualificationResult).filter(
                QualificationResult.pilot_id == dup.id
            ).update({"pilot_id": primary.id}, synchronize_session=False)
            db.query(BracketRaceResult).filter(
                BracketRaceResult.pilot_id == dup.id
            ).update({"pilot_id": primary.id}, synchronize_session=False)
            db.delete(dup)

    db.commit()
    return RedirectResponse(url=request.url_for("admin_index"), status_code=303)


@maintenance_router.post(
    "/dev/cleanup_orphan_pilots",
    include_in_schema=False,
    name="admin_cleanup_orphan_pilots",
)
async def admin_cleanup_orphan_pilots(
    request: Request,
    db: Session = Depends(get_db),
):
    orphan_pilots = db.scalars(
        select(Pilot).where(
            ~exists(
                select(QualificationResult.id).where(
                    QualificationResult.pilot_id == Pilot.id
                )
            ),
            ~exists(
                select(BracketRaceResult.id).where(
                    BracketRaceResult.pilot_id == Pilot.id
                )
            ),
        )
    ).all()

    for pilot in orphan_pilots:
        db.delete(pilot)

    db.commit()
    return RedirectResponse(url=request.url_for("admin_index"), status_code=303)
