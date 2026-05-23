@echo off
REM Сборка exe для запуска эксперимента (текущая версия)
REM По умолчанию: один exe (dist\VibeExperiment.exe) — первый запуск на другом ПК может занять 1-3 мин.
REM Для быстрого запуска: build_exe.bat onedir → dist\VibeExperiment\VibeExperiment.exe

cd /d "%~dp0"

python -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)" 2>nul || (
    echo Install dependencies first: pip install -r requirements.txt
    exit /b 1
)

if /i "%~1"=="onedir" (
    echo Building VibeExperiment folder ^(fast startup on other PCs^)...
    pyinstaller --clean --noconfirm vibe_experiment_onedir.spec
) else (
    echo Building VibeExperiment folder...
    pyinstaller --clean --noconfirm vibe_experiment.spec
)

if %ERRORLEVEL% neq 0 ( echo Build failed. & exit /b 1 )

REM Создать production vibe_flags.txt рядом с exe
set "FLAGS=dist\VibeExperiment\vibe_flags.txt"
(
echo # vibe_flags.txt ^— настройки запуска ^(можно редактировать без пересборки^)
echo # Формат: ФЛАГ=значение ^(true/false/yes/no/1/0 или целое число^)
echo # Строки с # игнорируются.
echo #
echo # ^%WARNING^% Перед каждой сессией задай PD_GROUP и PD_TASK_ORDER_INDEX^!
echo.
echo EYE_TRACKER_MOCK=false
echo CURSOR_ONLY_MODE=false
echo PD_SKIP_BLOCKS=false
echo.
echo # Группа контрбалансировки: 1 или 2 ^(чередовать между участниками^)
echo PD_GROUP=1
echo.
echo # Индекс порядка задач ^(0-3^)
echo PD_TASK_ORDER_INDEX=0
) > "%FLAGS%"

echo.
echo Done. Run: dist\VibeExperiment\VibeExperiment.exe
echo vibe_flags.txt created: %FLAGS%
