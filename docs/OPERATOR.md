# Operator guide

## What a node is

An orchestrator issues tickets, tracks pools, accepts submissions, verifies
receipts, aggregates deltas, and publishes a signed round record. Bulk weights
and deltas live in object storage (or Hugging Face); the orchestrator handles
control-plane metadata only.

## Requirements

- Linux, Python 3.12, 2 vCPU minimum.
- **RAM:** at least 8 GB for aggregation; 16 GB is comfortable. The 658M
  float32 base plus 350M engram is 4.03 GB before copies, and a measured
  aggregation pass peaked at 4.16 GB. The old 4 GB figure is too low.
- A writable data directory; default is beside `server.py`.
- Optional S3/R2 credentials for the data plane. Without them, the local
  presigned object endpoint is used (`/fl/object`).
- A reachable endpoint for clients and peers: public IP, reverse proxy,
  tunnel, or private mesh. Trainers need only outbound HTTPS; orchestrators
  need inbound. There is no NAT traversal. See `docs/NG_NETWORKING.md`.

## First run

```bash
python -m pip install -r requirements.lock
export CROWDGPT_ADMIN_SECRET='change-me'
export CROWDGPT_PUBLIC_URL='https://node.example'
export CROWDGPT_ORCHESTRATOR_DB=/var/lib/crowdgpt/state.sqlite3
export CROWDGPT_OBJECT_DIR=/var/lib/crowdgpt/objects
export CROWDGPT_PEERS_FILE=/var/lib/crowdgpt/peers.json
python tools/operator_selfcheck.py
python tools/orchestrator_cli.py doctor --dir /var/lib/crowdgpt
python server.py
```

`tools/operator_selfcheck.py` must print `RESULT: ready`.

The first boot creates `orchestrator_node_key.pem` (0600). Back it up. Losing
it means a new node id; do not share it.

## Docker and systemd

`docker compose up -d --build` uses `Dockerfile` and `docker-compose.yml` with
a `/data` volume. `deploy/crowdgpt-orchestrator.service` is the non-Docker unit.
Full instructions: `docs/DEPLOYMENT.md`.

## Peer networking

Set `CROWDGPT_PEERS` or `CROWDGPT_PEERS_FILE` to the static peer list. A lease
is valid only after `CROWDGPT_PEER_QUORUM` peers acknowledge it; the default is
a majority. `/cluster/health` reports the local view. Tickets and open-round
snapshots are replicated, but a live training pool is still local to the node
that created it.

There is no hole punching, STUN/TURN, relay, or DNS discovery. If a node is
behind NAT without a tunnel or mesh, peers cannot connect. See
`docs/NG_NETWORKING.md`.

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
