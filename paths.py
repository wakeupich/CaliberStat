# -*- coding: utf-8 -*-
"""Пути программы: всё живёт рядом с exe, а не в профиле пользователя.

Правило портативности: куда положили папку — там и данные. Никаких записей в
реестр (кроме автозапуска) и никаких путей вида `C:\\Users\\...` в коде.

    import paths
    paths.ensure_dirs()          # создать data/, logs/, replays/inbox/
    con = paths.db_path()        # data/caliberstat.db
    cfg = paths.load_settings()  # settings.json рядом с программой
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_TITLE = "CaliberStat"
VERSION = "0.1.6"

SETTINGS_NAME = "settings.json"
DB_NAME = "caliberstat.db"


def app_dir() -> Path:
    """Папка программы: у собранного exe — папка exe, иначе — папка этого файла."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    return app_dir() / "data"


def logs_dir() -> Path:
    return app_dir() / "logs"


def ref_dir() -> Path:
    return app_dir() / "ref"


def replays_dir() -> Path:
    return app_dir() / "replays"


def inbox_dir() -> Path:
    """Куда складываем чужие реплеи, залитые вручную."""
    return replays_dir() / "inbox"


def db_path() -> Path:
    return data_dir() / DB_NAME


def settings_path() -> Path:
    return app_dir() / SETTINGS_NAME


def ensure_dirs() -> None:
    for path in (data_dir(), logs_dir(), ref_dir(), inbox_dir()):
        path.mkdir(parents=True, exist_ok=True)


DEFAULT_SETTINGS = {
    # пути; пустая строка = определить автоматически при запуске
    "log_path": "",
    "game_dir": "",
    "replays_dir": "",
    # поведение
    "autostart": False,
    "collect_on_start": True,
    "import_replays_auto": True,
    # всплывающие сообщения трея: в бою они мешают, поэтому их можно выключить
    "notifications": True,
    "last_account_uid": 0,
    "window": {"w": 1280, "h": 820},
}


def load_settings() -> dict:
    """Настройки программы. Отсутствующие ключи добираются из умолчаний."""
    cfg = json.loads(json.dumps(DEFAULT_SETTINGS))
    path = settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f) or {}
        if isinstance(saved, dict):
            for key, value in saved.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[!] настройки не прочитались ({e}), беру умолчания")
    return cfg


def save_settings(cfg: dict) -> bool:
    """Атомарная запись: сначала во временный файл, потом подмена."""
    path = settings_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[!] настройки не сохранились: {e}")
        return False


def autostart_command() -> str:
    """Команда для HKCU\\...\\Run — путь внутри папки программы."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{app_dir() / "app.py"}"'


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "CaliberStat"


def autostart_enabled() -> bool:
    """Прописан ли автозапуск и совпадает ли он с текущей папкой программы."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, RUN_NAME)
        return bool(value)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def autostart_stale() -> bool:
    """Автозапуск есть, но указывает на старое место (папку перенесли)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, RUN_NAME)
    except Exception:
        return False
    return str(app_dir()).lower() not in str(value).lower()


def set_autostart(enabled: bool) -> bool:
    """Включить/выключить автозапуск. Возвращает успех записи в реестр."""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"[!] автозапуск не изменился: {e}")
        return False


if __name__ == "__main__":
    ensure_dirs()
    print(f"программа: {app_dir()}")
    print(f"база:      {db_path()}")
    print(f"настройки: {settings_path()}")
    print(f"реплеи:    {inbox_dir()}")
