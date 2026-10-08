from app.ai import gemini


def test_repeated_quota_failures_back_off_even_after_pause_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(gemini.time, 'monotonic', lambda: clock[0])
    key = ('credential-hash', 'model-a', False)
    for expected in [120, 240, 480, 960, 1800, 1800]:
        gemini._pause_model(key, 'model-a: HTTP 429')
        assert gemini._model_pauses[key] == clock[0] + expected
        clock[0] += expected + 1


def test_failed_model_does_not_pause_other_models_or_keys(monkeypatch):
    monkeypatch.setattr(gemini.time, 'monotonic', lambda: 1000.0)
    key = ('key-a', 'model-a', False)
    gemini._pause_model(key, 'model-a: HTTP 503')
    assert gemini._model_pauses[key] == 1030
    assert ('key-b', 'model-a', False) not in gemini._model_pauses
    assert ('key-a', 'model-b', False) not in gemini._model_pauses


def test_old_failure_history_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(gemini.time, 'monotonic', lambda: clock[0])
    key = ('key', 'model', False)
    gemini._pause_model(key, 'HTTP 429')
    clock[0] += 3601
    gemini._pause_model(key, 'HTTP 429')
    assert gemini._model_pauses[key] == clock[0] + 120
