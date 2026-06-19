#!/usr/bin/env python3
"""
VOX — Анализ возможного «раздвоения» базы данных (READ-ONLY).

Из-за исторического дефекта (разные дефолтные пути в vox_db.py и
billing_db.py) данные могли разойтись на два файла:
  - один с актуальными users/sessions/reviews,
  - другой с актуальными balance/payments.

Этот скрипт НИЧЕГО не меняет. Он открывает обе БД в режиме read-only,
показывает таблицы, число записей, диапазоны дат и пересечение user id —
чтобы понять, действительно ли базы разошлись и какая «новее».

Пример:
  python scripts/audit_split_databases.py \
      --db-a backend/vox.db --db-b /data/vox.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    # Открываем строго read-only через URI.
    uri = f"file:{path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _count(con: sqlite3.Connection, table: str) -> int:
    try:
        return con.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    except sqlite3.Error:
        return -1


def _date_range(con: sqlite3.Connection, table: str, col: str = "created_at"):
    try:
        cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            return None
        row = con.execute(f"SELECT MIN({col}) AS lo, MAX({col}) AS hi FROM {table}").fetchone()
        return (row["lo"], row["hi"])
    except sqlite3.Error:
        return None


def _user_ids(con: sqlite3.Connection) -> set[int]:
    try:
        return {r["id"] for r in con.execute("SELECT id FROM users").fetchall()}
    except sqlite3.Error:
        return set()


def _column_present(con: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        return col in cols
    except sqlite3.Error:
        return False


def describe(label: str, path: Path) -> dict:
    print(f"\n=== {label}: {path} ===")
    con = _connect_ro(path)
    if con is None:
        print("  (файл не существует)")
        return {"path": path, "exists": False, "user_ids": set(), "con": None}

    tables = _tables(con)
    print(f"  таблицы: {', '.join(tables) or '(нет)'}")
    for t in tables:
        cnt = _count(con, t)
        dr = _date_range(con, t)
        extra = f"  даты[{dr[0]}..{dr[1]}]" if dr else ""
        print(f"    - {t:<24} записей={cnt}{extra}")

    info = {
        "path": path,
        "exists": True,
        "tables": tables,
        "user_ids": _user_ids(con),
        "has_balance_col": _column_present(con, "users", "balance"),
        "has_payments": "payments" in tables,
        "con": con,
    }
    print(f"  users.balance колонка: {info['has_balance_col']}")
    print(f"  таблица payments: {info['has_payments']}")
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="READ-ONLY анализ двух кандидатов БД VOX")
    ap.add_argument("--db-a", required=True, type=Path)
    ap.add_argument("--db-b", required=True, type=Path)
    args = ap.parse_args()

    a = describe("DB A", args.db_a)
    b = describe("DB B", args.db_b)

    print("\n=== Сравнение ===")
    if not (a["exists"] and b["exists"]):
        print("  Один из файлов отсутствует — раздвоения по двум файлам нет.")
        if a["exists"] != b["exists"]:
            present = args.db_a if a["exists"] else args.db_b
            print(f"  Используется только: {present}")
        return 0

    ids_a, ids_b = a["user_ids"], b["user_ids"]
    common = ids_a & ids_b
    only_a = ids_a - ids_b
    only_b = ids_b - ids_a
    print(f"  user id: A={len(ids_a)} B={len(ids_b)} общих={len(common)} "
          f"только A={len(only_a)} только B={len(only_b)}")

    # Эвристика «разошлись ли данные»
    diverged = bool(only_a or only_b) or (ids_a != ids_b)
    same_users = ids_a == ids_b and ids_a

    if not ids_a and not ids_b:
        print("  Пользователей нет ни в одной БД — анализировать нечего.")
    elif same_users:
        print("  Набор user id ИДЕНТИЧЕН — вероятно это одна и та же БД "
              "(или точная копия). Раздвоения нет.")
    elif diverged:
        print("  ⚠️ Наборы user id РАЗЛИЧАЮТСЯ — базы, вероятно, разошлись.")
        print("     Запустите scripts/migrate_split_databases.py --dry-run "
              "для плана объединения.")

    for info in (a, b):
        if info.get("con"):
            info["con"].close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
