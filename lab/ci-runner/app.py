import os
from datetime import datetime

import requests
from fastapi import FastAPI

app = FastAPI(title="ci-runner")

SECRETS_STORE_URL = os.getenv("SECRETS_STORE_URL", "http://secrets-store:8083")
CI_JOB_TOKEN = os.getenv("CI_JOB_TOKEN", "ci-logs-token")
JOB_LOGS: list[str] = []


@app.get("/")
def index() -> dict:
    return {"service": "ci-runner", "jobs": len(JOB_LOGS)}


@app.post("/run")
def run(payload: dict) -> dict:
    job = payload.get("job", "export")
    log_line = f"{datetime.utcnow().isoformat()} job={job} token={CI_JOB_TOKEN}"

    # Vulnerability primitive: secret/token leakage in logs.
    JOB_LOGS.append(log_line)

    if job == "export":
        response = requests.get(
            f"{SECRETS_STORE_URL}/secret",
            headers={"Authorization": f"Bearer {CI_JOB_TOKEN}"},
            timeout=3,
        )
        return {
            "job": job,
            "result": "export completed" if response.status_code == 200 else "export failed",
            "secret_status": response.status_code,
            "log_hint": "check /logs for troubleshooting",
        }

    return {"job": job, "result": "noop"}


@app.get("/logs")
def logs() -> dict:
    return {"logs": JOB_LOGS[-50:]}
