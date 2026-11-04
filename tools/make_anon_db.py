# -*- coding: utf-8 -*-
"""Обезличенная копия базы для скриншотов и публикации.

Ники других игроков заменяются на «Игрок NN», свой аккаунт остаётся как есть.
Скриншоты тогда можно показывать людям, не выставляя напоказ чужой состав.

    python tools/make_anon_db.py                       # в temp, рядом с базой
    python tools/make_anon_db.py --out D:\\anon.db
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caliber_db as db   # noqa: E402
import paths              # noqa: E402


def anonymize(src: str, dst: str) -> dict:
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, dst + suffix)
    con = sqlite3.connect(dst)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT my_uid FROM matches WHERE my_uid IS NOT NULL LIMIT 1").fetchone()
    my_uid = row[0] if row else None
    uids = [r[0] for r in con.execute(
        "SELECT DISTINCT uid FROM match_players WHERE uid IS NOT NULL AND uid<>?",
        (my_uid,))]
    alias = {uid: f"Игрок {index + 1:02d}" for index, uid in enumerate(uids)}
    for table in ("match_players", "match_loadout", "nicknames"):
        try:
            con.executemany(f"UPDATE {table} SET nick=? WHERE uid=?",
                            [(name, uid) for uid, name in alias.items()])
        except sqlite3.Error:
            continue
    if my_uid is not None:
        con.execute("DELETE FROM accounts WHERE uid<>?", (my_uid,))
    con.commit()
    con.close()
    return {"my_uid": my_uid, "renamed": len(alias)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Обезличенная копия базы CaliberStat")
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."),
                                                  "caliberstat_anon.db"))
    args = ap.parse_args()
    src = str(paths.db_path())
    if not os.path.exists(src):
        print(f"[!] нет базы: {src}")
        return 1
    info = anonymize(src, args.out)
    print(f"[*] копия: {args.out}")
    print(f"[*] мой uid: {info['my_uid']}, переименовано игроков: {info['renamed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
