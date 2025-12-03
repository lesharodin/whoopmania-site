# backend/app/models/pilot.py

from datetime import datetime

from sqlalchemy import String, DateTime, Integer, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .bracket import BracketRaceResult   # <-- вот это ок, если в bracket.py нет прямого импорта Pilot

from ..db import Base


class Pilot(Base):
    __tablename__ = "pilots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    nickname: Mapped[str] = mapped_column(String, nullable=False, index=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)

    callsign: Mapped[str | None] = mapped_column(String, nullable=True)  # позывной
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    club: Mapped[str | None] = mapped_column(String, nullable=True)

    bracket_results = relationship(
        "BracketRaceResult",
        back_populates="pilot",
        cascade="all, delete-orphan",
    )

    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    def full_name(self) -> str:
        parts = [p for p in [self.first_name, self.last_name] if p]
        return " ".join(parts) if parts else self.nickname
