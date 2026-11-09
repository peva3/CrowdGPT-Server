import numpy as np

from orchestrator.diloco import bytes_per_token, compress_pseudograd, decompress_pseudograd, outer_step


def test_outer_step_direction_and_momentum():
    p = np.array([1.0, 1.0], dtype="float32")
    g = np.array([0.5, -0.5], dtype="float32")
    out, buf = outer_step(p, g, lr=1.0, momentum=0.9, nesterov=True)
    assert out[0] < p[0] and out[1] > p[1]
    assert np.allclose(buf, 0.9 * g)


def test_pseudograd_round_trip_and_bytes_per_token():
    g = np.random.default_rng(1).standard_normal(65536).astype("float32")
    blob = compress_pseudograd(g)
    back = decompress_pseudograd(blob)
    assert back.shape == g.shape
    assert bytes_per_token(len(blob), 4_000_000) < 1.0
