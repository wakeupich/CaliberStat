# -*- coding: utf-8 -*-
"""Разбор файлов реплея (Replays/*.bytes).

Внутри реплея лежит тот же протокол боя, что игра присылает после матча
(`{"Log": {...}, "Users": [...], "ResultTime": ...}`), плюс отдельные записи
игроков с ником и кодом оперативника. Поэтому реплей можно:
  * открыть и показать итоги боя, не заходя в игру;
  * залить в базу как обычный матч — в том числе пачкой.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import caliber_db as db

PLAYER_RE = re.compile(
    r'\{"0":(\d+),"1":(\d+),"2":"([^"]{1,40})".{0,400}?"8":\{"0":(\d+),"1":"([a-z0-9_]+)"')

# В реплее режим записан строкой (в логе игры на этом месте число)
MODE_TO_F4 = {
    "pve": 1,            # Зачистка
    "pve2": 1,           # Зачистка, новая версия
    "pvehard": 3,        # Спецоперация
    "halloweenonslaught": 33,   # Тени ужаса
}

# Семейство «Натиска»: в реплее режим приходит строкой, число бойцов разделяет
# Легенду (4) и Подкрепление (6).
ONSLAUGHT_MODES = ("onslaught", "onslaught2", "onslaughtextended")


def f4_from_payload(data: dict) -> Optional[int]:
    """Числовой код режима: из протокола, а если там строка — по строке и числу бойцов."""
    log = data.get("Log") or {}
    inner = log.get("Data") or {}
    raw = inner.get("4")
    if isinstance(raw, int):
        return raw
    players = len(inner.get("7") or [])
    mode = str(raw or "").lower()
    if mode in ONSLAUGHT_MODES:
        # «onslaughtextended» = «Натиск: Подкрепление» (подтверждено по бою
        # 04.11.2026 07:46 на карте «Дамба»: в клиенте именно Подкрепление)
        return 37 if players >= 6 else 14
    return MODE_TO_F4.get(mode)


def _extract_balanced(text: str, start: int) -> Optional[str]:
    """JSON-объект с позиции start (с учётом строк и экранирования)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def read_payload(path: str) -> Optional[dict]:
    """Достаёт из файла реплея протокол боя."""
    try:
        text = open(path, "rb").read().decode("latin-1")
    except OSError:
        return None
    for marker in ('{"Log"', '{"Log":'):
        pos = text.find(marker)
        if pos < 0:
            continue
        blob = _extract_balanced(text, pos)
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except Exception:
            continue
        if isinstance(data, dict) and "Log" in data and "Users" in data:
            return data
    return None


def player_codes(path: str) -> dict[str, str]:
    """ник -> код оперативника (из записей игроков в реплее)."""
    try:
        text = open(path, "rb").read().decode("latin-1")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for _uid, _uid2, nick, _card, code in PLAYER_RE.findall(text):
        out.setdefault(nick, code)
    return out


def describe(data: dict, codes: Optional[dict[str, str]] = None) -> dict:
    """Краткое описание боя для окна предпросмотра."""
    log = data.get("Log") or {}
    inner = log.get("Data") or {}
    battle_id = inner.get("0")
    map_mode = inner.get("2") or ""
    f4 = f4_from_payload(data)
    meta = inner.get("7") or []
    combat = log.get("Users") or []
    rewards = data.get("Users") or []
    winner = log.get("WinnerTeamId")
    duration = float(log.get("MatchTimeSeconds") or 0)

    players = []
    for i, m in enumerate(meta):
        nick = m.get("2")
        stats = combat[i] if i < len(combat) else {}
        rew = rewards[i] if i < len(rewards) else {}
        rw = ((rew.get("BattleRewards") or {}).get("Rewards") or {})
        players.append({
            "uid": m.get("0"),
            "nick": nick,
            "team": stats.get("TeamId"),
            "operator": (codes or {}).get(nick or ""),
            "kills": stats.get("PlayerKills", 0),
            "bot_kills": stats.get("BotKills", 0),
            "deaths": stats.get("Deaths", 0),
            "damage": round(float(stats.get("DamageDealt") or 0)),
            "headshots": stats.get("HeadshotBulletHits", 0),
            "body_hits": stats.get("BodyBulletHits", 0),
            "ribbon_xp": ((rw.get("Ribbon") or {}).get("xp")),
            "match_xp": ((rw.get("MatchReward") or {}).get("xp")),
        })

    win = None if winner in (None, -1) else (winner == 0)
    return {
        "battle_id": battle_id,
        "map_mode": map_mode,
        "map": inner.get("1") or "",
        "f4": f4,
        "mode_name": db.F4_NAMES.get(f4) or (map_mode.rsplit("_", 1)[-1] if map_mode else ""),
        "result": "победа" if win else ("поражение" if win is False else "—"),
        "win": win,
        "duration": duration,
        "result_time": data.get("ResultTime"),
        "ts": db.iso_to_epoch(data.get("ResultTime")),
        "players": players,
        "region": inner.get("6"),
        "server": inner.get("11"),
    }


