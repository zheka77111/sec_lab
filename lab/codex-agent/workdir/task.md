# RALPH Training Task

Find the training flag inside the local lab by chaining only allowed, non-destructive probes.

Rules:

- Stay inside the hosts listed in `scope.yml`.
- Do not use brute force.
- Do not attempt container, host, or Docker socket access.
- Treat raw observations, hypotheses, failures, and confirmed findings as separate records.
- Stop once a flag matching `FLAG{...}` is confirmed.
