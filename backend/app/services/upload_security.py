from __future__ import annotations

import hashlib
import io
import logging
import math
import re
import socket
import stat
import struct
import unicodedata
import warnings
import zipfile
import zlib
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import BinaryIO, Literal, Protocol
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError


logger = logging.getLogger(__name__)


MEBIBYTE = 1024 * 1024
SUPPORTED_UPLOAD_SUFFIXES = frozenset({".csv", ".docx", ".heic", ".jpeg", ".jpg", ".pdf", ".png", ".xlsx"})
HEIC_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"})
OOXML_MAIN_PARTS = {
    ".xlsx": (
        "xl/workbook.xml",
        "workbook",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".docx": (
        "word/document.xml",
        "document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}
OOXML_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
OOXML_MAIN_NAMESPACES = {
    ".xlsx": frozenset({
        "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "http://purl.oclc.org/ooxml/spreadsheetml/main",
    }),
    ".docx": frozenset({
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    }),
}


class UploadSecurityError(ValueError):
    """A stable, transport-neutral rejection that API layers can translate."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class UploadSecurityLimits:
    max_file_bytes: int = 30 * MEBIBYTE
    read_chunk_bytes: int = 64 * 1024
    zip_max_entries: int = 2_000
    zip_max_entry_bytes: int = 50 * MEBIBYTE
    zip_max_total_uncompressed_bytes: int = 200 * MEBIBYTE
    image_max_pixels: int = 50_000_000

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")


DEFAULT_UPLOAD_SECURITY_LIMITS = UploadSecurityLimits()


ScanStatus = Literal["clean", "infected", "unavailable", "not_scanned"]
ClamAVReadinessStatus = Literal["healthy", "unavailable"]


@dataclass(frozen=True)
class MalwareScanResult:
    status: ScanStatus
    signature: str | None = None


class MalwareScanner(Protocol):
    def scan(self, content: bytes) -> MalwareScanResult: ...


@dataclass(frozen=True)
class DisabledScanner:
    """Explicit no-op scanner for development and test environments."""

    def scan(self, content: bytes) -> MalwareScanResult:
        del content
        return MalwareScanResult(status="not_scanned")


DisabledMalwareScanner = DisabledScanner


@dataclass(frozen=True)
class ClamAVScanner:
    """Small clamd client using its length-prefixed INSTREAM protocol."""

    host: str = "127.0.0.1"
    port: int = 3310
    timeout_seconds: float = 10.0
    chunk_bytes: int = 64 * 1024
    response_max_bytes: int = 4_096

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("ClamAV host must not be empty")
        if not 1 <= self.port <= 65_535:
            raise ValueError("ClamAV port is invalid")
        if (
            not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.chunk_bytes <= 0
            or self.response_max_bytes <= 0
        ):
            raise ValueError("ClamAV timeout, chunk size, and response limit must be positive")

    def scan(self, content: bytes) -> MalwareScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                for offset in range(0, len(content), self.chunk_bytes):
                    chunk = content[offset : offset + self.chunk_bytes]
                    connection.sendall(struct.pack("!I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack("!I", 0))
                response = self._read_response(connection)
        except (OSError, TimeoutError, socket.timeout):
            return MalwareScanResult(status="unavailable")
        return self._parse_response(response)

    def ping(self) -> ClamAVReadinessStatus:
        """Probe clamd without scanning content or exposing connection errors."""
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.sendall(b"zPING\0")
                response = self._read_response(connection)
        except (OSError, TimeoutError, socket.timeout):
            return "unavailable"
        return "healthy" if response == b"PONG" else "unavailable"

    def _read_response(self, connection) -> bytes:
        response = bytearray()
        while len(response) < self.response_max_bytes:
            chunk = connection.recv(min(1_024, self.response_max_bytes - len(response)))
            if not chunk:
                break
            response.extend(chunk)
            if b"\0" in chunk or b"\n" in chunk:
                break
        return bytes(response).split(b"\0", 1)[0].split(b"\n", 1)[0]

    @staticmethod
    def _parse_response(response: bytes) -> MalwareScanResult:
        try:
            text = response.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError:
            return MalwareScanResult(status="unavailable")
        if text.endswith(": OK"):
            return MalwareScanResult(status="clean")
        found = re.search(r":\s*(.+?)\s+FOUND$", text)
        if found:
            return MalwareScanResult(status="infected", signature=found.group(1)[:200])
        return MalwareScanResult(status="unavailable")


def create_malware_scanner(
    mode: Literal["disabled", "clamav"],
    *,
    clamav_host: str = "127.0.0.1",
    clamav_port: int = 3310,
    clamav_timeout_seconds: float = 10.0,
) -> MalwareScanner:
    if mode == "disabled":
        return DisabledScanner()
    if mode == "clamav":
        return ClamAVScanner(host=clamav_host, port=clamav_port, timeout_seconds=clamav_timeout_seconds)
    raise ValueError(f"Unsupported malware scanner mode: {mode}")


def check_clamav_readiness(
    *,
    host: str,
    port: int = 3310,
    timeout_seconds: float = 10.0,
) -> ClamAVReadinessStatus:
    """Return only a stable readiness status for valid and invalid clamd settings."""
    try:
        scanner = ClamAVScanner(host=host, port=port, timeout_seconds=timeout_seconds)
        return scanner.ping()
    except Exception as exc:
        logger.warning("ClamAV readiness probe failed (%s)", type(exc).__name__)
        return "unavailable"


@dataclass(frozen=True)
class UploadInspection:
    content: bytes = field(repr=False, compare=False)
    detected_type: str
    detected_mime_type: str
    sha256: str
    size_bytes: int
    scan_status: Literal["clean", "not_scanned"]


def inspect_upload_stream(
    stream: BinaryIO,
    suffix: str,
    *,
    scanner: MalwareScanner | None = None,
    limits: UploadSecurityLimits = DEFAULT_UPLOAD_SECURITY_LIMITS,
    allowed_suffixes: Collection[str] | None = None,
) -> UploadInspection:
    content, digest = _read_stream(stream, limits)
    return _inspect_upload(
        content,
        digest,
        suffix,
        scanner=scanner,
        limits=limits,
        allowed_suffixes=allowed_suffixes,
    )


def inspect_upload_bytes(
    content: bytes | bytearray | memoryview,
    suffix: str,
    *,
    scanner: MalwareScanner | None = None,
    limits: UploadSecurityLimits = DEFAULT_UPLOAD_SECURITY_LIMITS,
    allowed_suffixes: Collection[str] | None = None,
) -> UploadInspection:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TypeError("content must be bytes-like")
    value = bytes(content)
    if len(value) > limits.max_file_bytes:
        _reject(413, "FILE_TOO_LARGE", "上传文件超过大小限制")
    return _inspect_upload(
        value,
        hashlib.sha256(value).hexdigest(),
        suffix,
        scanner=scanner,
        limits=limits,
        allowed_suffixes=allowed_suffixes,
    )


def _read_stream(stream: BinaryIO, limits: UploadSecurityLimits) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(limits.read_chunk_bytes)
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("stream.read() must return bytes-like data")
        value = bytes(chunk)
        size += len(value)
        if size > limits.max_file_bytes:
            _reject(413, "FILE_TOO_LARGE", "上传文件超过大小限制")
        digest.update(value)
        chunks.append(value)
    return b"".join(chunks), digest.hexdigest()


def _inspect_upload(
    content: bytes,
    digest: str,
    suffix: str,
    *,
    scanner: MalwareScanner | None,
    limits: UploadSecurityLimits,
    allowed_suffixes: Collection[str] | None,
) -> UploadInspection:
    normalized_suffix = _normalize_suffix(suffix)
    allowed = SUPPORTED_UPLOAD_SUFFIXES if allowed_suffixes is None else {
        _normalize_suffix(item) for item in allowed_suffixes
    }
    if normalized_suffix not in SUPPORTED_UPLOAD_SUFFIXES or normalized_suffix not in allowed:
        _reject(415, "FILE_TYPE_NOT_ALLOWED", "不支持此文件格式")
    if not content:
        _reject(422, "EMPTY_FILE", "上传文件为空")

    detected_type, detected_mime_type = _validate_content(content, normalized_suffix, limits)
    scan_result = _scan_content(scanner or DisabledScanner(), content)
    if scan_result.status == "infected":
        _reject(422, "MALWARE_DETECTED", "文件未通过安全检查")
    if scan_result.status == "unavailable":
        _reject(503, "FILE_SCAN_UNAVAILABLE", "文件安全检查暂不可用，请稍后重试")
    if scan_result.status not in {"clean", "not_scanned"}:
        _reject(503, "FILE_SCAN_UNAVAILABLE", "文件安全检查返回了无效结果")

    return UploadInspection(
        content=content,
        detected_type=detected_type,
        detected_mime_type=detected_mime_type,
        sha256=digest,
        size_bytes=len(content),
        scan_status=scan_result.status,
    )


def _scan_content(scanner: MalwareScanner, content: bytes) -> MalwareScanResult:
    try:
        result = scanner.scan(content)
    except Exception:
        return MalwareScanResult(status="unavailable")
    return result if isinstance(result, MalwareScanResult) else MalwareScanResult(status="unavailable")


def _normalize_suffix(suffix: str) -> str:
    normalized = suffix.strip().lower()
    if normalized and not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized


def _validate_content(content: bytes, suffix: str, limits: UploadSecurityLimits) -> tuple[str, str]:
    if suffix == ".pdf":
        return _validate_pdf(content)
    if suffix in {".jpg", ".jpeg"}:
        return _validate_image(content, expected_format="JPEG", limits=limits)
    if suffix == ".png":
        return _validate_image(content, expected_format="PNG", limits=limits)
    if suffix == ".heic":
        return _validate_heic(content)
    if suffix == ".csv":
        return _validate_csv(content)
    if suffix in OOXML_MAIN_PARTS:
        return _validate_ooxml(content, suffix, limits)
    _reject(415, "FILE_TYPE_NOT_ALLOWED", "不支持此文件格式")


def _validate_pdf(content: bytes) -> tuple[str, str]:
    if not re.match(rb"^%PDF-(?:1\.[0-9]|2\.0)(?:[\r\n\t %])", content[:16]):
        _content_mismatch()
    if b"%%EOF" not in content[-4_096:]:
        _reject(415, "FILE_CONTENT_INVALID", "PDF 文件结构不完整")
    return "pdf", "application/pdf"


def _validate_image(content: bytes, *, expected_format: str, limits: UploadSecurityLimits) -> tuple[str, str]:
    expected_prefix = b"\xff\xd8\xff" if expected_format == "JPEG" else b"\x89PNG\r\n\x1a\n"
    if not content.startswith(expected_prefix):
        _content_mismatch()
    if expected_format == "JPEG" and not content.rstrip(b"\x00\t\r\n ").endswith(b"\xff\xd9"):
        _reject(415, "FILE_CONTENT_INVALID", "JPEG 文件结构不完整")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != expected_format:
                    _content_mismatch()
                if image.width <= 0 or image.height <= 0 or image.width * image.height > limits.image_max_pixels:
                    _reject(415, "IMAGE_DIMENSIONS_INVALID", "图片像素尺寸超过限制")
                image.verify()
    except UploadSecurityError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, SyntaxError):
        _reject(415, "FILE_CONTENT_INVALID", "图片文件损坏或无法识别")
    if expected_format == "JPEG":
        return "jpeg", "image/jpeg"
    return "png", "image/png"


def _validate_heic(content: bytes) -> tuple[str, str]:
    if len(content) < 16 or content[4:8] != b"ftyp":
        _content_mismatch()
    box_size = int.from_bytes(content[:4], "big")
    header_size = 8
    if box_size == 1:
        if len(content) < 24:
            _reject(415, "FILE_CONTENT_INVALID", "HEIC 文件头不完整")
        box_size = int.from_bytes(content[8:16], "big")
        header_size = 16
    elif box_size == 0:
        box_size = len(content)
    if box_size < header_size + 8 or box_size > len(content):
        _reject(415, "FILE_CONTENT_INVALID", "HEIC ftyp 容器长度无效")
    major_brand = content[header_size : header_size + 4]
    compatible_start = header_size + 8
    compatible_brands = {
        content[offset : offset + 4]
        for offset in range(compatible_start, box_size - 3, 4)
    }
    if not ({major_brand} | compatible_brands) & HEIC_BRANDS:
        _content_mismatch()
    return "heic", "image/heic"


def _validate_csv(content: bytes) -> tuple[str, str]:
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        _reject(415, "FILE_CONTENT_INVALID", "CSV 必须使用 UTF-8 编码")
    if not text.strip():
        _reject(422, "EMPTY_FILE", "CSV 不包含有效内容")
    if "\x00" in text:
        _reject(415, "FILE_CONTENT_INVALID", "CSV 包含 NUL 字节")
    dangerous = [char for char in text if unicodedata.category(char) == "Cc" and char not in "\t\r\n"]
    if dangerous:
        _reject(415, "FILE_CONTENT_INVALID", "CSV 包含危险控制字符")
    return "csv", "text/csv; charset=utf-8"


def _validate_ooxml(content: bytes, suffix: str, limits: UploadSecurityLimits) -> tuple[str, str]:
    main_part, root_name, expected_content_type, detected_type, mime_type = OOXML_MAIN_PARTS[suffix]
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if not infos:
                _content_mismatch()
            if len(infos) > limits.zip_max_entries:
                _reject(415, "ARCHIVE_LIMIT_EXCEEDED", "Office 文件包含过多压缩条目")
            normalized_names: set[str] = set()
            total_uncompressed = 0
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                normalized = _validate_zip_entry(info)
                folded = normalized.casefold()
                if folded in normalized_names:
                    _reject(415, "ARCHIVE_UNSAFE", "Office 文件包含重复压缩路径")
                normalized_names.add(folded)
                info_by_name[normalized] = info
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    _reject(415, "ARCHIVE_UNSAFE", "Office 文件使用了不支持的压缩算法")
                if info.file_size > limits.zip_max_entry_bytes:
                    _reject(415, "ARCHIVE_LIMIT_EXCEEDED", "Office 文件单个压缩条目超过限制")
                total_uncompressed += info.file_size
                if total_uncompressed > limits.zip_max_total_uncompressed_bytes:
                    _reject(415, "ARCHIVE_LIMIT_EXCEEDED", "Office 文件解压后总体积超过限制")

            required_names = {"[Content_Types].xml", main_part}
            if not required_names.issubset(info_by_name):
                _content_mismatch()
            broken_entry = archive.testzip()
            if broken_entry is not None:
                _reject(415, "FILE_CONTENT_INVALID", "Office 文件压缩内容损坏")

            content_types_xml = archive.read("[Content_Types].xml")
            main_xml = archive.read(main_part)
    except UploadSecurityError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error):
        _content_mismatch()

    if not _declares_ooxml_main_type(content_types_xml, main_part, expected_content_type):
        _content_mismatch()
    try:
        main_root = ElementTree.fromstring(main_xml)
    except ElementTree.ParseError:
        _reject(415, "FILE_CONTENT_INVALID", "Office 主文档 XML 损坏")
    allowed_root_tags = {f"{{{namespace}}}{root_name}" for namespace in OOXML_MAIN_NAMESPACES[suffix]}
    if main_root.tag not in allowed_root_tags:
        _content_mismatch()
    return detected_type, mime_type


def _validate_zip_entry(info: zipfile.ZipInfo) -> str:
    raw_name = info.filename
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        _reject(415, "ARCHIVE_UNSAFE", "Office 文件包含非法压缩路径")
    path = PurePosixPath(raw_name)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", raw_name):
        _reject(415, "ARCHIVE_UNSAFE", "Office 文件包含越界压缩路径")
    if any(unicodedata.category(char) == "Cc" for char in raw_name):
        _reject(415, "ARCHIVE_UNSAFE", "Office 文件包含非法压缩路径")
    if info.flag_bits & 0x1:
        _reject(415, "ARCHIVE_UNSAFE", "Office 文件包含加密压缩条目")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(unix_mode):
        _reject(415, "ARCHIVE_UNSAFE", "Office 文件包含软链接")
    basename = path.name.casefold()
    if basename in {"vbaproject.bin", "vbaprojectsignature.bin"}:
        _reject(415, "ARCHIVE_UNSAFE", "Office 文件包含 VBA 宏")
    return str(path)


def _declares_ooxml_main_type(content: bytes, main_part: str, expected_content_type: str) -> bool:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return False
    if root.tag != f"{{{OOXML_CONTENT_TYPES_NAMESPACE}}}Types":
        return False
    expected_part_name = f"/{main_part}"
    for element in root.iter():
        if element.tag != f"{{{OOXML_CONTENT_TYPES_NAMESPACE}}}Override":
            continue
        if element.attrib.get("PartName") == expected_part_name:
            return element.attrib.get("ContentType") == expected_content_type
    return False


def _content_mismatch() -> None:
    _reject(415, "FILE_CONTENT_MISMATCH", "文件内容与扩展名不一致")


def _reject(status_code: int, code: str, message: str) -> None:
    raise UploadSecurityError(status_code, code, message)
