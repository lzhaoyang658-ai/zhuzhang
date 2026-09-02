from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.models import Evidence, Project, Quote
from app.services.source_rescan import rescan_source_files
from app.services.source_storage import LocalSourceStorage
from app.services.upload_security import DisabledScanner, MalwareScanResult, UploadSecurityLimits


class CleanScanner:
    def scan(self, _content: bytes) -> MalwareScanResult:
        return MalwareScanResult(status="clean")


def _project(db_session, project_id: str) -> Project:
    item = Project(
        id=project_id,
        name=f"项目 {project_id}",
        city="上海",
        area_sqm=80,
        fund_limit_cents=300_000_00,
        source_file_count=99,
        source_bytes=99,
    )
    db_session.add(item)
    db_session.flush()
    return item


def _quote(project_id: str, *, quote_id: str, filename: str, object_key: str, status: str, size: int) -> Quote:
    return Quote(
        id=quote_id,
        project_id=project_id,
        name=filename.rsplit(".", 1)[0],
        original_name=filename,
        object_key=object_key,
        source_size_bytes=size,
        scan_status=status,
        status="reviewing",
        total_cents=0,
    )


def _evidence(
    project_id: str,
    *,
    evidence_id: str,
    filename: str,
    object_key: str,
    status: str,
    size: int,
) -> Evidence:
    return Evidence(
        id=evidence_id,
        project_id=project_id,
        original_name=filename,
        object_key=object_key,
        mime_type="application/octet-stream",
        size_bytes=size,
        scan_status=status,
        evidence_type="其他",
    )


def test_rescan_local_sources_updates_integrity_metadata_and_recounts(db_session, tmp_path) -> None:
    project = _project(db_session, "project-success")
    quote = _quote(
        project.id,
        quote_id="quote-success",
        filename="报价.csv",
        object_key="quote.csv",
        status="legacy_unscanned",
        size=999,
    )
    evidence = _evidence(
        project.id,
        evidence_id="evidence-success",
        filename="验收.pdf",
        object_key="evidence.pdf",
        status="error",
        size=999,
    )
    untouched = _evidence(
        project.id,
        evidence_id="evidence-skipped",
        filename="旧记录.csv",
        object_key="skipped.csv",
        status="skipped",
        size=7,
    )
    db_session.add_all([quote, evidence, untouched])
    db_session.commit()

    quote_content = "项目名称,合价\n墙面找平,1200\n".encode()
    evidence_content = b"%PDF-1.7\nproof\n%%EOF\n"
    storage = LocalSourceStorage(tmp_path / "sources")
    storage.store_bytes(f"{project.id}/quotes/{quote.object_key}", quote_content, "text/csv")
    storage.store_bytes(f"{project.id}/{evidence.object_key}", evidence_content, "application/pdf")
    scanned_at = datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc)

    summary = rescan_source_files(
        db_session,
        storage=storage,
        scanner=CleanScanner(),
        limits=UploadSecurityLimits(),
        scanned_at=scanned_at,
    )

    assert summary["ok"] is True
    assert summary["records"] == {
        "selected": 2,
        "processed": 2,
        "clean": 2,
        "skipped": 0,
        "error": 0,
        "by_kind": {
            "quote": {"selected": 1, "clean": 1, "skipped": 0, "error": 0},
            "evidence": {"selected": 1, "clean": 1, "skipped": 0, "error": 0},
        },
    }
    assert quote.source_sha256 == hashlib.sha256(quote_content).hexdigest()
    assert quote.source_size_bytes == len(quote_content)
    assert quote.source_mime_type == "text/csv; charset=utf-8"
    assert quote.scan_status == "clean" and quote.scanned_at == scanned_at
    assert evidence.sha256 == hashlib.sha256(evidence_content).hexdigest()
    assert evidence.size_bytes == len(evidence_content)
    assert evidence.mime_type == "application/pdf"
    assert evidence.scan_status == "clean" and evidence.scanned_at == scanned_at
    assert untouched.scan_status == "skipped" and untouched.scanned_at is None
    assert project.source_file_count == 3
    assert project.source_bytes == len(quote_content) + len(evidence_content) + untouched.size_bytes
    assert summary["usage"] == {
        "projects_recalculated": 1,
        "source_file_count": project.source_file_count,
        "source_bytes": project.source_bytes,
    }


def test_disabled_scanner_records_verified_metadata_as_skipped(db_session, tmp_path) -> None:
    project = _project(db_session, "project-disabled")
    item = _evidence(
        project.id,
        evidence_id="disabled-evidence",
        filename="记录.csv",
        object_key="record.csv",
        status="legacy_unscanned",
        size=0,
    )
    db_session.add(item)
    db_session.commit()
    content = "名称,金额\n水电,100\n".encode()
    storage = LocalSourceStorage(tmp_path / "sources")
    storage.store_bytes(f"{project.id}/{item.object_key}", content, "text/csv")

    summary = rescan_source_files(db_session, storage=storage, scanner=DisabledScanner())

    assert summary["records"]["skipped"] == 1
    assert item.scan_status == "skipped"
    assert item.sha256 == hashlib.sha256(content).hexdigest()
    assert item.mime_type == "text/csv; charset=utf-8"
    assert item.size_bytes == len(content)
    assert item.scanned_at is not None


