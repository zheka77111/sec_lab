import os

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="secrets-store")

STORE_TOKEN = os.getenv("STORE_TOKEN", "ci-logs-token")
FLAG = os.getenv("FLAG", "FLAG{training-chain-compromise}")


@app.get("/")
def index() -> dict:
    return {"service": "secrets-store"}


@app.get("/secret")
def get_secret(authorization: str | None = Header(default=None)) -> dict:
    if authorization != f"Bearer {STORE_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")
    return {"flag": FLAG}
