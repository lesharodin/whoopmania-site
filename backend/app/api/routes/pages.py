# backend/app/api/routes/pages.py

import time
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models.event import Event

router = APIRouter()

templates = Jinja2Templates(directory="backend/app/templates")
from ...utils.formatting import format_ms
templates.env.filters["format_ms"] = format_ms


@router.get("/posters/event_{event_id}", include_in_schema=False, name="event_poster")
async def event_poster(event_id: int):
    posters_dir = Path(__file__).resolve().parents[2] / "static" / "posters"
    for ext in (".jpg", ".png"):
        candidate = posters_dir / f"event_{event_id}{ext}"
        if candidate.exists():
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="Poster not found")


@router.get("/", include_in_schema=False, name="index")
async def index(
    request: Request,
    db: Session = Depends(get_db),
):
    # последние N событий
    stmt = select(Event).order_by(Event.date.desc()).limit(5)
    events = db.scalars(stmt).all()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "events": events,
            "cache_bust": int(time.time()),
        },
    )
