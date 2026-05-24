"""
Database Engine — inisialisasi dan migrasi SQLite.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from el_solver.config import PROJECT_ROOT, settings
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Mendapatkan koneksi ke database SQLite dengan optimasi WAL."""
    settings.ensure_dirs()
    path = db_path or settings.database_path
    conn = sqlite3.connect(path, timeout=5.0)  # busy_timeout 5s
    conn.row_factory = sqlite3.Row

    # Optimasi untuk concurrency
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass

    return conn


def migrate(db_path: Path | None = None, migrations_dir: Path | None = None) -> list[str]:
    """Menjalankan migrasi SQL dari folder migrations/ secara idempotent.

    Returns list nama file yang baru dijalankan. Jika semua sudah jalan → list kosong.
    """
    mdir = migrations_dir or MIGRATIONS_DIR
    if not mdir.exists():
        logger.warning(f"Direktori migrasi tidak ditemukan: {mdir}")
        return []

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()

        # Pastikan tabel tracker ada
        cursor.executescript(_SCHEMA_MIGRATIONS_DDL)
        conn.commit()

        # Ambil yang sudah jalan
        applied = {row["filename"] for row in cursor.execute("SELECT filename FROM schema_migrations")}

        migration_files = sorted(mdir.glob("*.sql"))
        newly_applied: list[str] = []

        for sql_file in migration_files:
            if sql_file.name in applied:
                logger.debug(f"Migrasi sudah ada, skip: {sql_file.name}")
                continue

            logger.info(f"Menjalankan migrasi: {sql_file.name}")
            sql = sql_file.read_text(encoding="utf-8")
            cursor.executescript(sql)
            cursor.execute(
                "INSERT INTO schema_migrations (filename) VALUES (?)",
                (sql_file.name,),
            )
            conn.commit()
            newly_applied.append(sql_file.name)

        return newly_applied
    except Exception as e:
        conn.rollback()
        logger.error(f"Gagal menjalankan migrasi: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    applied = migrate()
    if applied:
        print(f"Migrasi selesai: {', '.join(applied)}")
    else:
        print("Sudah up-to-date.")
