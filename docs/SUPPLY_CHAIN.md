# Supply chain and provenance

Date: 2026-11-09.

## Source provenance

- Client/model fork: `https://github.com/peva3/CrowdGPT`, branch `ng/main`.
- Base: `Vxtzq/CrowdGPT` at `b7d5b39`, plus the merged PR set recorded in
  `docs/NG_BASE.md`.
- Server base: `Vxtzq/CrowdGPT-Server` at
  `34f190bb722e2de55de7473b5f34f862c806cad6`.
- Server fork: `https://github.com/peva3/CrowdGPT-Server`, branch `main`,
  created 2026-11-09 and fast-forwarded from base
  `34f190bb722e2de55de7473b5f34f862c806cad6` to commit `f8e445e`.
  `origin` in the local checkout still points at `Vxtzq/CrowdGPT-Server`;
  `fork` points at `peva3/CrowdGPT-Server`.

## Runtime pins

`requirements.lock` records the versions reviewed on 2026-11-09. Install from
it, not from the loose list, for a reproducible operator image. Torch wheels
are platform-specific; pin the exact wheel index and version in the image.

## Artifact checksums

`tools/checksums.py write dist/*` writes `SHA256SUMS`; `verify` fails closed on
a missing file, a changed digest, or a malformed manifest. Publish `SHA256SUMS`
next to every release artifact. The model manifest (`Vxtzq/Crowd-v1`) is
separate and remains the authority for weights.

## Build steps

1. Check out the recorded commit.
2. Install `requirements.lock` into a fresh virtualenv.
3. Run `pytest` in the ng and server trees.
4. Build the image/tarball from the clean tree; do not include
   `checkpoints/`, `data/`, `pending_uploads/`, `pending_deltas/`, or
   `orchestrator_state.sqlite3`.
5. Write `SHA256SUMS` and sign the release with the project key if the build is
   a public release.

## What is not covered

- No SBOM generator is wired yet; `pip list --format=freeze` is the current
  inventory.
- No reproducible-build check yet. Two builds are assumed equivalent if they
  use the same lock file and base image, which is weaker than bit-for-bit
  reproduction.
- The private `krum_stream` module is not in the public server repo; a public
  server build needs its own implementation or an explicit dependency before
  external operators can run it.