def test_corrupt_and_missing_sources_are_marked_error_without_stopping(db_session, tmp_path) -> None:
    project = _project(db_session, "project-errors")
    corrupt = _evidence(
        project.id,
        evidence_id="a-corrupt",
        filename="损坏.pdf",
        object_key="corrupt.pdf",
        status="legacy_unscanned",
        size=111,
    )
    good = _evidence(
        project.id,
        evidence_id="z-good",
        filename="正常.pdf",
        object_key="good.pdf",
        status="error",
        size=222,
    )
    missing = _quote(
        project.id,
        quote_id="missing-quote",
        filename="缺失.csv",
        object_key="missing.csv",
        status="legacy_unscanned",
        size=333,
    )
    db_session.add_all([corrupt, good, missing])
    db_session.commit()
    good_content = b"%PDF-1.7\ngood\n%%EOF\n"
    storage = LocalSourceStorage(tmp_path / "sources")
    storage.store_bytes(f"{project.id}/{corrupt.object_key}", b"not a PDF", "application/pdf")
    storage.store_bytes(f"{project.id}/{good.object_key}", good_content, "application/pdf")

    summary = rescan_source_files(db_session, storage=storage, scanner=CleanScanner())

    assert summary["ok"] is False
    assert summary["records"]["selected"] == 3
    assert summary["records"]["clean"] == 1
    assert summary["records"]["error"] == 2
    assert {(item["record_id"], item["code"]) for item in summary["failures"]} == {
        (corrupt.id, "FILE_CONTENT_MISMATCH"),
        (missing.id, "SOURCE_FILE_MISSING"),
    }
    assert corrupt.scan_status == missing.scan_status == "error"
    assert corrupt.scanned_at is not None and missing.scanned_at is not None
    assert good.scan_status == "clean"
    assert good.sha256 == hashlib.sha256(good_content).hexdigest()
    assert project.source_file_count == 3
    assert project.source_bytes == corrupt.size_bytes + missing.source_size_bytes + len(good_content)


def test_include_skipped_and_project_filter_limit_scan_and_recount_scope(db_session, tmp_path) -> None:
    first = _project(db_session, "project-first")
    second = _project(db_session, "project-second")
    first_item = _evidence(
        first.id,
        evidence_id="first-skipped",
        filename="first.csv",
        object_key="first.csv",
        status="skipped",
        size=1,
    )
    second_item = _evidence(
        second.id,
        evidence_id="second-skipped",
        filename="second.csv",
        object_key="second.csv",
        status="skipped",
        size=2,
    )
    db_session.add_all([first_item, second_item])
    db_session.commit()
    storage = LocalSourceStorage(tmp_path / "sources")
    first_content = b"a,b\n1,2\n"
    second_content = b"a,b\n3,4\n"
    storage.store_bytes(f"{first.id}/{first_item.object_key}", first_content, "text/csv")
    storage.store_bytes(f"{second.id}/{second_item.object_key}", second_content, "text/csv")

    no_skipped = rescan_source_files(
        db_session,
        project_id=first.id,
        storage=storage,
        scanner=CleanScanner(),
    )
    assert no_skipped["records"]["selected"] == 0
    assert first.source_file_count == 1 and first.source_bytes == 1
    assert second.source_file_count == 99 and second.source_bytes == 99

    included = rescan_source_files(
        db_session,
        project_id=first.id,
        include_skipped=True,
        storage=storage,
        scanner=CleanScanner(),
    )
    assert included["records"]["clean"] == 1
    assert first_item.scan_status == "clean"
    assert first.source_bytes == len(first_content)
    assert second_item.scan_status == "skipped"
    assert second.source_file_count == 99 and second.source_bytes == 99


def test_recount_without_project_filter_repairs_every_project_even_without_candidates(db_session, tmp_path) -> None:
    first = _project(db_session, "project-recount-a")
    second = _project(db_session, "project-recount-b")
    db_session.add(_evidence(
        first.id,
        evidence_id="recount-evidence",
        filename="clean.csv",
        object_key="clean.csv",
        status="clean",
        size=12,
    ))
    db_session.add(_quote(
        second.id,
        quote_id="recount-quote",
        filename="clean.csv",
        object_key="clean.csv",
        status="clean",
        size=34,
    ))
    db_session.commit()

    summary = rescan_source_files(
        db_session,
        storage=LocalSourceStorage(tmp_path / "sources"),
        scanner=CleanScanner(),
    )

    assert summary["records"]["selected"] == 0
    assert summary["usage"] == {
        "projects_recalculated": 2,
        "source_file_count": 2,
        "source_bytes": 46,
    }
    assert (first.source_file_count, first.source_bytes) == (1, 12)
    assert (second.source_file_count, second.source_bytes) == (1, 34)


def test_command_outputs_one_json_summary_and_forwards_filters(monkeypatch, capsys) -> None:
    import rescan_source_files as command

    class FakeSettings:
        def validate_runtime_safety(self) -> None:
            return None

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    captured: dict[str, object] = {}
    expected = {"ok": True, "records": {"error": 0}}

    def fake_rescan(_db, **kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(command, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(command, "SessionLocal", FakeSession)
    monkeypatch.setattr(command, "rescan_source_files", fake_rescan)

    exit_code = command.main(["--project-id", "project-cli", "--include-skipped"])

    assert exit_code == 0
    assert captured == {"project_id": "project-cli", "include_skipped": True}
    assert json.loads(capsys.readouterr().out) == expected
