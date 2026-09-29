from io import BytesIO

from app.config import settings
from app.models import PlantPhoto
from app.services import storage_service


def test_s3_storage_is_private_and_uses_short_presigned_urls(monkeypatch):
    calls = []

    class FakeS3:
        def put_object(self, **kwargs):
            calls.append(("put", kwargs))

        def get_object(self, **kwargs):
            calls.append(("get", kwargs))
            return {"Body": BytesIO(b"clean-image")}

        def generate_presigned_url(self, operation, Params, ExpiresIn):
            calls.append(("presign", {"operation": operation, "params": Params, "expires": ExpiresIn}))
            return "https://storage.example.test/private-signed-url"

        def delete_object(self, **kwargs):
            calls.append(("delete", kwargs))

    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "private-garden-photos")
    monkeypatch.setattr(settings, "s3_presigned_url_seconds", 300)
    monkeypatch.setattr(storage_service, "_s3_client", lambda: FakeS3())

    key, file_path = storage_service.store_photo(7, b"clean-image", ".jpg", "image/jpeg")
    assert key.startswith("7/")
    assert file_path is None
    put = calls[0][1]
    assert put["ServerSideEncryption"] == "AES256"
    assert put["Metadata"] == {"private": "true"}
    photo = PlantPhoto(plant_id=7, storage_key=key, file_path=None, content_type="image/jpeg")
    assert storage_service.read_photo(photo) == b"clean-image"
    assert storage_service.photo_download(photo).startswith("https://")
    assert calls[-1][1]["expires"] == 300
    storage_service.delete_photo(photo)
    assert calls[-1][0] == "delete"
