from app.api import server


def test_watchdog_change_invalidates_asset_version(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MINIAPP_DIR", tmp_path)
    (tmp_path / "app.js").write_text("unchanged application")
    (tmp_path / "boot.js").write_text("old watchdog")
    before = server._asset_version()
    (tmp_path / "boot.js").write_text("fixed watchdog")
    assert server._asset_version() != before
