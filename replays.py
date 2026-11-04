# -*- coding: utf-8 -*-
"""Пакетный разбор реплеев: обойти папки и залить всё, что ещё не в базе.

Один и тот же код работает для двух случаев:

* **первый запуск** — подхватить историю: в папке игры и в нашем `replays/inbox`
  уже могут лежать десятки боёв;
* **кнопка «Перечитать реплеи»** — пройтись заново, например после обновления
  справочников из клиента или если бой не разобрался с первого раза.

Разбор идемпотентен: матч узнаётся по id, повторный проход обновляет данные боя
(состав, награды, снаряжение), а не плодит дубли.

    python replays.py --scan                 # все папки, что нашли
    python replays.py --scan --dry-run       # только посчитать
    python replays.py --scan --path "D:\\replays"
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Iterable, Optional

import caliber_db as db
import detect
import paths
import replay

SUFFIX = ".bytes"


def default_folders(cfg: Optional[dict] = None) -> list[Path]:
    """Где искать реплеи: папка игры, наш inbox и то, что указали в настройках."""
    cfg = cfg or paths.load_settings()
    folders: list[Path] = []
    manual = (cfg.get("replays_dir") or "").strip()
    if manual:
        folders.append(Path(manual))
    else:
        info = detect.detect(cfg)
        if info.get("replays_dir"):
            folders.append(Path(info["replays_dir"]))
    folders.append(paths.inbox_dir())
    seen, out = set(), []
    for folder in folders:
        key = str(folder.resolve()).lower()
        if key not in seen and folder.is_dir():
            seen.add(key)
            out.append(folder)
    return out


def list_replays(folders: Iterable[Path]) -> list[Path]:
    """Все файлы реплеев из папок, без повторов (одна папка может быть вложена в другую)."""
    seen: dict[str, Path] = {}
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob(f"*{SUFFIX}")):
            if not path.is_file():
                continue
            key = str(path.resolve()).lower()
            seen.setdefault(key, path)
    return sorted(seen.values(), key=lambda p: p.stat().st_mtime)


def match_exists(con, match_id: str) -> bool:
    return con.execute("SELECT 1 FROM matches WHERE id=?", (match_id,)).fetchone() is not None


def scan(con, folders: Optional[Iterable[Path]] = None,
         progress=None, dry_run: bool = False) -> dict:
    """Обойти реплеи и залить их в базу.

    `progress(номер, всего, файл, статус)` вызывается после каждого файла;
    статус — `new`, `updated`, `failed`. Возвращает сводку.
    """
    folders = list(folders) if folders is not None else default_folders()
    files = list_replays(folders)
    stats = {"folders": [str(f) for f in folders], "files": len(files), "new": 0,
             "updated": 0, "failed": 0, "seconds": 0.0, "failed_list": []}
    started = time.perf_counter()

    for index, path in enumerate(files, 1):
        status = "failed"
        try:
            data = replay.read_payload(str(path))
            if not data:
                stats["failed"] += 1
                stats["failed_list"].append((str(path), "не протокол боя"))
            else:
                match_id = str(((data.get("Log") or {}).get("Data") or {}).get("0") or "")
                existed = bool(match_id) and match_exists(con, match_id)
                if dry_run:
                    status = "updated" if existed else "new"
                else:
                    # код оперативника из записей игроков: читаем файл второй раз,
                    # но только маленьким регулярным поиском по нему
                    codes = replay.player_codes(str(path))
                    replay.import_payload(con, data, path=None, codes=codes)
                    status = "updated" if existed else "new"
                stats[status] += 1
        except Exception as e:
            stats["failed"] += 1
            stats["failed_list"].append((str(path), f"{type(e).__name__}: {e}"))
        if progress:
            try:
                progress(index, len(files), path, status)
            except Exception:
                pass

    stats["seconds"] = round(time.perf_counter() - started, 2)
    return stats


def summary_text(stats: dict) -> str:
    """Короткая строка для лога и отчёта в окне."""
    text = (f"файлов: {stats['files']}, новых: {stats['new']}, "
            f"обновлено: {stats['updated']}, не разобрано: {stats['failed']}")
    if stats["folders"]:
        text += f" (папок: {len(stats['folders'])})"
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Пакетный разбор реплеев CaliberStat")
    ap.add_argument("--scan", action="store_true", help="обойти все папки с реплеями")
    ap.add_argument("--path", action="append", default=[],
                    help="своя папка с реплеями (можно несколько раз)")
    ap.add_argument("--dry-run", action="store_true", help="только посчитать")
    args = ap.parse_args()

    paths.ensure_dirs()
    folders = [Path(p) for p in args.path] if args.path else default_folders()
    if not folders:
        print("[!] Папок с реплеями не нашлось — укажи --path")
        return 1
    print("[*] папки:")
    for folder in folders:
        print(f"    {folder}")

    con = db.connect(str(paths.db_path()))
    try:
        stats = scan(con, folders, progress=lambda i, n, p, s: print(f"  [{i}/{n}] {s:8s} {p.name}"),
                     dry_run=args.dry_run)
    finally:
        con.close()
    print(f"[*] {summary_text(stats)} за {stats['seconds']} с")
    for path, reason in stats["failed_list"]:
        print(f"    [!] {path}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
