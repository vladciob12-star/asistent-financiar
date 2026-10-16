"""Create or restore an authenticated encrypted SQLite backup.

The encryption key is provided out of band with BACKUP_ENCRYPTION_KEY (base64
of 32 random bytes). This tool never uploads a backup or stores the key.
"""
from __future__ import annotations

import argparse
import base64
import os
import secrets
import sqlite3
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag


MAGIC = b"AFPB1"


def _validate_key(key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("Cheia de backup trebuie să aibă exact 32 bytes (Base64).")


def create_encrypted_backup(database: Path, output: Path, key: bytes) -> None:
    _validate_key(key)
    database, output = Path(database), Path(output)
    if database.resolve() == output.resolve():
        raise ValueError("Fișierul criptat trebuie să aibă o cale diferită de baza de date.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asistent-financiar-backup-") as folder:
        snapshot = Path(folder) / "snapshot.sqlite3"
        source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Verificarea integrității copiei SQLite a eșuat.")
        finally:
            target.close()
            source.close()
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, snapshot.read_bytes(), MAGIC)
        _atomic_write(output, MAGIC + nonce + ciphertext)


def restore_encrypted_backup(backup: Path, target: Path, key: bytes) -> None:
    """Restore to an explicit target path; stop the app before replacing its DB."""
    _validate_key(key)
    backup, target = Path(backup), Path(target)
    if backup.resolve() == target.resolve():
        raise ValueError("Fișierul copiei și baza de date restaurată trebuie să aibă căi diferite.")
    data = backup.read_bytes()
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 12 + 16:
        raise ValueError("Fișierul nu este o copie Asistent Financiar criptată validă.")
    nonce = data[len(MAGIC):len(MAGIC) + 12]
    try:
        plaintext = AESGCM(key).decrypt(nonce, data[len(MAGIC) + 12:], MAGIC)
    except InvalidTag as exc:
        raise ValueError("Cheia nu autentifică această copie sau fișierul este corupt.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asistent-financiar-restore-") as folder:
        restored = Path(folder) / "restored.sqlite3"
        restored.write_bytes(plaintext)
        db = sqlite3.connect(f"file:{restored.as_posix()}?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Copia decriptată nu trece verificarea SQLite.")
        finally:
            db.close()
        Path(str(target) + "-wal").unlink(missing_ok=True)
        Path(str(target) + "-shm").unlink(missing_ok=True)
        _atomic_write(target, plaintext)


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _key_from_env() -> bytes:
    value = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
    if not value:
        raise ValueError("Setează BACKUP_ENCRYPTION_KEY într-un manager de secrete.")
    try:
        key = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("BACKUP_ENCRYPTION_KEY trebuie să fie Base64 valid.") from exc
    _validate_key(key)
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true", help="creează o copie SQLite criptată")
    action.add_argument("--restore", action="store_true", help="restaurează o copie criptată în calea indicată")
    parser.add_argument("--database", type=Path, help="baza sursă pentru --create")
    parser.add_argument("--backup", type=Path, required=True, help="calea copiei criptate")
    parser.add_argument("--target", type=Path, help="baza destinație explicită pentru --restore")
    args = parser.parse_args()
    try:
        key = _key_from_env()
        if args.create:
            if not args.database:
                parser.error("--create necesită --database")
            create_encrypted_backup(args.database, args.backup, key)
        else:
            if not args.target:
                parser.error("--restore necesită --target")
            restore_encrypted_backup(args.backup, args.target, key)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
