# Compatibility and upgrade rules

API version: `ng-api-2`. Protocol: `ng-1`. Ticket: `ng-ticket-1`.

## Matrix

| Combination | Expected | Evidence |
|---|---|---|
| old client → new server | accepted if version >= `minClientVersion`; `426 update_required` otherwise | `/api/version`, `_version_gate`, `tools/compat_check.py` |
| new client → old server | old server ignores unknown headers and task fields; no LoRA/NGD1/eval claims | fields are additive; `/api/version` is optional |
| legacy JSON state → SQLite | first boot imports users/dataset/global state; SQLite schema is created | `StateStore` migration + restart tests |
| absent `slowapi` | server boots with a no-op limiter | optional import; `_HAS_SLOWAPI` |
| absent peers | single-node health reports `quorum: 1`, `configured: false` | `/cluster/health` test |
| absent evaluator | rounds publish `eval: null` unless `CROWDGPT_REQUIRE_EVAL=1` | require-eval tests |
| rollback | stop the new server, restore the previous checkout and backup; old schema is not downgraded | backup/restore test, `tools/compat_check.py` |

## Upgrade procedure

1. Back up `/var/lib/crowdgpt` with `orchestrator_cli.py backup`.
2. Stop the old process.
3. Start the new process against the same DB. Migrations are additive; no table is dropped.
4. Check `/api/version`, `/cluster/health`, and `/metrics`.
5. If the round is unhealthy, stop, restore the backup, and start the previous checkout.

## Rules

- Additive fields are allowed within `ng-api-2`. Removing or renaming a field bumps the API version.
- A schema change requires a migration and a backup/restore drill before merge.
- Old clients must keep working until `minClientVersion` is raised deliberately.
