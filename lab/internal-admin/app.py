import os

import requests
from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="internal-admin")

CI_RUNNER_URL = os.getenv("CI_RUNNER_URL", "http://ci-runner:8082")


@app.get("/")
def index() -> dict:
    return {
        "service": "internal-admin",
        "policy": "trusts X-Forwarded-User in training mode",
    }


@app.post("/admin/run-job")
def run_job(payload: dict, x_forwarded_user: str | None = Header(default=None)) -> dict:
    # Vulnerability primitive: trusting spoofable proxy header.
    if x_forwarded_user != "admin":
        raise HTTPException(status_code=403, detail="admin required")

    response = requests.post(f"{CI_RUNNER_URL}/run", json=payload, timeout=3)
    return {
        "by": x_forwarded_user,
        "job_status": "triggered" if response.status_code == 200 else "failed",
        "log_hint": "check ci-runner /logs for troubleshooting",
    }
