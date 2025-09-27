from fastapi import FastAPI

api = FastAPI()

@api.get("/health")
def health():
    return {"ok": True}
