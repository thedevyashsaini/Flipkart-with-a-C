from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import World

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORLD_PATH = Path(__file__).parent / "data" / "world.json"


def load_world() -> World:
    raw = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
    return World.model_validate(raw)

@app.get("/")
def root():
    return {"message": "Hello, FastAPI with uv!"}


@app.get("/world", response_model=World)
def world() -> World:
    return load_world()
