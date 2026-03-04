# backend/app/api/routes/admin.py

import json
import logging
import re
import secrets
from datetime import date
from pathlib import Path
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

from sqlalchemy import select, func
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

BRACKET_CONFIG = [
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


def parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
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


async def save_event_poster(event_id: int, poster_file: UploadFile) -> str:
    filename = poster_file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in {".jpg", ".png"}:
        raise ValueError("Poster must be .jpg/.jpeg or .png")

    app_dir = Path(__file__).resolve().parents[2]
    posters_dir = app_dir / "static" / "posters"
    posters_dir.mkdir(parents=True, exist_ok=True)

    # Keep only one active poster file per event id.
    for old_file in posters_dir.glob(f"event_{event_id}.*"):
        old_file.unlink(missing_ok=True)

    target = posters_dir / f"event_{event_id}{ext}"
    payload = await poster_file.read()
    target.write_bytes(payload)
    logger.info(
        "Poster file written for event_id=%s: path=%s bytes=%s",
        event_id,
        target,
        len(payload),
    )
    return target.name


def ensure_event_bracket(db: Session, event_id: int) -> None:
    existing = db.scalar(
        select(BracketRace.id).where(BracketRace.event_id == event_id).limit(1)
    )
    if existing:
        return

    for number, stage, short_label in BRACKET_CONFIG:
        side = "final" if stage == "final" else ("upper" if "upper" in stage else "lower")
        db.add(
            BracketRace(
                event_id=event_id,
                number=number,
                name=f"Гонка {number}",
                stage=stage,
                short_label=short_label,
                bracket_side=side,
            )
        )

    db.flush()


def _extract_heat_rows(heat: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidate = (
        heat.get("results")
        or heat.get("entries")
        or heat.get("leaderboard")
        or heat.get("by_race_time")
        or heat.get("pilots")
    )
    if isinstance(candidate, list):
        return candidate
    if isinstance(candidate, dict):
        for key in ("by_race_time", "meta", "rows", "results", "entries"):
            val = candidate.get(key)
            if isinstance(val, list):
                return val
    return []


def _extract_points_list(row: Dict[str, Any]) -> List[int | None]:
    points = row.get("round_points") or row.get("points")
    if isinstance(points, list):
        return [parse_optional_int(x) for x in points[:5]]
    return []


def _first_non_none(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _place_to_round_points(place: int | None) -> int | None:
    if place is None:
        return None
    if place == 1:
        return 3
    if place == 2:
        return 2
    if place == 3:
        return 1
    if place == 4:
        return 0
    return None


def extract_finals_races(rh_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    heats: List[Dict[str, Any]] | None = None
    if isinstance(rh_json.get("heats"), list):
        heats = rh_json["heats"]
    elif isinstance(rh_json.get("heats"), dict):
        heats = [x for x in rh_json["heats"].values() if isinstance(x, dict)]
    elif isinstance(rh_json.get("results"), dict) and isinstance(rh_json["results"].get("heats"), list):
        heats = rh_json["results"]["heats"]
    elif isinstance(rh_json.get("results"), dict) and isinstance(rh_json["results"].get("heats"), dict):
        heats = [x for x in rh_json["results"]["heats"].values() if isinstance(x, dict)]
    elif isinstance(rh_json.get("event"), dict) and isinstance(rh_json["event"].get("heats"), list):
        heats = rh_json["event"]["heats"]
    elif isinstance(rh_json.get("event"), dict) and isinstance(rh_json["event"].get("heats"), dict):
        heats = [x for x in rh_json["event"]["heats"].values() if isinstance(x, dict)]

    if not heats:
        raise ValueError("No heats list found in RH finals JSON")

    race_payloads: List[Dict[str, Any]] = []
    for index, heat in enumerate(heats, start=1):
        if not isinstance(heat, dict):
            continue

        parsed_number = parse_optional_int(heat.get("number") or heat.get("heat_number"))
        if parsed_number is None:
            name = str(heat.get("name") or heat.get("displayname") or "")
            found = re.search(r"\d+", name)
            parsed_number = int(found.group(0)) if found else index

        rows = _extract_heat_rows(heat)
        if not rows:
            continue

        # Try to pull per-round places from RH rounds leaderboard.
        round_points_by_pilot: Dict[str, List[int | None]] = {}
        rounds = heat.get("rounds")
        if isinstance(rounds, list):
            round_slot = 0
            for round_payload in rounds:
                if round_slot >= 5:
                    break
                if not isinstance(round_payload, dict):
                    continue
                round_rows = _extract_heat_rows(round_payload)
                round_map: Dict[str, int] = {}
                for round_row in round_rows:
                    if not isinstance(round_row, dict):
                        continue
                    round_nickname = _first_non_none(round_row, "callsign", "pilot", "pilot_name", "name")
                    if not round_nickname:
                        continue
                    place = parse_optional_int(_first_non_none(round_row, "position", "rank", "place"))
                    pts = _place_to_round_points(place)
                    if pts is None:
                        continue
                    nick_key = str(round_nickname).strip() or "Unknown"
                    round_map[nick_key] = pts
                if not round_map:
                    continue
                for nick_key, pts in round_map.items():
                    if nick_key not in round_points_by_pilot:
                        round_points_by_pilot[nick_key] = [None, None, None, None, None]
                    round_points_by_pilot[nick_key][round_slot] = pts
                round_slot += 1

        participants: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            nickname = (
                _first_non_none(row, "callsign", "pilot", "pilot_name", "name")
                or "Unknown"
            )
            points = _extract_points_list(row)
            p1 = parse_optional_int(_first_non_none(row, "points_r1", "r1"))
            p2 = parse_optional_int(_first_non_none(row, "points_r2", "r2"))
            p3 = parse_optional_int(_first_non_none(row, "points_r3", "r3"))
            p4 = parse_optional_int(_first_non_none(row, "points_r4", "r4"))
            p5 = parse_optional_int(_first_non_none(row, "points_r5", "r5"))
            final_position = parse_optional_int(_first_non_none(row, "position", "rank", "place"))
            total_points = parse_optional_float(_first_non_none(row, "total_points", "points_total"))
            round_points = round_points_by_pilot.get(str(nickname).strip() or "Unknown", [None, None, None, None, None])

            participants.append(
                {
                    "nickname": str(nickname).strip() or "Unknown",
                    "slot_index": parse_optional_int(row.get("slot")) or (len(participants) + 1),
                    "points_r1": p1 if p1 is not None else (points[0] if len(points) > 0 else round_points[0]),
                    "points_r2": p2 if p2 is not None else (points[1] if len(points) > 1 else round_points[1]),
                    "points_r3": p3 if p3 is not None else (points[2] if len(points) > 2 else round_points[2]),
                    "points_r4": p4 if p4 is not None else (points[3] if len(points) > 3 else round_points[3]),
                    "points_r5": p5 if p5 is not None else (points[4] if len(points) > 4 else round_points[4]),
                    "total_points": total_points,
                    "final_position": final_position,
                }
            )

        for p in participants:
            per_round = [p.get("points_r1"), p.get("points_r2"), p.get("points_r3"), p.get("points_r4"), p.get("points_r5")]
            numeric = [x for x in per_round if isinstance(x, (int, float))]
            computed_total = float(sum(numeric)) if numeric else None
            existing_total = p.get("total_points")
            if existing_total is None:
                p["total_points"] = computed_total
            elif existing_total == 0 and computed_total not in (None, 0):
                p["total_points"] = computed_total

        # Some RH exports omit rank/place fields for non-final heats; keep table usable.
        used_positions = {p["final_position"] for p in participants if p.get("final_position") is not None}
        next_pos = 1
        for p in participants:
            if p.get("final_position") is None:
                while next_pos in used_positions:
                    next_pos += 1
                p["final_position"] = next_pos
                used_positions.add(next_pos)

        if participants:
            race_payloads.append(
                {
                    "number": parsed_number,
                    "participants": participants,
                }
            )

    race_payloads.sort(key=lambda x: x["number"])
    for i, payload in enumerate(race_payloads, start=1):
        number = payload["number"]
        if number < 1 or number > 14:
            payload["number"] = i

    filtered = [p for p in race_payloads if 1 <= p["number"] <= 14]
    if not filtered:
        raise ValueError("No race rows extracted from RH finals JSON")
    return filtered


def import_finals_rows(db: Session, event_id: int, races_payload: List[Dict[str, Any]]) -> tuple[int, int]:
    ensure_event_bracket(db, event_id)

    races = db.scalars(
        select(BracketRace).where(BracketRace.event_id == event_id)
    ).all()
    race_by_number = {r.number: r for r in races}
    race_ids = [r.id for r in races]
    if race_ids:
        db.query(BracketRaceResult).filter(
            BracketRaceResult.bracket_race_id.in_(race_ids)
        ).delete(synchronize_session=False)

    imported_races = 0
    imported_results = 0
    for race_payload in races_payload:
        race = race_by_number.get(race_payload["number"])
        if not race:
            continue
        imported_races += 1
        for participant in race_payload["participants"][:4]:
            pilot = get_or_create_pilot(db, participant["nickname"])
            db.add(
                BracketRaceResult(
                    bracket_race_id=race.id,
                    pilot_id=pilot.id,
                    slot_index=parse_optional_int(participant.get("slot_index")) or (imported_results + 1),
                    points_r1=parse_optional_int(participant.get("points_r1")),
                    points_r2=parse_optional_int(participant.get("points_r2")),
                    points_r3=parse_optional_int(participant.get("points_r3")),
                    points_r4=parse_optional_int(participant.get("points_r4")),
                    points_r5=parse_optional_int(participant.get("points_r5")),
                    total_points=parse_optional_float(participant.get("total_points")),
                    final_position=parse_optional_int(participant.get("final_position")),
                )
            )
            imported_results += 1

    return imported_races, imported_results


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

    if poster and poster.filename:
        try:
            saved_name = await save_event_poster(event.id, poster)
            logger.info(
                "Poster saved on create for event_id=%s, filename=%s",
                event.id,
                saved_name,
            )
        except Exception:
            logger.exception(
                "Failed to save poster on create for event_id=%s, filename=%s",
                event.id,
                poster.filename,
            )
            raise HTTPException(status_code=400, detail="Ошибка загрузки афиши")

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
    logger.info(
        "Admin update started for event_id=%s, form_keys=%s",
        event_id,
        sorted(form.keys()),
    )

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

    poster_file = form.get("poster")
    if poster_file is not None and getattr(poster_file, "filename", ""):
        try:
            saved_name = await save_event_poster(event_id, poster_file)
            logger.info(
                "Poster saved for event_id=%s, filename=%s",
                event_id,
                saved_name,
            )
        except ValueError as exc:
            logger.warning(
                "Poster validation failed for event_id=%s, filename=%s: %s",
                event_id,
                poster_file.filename,
                exc,
            )
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            logger.exception(
                "Failed to save poster for event_id=%s, filename=%s",
                event_id,
                poster_file.filename,
            )
            raise HTTPException(status_code=500, detail="Ошибка загрузки афиши")

    rh_json_file = form.get("rh_json_file")
    json_uploaded = False
    if rh_json_file is not None and getattr(rh_json_file, "filename", ""):
        try:
            payload = await rh_json_file.read()
            rh_json = json.loads(payload.decode("utf-8"))
            rows = extract_qual_rows(rh_json)
            imported = import_qualification_rows(db, event_id=event_id, rows=rows)
            json_uploaded = True
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

    rh_finals_file = form.get("rh_finals_file")
    finals_uploaded = False
    if rh_finals_file is not None and getattr(rh_finals_file, "filename", ""):
        try:
            payload = await rh_finals_file.read()
            finals_json = json.loads(payload.decode("utf-8"))
            races_payload = extract_finals_races(finals_json)
            imported_races, imported_results = import_finals_rows(
                db,
                event_id=event_id,
                races_payload=races_payload,
            )
            finals_uploaded = True
            logger.info(
                "RH finals imported for event_id=%s, races=%s, results=%s, filename=%s",
                event_id,
                imported_races,
                imported_results,
                rh_finals_file.filename,
            )
        except json.JSONDecodeError:
            logger.warning(
                "Invalid finals JSON file uploaded for event_id=%s, filename=%s",
                event_id,
                rh_finals_file.filename,
            )
            raise HTTPException(status_code=400, detail="Некорректный JSON финалов")
        except ValueError as exc:
            logger.warning(
                "Unsupported finals JSON structure for event_id=%s, filename=%s: %s",
                event_id,
                rh_finals_file.filename,
                exc,
            )
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            logger.exception(
                "Failed to import RH finals for event_id=%s, filename=%s",
                event_id,
                rh_finals_file.filename,
            )
            raise HTTPException(status_code=500, detail="Ошибка импорта JSON финалов")

    qual_updated = 0
    qual_deleted = 0
    q_pattern = re.compile(r"^q_(\d+)_(rank|nickname|best3|bestlap|consec|laps|attempts|delete)$")
    q_ids_from_form = {
        int(match.group(1))
        for key in form.keys()
        for match in [q_pattern.match(key)]
        if match
    }

    if json_uploaded:
        logger.info(
            "Skipping manual qualification edits for event_id=%s because RH JSON was uploaded in same request",
            event_id,
        )
    elif q_ids_from_form:
        stmt = select(QualificationResult).where(
            QualificationResult.event_id == event_id,
            QualificationResult.id.in_(q_ids_from_form),
        )
        qualification_rows = db.scalars(stmt).all()

        for q in qualification_rows:
            prefix = f"q_{q.id}_"
            if form.get(f"{prefix}delete"):
                db.delete(q)
                qual_deleted += 1
                continue

            nickname = (form.get(f"{prefix}nickname") or "").strip()
            if nickname:
                q.pilot = get_or_create_pilot(db, nickname)

            q.rank = parse_optional_int(form.get(f"{prefix}rank"))
            q.best3_avg_ms = parse_optional_int(form.get(f"{prefix}best3"))
            q.best_lap_ms = parse_optional_int(form.get(f"{prefix}bestlap"))
            q.consecutives_count = parse_optional_int(form.get(f"{prefix}consec"))
            q.laps_total = parse_optional_int(form.get(f"{prefix}laps"))
            q.attempts_count = parse_optional_int(form.get(f"{prefix}attempts"))
            qual_updated += 1

    br_updated = 0
    br_pattern = re.compile(r"^br_(\d+)_(nickname|r1|r2|r3|r4|r5|total|pos|slot)$")
    br_ids_from_form = {
        int(match.group(1))
        for key in form.keys()
        for match in [br_pattern.match(key)]
        if match
    }

    if finals_uploaded:
        logger.info(
            "Skipping manual bracket edits for event_id=%s because finals JSON was uploaded in same request",
            event_id,
        )
    elif br_ids_from_form:
        bracket_rows = db.scalars(
            select(BracketRaceResult)
            .join(BracketRace, BracketRace.id == BracketRaceResult.bracket_race_id)
            .where(
                BracketRaceResult.id.in_(br_ids_from_form),
                BracketRace.event_id == event_id,
            )
        ).all()
        for row in bracket_rows:
            prefix = f"br_{row.id}_"
            nickname = (form.get(f"{prefix}nickname") or "").strip()
            if nickname:
                row.pilot = get_or_create_pilot(db, nickname)
            elif f"{prefix}nickname" in form:
                row.pilot_id = None

            row.points_r1 = parse_optional_int(form.get(f"{prefix}r1"))
            row.points_r2 = parse_optional_int(form.get(f"{prefix}r2"))
            row.points_r3 = parse_optional_int(form.get(f"{prefix}r3"))
            row.points_r4 = parse_optional_int(form.get(f"{prefix}r4"))
            row.points_r5 = parse_optional_int(form.get(f"{prefix}r5"))
            row.total_points = parse_optional_float(form.get(f"{prefix}total"))
            row.final_position = parse_optional_int(form.get(f"{prefix}pos"))
            row.slot_index = parse_optional_int(form.get(f"{prefix}slot")) or row.slot_index
            br_updated += 1

    db.commit()
    visible_qual_rows = db.scalar(
        select(func.count(QualificationResult.id)).where(
            QualificationResult.event_id == event_id,
            QualificationResult.rank.is_not(None),
        )
    )
    logger.info(
        "Admin update completed for event_id=%s, qual_updated=%s, qual_deleted=%s, br_updated=%s, visible_qual_rows=%s",
        event_id,
        qual_updated,
        qual_deleted,
        br_updated,
        visible_qual_rows,
    )

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

    for number, stage, short_label in BRACKET_CONFIG:
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
