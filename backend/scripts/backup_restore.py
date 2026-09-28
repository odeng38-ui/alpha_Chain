"""Database backup, restore, and integrity helpers for SQLite acceptance drills."""

import argparse
import hashlib
import secrets
import sqlite3
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_sqlite(source: Path, destination: Path) -> str:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")
    if source == destination:
        raise ValueError("backup destination must differ from source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
    checksum = sha256_file(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        checksum + "\n", encoding="ascii"
    )
    return checksum


def verify_sqlite(database: Path) -> None:
    with sqlite3.connect(database.resolve()) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result}")


def restore_sqlite(backup: Path, target: Path, confirm_target: str) -> None:
    backup = backup.resolve()
    target = target.resolve()
    if confirm_target != str(target):
        raise ValueError("restore target confirmation does not match the resolved target")
    checksum_file = backup.with_suffix(backup.suffix + ".sha256")
    expected = checksum_file.read_text(encoding="ascii").strip()
    actual = sha256_file(backup)
    if not expected or not secrets.compare_digest(expected, actual):
        raise ValueError("backup checksum verification failed")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup) as backup_db, sqlite3.connect(target) as target_db:
        backup_db.backup(target_db)
    verify_sqlite(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("source", type=Path)
    backup_parser.add_argument("destination", type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("target", type=Path)
    restore_parser.add_argument("--confirm-target", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("database", type=Path)
    args = parser.parse_args()
    if args.command == "backup":
        print(backup_sqlite(args.source, args.destination))
    elif args.command == "restore":
        restore_sqlite(args.backup, args.target, args.confirm_target)
        print("restore verified")
    else:
        verify_sqlite(args.database)
        print("integrity verified")


if __name__ == "__main__":
    main()
