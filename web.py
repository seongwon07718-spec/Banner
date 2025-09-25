python
from fastapi import FastAPI
from db import init_db

app = FastAPI()
init_db()

@app.get("/health")
def health():
    return {"ok": True}
