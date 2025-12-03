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
from ...utils.formatting import format_ms
from ...models.bracket import BracketRace, BracketRaceResult
# --- простая Basic-авторизация admin/admin ------------------------

security = HTTPBasic()


def admin_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, "admin")

    if not (correct_username and correct_password):
        # попросим браузер показать стандартное окошко логина/пароля
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(admin_auth)],  # защита всех /admin/*
)

templates = Jinja2Templates(directory="backend/app/templates")
templates.env.filters["format_ms"] = format_ms

# --- вспомогательные функции --------------------------------------


def get_or_create_pilot(db: Session, nickname: str) -> Pilot:
    stmt = select(Pilot).where(Pilot.nickname == nickname)
    p = db.scalar(stmt)
    if p:
        return p
    p = Pilot(nickname=nickname)
    db.add(p)
    db.flush()
    return p


def import_rh_qualification_for_event(
    db: Session, event_id: int, rh_json: Dict[str, Any]
) -> int:
    """Импорт квалы из RH JSON для события."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    try:
        qual_table: List[Dict[str, Any]] = rh_json["event_leaderboard"]["by_consecutives"]
    except KeyError:
        try:
            qual_table = rh_json["leaderboard"]["by_consecutives"]
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail="No 'by_consecutives' leaderboard found in RH JSON",
            )

    db.query(QualificationResult).filter(
        QualificationResult.event_id == event_id
    ).delete()

    imported = 0

    for row in qual_table:
        nickname = row.get("callsign") or "Unknown"
        rank = row.get("position")
        best3_avg = row.get("consecutives_raw")
        best_lap = row.get("fastest_lap_raw")
        laps = row.get("laps")
        attempts = row.get("starts")
        consecutives_count = row.get("consecutives_base")

        pilot = get_or_create_pilot(db, nickname)

        q = QualificationResult(
            event_id=event_id,
            pilot_id=pilot.id,
            rank=rank,
            best_lap_ms=int(best_lap) if best_lap is not None else None,
            best3_avg_ms=int(best3_avg) if best3_avg is not None else None,
            laps_total=laps,
            attempts_count=attempts,
            consecutives_count=consecutives_count,
        )
        db.add(q)
        imported += 1

    db.commit()
    return imported


# --- ручки админки -------------------------------------------------


@router.get("/", include_in_schema=False)
async def admin_index(request: Request, db: Session = Depends(get_db)):
    """Базовый экран админки: список событий."""
    stmt = select(Event).order_by(Event.date.desc())
    events = db.scalars(stmt).all()

    return templates.TemplateResponse(
        "admin_index.html",
        {"request": request, "events": events},
    )


@router.get("/events/new", include_in_schema=False)
async def admin_new_event_form(request: Request):
    """Форма создания нового события."""
    return templates.TemplateResponse(
        "admin_event_form.html",
        {"request": request},
    )


@router.post("/events/new", include_in_schema=False)
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
    # дата
    try:
        y, m, d = map(int, date_str.split("-"))
        event_date = date(y, m, d)
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректная дата (ожидается YYYY-MM-DD)")

    # тип события
    try:
        etype = EventType(event_type)
    except ValueError:
        etype = EventType.RACE

    # создаём событие
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

    # постер
    if poster and poster.filename:
        ext = os.path.splitext(poster.filename)[1].lower() or ".jpg"
        posters_dir = "backend/app/static/posters"
        os.makedirs(posters_dir, exist_ok=True)
        dest_path = os.path.join(posters_dir, f"event_{event.id}{ext}")

        data = await poster.read()
        with open(dest_path, "wb") as f:
            f.write(data)

    # RH JSON
    if rh_json_file and rh_json_file.filename:
        raw = await rh_json_file.read()
        try:
            rh_data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Не удалось прочитать RH JSON")

        import_rh_qualification_for_event(db, event.id, rh_data)

    return RedirectResponse(url=f"/events/{event.id}", status_code=303)


@router.get("/events/{event_id}/edit", include_in_schema=False)
async def admin_edit_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # грузим квалу для редактирования
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

def import_rh_bracket_for_event(db: Session, event_id: int, rh_data: dict):
    """
    Импортирует данные финалов из RH JSON в нашу сетку:
    - для каждой heat (1..14) ищет BracketRace с таким же номером;
    - считает очки 3-2-1-0 по вылетам (3 вылета, в финале 5);
    - записывает BracketRaceResult.
    """

    heats = rh_data.get("heats") or {}
    if not heats:
        raise HTTPException(status_code=400, detail="В RH JSON нет секции 'heats'")

    def compute_heat_points(heat: dict, heat_number: int):
        """Возвращает (индексы выбранных раундов, список результатов)."""
        rounds = heat.get("rounds") or []
        if not rounds:
            return [], []

        # сколько активных пилотов в каждом вылете
        active_counts = []
        for r in rounds:
            br = r["leaderboard"]["by_race_time"]
            active = [e for e in br if e.get("position") is not None]
            active_counts.append(len(active))

        max_active = max(active_counts) if active_counts else 0
        is_final = (heat_number == 14)
        desired_rounds = 5 if is_final else 3

        # берём первые N вылетов, где активных = максимум
        selected_indices = [
            i for i, c in enumerate(active_counts) if c == max_active
        ][:desired_rounds]

        by_pilot = {}  # pilot_id -> {name, round_points, positions}

        round_slot = 0
        for idx in selected_indices:
            round_slot += 1
            r = rounds[idx]
            for entry in r["leaderboard"]["by_race_time"]:
                pos = entry.get("position")
                if pos is None:
                    continue
                rh_pid = entry["pilot_id"]
                name = entry["callsign"]

                if rh_pid not in by_pilot:
                    by_pilot[rh_pid] = {
                        "name": name,
                        "round_points": {},
                        "positions": {},
                    }

                # очки 3-2-1-0 за 1-2-3-4 место
                if pos == 1:
                    pts = 3
                elif pos == 2:
                    pts = 2
                elif pos == 3:
                    pts = 1
                else:
                    pts = 0

                by_pilot[rh_pid]["round_points"][round_slot] = pts
                by_pilot[rh_pid]["positions"][round_slot] = pos

        results = []
        for rh_pid, info in by_pilot.items():
            total = sum(info["round_points"].values())
            results.append(
                (rh_pid, info["name"], info["round_points"], total, info["positions"])
            )

        # сортируем по сумме очков (по убыванию); при равенстве — по pilot_id
        results.sort(key=lambda x: (-x[3], x[0]))

        final = []
        for i, (rh_pid, name, round_points, total, positions) in enumerate(results, start=1):
            final.append(
                {
                    "final_position": i,
                    "rh_pilot_id": rh_pid,
                    "name": name,
                    "round_points": round_points,
                    "total_points": total,
                    "positions": positions,
                }
            )
        return selected_indices, final

    # идём по всем heat'ам
    for heat_key, heat in heats.items():
        heat_number = heat.get("heat_id") or int(heat_key)

        race = db.scalar(
            select(BracketRace).where(
                BracketRace.event_id == event_id,
                BracketRace.number == heat_number,
            )
        )
        if not race:
            # на всякий случай, если нет такой гонки в нашей сетке
            continue

        _, computed = compute_heat_points(heat, heat_number)

        # чистим старые результаты для этой гонки
        db.query(BracketRaceResult).filter(
            BracketRaceResult.bracket_race_id == race.id
        ).delete()

        slot_index = 0
        for res in computed:
            name = res["name"]
            total_points = res["total_points"]
            round_points = res["round_points"]
            final_position = res["final_position"]

            # ищем пилота по нику (callsign)
            pilot = db.scalar(select(Pilot).where(Pilot.nickname == name))
            if not pilot:
                pilot = Pilot(nickname=name)
                db.add(pilot)
                db.flush()

            slot_index += 1

            br_res = BracketRaceResult(
                bracket_race_id=race.id,
                pilot_id=pilot.id,
                slot_index=slot_index,
                points_r1=round_points.get(1),
                points_r2=round_points.get(2),
                points_r3=round_points.get(3),
                points_r4=round_points.get(4),
                points_r5=round_points.get(5),
                total_points=total_points,
                final_position=final_position,
            )
            db.add(br_res)

    db.commit()

@router.post("/events/{event_id}/edit", include_in_schema=False)
async def admin_update_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    from fastapi.responses import RedirectResponse
    import os
    import json
    from datetime import date as _date
    from sqlalchemy import select as _select

    from ...models.qualification import QualificationResult
    from ...models.pilot import Pilot
    from ...models.bracket import BracketRaceResult
    from ...models.event import EventType

    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    form = await request.form()

    # ---------- базовые поля события ----------
    name = form.get("name") or event.name
    date_str = form.get("date_str") or str(event.date)
    location = form.get("location") or None
    description = form.get("description") or None
    event_type = form.get("event_type") or "race"

    try:
        y, m, d = map(int, date_str.split("-"))
        event_date = _date(y, m, d)
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректная дата (ожидается YYYY-MM-DD)")

    try:
        etype = EventType(event_type)
    except ValueError:
        etype = EventType.RACE

    event.name = name
    event.date = event_date
    event.location = location
    event.description = description
    event.event_type = etype

    db.add(event)
    db.commit()
    db.refresh(event)

    # ---------- файлы ----------
    poster = form.get("poster")
    rh_json_file = form.get("rh_json_file")
    rh_finals_file = form.get("rh_finals_file")

    # афиша
    if poster is not None and getattr(poster, "filename", ""):
        ext = os.path.splitext(poster.filename)[1].lower() or ".jpg"
        posters_dir = "backend/app/static/posters"
        os.makedirs(posters_dir, exist_ok=True)
        dest_path = os.path.join(posters_dir, f"event_{event.id}{ext}")

        data_bytes = await poster.read()
        with open(dest_path, "wb") as f:
            f.write(data_bytes)

    # квалификация (by_consecutives)
    if rh_json_file is not None and getattr(rh_json_file, "filename", ""):
        raw = await rh_json_file.read()
        try:
            rh_data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Не удалось прочитать RH JSON (квалификация)")

        # здесь используй твой реальный путь к функции импорта квалификации
        from ...services.qualification_import import import_rh_qualification_for_event
        import_rh_qualification_for_event(db, event.id, rh_data)

    # финалы / сетка
    if rh_finals_file is not None and getattr(rh_finals_file, "filename", ""):
        raw = await rh_finals_file.read()
        try:
            rh_finals_data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Не удалось прочитать RH JSON (финалы)")

        from ...api.routes.admin import import_rh_bracket_for_event
        import_rh_bracket_for_event(db, event.id, rh_finals_data)

    # ---------- helper ----------
    def to_float_or_none(v: str | None) -> float | None:
        if v is None:
            return None
        v = v.strip().replace(",", ".")
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    def to_int_or_none(v: str | None) -> int | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None
        


    # ---------- обновление квалификации ----------
    updates: dict[int, dict[str, str]] = {}
    delete_ids: set[int] = set()

    for key, value in form.items():
        if not key.startswith("q_"):
            continue
        parts = key.split("_", 2)
        if len(parts) != 3:
            continue
        _, id_str, field = parts
        try:
            q_id = int(id_str)
        except ValueError:
            continue

        # флаг удаления
        if field == "delete":
            # чекбокс обычно даёт "on"
            if str(value).lower() in ("on", "1", "true", "yes"):
                delete_ids.add(q_id)
            continue

        updates.setdefault(q_id, {})[field] = value

    # сначала удаляем строки (и, при необходимости, пилотов)
    for q_id in delete_ids:
        q = db.get(QualificationResult, q_id)
        if not q:
            continue

        pilot = q.pilot
        db.delete(q)

        if pilot:
            # есть ли ещё квалы этого пилота?
            has_other_qual = db.scalar(
                _select(QualificationResult)
                .where(QualificationResult.pilot_id == pilot.id)
                .limit(1)
            )
            # есть ли результаты в сетке?
            has_bracket = db.scalar(
                _select(BracketRaceResult)
                .where(BracketRaceResult.pilot_id == pilot.id)
                .limit(1)
            )

            if not has_other_qual and not has_bracket:
                db.delete(pilot)

    # затем обновляем оставшиеся строки
    for q_id, fields in updates.items():
        if q_id in delete_ids:
            continue  # эту строку уже удалили

        q = db.get(QualificationResult, q_id)
        if not q:
            continue

        new_nick = (fields.get("nickname") or "").strip()
        if new_nick:
            if new_nick != q.pilot.nickname:
                stmt = _select(Pilot).where(Pilot.nickname == new_nick)
                existing_pilot = db.scalar(stmt)
                if existing_pilot:
                    q.pilot_id = existing_pilot.id
                else:
                    q.pilot.nickname = new_nick

        rank = to_int_or_none(fields.get("rank"))
        best3 = to_int_or_none(fields.get("best3"))
        bestlap = to_int_or_none(fields.get("bestlap"))
        laps = to_int_or_none(fields.get("laps"))
        attempts = to_int_or_none(fields.get("attempts"))
        consec = to_int_or_none(fields.get("consec"))

        if rank is not None:
            q.rank = rank
        q.best3_avg_ms = best3
        q.best_lap_ms = bestlap
        q.laps_total = laps
        q.attempts_count = attempts
        q.consecutives_count = consec

        db.add(q)


    # ---------- обновление сетки ----------
    # Ожидаем поля вида:
    # br_<id>_nickname
    # br_<id>_r1 .. br_<id>_r5
    # br_<id>_total
    # br_<id>_pos
    # br_<id>_slot

    br_updates: dict[int, dict[str, str]] = {}
    for key, value in form.items():
        if not key.startswith("br_"):
            continue
        parts = key.split("_", 2)
        if len(parts) != 3:
            continue
        _, rid_str, field = parts
        try:
            rid = int(rid_str)
        except ValueError:
            continue
        br_updates.setdefault(rid, {})[field] = value

    for rid, fields in br_updates.items():
        res = db.get(BracketRaceResult, rid)
        if not res:
            continue

        # ник → Pilot
        new_nick = (fields.get("nickname") or "").strip()
        if new_nick:
            stmt = _select(Pilot).where(Pilot.nickname == new_nick)
            pilot = db.scalar(stmt)
            if not pilot:
                pilot = Pilot(nickname=new_nick)
                db.add(pilot)
                db.flush()
            res.pilot_id = pilot.id

        # очки по вылетам
        r1 = to_int_or_none(fields.get("r1"))
        r2 = to_int_or_none(fields.get("r2"))
        r3 = to_int_or_none(fields.get("r3"))
        r4 = to_int_or_none(fields.get("r4"))
        r5 = to_int_or_none(fields.get("r5"))

        res.points_r1 = r1
        res.points_r2 = r2
        res.points_r3 = r3
        res.points_r4 = r4
        res.points_r5 = r5

        # сумма и место
        total = to_float_or_none(fields.get("total"))
        if total is not None:
            res.total_points = total

        pos_val = to_int_or_none(fields.get("pos"))
        if pos_val is not None:
            res.final_position = pos_val

        slot_val = to_int_or_none(fields.get("slot"))
        if slot_val is not None:
            res.slot_index = slot_val

        db.add(res)

    db.commit()

    return RedirectResponse(url=f"/events/{event.id}", status_code=303)


@router.post("/pilots/dedupe", include_in_schema=False)
def admin_dedupe_pilots(db: Session = Depends(get_db)):
    """
    Убираем дубли пилотов по никнейму:
    - оставляем пилота с минимальным id как основного;
    - все результаты квалы перекидываем на него;
    - дубли удаляем.
    """
    # грузим всех пилотов
    stmt = select(Pilot).order_by(Pilot.nickname, Pilot.id)
    pilots = db.scalars(stmt).all()

    groups: dict[str, list[Pilot]] = {}
    for p in pilots:
        key = (p.nickname or "").strip()
        if not key:
            continue
        groups.setdefault(key, []).append(p)

    merged = 0
    for nickname, plist in groups.items():
        if len(plist) <= 1:
            continue
        # канонический пилот
        canonical = min(plist, key=lambda p: p.id)
        duplicates = [p for p in plist if p.id != canonical.id]

        for dup in duplicates:
            # переносим все квалификационные результаты
            db.query(QualificationResult).filter(
                QualificationResult.pilot_id == dup.id
            ).update({"pilot_id": canonical.id})
            db.delete(dup)
            merged += 1

    db.commit()
    return {"status": "ok", "merged_pilots": merged}

@router.post("/events/{event_id}/create_bracket", include_in_schema=False)
async def admin_create_bracket(
    event_id: int,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Уже есть сетка – ничего не делаем
    existing = db.scalar(
        select(BracketRace).where(BracketRace.event_id == event_id)
    )
    if existing:
        return RedirectResponse(
            url=f"/admin/events/{event_id}/edit",
            status_code=303,
        )

    config = [
        (1,  "upper_1_16", "1/16"),
        (2,  "upper_1_16", "1/16"),
        (3,  "upper_1_16", "1/16"),
        (4,  "upper_1_16", "1/16"),

        (5,  "lower_1_16", "1/16"),
        (6,  "upper_1_8",  "1/8"),
        (7,  "lower_1_16", "1/16"),
        (8,  "upper_1_8",  "1/8"),

        (9,  "lower_1_8",  "1/8"),
        (10, "lower_1_8",  "1/8"),

        (11, "upper_1_4",  "1/4"),
        (12, "lower_1_4",  "1/4"),

        (13, "semi",       "Полуфинал"),
        (14, "final",      "Финал"),
    ]

    for number, stage, short_label in config:
        if "upper" in stage:
            side = "upper"
        elif "lower" in stage:
            side = "lower"
        else:
            side = "final"

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
        url=f"/admin/events/{event_id}/edit",
        status_code=303,
    )
# backend/app/api/routes/admin.py

@router.post("/dev/cleanup_orphan_pilots", include_in_schema=False)
def cleanup_orphan_pilots(db: Session = Depends(get_db)):
    from sqlalchemy import select

    orphans = []

    pilots = db.scalars(select(Pilot)).all()
    for p in pilots:
        has_qual = db.scalar(
            select(QualificationResult)
            .where(QualificationResult.pilot_id == p.id)
            .limit(1)
        )
        has_bracket = db.scalar(
            select(BracketRaceResult)
            .where(BracketRaceResult.pilot_id == p.id)
            .limit(1)
        )
        if not has_qual and not has_bracket:
            orphans.append(p)

    for p in orphans:
        db.delete(p)

    db.commit()
    return {"deleted_pilots": len(orphans)}
