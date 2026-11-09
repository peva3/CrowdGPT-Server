# Ten-minute units and model tuning (P5)

## Delta compression (P5.1)

`orchestrator/compression.py` is a 1-bit codec with per-1024-param fp16 scale,
zlib framing, and `MAGIC=NGD1`. For 657,787,392 parameters the payload is
657,787,392/8 = **82.2 MB** of signs plus ~1.3 MB of scales, under the 200 MB
target. Tests measure a 131k-param round trip and a 1M-param size bound; a
production delta fixture has not been encoded yet.

## Short tickets and resume (P5.2)

- `CROWDGPT_TICKET_TTL_S` default **900 s**.
- `CROWDGPT_MAX_LOCAL_STEPS` default **500** (was up to 2500).
- `CROWDGPT_UNIT_CHECKPOINT_EVERY=<n>` (default 0) saves model/optimizer/
  counters; a user stop force-saves one, and a later unit with the same task id
  resumes it.
- The existing expiry path rejects `ticket_expired`; a consumed ticket returns
  `ticket_consumed`.

## LoRA track (P5.3, library only)

`orchestrator/lora.py`: rank-16 A/B adapters, `delta = B@A * alpha/rank`,
bf16 serialization, merge, and byte accounting. A 2560x1536 rank-16 adapter is
under 1 MB per matrix; a full-model adapter set is tens of MB. The client does
not yet train or submit LoRA adapters, so the 8–12 GB card claim is a design
target, not a measurement.

## Engram (P5.4, partial)

`CrowdV1.forward_features` now adds `self.engram(idx)`, so the 350M parameters
are live rather than inert. The 10% top-k sparse delta truncation remains; the
default is unchanged because changing it needs a held-out loss comparison that
has not been run. This is the main unresolved model-quality item.

## FLOP and data path (P5.5, code fixed, measurement pending)

- `_fwl` now uses `model.use_ckpt` instead of hardcoding `True`.
- Streamed blocks honor `use_ckpt` instead of always checkpointing.
- `StreamingShardDataset.get_batch` draws a permutation without replacement, so
  bs=1 no longer discards 7/8 of each window and bs=8 no longer oversamples.
- The cached streamed-parameter path avoids the duplicate H2D copy on
  checkpoint recompute.

No tokens/s before/after measurement was run in this session, so the FLOP gain
is not claimed as a measured number.

## DiLoCo (P5.6, library only)

`orchestrator/diloco.py`: Nesterov outer step, pseudogradient compression via
the 1-bit codec, bytes/token accounting. It is not wired into the server
aggregation loop yet, so no equal-token or equal-wall-clock comparison exists.
