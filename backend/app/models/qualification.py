# backend/app/models/qualification.py

from sqlalchemy import Integer, ForeignKey  
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .event import Event
from .pilot import Pilot


class QualificationResult(Base):
    __tablename__ = "qualification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pilot_id: Mapped[int] = mapped_column(
        ForeignKey("pilots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    best_lap_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best3_avg_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    laps_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ➕ вот это поле:
    consecutives_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    event: Mapped[Event] = relationship(backref="qualification_results")
    pilot: Mapped[Pilot] = relationship(backref="qualification_results")
