# -*- coding: utf-8 -*-
"""Сторож: держит CaliberStat в трее запущенным.

Если трей завершится (ошибка, случайное закрытие, сбой библиотеки), сбор и
подхват реплеев пропадут до ручного перезапуска. Сторож каждые три секунды
проверяет, жив ли трей, и поднимает его заново.

    pythonw watchdog.py          # следить (так и прописывается в автозапуск)
    python watchdog.py --check   # разово сказать, работает ли трей
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, "data", "watchdog.log")
APP_SCRIPT = os.path.join(BASE, "app.py")
APP_MUTEX = "CaliberStatApp_SingleInstance"
WATCHDOG_MUTEX = "CaliberStatWatchdog_SingleInstance"
CREATE_NO_WINDOW = 0x08000000
CHECK_EVERY = 3.0


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1024 * 1024:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _open_mutex(name: str) -> bool:
    """Существует ли мьютекс с таким именем (то есть запущен ли владелец)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenMutexW(0x00100000, False, name)   # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def app_running() -> bool:
    return _open_mutex(APP_MUTEX)


def take_single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, WATCHDOG_MUTEX)
    if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        return False
    globals()["_mutex_handle"] = handle
    return True


def launch_command() -> list[str]:
    """Как запустить трей: собранный exe или pythonw + app.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    return [exe, APP_SCRIPT]


def main() -> int:
    ap = argparse.ArgumentParser(description="Сторож CaliberStat")
    ap.add_argument("--check", action="store_true",
                    help="только проверить состояние и выйти")
    args = ap.parse_args()

    if args.check:
        print("трей запущен" if app_running() else "трей НЕ запущен")
        return 0

    if not take_single_instance():
        log("сторож уже работает — второй экземпляр не нужен")
        return 0

    log(f"сторож запущен (pid {os.getpid()})")
    while True:
        if app_running():
            time.sleep(CHECK_EVERY)
            continue
        try:
            proc = subprocess.Popen(launch_command(), cwd=BASE,
                                    creationflags=CREATE_NO_WINDOW)
            log(f"трей запущен, pid {proc.pid}")
            proc.wait()
            log(f"трей завершился с кодом {proc.returncode} — перезапускаю")
        except Exception as e:
            log(f"не удалось запустить трей: {e}")
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    raise SystemExit(main())
