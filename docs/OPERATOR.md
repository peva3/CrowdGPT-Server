# Operator guide

## What a node is

An orchestrator issues tickets, tracks pools, accepts submissions, verifies
receipts, aggregates deltas, and publishes a signed round record. Bulk weights
and deltas live in object storage (or Hugging Face); the orchestrator handles
control-plane metadata only.

## Requirements

- Linux, Python 3.12, 2 vCPU / 4 GB RAM minimum for the control plane.
- A writable data directory; default is beside `server.py`.
- Optional S3/R2 credentials for the data plane. Without them, the local
  presigned object endpoint is used (`/fl/object`).

## First run

```bash
python -m pip install -r requirements.txt   # fastapi, uvicorn, torch, cryptography, requests
export CROWDGPT_ADMIN_SECRET='change-me'
export CROWDGPT_PUBLIC_URL='https://node.example'
export CROWDGPT_ORCHESTRATOR_DB=/var/lib/crowdgpt/state.sqlite3
export CROWDGPT_OBJECT_DIR=/var/lib/crowdgpt/objects
python tools/operator_selfcheck.py
python server.py
```

`tools/operator_selfcheck.py` must print `RESULT: ready`.

The first boot creates `orchestrator_node_key.pem` (0600). Back it up. Losing
it means a new node id; do not share it.

## Register and rotate

Self-registration writes the local `NodeRecord`. To add a remote operator
(admin secret required):

```bash
curl -X POST "$NODE/api/admin/node/register?secret=$SECRET" \
  -H 'content-type: application/json' \
  -d '{"record": {...}, "signature": "<hex>"}'
```

The signature is over canonical JSON of the record. A bad signature returns
`400 node_signature_invalid`. Rotation keeps the old key valid for
`overlap_s` (default 3600 s), then it stops resolving.

## Admission policy

`CROWDGPT_ADMISSION_POLICY=open|bonded|vetted` (default `open`).
`CROWDGPT_BOND_ROUNDS` (default 3) and `CROWDGPT_MIN_NODE_VERSION` (default
3.2). Changing the environment and restarting changes admission with no code
change. `bonded` requires certified rounds; `vetted` requires an explicit
voucher from an existing operator.

## Health

- `GET /api/cluster` — nodes, healthy set, lease.
- `GET /api/node` — active node records.
- `GET /api/audit/chain` — `{ok, rounds, broken}`.
- `GET /fl/round_status` — current round, pools, control-plane bytes.

## Backup

Back up `orchestrator_state.sqlite3` (WAL + `-wal`/`-shm`), the node key, and
`data/public_rounds.jsonl`. The model checkpoint is reproducible from HF; the
signed records are not.
