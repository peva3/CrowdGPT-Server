import numpy as np

from orchestrator.compression import compress_delta, compression_ratio, decompress_delta


def test_round_trip_and_error_bound():
    rng = np.random.default_rng(7)
    values = rng.standard_normal(131_072).astype("float32")
    blob = compress_delta(values)
    back = decompress_delta(blob)
    assert back.shape == values.shape
    err = np.abs(back - values)
    mean_abs = np.mean(np.abs(values))
    assert np.mean(err) < 1.2 * mean_abs
    assert np.all(np.sign(back) == np.sign(values))


def test_size_target_for_model_scale():
    values = np.zeros(1_000_000, dtype="float32")
    blob = compress_delta(values)
    # 1-bit payload + scales + framing; zeros compress well
    assert len(blob) < 200_000
    assert compression_ratio(values.nbytes, blob) > 10


def test_exact_round_trip_for_zero():
    values = np.zeros(2048, dtype="float32")
    assert np.array_equal(decompress_delta(compress_delta(values)), values)


def test_framed_container_round_trip_and_errors():
    from orchestrator.compression import iter_frames, pack_frames, unpack_frames
    import pytest

    blocks = [np.full(64, 0.25, dtype=np.float32), np.zeros(32, dtype=np.float32)]
    blob = pack_frames(blocks)
    assert blob.startswith(b"NGD2")
    back = unpack_frames(blob)
    assert back.size == 96
    assert np.allclose(back[:64], 0.25)
    assert np.all(back[64:] == 0)
    with pytest.raises(ValueError):
        list(iter_frames(blob[:-5]))
    with pytest.raises(ValueError):
        list(iter_frames(b"bad"))
