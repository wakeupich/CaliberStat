# -*- coding: utf-8 -*-
"""CaliberStat — окно: обзор, матчи, состав боя, карточка игрока, служебное, настройки.

Читает базу рядом с программой и обновляется сама. Ничего не пишет в игру.

    python gui.py
    python gui.py --shot docs/preview.png     # отрисовать окно и выйти (для проверок)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit,
    QMainWindow, QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import caliber_db as db          # noqa: E402
import client_data                # noqa: E402
import detect                     # noqa: E402
import paths                      # noqa: E402
import queries as q               # noqa: E402
import refs as refs_mod           # noqa: E402
import replays as replays_mod     # noqa: E402
import share_code as share        # noqa: E402  (tools/share_code.py)

ACCENT = "#d6b05a"
TEXT = "#e8e6e2"
DIM = "#8c929b"
BG = "#14171c"

STYLE = f"""
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}
QLabel {{ color: {TEXT}; }}
QLabel#h1 {{ font-size: 15pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11pt; font-weight: 600; color: {ACCENT}; }}
QLabel#cardValue {{ font-size: 17pt; font-weight: 700; color: {TEXT}; }}
QLabel#cardTitle {{ font-size: 8pt; color: {DIM}; }}
QLabel#dim {{ color: {DIM}; }}
QFrame#card {{ background: #1b1f26; border: 1px solid #2a3039; border-radius: 10px; }}
QTabWidget::pane {{ border: 1px solid #2a3039; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {DIM}; padding: 8px 16px;
                border: none; font-size: 9.5pt; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTableWidget {{ background: #171b21; alternate-background-color: #1b2028;
                gridline-color: #242a32; border: 1px solid #2a3039; border-radius: 8px;
                selection-background-color: #3a3320; selection-color: {TEXT}; }}
QTableWidget::item:selected {{ background: #3a3320; color: {TEXT}; }}
QTableWidget::item:selected:!active {{ background: #3a3320; color: {TEXT}; }}
QHeaderView::section {{ background: #1b2028; color: {DIM}; border: none;
                        border-bottom: 1px solid #2a3039; padding: 6px; }}
QLineEdit, QComboBox {{ background: #1b2028; border: 1px solid #2e3540; border-radius: 6px;
                        padding: 5px 8px; color: {TEXT}; }}
QPushButton {{ background: #222830; border: 1px solid #333a43; border-radius: 6px;
               padding: 6px 12px; color: {TEXT}; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {DIM}; border-color: #262c34; }}
QScrollBar:vertical {{ background: #171b21; width: 10px; }}
QScrollBar::handle:vertical {{ background: #333a43; border-radius: 5px; min-height: 24px; }}
"""


# ---------------------------------------------------------------------------
# Мелочи оформления
# ---------------------------------------------------------------------------
class Card(QFrame):
    """Карточка с большой цифрой и подписью."""

    def __init__(self, title: str, value: str = "—", hint: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumHeight(78)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.value = QLabel(value)
        self.value.setObjectName("cardValue")
        self.hint = QLabel(hint)
        self.hint.setObjectName("dim")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set(self, value: str, hint: str = "") -> None:
        self.value.setText(value)
        self.hint.setText(hint)


class Bars(QWidget):
    """Столбики по дням без сторонних библиотек."""

    def __init__(self, height: int = 130):
        super().__init__()
        self.setMinimumHeight(height)
        self.items: list[tuple[str, int]] = []

    def set_data(self, items: list[tuple[str, int]]) -> None:
        self.items = items
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        if not self.items:
            painter.setPen(QPen(QColor(DIM)))
            painter.drawText(self.rect(), Qt.AlignCenter, "пока нет данных")
            return
        top, bottom = 16, 22
        plot = height - top - bottom
        count = len(self.items)
        step = width / max(count, 1)
        peak = max((v for _, v in self.items), default=0) or 1
        bar_w = max(4.0, min(step * 0.62, 34.0))
        for index, (day, value) in enumerate(self.items):
            cx = step * index + step / 2
            bar_h = plot * (value / peak) if value else 0
            rect_x = cx - bar_w / 2
            rect_y = top + (plot - bar_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(ACCENT if value else "#2a3039"))
            painter.drawRoundedRect(int(rect_x), int(rect_y), int(bar_w),
                                    max(2, int(bar_h)), 3, 3)
            if value:
                painter.setPen(QPen(QColor(TEXT)))
                font = painter.font()
                font.setPointSize(8)
                painter.setFont(font)
                painter.drawText(int(cx - 14), int(rect_y - 4), 28, 14,
                                 Qt.AlignCenter, str(value))
            painter.setPen(QPen(QColor(DIM)))
            font = painter.font()
            font.setPointSize(7)
            painter.setFont(font)
            painter.drawText(int(cx - 18), height - 18, 36, 16, Qt.AlignCenter,
                             day[-5:].replace("-", "."))


def make_table(headers: list[str], stretch_last: bool = True) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_last:
        header.setStretchLastSection(True)
    table.verticalHeader().setDefaultSectionSize(24)
    return table


def fill_table(table: QTableWidget, rows: list[list[str]],
               right: tuple[int, ...] = ()) -> None:
    table.setRowCount(0)
    for row in rows:
        index = table.rowCount()
        table.insertRow(index)
        for column, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            if column in right:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(index, column, item)


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("h2")
    return label


def paint_results(table: QTableWidget, column: int = 3) -> None:
    """Победа — зеленоватым, поражение — красноватым, остальное без цвета."""
    for row in range(table.rowCount()):
        item = table.item(row, column)
        if not item:
            continue
        if item.text() == "победа":
            item.setForeground(QColor("#8fc98f"))
        elif item.text() == "поражение":
            item.setForeground(QColor("#d98b8b"))


# ---------------------------------------------------------------------------
# Названия из справочников клиента (см. refs.py); нет названия — показываем ключ
# ---------------------------------------------------------------------------
def refs():
    return refs_mod.get(connection())


def map_title(match) -> str:
    value = match["map"] if not isinstance(match, dict) else match.get("map")
    return refs().map(value) if value else "—"


def role_title(role) -> str:
    return db.role_ru(role) or "—"


def level_text(player: dict) -> str:
    """«(оперативник 15, престиж 1, аккаунт 100)» — как нашивки в игре.

    У оперативника 15 уровней, дальше качается престиж: в бою его рисуют
    жёлтым квадратом вместо чёрного с уровнем. Уровень аккаунта — отдельный
    счётчик, в бою он в белом квадрате.
    """
    level = player.get("card_level")
    prestige = player.get("prestige")
    account = player.get("account_level")
    parts = []
    if level is not None:
        parts.append(f"оперативник {level}")
    if prestige:
        parts.append(f"престиж {prestige}")
    if account:
        parts.append(f"аккаунт {account}")
    return f" ({', '.join(parts)})" if parts else ""


def result_text(match) -> str:
    win = q.result_of(match)
    if win is None:
        return "—"
    return "победа" if win else "поражение"


# ---------------------------------------------------------------------------
# Карточка игрока в бою
# ---------------------------------------------------------------------------
class PlayerCardDialog(QDialog):
    """Карточка игрока в бою: всё разложено по вкладкам и тянется по окну.

    Раньше это была одна длинная лента со скроллом — читать неудобно. Теперь
    пять вкладок: бой, снаряжение, навыки, прокачка, награды. Таблицы
    растягиваются вместе с окном, внешнего скролла нет.
    """

    def __init__(self, parent, match: dict, player: dict):
        super().__init__(parent)
        self.match = match
        self.player = player
        # разобранный код комплекта этого игрока (если его вставили) — из него
        # берётся его личный выбор узлов дерева: в реплее его нет
        self.code_payload: dict | None = None
        self.code_tree: dict[int, str] = {}
        self.setWindowTitle(f"{player.get('nick') or 'игрок'} — карточка боя")
        self.resize(900, 680)
        self.setMinimumSize(720, 460)
        self.setStyleSheet(STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(6)

        head = QLabel(f"{player.get('nick') or '—'}")
        head.setObjectName("h1")
        outer.addWidget(head)
        sub = QLabel(f"{role_title(player.get('role'))} · "
                     f"оперативник: {refs().operator(player.get('operator_code'))}"
                     f"{level_text(player)} · "
                     f"{map_title(match)} · {q.fmt_dt(q.start_ts(match))}")
        sub.setObjectName("dim")
        outer.addWidget(sub)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self._battle_tab(), "Бой")
        self.tabs.addTab(self._gear_tab(), "Снаряжение")
        self.tabs.addTab(self._perks_tab(), "Навыки")
        self.tabs.addTab(self._levels_tab(), "Прокачка")
        self.tabs.addTab(self._rewards_tab(), "Награды")

        buttons = QHBoxLayout()
        self.code_hint = QLabel("")
        self.code_hint.setObjectName("dim")
        self.code_hint.setWordWrap(True)
        buttons.addWidget(self.code_hint, 1)
        self.copy_code = QPushButton("Скопировать комплект")
        self.copy_code.setToolTip(
            "Собрать код комплекта оперативника из этого боя и положить в буфер обмена.\n"
            "Дерево прокачки в бою не передаётся — в код идёт первый доступный узел\n"
            "каждого уровня карточки, если не вставлен код игрока.")
        self.copy_code.clicked.connect(self._copy_loadout_code)
        if not player.get("has_loadout"):
            self.copy_code.setEnabled(False)
        buttons.addWidget(self.copy_code)
        self.paste_code = QPushButton("Вставить код")
        self.paste_code.setToolTip(
            "Разобрать код комплекта, который прислал игрок: покажет его настоящий\n"
            "выбор узлов дерева и подставит его в новый код из боя.")
        self.paste_code.clicked.connect(self._paste_code)
        buttons.addWidget(self.paste_code)
        buttons.addSpacing(8)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        outer.addLayout(buttons)

    # -- общие помощники ------------------------------------------------
    @staticmethod
    def _note(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("dim")
        return label

    @staticmethod
    def _table(headers: list[str], rows: list[list], right: tuple[int, ...] = (),
               wrap: bool = False, fill: bool = False) -> QTableWidget:
        """Таблица вкладки.

        `fill=True` — растянуть по всей вкладке (длинные списки вроде прокачки);
        иначе высота подгоняется под содержимое, чтобы под короткой таблицей
        не висело пустое поле и не появлялось лишних полос прокрутки.
        """
        table = make_table(headers)
        table.setWordWrap(wrap)
        if wrap:
            table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        fill_table(table, rows, right=right)
        if fill:
            table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            if wrap:
                table.resizeRowsToContents()
        else:
            table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            if wrap:
                table.resizeRowsToContents()
            height = table.horizontalHeader().height() + 4
            for row in range(table.rowCount()):
                height += table.rowHeight(row)
            table.setFixedHeight(height + 4)
        return table

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        return page, layout

    # -- вкладки --------------------------------------------------------
    def _battle_tab(self) -> QWidget:
        player = self.player
        page, layout = self._page()
        layout.addWidget(self._table(["Показатель", "Значение"], [
            ["Убийства игроков", player.get("kills") or 0],
            ["Убийства ботов", player.get("bot_kills") or 0],
            ["Смерти", player.get("deaths") or 0],
            ["Помощь", player.get("assists") or 0],
            ["Урон", int(player.get("damage_dealt") or 0)],
            ["Получено урона", int(player.get("damage_received") or 0)],
            ["Лечение", int(player.get("heal") or 0)],
            ["Оживления", player.get("revives") or 0],
            ["Попаданий в голову", head_cell(player.get("headshots"),
                                             player.get("body_hits"))],
            ["Попаданий в тело", int(player.get("body_hits") or 0)],
            ["Вышел из боя", "да" if player.get("first_leave") else "нет"],
        ], right=(1,)))
        layout.addStretch(1)
        return page

    # слоты модулей: хвост ключа говорит, куда модуль ставится
    MODULE_SLOTS = {
        "top": "Прицел", "front": "Ствол", "back": "Приклад", "mag": "Магазин",
        "side": "ЛЦУ", "underbarrel": "Подствольник", "pistol_grip": "Рукоять",
        "grip": "Рукоять", "handguard": "Цевьё", "stock": "Приклад",
        "muzzle": "Дульный тормоз", "barrel": "Ствол",
    }

    @classmethod
    def module_kind(cls, key: str) -> str:
        tail = str(key).rsplit("_", 1)[-1].lower() if "_" in str(key) else ""
        return cls.MODULE_SLOTS.get(tail, "Модуль")

    def _weapon_block(self, slot: str, info: dict) -> QFrame:
        """Оружие отдельной карточкой: модули списком, а не одной длинной строкой."""
        frame = QFrame()
        frame.setObjectName("card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(6)

        weapon_name = refs().item(info.get("key"))
        translated = weapon_name != str(info.get("key"))
        title = QLabel(f"{replay_slot_title(slot)} · {weapon_name}")
        title.setObjectName("h2")
        title.setWordWrap(True)
        if not translated:
            title.setStyleSheet(f"color: {DIM};")
            title.setToolTip("в клиенте нет названия — показываем ключ")
        box.addWidget(title)

        modules = [m for m in (info.get("modules") or []) if m]
        if modules:
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(2)
            for row, key in enumerate(modules):
                kind = QLabel(self.module_kind(key))
                kind.setObjectName("dim")
                kind.setFixedWidth(112)
                name_text = refs().item(key)
                name = QLabel(name_text)
                name.setWordWrap(True)
                if name_text == str(key):
                    name.setStyleSheet(f"color: {DIM};")
                    name.setToolTip("в клиенте нет названия — показываем ключ")
                grid.addWidget(kind, row, 0, Qt.AlignTop)
                grid.addWidget(name, row, 1)
            grid.setColumnStretch(1, 1)
            box.addLayout(grid)
        else:
            box.addWidget(self._note("без модулей"))

        skin = skin_title(info.get("skin"))
        if skin and skin != "—":
            box.addWidget(self._note(f"Скин: {skin}"))
        return frame

    def _gear_tab(self) -> QWidget:
        page, layout = self._page()
        loadout = db_loadout(self, self.match.get("id"), self.player.get("uid"))
        if not loadout:
            layout.addWidget(self._note(
                "Снаряжение есть только в реплее. Реплей этого боя не найден — "
                "залей его кнопкой «Перечитать реплеи»."))
            layout.addStretch(1)
            return page

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        for slot, info in (loadout.get("weapons") or {}).items():
            box.addWidget(self._weapon_block(slot, info))

        extra = []
        if loadout.get("consumables"):
            extra.append("Расходники: " + ", ".join(
                refs().item(c) for c in loadout["consumables"]))
        if loadout.get("skins"):
            extra.append("Комплект: " + skins_summary(loadout["skins"]))
        if loadout.get("fatality"):
            extra.append("Казнь: " + refs().animation(loadout["fatality"]))
        if extra:
            note = self._note("\n".join(extra))
            box.addWidget(note)
        box.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        return page

    def _perks_tab(self) -> QWidget:
        page, layout = self._page()
        loadout = db_loadout(self, self.match.get("id"), self.player.get("uid"))
        perks = (loadout or {}).get("perks") or []
        levels = (loadout or {}).get("perk_levels") or {}
        if not perks:
            layout.addWidget(self._note("Навыки не сохранились: нужен реплей этого боя."))
            layout.addStretch(1)
            return page
        table = self._table(["Навык", "Уровень"],
                            [[refs().item(p), levels.get(p, "—")] for p in perks],
                            right=(1,))
        layout.addWidget(table)
        layout.addStretch(1)
        layout.addWidget(self._note(
            f"Взято в бой: {len(perks)}. Уровни 1–2 — ступень прокачки навыка."))
        return page

    def _levels_tab(self) -> QWidget:
        page, layout = self._page()
        code = self.player.get("operator_code")
        levels_map = refs().operator_levels(code) if code else {}
        if not levels_map:
            layout.addWidget(self._note(
                "Нет данных о прокачке: обнови справочники из клиента "
                "и залей реплей этого боя."))
            layout.addStretch(1)
            return page
        my_level = self.player.get("card_level") or 0
        chosen = self.code_tree or {}
        rows = []
        for level in sorted(levels_map):
            parts = []
            for node in levels_map[level]:
                text = node["name"] or ""
                if node["effect"]:
                    text += f" {node['effect']}"
                text = text or node["key"]
                if chosen.get(level) == node["key"]:
                    text = "★ " + text
                parts.append(text)
            rows.append([f"{level}" + (" ✓" if my_level and level <= my_level else ""),
                         "; ".join(parts)])
        layout.addWidget(self._table(["Уровень", "Что открывается"], rows,
                                     right=(0,), wrap=True), 1)
        note = (f"Галочкой отмечены уровни, которые у него уже есть "
                f"(текущий: {my_level or '—'}). Названия узлов и числа — из клиента.")
        if chosen:
            note += (" ★ — что игрок выбрал на этом уровне (из его кода комплекта): "
                     "в бою этот выбор не передаётся.")
        else:
            note += (" Какой узел игрок выбрал, в реплее не записано — вставь его код "
                     "комплекта кнопкой «Вставить код», и выбор появится здесь.")
        layout.addWidget(self._note(note))
        return page

    def _rewards_tab(self) -> QWidget:
        page, layout = self._page()
        rewards = parent_rewards(self, self.match.get("id"), self.player.get("uid"))
        layout.addWidget(self._table(["Что", "Сколько"], [
            ["Опыт за бой", rewards.get("match_xp") or 0],
            ["Опыт за ленты", rewards.get("ribbon_xp") or 0],
            ["Опыт за медали", rewards.get("medal_xp") or 0],
            ["Опыт оперативника", rewards.get("card_xp") or 0],
            ["Боевой пропуск", rewards.get("battlepass_base") or 0],
        ], right=(1,)))
        layout.addStretch(1)

        medals = rewards.get("medals_json")
        text = ""
        if medals:
            try:
                data = json.loads(medals)
                if isinstance(data, dict):
                    text = ", ".join(f"{refs().medal(code)} ×{count}"
                                     for code, count in data.items())
            except Exception:
                text = str(medals)
        layout.addWidget(self._note("Медали: " + text if text else "Медалей за бой нет."))
        return page

    def _paste_code(self) -> None:
        """Разобрать присланный код: показать выбор узлов игрока и взять его в сборку."""
        text, ok = QInputDialog.getText(self, "Код комплекта",
                                        "Вставь код, который прислал игрок:")
        if not ok or not str(text).strip():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            catalog = share_catalog()
            payload = share.decode(text)
        except Exception as error:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Код комплекта", f"Код не разобрался: {error}")
            return
        QApplication.restoreOverrideCursor()

        operator = catalog.operator(payload.get("0"))
        self.code_payload = payload
        self.code_tree = {level: catalog.upgrade(node) for level, node in
                          enumerate((payload.get("1") or {}).get("1") or [], start=1)}
        # вкладку «Прокачка» перерисовываем: на ней появляются звёздочки выбора
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == "Прокачка":
                widget = self._levels_tab()
                self.tabs.removeTab(index)
                self.tabs.insertTab(index, widget, "Прокачка")
                self.tabs.setCurrentIndex(index)
                break
        hint = (f"код разобран: {operator}, узлов {len(self.code_tree)} · "
                f"новый код из боя возьмёт этот выбор")
        if operator != self.player.get("operator_code"):
            hint += f" (в бою он на {self.player.get('operator_code')})"
        self.code_hint.setText(hint)

    def _copy_loadout_code(self) -> None:
        """Код комплекта из боя — в буфер обмена, чтобы вставить его в клиенте."""
        loadout = db_loadout(self, self.match.get("id"), self.player.get("uid"))
        if not loadout or not loadout.get("operator"):
            self.code_hint.setText("в этом бою снаряжения нет — код не собрать")
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            catalog = share_catalog()
            # если игрок присылал свой код и оперативник тот же — берём его дерево
            template = None
            if self.code_payload and \
                    catalog.operator(self.code_payload.get("0")) == loadout.get("operator"):
                template = self.code_payload
            code, payload, warnings = share.code_from_loadout(loadout, catalog,
                                                              template=template)
        except Exception as error:          # клиента нет, оперативник неизвестен
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Комплект", str(error))
            return
        QApplication.restoreOverrideCursor()

        QApplication.clipboard().setText(code)
        inner = payload["1"]
        text = (f"код скопирован: {len(code)} символов · "
                f"оружие {len(inner['5'])} · модулей "
                f"{sum(len(w['1']) for w in inner['5'])} · "
                f"навыки {len(inner['4'])} · образы {len(inner['3'])} · "
                f"дерево {len(inner['1'])} ({'из его кода' if template else 'по умолчанию'})")
        if warnings:
            text += " · не нашлось: " + "; ".join(warnings)
        self.code_hint.setText(text)
        self.code_hint.setToolTip("Вставь в клиенте: «Обмен комплектами» → «Загрузить комплект». "
                                  f"Код в буфере обмена:\n\n{code}")

    def scroll_to_end(self) -> None:
        """Совместимость с ключом --bottom: вкладки прокручивать не нужно."""
        return None


def head_cell(head, body) -> str:
    """Попадания в голову и их доля: «809 (18,8%)» — как в исходном хабе."""
    head = int(head or 0)
    body = int(body or 0)
    total = head + body
    if not total:
        return "—"
    share = 100.0 * head / total
    return f"{head} ({share:.1f}%)".replace(".", ",")


SKIN_SLOTS = {"Head": "голова", "Suit": "костюм", "Gear": "снаряжение",
              "Model": "модель", "WeaponPrimary": "оружие", "WeaponSecondary": "оружие 2"}


def skins_summary(skins) -> str:
    """Комплект без повторов: один образ на голову, костюм и снаряжение — одна строка."""
    groups: dict[str, list[str]] = {}
    for key in skins or []:
        parts = str(key).rsplit("_", 1)
        slot = SKIN_SLOTS.get(parts[1]) if len(parts) == 2 else None
        groups.setdefault(skin_title(key), []).append(slot or "")
    out = []
    for name, slots in groups.items():
        real = [slot for slot in slots if slot]
        out.append(f"{name} — {', '.join(real)}" if real else name)
    return ", ".join(out)


def replay_slot_title(slot: str) -> str:
    return {"PrimaryWeapon": "Основное", "SecondaryWeapon": "Второе",
            "HeavyWeapon": "Тяжёлое"}.get(slot, slot)


def skin_title(key) -> str:
    """Скин оружия/комплекта. «Stock_*» — заводской, без кастомного образа."""
    if not key:
        return "—"
    if str(key).startswith("Stock_"):
        return "заводской"
    return refs().skin(key)


_LOADOUT_CACHE: dict[tuple[str, int], dict] = {}

# окно обращается к базе через эти обёртки: соединение открывается один раз
_CON: "db.sqlite3.Connection | None" = None
_DB_PATH: str | None = None


def set_db_path(path: str) -> None:
    """Переключить базу (для снимков и проверок на копии базы)."""
    global _DB_PATH, _CON
    _DB_PATH = path
    if _CON is not None:
        try:
            _CON.close()
        except Exception:
            pass
    _CON = None


def connection():
    global _CON
    if _CON is None:
        _CON = db.connect(_DB_PATH or str(paths.db_path()))
    return _CON


def db_loadout(_parent, match_id, uid) -> dict:
    if not match_id or uid is None:
        return {}
    key = (match_id, int(uid))
    if key not in _LOADOUT_CACHE:
        _LOADOUT_CACHE[key] = db.get_loadout(connection(), match_id, int(uid)) or {}
    return _LOADOUT_CACHE[key]


def parent_rewards(_parent, match_id, uid) -> dict:
    if not match_id or uid is None:
        return {}
    row = connection().execute(
        "SELECT * FROM match_rewards WHERE match_id=? AND uid=?",
        (match_id, int(uid))).fetchone()
    return dict(row) if row else {}


_SHARE_CATALOG = None


def share_catalog():
    """Списки клиента для сборки кода комплекта: читаются один раз за запуск."""
    global _SHARE_CATALOG
    if _SHARE_CATALOG is None:
        _SHARE_CATALOG = share.load_catalog()
    return _SHARE_CATALOG


# ---------------------------------------------------------------------------
# Окно
# ---------------------------------------------------------------------------
class Hub(QMainWindow):
    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self.settings = paths.load_settings()
        self.my_uid: int | None = None
        self.match_id: str | None = None
        self.watcher = None
        self.tabs_pages: dict[str, QWidget] = {}

        self.setWindowTitle(f"CaliberStat {paths.VERSION}")
        self.resize(1280, 830)
        self.setStyleSheet(STYLE)
        self.setWindowIcon(make_icon())

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("CaliberStat")
        title.setObjectName("h1")
        top.addWidget(title)
        top.addSpacing(16)
        top.addWidget(QLabel("аккаунт:"))
        self.account_combo = QComboBox()
        self.account_combo.setMinimumWidth(200)
        self.account_combo.currentIndexChanged.connect(self._account_changed)
        top.addWidget(self.account_combo)
        top.addStretch(1)
        rescan = QPushButton("Перечитать реплеи")
        rescan.setToolTip("Обойти папки с реплеями и залить всё, чего нет в базе")
        rescan.clicked.connect(self._rescan_replays)
        top.addWidget(rescan)
        top.addSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        top.addWidget(self.status)
        outer.addLayout(top)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self._build_overview()
        self._build_matches()
        self._build_service()
        self._build_settings()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.reload)
        self.timer.start(5000)
        self.reload()

    # -- вкладки -------------------------------------------------------
    def _tab(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.tabs.addTab(page, title)
        self.tabs_pages[title] = page
        return page, layout

    def _build_overview(self) -> None:
        page, layout = self._tab("Обзор")
        grid = QGridLayout()
        grid.setSpacing(10)
        self.cards = {
            "matches": Card("боёв"),
            "week": Card("за 7 дней"),
            "duration": Card("время в боях"),
            "kills": Card("убийства"),
            "accuracy": Card("точность в голову"),
            "damage": Card("урон"),
        }
        for index, card in enumerate(self.cards.values()):
            grid.addWidget(card, index // 3, index % 3)
        layout.addLayout(grid)

        layout.addWidget(section_title("Бои по дням"))
        self.bars = Bars()
        layout.addWidget(self.bars)

        layout.addWidget(section_title("Последние бои"))
        self.last_table = make_table(
            ["Начало", "Режим", "Карта", "Итог", "Длительность",
             "Убийств", "Ботов", "Смертей", "Урон"])
        self.last_table.doubleClicked.connect(self._open_match_from_last)
        layout.addWidget(self.last_table, 1)
        self._last_ids: list[str] = []

    def _build_matches(self) -> None:
        page, layout = self._tab("Матчи")

        filters = QHBoxLayout()
        filters.addWidget(QLabel("режим:"))
        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumWidth(220)
        self.mode_combo.currentIndexChanged.connect(lambda _i: self._reload_matches())
        filters.addWidget(self.mode_combo)
        filters.addStretch(1)
        self.match_info = QLabel("выбери бой — внизу появится состав")
        self.match_info.setObjectName("dim")
        filters.addWidget(self.match_info)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Vertical)
        self.matches_table = make_table(
            ["Начало", "Режим", "Карта", "Итог", "Длит.",
             "Бойцов", "Снаряж.", "Моё: убийств/ботов/смертей", "Урон"])
        self.matches_table.itemSelectionChanged.connect(self._match_selected)
        splitter.addWidget(self.matches_table)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)
        bottom_layout.addWidget(section_title("Состав боя"))
        self.roster_table = make_table(
            ["Роль", "Ник", "Оперативник", "Опер.", "Прест.", "Акк.", "Убийств",
             "Ботов", "Смертей", "Помощь", "Урон", "Головы", "Тело", "Снаряжение"])
        self.roster_table.doubleClicked.connect(self._open_player_card)
        bottom_layout.addWidget(self.roster_table)
        splitter.addWidget(bottom)
        splitter.setSizes([420, 260])
        layout.addWidget(splitter, 1)
        self._match_ids: list[str] = []

    def _build_service(self) -> None:
        page, layout = self._tab("Служебное")
        layout.addWidget(section_title("База"))
        self.db_label = QLabel("—")
        self.db_label.setWordWrap(True)
        layout.addWidget(self.db_label)

        layout.addWidget(section_title("Серверы"))
        self.servers_table = make_table(["Сервер", "Регион", "Боёв", "Последний бой"])
        self.servers_table.setMaximumHeight(180)
        layout.addWidget(self.servers_table)

        layout.addWidget(section_title("Ошибки клиента"))
        self.errors_table = make_table(["Когда", "Сообщение"])
        layout.addWidget(self.errors_table, 1)

    def _build_settings(self) -> None:
        page, layout = self._tab("Настройки")
        form = QFormLayout()
        form.setSpacing(8)

        self.log_edit = QLineEdit()
        self.game_edit = QLineEdit()
        self.replays_edit = QLineEdit()
        for edit, key in ((self.log_edit, "log"), (self.game_edit, "game"),
                          (self.replays_edit, "replays")):
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            button = QPushButton("…")
            button.setMaximumWidth(36)
            button.clicked.connect(lambda _c=False, e=edit, k=key: self._pick_path(e, k))
            row.addWidget(button)
            form.addRow({"log": "Лог игры", "game": "Папка клиента",
                         "replays": "Папка реплеев"}[key], row)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        detect_button = QPushButton("Определить автоматически")
        detect_button.clicked.connect(self._auto_detect)
        buttons.addWidget(detect_button)
        save_button = QPushButton("Сохранить")
        save_button.clicked.connect(self._save_settings)
        buttons.addWidget(save_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addWidget(section_title("Данные"))
        self.replays_info = QLabel("—")
        self.replays_info.setWordWrap(True)
        self.replays_info.setObjectName("dim")
        layout.addWidget(self.replays_info)

        data_buttons = QHBoxLayout()
        rescan = QPushButton("Перечитать реплеи")
        rescan.setToolTip("Обойти все папки с реплеями и залить всё, чего нет в базе")
        rescan.clicked.connect(self._rescan_replays)
        data_buttons.addWidget(rescan)

        self.refs_button = QPushButton("Обновить справочники из клиента")
        self.refs_button.setToolTip(
            "Прочитать файлы клиента и обновить названия карт, оружия, "
            "оперативников и навыков. После патча игры — нажать снова.")
        self.refs_button.clicked.connect(self._update_refs)
        data_buttons.addWidget(self.refs_button)
        data_buttons.addStretch(1)
        layout.addLayout(data_buttons)

        self.refs_info = QLabel("—")
        self.refs_info.setObjectName("dim")
        self.refs_info.setWordWrap(True)
        layout.addWidget(self.refs_info)

        services = QHBoxLayout()
        self.auto_replays_box = QCheckBox("Подхватывать реплеи после боя")
        self.auto_replays_box.setChecked(bool(self.settings.get("import_replays_auto", True)))
        self.auto_replays_box.setToolTip(
            "Как только игра допишет файл реплея, бой сам появится в статистике")
        self.auto_replays_box.stateChanged.connect(self._toggle_auto_replays)
        services.addWidget(self.auto_replays_box)

        self.autostart_box = QCheckBox("Автозапуск при входе в Windows")
        self.autostart_box.setChecked(paths.autostart_enabled())
        self.autostart_box.setToolTip("Запускает трей со сбором и подхватом реплеев")
        self.autostart_box.stateChanged.connect(self._toggle_autostart)
        services.addWidget(self.autostart_box)
        services.addStretch(1)
        layout.addLayout(services)
        layout.addStretch(1)

        self._load_settings_into_fields()
        self._update_refs_info()

    # -- данные --------------------------------------------------------
    def _watcher(self):
        if self.watcher is None:
            self.watcher = replays_mod.Watcher(
                connection(), replays_mod.default_folders(self.settings))
        return self.watcher

    def reload(self) -> None:
        try:
            picked = self._watch_replays()
            self._reload_accounts()
            self._reload_overview()
            self._reload_matches()
            self._reload_service()
            stats = q.db_stats(connection())
            if picked:
                names = ", ".join(item["file"][:32] for item in picked[:3])
                self.status.setText(f"подхвачено реплеев: {len(picked)} · {names}")
            else:
                self.status.setText(
                    f"боёв {stats['matches']} · игроков {stats['players']} · "
                    f"снаряжений {stats['loadouts']} · ников {stats['nicks']}")
        except Exception as e:
            self.status.setText(f"ошибка чтения базы: {e}")

    def _watch_replays(self) -> list[dict]:
        """Подхват новых реплеев: игра дописала файл — сразу в базу."""
        if not self.settings.get("import_replays_auto", True):
            return []
        try:
            return self._watcher().step()
        except Exception as e:
            self.status.setText(f"слежение за реплеями: {e}")
            return []

    def _reload_accounts(self) -> None:
        accounts = q.accounts(connection())
        current = self.account_combo.currentData()
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        self.account_combo.addItem("все аккаунты", None)
        for acc in accounts:
            label = f"{acc['nick'] or acc['uid']} ({acc['matches']} боёв)"
            self.account_combo.addItem(label, acc["uid"])
        index = self.account_combo.findData(current)
        if current is None and accounts:
            index = 1        # по умолчанию — первый реальный аккаунт, не «все»
        self.account_combo.setCurrentIndex(max(index, 0))
        self.account_combo.blockSignals(False)
        self.my_uid = self.account_combo.currentData()

    def _account_changed(self, _index: int) -> None:
        self.my_uid = self.account_combo.currentData()
        self.reload()

    def _reload_overview(self) -> None:
        stats = q.overview(connection(), self.my_uid)
        self.cards["matches"].set(str(stats["matches"]),
                                  f"побед {stats['wins']}, поражений {stats['losses']}")
        self.cards["week"].set(str(stats["week"]), "за последние 7 дней")
        self.cards["duration"].set(q.fmt_hours(stats["duration"]),
                                   f"в среднем {q.fmt_dur(stats['duration'] / max(stats['matches'], 1))}")
        kills = stats["kills"] + stats["bot_kills"]
        self.cards["kills"].set(str(kills),
                                f"игроков {stats['kills']}, ботов {stats['bot_kills']}, "
                                f"смертей {stats['deaths']}")
        accuracy = stats["accuracy"]
        self.cards["accuracy"].set(f"{100 * accuracy:.0f}%" if accuracy is not None else "—",
                                   f"{stats['headshots']} голов / {stats['body_hits']} тело")
        self.cards["damage"].set(f"{int(stats['damage']):,}".replace(",", " "),
                                 "суммарный урон")

        self.bars.set_data(q.daily(connection(), self.my_uid, days=14))

        rows, ids = [], []
        for match in q.recent(connection(), limit=30, my_uid=self.my_uid):
            rows.append([
                q.fmt_dt(q.start_ts(match)), match["mode_name"] or "—",
                map_title(match), result_text(match),
                q.fmt_dur(match["duration_s"]),
                match["my_kills"] or 0,
                match["my_bot_kills"] or 0,
                match["my_deaths"] or 0,
                int(match["my_damage"] or 0),
            ])
            ids.append(match["id"])
        fill_table(self.last_table, rows, right=(5, 6, 7, 8))
        paint_results(self.last_table, 3)
        self._last_ids = ids

    def _reload_matches(self) -> None:
        current_mode = self.mode_combo.currentText()
        modes = q.modes(connection())
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        self.mode_combo.addItem("все режимы", "")
        for mode in modes:
            self.mode_combo.addItem(mode, mode)
        index = self.mode_combo.findData(current_mode if current_mode != "все режимы" else "")
        self.mode_combo.setCurrentIndex(max(index, 0))
        self.mode_combo.blockSignals(False)

        mode = self.mode_combo.currentData() or None
        rows, ids = [], []
        for match in q.recent(connection(), limit=300, my_uid=self.my_uid, mode=mode):
            rows.append([
                q.fmt_dt(q.start_ts(match)), match["mode_name"] or "—",
                map_title(match), result_text(match),
                q.fmt_dur(match["duration_s"]), match["players"] or 0,
                "есть" if match["loadouts"] else "нет",
                f"{match['my_kills'] or 0} / {match['my_bot_kills'] or 0} / "
                f"{match['my_deaths'] or 0}",
                int(match["my_damage"] or 0),
            ])
            ids.append(match["id"])

        selected = self.match_id
        fill_table(self.matches_table, rows, right=(5, 8))
        paint_results(self.matches_table, 3)
        self._match_ids = ids
        if ids:
            index = ids.index(selected) if selected in ids else 0
            self.matches_table.selectRow(index)
            self._show_match(ids[index])

    def _reload_service(self) -> None:
        stats = q.db_stats(connection())
        first, last = stats["first_match"], stats["last_match"]
        self.db_label.setText(
            f"Боёв: {stats['matches']} · игроков в боях: {stats['players']} · "
            f"снаряжений: {stats['loadouts']} · ников: {stats['nicks']} · "
            f"первый бой: {q.fmt_dt(first, True)} · последний: {q.fmt_dt(last, True)}")
        fill_table(self.servers_table, [
            [s["server"], s["region"], s["matches"], q.fmt_dt(s["last_ts"], True)]
            for s in q.servers(connection())], right=(2,))
        fill_table(self.errors_table, [
            [q.fmt_dt(e["ts"], True), e["message"]] for e in q.errors(connection())])

    # -- выбор боя и игрока -------------------------------------------
    def _match_selected(self) -> None:
        rows = self.matches_table.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if index < len(self._match_ids):
            self._show_match(self._match_ids[index])

    def _show_match(self, match_id: str) -> None:
        self.match_id = match_id
        match = q.match(connection(), match_id)
        if not match:
            return
        players = q.match_players(connection(), match_id)
        counts = sum(1 for p in players if p["has_loadout"])
        self.match_info.setText(
            f"{map_title(match)} · {match['mode_name'] or '—'} · "
            f"{q.fmt_dt(q.start_ts(match))} · {q.fmt_dur(match['duration_s'])} · "
            f"{result_text(match)} · бойцов {len(players)} · снаряжение у {counts}")

        rows = []
        for player in players:
            rows.append([
                role_title(player["role"]), player["nick"] or "—",
                refs().operator(player.get("operator_code")),
                player.get("card_level") if player.get("card_level") is not None else "—",
                player.get("prestige") or "—",
                player.get("account_level") or "—",
                player["kills"] or 0, player["bot_kills"] or 0, player["deaths"] or 0,
                player["assists"] or 0, int(player["damage_dealt"] or 0),
                head_cell(player["headshots"], player["body_hits"]),
                int(player["body_hits"] or 0),
                "есть" if player["has_loadout"] else "—",
            ])
        fill_table(self.roster_table, rows,
                   right=(3, 4, 5, 6, 7, 8, 9, 10, 11, 12))

    def _open_match_from_last(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._last_ids):
            match_id = self._last_ids[row]
            self.matches_table.clearSelection()
            self.tabs.setCurrentWidget(self.tabs_pages["Матчи"])
            self.match_id = match_id
            self._reload_matches()
            self._show_match(match_id)

    def _open_player_card(self, index) -> None:
        if not self.match_id:
            return
        players = q.match_players(connection(), self.match_id)
        row = index.row()
        if not (0 <= row < len(players)):
            return
        match = q.match(connection(), self.match_id)
        dialog = PlayerCardDialog(self, match, players[row])
        dialog.exec()

    def make_card_dialog(self):
        """Карточка игрока для снимка/проверки: мой игрок в выбранном бою."""
        players = q.match_players(connection(), self.match_id) if self.match_id else []
        if not players:
            return None
        player = next((p for p in players if p["uid"] == self.my_uid), players[0])
        match = q.match(connection(), self.match_id)
        return PlayerCardDialog(self, match, player)

    # -- настройки ----------------------------------------------------
    def _load_settings_into_fields(self) -> None:
        info = detect.report(self.settings)
        self.log_edit.setText(info["log_path"])
        self.game_edit.setText(info["game_dir"])
        self.replays_edit.setText(info["replays_dir"])
        folders = replays_mod.default_folders(self.settings)
        files = replays_mod.list_replays(folders)
        self.replays_info.setText(
            f"Папок с реплеями: {len(folders)} · файлов: {len(files)} · "
            + " | ".join(str(f) for f in folders))

    def _pick_path(self, edit: QLineEdit, kind: str) -> None:
        if kind == "log":
            chosen, _ = QFileDialog.getOpenFileName(self, "Файл лога", edit.text(),
                                                    "Лог (*.log);;Все файлы (*)")
        else:
            chosen = QFileDialog.getExistingDirectory(self, "Папка", edit.text())
        if chosen:
            edit.setText(chosen)

    def _auto_detect(self) -> None:
        cfg = dict(self.settings, log_path="", game_dir="", replays_dir="")
        info = detect.report(cfg)
        self.log_edit.setText(info["log_path"])
        self.game_edit.setText(info["game_dir"])
        self.replays_edit.setText(info["replays_dir"])
        checks = info["checks"]
        self.replays_info.setText(
            "Найдено: " + ", ".join(f"{name} {'OK' if ok else 'НЕТ'}"
                                    for name, ok in checks.items()))

    def _save_settings(self) -> None:
        self.settings["log_path"] = self.log_edit.text().strip()
        self.settings["game_dir"] = self.game_edit.text().strip()
        self.settings["replays_dir"] = self.replays_edit.text().strip()
        if paths.save_settings(self.settings):
            self.watcher = None
            self._load_settings_into_fields()
            self.status.setText("настройки сохранены")

    def _rescan_replays(self) -> None:
        folders = replays_mod.default_folders(self.settings)
        files = replays_mod.list_replays(folders)
        if not files:
            QMessageBox.information(self, "Реплеи",
                                    "Папок с реплеями не нашлось.\n"
                                    "Укажи папку в настройках и сохрани.")
            return
        progress = QProgressDialog(f"Разбираю реплеи (0 из {len(files)})…", "Отмена",
                                   0, len(files), self)
        progress.setWindowTitle("Реплеи")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def on_progress(number: int, total: int, path, status: str) -> None:
            progress.setMaximum(total)
            progress.setValue(number - 1)
            progress.setLabelText(f"{number} из {total}: {os.path.basename(str(path))[:60]}")
            QApplication.processEvents()
            if progress.wasCanceled():
                raise KeyboardInterrupt

        try:
            stats = replays_mod.scan(connection(), folders, progress=on_progress)
            message = replays_mod.summary_text(stats)
            if stats["failed_list"]:
                message += "\n\nНе разобрано:\n" + "\n".join(
                    f"• {os.path.basename(p)}: {why}" for p, why in stats["failed_list"][:8])
            progress.close()
            QMessageBox.information(self, "Реплеи", message)
        except KeyboardInterrupt:
            progress.close()
            self.status.setText("разбор реплеев отменён")
        finally:
            progress.close()
            self.reload()

    def _toggle_auto_replays(self, _state: int = 0) -> None:
        self.settings["import_replays_auto"] = self.auto_replays_box.isChecked()
        paths.save_settings(self.settings)
        self.watcher = None

    def _toggle_autostart(self, _state: int = 0) -> None:
        wanted = self.autostart_box.isChecked()
        if paths.set_autostart(wanted):
            self.autostart_box.setChecked(paths.autostart_enabled())
            self.status.setText("автозапуск " + ("включён" if wanted else "выключен"))
        else:
            self.autostart_box.setChecked(not wanted)
            self.status.setText("автозапуск изменить не удалось")

    def _update_refs_info(self) -> None:
        refs = refs_mod.get(connection())
        if refs.meta.get("updated_at"):
            self.refs_info.setText(refs.updated_text())
        else:
            self.refs_info.setText(
                "Справочники не загружены: нажми «Обновить справочники из клиента». "
                "Пока интерфейс показывает ключи из реплеев.")

    def _update_refs(self) -> None:
        steps = client_data.STEPS
        progress = QProgressDialog(f"Обновляю справочники (1 из {len(steps)})…",
                                   None, 0, len(steps), self)
        progress.setWindowTitle("Справочники из клиента")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def on_step(index: int, total: int, text: str) -> None:
            progress.setMaximum(total)
            progress.setValue(index)
            progress.setLabelText(f"{index + 1} из {total}: {text}")
            QApplication.processEvents()

        try:
            counts = client_data.update(connection(), self.settings, progress=on_step)
        except client_data.ClientDataError as e:
            progress.close()
            QMessageBox.warning(self, "Справочники", str(e))
            return
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Справочники",
                                 f"Не удалось обновить: {type(e).__name__}: {e}")
            return
        progress.close()
        refs_mod.get(connection(), reload=True)
        self._update_refs_info()
        self.reload()
        QMessageBox.information(self, "Справочники",
                                "Готово.\n\n" + client_data.summary_text(counts))


def make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#14171c"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(ACCENT), 4))
    painter.setBrush(QColor("#1b1f26"))
    painter.drawRoundedRect(6, 6, 52, 52, 12, 12)
    painter.setPen(QPen(QColor(ACCENT), 6))
    for index, height in enumerate((14, 26, 38)):
        painter.drawLine(20 + index * 12, 50, 20 + index * 12, 50 - height)
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    ap = argparse.ArgumentParser(description="Окно CaliberStat")
    ap.add_argument("--db", default=str(paths.db_path()))
    ap.add_argument("--shot", help="сохранить снимок окна и выйти")
    ap.add_argument("--tab", help="какую вкладку открыть для снимка")
    ap.add_argument("--card", help="сохранить снимок карточки игрока и выйти")
    ap.add_argument("--bottom", action="store_true",
                    help="для --card: прокрутить карточку вниз (дерево навыков)")
    ap.add_argument("--card-tab", help="для --card: какую вкладку открыть")
    args = ap.parse_args()

    paths.ensure_dirs()
    set_db_path(args.db)
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(STYLE)
    window = Hub(args.db)
    window.show()
    if args.tab:
        for index in range(window.tabs.count()):
            if window.tabs.tabText(index) == args.tab:
                window.tabs.setCurrentIndex(index)
    app.processEvents()

    if args.card:
        dialog = window.make_card_dialog()
        if dialog:
            dialog.show()
            app.processEvents()
            if args.bottom:
                dialog.scroll_to_end()
                app.processEvents()
            if args.card_tab:
                for index in range(dialog.tabs.count()):
                    if dialog.tabs.tabText(index).lower() == args.card_tab.lower():
                        dialog.tabs.setCurrentIndex(index)
                        break
                app.processEvents()

            def finish() -> None:
                dialog.grab().save(args.card)
                dialog.close()
                window.close()
                app.quit()

            QTimer.singleShot(400, finish)
            return app.exec()
        print("[!] нет боя для карточки")
        return 1

    if args.shot:
        def snapshot() -> None:
            window.grab().save(args.shot)
            window.close()
            app.quit()
        QTimer.singleShot(400, snapshot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
