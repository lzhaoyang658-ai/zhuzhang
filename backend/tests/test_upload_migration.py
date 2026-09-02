from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(database_path: Path, *arguments: str) -> None:
    env = os.environ.copy()
    env.update({
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{database_path}",
    })
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_upload_metadata_migration_backfills_and_round_trips(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    _run_alembic(database_path, "upgrade", "d4a8b7c91e20")

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            INSERT INTO projects (
                id, name, city, area_sqm, area_basis, renovation_type,
                address, notes, planned_start, planned_end, fund_limit_cents,
                reserve_cents, status, status_before_deletion, archived_at,
                deletion_requested_at, deletion_scheduled_for, created_at
            ) VALUES (
                'project-1', '迁移测试', '苏州', 100, '套内面积', '全包',
                NULL, '', NULL, NULL, 10000000, 0, '施工中', NULL, NULL,
                NULL, NULL, CURRENT_TIMESTAMP
            );
            INSERT INTO quotes (
                id, project_id, name, original_name, object_key, status,
                total_cents, source_total_cents, input_type, parse_method,
                parser_version, page_count, warnings, error_message, created_at
            ) VALUES
                (
                    'quote-1', 'project-1', '报价一', 'quote.csv', 'quote.csv',
                    'reviewing', 0, NULL, 'spreadsheet', 'deterministic_table',
                    'beta-1', 1, '[]', NULL, CURRENT_TIMESTAMP
                ),
                (
                    'quote-2', 'project-1', '报价二', 'quote.xlsx', 'quote.xlsx',
                    'reviewing', 0, NULL, 'spreadsheet', 'deterministic_table',
                    'beta-1', 1, '[]', NULL, CURRENT_TIMESTAMP
                );
            INSERT INTO evidence (
                id, project_id, original_name, object_key, mime_type,
                size_bytes, evidence_type, description, related_type,
                related_id, created_at
            ) VALUES (
                'evidence-1', 'project-1', 'evidence.pdf', 'evidence.pdf',
                'application/pdf', 1234, '合同', '', NULL, NULL,
                CURRENT_TIMESTAMP
            );
        """)

    _run_alembic(database_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        project = connection.execute(
            "SELECT source_file_count, source_bytes FROM projects WHERE id = 'project-1'"
        ).fetchone()
        quote = connection.execute(
            "SELECT source_size_bytes, source_sha256, source_mime_type, scan_status "
            "FROM quotes WHERE id = 'quote-1'"
        ).fetchone()
        evidence = connection.execute(
            "SELECT sha256, scan_status FROM evidence WHERE id = 'evidence-1'"
        ).fetchone()
        assert project == (3, 1234)
        assert quote == (0, None, None, "legacy_unscanned")
        assert evidence == (None, "legacy_unscanned")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE projects SET source_bytes = -1 WHERE id = 'project-1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE quotes SET source_sha256 = 'short' WHERE id = 'quote-1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE evidence SET scan_status = 'unsafe' WHERE id = 'evidence-1'"
            )

    _run_alembic(database_path, "downgrade", "d4a8b7c91e20")
    with sqlite3.connect(database_path) as connection:
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)")
        }
        quote_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(quotes)")
        }
        evidence_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(evidence)")
        }
        assert "source_file_count" not in project_columns
        assert "source_size_bytes" not in quote_columns
        assert "scan_status" not in evidence_columns

    _run_alembic(database_path, "upgrade", "head")
    _run_alembic(database_path, "check")
