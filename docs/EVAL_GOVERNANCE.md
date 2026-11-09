# Held-out evaluation governance

Every adopted checkpoint should have a held-out result with a manifest hash.
`CROWDGPT_REQUIRE_EVAL=1` turns that from a convention into a gate: a round
without an evaluator is published as `{"status":"unevaluated","required":true}`
and its deltas are not applied.

## Manifest requirements

- SHA-256 of the evaluation manifest and every shard.
- Dataset source and license.
- Tokenizer revision and vocabulary size.
- Explicit contamination note: which training sources were checked, and which
  were not available for comparison.
- Fixed sampling seed and window count.

## Rotation

Keep a frozen public eval set for comparability and a separate rotating private
set for overfitting resistance. Publish both manifest hashes. A revision that
only improves the private set is not enough to claim progress.

## Limits

The local uniform fixture only proves the harness computes `ln(vocab)`. A real
checkpoint eval requires a trained checkpoint and is not claimed by unit tests.
