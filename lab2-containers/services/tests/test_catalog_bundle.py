"""catalog-service: кілька послуг за один візит."""
import os

if os.path.exists("test_catalog.db"):
    os.remove("test_catalog.db")
os.environ["DATABASE_URL"] = "sqlite:///./test_catalog.db"

from fastapi.testclient import TestClient  # noqa: E402

from catalog import main as catalog  # noqa: E402

client = TestClient(catalog.app)
client.__enter__()


def test_bundle_sums_duration_and_price_and_keeps_common_masters():
    r = client.get("/internal/bundle", params={"ids": "oil-change,brake-pads"}).json()
    assert r["durationMin"] == 40 + 90 and r["priceUah"] == 650 + 1200
    assert r["name"] == "Заміна масла й фільтра + Заміна гальмівних колодок"
    assert [m["id"] for m in r["masters"]] == ["master-1"]  # лише Олег уміє обидві


def test_bundle_without_common_master_has_no_masters():
    assert client.get("/internal/bundle", params={"ids": "tire-fitting,ac-service"}).json()["masters"] == []


def test_single_service_bundle_and_errors():
    one = client.get("/internal/bundle", params={"ids": "diagnostics"}).json()
    assert one["id"] == "diagnostics" and one["durationMin"] == 50
    assert client.get("/internal/bundle", params={"ids": "nope"}).status_code == 404
    assert client.get("/internal/bundle", params={"ids": ""}).status_code == 422
