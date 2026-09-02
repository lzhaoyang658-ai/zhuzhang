from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.database import Base, enable_sqlite_foreign_keys  # noqa: E402
from app.models import Evidence, Project  # noqa: E402
from app.services.exporter import create_project_archive  # noqa: E402
from app.services.seed import seed_demo  # noqa: E402


def create_site_photo(path: Path) -> None:
    image = Image.new("RGB", (1200, 800), "#d9d4c8")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 520, 1200, 800), fill="#b8a88e")
    draw.rectangle((110, 115, 480, 610), fill="#ede8dd", outline="#6f746d", width=8)
    draw.rectangle((650, 170, 1080, 615), fill="#c9d2cb", outline="#52675e", width=8)
    draw.line((600, 70, 600, 700), fill="#7d8179", width=10)
    for x in range(690, 1050, 70):
        draw.line((x, 210, x, 580), fill="#8b9991", width=4)
    image.save(path, quality=92)


def main() -> None:
    output = ROOT / "output" / "pdf" / "阶段9-项目档案报告样例.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    task_settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="renovation-report-") as directory:
        workspace = Path(directory)
        task_settings.upload_dir = workspace / "uploads"
        engine = enable_sqlite_foreign_keys(create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool))
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            seed_demo(db)
            project = db.scalar(select(Project))
            project_dir = task_settings.upload_dir / project.id
            project_dir.mkdir(parents=True)
            photo_path = project_dir / "site-photo.jpg"
            create_site_photo(photo_path)
            db.add(Evidence(
                project_id=project.id,
                original_name="水电阶段现场复核.jpg",
                object_key=photo_path.name,
                mime_type="image/jpeg",
                size_bytes=photo_path.stat().st_size,
                evidence_type="现场照片",
                description="水电点位与墙面开槽完成后的现场复核记录。",
            ))
            db.commit()
            build = create_project_archive(db, project, workspace / "archive", object_name="sample.zip")
            with zipfile.ZipFile(build.path) as archive:
                with archive.open("项目正式报告.pdf") as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target)
            print(f"{output}\n页数：{build.report_page_count}")
        finally:
            db.close()


if __name__ == "__main__":
    main()
