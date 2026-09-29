from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.package_release import MANIFEST_NAME, build_archive


def test_release_archive_is_clean_and_deterministic(tmp_path: Path):
    source = tmp_path / "project"
    source.mkdir()
    (source / "app.py").write_text("print('safe')\n", encoding="utf-8")
    (source / ".env.example").write_text("KEY=example\n", encoding="utf-8")
    (source / MANIFEST_NAME).write_text("stale manifest\n", encoding="utf-8")
    for relative in (
        ".env",
        ".venv/secret.txt",
        ".git/config",
        ".agents/state.json",
        "uploads/user.jpg",
        "test_uploads/test.png",
        "database.db",
        "old.zip",
        "__pycache__/app.pyc",
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private", encoding="utf-8")

    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_count, first_digest = build_archive(source, first)
    second_count, second_digest = build_archive(source, second)

    assert first_count == second_count == 2
    assert first_digest == second_digest
    with ZipFile(first) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert names.count(MANIFEST_NAME) == 1
        assert set(names) == {".env.example", "app.py", MANIFEST_NAME}
        manifest = archive.read(MANIFEST_NAME).decode("utf-8")
        assert "app.py" in manifest
        assert ".env.example" in manifest
        assert "private" not in manifest


def test_repacking_extracted_archive_does_not_duplicate_manifest(tmp_path: Path):
    source = tmp_path / "extracted"
    source.mkdir()
    (source / "app.py").write_text("print('safe')\n", encoding="utf-8")
    (source / MANIFEST_NAME).write_text("old manifest\n", encoding="utf-8")

    output = tmp_path / "repacked.zip"
    count, _ = build_archive(source, output)

    assert count == 1
    with ZipFile(output) as archive:
        assert archive.namelist() == ["app.py", MANIFEST_NAME]


def test_release_archive_must_be_outside_source(tmp_path: Path):
    source = tmp_path / "project"
    source.mkdir()

    with pytest.raises(ValueError, match="outside"):
        build_archive(source, source / "release.zip")
