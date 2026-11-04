# -*- coding: utf-8 -*-
"""Чтение справочников из базы: как показать ключ из реплея по-человечески.

Справочники лежат в таблицах `ref_*` и обновляются кнопкой «Обновить
справочники из клиента». Здесь только чтение и правила подстановки:

* карта — в базе хранится без режима и погоды (`lv_zalessye_dam`), а в бою
  приходит `lv_zalessye_dam_default`, поэтому хвост отрезаем;
* скин — в бою `PatternCobraScaleB_Gear`, в клиенте `SKIN_PATTERNCOBRASCALEB`;
* всё остальное ищем как есть, затем без учёта регистра.

Нет названия — возвращаем исходный ключ. Врать «красивым» именем нельзя:
лучше видеть `fr2_custom`, чем чужое оружие.

    python refs.py            # что загрузилось и сколько
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import client_data
import paths


class Refs:
    def __init__(self, con: sqlite3.Connection):
        self.maps = {row[0]: row[1] for row in con.execute("SELECT key, name FROM ref_map")}
        self.items = {}
        for key, name, kind in con.execute("SELECT key, name, kind FROM ref_item"):
            self.items[key] = (name, kind)
        self.items_lower = {key.lower(): value for key, value in self.items.items()}
        self.operators = {}
        for row in con.execute("SELECT code, name, role, collection_ru, operator_id "
                               "FROM ref_operator"):
            self.operators[row[0]] = {"code": row[0], "name": row[1], "role": row[2],
                                      "collection": row[3], "operator_id": row[4]}
        self.medals = {r[0]: r[1] for r in con.execute("SELECT code, name FROM ref_medal")}
        self.ribbons = {r[0]: r[1] for r in con.execute("SELECT code, name FROM ref_ribbon")}
        self.meta = {r[0]: r[1] for r in con.execute("SELECT key, value FROM ref_meta")}

    # -- готовность ----------------------------------------------------
    @property
    def ready(self) -> bool:
        return bool(self.items or self.maps or self.operators)

    @property
    def counts(self) -> dict:
        return {"карт": len(self.maps), "оперативников": len(self.operators),
                "предметов": len(self.items), "медалей": len(self.medals),
                "лент": len(self.ribbons)}

    # -- подстановки ---------------------------------------------------
    def item(self, key) -> str:
        """Оружие, модуль, расходник, навык — по ключу из реплея."""
        if key in (None, ""):
            return "—"
        text = str(key)
        found = self.items.get(text) or self.items_lower.get(text.lower())
        return found[0] if found else text

    def skin(self, key) -> str:
        """Скин: в бою ключ со слотом, в клиенте — без него.

        `PatternCobraScaleB_Gear` -> `SKIN_PATTERNCOBRASCALEB`,
        `FtTropical_Suit_Full` -> `SKIN_FTTROPICAL`. Поэтому отрезаем хвост
        по одному сегменту, пока не найдём строку в клиенте.
        """
        if key in (None, ""):
            return "—"
        text = str(key)
        found = self.items.get(text) or self.items_lower.get(text.lower())
        if found:
            return found[0]
        parts = text.split("_")
        for cut in range(len(parts) - 1, 0, -1):
            base = "_".join(parts[:cut]).upper()
            found = self.items.get(f"SKIN::{base}")
            if found:
                return found[0]
        return text

    def animation(self, key) -> str:
        """Казнь или добивание: ANIMATION_AC_Fatality_... в клиенте."""
        if key in (None, ""):
            return "—"
        text = str(key)
        found = self.items.get(text)
        return found[0] if found else text

    def map(self, key) -> str:
        """Название карты: в базе оно без режима и погоды."""
        if key in (None, ""):
            return "—"
        text = str(key)
        if text in self.maps:
            return self.maps[text]
        base = client_data.strip_map_suffixes(text)
        if base in self.maps:
            return self.maps[base]
        lowered = {k.lower(): v for k, v in self.maps.items()}
        return lowered.get(base.lower()) or text

    def operator(self, code) -> str:
        """Имя оперативника по коду; если в клиенте имени нет — сам код."""
        if code in (None, ""):
            return "—"
        info = self.operators.get(str(code))
        return (info or {}).get("name") or str(code)

    def operator_info(self, code) -> dict:
        return self.operators.get(str(code or ""), {})

    def medal(self, code) -> str:
        if code in (None, ""):
            return "—"
        return self.medals.get(str(code).lower()) or str(code)

    def ribbon(self, code) -> str:
        if code in (None, ""):
            return "—"
        return self.ribbons.get(str(code).lower()) or str(code)

    def updated_text(self) -> str:
        import datetime
        stamp = self.meta.get("updated_at")
        if not stamp:
            return "справочники ещё не загружались"
        when = datetime.datetime.fromtimestamp(int(stamp)).strftime("%d.%m.%Y %H:%M")
        return f"обновлено {when} · " + " · ".join(
            f"{name}: {self.meta.get(key, '?')}"
            for key, name in (("maps", "карт"), ("operators", "оперативников"),
                              ("items", "предметов")))


_CACHE: Optional[Refs] = None


def get(con: sqlite3.Connection, reload: bool = False) -> Refs:
    global _CACHE
    if _CACHE is None or reload:
        _CACHE = Refs(con)
    return _CACHE


if __name__ == "__main__":
    import caliber_db as db
    con = db.connect(str(paths.db_path()))
    refs = Refs(con)
    print(refs.counts)
    print(refs.updated_text())
    print("карта:", refs.map("lv_zalessye_dam_default"))
    print("оперативник:", refs.operator("ksk2011s"))
    print("оружие:", refs.item("fr2_custom"))
    print("скин:", refs.skin("PatternCobraScaleB_Gear"))
    print("казнь:", refs.animation("AC_Fatality_W1_Stom"))
