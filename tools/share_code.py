# -*- coding: utf-8 -*-
"""Код комплекта оперативника: разбор, сборка и генерация из боя.

Как устроен код (проверено на живом коде из игры, пересобирается байт в байт):

    base64( raw deflate( JSON ) )

JSON — обычный, без шифрования:

    {"0": 21,                     -- оперативник: индекс в character_cards.json (с нуля)
     "1": {
       "0": 0,                    -- номер варианта/пресета
       "1": [15 чисел],           -- дерево прокачки: выбор узла на уровнях 1–15
       "2": [15 чисел],           -- вторая часть улучшений (на сборку не влияет)
       "3": [6 чисел],            -- образы: комплект + скины оружия (skin_groups.json)
       "4": [4 числа],            -- навыки: индекс в technologies.json
       "5": [{"0": 744, "1": [681, ...]}],  -- оружие (индекс в items) и модули
       "6": null
     }}

Важно: числа — это П0ЗИЦИИ в списках клиента (порядок ключей в static.bytes),
а не отдельные id. Поэтому код действителен только для того же клиента: после
патча, где поменялся порядок, старые коды могут поехать.

Чего в реплее нет: какой узел дерева игрок выбрал на каждом уровне. При сборке
кода из боя на каждый уровень ставится первый доступный узел его карточки —
валидный, но не обязательно личный выбор игрока.

    python tools/share_code.py --code "JY8x..."
    python tools/share_code.py --code "JY8x..." --json
    python tools/share_code.py --from-replay battle.bytes            # коды всех в бою
    python tools/share_code.py --from-replay battle.bytes --nick Курт
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import client_data as cd   # noqa: E402
import detect              # noqa: E402

# Сколько узлов дерева в коде: по одному выбору на уровень карточки 1–15
TREE_LEVELS = 15
# Слоты, которые попадают в код: тяжёлое оружие в комплект не входит
CODE_SLOTS = ("PrimaryWeapon", "SecondaryWeapon")
# «Stock_*» — заводской образ, в коде он не отдельной строкой
STOCK_PREFIX = "Stock_"


class Catalog:
    """Списки клиента, в которых лежат числа из кода, и узлы дерева."""

    def __init__(self, data: dict):
        items = dict(data.get("items.json") or {})
        items.update(data.get("items_custom.json") or {})
        self.items = list(items)
        self.upgrades = list(data.get("upgrades.json") or {}) + \
            list(data.get("upgrades_modules.json") or {})
        self.skins = list(data.get("skin_groups.json") or {})
        self.technologies = list(data.get("technologies.json") or {})
        self.cards = list(data.get("character_cards.json") or {})
        self.slots = data.get("card_level_slots.json") or {}

    @staticmethod
    def _at(seq: list, index) -> str:
        try:
            return seq[int(index)]
        except (IndexError, TypeError, ValueError):
            return f"?{index}"

    def operator(self, index) -> str:
        return self._at(self.cards, index)

    def item(self, index) -> str:
        return self._at(self.items, index)

    def upgrade(self, index) -> str:
        return self._at(self.upgrades, index)

    def skin(self, index) -> str:
        return self._at(self.skins, index)

    def technology(self, index) -> str:
        return self._at(self.technologies, index)


def decode(code: str) -> dict:
    """Код -> JSON комплекта."""
    raw = base64.b64decode(code.strip())
    body = zlib.decompressobj(-15).decompress(raw)
    return json.loads(body)


def encode(payload: dict) -> str:
    """JSON комплекта -> код (тот же raw deflate + base64)."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = comp.compress(body) + comp.flush()
    return base64.b64encode(raw).decode("ascii")


