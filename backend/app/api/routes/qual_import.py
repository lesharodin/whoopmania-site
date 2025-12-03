from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Any, Dict, List

from ...db import get_db
from ...models.event import Event
from ...models.pilot import Pilot
from ...models.qualification import QualificationResult

router = APIRouter(prefix="/qual", tags=["qualification"])


def get_or_create_pilot(db: Session, nickname: str) -> Pilot:
    stmt = select(Pilot).where(Pilot.nickname == nickname)
    p = db.scalar(stmt)
    if p:
        return p
    p = Pilot(nickname=nickname)
    db.add(p)
    db.flush()
    return p


@router.post("/import_rh/{event_id}", include_in_schema=True)
async def import_rh_qualification(event_id: int, rh_json: Dict[str, Any], db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Определяем, где таблица квалификации
    try:
        # Для твоего файла:
        qual_table: List[Dict[str, Any]] = rh_json["event_leaderboard"]["by_consecutives"]
    except KeyError:
        # Фоллбэк, если когда-нибудь попадётся другая структура
        try:
            qual_table = rh_json["leaderboard"]["by_consecutives"]
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail="No 'by_consecutives' leaderboard found in RH JSON (event_leaderboard/leaderboard)",
            )


    # Удаляем старую квалу при повторном импорте
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
        consecutives_count = row.get("consecutives_base")  # 3,2,1,0

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



    db.commit()
    return {"status": "ok", "imported": imported}
