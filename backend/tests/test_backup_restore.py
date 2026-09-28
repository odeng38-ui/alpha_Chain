import sqlite3

import pytest

from scripts.backup_restore import backup_sqlite, restore_sqlite, sha256_file, verify_sqlite


def test_sqlite_backup_restore_drill(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backups" / "source.db"
    restored = tmp_path / "restored.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO sample (value) VALUES (?)",
            [("alpha",), ("beta",), ("gamma",)],
        )
        connection.commit()

    checksum = backup_sqlite(source, backup)
    assert checksum == sha256_file(backup)
    assert backup.with_suffix(".db.sha256").read_text(encoding="ascii").strip() == checksum

    restore_sqlite(backup, restored, str(restored.resolve()))
    verify_sqlite(restored)
    with sqlite3.connect(restored) as connection:
        rows = connection.execute("SELECT id, value FROM sample ORDER BY id").fetchall()
    assert rows == [(1, "alpha"), (2, "beta"), (3, "gamma")]


def test_restore_rejects_wrong_target_confirmation(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    backup_sqlite(source, backup)

    with pytest.raises(ValueError, match="confirmation"):
        restore_sqlite(backup, tmp_path / "target.db", "wrong-target")


def test_restore_rejects_tampered_backup(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    backup_sqlite(source, backup)
    with backup.open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        restore_sqlite(backup, target, str(target.resolve()))
