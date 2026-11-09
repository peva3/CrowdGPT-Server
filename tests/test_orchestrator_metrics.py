from orchestrator.metrics import MetricsRecorder, RoundMetric, scrub


def _metric(**kw):
    base = dict(schema="ng-metric-1", round=1, step=1, opened_at="a",
                closed_at="b", duration_s=1.0, tokens=10, deltas=2,
                accepted=2, rejected=0, bytes_down=100, bytes_up=50)
    base.update(kw)
    return RoundMetric(**base)


def test_metric_round_trip_and_summary(tmp_path):
    recorder = MetricsRecorder(tmp_path / "metrics.jsonl")
    recorder.append(_metric())
    recorder.append(_metric(round=2, step=2, tokens=20, certified=True))
    rows = list(recorder.read())
    assert [r["round"] for r in rows] == [1, 2]
    summary = recorder.summary()
    assert summary["rounds"] == 2
    assert summary["tokens"] == 30
    assert summary["certified"] == 1


def test_identity_and_secret_keys_are_dropped():
    scrubbed = scrub({"tokens": 1, "ip": "1.2.3.4", "secret": "s",
                      "nested": {"client_ip": "x", "ok": 2}})
    assert scrubbed == {"tokens": 1, "nested": {"ok": 2}}
    metric = _metric(client_versions=("3.2",), gpu_classes=("RTX 4090",))
    data = metric.to_dict()
    assert "ip" not in data and "secret" not in data
    assert data["gpu_classes"] == ("RTX 4090",)


def test_bad_values_fail_closed(tmp_path):
    recorder = MetricsRecorder(tmp_path / "m.jsonl")
    for bad in (_metric(schema="nope"), _metric(tokens=-1), _metric(bytes_down=-5)):
        try:
            recorder.append(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("bad metric accepted")
    assert list(recorder.read()) == []
