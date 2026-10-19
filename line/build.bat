@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "STAGE=build\stage"

if not exist "%PY%" (
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python Launcher with Python 3.12 is required to create the isolated LINE build environment.
    echo Install Python 3.12, then run build.bat again.
    exit /b 1
  )
  py -3.12 -m venv "%~dp0.venv" || goto :fail
)

"%PY%" -c "import sys; assert sys.version_info[:2] == (3, 12), 'LINE build requires Python 3.12'" || goto :fail
"%PY%" "%~dp0..\tools\require_non_admin.py" || goto :fail
"%PY%" -m pip install --disable-pip-version-check -r requirements-build.txt || goto :fail
"%PY%" -m unittest discover -s tests -v || goto :fail
"%PY%" -m py_compile config_store.py clipboard_support.py line_automation.py line_status_gui.py line_tray.py tests\test_core.py || goto :fail

if exist build rmdir /s /q build
mkdir "%STAGE%" || goto :fail

if exist app.ico (
  "%PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller --noconfirm --onefile --windowed ^
    --name line-status-changer --icon "%~dp0app.ico" --add-data "%~dp0app.ico;." ^
    --distpath "%STAGE%" --workpath "build\work" --specpath build ^
    --collect-all pywinauto --collect-submodules comtypes line_status_gui.py || goto :fail
) else (
  "%PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller --noconfirm --onefile --windowed ^
    --name line-status-changer --distpath "%STAGE%" --workpath "build\work" --specpath build ^
    --collect-all pywinauto --collect-submodules comtypes line_status_gui.py || goto :fail
)
if not exist "%STAGE%\line-status-changer.exe" goto :fail
"%PY%" tools\write_build_manifest.py "%STAGE%" || goto :fail

rem Keep the user's live settings when rebuilding; fall back to the source config,
rem then create a clean default config for a fresh checkout.
if exist "dist\line_config.json" (
  copy /y "dist\line_config.json" "%STAGE%\line_config.json" >nul || goto :fail
) else if exist "line_config.json" (
  copy /y "line_config.json" "%STAGE%\line_config.json" >nul || goto :fail
) else (
  "%PY%" -c "from config_store import default_config, save_config; save_config(r'%STAGE%\line_config.json', default_config())" || goto :fail
)
"%PY%" -c "from config_store import load_config; load_config(r'%STAGE%\line_config.json', create_if_missing=False)" || goto :fail

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_line_build.ps1" || goto :fail

if not exist dist\line-status-changer.exe goto :fail
if not exist dist\SHA256SUMS.txt goto :fail
if not exist dist\line_config.json goto :fail
echo.
echo BUILD OK: dist\line-status-changer.exe with SHA256SUMS.txt and separate line_config.json
echo Next: double-click dist\line-status-changer.exe. Press X to hide it in the system tray.
exit /b 0

:fail
echo.
echo BUILD FAILED. The previous dist folder was preserved when possible.
exit /b 1
