# `krum_stream` decision

Status: **reference module**, not a pinned external package.

`krum_stream.py` is now present in the server repository, so an external
operator can import and run aggregation from the fork. It is correctness-first:
it reads flat float32 vectors and computes exact pairwise squared L2 distances.
It is not tuned for the 658M model.

## Benchmark

`tools/bench_krum.py` measured the reference implementation on this machine:

| params | delta bytes | pairwise time |
|---:|---:|---:|
| 1,000,000 | 25 MB | 0.0104 s |
| 8,000,000 | 137 MB | 0.0909 s |
| 32,000,000 | 344 MB | 0.4116 s |

The linear projection to 657,787,392 params is roughly **6.8–8.5 s** and
**6.7–15.7 GB** transient memory depending on allocator behaviour. That is a
projection from smaller runs, not a full-model measurement. A production
deployment should either accept the memory cost or provide a streaming
implementation behind the same interface.

## Rules

- Keep the public reference as the default so a new operator can run.
- Do not claim its throughput is production-tuned.
- If a faster implementation is adopted, keep the module name and the
  `open_delta` / `streaming_pairwise_sq_l2` / `stream_add_base_delta` /
  `read_engram_tail` interface stable.
