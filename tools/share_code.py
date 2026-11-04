# -*- coding: utf-8 -*-
"""Код комплекта оперативника: разбор и сборка.

Как устроен код (проверено на живом коде из игры, пересобирается байт в байт):

    base64( raw deflate( JSON ) )

JSON — обычный, без шифрования:

    {"0": 21,                     -- оперативник: индекс в character_cards.json (с нуля)
     "1": {
       "0": 0,                    -- номер варианта/пресета
       "1": [15 чисел],           -- улучшения дерева (индекс в upgrades+upgrades_modules)
       "2": [15 чисел],           -- вторая часть улучшений
       "3": [6 чисел],            -- образы: индекс в skin_groups.json
       "4": [4 числа],            -- навыки: индекс в technologies.json
       "5": [{"0": 744, "1": [681, ...]}],  -- оружие (индекс в items) и модули
       "6": null
     }}

Важно: числа — это П0ЗИЦИИ в списках клиента (порядок ключей в static.bytes),
а не отдельные id. Поэтому код действителен только для того же клиента: после
патча, где поменялся порядок, старые коды могут поехать.

    python tools/share_code.py --code "JY8x..."
    python tools/share_code.py --code "JY8x..." --json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import client_data as cd   # noqa: E402
import detect              # noqa: E402


class Catalog:
    """Списки клиента, в которых лежат числа из кода."""

    def __init__(self, data: dict):
        items = dict(data.get("items.json") or {})
        items.update(data.get("items_custom.json") or {})
        self.items = list(items)
        self.upgrades = list(data.get("upgrades.json") or {}) + \
            list(data.get("upgrades_modules.json") or {})
        self.skins = list(data.get("skin_groups.json") or {})
        self.technologies = list(data.get("technologies.json") or {})
        self.cards = list(data.get("character_cards.json") or {})

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


def load_catalog() -> Catalog:
    import paths
    info = detect.report(paths.load_settings())
    static = detect.static_bytes(info.get("game_dir") or "")
    if not os.path.isfile(str(static)):
        raise SystemExit("[!] не найден static.bytes — запусти игру хотя бы раз")
    return Catalog(cd.load_static(static))


def main() -> int:
    ap = argparse.ArgumentParser(description="Разбор кода комплекта Caliber")
    ap.add_argument("--code", required=True, help="код из окна «Обмен комплектами»")
    ap.add_argument("--json", action="store_true", help="показать исходный JSON")
    ap.add_argument("--roundtrip", action="store_true",
                    help="проверить, что код пересобирается байт в байт")
    args = ap.parse_args()

    payload = decode(args.code)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    catalog = load_catalog()
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
