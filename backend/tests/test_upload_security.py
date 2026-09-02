from __future__ import annotations

import hashlib
import io
import stat
import struct
import zipfile
from dataclasses import replace

import pytest
from PIL import Image

from app.services import upload_security
from app.services.upload_security import (
    ClamAVScanner,
    DisabledScanner,
    MalwareScanResult,
    UploadSecurityError,
    UploadSecurityLimits,
    check_clamav_readiness,
    create_malware_scanner,
    inspect_upload_bytes,
    inspect_upload_stream,
)


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/{main_part}" ContentType="{content_type}"/>
</Types>
"""

MAIN_PARTS = {
    ".xlsx": (
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
    ),
    ".docx": (
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
    ),
}


def _error_code(call) -> str:
    with pytest.raises(UploadSecurityError) as exc_info:
        call()
    return exc_info.value.code


def _image_bytes(image_format: str, size: tuple[int, int] = (3, 2)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(35, 80, 120)).save(buffer, format=image_format)
    return buffer.getvalue()


def _ooxml_bytes(
    suffix: str,
    *,
    extras: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
    content_type: str | None = None,
    main_xml: bytes | None = None,
) -> bytes:
    main_part, expected_content_type, expected_main_xml = MAIN_PARTS[suffix]
    types_xml = CONTENT_TYPES.format(
        main_part=main_part,
        content_type=content_type or expected_content_type,
    ).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types_xml)
        archive.writestr(main_part, main_xml or expected_main_xml)
        for name, value in extras or []:
            archive.writestr(name, value)
    return buffer.getvalue()


def _ordinary_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", "not an Office document")
    return buffer.getvalue()


def _mark_zip_entries_encrypted(content: bytes) -> bytes:
    patched = bytearray(content)
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        cursor = 0
        while True:
            cursor = patched.find(signature, cursor)
            if cursor < 0:
                break
            offset = cursor + flag_offset
            flags = int.from_bytes(patched[offset : offset + 2], "little") | 0x1
            patched[offset : offset + 2] = flags.to_bytes(2, "little")
            cursor += 4
    return bytes(patched)


class StaticScanner:
    def __init__(self, status: str):
        self.status = status
        self.seen: bytes | None = None

    def scan(self, content: bytes) -> MalwareScanResult:
        self.seen = content
        return MalwareScanResult(status=self.status)  # type: ignore[arg-type]


class FakeClamdConnection:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = bytearray()
        self.timeout: float | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, content: bytes) -> None:
        self.sent.extend(content)

    def recv(self, _size: int) -> bytes:
        response, self.response = self.response, b""
        return response


def _decode_instream_request(request: bytes) -> tuple[list[bytes], int]:
    command = b"zINSTREAM\0"
    assert request.startswith(command)
    cursor = len(command)
    chunks: list[bytes] = []
    while True:
        length = struct.unpack("!I", request[cursor : cursor + 4])[0]
        cursor += 4
        if length == 0:
            return chunks, cursor
        chunks.append(request[cursor : cursor + length])
        cursor += length


def test_bytes_and_stream_interfaces_return_content_identity() -> None:
    content = "项目名称,合价\n墙面找平,1200\n".encode("utf-8-sig")
    scanner = StaticScanner("clean")

    from_bytes = inspect_upload_bytes(content, "CSV", scanner=scanner)
    from_stream = inspect_upload_stream(io.BytesIO(content), ".csv", scanner=DisabledScanner())

    assert from_bytes.content == from_stream.content == content
    assert from_bytes.detected_type == "csv"
    assert from_bytes.detected_mime_type == "text/csv; charset=utf-8"
    assert from_bytes.sha256 == from_stream.sha256 == hashlib.sha256(content).hexdigest()
    assert from_bytes.size_bytes == len(content)
    assert from_bytes.scan_status == "clean"
    assert from_stream.scan_status == "not_scanned"
    assert scanner.seen == content


def test_stream_enforces_size_while_reading() -> None:
    limits = replace(UploadSecurityLimits(), max_file_bytes=5, read_chunk_bytes=2)
    code = _error_code(lambda: inspect_upload_stream(io.BytesIO(b"123456"), ".csv", limits=limits))
    assert code == "FILE_TOO_LARGE"


def test_empty_and_disallowed_uploads_are_rejected() -> None:
    assert _error_code(lambda: inspect_upload_bytes(b"", ".csv")) == "EMPTY_FILE"
    assert _error_code(lambda: inspect_upload_bytes(b"text", ".exe")) == "FILE_TYPE_NOT_ALLOWED"
    assert _error_code(
        lambda: inspect_upload_bytes(b"a,b\n1,2\n", ".csv", allowed_suffixes={".pdf"})
    ) == "FILE_TYPE_NOT_ALLOWED"


def test_pdf_requires_version_header_and_eof_marker() -> None:
    content = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    inspected = inspect_upload_bytes(content, ".pdf")
    assert inspected.detected_type == "pdf"
    assert inspected.detected_mime_type == "application/pdf"
    assert _error_code(lambda: inspect_upload_bytes(b"PDF-1.7\n%%EOF", ".pdf")) == "FILE_CONTENT_MISMATCH"
    assert _error_code(lambda: inspect_upload_bytes(b"%PDF-1.7\nno trailer", ".pdf")) == "FILE_CONTENT_INVALID"


@pytest.mark.parametrize(("image_format", "suffix", "detected", "mime"), [
    ("JPEG", ".jpg", "jpeg", "image/jpeg"),
    ("PNG", ".png", "png", "image/png"),
])
def test_jpeg_and_png_are_decoded_and_verified(image_format: str, suffix: str, detected: str, mime: str) -> None:
    inspected = inspect_upload_bytes(_image_bytes(image_format), suffix)
    assert (inspected.detected_type, inspected.detected_mime_type) == (detected, mime)


def test_images_reject_mismatched_corrupt_and_excessive_dimensions() -> None:
    jpeg = _image_bytes("JPEG", (3, 2))
    assert _error_code(lambda: inspect_upload_bytes(jpeg, ".png")) == "FILE_CONTENT_MISMATCH"
    assert _error_code(lambda: inspect_upload_bytes(b"\xff\xd8\xffbroken\xff\xd9", ".jpg")) == "FILE_CONTENT_INVALID"
    limits = replace(UploadSecurityLimits(), image_max_pixels=5)
    assert _error_code(lambda: inspect_upload_bytes(jpeg, ".jpg", limits=limits)) == "IMAGE_DIMENSIONS_INVALID"


def test_csv_requires_utf8_and_rejects_nul_and_control_characters() -> None:
    assert inspect_upload_bytes("名称,金额\n水电,100\n".encode(), ".csv").detected_type == "csv"
    assert _error_code(lambda: inspect_upload_bytes(b"\xff\xfeinvalid", ".csv")) == "FILE_CONTENT_INVALID"
    assert _error_code(lambda: inspect_upload_bytes(b"a,b\x00\n", ".csv")) == "FILE_CONTENT_INVALID"
    assert _error_code(lambda: inspect_upload_bytes(b"a,b\x1b\n", ".csv")) == "FILE_CONTENT_INVALID"


def test_heic_requires_ftyp_and_supported_brand() -> None:
    payload = b"heic" + b"\x00\x00\x00\x00" + b"mif1"
    content = (8 + len(payload)).to_bytes(4, "big") + b"ftyp" + payload
    inspected = inspect_upload_bytes(content, ".heic")
    assert (inspected.detected_type, inspected.detected_mime_type) == ("heic", "image/heic")

    avif_payload = b"avif" + b"\x00\x00\x00\x00" + b"avis"
    avif = (8 + len(avif_payload)).to_bytes(4, "big") + b"ftyp" + avif_payload
    assert _error_code(lambda: inspect_upload_bytes(avif, ".heic")) == "FILE_CONTENT_MISMATCH"
    assert _error_code(lambda: inspect_upload_bytes(b"not-heic", ".heic")) == "FILE_CONTENT_MISMATCH"


@pytest.mark.parametrize(("suffix", "detected", "mime"), [
    (".xlsx", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (".docx", "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
])
def test_ooxml_requires_valid_container_content_type_and_main_xml(suffix: str, detected: str, mime: str) -> None:
    inspected = inspect_upload_bytes(_ooxml_bytes(suffix), suffix)
    assert (inspected.detected_type, inspected.detected_mime_type) == (detected, mime)


def test_arbitrary_zip_wrong_ooxml_kind_and_macro_content_type_are_rejected() -> None:
    assert _error_code(lambda: inspect_upload_bytes(_ordinary_zip(), ".xlsx")) == "FILE_CONTENT_MISMATCH"
    assert _error_code(lambda: inspect_upload_bytes(_ooxml_bytes(".docx"), ".xlsx")) == "FILE_CONTENT_MISMATCH"
    macro_type = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
    assert _error_code(
        lambda: inspect_upload_bytes(_ooxml_bytes(".xlsx", content_type=macro_type), ".xlsx")
    ) == "FILE_CONTENT_MISMATCH"


@pytest.mark.parametrize("extra_name", ["../escape.bin", "/absolute.bin", "C:/windows.bin", "folder\\evil.bin"])
def test_ooxml_rejects_unsafe_paths(extra_name: str) -> None:
    content = _ooxml_bytes(".xlsx", extras=[(extra_name, b"unsafe")])
    assert _error_code(lambda: inspect_upload_bytes(content, ".xlsx")) == "ARCHIVE_UNSAFE"


def test_ooxml_rejects_encryption_symlinks_and_vba() -> None:
    encrypted = _mark_zip_entries_encrypted(_ooxml_bytes(".xlsx"))
    assert _error_code(lambda: inspect_upload_bytes(encrypted, ".xlsx")) == "ARCHIVE_UNSAFE"

    symlink = zipfile.ZipInfo("xl/link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with_symlink = _ooxml_bytes(".xlsx", extras=[(symlink, b"../outside")])
    assert _error_code(lambda: inspect_upload_bytes(with_symlink, ".xlsx")) == "ARCHIVE_UNSAFE"

    with_vba = _ooxml_bytes(".xlsx", extras=[("xl/vbaProject.bin", b"macro")])
    assert _error_code(lambda: inspect_upload_bytes(with_vba, ".xlsx")) == "ARCHIVE_UNSAFE"


def test_ooxml_enforces_entry_single_file_and_total_uncompressed_limits() -> None:
    content = _ooxml_bytes(".xlsx", extras=[("xl/worksheets/sheet1.xml", b"x" * 100)])
    assert _error_code(
        lambda: inspect_upload_bytes(content, ".xlsx", limits=replace(UploadSecurityLimits(), zip_max_entries=2))
    ) == "ARCHIVE_LIMIT_EXCEEDED"
    assert _error_code(
        lambda: inspect_upload_bytes(content, ".xlsx", limits=replace(UploadSecurityLimits(), zip_max_entry_bytes=50))
    ) == "ARCHIVE_LIMIT_EXCEEDED"
    assert _error_code(
        lambda: inspect_upload_bytes(
            content,
            ".xlsx",
            limits=replace(UploadSecurityLimits(), zip_max_total_uncompressed_bytes=200),
        )
    ) == "ARCHIVE_LIMIT_EXCEEDED"


def test_ooxml_rejects_corrupt_main_xml() -> None:
    content = _ooxml_bytes(".docx", main_xml=b"<document>")
    assert _error_code(lambda: inspect_upload_bytes(content, ".docx")) == "FILE_CONTENT_INVALID"


def test_clamav_scanner_sends_instream_frames_and_parses_clean_response(monkeypatch) -> None:
    connection = FakeClamdConnection(b"stream: OK\0")
    monkeypatch.setattr(upload_security.socket, "create_connection", lambda *_args, **_kwargs: connection)
    scanner = ClamAVScanner(host="clamd", port=3310, timeout_seconds=2, chunk_bytes=3)

    result = scanner.scan(b"abcdefg")
    chunks, consumed = _decode_instream_request(bytes(connection.sent))

    assert result.status == "clean"
    assert chunks == [b"abc", b"def", b"g"]
    assert consumed == len(connection.sent)
    assert connection.timeout == 2


def test_clamav_scanner_parses_infection_and_unavailable(monkeypatch) -> None:
    infected_connection = FakeClamdConnection(b"stream: Eicar-Signature FOUND\0")
    monkeypatch.setattr(upload_security.socket, "create_connection", lambda *_args, **_kwargs: infected_connection)
    infected = ClamAVScanner().scan(b"sample")
    assert infected.status == "infected"
    assert infected.signature == "Eicar-Signature"

    def fail_connection(*_args, **_kwargs):
        raise OSError("clamd is down")

    monkeypatch.setattr(upload_security.socket, "create_connection", fail_connection)
    assert ClamAVScanner().scan(b"sample").status == "unavailable"


def test_clamav_readiness_uses_null_terminated_ping_and_accepts_only_pong(monkeypatch) -> None:
    connection = FakeClamdConnection(b"PONG\0")
    monkeypatch.setattr(upload_security.socket, "create_connection", lambda *_args, **_kwargs: connection)

    assert check_clamav_readiness(host="clamd", port=3310, timeout_seconds=2) == "healthy"
    assert bytes(connection.sent) == b"zPING\0"
    assert connection.timeout == 2

    malformed = FakeClamdConnection(b"unexpected internal detail\0")
    monkeypatch.setattr(upload_security.socket, "create_connection", lambda *_args, **_kwargs: malformed)
    assert check_clamav_readiness(host="clamd") == "unavailable"


def test_clamav_readiness_returns_unavailable_without_exposing_failures(monkeypatch) -> None:
    def fail_connection(*_args, **_kwargs):
        raise OSError("sensitive network topology")

    monkeypatch.setattr(upload_security.socket, "create_connection", fail_connection)

    assert check_clamav_readiness(host="clamd") == "unavailable"
    assert check_clamav_readiness(host="") == "unavailable"

    class UnexpectedFailure(FakeClamdConnection):
        def sendall(self, _content: bytes) -> None:
            raise RuntimeError("sensitive scanner implementation detail")

    monkeypatch.setattr(
        upload_security.socket,
        "create_connection",
        lambda *_args, **_kwargs: UnexpectedFailure(b""),
    )
    assert check_clamav_readiness(host="clamd") == "unavailable"


def test_inspection_fails_closed_for_infection_and_scanner_failure() -> None:
    content = b"a,b\n1,2\n"
    assert _error_code(
        lambda: inspect_upload_bytes(content, ".csv", scanner=StaticScanner("infected"))
    ) == "MALWARE_DETECTED"
    assert _error_code(
        lambda: inspect_upload_bytes(content, ".csv", scanner=StaticScanner("unavailable"))
    ) == "FILE_SCAN_UNAVAILABLE"


def test_scanner_factory_requires_an_explicit_supported_mode() -> None:
    assert isinstance(create_malware_scanner("disabled"), DisabledScanner)
    assert isinstance(create_malware_scanner("clamav", clamav_host="scanner"), ClamAVScanner)
    with pytest.raises(ValueError, match="Unsupported malware scanner mode"):
        create_malware_scanner("unknown")  # type: ignore[arg-type]
