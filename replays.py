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
             "updated": 0, "failed": 0, "training": 0, "seconds": 0.0,
             "failed_list": []}
    started = time.perf_counter()

    for index, path in enumerate(files, 1):
        status = "failed"
        try:
            data = replay.read_payload(str(path))
            if not data:
                stats["failed"] += 1
                stats["failed_list"].append((str(path), "не протокол боя"))
            elif replay.is_training(data):
                # полигон: бой с ботами без соперников, статистику им не портим
                stats["training"] += 1
                status = "training"
            else:
                match_id = str(((data.get("Log") or {}).get("Data") or {}).get("0") or "")
                existed = bool(match_id) and match_exists(con, match_id)
                if dry_run:
                    status = "updated" if existed else "new"
                else:
                    # код оперативника из записей игроков: читаем файл второй раз,
                    # но только маленьким регулярным поиском по нему
                    codes = replay.player_codes(str(path))
                    replay.import_payload(con, data, path=str(path), codes=codes)
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
    if stats.get("training"):
        text += f", тренировок пропущено: {stats['training']}"
    if stats["folders"]:
        text += f" (папок: {len(stats['folders'])})"
    return text


class Watcher:
    """Слежение за папками реплеев: подхватить бой сразу после того, как игра
    дописала файл.

    Файл нельзя читать, пока игра в него пишет, поэтому сначала запоминаем размер,
    а импортируем только когда он перестал меняться (`settle` секунд).
    Повторно один и тот же файл не разбираем: следим за размером и временем
    изменения.
    """

    def __init__(self, con, folders: Optional[Iterable[Path]] = None,
                 settle: float = 3.0):
        self.con = con
        self.folders = list(folders) if folders is not None else None
        self.settle = settle
        self.seen: dict[str, tuple[int, float, float]] = {}   # путь -> (размер, mtime, замечен)
        self.done: set[tuple[str, int, float]] = set()        # уже разобранные версии файла

    def step(self, now: Optional[float] = None) -> list[dict]:
        """Один проход. Возвращает список импортированных боёв."""
        now = time.time() if now is None else now
        folders = self.folders if self.folders is not None else default_folders()
        imported: list[dict] = []
        alive: set[str] = set()
        for path in list_replays(folders):
            key = str(path)
            alive.add(key)
            try:
                stat = path.stat()
            except OSError:
                continue
            size, mtime = stat.st_size, stat.st_mtime
            version = (key, size, mtime)
            if version in self.done:
                continue                     # эту версию файла уже разбирали
            mark = self.seen.get(key)
            if mark is None:
                self.seen[key] = (size, mtime, now)
                continue                     # только что увидели — ждём, пока допишется
            old_size, old_mtime, first_seen = mark
            if size != old_size or mtime != old_mtime:
                self.seen[key] = (size, mtime, now)
                continue                     # файл ещё меняется
            if now - first_seen < self.settle:
                continue
            result = self._import(path)
            self.done.add(version)           # больше не трогаем, даже если не разобрался
            if result:
                imported.append(result)
            self.seen[key] = (size, mtime, now)
        for key in list(self.seen):
            if key not in alive:
                self.seen.pop(key, None)     # файл убрали — забываем
        self.done = {item for item in self.done if item[0] in alive}
        return imported

    def _import(self, path: Path) -> Optional[dict]:
        """Разобрать один файл. Возвращает описание боя или None."""
        try:
            data = replay.read_payload(str(path))
            if not data or replay.is_training(data):
                return None
            match_id = str(((data.get("Log") or {}).get("Data") or {}).get("0") or "")
            existed = bool(match_id) and match_exists(self.con, match_id)
            codes = replay.player_codes(str(path))
            replay.import_payload(self.con, data, path=str(path), codes=codes)
            return {"file": path.name, "match_id": match_id, "new": not existed}
        except Exception:
            return None


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
