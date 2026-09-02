from __future__ import annotations

import json

from app.core.config import get_settings
from app.services.maintenance import run_maintenance_once


def main() -> None:
    get_settings().validate_runtime_safety()
    print(json.dumps(run_maintenance_once(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
