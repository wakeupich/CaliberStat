# -*- coding: utf-8 -*-
"""Сборщик: читает Player.log и складывает всё в data/caliberstat.db.

Работает в фоне (по умолчанию), переживает перезапуск игры и собственный
перезапуск: позиция в логе хранится в таблице meta, при ротации лог
перечитывается с начала.

    python collector.py                 # следить за логом
    python collector.py --once          # разобрать текущий лог и выйти
    python collector.py --once --log "D:\\путь\\Player.log" --db test.db
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

import caliber_db as db

DEFAULT_LOG = os.path.expandvars(
    r"%USERPROFILE%\AppData\LocalLow\1CGS\Caliber\Player.log")

REQ = re.compile(r"Request complete\s+https://([^/]+)(/\S*?)(?:\s|$)")
LOADING = re.compile(r"-{4,}Loading:(\S+)\s+(\S+)\s+([\d.]+)s")
TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?\]")

HANDLED = (
    "/v2/meta/me",
    "/v2/battlepreparation/me",
    "/v2/postbattle/battles/",
    "/v2/userbook/get_profiles",
    "/v2/meta/account/get_user_srv_notification",
)


def parse_ts(line: str) -> float:
    m = TS.match(line)
    if m:
        base, frac = m.group(1), (m.group(2) or "0")[:6]
        try:
            dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            return dt.timestamp() + int(frac.ljust(6, "0")) / 1_000_000
        except ValueError:
            pass
    return time.time()


def open_shared(path: str):
    """Чтение, не мешающее игре писать и переименовывать лог."""
    if os.name != "nt":
        return open(path, "r", encoding="utf-8", errors="replace", buffering=1)
    import msvcrt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = k32.CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = create(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0, None)
    if handle in {None, -1, ctypes.c_void_p(-1).value}:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed: {path}")
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_TEXT)
    return os.fdopen(fd, "r", encoding="utf-8", errors="replace", buffering=1)


class Collector:
    def __init__(self, con, log_path: str):
        self.con = con
        self.log_path = log_path
        self._stop = threading.Event()
        # время строки «Request complete» — тело ответа идёт без своего времени,
        # поэтому запоминаем его здесь, чтобы не писать «сейчас»
        self._pending_ts = None
        self.stats = {"lines": 0, "matches": 0, "lobby": 0,
                      "profiles": 0, "api": 0, "errors": 0, "loading": 0,
                      "rotations": 0}

    # -- обработка одной строки -----------------------------------------
    def feed(self, line: str, pending: list) -> None:
        self.stats["lines"] += 1
        ts = parse_ts(line)

        # JSON-ответ после "Request complete ... OK:"
        if pending:
            # новая строка лога = тело ответа закончилось (или его не было):
            # бросаем недобранный ответ и разбираем строку как обычную
            if TS.match(line) or len(pending) > 400:
                pending.clear()
                self._pending_ts = None
            else:
                pending.append(line.strip())
                blob = "".join(pending[1:])
                if len(blob) > 4_000_000:
                    pending.clear()
                    self._pending_ts = None
                    return
                try:
                    data = json.loads(blob)
                except Exception:
                    return
                endpoint = pending[0]
                payload_ts = self._pending_ts or ts
                pending.clear()
                self._pending_ts = None
                self.on_payload(endpoint, data, payload_ts)
                return

        m = REQ.search(line)
        if m and "OK" in line:
            endpoint = m.group(2)
            # собираем тело только у тех ответов, которые нам нужны: пустые
            # ответы (карма, лайки) иначе «залипали» и глушили разбор матчей
            if any(endpoint.startswith(h) for h in HANDLED):
                self._pending_ts = ts
                rest = line.split("OK:", 1)[1].strip() if "OK:" in line else ""
                if rest:
                    try:
                        data = json.loads(rest)
                    except Exception:
                        pending.append(endpoint)
                        pending.append(rest)
                    else:
                        self._pending_ts = None
                        self.on_payload(endpoint, data, ts)
                else:
                    pending.append(endpoint)
            return
        if m and ("ERROR" in line or "FAIL" in line):
            db.save_error(self.con, ts, f"API {m.group(2)}: {line.strip()[-120:]}")
            self.stats["errors"] += 1
            return

        load = LOADING.search(line)
        if load:
            db.save_loading(self.con, ts, load.group(1), load.group(2), float(load.group(3)))
            self.stats["loading"] += 1
            return

        if '"MatchTimeSeconds"' in line and line.lstrip().startswith("{"):
            try:
                data = json.loads(line)
            except Exception:
                return
            if db.save_match(self.con, data, source="log"):
                self.stats["matches"] += 1
            return

        low = line
        noisy = ("Exception" in low
                 or ("Replay Storage" in low and ("rror" in low or "ailed" in low)))
        if noisy:
            msg = low.strip()
            if msg and not msg.startswith(("UnityEngine", "System.", "LogHandler", "---")):
                db.save_error(self.con, ts, msg[:300])
                self.stats["errors"] += 1

    def on_payload(self, endpoint: str, data: dict, ts: float) -> None:
        if not any(endpoint.startswith(h) for h in HANDLED):
            return
        self.stats["api"] += 1
        db.save_api_payload(self.con, endpoint, ts, data)

        if endpoint.startswith("/v2/meta/me"):
            b = data.get("b") or {}
            uid, nick = b.get("0"), b.get("1")
            prev_uid = db.get_meta(self.con, "my_uid")
            if uid:
                db.set_meta(self.con, "my_uid", str(uid))
            if nick:
                db.set_meta(self.con, "my_nick", str(nick))
            if uid:
                db.save_account(self.con, uid, nick, ts)
                # смена аккаунта — просто фиксируем в консоли
                if prev_uid and str(prev_uid) != str(uid):
                    print(f"[*] аккаунт сменился: uid {prev_uid} -> {uid} ({nick})")
        elif endpoint.startswith("/v2/battlepreparation/me"):
            db.save_lobby(self.con, ts, data)
            self.stats["lobby"] += 1
        elif endpoint.startswith("/v2/userbook/get_profiles"):
            saved = db.save_profiles(self.con, ts, data)
            self.stats["profiles"] += saved
        elif endpoint.startswith("/v2/postbattle/battles/"):
            if db.save_match(self.con, data, source="log"):
                self.stats["matches"] += 1

    # -- разбор файла ----------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    def _identity(self):
        """(устройство, inode, размер) — чтобы отличить новый файл от старого."""
        try:
            st = os.stat(self.log_path)
            return (st.st_dev, st.st_ino, st.st_size)
        except OSError:
            return None

    def process_file(self, follow: bool, from_start: bool = False) -> None:
        key = f"off:{os.path.basename(self.log_path)}"
        ident_key = f"ident:{os.path.basename(self.log_path)}"
        offset = 0 if from_start else int(db.get_meta(self.con, key, "0") or 0)
        saved_ident = "" if from_start else db.get_meta(self.con, ident_key, "")
        f = None
        ident = None
        pending: list = []
        idle = 0.0
        try:
            while not self._stop.is_set():
                if f is None:
                    if self._identity() is None:
                        if not follow:
                            return
                        time.sleep(0.5)
                        continue
                    f = open_shared(self.log_path)
                    ident = self._identity()
                    # Продолжаем с сохранённой позиции только если это тот же файл:
                    # если пока сборщик был выключен игра пересоздала лог, читаем с начала.
                    same_file = bool(saved_ident) and f"{ident[0]}:{ident[1]}" == saved_ident
                    start = offset if (offset and same_file and ident[2] >= offset) else 0
                    f.seek(start)
                    offset = start
                    db.set_meta(self.con, ident_key, f"{ident[0]}:{ident[1]}")
                    if start == 0:
                        pending.clear()
                        self._pending_ts = None
                        db.set_meta(self.con, key, "0")

                line = f.readline()
                if line:
                    idle = 0.0
                    self.feed(line, pending)
                    offset = f.tell()
                    continue

                # данных пока нет
                self.con.commit()
                db.set_meta(self.con, key, str(offset))
                if not follow:
                    return
                time.sleep(0.2)
                idle += 0.2
                if idle < 0.6:
                    continue
                idle = 0.0

                now = self._identity()
                rotated = (now is None or ident is None
                           or now[:2] != ident[:2]     # новый файл: игра переименовала лог
                           or now[2] < offset)         # файл усечён/пересоздан
                if rotated:
                    try:
                        f.close()
                    except Exception:
                        pass
                    f = None
                    ident = None
                    offset = 0
                    pending.clear()
                    self._pending_ts = None
                    db.set_meta(self.con, key, "0")
                    self.stats["rotations"] += 1
        finally:
            if f:
                try:
                    f.close()
                except Exception:
                    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборщик данных Калибра из Player.log")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--once", action="store_true", help="разобрать лог и выйти")
    ap.add_argument("--from-start", action="store_true",
                    help="перечитать лог с начала, не глядя на сохранённую позицию")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.log):
        print(f"[!] Лог не найден: {args.log}")
        return 1

    con = db.connect(args.db)
    col = Collector(con, args.log)
    print(f"[*] Лог: {args.log}")
    print(f"[*] База: {args.db}")
    if not args.quiet:
        print("[*] Ctrl+C — остановить" if not args.once else "[*] разовый разбор")

    try:
        col.process_file(follow=not args.once, from_start=args.from_start)
    except KeyboardInterrupt:
        pass
    finally:
        con.commit()
        s = col.stats
        print(f"[*] строк: {s['lines']}, матчей: {s['matches']}, профилей: {s['profiles']}, "
              f"лобби: {s['lobby']}, API: {s['api']}, ошибок: {s['errors']}, "
              f"загрузок: {s['loading']}")
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
