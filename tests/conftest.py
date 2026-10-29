import os
import tempfile

import pytest

os.environ.setdefault("YANGO_SOURCE", "mock")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pedidos-test-")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    from app import db
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "t.db")
    monkeypatch.setattr(settings, "sessions_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "debug_dir", tmp_path / "debug")
    db.reset_for_tests()
    db.connect()
    yield
    db.reset_for_tests()
