# CaliberStat

Статистика «Калибра» из лога игры и реплеев: обзор, матчи, состав боя, подробная
карточка игрока. Портативно: папку скопировал — работает, данные и настройки живут
внутри папки.

План работ и границы проекта — [docs/PLAN.md](docs/PLAN.md).

## Что уже есть

| Файл | Что делает |
|---|---|
| `gui.py` | окно: обзор, матчи, состав боя, карточка игрока, служебное, настройки |
| `queries.py` | все запросы к базе для окна |
| `collector.py` | читает `Player.log`, складывает бои, лобби, профили и ошибки в SQLite |
| `caliber_db.py` | схема базы и запись (без клана, квестов и войн) |
| `replay.py` | разбор `*.bytes`: протокол боя, снаряжение, навыки |
| `replays.py` | пакетный разбор реплеев: обход папок, прогресс, отчёт («перечитать реплеи») |
| `paths.py` | папки программы (портативность), `settings.json` |
| `detect.py` | автоопределение лога, папки клиента и папки реплеев |

Что осталось по плану: справочники из клиента (русские названия карт, оружия,
оперативников), автоподхват реплеев после боя, трей с автозапуском, сборка exe.

## Как выглядит

![Обзор](docs/preview-overview.png)
![Матчи и состав боя](docs/preview-matches.png)
![Карточка игрока](docs/preview-card.png)

## Запуск из исходников

```powershell
# один раз: окружение рядом с проектом
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install pyside6

# окно
.\.venv\Scripts\python.exe gui.py

# залить все реплеи в базу (то же делает кнопка «Перечитать реплеи»)
.\.venv\Scripts\python.exe replays.py --scan

# разобрать текущий лог игры
.\.venv\Scripts\python.exe collector.py --once

# что нашлось на этой машине
.\.venv\Scripts\python.exe detect.py --show
```

База — `data/caliberstat.db`. Реплеи, залитые вручную, — `replays/inbox/`.

## Структура

```
gui.py               окно (PySide6)
app.py               точка входа для трея (в работе)
collector.py         сбор из лога
caliber_db.py        база
replay.py            разбор одного реплея
replays.py           пакетный разбор реплеев
queries.py           запросы для окна
paths.py             папки и настройки
detect.py            автоопределение путей
client_data.py       справочники из клиента (в работе)
data/  logs/  ref/  replays/   создаются при первом запуске
```

## Проверки

```powershell
python queries.py            # цифры обзора без окна
python replays.py --scan     # разбор реплеев с прогрессом
python gui.py --shot docs\preview-overview.png   # снимок окна
```
