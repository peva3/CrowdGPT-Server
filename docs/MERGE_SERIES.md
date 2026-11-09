# Merge series

The fork is one history for review convenience; upstream should receive it as
small, testable changes in this order. Never merge the fork as one commit.

1. `ng-1` protocol objects, canonical JSON, Merkle roots, Ed25519 envelopes.
2. Legacy round exporter and held-out eval harness.
3. Durable `orchestrator/` state, admission, scheduling, submission, verification, publication.
4. Signed tickets and idempotent finalize.
5. Presigned object uploads and SHA-256 verification.
6. Signed round records, receipts, fingerprints, audit page.
7. Peer registry, gossip, leases, quorum primitives.
8. Permissionless admission, reputation, bonds, runbooks.
9. Docker/compose/systemd, operator CLI, `/operator`, `/metrics`, backup timer.
10. LoRA, NGD1 codec, DiLoCo, engram delta policy.
11. Compatibility, security, rollout, and RC documentation.

Each PR must pass `make check` and include a migration/rollback note if it
touches state.
