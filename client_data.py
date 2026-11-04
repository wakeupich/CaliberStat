# -*- coding: utf-8 -*-
"""Справочники из файлов клиента: карты, оперативники, оружие, модули, навыки.

Источники (только чтение, игру не трогаем):

* `resources.assets` — локализация: пары `msgid "..." msgstr "..."`;
* `static.bytes` — zlib+JSON с настройками: карточки оперативников, предметы,
  модули, технологии.

Названия берутся из локализации клиента, а не из догадок:

* карта — `MISSIONS_<id карты>_<режим>`, режим и погода из id отрезаются;
* оперативник — `COLLECTIONS_PL_<коллекция>_<роль>` (роль одной буквой: A/G/M/S);
* оружие — `<visual>_SHORT`, модули — `UPGRADES_<ключ>_SHORT`;
* навыки — `QUARTERS_TECHNOLOGIES_<КЛЮЧ>`;
* расходники — `CONSUMABLES_<КЛЮЧ>`; скины — `SKIN_<ключ>`.

Обновление ручное (кнопка «Обновить справочники из клиента»): после патча игры
пользователь жмёт её, и новые пушки/карты/оперативники подтягиваются без правок
кода. Запись идёт одной транзакцией: либо обновилось всё, либо ничего.

    python client_data.py            # обновить и показать отчёт
    python client_data.py --dry-run  # только посчитать, без записи
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Optional

import caliber_db as db
import detect

# Порядок шагов для прогресса в окне
STEPS = (
    "ищем клиент и static.bytes",
    "читаем локализацию клиента",
    "распаковываем static.bytes",
    "оперативники и карты",
    "предметы, модули, навыки, скины",
    "записываем в базу",
)

# Режимы, которые в ключах локализации идут после id карты
MAP_MODES = ("pvpve2", "pvpve", "onslaught2", "onslaughtnormal", "onslaugthard",
             "onslaughtextended", "halloweenonslaught", "onslaught", "hackingranked",
             "hacking", "pvpranked", "pvpdestructionranked", "pvpdestruction",
             "pvptdm", "pvpduel", "pvp16", "pvpfpp", "pvp", "pvehard", "pve",
             "polygon", "intercept", "custom", "training")

WEATHERS = ("default", "aurora", "evening", "twilight", "night", "day", "dusk",
            "dawn", "sunset", "sunrise", "morning", "dark", "storm", "sandstorm",
            "winter", "snow", "fog", "rain", "overcast", "9may")

WEAPON_CLASSES = {
    "AssaultRifle": "Автомат", "SniperRifle": "Снайперская винтовка",
    "LMG": "Пулемёт", "SMG": "Пистолет-пулемёт", "Shotgun": "Дробовик",
    "Pistol": "Пистолет", "Melee": "Ближний бой", "Explosive": "Взрывчатка",
    "Common": "Прочее",
}
WEAPON_TYPES = tuple(WEAPON_CLASSES)

CONSUMABLES = ("HealthPack", "ArmorPack", "HealthPackV2", "ArmorPackV2",
               "HealthPackV3", "ArmorPackV3", "Adrenaline", "SmokeGrenade",
               "AmmoPack", "StaminaRegenBooster", "SilverBullets", "EnergyDome",
               "GoldArmor", "HolyWater")

LOC_LINE = re.compile(r'msgid\s+"([^"]+)"\s+msgstr\s+"([^"]+)"')
RU = re.compile(r"[А-Яа-яЁё]")


class ClientDataError(RuntimeError):
    """Справочники собрать не удалось — причина понятна человеку."""


# -- чтение файлов клиента -------------------------------------------------
def find_assets(game_dir: str) -> Optional[Path]:
    """`resources.assets` в папке клиента."""
    if not game_dir:
        return None
    direct = Path(game_dir) / "Game" / "Data" / "resources.assets"
    if direct.is_file():
        return direct
    for root, _dirs, files in os.walk(game_dir):
        if "resources.assets" in files:
            return Path(root) / "resources.assets"
    return None


def load_localization(assets: Path) -> dict[str, list[str]]:
    """msgid/msgstr из клиента: ключ -> все варианты перевода."""
    with open(assets, "rb") as f:
        content = f.read().decode("utf-8", errors="ignore")
    groups: dict[str, list[str]] = {}
    for key, value in LOC_LINE.findall(content):
        groups.setdefault(key, []).append(value)
    return groups


def load_static(static_path: Path) -> dict:
    with open(static_path, "rb") as f:
        return json.loads(zlib.decompress(f.read(), zlib.MAX_WBITS | 32))


def clean_text(text: Optional[str]) -> Optional[str]:
    """Убирает разметку: «22<nobr> СпН</nobr>» -> «22 СпН»."""
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


CJK = re.compile(r"[\u4e00-\u9fff]")


def pick_ru(groups: dict[str, list[str]], *keys: str) -> Optional[str]:
    """Первый русский вариант среди ключей, иначе первый без иероглифов.

    В клиенте часть строк продублирована на китайском («海豹» вместо «SEAL»),
    такие названия в интерфейсе хуже, чем код из файла.
    """
    fallback = None
    for key in keys:
        for value in groups.get(key) or []:
            value = clean_text(value)
            if not value:
                continue
            if RU.search(value):
                return value
            if fallback is None and not CJK.search(value):
                fallback = value
    return fallback


def strip_map_suffixes(map_id: str) -> str:
    """Убирает из id карты режим и погоду: lv_x_pve_halloweenonslaught -> lv_x."""
    cleaned = map_id or ""
    changed = True
    while changed:
        changed = False
        for mode in MAP_MODES:
            if cleaned.endswith("_" + mode):
                cleaned = cleaned[:-(len(mode) + 1)]
                changed = True
        for weather in WEATHERS:
            if cleaned.endswith("_" + weather):
                cleaned = cleaned[:-(len(weather) + 1)]
                changed = True
    return cleaned


# -- сборка справочников ---------------------------------------------------
def build_maps(groups: dict[str, list[str]]) -> dict[str, str]:
    """id карты -> название. Одна запись на карту, без режимов и погоды."""
    maps: dict[str, str] = {}
    for key in groups:
        if not key.startswith("MISSIONS_"):
            continue
        rest = key[len("MISSIONS_"):]
        if not rest.startswith("lv_"):
            continue
        mode = next((m for m in MAP_MODES if rest.endswith("_" + m)), None)
        if not mode:
            continue
        base = strip_map_suffixes(rest[:-(len(mode) + 1)])
        if not base or base in maps:
            continue
        name = pick_ru(groups, key)
        if name:
            maps[base] = name
    return maps


def build_operators(groups: dict[str, list[str]], data: dict) -> dict[str, dict]:
    """Код оперативника -> имя, роль, коллекция.

    Код (`ksk2011s`) — то, что приходит в реплее. id оперативника в игре — его
    позиция в `character_cards.json` (с единицы), она тоже сохраняется.
    """
    cards = data.get("character_cards.json") or {}
    out: dict[str, dict] = {}
    for index, (code, card) in enumerate(cards.items(), start=1):
        role_client = card.get("role") or ""
        letter = role_client[0].upper() if role_client else ""
        collection = card.get("collection") or ""
        name = pick_ru(groups, f"COLLECTIONS_PL_{collection.upper()}_{letter}")
        collection_ru = pick_ru(groups, f"COLLECTIONS_{collection.upper()}_SHORT",
                                f"COLLECTIONS_{collection.upper()}")
        out[code] = {
            "operator_id": index,
            "name": name,
            "role": role_client or db.role_from_operator_code(code),
            "collection": collection,
            "collection_ru": collection_ru or collection.replace("_", " ").upper(),
            "enabled": 1 if card.get("enabledInGame", True) else 0,
        }
    return out


def build_items(groups: dict[str, list[str]], data: dict) -> dict[str, dict]:
    """Ключ предмета -> имя. Оружие, модули, расходники, скины, навыки."""
    items = dict(data.get("items.json") or {})
    items.update(data.get("items_custom.json") or {})
    names: dict[str, dict] = {}
    lower_index: dict[str, str] = {}

    def put(key: str, kind: str, *loc_keys: str) -> None:
        if not key or key in names:
            return
        name = pick_ru(groups, *loc_keys)
        if not name:
            return
        names[key] = {"name": name, "kind": kind}
        lower_index.setdefault(key.lower(), key)

    modules: set[str] = set()
    for key, item in items.items():
        default = (item or {}).get("default") or {}
        visual = default.get("visual") or ""
        slot = default.get("slot") or ""
        item_type = default.get("itemType") or ""
        kind = "weapon" if (visual.startswith("WP_") or slot.endswith("Weapon")
                            or item_type in WEAPON_TYPES) else "gear"
        put(key, kind, f"{visual}_SHORT", visual)
        modules.update(default.get("modules") or [])
        modules.update(default.get("upgrades") or [])

    for key in sorted(modules):
        put(key, "module", f"UPGRADES_{key}_SHORT", f"UPGRADES_{key.upper()}_SHORT")
        base = re.sub(r"_(top|front|back|mag|side|underbarrel|pistol_grip|handguard|"
                      r"stock|muzzle|barrel|grip)$", "", key, flags=re.I)
        if base != key:
            put(key, "module", f"UPGRADES_{base}_SHORT",
                f"UPGRADES_{base.upper()}_SHORT", f"UPGRADES_{key}_SHORT")

    for key in CONSUMABLES:
        put(key, "consumable", f"CONSUMABLES_{key.upper()}")
    for key in (data.get("consumables.json") or {}):
        put(key, "consumable", f"CONSUMABLES_{key.upper()}")

    # модули: id лежат в upgrades*.json, имя — по их visual (только короткое имя,
    # длинное описание вроде «Магазин на 20 патронов. Ёмкость…» в таблицу не тащим)
    for source in ("upgrades_modules.json", "upgrades.json"):
        for key, entry in (data.get(source) or {}).items():
            visual = (entry or {}).get("visual") or ""
            if visual:
                put(key, "module", f"UPGRADES_{visual}_SHORT")

    # скины: в реплее ключ идёт со слотом (PatternCobraScaleB_Gear), а в клиенте
    # строка лежит без него — SKIN_PATTERNCOBRASCALEB. Храним по базе без слота,
    # при показе отрезаем последнюю часть после «_».
    for key in groups:
        if key.startswith("SKIN_") and len(key) > 5:
            base = key[len("SKIN_"):]
            name = pick_ru(groups, key)
            if name and base not in ("REWARD_NOTIFICATION_TITLE",):
                names.setdefault(f"SKIN::{base.upper()}",
                                 {"name": name, "kind": "skin"})

    # казни и добивания: ANIMATION_AC_Fatality_W0_Stom -> AC_Fatality_W0_Stom
    for key in groups:
        if key.startswith("ANIMATION_AC_"):
            raw = key[len("ANIMATION_"):]
            name = pick_ru(groups, key)
            if name:
                names.setdefault(raw, {"name": name, "kind": "animation"})

    # технологии (навыки): QUARTERS_TECHNOLOGIES_TAKE_AIM -> take_aim
    for key in groups:
        if key.startswith("QUARTERS_TECHNOLOGIES_") and not key.endswith("_DESCR"):
            raw = key[len("QUARTERS_TECHNOLOGIES_"):].lower()
            name = pick_ru(groups, key)
            if name:
                names.setdefault(raw, {"name": name, "kind": "perk"})
    return names


def build_perk_tree(groups: dict[str, list[str]], data: dict) -> dict[str, dict]:
    """Дерево навыков оперативника: какая технология в какой ветке и на каком месте.

    В клиенте дерево описано четырьмя ветками (`technology_lines.json`), а порядок
    внутри ветки — порядком в списке. Координат узлов в файлах нет: игру рисует
    сетку сама, поэтому дерево показываем ветками, а не картинкой.
    """
    lines = data.get("technology_lines.json") or {}
    tree: dict[str, dict] = {}
    for line, payload in lines.items():
        line_name = pick_ru(groups, f"QUARTERS_TECHNOLOGY_LINES_{line.upper()}") or line
        for pos, tech in enumerate((payload or {}).get("Technologies") or []):
            descr = pick_ru(groups, f"QUARTERS_TECHNOLOGIES_{tech.upper()}_DESCR")
            tree[tech] = {"line": line, "line_name": line_name, "pos": pos,
                          "descr": descr}
    return tree


SLOT_KEY = re.compile(r"^(.+?)_(tree|linear)_slot_(\d+)(?:_(\d+))?$")


def _first_sentence(text: Optional[str], limit: int = 70) -> Optional[str]:
    """Первое предложение описания, укороченное до разумной длины."""
    if not text:
        return None
    head = re.split(r"\.\s", text, maxsplit=1)[0]
    head = clean_text(head) or None
    if head and len(head) > limit:
        head = head[:limit].rstrip(" ,;—-") + "…"
    return head


def upgrade_title(groups: dict[str, list[str]], upgrades: dict, upg_id: str) -> tuple:
    """Название и числовой эффект узла прокачки оперативника.

    У узла в клиенте есть `visual` (например `extra_HP`), а в локализации —
    `<VISUAL>_SHORT` (короткое имя) и `UPGRADES_<VISUAL>` (описание). Число
    берём из `modifiers.ui`: там уже готовое значение для интерфейса (+5 ОЗ,
    −6 с отката).
    """
    info = upgrades.get(upg_id) or {}
    visual = (info.get("visual") or upg_id).upper()
    name = pick_ru(groups, f"{visual}_SHORT")
    if not name:
        name = _first_sentence(pick_ru(groups, f"UPGRADES_{visual}")) or visual
    effect = ""
    ui = ((info.get("modifiers") or {}).get("ui") or {})
    values = [value for value in ui.values() if isinstance(value, (int, float))]
    if values:
        effect = ", ".join(f"{'+' if value > 0 else '−'}{abs(value):g}"
                           for value in values)
    return name, effect


def build_card_tree(groups: dict[str, list[str]], data: dict,
                    items: dict) -> dict:
    """Дерево прокачки оперативника: какой узел на каком уровне открывается.

    `card_level_slots.json` описывает две вещи:

    * `<карта>_tree_slot_<уровень>_<слот>` — узлы прокачки конкретного
      оперативника (здоровье, скорострельность, улучшения способности);
    * `<карта>_linear_slot_<уровень>` — общие уровни: `UnlockTechnologySlot`
      (открывается слот навыка) и выдача конкретных навыков.

    Игра рисует это сеткой с осью 1–15, поэтому и мы показываем по уровням.
    """
    slots = data.get("card_level_slots.json") or {}
    upgrades = data.get("upgrades.json") or {}
    rows: dict[tuple, dict] = {}
    for key, value in slots.items():
        m = SLOT_KEY.match(key)
        if not m or not isinstance(value, dict):
            continue
        card, line, level, slot = m.group(1), m.group(2), int(m.group(3)), int(m.group(4) or 1)
        for upg in value.get("Upgrades") or []:
            name, effect = upgrade_title(groups, upgrades, upg)
            rows[(card, level, slot, upg)] = {"kind": "upgrade", "name": name,
                                              "effect": effect}
        for tech in value.get("Technologies") or []:
            info = items.get(tech) or {}
            rows[(card, level, slot, tech)] = {"kind": "tech",
                                               "name": info.get("name") or tech,
                                               "effect": ""}
        if value.get("UnlockTechnologySlot"):
            rows[(card, level, slot, "slot")] = {"kind": "slot",
                                                 "name": "слот навыка", "effect": ""}
    return rows


def build_medals(groups: dict[str, list[str]]) -> dict[str, str]:
    medals = {}
    for key in groups:
        if key.startswith("MEDALS_") and not key.endswith("_DESCR"):
            name = pick_ru(groups, key)
            if name:
                medals[key[len("MEDALS_"):].lower()] = name
    return medals


def build_ribbons(groups: dict[str, list[str]]) -> dict[str, str]:
    ribbons = {}
    for key in groups:
        if key.startswith("RIBBONS_") and not key.endswith("_DESCR"):
            name = pick_ru(groups, key)
            if name:
                ribbons[key[len("RIBBONS_"):].lower()] = name
    return ribbons


# -- запись ----------------------------------------------------------------
def store(con: sqlite3.Connection, maps: dict, operators: dict, items: dict,
          medals: dict, ribbons: dict, source: str,
          perk_tree: Optional[dict] = None,
          card_tree: Optional[dict] = None) -> dict:
    """Справочники одной транзакцией: либо всё новое, либо всё старое."""
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM ref_map")
        con.executemany("INSERT OR REPLACE INTO ref_map(key, name) VALUES (?,?)",
                        list(maps.items()))
        con.execute("DELETE FROM ref_operator")
        con.executemany(
            "INSERT OR REPLACE INTO ref_operator(code, operator_id, name, role, "
            "collection, collection_ru, enabled) VALUES (?,?,?,?,?,?,?)",
            [(code, info["operator_id"], info["name"], info["role"],
              info["collection"], info["collection_ru"], info["enabled"])
             for code, info in operators.items()])
        con.execute("DELETE FROM ref_item")
        con.executemany("INSERT OR REPLACE INTO ref_item(key, name, kind) VALUES (?,?,?)",
                        [(key, info["name"], info["kind"]) for key, info in items.items()])
        con.execute("DELETE FROM ref_medal")
        con.executemany("INSERT OR REPLACE INTO ref_medal(code, name) VALUES (?,?)",
                        list(medals.items()))
        con.execute("DELETE FROM ref_ribbon")
        con.executemany("INSERT OR REPLACE INTO ref_ribbon(code, name) VALUES (?,?)",
                        list(ribbons.items()))
        con.execute("DELETE FROM ref_perk_line")
        con.executemany(
            "INSERT OR REPLACE INTO ref_perk_line(technology, line, line_name, pos, "
            "descr) VALUES (?,?,?,?,?)",
            [(tech, info["line"], info["line_name"], info["pos"], info["descr"])
             for tech, info in (perk_tree or {}).items()])
        con.execute("DELETE FROM ref_card_tree")
        con.executemany(
            "INSERT OR REPLACE INTO ref_card_tree(card, level, slot, key, kind, name, "
            "effect) VALUES (?,?,?,?,?,?,?)",
            [(card, level, slot, key, info["kind"], info["name"], info["effect"])
             for (card, level, slot, key), info in (card_tree or {}).items()])
        for key, value in (("updated_at", str(int(time.time()))),
                           ("source", source),
                           ("maps", str(len(maps))),
                           ("operators", str(len(operators))),
                           ("items", str(len(items))),
                           ("perks", str(len(perk_tree or {})))):
            con.execute("INSERT OR REPLACE INTO ref_meta(key, value) VALUES (?,?)",
                        (key, value))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"maps": len(maps), "operators": len(operators), "items": len(items),
            "medals": len(medals), "ribbons": len(ribbons),
            "perks": len(perk_tree or {}), "card_tree": len(card_tree or {})}


def update(con: sqlite3.Connection, cfg: Optional[dict] = None,
           progress=None, dry_run: bool = False) -> dict:
    """Полное обновление справочников. `progress(шаг, всего, текст)` — для окна."""
    def step(index: int, text: Optional[str] = None) -> None:
        if progress:
            progress(index, len(STEPS), text or STEPS[index])

    step(0)
    info = detect.report(cfg)
    game_dir = info.get("game_dir") or ""
    assets = find_assets(game_dir)
    static = detect.static_bytes(game_dir)
    if not assets:
        raise ClientDataError(
            "Не нашёл resources.assets в папке клиента.\n"
            f"Папка клиента: {game_dir or 'не определена'}.\n"
            "Укажи её в настройках и повтори.")
    if not Path(static).is_file():
        raise ClientDataError(
            "Не нашёл static.bytes — запусти игру хотя бы раз, клиент его скачает.\n"
            f"Ожидался файл: {static}")

    step(1)
    groups = load_localization(assets)
    if not groups:
        raise ClientDataError("В resources.assets не нашлось строк локализации — "
                              "файл другой или повреждён.")

    step(2)
    data = load_static(Path(static))
    cards = data.get("character_cards.json") or {}
    if not cards:
        raise ClientDataError("В static.bytes нет карточек оперативников — "
                              "формат клиента изменился.")

    step(3)
    maps = build_maps(groups)
    operators = build_operators(groups, data)

    step(4)
    items = build_items(groups, data)
    medals = build_medals(groups)
    ribbons = build_ribbons(groups)
    perk_tree = build_perk_tree(groups, data)
    card_tree = build_card_tree(groups, data, items)

    step(5)
    counts = {"maps": len(maps), "operators": len(operators), "items": len(items),
              "medals": len(medals), "ribbons": len(ribbons),
              "perks": len(perk_tree), "card_tree": len(card_tree)}
    if not dry_run:
        counts = store(con, maps, operators, items, medals, ribbons,
                       source=f"{Path(static).name} · {Path(assets).parent}",
                       perk_tree=perk_tree, card_tree=card_tree)

    # чего не хватает: карты из боёв, для которых клиент не дал названия
    missing = []
    try:
        for row in con.execute("SELECT DISTINCT map FROM matches WHERE map IS NOT NULL"):
            base = strip_map_suffixes(row["map"])
            if base and base not in maps:
                missing.append(row["map"])
    except Exception:
        pass

    counts["missing_maps"] = sorted(set(missing))
    counts["no_name_operators"] = sorted(c for c, o in operators.items() if not o["name"])
    counts["localization_keys"] = len(groups)
    return counts


def summary_text(counts: dict) -> str:
    text = (f"карт: {counts['maps']}, оперативников: {counts['operators']}, "
            f"предметов и навыков: {counts['items']}, "
            f"медалей: {counts['medals']}, лент: {counts['ribbons']}")
    if counts.get("perks"):
        text += f", узлов дерева навыков: {counts['perks']}"
    if counts.get("card_tree"):
        text += f", узлов прокачки оперативников: {counts['card_tree']}"
    if counts.get("no_name_operators"):
        text += f"\nбез имени: {len(counts['no_name_operators'])}"
    if counts.get("missing_maps"):
        text += f"\nкарт без названия: {', '.join(counts['missing_maps'][:6])}"
    return text


def main() -> int:
    import paths

    ap = argparse.ArgumentParser(description="Справочники из файлов клиента")
    ap.add_argument("--dry-run", action="store_true", help="только посчитать")
    args = ap.parse_args()

    paths.ensure_dirs()
    con = db.connect(str(paths.db_path()))
    started = time.perf_counter()

    def show(step: int, total: int, text: str) -> None:
        print(f"  [{step + 1}/{total}] {text}")

    try:
        counts = update(con, paths.load_settings(), progress=show, dry_run=args.dry_run)
    except ClientDataError as e:
        print(f"[!] {e}")
        return 1
    finally:
        con.close()
    print(f"[*] {summary_text(counts)} за {time.perf_counter() - started:.1f} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
