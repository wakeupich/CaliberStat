# -*- coding: utf-8 -*-
"""Схема базы «Калибр-центра» и функции записи.

SQLite, WAL. Все таблицы идемпотентны по ключам: повторный разбор того же
лога/матча ничего не дублирует.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "caliberstat.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id           TEXT PRIMARY KEY,
    result_ts    TEXT,
    ts_epoch     REAL,
    mode         TEXT,        -- pve | pve2 | onslaught2 | pvp | pvpve2
    f4           INTEGER,
    mode_name    TEXT,
    map          TEXT,
    map_mode     TEXT,
    region       TEXT,
    server       TEXT,
    duration_s   REAL,
    mission_start REAL,
    winner_team  INTEGER,
    my_uid       INTEGER,
    my_team      INTEGER,
    rounds_json  TEXT,
    onslaught_json TEXT,
    segments_json  TEXT,
    players_count  INTEGER,
    source       TEXT,        -- log | archive
    UNIQUE(id)
);

CREATE TABLE IF NOT EXISTS match_players (
    match_id      TEXT NOT NULL,
    uid           INTEGER,
    nick          TEXT,
    team          INTEGER,
    role          TEXT,       -- Assault | Gunner | Medic | Sniper (из подготовки боя)
    operator_id   INTEGER,    -- CharacterId оперативника (из PickedCard подготовки боя)
    kills         INTEGER,
    bot_kills     INTEGER,
    assists       INTEGER,
    bot_assists   INTEGER,
    executions    INTEGER,
    deaths        INTEGER,
    revives       INTEGER,
    damage_dealt  REAL,
    damage_received REAL,
    friendly_damage REAL,
    heal          REAL,
    headshots     INTEGER,
    body_hits     INTEGER,
    leave_time    REAL,
    loaded        INTEGER,
    connected_finish INTEGER,
    first_leave   INTEGER,
    kick_reason   TEXT,
    ribbons_json  TEXT,
    PRIMARY KEY (match_id, uid)
);

CREATE TABLE IF NOT EXISTS match_rewards (
    match_id      TEXT NOT NULL,
    uid           INTEGER,
    ribbon_xp     INTEGER,
    medal_xp      INTEGER,
    match_xp      INTEGER,
    premium       INTEGER,
    battlepass_base INTEGER,
    battlepass_premium INTEGER,
    battlepass_before INTEGER,
    card_xp       INTEGER,
    currencies_json TEXT,
    penalties_json  TEXT,
    medals_json   TEXT,
    PRIMARY KEY (match_id, uid)
);

CREATE TABLE IF NOT EXISTS nicknames (
    uid        INTEGER PRIMARY KEY,
    nick       TEXT,
    first_seen REAL,
    last_seen  REAL
);

-- снаряжение игроков в бою (только из реплея: там предметы записаны ключами)
CREATE TABLE IF NOT EXISTS match_loadout (
    match_id     TEXT NOT NULL,
    uid          INTEGER NOT NULL,
    nick         TEXT,
    loadout_json TEXT NOT NULL,
    PRIMARY KEY (match_id, uid)
);

-- аккаунты, под которыми заходили (несколько игроков на одной машине)
CREATE TABLE IF NOT EXISTS accounts (
    uid        INTEGER PRIMARY KEY,
    nick       TEXT,
    first_seen REAL,
    last_seen  REAL
);

CREATE TABLE IF NOT EXISTS lobby (
    battle_id  TEXT PRIMARY KEY,
    ts         REAL,
    mode       INTEGER,
    mission    TEXT,
    map_id     TEXT,
    players    INTEGER,
    teams_json TEXT
);

CREATE TABLE IF NOT EXISTS api_log (
    endpoint   TEXT,
    ts         REAL,
    hash       TEXT,
    payload    TEXT,
    PRIMARY KEY (endpoint, hash)
);

CREATE TABLE IF NOT EXISTS errors (
    ts      REAL,
    message TEXT,
    PRIMARY KEY (ts, message)
);

CREATE TABLE IF NOT EXISTS loading (
    ts       REAL,
    map      TEXT,
    mode_map TEXT,
    seconds  REAL,
    PRIMARY KEY (ts, map)
);

CREATE INDEX IF NOT EXISTS idx_matches_ts ON matches(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_mp_nick ON match_players(nick);
CREATE INDEX IF NOT EXISTS idx_mp_uid ON match_players(uid);
CREATE INDEX IF NOT EXISTS idx_api_endpoint ON api_log(endpoint);

-- справочники из клиента: обновляются кнопкой, а не фоном
CREATE TABLE IF NOT EXISTS ref_map (
    key  TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_operator (
    code          TEXT PRIMARY KEY,   -- код из реплея: ksk2011s
    operator_id   INTEGER,            -- позиция карточки в клиенте (с единицы)
    name          TEXT,
    role          TEXT,
    collection    TEXT,
    collection_ru TEXT,
    enabled       INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_ref_operator_id ON ref_operator(operator_id);

CREATE TABLE IF NOT EXISTS ref_item (
    key  TEXT PRIMARY KEY,            -- fr2_custom, ACOGTA01NSN_top, field_medicine
    name TEXT NOT NULL,
    kind TEXT                         -- weapon | module | gear | consumable | skin | perk
);
CREATE INDEX IF NOT EXISTS idx_ref_item_kind ON ref_item(kind);

-- дерево навыков: ветки и порядок узлов (технологии клиента)
CREATE TABLE IF NOT EXISTS ref_perk_line (
    technology TEXT PRIMARY KEY,
    line       TEXT,
    line_name  TEXT,
    pos        INTEGER,
    descr      TEXT
);

-- дерево прокачки оперативника: что открывается на каком уровне карточки
CREATE TABLE IF NOT EXISTS ref_card_tree (
    card   TEXT,
    level  INTEGER,
    slot   INTEGER,
    key    TEXT,
    kind   TEXT,      -- upgrade | tech | slot
    name   TEXT,
    effect TEXT,
    PRIMARY KEY (card, level, slot, key)
);
CREATE INDEX IF NOT EXISTS idx_ref_card_tree ON ref_card_tree(card, level);

CREATE TABLE IF NOT EXISTS ref_medal (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_ribbon (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

F4_NAMES = {
    1: "Зачистка",
    3: "Спецоперация",
    14: "Натиск: Легенда",
    33: "Тени ужаса",
    37: "Натиск: Подкрепление",
}

ROLE_NAMES = {
    "-1": "Лидер",
    "-2": "Офицер",
    "-3": "Боец",
    "-4": "Рекрут",
    "-5": "Командир",
    "-6": "Заместитель",
}

# боевые роли оперативников: приходят в ответе подготовки боя (battlepreparation)
# и в архиве матчей старого парсера в поле PlayerRoles
OPERATOR_ROLES = {
    "Assault": "Штурмовик",
    "Gunner": "Поддержка",
    "Medic": "Медик",
    "Sniper": "Снайпер",
}


def role_ru(role: Optional[str]) -> str:
    if not role:
        return ""
    return OPERATOR_ROLES.get(role, role)


# Роль зашита в код оперативника последней буквой: seal2014a, 22spn2016g, amfm, ksk2011s.
# Проверено по скриншоту итогов боя 04.11.2026 (Дамба, Натиск: Подкрепление): 6 из 6
# совпали — a штурмовик, g поддержка, m медик, s снайпер.
ROLE_BY_SUFFIX = {"a": "Assault", "g": "Gunner", "m": "Medic", "s": "Sniper"}


def role_from_operator_code(code: Optional[str]) -> Optional[str]:
    """Роль по коду оперативника. Нужна для реплеев: там роли отдельно не приходят."""
    if not code:
        return None
    return ROLE_BY_SUFFIX.get(str(code).strip().lower()[-1:])


def _roles_from_teams_json(text: Optional[str]) -> dict:
    """{"ник": "Gunner"} из lobby.teams_json (сохранённая подготовка боя)."""
    if not text:
        return {}
    try:
        players = json.loads(text)
    except Exception:
        return {}
    roles = {}
    for p in players or []:
        if isinstance(p, dict) and p.get("nick") and p.get("role"):
            roles[p["nick"]] = p["role"]
    return roles


def _operators_from_teams_json(text: Optional[str]) -> dict:
    """{"ник": CharacterId} из lobby.teams_json."""
    if not text:
        return {}
    try:
        players = json.loads(text)
    except Exception:
        return {}
    out = {}
    for p in players or []:
        if isinstance(p, dict) and p.get("nick") and p.get("operator_id"):
            out[p["nick"]] = int(p["operator_id"])
    return out


def operator_id_from_user(user: dict) -> Optional[int]:
    """CharacterId оперативника из записи игрока в подготовке боя.

    В игре это `PickedCard.Card["1"] + 1` — проверено по ролям 4 888 записей:
    совпадение с базой оперативников 94 % (остальное — новые оперативники,
    которых в старой базе нет).
    """
    card = ((user.get("PickedCard") or {}).get("Card") or {})
    raw = card.get("1")
    try:
        return int(raw) + 1 if raw is not None else None
    except (TypeError, ValueError):
        return None


def backfill_roles(con: sqlite3.Connection, match_id: Optional[str] = None) -> int:
    """Добирает роли из подготовки боя в match_players (только пустые)."""
    sql = "SELECT battle_id, teams_json FROM lobby"
    args: tuple = ()
    if match_id:
        sql += " WHERE battle_id=?"
        args = (match_id,)
    changed = 0
    try:
        rows = con.execute(sql, args).fetchall()
    except Exception:
        return 0
    for row in rows:
        roles = _roles_from_teams_json(row["teams_json"])
        operators = _operators_from_teams_json(row["teams_json"])
        for nick, role in roles.items():
            cur = con.execute(
                "UPDATE match_players SET role=? WHERE match_id=? AND nick=? "
                "AND (role IS NULL OR role='')",
                (role, row["battle_id"], nick))
            changed += cur.rowcount or 0
        for nick, op_id in operators.items():
            cur = con.execute(
                "UPDATE match_players SET operator_id=? WHERE match_id=? AND nick=? "
                "AND operator_id IS NULL",
                (op_id, row["battle_id"], nick))
            changed += cur.rowcount or 0
    return changed


def _migrate(con: sqlite3.Connection) -> None:
    """Простые миграции старых баз."""
    # клановый бонус появился позже — добавляем колонку в старые базы
    reward_cols = [row[1] for row in con.execute("PRAGMA table_info(match_rewards)")]
    if reward_cols and "clan_bonus" not in reward_cols:
        con.execute("ALTER TABLE match_rewards ADD COLUMN clan_bonus INTEGER")

    # роль оперативника (Штурмовик/Поддержка/Медик/Снайпер) — тоже позже
    player_cols = [row[1] for row in con.execute("PRAGMA table_info(match_players)")]
    if player_cols and "role" not in player_cols:
        con.execute("ALTER TABLE match_players ADD COLUMN role TEXT")
    if player_cols and "operator_id" not in player_cols:
        con.execute("ALTER TABLE match_players ADD COLUMN operator_id INTEGER")


def save_loadout(con: sqlite3.Connection, match_id: str, uid: int,
                 nick: Optional[str], loadout: dict) -> None:
    """Снаряжение одного игрока (из реплея)."""
    con.execute("INSERT OR REPLACE INTO match_loadout(match_id, uid, nick, loadout_json) "
                "VALUES (?,?,?,?)",
                (match_id, int(uid), nick, json.dumps(loadout, ensure_ascii=False)))


def get_loadout(con: sqlite3.Connection, match_id: str, uid: int) -> Optional[dict]:
    row = con.execute("SELECT loadout_json FROM match_loadout WHERE match_id=? AND uid=?",
                      (match_id, int(uid))).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["loadout_json"])
    except Exception:
        return None


def loadouts_count(con: sqlite3.Connection, match_id: str) -> int:
    return con.execute("SELECT COUNT(*) c FROM match_loadout WHERE match_id=?",
                       (match_id,)).fetchone()["c"]


def connect(path: str = DB_PATH, same_thread_only: bool = True) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # автокоммит: сборщик и хаб работают одновременно, долгие транзакции
    # держат блокировку и мешают друг другу
    con = sqlite3.connect(path, timeout=30, check_same_thread=same_thread_only,
                          isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.executescript(SCHEMA)
    _migrate(con)
    backfill_pve_teams(con)
    backfill_roles(con)
    return con


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_meta(con: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def backfill_my(con: sqlite3.Connection, nick: Optional[str] = None) -> int:
    """Проставляет «мой» uid и команду в матчах, где они не были известны
    (например, архив импортирован до первого логина)."""
    nick = nick or get_meta(con, "my_nick")
    if not nick:
        return 0
    cur = con.execute(
        "UPDATE matches SET my_uid = ("
        "  SELECT p.uid FROM match_players p WHERE p.match_id=matches.id AND p.nick=? LIMIT 1"
        ") WHERE my_uid IS NULL", (nick,))
    changed = cur.rowcount or 0
    con.execute(
        "UPDATE matches SET my_team = ("
        "  SELECT p.team FROM match_players p WHERE p.match_id=matches.id AND p.uid=matches.my_uid"
        ") WHERE my_team IS NULL AND my_uid IS NOT NULL")
    return changed


# PvE-режимы: все игроки в одной команде, отдельных записей TeamId сервер не даёт.
# 33 — «Тени ужаса» (хэллоуинский Натиск, 6 бойцов), по командам он тоже PvE.
PVE_F4 = (1, 3, 14, 33, 37)


def backfill_pve_teams(con: sqlite3.Connection) -> None:
    """В PvE команда всегда одна — проставляем 0, чтобы считался результат боя."""
    con.execute(
        "UPDATE match_players SET team=0 WHERE team IS NULL AND match_id IN "
        "(SELECT id FROM matches WHERE f4 IN (1,3,14,33,37))")
    con.execute(
        "UPDATE matches SET my_team=0 WHERE my_team IS NULL AND f4 IN (1,3,14,33,37) "
        "AND my_uid IS NOT NULL AND EXISTS (SELECT 1 FROM match_players p "
        "WHERE p.match_id=matches.id AND p.uid=matches.my_uid)")


def epoch_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def iso_to_epoch(text: Optional[str]) -> Optional[float]:
    """ISO-время из игры -> epoch. «2026-10-21T17:50:07.4403708Z» это UTC."""
    return parse_iso_ts(text)


def upsert_nick(con: sqlite3.Connection, uid, nick: str, ts: float) -> None:
    if not uid or not nick or not isinstance(nick, str) or len(nick) < 2:
        return
    con.execute(
        "INSERT INTO nicknames(uid, nick, first_seen, last_seen) VALUES(?,?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET nick=excluded.nick, last_seen=excluded.last_seen",
        (int(uid), nick, ts, ts))


def save_account(con: sqlite3.Connection, uid, nick: str, ts: float) -> None:
    """Запоминает аккаунт, под которым заходили (для мультисессии)."""
    if not uid:
        return
    con.execute(
        "INSERT INTO accounts(uid, nick, first_seen, last_seen) VALUES(?,?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET nick=excluded.nick, last_seen=excluded.last_seen",
        (int(uid), nick or "", ts, ts))
    upsert_nick(con, uid, nick, ts)


def save_api_payload(con: sqlite3.Connection, endpoint: str, ts: float,
                     payload, max_len: int = 400_000) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_len:
        return
    import hashlib
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    con.execute("INSERT OR IGNORE INTO api_log(endpoint, ts, hash, payload) VALUES(?,?,?,?)",
                (endpoint, ts, digest, text))


def save_error(con: sqlite3.Connection, ts: float, message: str) -> None:
    """Ошибки схлопываем по минуте: одинаковый текст в одну минуту — одна запись."""
    import re
    message = re.sub(r"^\[[^\]]+\]\s*", "", message.strip())[:500]
    if not message:
        return
    con.execute("INSERT OR IGNORE INTO errors(ts, message) VALUES(?,?)",
                (float(int(ts // 60) * 60), message))


def save_loading(con: sqlite3.Connection, ts: float, map_name: str,
                 mode_map: str, seconds: float) -> None:
    con.execute("INSERT OR IGNORE INTO loading(ts, map, mode_map, seconds) VALUES(?,?,?,?)",
                (round(ts, 1), map_name, mode_map, seconds))


def parse_iso_ts(value) -> Optional[float]:
    """Разбирает ISO-время из игры («2026-07-15T18:19:30.9694162Z»)."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)   # из 7 долей секунды делаем 6
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def save_match(con: sqlite3.Connection, data: dict, source: str = "log") -> Optional[str]:
    """Разбирает протокол боя (Log + Users) и пишет его в базу. Возвращает id матча."""
    log = data.get("Log") or {}
    inner = log.get("Data") or {}
    match_id = inner.get("0")
    if not match_id:
        return None

    players_meta = inner.get("7") or []
    combat = log.get("Users") or []
    rewards = data.get("Users") or []

    map_mode = inner.get("2") or ""
    mode = map_mode.rsplit("_", 1)[-1] if "_" in map_mode else (map_mode or "")
    f4 = inner.get("4")
    result_ts = data.get("ResultTime")
    ts_epoch = iso_to_epoch(result_ts)

    team_map = {}
    for key in ("PvEModeEntries", "PvPvEModeEntries", "PvPModeEntries", "InterceptModeEntries"):
        for entry in (log.get(key) or []):
            if isinstance(entry, dict) and "UserId" in entry:
                team_map[entry["UserId"]] = entry.get("TeamId")

    my_uid = get_meta(con, "my_uid")
    my_uid = int(my_uid) if my_uid and str(my_uid).isdigit() else None
    my_team = None
    my_nick = get_meta(con, "my_nick")

    # роли и оперативники участников: из архива (PlayerRoles) либо из подготовки боя
    lobby_row = con.execute("SELECT teams_json FROM lobby WHERE battle_id=?",
                            (match_id,)).fetchone()
    teams_json = lobby_row["teams_json"] if lobby_row else None
    roles = data.get("PlayerRoles")
    if not isinstance(roles, dict) or not roles:
        roles = _roles_from_teams_json(teams_json)
    roles = {nick: (value.get("role") if isinstance(value, dict) else value)
             for nick, value in roles.items() if nick}
    operators = _operators_from_teams_json(teams_json)

    con.execute(
        "INSERT INTO matches(id, result_ts, ts_epoch, mode, f4, mode_name, map, map_mode, "
        "region, server, duration_s, mission_start, winner_team, my_uid, my_team, "
        "rounds_json, onslaught_json, segments_json, players_count, source) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET result_ts=excluded.result_ts, ts_epoch=excluded.ts_epoch, "
        "duration_s=excluded.duration_s, winner_team=excluded.winner_team, "
        "my_uid=excluded.my_uid, my_team=excluded.my_team, source=excluded.source",
        (match_id, result_ts, ts_epoch, mode, f4, F4_NAMES.get(f4),
         inner.get("1"), map_mode, inner.get("6"), inner.get("11"),
         log.get("MatchTimeSeconds"), log.get("MissionStartTime"),
         log.get("WinnerTeamId"), my_uid, None,
         json.dumps(log.get("Rounds"), ensure_ascii=False),
         json.dumps(log.get("OnslaughtRounds") or log.get("HalloweenOnslaughtRounds"), ensure_ascii=False),
         json.dumps(log.get("Segments"), ensure_ascii=False),
         len(players_meta), source))

    for idx, meta in enumerate(players_meta):
        uid = meta.get("0")
        nick = meta.get("2")
        team = team_map.get(uid)
        if team is None and f4 in PVE_F4:
            team = 0
        stats = combat[idx] if idx < len(combat) else {}
        rew = rewards[idx] if idx < len(rewards) else {}
        br = (rew.get("BattleRewards") or {})
        rw = br.get("Rewards") or {}
        bp = rew.get("BattlepassResult") or {}

        if nick:
            upsert_nick(con, uid, nick, ts_epoch or 0.0)
        if my_uid is not None and uid == my_uid:
            my_team = team

        con.execute(
            "INSERT OR REPLACE INTO match_players(match_id, uid, nick, team, role, "
            "operator_id, kills, bot_kills, assists, bot_assists, executions, deaths, "
            "revives, damage_dealt, damage_received, friendly_damage, heal, headshots, "
            "body_hits, leave_time, loaded, connected_finish, first_leave, kick_reason, "
            "ribbons_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (match_id, uid, nick, team, roles.get(nick), operators.get(nick or ""),
             stats.get("PlayerKills"), stats.get("BotKills"),
             stats.get("PlayerAssists"), stats.get("BotKillAssists"),
             stats.get("Executions"), stats.get("Deaths"), stats.get("Revives"),
             stats.get("DamageDealt"), stats.get("DamageReceived"),
             stats.get("FriendlyDamageDealt"), stats.get("HealProvided"),
             stats.get("HeadshotBulletHits"), stats.get("BodyBulletHits"),
             stats.get("LeaveTime"), 1 if stats.get("LoadedInMatch") else 0,
             1 if stats.get("ConnectedOnMatchFinish") else 0,
             1 if stats.get("FirstWhoLeave") else 0,
             str(stats.get("KickReason") or ""),
             json.dumps(stats.get("Ribbons") or {}, ensure_ascii=False)))

        con.execute(
            "INSERT OR REPLACE INTO match_rewards(match_id, uid, ribbon_xp, medal_xp, match_xp, "
            "premium, battlepass_base, battlepass_premium, battlepass_before, card_xp, "
            "currencies_json, penalties_json, medals_json, clan_bonus) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (match_id, uid,
             ((rw.get("Ribbon") or {}).get("xp")), ((rw.get("Medal") or {}).get("xp")),
             ((rw.get("MatchReward") or {}).get("xp")), ((rw.get("Premium") or {}).get("xp")),
             bp.get("BaseExperience"), bp.get("PremiumExperience"), bp.get("ExperienceBefore"),
             rew.get("CardXp"),
             json.dumps(rew.get("CurrenciesTaken"), ensure_ascii=False),
             json.dumps(br.get("Penalties"), ensure_ascii=False),
             json.dumps(br.get("Medals"), ensure_ascii=False),
             ((rw.get("ClanBonus") or {}).get("xp"))))

    if my_team is None and my_uid is not None and f4 in PVE_F4:
        my_team = 0
    con.execute("UPDATE matches SET my_team=? WHERE id=?", (my_team, match_id))
    return match_id


