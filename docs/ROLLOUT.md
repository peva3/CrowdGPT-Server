# Client and node rollout

## Client

- `minClientVersion` is the hard gate; old clients receive `426 update_required`.
- New features are opt-in: `CROWDGPT_LORA=1`, `CROWDGPT_DELTA_CODEC=ngd1`,
  `CROWDGPT_FINGERPRINT=1`, `CROWDGPT_ORCHESTRATORS`.
- Roll out to a canary pool before raising the minimum. Watch rejected
  submissions and `update_required` counts.
- The installer must be tested from a clean home directory and must not require
  a pre-existing Git checkout.

## Orchestrator

1. Start a new node with an empty `/data` and run `operator_selfcheck.py`.
2. Join with a signed invite; confirm `/cluster/health` shows the peer.
3. Run one validation round; require a signed record and `/metrics`.
4. Add to the client seed list only after two consecutive healthy rounds.
5. A failed node is removed from the seed list; its signed records remain.

## Rollback

Stop the node, restore the last backup, restart the previous image or checkout,
and confirm `/api/version` and `/cluster/health`. Never delete signed records
during a rollback.
