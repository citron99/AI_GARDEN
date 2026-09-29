from app.catalog_taxonomy import country_code_from_region, resolve_catalog_code
from app.database import SessionLocal


def test_country_code_from_region_is_conservative_and_supports_local_aliases():
    assert country_code_from_region("LV") == "LV"
    assert country_code_from_region("Rīga, Latvija") == "LV"
    assert country_code_from_region("Tallinn") == "EE"
    assert country_code_from_region("Vilnius, Lithuania") == "LT"
    assert country_code_from_region("Springfield") is None
    assert country_code_from_region(None) is None


def test_admin_manages_localized_catalog_aliases(client):
    auth = client.post("/api/v1/auth/register", json={
        "email": "taxonomy-admin@example.com", "name": "Taxonomy admin",
        "password": "strong-password", "region": "Riga",
    })
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}
    assert client.post("/api/v1/admin/catalog-taxonomy-aliases", headers=headers, json={
        "alias_type": "problem", "stable_code": "care.water_stress",
        "locale": "en", "alias": "Yellow leaves after overwatering",
    }).status_code == 403

    from app.admin_cli import set_admin
    assert set_admin("taxonomy-admin@example.com", True)
    created = client.post("/api/v1/admin/catalog-taxonomy-aliases", headers=headers, json={
        "alias_type": "problem", "stable_code": "care.water_stress",
        "locale": "en", "alias": "Yellow leaves after overwatering",
    })
    assert created.status_code == 201
    assert created.json()["stable_code"] == "care.water_stress"

    duplicate = client.post("/api/v1/admin/catalog-taxonomy-aliases", headers=headers, json={
        "alias_type": "problem", "stable_code": "care.water_stress",
        "locale": "en", "alias": "  YELLOW LEAVES AFTER OVERWATERING  ",
    })
    assert duplicate.status_code == 409
    listed = client.get("/api/v1/admin/catalog-taxonomy-aliases",
                        headers=headers, params={"alias_type": "problem"})
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]
    with SessionLocal() as db:
        assert resolve_catalog_code(
            db, "problem", "yellow leaves after overwatering", "en") == "care.water_stress"

    deleted = client.delete(
        f"/api/v1/admin/catalog-taxonomy-aliases/{created.json()['id']}", headers=headers)
    assert deleted.status_code == 204
    with SessionLocal() as db:
        assert resolve_catalog_code(
            db, "problem", "yellow leaves after overwatering", "en") is None