def save_profiles(con: sqlite3.Connection, ts: float, payload: dict) -> int:
    """Ответ /v2/userbook/get_profiles: список профилей {"0": {0: uid, 1: nick, ...}}.

    Именно этим запросом клиент догружает ники участников клана, когда открывают
    вкладку клана. Сохраняем ники навсегда — потом они не теряются.
    """
    items = (payload or {}).get("b") or []
    if not isinstance(items, list):
        return 0
    saved = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        prof = item.get("0") if isinstance(item.get("0"), dict) else item
        uid = prof.get("0")
        nick = prof.get("1")
        if uid and isinstance(nick, str) and nick.strip():
            upsert_nick(con, uid, nick, ts)
            saved += 1
    return saved


def save_lobby(con: sqlite3.Connection, ts: float, payload: dict) -> None:
    b = (payload or {}).get("b") or {}
    battle_id = b.get("BattleId")
    if not battle_id:
        return
    teams = b.get("Teams") or []
    players = []
    for team in teams:
        for user in (team.get("Users") or []):
            players.append({"uid": user.get("UserId"), "nick": user.get("Nickname"),
                            "role": user.get("Role"), "team": team.get("TeamId"),
                            "operator_id": operator_id_from_user(user)})
            upsert_nick(con, user.get("UserId"), user.get("Nickname"), ts)
    mission = b.get("Mission") or {}
    selected = mission.get("SelectedMission") or {}
    con.execute(
        "INSERT OR REPLACE INTO lobby(battle_id, ts, mode, mission, map_id, players, teams_json) "
        "VALUES(?,?,?,?,?,?,?)",
        (battle_id, ts, b.get("Mode"),
         (selected.get("MissionId") or ""), (selected.get("MapId") or ""),
         len(players), json.dumps(players, ensure_ascii=False)))

