"""Snapshot bazy w GCS (Cloud Run) — na atrapie bloba, bez sieci."""
import shutil

from app import db


class FakeBlob:
    def __init__(self, store):
        self.store = store

    def exists(self):
        return "file" in self.store

    def upload_from_filename(self, path):
        self.store["file"] = path + ".uploaded"
        shutil.copy(path, self.store["file"])

    def download_to_filename(self, path):
        shutil.copy(self.store["file"], path)


def test_backup_and_restore_roundtrip(monkeypatch, tmp_path):
    store = {}
    monkeypatch.setenv("GCS_DB_URI", "gs://bucket/profitability.duckdb")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(db, "_gcs_blob", lambda uri: FakeBlob(store))

    db.init_db()  # brak snapshotu → pusta baza
    db.set_margins({"Laptopy": 7.5})
    assert db.backup_to_gcs()

    # „nowa instancja Cloud Run”: lokalny plik znika, baza wraca z GCS
    get_settings().db_path.unlink()
    db.init_db()
    assert db.get_margins() == {"Laptopy": 7.5}
