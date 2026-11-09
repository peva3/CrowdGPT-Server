# SBOM and release provenance

`python tools/sbom.py > sbom.json` writes a CycloneDX-style inventory of the
installed Python distributions. It is generated from the environment that runs
the tests; do not commit it if it contains host-specific packages.

For a release:

1. Build the image from the recorded commit and `requirements.lock`.
2. Run `make check`.
3. Run `python tools/sbom.py > sbom.json` and attach it to the release.
4. Sign the image digest with the project key (`cosign` or equivalent).
5. Publish the commit, lock file, SBOM, image digest, and signature together.

The repository cannot prove bit-for-bit reproducible builds yet. The lock file
and image digest are the current provenance boundary.
