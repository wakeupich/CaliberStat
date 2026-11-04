# -*- coding: utf-8 -*-
"""Запросы к базе для окна: всё, что показывает интерфейс, живёт здесь.

Отдельно от `gui.py`, чтобы интерфейс не разрастался SQL-строками, а запросы
можно было проверить из консоли без запуска окна.

    python queries.py
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from typing import Optional

import caliber_db as db

DAY = 86400.0


# -- общее ---------------------------------------------------------------
def result_of(match) -> Optional[bool]:
    """Победа = True, поражение = False, неизвестно = None.

    Сравниваем команду-победителя со своей. Если своей команды нет (бой из
    чужого реплея без записи «кто играл»), честно возвращаем None.
    """
    try:
        my_team = match["my_team"]
        winner = match["winner_team"]
    except (KeyError, IndexError, TypeError):
        my_team, winner = match.get("my_team"), match.get("winner_team")
    if my_team is None or winner is None or winner == -1:
        return None
    return winner == my_team


def start_ts(match) -> Optional[float]:
    """Игра показывает НАЧАЛО боя, а в базе лежит время результата."""
    ts = match["ts_epoch"] if not isinstance(match, dict) else match.get("ts_epoch")
    if ts is None:
        return None
    duration = match["duration_s"] if not isinstance(match, dict) else match.get("duration_s")
    return ts - float(duration or 0)


def fmt_dt(ts: Optional[float], with_year: bool = False) -> str:
    if not ts:
        return "—"
    fmt = "%d.%m.%Y %H:%M" if with_year else "%d.%m %H:%M"
    return datetime.fromtimestamp(ts).strftime(fmt)


def fmt_dur(seconds: Optional[float]) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} ч {m:02d} мин"
    return f"{m}:{s:02d}"


def fmt_hours(seconds: Optional[float]) -> str:
    if not seconds:
        return "0 ч"
    hours = float(seconds) / 3600.0
    if hours < 1:
        return f"{int(float(seconds) / 60)} мин"
    return f"{hours:.1f} ч".replace(".", ",")


# -- аккаунты ------------------------------------------------------------
def accounts(con: sqlite3.Connection) -> list[dict]:
    """Аккаунты, которыми играли: uid и ник из таблицы accounts, счётчик — из боёв."""
    rows = con.execute("""
        SELECT m.my_uid AS uid,
               (SELECT a.nick FROM accounts a WHERE a.uid=m.my_uid) AS nick,
               COUNT(*) AS matches, MAX(m.ts_epoch) AS last_ts
        FROM matches m WHERE m.my_uid IS NOT NULL
        GROUP BY m.my_uid ORDER BY matches DESC
    """).fetchall()
    return [dict(r) for r in rows]


# -- обзор ---------------------------------------------------------------
def overview(con: sqlite3.Connection, my_uid: Optional[int] = None,
             days: int = 7) -> dict:
    """Цифры для карточек обзора. Считаем только по «моим» боям."""
    where, args = ["1=1"], []
    if my_uid:
        where.append("m.my_uid=?")
        args.append(int(my_uid))
    where_sql = " AND ".join(where)

    matches = con.execute(
        f"SELECT * FROM matches m WHERE {where_sql}", args).fetchall()
    total = len(matches)
    week_ago = time.time() - days * DAY
    week = sum(1 for m in matches if (m["ts_epoch"] or 0) >= week_ago)
    duration = sum(float(m["duration_s"] or 0) for m in matches)
    wins = sum(1 for m in matches if result_of(m) is True)
    losses = sum(1 for m in matches if result_of(m) is False)

    stats = {"matches": total, "week": week, "duration": duration,
             "wins": wins, "losses": losses, "kills": 0, "bot_kills": 0,
             "deaths": 0, "damage": 0.0, "headshots": 0, "body_hits": 0,
             "revives": 0}
    if my_uid:
        row = con.execute(
            """SELECT COUNT(*) c, COALESCE(SUM(p.kills),0) k,
                      COALESCE(SUM(p.bot_kills),0) bk, COALESCE(SUM(p.deaths),0) d,
                      COALESCE(SUM(p.damage_dealt),0) dmg,
                      COALESCE(SUM(p.headshots),0) hs, COALESCE(SUM(p.body_hits),0) bh,
                      COALESCE(SUM(p.revives),0) rv
               FROM match_players p JOIN matches m ON m.id=p.match_id
               WHERE p.uid=? AND """ + where_sql.replace("m.", "m."),
            [int(my_uid)] + args).fetchone()
        if row:
            stats.update({"kills": row["k"], "bot_kills": row["bk"],
                          "deaths": row["d"], "damage": row["dmg"],
                          "headshots": row["hs"], "body_hits": row["bh"],
                          "revives": row["rv"]})
    hits = stats["headshots"] + stats["body_hits"]
    stats["accuracy"] = (stats["headshots"] / hits) if hits else None
    stats["kd"] = (stats["kills"] + stats["bot_kills"]) / stats["deaths"] \
        if stats["deaths"] else None
    return stats


def daily(con: sqlite3.Connection, my_uid: Optional[int] = None,
          days: int = 14) -> list[tuple[str, int]]:
    """Сколько боёв в день, за последние `days` дней."""
    where, args = ["ts_epoch IS NOT NULL"], []
    if my_uid:
        where.append("my_uid=?")
        args.append(int(my_uid))
    cutoff = time.time() - days * DAY
    args.append(cutoff)
    rows = con.execute(
        f"""SELECT date(ts_epoch,'unixepoch','localtime') d, COUNT(*) c
            FROM matches WHERE {' AND '.join(where)} AND ts_epoch>=?
            GROUP BY d ORDER BY d""", args).fetchall()
    by_day = {r["d"]: r["c"] for r in rows}
    out = []
    for index in range(days - 1, -1, -1):
        key = datetime.fromtimestamp(time.time() - index * DAY).strftime("%Y-%m-%d")
        out.append((key, by_day.get(key, 0)))
    return out


# -- матчи ---------------------------------------------------------------
def recent(con: sqlite3.Connection, limit: int = 200,
           my_uid: Optional[int] = None, mode: Optional[str] = None) -> list[dict]:
    """Бои для таблицы: сразу с моими цифрами (один запрос, не N+1)."""
    where, args = ["1=1"], []
    if my_uid:
        where.append("m.my_uid=?")
        args.append(int(my_uid))
    if mode:
        where.append("m.mode_name=?")
        args.append(mode)
    args.append(limit)
    rows = con.execute(f"""
        SELECT m.*,
               (SELECT COUNT(*) FROM match_players p WHERE p.match_id=m.id) AS players,
               (SELECT COUNT(*) FROM match_loadout l WHERE l.match_id=m.id) AS loadouts,
               p.kills AS my_kills, p.bot_kills AS my_bot_kills,
               p.deaths AS my_deaths, p.damage_dealt AS my_damage,
               p.headshots AS my_headshots, p.body_hits AS my_body_hits,
               p.role AS my_role, p.operator_id AS my_operator_id
        FROM matches m
        LEFT JOIN match_players p ON p.match_id=m.id AND p.uid=?
        WHERE {' AND '.join(where)}
        ORDER BY m.ts_epoch DESC LIMIT ?""", [int(my_uid or 0)] + args).fetchall()
    return [dict(r) for r in rows]


def modes(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT DISTINCT mode_name FROM matches WHERE mode_name "
                       "IS NOT NULL ORDER BY mode_name").fetchall()
    return [r[0] for r in rows]


def match(con: sqlite3.Connection, match_id: str) -> Optional[dict]:
    row = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    return dict(row) if row else None


def match_players(con: sqlite3.Connection, match_id: str) -> list[dict]:
    """Состав боя: телеметрия, код оперативника и наличие снаряжения."""
    rows = con.execute("""
        SELECT p.*, l.loadout_json
        FROM match_players p
        LEFT JOIN match_loadout l ON l.match_id=p.match_id AND l.uid=p.uid
        WHERE p.match_id=?
        ORDER BY COALESCE(p.kills,0)+COALESCE(p.bot_kills,0) DESC, p.nick""",
        (match_id,)).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        raw = data.pop("loadout_json", None)
        code = None
        level = prestige = None
        if raw:
            try:
                loadout = json.loads(raw) or {}
                code = loadout.get("operator")
                level = loadout.get("card_level")
                prestige = loadout.get("prestige")
            except Exception:
                code = None
        data["operator_code"] = code
        data["card_level"] = level
        data["prestige"] = prestige
        data["has_loadout"] = 1 if raw else 0
        out.append(data)
    return out


def player_card(con: sqlite3.Connection, match_id: str, uid: int) -> dict:
    """Всё про игрока в конкретном бою: телеметрия, снаряжение, награды."""
    player = con.execute("SELECT * FROM match_players WHERE match_id=? AND uid=?",
                         (match_id, int(uid))).fetchone()
    rewards = con.execute("SELECT * FROM match_rewards WHERE match_id=? AND uid=?",
                          (match_id, int(uid))).fetchone()
    loadout = db.get_loadout(con, match_id, int(uid)) or {}
    return {"player": dict(player) if player else {},
            "rewards": dict(rewards) if rewards else {},
            "loadout": loadout}


def teammates(con: sqlite3.Connection, uid: int, exclude_match: Optional[str] = None
              ) -> list[dict]:
    """С кем чаще всего играл: ник, сколько совместных боёв, сколько побед."""
    rows = con.execute("""
        SELECT p.nick, COUNT(*) AS matches,
               SUM(CASE WHEN mm.my_team IS NOT NULL AND mm.winner_team=mm.my_team
                        THEN 1 ELSE 0 END) AS wins
        FROM match_players p
        JOIN matches mm ON mm.id=p.match_id
        WHERE p.uid<>? AND p.match_id IN
              (SELECT match_id FROM match_players WHERE uid=?)
        GROUP BY p.nick ORDER BY matches DESC LIMIT 30""",
        (int(uid), int(uid))).fetchall()
    return [dict(r) for r in rows]


# -- служебное -----------------------------------------------------------
def servers(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute("""
        SELECT COALESCE(server,'—') AS server, COALESCE(region,'—') AS region,
               COUNT(*) AS matches, MAX(ts_epoch) AS last_ts
        FROM matches GROUP BY server, region ORDER BY matches DESC""").fetchall()
    return [dict(r) for r in rows]


def errors(con: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = con.execute("SELECT ts, message FROM errors ORDER BY ts DESC LIMIT ?",
                       (limit,)).fetchall()
    return [dict(r) for r in rows]


def loading(con: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = con.execute("""SELECT ts, map_name, mode, seconds FROM loading
                          ORDER BY ts DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def db_stats(con: sqlite3.Connection) -> dict:
    one = lambda sql: con.execute(sql).fetchone()[0]
    return {
        "matches": one("SELECT COUNT(*) FROM matches"),
        "players": one("SELECT COUNT(*) FROM match_players"),
        "loadouts": one("SELECT COUNT(*) FROM match_loadout"),
        "nicks": one("SELECT COUNT(*) FROM nicknames"),
        "first_match": one("SELECT MIN(ts_epoch) FROM matches"),
        "last_match": one("SELECT MAX(ts_epoch) FROM matches"),
    }


def _demo() -> None:
    """Быстрая проверка запросов без окна."""
    import paths
    con = db.connect(str(paths.db_path()))
    con.row_factory = sqlite3.Row
    acc = accounts(con)
    print("аккаунты:", acc)
    uid = acc[0]["uid"] if acc else None
    print("обзор:", overview(con, uid))
    print("по дням:", daily(con, uid, days=5))
    print("режимы:", modes(con))
    ms = recent(con, limit=3, my_uid=uid)
    for m in ms:
        print(f"  {m['id'][:8]} {m['mode_name']} {fmt_dt(start_ts(m))} "
              f"итог={result_of(m)}")
    if ms:
        pl = match_players(con, ms[0]["id"])
        print(f"  игроков в бою: {len(pl)}")
        me = next((p for p in pl if p["uid"] == uid), pl[0])
        card = player_card(con, ms[0]["id"], me["uid"])
        print(f"  карточка {me['nick']}: снаряжение есть={bool(card['loadout'])}")
    print("серверы:", servers(con)[:3])
    print("статистика базы:", db_stats(con))


if __name__ == "__main__":
    _demo()
