# backend/app/api/routes/events.py

from datetime import date

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models.event import Event, EventType
from ...models.qualification import QualificationResult

router = APIRouter(prefix="/events", tags=["events"])

templates = Jinja2Templates(directory="backend/app/templates")
from ...utils.formatting import format_ms
from ...utils.jinja_filters import format_float_clean
templates.env.filters["float_clean"] = format_float_clean
templates.env.filters["format_ms"] = format_ms


@router.get("/dev/create_sample", include_in_schema=False)
def create_sample_events(db: Session = Depends(get_db)):
    """Создаёт пару тестовых событий, если БД пустая."""
    existing = db.scalar(select(Event).limit(1))
    if existing:
        return {"status": "already_has_events"}

    e1 = Event(
        name="WhoopMania #1",
        event_type=EventType.RACE,
        date=date(2025, 1, 15),
        location="Москва, WhoopClub",
        description="Первая гонка сезона, тестируем формат.",
    )
    e2 = Event(
        name="WhoopMania #2",
        event_type=EventType.RACE,
        date=date(2025, 2, 10),
        location="Москва, WhoopClub",
        description="Топ-16 double elim, первая официальная сетка.",
    )
    db.add_all([e1, e2])
    db.commit()
    return {"status": "created", "count": 2}


@router.get("/", include_in_schema=False)
async def events_list(
    request: Request,
    db: Session = Depends(get_db),
):
    stmt = select(Event).order_by(Event.date.desc())
    events = db.scalars(stmt).all()

    return templates.TemplateResponse(
        "events_list.html",
        {"request": request, "events": events},
    )


@router.get("/{event_id}", include_in_schema=False)
async def event_detail(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Квалификация в порядке, как её посчитал RH (rank)
    stmt = (
        select(QualificationResult)
        .where(QualificationResult.event_id == event_id)
        .order_by(QualificationResult.rank.asc())
    )
    qual_results = db.scalars(stmt).all()

    return templates.TemplateResponse(
        "event_detail.html",
        {
            "request": request,
            "event": event,
            "qualification": qual_results,
        },
    )
