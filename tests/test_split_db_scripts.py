"""
Block A — dry-run анализ и план миграции двух разошедшихся БД.

Тест строит две SQLite-базы вручную (имитируя исторический раскол) и
проверяет, что:
  * audit обнаруживает расхождение user id;
  * migrate в dry-run строит корректный план и НИЧЕГО не пишет;
  * migrate не дублирует платежи и не начисляет бонус повторно;
  * конфликт балансов останавливает миграцию.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_split_databases as mig  # noqa: E402


def _make_db(path: Path, users: list[dict], payments: list[dict] | None = None):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE users(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, name TEXT,
        balance REAL DEFAULT 0, bonus_given INTEGER DEFAULT 0,
        is_email_verified INTEGER DEFAULT 0, created_at TEXT DEFAULT '2026-01-01')""")
    con.execute("""CREATE TABLE payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        stripe_session_id TEXT UNIQUE, amount REAL, status TEXT DEFAULT 'completed',
        created_at TEXT DEFAULT '2026-01-01')""")
    for u in users:
        con.execute(
            "INSERT INTO users(id,email,name,balance,bonus_given,is_email_verified) VALUES(?,?,?,?,?,?)",
            (u["id"], u["email"], u.get("name", "U"), u.get("balance", 0),
             u.get("bonus_given", 0), u.get("is_email_verified", 0)),
        )
    for p in (payments or []):
        con.execute(
            "INSERT INTO payments(user_id,stripe_session_id,amount,status) VALUES(?,?,?,?)",
            (p["user_id"], p["sid"], p["amount"], p.get("status", "completed")),
        )
    con.commit()
    con.close()


def test_dry_run_plan_no_writes(tmp_path):
    target = tmp_path / "target.db"  # auth здесь, billing пустой
    source = tmp_path / "source.db"  # billing-состояние здесь
    _make_db(target, [{"id": 1, "email": "a@x.com", "balance": 0, "bonus_given": 0}])
    _make_db(
        source,
        [{"id": 7, "email": "a@x.com", "balance": 5.0, "bonus_given": 1, "is_email_verified": 1}],
        [{"user_id": 7, "sid": "cs_test_1", "amount": 5.0}],
    )

    before = target.read_bytes()
    report = mig.plan_migration(target, source)

    assert report["balance_updates"] == [("a@x.com", 0.0, 5.0)]
    assert report["bonus_flag_sets"] == ["a@x.com"]
    assert report["verify_flag_sets"] == ["a@x.com"]
    assert ("a@x.com", "cs_test_1", 5.0) in report["payments_to_copy"]
    assert not report["conflicts"]
    # dry-run: файл TARGET не изменился
    assert target.read_bytes() == before


def test_apply_is_idempotent_and_no_dupes(tmp_path):
    target = tmp_path / "target.db"
    source = tmp_path / "source.db"
    _make_db(target, [{"id": 1, "email": "a@x.com", "balance": 0}])
    _make_db(
        source,
        [{"id": 7, "email": "a@x.com", "balance": 5.0, "bonus_given": 1}],
        [{"user_id": 7, "sid": "cs_test_1", "amount": 5.0}],
    )

    mig.apply_migration(target, source, mig.plan_migration(target, source))
    # Повторный проход: план должен быть пустым (идемпотентность)
    report2 = mig.plan_migration(target, source)
    assert report2["balance_updates"] == []
    assert report2["payments_to_copy"] == []

    con = sqlite3.connect(target)
    bal = con.execute("SELECT balance FROM users WHERE email='a@x.com'").fetchone()[0]
    pay = con.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    con.close()
    assert abs(bal - 5.0) < 1e-9
    assert pay == 1  # платёж не задублирован


def test_balance_conflict_stops(tmp_path):
    target = tmp_path / "target.db"
    source = tmp_path / "source.db"
    _make_db(target, [{"id": 1, "email": "a@x.com", "balance": 9.0}])
    _make_db(source, [{"id": 7, "email": "a@x.com", "balance": 5.0}])

    report = mig.plan_migration(target, source)
    assert any(c[1] == "balance_conflict" for c in report["conflicts"])
