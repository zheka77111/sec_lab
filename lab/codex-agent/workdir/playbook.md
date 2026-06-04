# RALPH Loop Playbook

RALPH is a bounded vulnerability-discovery loop for this training lab.

Stages:

1. Recall: read scope, task, prior state, findings, failures, and lessons.
2. Analyze: build the current map of services, endpoints, credentials, and trust assumptions.
3. List Hypotheses: choose a small set of likely next checks.
4. Probe: run safe HTTP checks within the allowlist.
5. Harvest: store raw logs, classify outcomes, update memory, and select the next step.

Expected hypothesis families:

- Debug information disclosure.
- Object-level authorization gap.
- Spoofable internal trust boundary.
- Over-privileged CI path.
- Secret leakage in logs.
- Token reuse against secret storage.

Output contract:

- `memory/state.json`: current structured memory.
- `memory/hypotheses.jsonl`: hypotheses selected per iteration.
- `memory/findings.jsonl`: confirmed findings only.
- `memory/failures.jsonl`: failed or inconclusive checks.
- `memory/lessons.md`: human-readable run summary.
- `logs/requests.jsonl`: outbound request metadata.
- `logs/responses.jsonl`: response metadata and truncated bodies.
- `logs/iterations.jsonl`: iteration-level summaries.
