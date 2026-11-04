@echo off
rem Сборка портативного CaliberStat: dist\CaliberStat\CaliberStat.exe
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] Нет окружения .venv - выполни: python -m venv .venv
    exit /b 1
)

echo [*] Чищу прошлую сборку
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [*] Собираю через PyInstaller
".venv\Scripts\python.exe" -m PyInstaller CaliberStat.spec --noconfirm
if errorlevel 1 (
    echo [!] Сборка упала
    exit /b 1
)

echo [*] Готово: dist\CaliberStat\CaliberStat.exe
endlocal
