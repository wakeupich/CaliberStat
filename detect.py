# -*- coding: utf-8 -*-
"""Автоопределение лога игры, папки клиента и папки реплеев.

Порядок: сначала то, что указано в настройках вручную, потом автоматика.
Автоматика опирается на факты, а не на догадки:

* лог — стандартный путь Unity-игры `%USERPROFILE%\\AppData\\LocalLow\\1CGS\\Caliber\\Player.log`;
* папка клиента — берётся из самого лога, строка `Command line: <путь>\\Caliber.exe`
  (путь берётся из строки запуска в самом логе);
* реплеи — `<LocalLow>\\1CGS\\Caliber\\Replays` либо `CustomSaveRaplaysDirectory`
  из `replays.json`, если игрок задал свою папку.

    python detect.py --show
"""
from __future__ import annotations

import argparse
import json
import os
import re
import string
from pathlib import Path
from typing import Optional

import paths

LOCAL_LOW = Path(os.path.expandvars(r"%USERPROFILE%\AppData\LocalLow")) / "1CGS" / "Caliber"
DEFAULT_LOG = LOCAL_LOW / "Player.log"
STATIC_BYTES = LOCAL_LOW / "CompressedData" / "client" / "static.bytes"
REPLAYS_JSON = LOCAL_LOW / "Replays" / "replays.json"

CMD_LINE = re.compile(r"Command line:\s*(.+?\.exe)")

# Сколько строк лога просматриваем в поисках пути к клиенту: строка `Command line`
# стоит в первых десятках строк, дальше искать смысла нет.
LOG_HEAD_LINES = 200


def find_in_log(log_path: Path) -> Optional[Path]:
    """Папка клиента из строки запуска в логе."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for index, line in enumerate(f):
                if index > LOG_HEAD_LINES:
                    break
                m = CMD_LINE.search(line)
                if m:
                    exe = Path(m.group(1).strip())
                    if exe.exists():
                        return game_dir_of(exe)
    except OSError:
        return None
    return None


def game_dir_of(exe: Path) -> Path:
    """`...\\Caliber\\Game\\Caliber.exe` -> `...\\Caliber` (папка клиента)."""
    parent = exe.parent
    return parent.parent if parent.name.lower() == "game" else parent


def looks_like_game_dir(folder: Path) -> bool:
    return (folder / "Game" / "Data" / "resources.assets").exists() or \
           (folder / "Game" / "Caliber.exe").exists()


def scan_drives(max_depth: int = 3) -> Optional[Path]:
    """Запасной путь: поиск клиента по дискам. Медленно, поэтому только по кнопке."""
    roots = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:/")
        if drive.exists():
            roots.append(drive)
    for root in roots:
        stack = [(root, 0)]
        while stack:
            folder, depth = stack.pop()
            if depth > max_depth:
                continue
            try:
                for entry in folder.iterdir():
                    if not entry.is_dir():
                        continue
                    name = entry.name.lower()
                    if name == "caliber" and looks_like_game_dir(entry):
                        return entry
                    if name in ("windows", "program files", "program files (x86)",
                                "$recycle.bin", "system volume information",
                                "appdata", "node_modules"):
                        continue
                    stack.append((entry, depth + 1))
            except (OSError, PermissionError):
                continue
    return None


def replays_from_json(default: Path) -> Path:
    """Игра умеет писать реплеи в свою папку — она в replays.json."""
    try:
        with open(REPLAYS_JSON, encoding="utf-8") as f:
            data = json.load(f) or {}
        custom = (data.get("CustomSaveRaplaysDirectory") or "").strip()
        if custom and Path(custom).is_dir():
            return Path(custom)
    except Exception:
        pass
    return default


def detect(cfg: Optional[dict] = None, scan: bool = False) -> dict:
    """Что нашли и откуда взяли. Значения — строки путей или пустая строка."""
    cfg = cfg or paths.load_settings()
    out = {"log_path": "", "game_dir": "", "replays_dir": "", "sources": {}}

    # 1. лог
    manual = (cfg.get("log_path") or "").strip()
    if manual and Path(manual).is_file():
        out["log_path"], out["sources"]["log"] = manual, "настройки"
    elif DEFAULT_LOG.is_file():
        out["log_path"], out["sources"]["log"] = str(DEFAULT_LOG), "авто"

    # 2. папка клиента: сначала настройки, потом лог, потом (если разрешено) диски
    manual = (cfg.get("game_dir") or "").strip()
    if manual and Path(manual).is_dir():
        out["game_dir"], out["sources"]["game"] = manual, "настройки"
    else:
        found = find_in_log(Path(out["log_path"])) if out["log_path"] else None
        if found:
            out["game_dir"], out["sources"]["game"] = str(found), "из лога"
        elif scan:
            found = scan_drives()
            if found:
                out["game_dir"], out["sources"]["game"] = str(found), "поиск по дискам"

    # 3. реплеи
    manual = (cfg.get("replays_dir") or "").strip()
    if manual and Path(manual).is_dir():
        out["replays_dir"], out["sources"]["replays"] = manual, "настройки"
    else:
        found = replays_from_json(LOCAL_LOW / "Replays")
        out["replays_dir"] = str(found)
        out["sources"]["replays"] = "авто" if found.is_dir() else ""

    return out


def static_bytes(game_dir: str) -> Path:
    """Файл с карточками клиента: сначала кэш клиента, потом папка игры."""
    if STATIC_BYTES.is_file():
        return STATIC_BYTES
    if game_dir:
        for candidate in (Path(game_dir) / "Game" / "Data" / "static.bytes",
                          Path(game_dir) / "Game" / "Data" / "CompressedData" / "static.bytes"):
            if candidate.is_file():
                return candidate
    return STATIC_BYTES


def report(cfg: Optional[dict] = None, scan: bool = False) -> dict:
    """Результат определения + проверка, что всё нужное существует."""
    info = detect(cfg, scan=scan)
    checks = {
        "лог": Path(info["log_path"]).is_file() if info["log_path"] else False,
        "клиент": bool(info["game_dir"]) and looks_like_game_dir(Path(info["game_dir"])),
        "static.bytes": static_bytes(info["game_dir"]).is_file(),
        "реплеи": Path(info["replays_dir"]).is_dir() if info["replays_dir"] else False,
    }
    info["checks"] = checks
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="Определение путей CaliberStat")
    ap.add_argument("--show", action="store_true", help="показать, что найдено")
    ap.add_argument("--scan", action="store_true", help="искать клиент по дискам")
    args = ap.parse_args()

    info = report(scan=args.scan)
    for key, source_key, label in (("log_path", "log", "лог игры"),
                                   ("game_dir", "game", "папка клиента"),
                                   ("replays_dir", "replays", "папка реплеев")):
        source = info["sources"].get(source_key) or "не найдено"
        print(f"{label:16s} {info[key] or '—':60s} [{source}]")
    print(f"{'static.bytes':16s} {static_bytes(info['game_dir'])}")
    print("\nпроверка:")
    for name, ok in info["checks"].items():
        print(f"  {'OK  ' if ok else 'НЕТ '} {name}")
    return 0 if all(info["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
