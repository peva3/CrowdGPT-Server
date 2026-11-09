# Incident runbook

## Orchestrator down

1. Check `/api/cluster` from another node: is the lease expired?
2. Clients with `CROWDGPT_ORCHESTRATORS` fail over to the next URL.
3. The next leader is `sha256(prev_cert:round) mod sorted(healthy)`.
4. A round without a 2-of-3 certificate is published **uncertified** and must
   not be adopted as the new base.

## Split brain

Two leaders may publish branches. Keep both records. Consumers choose the
branch with a valid certificate and the higher evaluated checkpoint. Never
merge two branches into one aggregate.

## Object store outage

Uploads fail with `503`/timeout; no delta is recorded. Tickets remain valid
until expiry. The control plane stays up; do not restart just because the data
plane is down.

## Incorrect model output

Treat the last checkpoint as suspect, not the record. Roll back to the last
checkpoint with a held-out eval, mark the round `lossRejected`, and keep the
record. Do not delete it.
