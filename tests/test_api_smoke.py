"""
Block G — лёгкий end-to-end смоук основных эндпоинтов через TestClient.

Импорт full-app (main) тянет fastapi + openai + (optional) stripe/edge-tts.
Если каких-то зависимостей нет в окружении — тест пропускается, а не падает.
"""

import pytest

main = pytest.importorskip("main", reason="full app deps not installed")
from fastapi.testclient import TestClient  # noqa: E402

import billing_db  # noqa: E402

client = TestClient(main.app)


def test_status_ok():
    assert client.get("/status").status_code == 200


def test_languages_endpoint():
    r = client.get("/api/languages")
    assert r.status_code == 200
    data = r.json()
    assert len(data["languages"]) == 15
    a, b = data["duo_one_device_default"]["lang_a"], data["duo_one_device_default"]["lang_b"]
    # дефолтная пара one-device обязана поддерживаться
    one_device = {l["code"] for l in data["languages"] if l["duo_one_device"]}
    assert a in one_device and b in one_device


def test_static_js_served():
    for path in ("/vox-connection.js", "/vox-socket.js", "/vox-diagnostics.js", "/pwa-install.js", "/sw.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "javascript" in r.headers.get("content-type", "")


def test_set_config_is_validate_only():
    ok = client.post("/set-config", json={"target_lang": "de"}).json()
    assert ok["deprecated"] is True and ok["config"]["target_lang"] == "de"
    assert client.post("/set-config", json={"target_lang": "zz"}).status_code == 400


def test_register_grants_no_bonus():
    r = client.post("/api/register", json={
        "email": "apismoke@x.com", "name": "S", "password": "secret123",
    })
    assert r.status_code == 200
    uid = r.json()["user"]["id"]
    assert billing_db.get_user_balance(uid) == 0.0
