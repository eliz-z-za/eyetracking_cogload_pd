# Build exe for vibe-eyetracking
# Run: .\build_exe.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== Building Experiment.exe ===" -ForegroundColor Cyan

# Проверка venv
if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Host "Creating venv..." -ForegroundColor Yellow
    python -m venv .venv
}
.\.venv\Scripts\Activate.ps1

# Установка зависимостей
Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt -q

# Сборка
Write-Host "Running PyInstaller..." -ForegroundColor Yellow
pyinstaller experiment.spec --noconfirm

if ($LASTEXITCODE -eq 0) {
    # Скопировать flags.txt из корня проекта (production-шаблон) рядом с exe
    $flagsPath = "dist\Experiment\flags.txt"
    Copy-Item -Path "flags.txt" -Destination $flagsPath -Force
    Write-Host ""
    Write-Host "Done! Exe: dist\Experiment\Experiment.exe" -ForegroundColor Green
    Write-Host "      flags.txt created: $flagsPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "On another computer you need:" -ForegroundColor Cyan
    Write-Host "  1. Gazepoint Control (if using eye tracker)"
    Write-Host "  2. Copy the whole dist\Experiment\ folder"
    Write-Host "  3. Edit flags.txt before each session:"
    Write-Host "       PD_GROUP=1          # 1 or 2 (condition order: flat/sections)"
    Write-Host "       PD_DATASET_ORDER=1  # 1 or 2 (dataset order: alpha/beta)"
    Write-Host "       PD_TASK_ORDER_INDEX=0  # 0-3"
    Write-Host ""
    Write-Host "Post-processing (run on dev machine with Python venv):" -ForegroundColor Cyan
    Write-Host "  python analysis_pd.py <participant_id> <data_dir>"
    Write-Host "  python analysis_paintings.py"
    Write-Host "  python group_analysis_pd.py <data_dir>"
    Write-Host ""
    Write-Host "NOTE: analysis_pd.py is NOT bundled in the exe." -ForegroundColor Yellow
    Write-Host "      Run it separately after copying data back to dev machine." -ForegroundColor Yellow
} else {
    Write-Host "Build failed" -ForegroundColor Red
    exit 1
}
