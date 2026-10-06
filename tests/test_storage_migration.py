from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Garden,
    Plant,
    PlantPhoto,
    StorageDeletionOutbox,
    StorageMigrationRecord,
    User,
)
from app.services.storage_migration_service import migrate_local_photos_to_s3
from app.services.storage_outbox_service import (
    enqueue_photo_deletions,
    process_storage_deletion_outbox,
)


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_count = 0

    def put_object(self, **kwargs):
        self.put_count += 1
        self.objects[kwargs["Key"]] = {
            "body": kwargs["Body"],
            "metadata": kwargs["Metadata"],
        }

    def head_object(self, **kwargs):
        item = self.objects[kwargs["Key"]]
        return {"ContentLength": len(item["body"]), "Metadata": item["metadata"]}


def test_local_photo_migration_is_dry_run_verified_and_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "s3_bucket", "private-photo-bucket")
    content = b"verified-image-content"
    source = (settings.upload_dir / "1" / "legacy.jpg").resolve()
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    with SessionLocal() as db:
        user = User(email="storage-migration@example.com", name="Storage", password_hash="unused")
        db.add(user)
        db.flush()
        garden = Garden(user_id=user.id, name="Migration garden")
        db.add(garden)
        db.flush()
        plant = Plant(garden_id=garden.id, name="Rose", growing_place="open_ground")
        db.add(plant)
        db.flush()
        photo = PlantPhoto(
            plant_id=plant.id,
            file_path=str(source),
            storage_key=str(source),
            content_type="image/jpeg",
        )
        db.add(photo)
        db.commit()
        photo_id = photo.id

    fake = FakeS3()
    with SessionLocal() as db:
        dry_run = migrate_local_photos_to_s3(db, client=fake)
        assert dry_run == {
            "eligible": 1, "planned": 1, "uploaded": 0, "verified": 0,
            "failed": 0, "remaining": 1, "ready_to_switch": False,
        }
        assert fake.put_count == 0

        migrated = migrate_local_photos_to_s3(db, execute=True, client=fake)
        assert migrated["uploaded"] == 1
        assert migrated["verified"] == 1
        assert migrated["remaining"] == 0
        assert migrated["ready_to_switch"] is True
        photo = db.get(PlantPhoto, photo_id)
        assert photo.storage_key.startswith(f"legacy/{photo.plant_id}/{photo.id}-")
        assert not Path(photo.storage_key).is_absolute()
        journal = db.scalar(select(StorageMigrationRecord).where(
            StorageMigrationRecord.photo_id == photo_id,
        ))
        assert journal.status == "completed"
        assert len(journal.checksum_sha256) == 64

        repeated = migrate_local_photos_to_s3(db, execute=True, client=fake)
        assert repeated["uploaded"] == 0
        assert repeated["verified"] == 1
        assert repeated["ready_to_switch"] is True
        assert fake.put_count == 1

        verified = migrate_local_photos_to_s3(db, verify_only=True, client=fake)
        assert verified["verified"] == 1
        assert verified["ready_to_switch"] is True

        monkeypatch.setattr(settings, "storage_backend", "s3")
        deleted = []
        monkeypatch.setattr(
            "app.services.storage_outbox_service.delete_storage_reference",
            lambda backend, storage_key, file_path: deleted.append(
                (backend, storage_key, file_path)
            ),
        )
        assert enqueue_photo_deletions(db, [photo]) == 2
        db.commit()
        queued = list(db.scalars(
            select(StorageDeletionOutbox).order_by(StorageDeletionOutbox.id)
        ))
        assert [(item.backend, item.storage_key, item.file_path) for item in queued] == [
            ("s3", photo.storage_key, None),
            ("local", None, str(source)),
        ]

        assert process_storage_deletion_outbox(db) == {"completed": 2, "failed": 0}
        assert deleted == [
            ("s3", photo.storage_key, None),
            ("local", None, str(source)),
        ]
