# Auditor guide

## What to check

1. `GET /api/audit/chain` → `ok: true`, no broken rounds.
2. `GET /api/audit/rounds` → each record has a certificate, roots, and the
   previous record hash.
3. `GET /api/audit/proof?round=N` → `valid: true` and a root matching
   `/api/audit/checkpoint`.
4. `/audit` renders the same data as HTML.
5. A checkpoint's `rounds.jsonl` is mirrored to HF and to
   `CROWDGPT_AUDIT_MIRROR`.

## What is not proved

A signed record proves who published it and that it has not changed. It does
not prove the reported loss or that every delta was honest. Those require
fingerprint replay (`orchestrator/validation.py`) and held-out eval. Reported,
checked, and held-out numbers are separate fields and must never be summed.

## Inclusion proof

```bash
curl "$NODE/api/audit/proof?round=337"
```

`leaf` is the record hash; `proof` is the sibling path; `root` must equal the
checkpoint root for the same range.
