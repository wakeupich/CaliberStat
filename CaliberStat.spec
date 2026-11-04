# -*- mode: python ; coding: utf-8 -*-
"""Сборка портативного CaliberStat (PyInstaller, папка onedir).

Запуск: `build.cmd` или
    .venv\\Scripts\\pyinstaller.exe CaliberStat.spec --noconfirm

Результат — `dist\\CaliberStat\\` с `CaliberStat.exe` внутри. Папку целиком можно
копировать на другой компьютер: база и справочники создаются рядом с exe.
"""

a = Analysis(
    ["app.py"],
    pathex=["tools"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
        "gui", "collector", "caliber_db", "client_data", "detect", "paths",
        "queries", "refs", "replays", "replay", "share_code",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "test", "PySide6.QtNetwork",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore",
              "PySide6.QtWebEngineCore", "PySide6.QtMultimedia",
              "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CaliberStat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CaliberStat",
)
