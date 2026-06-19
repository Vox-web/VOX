#!/usr/bin/env python3
"""
VOX — Безопасное объединение «разошедшихся» БД (по умолчанию DRY-RUN).

Сценарий: из-за исторического дефекта billing-данные (balance, payments,
bonus_given, is_email_verified) могли осесть в одном файле, а users/auth —
в другом. Этот скрипт переносит billing-состояние из SOURCE в TARGET,
сопоставляя пользователей по EMAIL (стабильный ключ; id могут отличаться).

ГАРАНТИИ БЕЗОПАСНОСТИ:
  * По умолчанию режим --dry-run: только показывает план, ничего не пишет.
  * Реальное изменение — только с явным --apply.
  * Перед --apply создаётся backup TARGET (<target>.bak-YYYYmmdd-HHMMSS).
  * Записи НЕ удаляются.
  * Бонус НЕ начисляется повторно (bonus_given объединяется по OR).
  * Платежи НЕ дублируются (ключ stripe_session_id UNIQUE).
  * При конфликте балансов (оба ненулевые и разные) скрипт ОСТАНАВЛИВАЕТСЯ.

Примеры:
  python scripts/migrate_split_databases.py --target backend/vox.db --source /data/vox.db
  python scripts/migrate_split_databases.py --target backend/vox.db --source /data/vox.db --apply
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _connect(path: Path, read_only: bool) -> sqlite3.Connection:
    if read_only:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _users_by_email(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    try:
        rows = con.execute("SELECT * FROM users").fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for r in rows:
        email = (r["email"] or "").lower().strip()
        if email:
            out[email] = r
    return out


def _has_col(row: sqlite3.Row, col: str) -> bool:
    return col in row.keys()


def _val(row: sqlite3.Row, col: str, default=None):
    return row[col] if _has_col(row, col) else default


def plan_migration(target: Path, source: Path) -> dict:
    tcon = _connect(target, read_only=True)
    scon = _connect(source, read_only=True)

    t_users = _users_by_email(tcon)
    s_users = _users_by_email(scon)

    report = {
        "balance_updates": [],   # (email, old, new)
        "bonus_flag_sets": [],   # email
        "verify_flag_sets": [],  # email
        "payments_to_copy": [],  # (email, session_id, amount)
        "conflicts": [],         # (email, reason, detail)
        "users_only_in_source": sorted(set(s_users) - set(t_users)),
    }

    for email, su in s_users.items():
        tu = t_users.get(email)
        if not tu:
            # Пользователь есть только в source — его billing некуда переносить
            # без создания нового аккаунта. Это конфликт, требующий ручного решения.
            report["conflicts"].append(
                (email, "user_missing_in_target",
                 "пользователь есть только в SOURCE — перенос требует ручного решения")
            )
            continue

        s_balance = float(_val(su, "balance", 0) or 0)
        t_balance = float(_val(tu, "balance", 0) or 0)

        if s_balance and t_balance and abs(s_balance - t_balance) > 1e-9:
            report["conflicts"].append(
                (email, "balance_conflict",
                 f"target={t_balance} source={s_balance} — оба ненулевые и разные")
            )
            continue

        if s_balance and not t_balance:
            report["balance_updates"].append((email, t_balance, s_balance))

        if int(_val(su, "bonus_given", 0) or 0) and not int(_val(tu, "bonus_given", 0) or 0):
            report["bonus_flag_sets"].append(email)

        if int(_val(su, "is_email_verified", 0) or 0) and not int(_val(tu, "is_email_verified", 0) or 0):
            report["verify_flag_sets"].append(email)

    # Платежи: переносим те, чей stripe_session_id отсутствует в target.
    try:
        t_sessions = {
            r["stripe_session_id"]
            for r in tcon.execute("SELECT stripe_session_id FROM payments").fetchall()
            if r["stripe_session_id"]
        }
    except sqlite3.Error:
        t_sessions = set()

    try:
        s_payments = scon.execute(
            "SELECT p.*, u.email AS _email FROM payments p LEFT JOIN users u ON u.id=p.user_id"
        ).fetchall()
    except sqlite3.Error:
        s_payments = []

    for p in s_payments:
        sid = p["stripe_session_id"]
        if sid and sid not in t_sessions:
            report["payments_to_copy"].append(
                ((p["_email"] or "").lower().strip(), sid, float(p["amount"] or 0))
            )

    tcon.close()
    scon.close()
    return report


def print_report(report: dict) -> None:
    print("\n=== ПЛАН МИГРАЦИИ ===")
    print(f"  balance: обновлений {len(report['balance_updates'])}")
    for email, old, new in report["balance_updates"]:
        print(f"    {email}: {old} -> {new}")
    print(f"  bonus_given -> 1: {len(report['bonus_flag_sets'])}")
    print(f"  is_email_verified -> 1: {len(report['verify_flag_sets'])}")
    print(f"  платежей к копированию: {len(report['payments_to_copy'])}")
    for email, sid, amt in report["payments_to_copy"]:
        print(f"    {email}: {sid} ${amt}")
    if report["users_only_in_source"]:
        print(f"  ⚠️ пользователи только в SOURCE: {len(report['users_only_in_source'])}")
    if report["conflicts"]:
        print(f"  ❌ КОНФЛИКТЫ: {len(report['conflicts'])}")
        for email, reason, detail in report["conflicts"]:
            print(f"    {email}: {reason} — {detail}")


def apply_migration(target: Path, source: Path, report: dict) -> None:
    # Backup target
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(target.name + f".bak-{stamp}")
    shutil.copy2(target, backup)
    print(f"\n💾 Backup TARGET: {backup}")

    tcon = _connect(target, read_only=False)
    scon = _connect(source, read_only=True)
    try:
        s_users = _users_by_email(scon)

        for email, _old, new in report["balance_updates"]:
            tcon.execute(
                "UPDATE users SET balance=ROUND(?,6) WHERE lower(trim(email))=?",
                (new, email),
            )
        for email in report["bonus_flag_sets"]:
            tcon.execute(
                "UPDATE users SET bonus_given=1 WHERE lower(trim(email))=?", (email,)
            )
        for email in report["verify_flag_sets"]:
            tcon.execute(
                "UPDATE users SET is_email_verified=1 WHERE lower(trim(email))=?", (email,)
            )

        # Платежи: вставляем с user_id из TARGET (по email), без дублей.
        for email, sid, amt in report["payments_to_copy"]:
            trow = tcon.execute(
                "SELECT id FROM users WHERE lower(trim(email))=?", (email,)
            ).fetchone()
            if not trow:
                continue
            srow = scon.execute(
                "SELECT status, created_at FROM payments WHERE stripe_session_id=?", (sid,)
            ).fetchone()
            status = srow["status"] if srow else "completed"
            tcon.execute(
                "INSERT OR IGNORE INTO payments (user_id, stripe_session_id, amount, status, created_at) "
                "VALUES (?,?,?,?, COALESCE(?, CURRENT_TIMESTAMP))",
                (trow["id"], sid, amt, status, srow["created_at"] if srow else None),
            )

        tcon.commit()
        print("✅ Миграция применена.")
    finally:
        tcon.close()
        scon.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Безопасное объединение разошедшихся БД VOX")
    ap.add_argument("--target", required=True, type=Path, help="БД, в которую переносим (остаётся основной)")
    ap.add_argument("--source", required=True, type=Path, help="БД-источник billing-состояния")
    ap.add_argument("--apply", action="store_true", help="Реально применить (иначе dry-run)")
    args = ap.parse_args()

    if not args.target.exists():
        print(f"❌ TARGET не найден: {args.target}")
        return 2
    if not args.source.exists():
        print(f"❌ SOURCE не найден: {args.source}")
        return 2
    if args.target.resolve() == args.source.resolve():
        print("❌ TARGET и SOURCE — один и тот же файл, объединять нечего.")
        return 2

    report = plan_migration(args.target, args.source)
    print_report(report)

    if report["conflicts"]:
        print("\n🛑 Обнаружены конфликты. Миграция остановлена. "
              "Разрешите конфликты вручную и повторите.")
        return 1

    if not args.apply:
        print("\n(dry-run) Изменения НЕ применялись. Для применения добавьте --apply")
        return 0

    apply_migration(args.target, args.source, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
