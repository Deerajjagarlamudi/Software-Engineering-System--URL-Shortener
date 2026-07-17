# Ten-minute reviewer demo

1. Start with `make setup && make test`, then open `/console`.
2. Run the greenfield scenario and approve architecture, implementation, and release.
3. Inspect the branched DAG, generated files, artifact lineage, audit trail, and per-run metrics.
4. Run the ambiguous scenario; show the requirement artifact and assumptions before approving it.
5. Run the brownfield scenario; show the baseline regression test, expiry migration, and HTTP validation.
6. Run the chaos command with `security_review`; show the retry event and non-zero retry metric.
7. Replan a completed run with a reason and actor; show inactive superseded artifacts and renewed approvals.
8. Open `/api/v1/metrics` to show aggregate success, retries, rollbacks, MTTR, and latency.
