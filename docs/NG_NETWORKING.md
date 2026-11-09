# Orchestrator networking and reachability

Date: 2026-11-09.

This document is the operator-facing contract for how an orchestrator node is
reached. It describes the code as shipped, not an aspiration.

## Two different connection paths

### 1. Trainer client → orchestrator

The client makes **outbound HTTPS** requests only:

- `GET /fl/round_status`
- `GET /fl/task` (metadata or streamed weights)
- `POST /fl/submit/init`, `/fl/submit/chunk`, `/fl/submit/finalize`
- `GET /fl/heartbeat`
- optionally `PUT /fl/object/{key}` when the local object backend is used

The orchestrator never connects back to the trainer. **Trainers do not need
port forwarding, a public IP, or inbound firewall rules.**

Multiple orchestrator URLs are configured with `--server` plus
`CROWDGPT_ORCHESTRATORS` (comma-separated). GET, metadata, streamed task, and
heartbeat requests rotate on connection failure. An HTTP refusal is not treated
as a transport failure.

### 2. Orchestrator ↔ orchestrator

The peer protocol is HTTP JSON with a static peer list:

```json
[
  {"node_id": "node-abc", "endpoint": "https://a.example", "pubkey": "..."},
  {"node_id": "node-def", "endpoint": "https://b.example", "pubkey": "..."}
]
```

Routes under `/cluster/*` (formerly `/sim/*` in the drill harness):

- `POST /cluster/lease/ack` — acknowledge a signed lease.
- `GET  /cluster/lease/current` — inspect the local lease view.
- `POST /cluster/registry/announce` — announce a signed `NodeRecord`.
- `POST /cluster/ticket/announce` — replicate a signed ticket and its task.
- `POST /cluster/round/announce` — replicate an open-round snapshot.
- `POST /cluster/submission/announce` — announce an accepted content hash.
- `POST /cluster/witness/sign` — co-sign a round record.
- `GET  /cluster/health` — liveness, node id, peers, quorum.

A lease is valid only after the configured quorum acknowledges the same
`(round, leader, term, expiresAt)` payload. Peer requests carry a timestamp and
nonce; the receiver rejects stale timestamps and reused nonces.

**Every orchestrator needs a reachable endpoint** for clients and peers:
a public IP with port forwarding, a VPS behind Caddy/Nginx, a private mesh
(WireGuard/Tailscale), or a tunnel (Cloudflare Tunnel). There is no NAT
traversal, STUN/TURN, relay, UPnP, DNS seed, or hole punching in this build.
If all nodes are behind NAT without a tunnel or mesh, peer replication cannot
connect.

## Supported deployment shapes

### Public VPS with reverse proxy (recommended for a real quorum)

```text
client ──HTTPS──> Caddy/Nginx ──HTTP──> orchestrator:5006
peer   ──HTTPS──> Caddy/Nginx ──HTTP──> orchestrator:5006
```

Set `CROWDGPT_TRUSTED_PROXIES` to the proxy address range so per-IP limits and
bans see the real client. Use a DNS name and a valid certificate.

### Private mesh

Put all orchestrators on Tailscale or WireGuard and use private endpoints in
the peer list. Clients still need a reachable HTTPS endpoint; expose one node
or run a small public gateway.

### Tunnel

Cloudflare Tunnel or an equivalent outbound tunnel can expose a home/VPS node.
The tunnel must expose both the client routes and `/cluster/*`.

### Local multi-process simulation

`tools/sim_cluster.py` starts three loopback nodes on distinct ports. This is
for drills only; it does not prove public reachability.

## TLS and peer authentication

`server.py` serves plain HTTP. TLS is provided by the reverse proxy or tunnel.
Do not expose port 5006 directly to the public internet.

Peer payloads are signed, but signatures do not encrypt traffic. For a public
quorum:

- terminate TLS at the proxy and deny plain HTTP;
- keep the peer list and public keys in a root-owned file;
- set `CROWDGPT_PEER_TLS_VERIFY=1` when peers use HTTPS;
- the `/cluster/*` receiver rejects stale timestamps and replayed nonces.

mTLS is not implemented. If the operators do not trust the network path, use a
private mesh or put the nodes behind a VPN.

## Join flow

`crowdgpt-orchestrator init` creates the state directory, node key, and empty
peer list.

`crowdgpt-orchestrator invite` creates a signed `ng-join-1` token containing
the issuer endpoint, node id, public key, roles, issue time, expiry, and nonce.

`crowdgpt-orchestrator join` verifies the token against a trusted public key,
writes the peer entry, optionally probes `/cluster/health`, and prints the
next command. A self-signed token is not trusted; the operator must supply the
expected issuer key out of band.

## What is not implemented

- Hole punching, STUN/TURN, relay, UPnP, libp2p, Iroh, WebRTC.
- DNS-based peer discovery or a public seed service.
- mTLS.
- Full pool/delta handoff on a peer that did not issue the ticket; ticket,
  task, and round snapshots are replicated, but a live training pool is still
  local to the node that created it.

For permissionless home operators, the shortest path is a tunnel or mesh. The
long-term path is a NAT-traversal transport with relays, not a hand-rolled
STUN client.
