# backend/app/models/bracket.py
from sqlalchemy import Column, Integer, String, ForeignKey, Float
from sqlalchemy.orm import relationship

from ..db import Base


class BracketRace(Base):
    __tablename__ = "bracket_races"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )

    number = Column(Integer, nullable=False)
    name = Column(String(50), nullable=False)
    short_label = Column(String(20), nullable=False)
    stage = Column(String(50), nullable=False)        # upper_1_16, lower_1_8, semi, final ...
    bracket_side = Column(String(10), nullable=False) # upper / lower / final

    event = relationship("Event", back_populates="bracket_races")
    results = relationship(
        "BracketRaceResult",
        back_populates="race",
        cascade="all, delete-orphan",
    )


class BracketRaceResult(Base):
    """
    Результат пилота в конкретной гонке сетки.
    Храним очки по вылетам (до 5), сумму и финальное место.
    """
    __tablename__ = "bracket_race_results"

    id = Column(Integer, primary_key=True)
    bracket_race_id = Column(
        Integer,
        ForeignKey("bracket_races.id", ondelete="CASCADE"),
        nullable=False,
    )
    pilot_id = Column(
        Integer,
        ForeignKey("pilots.id", ondelete="SET NULL"),
        nullable=True,
    )

    # просто порядковый номер строки (1..4), чтобы фиксировать исходное положение
    slot_index = Column(Integer, nullable=True)

    points_r1 = Column(Integer, nullable=True)
    points_r2 = Column(Integer, nullable=True)
    points_r3 = Column(Integer, nullable=True)
    points_r4 = Column(Integer, nullable=True)
    points_r5 = Column(Integer, nullable=True)

    total_points = Column(Float, nullable=True)
    final_position = Column(Integer, nullable=True)

    race = relationship("BracketRace", back_populates="results")
    pilot = relationship("Pilot", back_populates="bracket_results")
