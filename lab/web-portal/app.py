import os
import traceback

import requests
from fastapi import FastAPI

app = FastAPI(title="web-portal")

USERS = {
    "1": {"id": 1, "email": "alice@corp.local", "role": "user"},
    "2": {"id": 2, "email": "bob@corp.local", "role": "user"},
    "3": {"id": 3, "email": "admin@corp.local", "role": "admin"},
}

INTERNAL_ADMIN_URL = os.getenv("INTERNAL_ADMIN_URL", "http://internal-admin:8081")


@app.get("/")
def index() -> dict:
    return {
        "service": "web-portal",
        "note": "Training target. Do not expose outside isolated lab.",
    }


@app.get("/api/profile")
def profile(id: str) -> dict:
    # Vulnerability primitive: IDOR (no ownership/authorization check).
    user = USERS.get(id)
    if not user:
        return {"error": "not_found"}
    return user


@app.get("/api/error-debug")
def error_debug() -> dict:
    # Vulnerability primitive: information disclosure via verbose errors.
    try:
        _ = 1 / 0
    except Exception as exc:
        return {
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "internal_admin_url": INTERNAL_ADMIN_URL,
            "example_internal_header": "X-Forwarded-User: admin",
        }


@app.get("/api/admin/proxy-run")
def proxy_run(job: str = "export") -> dict:
    # Simulates a weak reverse proxy passing user-controlled trust header inside.
    response = requests.post(
        f"{INTERNAL_ADMIN_URL}/admin/run-job",
        headers={"X-Forwarded-User": "admin"},
        json={"job": job},
        timeout=3,
    )
    return {
        "status_code": response.status_code,
        "result": "internal job triggered",
        "log_hint": "check ci-runner /logs for troubleshooting",
    }