# Имя файла реплея: <uuid боя>_<uid игрока>_<дата>_<время>_<номер>_<часть>.bytes
NAME_UID = re.compile(r"_(\d{3,})_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_")


def my_uid_from_path(path: str) -> Optional[int]:
    """Чей это реплей: uid записавшего берётся из имени файла.

    Проверено на 19 файлах: uid из имени всегда присутствует среди участников
    боя и совпадает с ником записавшего. Это единственный способ понять «мой
    бой», когда реплей принесли с другой машины и лога игры рядом нет.
    """
    m = NAME_UID.search(os.path.basename(path or ""))
    return int(m.group(1)) if m else None


def import_replay(con, path: str, codes: Optional[dict[str, str]] = None) -> Optional[str]:
    """Заливает реплей в базу. Возвращает id матча (или None)."""
    data = read_payload(path)
    if not data:
        return None
    return import_payload(con, data, path=path, codes=codes)


def import_payload(con, data: dict, path: Optional[str] = None,
                   codes: Optional[dict[str, str]] = None) -> Optional[str]:
    """Заливает уже прочитанный протокол боя.

    Отдельно от `import_replay`, чтобы при пачке файлов читать каждый файл
    ровно один раз: протокол боя весит мегабайты.
    """
    match_id = db.save_match(con, data, source="replay")
    if not match_id:
        return None
    # в реплее режим строкой — приводим к числовому коду и заполняем команды
    f4 = f4_from_payload(data)
    if f4:
        con.execute("UPDATE matches SET f4=?, mode_name=? WHERE id=?",
                    (f4, db.F4_NAMES.get(f4), match_id))
        db.backfill_pve_teams(con)
    else:
        # режим не опознан: в f4 не должно попасть строкой, иначе в интерфейсе
        # появится режим с именем вроде «onslaughtextended» вместо числа
        con.execute("UPDATE matches SET f4=NULL, mode_name=NULL WHERE id=?", (match_id,))
    if path:
        codes = codes or player_codes(path)
    # снаряжение: в реплее предметы записаны ключами, поэтому имена читаемые
    try:
        for uid, loadout in loadout_of(data).items():
            db.save_loadout(con, match_id, uid, loadout.get("nick"), loadout)
            # роль в реплее отдельно не приходит — берём её из кода оперативника
            role = db.role_from_operator_code(loadout.get("operator"))
            if role:
                con.execute("UPDATE match_players SET role=? WHERE match_id=? AND uid=? "
                            "AND role IS NULL", (role, match_id, uid))
    except Exception:
        pass
    if codes:
        ids_by_code = {v.get("code"): int(k) for k, v in _catalog().items()
                       if v.get("code")}
        for nick, code in codes.items():
            op_id = ids_by_code.get(code)
            if not op_id:
                continue
            con.execute("UPDATE match_players SET operator_id=? WHERE match_id=? "
                        "AND nick=?", (op_id, match_id, nick))
    if path:
        _apply_my_identity(con, match_id, my_uid_from_path(path), f4,
                           db.iso_to_epoch(data.get("ResultTime")) or 0.0)
    return match_id


def _apply_my_identity(con, match_id: str, uid: Optional[int], f4: Optional[int],
                       ts: float = 0.0) -> None:
    """Проставляет «мой» uid и команду, если их ещё нет (реплей без лога игры).

    uid берём из имени файла и только если такой игрок реально есть в бою —
    иначе лучше пусто, чем чужая статистика.
    """
    if not uid:
        return
    row = con.execute("SELECT nick, team FROM match_players WHERE match_id=? AND uid=?",
                      (match_id, int(uid))).fetchone()
    if not row:
        return
    if con.execute("SELECT my_uid FROM matches WHERE id=?", (match_id,)).fetchone()[0]:
        return          # уже известно из лога — не перебиваем
    team = row["team"]
    if team is None and f4 in db.PVE_F4:
        team = 0
    con.execute("UPDATE matches SET my_uid=?, my_team=? WHERE id=?",
                (int(uid), team, match_id))
    if row["nick"]:
        db.save_account(con, int(uid), row["nick"], ts)


_CATALOG_CACHE: Optional[dict] = None


