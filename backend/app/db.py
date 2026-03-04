# backend/app/db.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from pathlib import Path

DB_FILE_PATH = (Path(__file__).resolve().parents[2] / "whoopmania.db").resolve()
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_FILE_PATH}"

# Для SQLite в однопоточной FastAPI нужно отключить check_same_thread
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Базовый класс для моделей SQLAlchemy."""
    pass


def get_db():
    """Зависимость FastAPI для получения сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
