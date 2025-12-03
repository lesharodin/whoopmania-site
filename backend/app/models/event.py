# backend/app/models/event.py

from datetime import date
import enum

from sqlalchemy import String, Date, Enum, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .bracket import BracketRace

from ..db import Base


class EventType(str, enum.Enum):
    RACE = "race"
    TRAINING = "training"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType),
        nullable=False,
        default=EventType.RACE,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    bracket_races = relationship(
        "BracketRace",
        back_populates="event",
        cascade="all, delete-orphan",
    )    