from __future__ import annotations

import argparse
import json
import logging

from app.core.config import get_settings
from app.database import SessionLocal
from app.services.source_rescan import SourceRescanError, rescan_source_files


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重新校验并扫描历史报价和证据原文件")
    parser.add_argument("--project-id", help="只处理指定项目")
    parser.add_argument(
        "--include-skipped",
        action="store_true",
        help="同时重新处理之前因扫描器关闭而标记为 skipped 的文件",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        settings.validate_runtime_safety()
    except RuntimeError as exc:
        print(json.dumps({
            "ok": False,
            "error": {"code": "RUNTIME_SAFETY_CHECK_FAILED", "message": str(exc)},
        }, ensure_ascii=False, sort_keys=True))
        return 2

    try:
        with SessionLocal() as db:
            summary = rescan_source_files(
                db,
                project_id=args.project_id,
                include_skipped=args.include_skipped,
            )
    except SourceRescanError as exc:
        print(json.dumps({
            "ok": False,
            "error": {"code": exc.code, "message": exc.message},
        }, ensure_ascii=False, sort_keys=True))
        return 2
    except Exception:
        logger.exception("Source file rescan failed")
        print(json.dumps({
            "ok": False,
            "error": {"code": "SOURCE_RESCAN_FAILED", "message": "源文件复扫失败，请检查服务日志"},
        }, ensure_ascii=False, sort_keys=True))
        return 1

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
