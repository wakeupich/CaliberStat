# -*- coding: utf-8 -*-
"""CaliberStat в фоне: иконка в трее, сбор лога и подхват реплеев.

Это основной вход программы: окно открывается из трея и закрывается без
остановки сбора. Всё, что делает программа, живёт здесь:

* сбор `Player.log` в отдельном потоке (игру не трогаем, только читаем);
* слежение за папками реплеев — после боя бой сам попадает в статистику;
* автозапуск при входе в Windows (запись в HKCU, без служб и планировщика).

    pythonw app.py            # обычный запуск (так и прописан автозапуск)
    python app.py --window    # сразу открыть окно
    python app.py --no-collect  # только окно, без сбора
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caliber_db as db          # noqa: E402
import gui                        # noqa: E402
import paths                      # noqa: E402
import replays as replays_mod     # noqa: E402
from collector import Collector, DEFAULT_LOG   # noqa: E402

MUTEX_NAME = "CaliberStatApp_SingleInstance"
TICK_MS = 5000


def single_instance() -> bool:
    """Второй экземпляр не нужен: иначе два сборщика пишут в одну базу."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        return False
    globals()["_mutex"] = handle            # держим хэндл до выхода
    return True


class App:
    """Трей + сбор + подхват реплеев."""

    def __init__(self, collect: bool = True):
        self.settings = paths.load_settings()
        paths.ensure_dirs()
        self.con = db.connect(str(paths.db_path()))
        self.window: gui.Hub | None = None
        self.collector: Collector | None = None
        self.thread: threading.Thread | None = None
        self.watcher = replays_mod.Watcher(
            self.con, replays_mod.default_folders(self.settings))
        self.last_import: str = ""

        self.tray = QSystemTrayIcon(gui.make_icon())
        self.tray.setToolTip(f"CaliberStat {paths.VERSION}")
        self.menu = QMenu()
        self.status_action = QAction("сбор не запущен", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        open_action = QAction("Открыть окно", self.menu)
        open_action.triggered.connect(self.open_window)
        self.menu.addAction(open_action)

        self.collect_action = QAction("Остановить сбор", self.menu)
        self.collect_action.triggered.connect(self.toggle_collect)
        self.menu.addAction(self.collect_action)

        rescan_action = QAction("Перечитать реплеи", self.menu)
        rescan_action.triggered.connect(self.rescan_replays)
        self.menu.addAction(rescan_action)

        self.autostart_action = QAction("Автозапуск при входе в Windows", self.menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(paths.autostart_enabled())
        self.autostart_action.triggered.connect(self.toggle_autostart)
        self.menu.addAction(self.autostart_action)

        self.menu.addSeparator()
        folder_action = QAction("Папка программы", self.menu)
        folder_action.triggered.connect(
            lambda: os.startfile(str(paths.app_dir())))
        self.menu.addAction(folder_action)

        quit_action = QAction("Выход", self.menu)
        quit_action.triggered.connect(self.quit)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)

        if collect and self.settings.get("collect_on_start", True):
            self.start_collect()
        self.tick()

    # -- сбор лога -----------------------------------------------------
    def start_collect(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        log_path = self.settings.get("log_path") or DEFAULT_LOG
        if not os.path.exists(log_path):
            self.status_action.setText("лог игры не найден")
            return
        self.collector = Collector(self.con, log_path)
        self.thread = threading.Thread(
            target=self.collector.process_file, kwargs={"follow": True},
            daemon=True, name="collector")
        self.thread.start()
        self.collect_action.setText("Остановить сбор")
        self.status_action.setText("сбор идёт")
        self._update_tooltip()

    def stop_collect(self) -> None:
        if self.collector:
            self.collector.stop()
        self.collector = None
        self.thread = None
        self.collect_action.setText("Запустить сбор")
        self.status_action.setText("сбор остановлен")
        self._update_tooltip()

    def toggle_collect(self) -> None:
        if self.thread and self.thread.is_alive():
            self.stop_collect()
        else:
            self.start_collect()

    # -- реплеи --------------------------------------------------------
    def rescan_replays(self) -> None:
        try:
            stats = replays_mod.scan(self.con, replays_mod.default_folders(self.settings))
            message = replays_mod.summary_text(stats)
        except Exception as e:
            message = f"не удалось: {e}"
        self.tray.showMessage("Реплеи", message, QSystemTrayIcon.Information, 6000)
        self.watcher = replays_mod.Watcher(
            self.con, replays_mod.default_folders(self.settings))
        self.tick()

    # -- прочее --------------------------------------------------------
    def open_window(self) -> None:
        if self.window is None:
            self.window = gui.Hub(str(paths.db_path()))
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _tray_clicked(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_window()

    def toggle_autostart(self) -> None:
        wanted = self.autostart_action.isChecked()
        ok = paths.set_autostart(wanted)
        self.autostart_action.setChecked(paths.autostart_enabled() if ok else not wanted)
        if ok:
            self.tray.showMessage(
                "Автозапуск",
                "включён" if wanted else "выключен",
                QSystemTrayIcon.Information, 4000)

    def _update_tooltip(self) -> None:
        collected = self.collector.stats if self.collector else {}
        matches = self.con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        self.tray.setToolTip(
            f"CaliberStat {paths.VERSION}\nбоёв в базе: {matches}\n"
            f"сбор: {'идёт' if self.thread and self.thread.is_alive() else 'стоит'}"
            + (f"\nстрок: {collected.get('lines', 0)}" if collected else ""))

    def tick(self) -> None:
        try:
            picked = self.watcher.step()
            if picked:
                self.last_import = picked[-1]["file"]
                self.tray.showMessage(
                    "Новый бой",
                    f"подхвачен реплей: {self.last_import}",
                    QSystemTrayIcon.Information, 4000)
            if self.collector and not (self.thread and self.thread.is_alive()):
                self.status_action.setText("сбор завершился — перезапускаю")
                self.start_collect()
        except Exception as e:
            self.status_action.setText(f"ошибка: {e}")
        self._update_tooltip()

    def quit(self) -> None:
        self.stop_collect()
        try:
            self.con.close()
        except Exception:
            pass
        QApplication.quit()


def main() -> int:
    ap = argparse.ArgumentParser(description="CaliberStat — фон и трей")
    ap.add_argument("--window", action="store_true", help="сразу открыть окно")
    ap.add_argument("--no-collect", action="store_true", help="без сбора лога")
    args = ap.parse_args()

    if not single_instance():
        print("CaliberStat уже запущен")
        return 1

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[!] трей недоступен — открываю только окно")
        window = gui.Hub(str(paths.db_path()))
        window.show()
        return app.exec()

    core = App(collect=not args.no_collect)
    if args.window:
        core.open_window()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
