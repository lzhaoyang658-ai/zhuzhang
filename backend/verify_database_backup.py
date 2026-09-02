from __future__ import annotations

import json

from app.services.database_backup import verify_remote_sqlite_backup


def main() -> None:
    result = verify_remote_sqlite_backup()
    print(json.dumps({
        "status": "ok",
        "created_at": result["created_at"],
        "size_bytes": result["size_bytes"],
        "checksum": result["sha256"][:12],
        "manifest_version": result["manifest_version"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
