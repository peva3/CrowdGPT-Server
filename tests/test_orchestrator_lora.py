import numpy as np
import pytest

from orchestrator.lora import (adapter_bytes, decode_payload, delta, deserialize,
                               encode_payload, expand_to_dense, init_adapter, merge,
                               payload_bytes, serialize)


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


def test_wire_round_trip_and_dense_expansion():
    a, b = init_adapter(16, 8, rank=4, seed=3)
    b[:] = 0.25
    blob = encode_payload([{"name": "blocks.0.attn.wq.weight", "offset": 4, "a": a, "b": b}])
    parsed = decode_payload(blob, model_size=140)
    assert parsed[0]["name"] == "blocks.0.attn.wq.weight"
    dense = expand_to_dense(parsed, 140)
    expected = (b @ a * (32.0 / 4)).reshape(-1).astype(np.float32)
    assert np.allclose(dense[4:4 + 128], expected, atol=2e-3)
    assert np.allclose(dense[:4], 0) and np.allclose(dense[132:], 0)


def test_wire_rejects_bad_span_overlap_and_truncation():
    a, b = init_adapter(8, 4, rank=2)
    good = {"name": "x", "offset": 0, "a": a, "b": b}
    with pytest.raises(ValueError):
        decode_payload(encode_payload([good]), model_size=8)
    overlapping = [good, {"name": "y", "offset": 4, "a": a, "b": b}]
    with pytest.raises(ValueError):
        decode_payload(encode_payload(overlapping), model_size=64)
    blob = encode_payload([good])
    with pytest.raises(ValueError):
        decode_payload(blob[:-3], model_size=64)


def test_full_model_adapter_payload_is_under_50mb():
    # 24 blocks x (4 attention + 3 MLP) rank-16 adapters on the 658M model.
    targets = [(1536, 1536)] * 4 + [(2560, 1536), (2560, 1536), (1536, 2560)]
    size = payload_bytes(targets * 24)
    assert 15_000_000 < size < 50_000_000
