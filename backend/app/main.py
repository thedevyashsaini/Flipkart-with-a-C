# uv run uvicorn app.main:app --reload
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Hello, FastAPI with uv!"}