def _catalog() -> dict:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "operators.json")
        try:
            with open(path, encoding="utf-8") as f:
                _CATALOG_CACHE = {int(k): v for k, v in json.load(f).items()}
        except Exception:
            _CATALOG_CACHE = {}
    return _CATALOG_CACHE


_ITEMS_CACHE: Optional[dict] = None


def item_names() -> dict:
    """Ключ предмета -> русское имя и вид (из item_names.json)."""
    global _ITEMS_CACHE
    if _ITEMS_CACHE is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "item_names.json")
        try:
            with open(path, encoding="utf-8") as f:
                _ITEMS_CACHE = json.load(f) or {}
        except Exception:
            _ITEMS_CACHE = {}
    return _ITEMS_CACHE


SLOT_TITLES = {"PrimaryWeapon": "Основное", "SecondaryWeapon": "Второе",
               "HeavyWeapon": "Тяжёлое"}


def loadout_of(data: dict) -> dict[int, dict]:
    """Снаряжение игроков из протокола реплея (ключи предметов, не числа).

    В реплее, в отличие от ответа `battlepreparation`, предметы записаны
    строковыми ключами (`sso2013_ak105b`, `ACOGTA01NSN_top`), поэтому их можно
    сразу показать по-русски.
    """
    log = data.get("Log") or {}
    inner = log.get("Data") or {}
    out: dict[int, dict] = {}
    for meta in inner.get("7") or []:
        uid = meta.get("0")
        card = meta.get("8") or {}
        # снаряжение лежит внутри карточки: 8 -> 24
        loadout = (card or {}).get("24")
        if not isinstance(loadout, dict):
            loadout = {}
        chosen = loadout.get("0") or {}
        slots = loadout.get("1") or {}
        weapons = {}
        for slot_title, item in slots.items():
            key = item if isinstance(item, str) else None
            if not key:
                continue
            info = chosen.get(key) or {}
            weapons[slot_title] = {"key": key,
                                   "modules": list(info.get("1") or []),
                                   "skin": info.get("3")}
        if not weapons:
            # слоты могут не прийти отдельной картой — берём всё выбранное
            for key, info in chosen.items():
                if (info or {}).get("0"):
                    weapons[key] = {"key": key,
                                    "modules": list((info or {}).get("1") or []),
                                    "skin": (info or {}).get("3")}
        out[int(uid) if uid is not None else 0] = {
            "nick": meta.get("2"),
            "operator": (card or {}).get("1"),
            # уровень карточки оперативника и престиж — разные поля.
            # В итогах боя игра рисует нашивку: жёлтую с престижем, если он есть,
            # иначе чёрную с уровнем. Сверено по скриншоту боя 04.11 (Дамба),
            # 6 бойцов из 6: 8.18 = уровень (15/15/12/15/15/5),
            # 8.19 = престиж (0/8/0/0/1/0).
            "card_level": (card or {}).get("18"),
            "prestige": (card or {}).get("19"),
            # уровень аккаунта игрока — число в белом квадрате на экране боя
            "account_level": meta.get("3"),
            "weapons": weapons,
            "perks": list((card or {}).get("15") or []),
            # уровни навыков лежат не в карточке, а рядом с игроком (поле 20):
            # {"booster_pouch": 2, ...} — 0 не взят, 1 и 2 — ступени прокачки
            "perk_levels": dict(meta.get("20") or {}),
            "consumables": list((card or {}).get("14") or []),
            "skins": list((card or {}).get("9") or []),
            "fatality": (card or {}).get("13"),
            "backstab": (card or {}).get("22"),
        }
    return out


def loadout_titles(loadout: dict) -> dict:
    """То же снаряжение, но с русскими названиями — для показа."""
    names = item_names()

    def title(key) -> str:
        if not key:
            return "—"
        info = names.get(key) or names.get(str(key).lower()) or {}
        return info.get("name") or str(key)

    weapons = []
    for slot, info in (loadout.get("weapons") or {}).items():
        weapons.append({
            "slot": SLOT_TITLES.get(slot, slot),
            "name": title(info.get("key")),
            "key": info.get("key"),
            "modules": [title(m) for m in (info.get("modules") or [])],
            "skin": title(info.get("skin")) if info.get("skin") else None,
        })
    return {
        "nick": loadout.get("nick"),
        "operator": loadout.get("operator"),
        "weapons": weapons,
        "perks": [title(p) for p in loadout.get("perks") or []],
        "consumables": [title(c) for c in loadout.get("consumables") or []],
        "skins": [title(s) for s in loadout.get("skins") or []],
        "fatality": title(loadout.get("fatality")) if loadout.get("fatality") else None,
    }
