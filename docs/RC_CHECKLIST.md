# Release-candidate go/no-go

## Must pass locally

- [x] `make check` (server tests, ng tests, compile, selfcheck)
- [x] `python tools/sbom.py > sbom.json`
- [x] Docker image builds and the container reports `/cluster/health`
- [x] `GET /metrics` returns text/plain with no identities
- [x] backup → restore → verify drill
- [x] version gate: old client rejected, new client accepted
- [x] one simulated 3-node round with a killed leader
- [x] signed round record and `/audit` proof verify
- [x] S3 fake endpoint: PUT/HEAD/GET/DELETE, hash, download, GC, outage
- [x] durable peer outbox retries after a failed announcement
- [x] automatic witness collection attaches a quorum

## Blocked until a real deployment

- [ ] production 3-node RTO
- [ ] 30-day external pilot with three operators
- [ ] captured production delta codec measurement
- [ ] client onboarding by an unassisted newcomer

Do not claim a blocked item as complete. Record the date and the environment
for every measured row.
