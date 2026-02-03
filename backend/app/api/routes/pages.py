# backend/app/api/routes/pages.py

from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models.event import Event

router = APIRouter()

templates = Jinja2Templates(directory="backend/app/templates")
from ...utils.formatting import format_ms
templates.env.filters["format_ms"] = format_ms



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
        },
    )