def default_tree(catalog: Catalog, card: str) -> list[str]:
    """Узлы дерева по умолчанию: первый доступный на каждом уровне 1–15.

    Игра на каждом уровне карточки предлагает 1–3 узла; какой выбрал игрок, в
    реплее не передаётся. Берём первый по порядку — это валидный узел его же
    карточки, поэтому клиент принимает такой комплект.
    """
    pattern = re.compile(rf"^{re.escape(str(card))}_tree_slot_(\d+)_(\d+)$")
    by_level: dict[int, list[tuple[int, str]]] = {}
    for key, value in catalog.slots.items():
        match = pattern.match(key)
        if not match:
            continue
        level, slot = int(match.group(1)), int(match.group(2))
        upgrades = (value or {}).get("Upgrades") or []
        if upgrades:
            by_level.setdefault(level, []).append((slot, upgrades[0]))
    ordered = []
    for level in sorted(by_level):
        ordered.append(by_level[level][0][1])
    return ordered[:TREE_LEVELS]


def _position(seq: list, key, label: str, warnings: list[str]):
    """Позиция ключа в списке клиента; None и предупреждение, если ключа нет."""
    try:
        return seq.index(key)
    except (ValueError, TypeError):
        warnings.append(f"{label} нет в клиенте: {key}")
        return None


def build_payload(loadout: dict, catalog: Catalog,
                  template: dict | None = None) -> tuple[dict, list[str]]:
    """Снаряжение из боя -> JSON комплекта.

    `template` — уже разобранный код того же оперативника: из него берётся
    дерево и поле «2», которые в реплее отсутствуют. Без шаблона дерево
    собирается по умолчанию, поле «2» остаётся пустым.

    Возвращает (payload, предупреждения о том, что не нашлось в клиенте).
    """
    warnings: list[str] = []
    card = loadout.get("operator")
    card_index = _position(catalog.cards, card, "оперативник", warnings)
    if card_index is None:
        raise ValueError(f"оперативник «{card}» не найден в файлах клиента — "
                         f"обнови справочники")

    weapons = []
    for slot in CODE_SLOTS:
        info = (loadout.get("weapons") or {}).get(slot) or {}
        key = info.get("key")
        if not key:
            continue
        item_index = _position(catalog.items, key, "оружие", warnings)
        if item_index is None:
            continue
        modules = []
        for module in info.get("modules") or []:
            index = _position(catalog.upgrades, module, "модуль", warnings)
            if index is not None:
                modules.append(index)
        weapons.append({"0": item_index, "1": modules})

    skins = []
    for key in loadout.get("skins") or []:
        index = _position(catalog.skins, key, "образ", warnings)
        if index is not None:
            skins.append(index)
    for slot in CODE_SLOTS:
        info = (loadout.get("weapons") or {}).get(slot) or {}
        key = info.get("skin")
        if not key or str(key).startswith(STOCK_PREFIX):
            continue
        index = _position(catalog.skins, key, "скин оружия", warnings)
        if index is not None:
            skins.append(index)

    perks = []
    for perk in loadout.get("perks") or []:
        if not perk:
            continue
        index = _position(catalog.technologies, perk, "навык", warnings)
        if index is not None:
            perks.append(index)

    if template:
        tree = list((template.get("1") or {}).get("1") or [])
        second = list((template.get("1") or {}).get("2") or [])
        variant = (template.get("1") or {}).get("0", 0)
        field6 = (template.get("1") or {}).get("6")
    else:
        tree = [i for i in (_position(catalog.upgrades, u, "узел дерева", warnings)
                            for u in default_tree(catalog, card)) if i is not None]
        second, variant, field6 = [], 0, None
        if len(tree) < TREE_LEVELS:
            warnings.append(f"дерево: узлов {len(tree)} из {TREE_LEVELS} — "
                            f"уровни выше не открыты")

    payload = {"0": card_index,
               "1": {"0": variant, "1": tree, "2": second,
                     "3": skins, "4": perks, "5": weapons, "6": field6}}
    return payload, warnings


def code_from_loadout(loadout: dict, catalog: Catalog,
                      template: dict | None = None) -> tuple[str, dict, list[str]]:
    """Снаряжение из боя -> готовый код для клиента."""
    payload, warnings = build_payload(loadout, catalog, template=template)
    return encode(payload), payload, warnings


