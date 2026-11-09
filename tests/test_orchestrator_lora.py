import numpy as np

from orchestrator.lora import (adapter_bytes, delta, deserialize, init_adapter,
                               merge, serialize)


def test_zero_init_has_no_delta_and_round_trips():
    a, b = init_adapter(64, 32, rank=16)
    assert np.allclose(delta(a, b), 0)
    a2, b2 = deserialize(serialize(a, b))
    assert np.allclose(a2, a, atol=1e-3) and np.allclose(b2, b)


def test_merge_and_size():
    a, b = init_adapter(2560, 1536, rank=16, seed=1)
    b[:] = 0.5
    base = np.zeros((2560, 1536), dtype="float32")
    out = merge(base, a, b)
    assert out.shape == base.shape
    assert adapter_bytes(2560, 1536) < 1_000_000
