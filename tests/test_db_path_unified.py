"""
Block A — auth и billing должны использовать ОДИН файл БД.

Регрессионный тест на дефект аудита: vox_db указывал на "vox.db", а
billing_db — на "/data/vox.db".
"""

import os
from pathlib import Path

import db_config
import vox_db
import billing_db


def test_all_modules_share_same_db_path():
    assert vox_db.DB_PATH == db_config.DB_PATH
    assert billing_db.DB_PATH == db_config.DB_PATH
    # И это именно тестовый путь из conftest.
    assert str(db_config.DB_PATH) == os.environ["VOX_DB_PATH"]


def test_env_var_overrides_path(monkeypatch):
    monkeypatch.setenv("VOX_DB_PATH", "/tmp/whatever/custom.db")
    resolved = db_config.resolve_db_path()
    assert resolved == Path("/tmp/whatever/custom.db").expanduser().resolve()


def test_auth_and_billing_hit_same_file(tmp_path, monkeypatch):
    """
    Регистрируем пользователя через vox_db и сразу читаем его баланс через
    billing_db. Если бы это были разные файлы — billing не нашёл бы юзера.
    """
    # init таблиц на общем (тестовом) пути
    vox_db.init_db()
    billing_db.migrate()

    res = vox_db.register_user("samefile@example.com", "Same File", "secret123")
    assert res["ok"], res
    uid = res["user"]["id"]

    # billing видит того же пользователя в той же таблице users
    bu = billing_db.get_user_by_id(uid)
    assert bu is not None
    assert bu["email"] == "samefile@example.com"

    # и баланс читается без ошибок
    bal = billing_db.get_user_balance(uid)
    assert isinstance(bal, float)