def describe(payload: dict, catalog: Catalog) -> dict:
    """Разложить код на понятные части."""
    inner = payload.get("1") or {}
    weapons = []
    for weapon in inner.get("5") or []:
        weapons.append({
            "weapon": catalog.item(weapon.get("0")),
            "weapon_index": weapon.get("0"),
            "modules": [catalog.upgrade(m) for m in weapon.get("1") or []],
        })
    return {
        "оперативник": catalog.operator(payload.get("0")),
        "оперативник_индекс": payload.get("0"),
        "вариант": inner.get("0"),
        "навыки": [catalog.technology(n) for n in inner.get("4") or []],
        "оружие": weapons,
        "образы": [catalog.skin(n) for n in inner.get("3") or []],
        "улучшения_1": [catalog.upgrade(n) for n in inner.get("1") or []],
        "улучшения_2": [catalog.upgrade(n) for n in inner.get("2") or []],
        "поле_6": inner.get("6"),
    }


class CatalogError(RuntimeError):
    """Файлы клиента не нашлись или не читаются — сказать это человеку."""


def load_catalog() -> Catalog:
    """Списки клиента для подстановки имён и поиска позиций."""
    import paths
    info = detect.report(paths.load_settings())
    static = detect.static_bytes(info.get("game_dir") or "")
    if not os.path.isfile(str(static)):
        raise CatalogError("не найден static.bytes клиента — запусти игру хотя бы раз "
                           "или укажи папку клиента в настройках")
    try:
        data = cd.load_static(static)
    except Exception as error:
        raise CatalogError(f"static.bytes не читается: {error}") from error
    return Catalog(data)


def _catalog_or_exit() -> Catalog:
    try:
        return load_catalog()
    except CatalogError as error:
        print(f"[!] {error}")
        raise SystemExit(1)


def _print_code(nick: str, loadout: dict, catalog: Catalog) -> None:
    code, payload, warnings = code_from_loadout(loadout, catalog)
    print(f"\n{nick or '—'} / {loadout.get('operator')} "
          f"(уровень {loadout.get('card_level') or '—'}): {len(code)} символов")
    print(code)
    for warning in warnings:
        print(f"    [!] {warning}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Разбор и сборка кода комплекта Caliber")
    ap.add_argument("--code", help="код из окна «Обмен комплектами»")
    ap.add_argument("--from-replay", help="файл реплея: собрать коды участников")
    ap.add_argument("--nick", help="только этот игрок (с --from-replay)")
    ap.add_argument("--json", action="store_true", help="показать исходный JSON")
    ap.add_argument("--roundtrip", action="store_true",
                    help="проверить, что код пересобирается байт в байт")
    args = ap.parse_args()

    if args.from_replay:
        import replay
        players = replay.loadout_of(replay.read_payload(args.from_replay) or {})
        if not players:
            print("[!] в реплее нет карточек игроков")
            return 1
        catalog = _catalog_or_exit()
        chosen = [p for p in players.values()
                  if not args.nick or p.get("nick") == args.nick]
        if not chosen:
            print(f"[!] игрока «{args.nick}» в бою нет")
            return 1
        for loadout in chosen:
            _print_code(loadout.get("nick"), loadout, catalog)
        return 0

    if not args.code:
        ap.error("нужен либо --code, либо --from-replay")

    payload = decode(args.code)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    catalog = _catalog_or_exit()
    info = describe(payload, catalog)
    print(f"оперативник: {info['оперативник']} (индекс {info['оперативник_индекс']})")
    print(f"навыки: {', '.join(info['навыки']) or '—'}")
    for weapon in info["оружие"]:
        print(f"оружие: {weapon['weapon']}")
        for module in weapon["modules"]:
            print(f"    модуль: {module}")
    print(f"образы: {', '.join(info['образы']) or '—'}")
    print(f"улучшения (поле 1): {len(info['улучшения_1'])}")
    for name in info["улучшения_1"]:
        print(f"    {name}")
    print(f"улучшения (поле 2): {len(info['улучшения_2'])}")
    for name in info["улучшения_2"]:
        print(f"    {name}")
    if args.roundtrip:
        same = encode(payload) == args.code.strip()
        print(f"\nпересборка байт в байт: {'да' if same else 'НЕТ'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
