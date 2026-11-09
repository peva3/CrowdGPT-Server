# Observability

Date: 2026-11-09.

## Rules

1. **Reported is not verified.** A client-reported loss, token count, or step
   count is written with a `reported` label until a receipt or replay checks
   it.
2. **No IPs, tokens, or keys in metrics.** `orchestrator/metrics.py` drops
   denied keys (`ip`, `client_ip`, `x_forwarded_for`, `authorization`,
   `secret`, `token`, `api_key`, `password`, `cookie`) recursively.
3. **One row per round.** `RoundMetric` records round, step, open/close times,
   duration, tokens, deltas, accepted/rejected, bytes down/up, optional
   held-out eval, client versions, GPU classes, and certification.
4. **Append-only.** The metrics file is JSONL; never rewrite a row. A corrected
   value is a new row with a later timestamp.
5. **Wired at round close.** `_publish_round_record()` appends one
   `RoundMetric` to `data/round_metrics.jsonl` after publishing the signed
   record. `certified` stays false until witness quorum is assembled; the
   leader signature alone is not counted.

## Round record fields

| field | meaning |
|---|---|
| `round`, `step` | round id and global step |
| `opened_at`, `closed_at`, `duration_s` | wall clock |
| `tokens`, `deltas` | accepted work |
| `accepted`, `rejected` | submission outcomes |
| `bytes_down`, `bytes_up` | data-plane bytes |
| `eval_loss`, `eval_n` | optional held-out result |
| `client_versions`, `gpu_classes` | coarse distribution, no identities |
| `certified` | a valid round certificate exists |

## Dashboards to derive

- liveness: rounds/day, tokens/day (7-day trailing), round duration p50/p95
- quality: held-out loss per revision with CI, eval coverage
- contribution: verified tokens, streaks, bytes per accepted token
- health: pool fill, abandoned pools, flags per 1k rounds, duplicate devices
- distribution: top-1/3/10 share, HHI, ASN/country spread (aggregate only)
- cost: GPU-hours, FLOPs, energy if measured

## What not to log

Prompt text, model output, user names paired with IPs, raw headers, presigned
URLs, admin secrets, client keys. Public logs are sanitized separately by
`public_log.py`.
