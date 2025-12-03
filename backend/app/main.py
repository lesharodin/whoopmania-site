# backend/app/main.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes import pages, events, pilots   # добавим pilots
from .api.routes import qual_import   # ← добавить импорт
from .api.routes import admin   # ← ДОБАВЬ
from .db import Base, engine

from . import models  # noqa: F401  # важно, чтобы модели подхватились


app = FastAPI(title="WhoopMania")

Base.metadata.create_all(bind=engine)

app.mount("/static", StaticFiles(directory="backend/app/static"), name="static")

app.include_router(pages.router)
app.include_router(events.router)
app.include_router(pilots.router)   # новый роутер
app.include_router(qual_import.router)   # ← подключить
app.include_router(admin.router)   # ← ДОБАВЬ