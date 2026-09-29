import argparse
import json

from app.database import SessionLocal
from app.services.storage_migration_service import migrate_local_photos_to_s3


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверяемая миграция локальных фотографий в S3")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Загрузить и проверить объекты")
    mode.add_argument("--verify-only", action="store_true", help="Повторно сверить ранее загруженные объекты")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = migrate_local_photos_to_s3(
            db, execute=args.execute, verify_only=args.verify_only,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
