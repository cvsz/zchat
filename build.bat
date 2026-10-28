@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "FEATURE_NAME=camfrog-features"

rem ============================================================
rem Select supported 64-bit Python
rem ============================================================
set "PYSEL="
py -3.12-64 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYSEL=py -3.12-64"

if not defined PYSEL set "PYSEL=python"

%PYSEL% tools\require_non_admin.py
if errorlevel 1 exit /b 1

echo Using Python / ใช้ Python:
%PYSEL% -c "import sys,struct;print(sys.version);print(struct.calcsize('P')*8,'bit')"
if errorlevel 1 goto :nopython

for /f %%i in ('%PYSEL% -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])"') do (
    set "PYV=%%i"
)

%PYSEL% tools\doctor.py --quick
if errorlevel 1 goto :nopython


rem ============================================================
rem Create/recreate virtual environment
rem ============================================================

if exist ".venv\pyver.txt" (
    findstr /x "%PYV%" ".venv\pyver.txt" >nul
    if errorlevel 1 rmdir /s /q ".venv"
)

if exist ".venv" (
    if not exist ".venv\pyver.txt" rmdir /s /q ".venv"
)

if not exist ".venv" (
    %PYSEL% -m venv ".venv"
    if errorlevel 1 goto :fail

    > ".venv\pyver.txt" echo %PYV%
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" goto :fail

echo Build Python:
"%VENV_PY%" -c "import sys;print(sys.executable);print(sys.version)"
if errorlevel 1 goto :fail


rem ============================================================
rem Dependencies
rem ============================================================

"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail

"%VENV_PY%" -m pip install --only-binary=:all: "pywin32>=311"
if errorlevel 1 goto :pywin

"%VENV_PY%" -m pip install -r requirements-dev.txt "pyinstaller>=6.9"
if errorlevel 1 goto :fail


rem ============================================================
rem Quality gates
rem ============================================================

echo.
echo === PYTHON COMPILE CHECK ===

"%VENV_PY%" -m py_compile ^
    src\camfrog\camfrog_auto.py ^
    src\camfrog\camfrog_gui.py ^
    src\features\camfrog_status_gui.py ^
    src\camfrog\camfrog_windows_runtime.py ^
    src\camfrog\camfrog_status_engine.py ^
    src\camfrog\camfrog_emergency_stop.py ^
    src\features\camfrog_private_chat_gui.py ^
    src\features\camfrog_music_gui.py ^
    src\features\room_control_gui.py ^
    src\features\im_autoreply_gui.py ^
    src\features\status_random_gui.py ^
    src\features\status_marquee_gui.py ^
    src\features\status_changer_gui.py ^
    src\features\chat_im_private_gui.py ^
    src\features\room_control_entry.py ^
    src\features\chat_im_private_entry.py ^
    src\features\status_random_entry.py ^
    src\features\status_marquee_entry.py ^
    src\features\status_changer_entry.py ^
    src\features\im_autoreply_entry.py ^
    src\features\autodj_entry.py ^
    src\camfrog\camfrog_tray.py ^
    src\camfrog\clipboard_support.py ^
    src\features\web_status_entry.py ^
    src\features\name_color_changer_entry.py ^
    src\features\name_color_changer_gui.py ^
    src\features\icon_changer_entry.py ^
    src\features\icon_changer_gui.py ^
    src\features\name_changer_entry.py ^
    src\features\name_changer_gui.py ^
    tools\web_status.py

if errorlevel 1 goto :fail


echo.
echo === TESTS ===

rem Use a fresh per-invocation base temp directory in build/ to avoid WinError 5 /
rem permission errors on Windows when %TEMP%\pytest-of-* or a stale build temp
rem directory has restrictive ACLs or a file is still held by another process.
if not exist "build" mkdir "build"
set "PYTEST_RUN_ID=%RANDOM%-%RANDOM%-%RANDOM%"
set "PYTEST_TMP_DIR=%CD%\build\pytest_tmp_%PYTEST_RUN_ID%"

rem Tk initialization on Windows is process-global. GUI fixtures that create or
rem destroy a root in one test module can invalidate Tcl's tcl_findLibrary
rem command for a later module. Run the private-chat GUI module in an isolated
rem interpreter while still testing every test (no skips or xfails).
"%VENV_PY%" -m pytest -p no:cacheprovider -q tests --basetemp="%PYTEST_TMP_DIR%" --ignore=tests/test_private_chat_gui.py
if errorlevel 1 goto :fail

echo.
echo === ISOLATED PRIVATE-CHAT GUI TESTS ===
"%VENV_PY%" -m pytest -p no:cacheprovider -q tests/test_private_chat_gui.py --basetemp="%PYTEST_TMP_DIR%_private"
if errorlevel 1 goto :fail

echo.
echo === WINDOWS MCI INTEGRATION TESTS ===
"%VENV_PY%" -m pytest -p no:cacheprovider -q windows_tests --basetemp="%PYTEST_TMP_DIR%_mci"
if errorlevel 1 goto :fail

echo.
echo === CONFIG VALIDATION EN ===

"%VENV_PY%" -m src.camfrog.camfrog_auto check --config config.example.json
if errorlevel 1 goto :fail


echo.
echo === CONFIG VALIDATION TH ===

"%VENV_PY%" -m src.camfrog.camfrog_auto --lang th check --config config.example.json
if errorlevel 1 goto :fail


rem ============================================================
rem Stop running dist applications
rem ============================================================

echo.
echo === STOP OLD APPLICATIONS ===

powershell.exe -NoProfile -ExecutionPolicy Bypass ^
    -File "%~dp0tools\end_dist_processes.ps1"

if errorlevel 1 goto :fail

"%VENV_PY%" -c "import time; time.sleep(2)"
if errorlevel 1 goto :fail


rem ============================================================
rem Clean old build
rem ============================================================

echo.
echo === CLEAN BUILD DIRECTORIES ===

if exist "build" rmdir /s /q "build"
if not exist "dist" mkdir "dist"
if exist "dist\room-control\room-control.exe" del /q "dist\room-control\room-control.exe"
if exist "dist\chat-im-private\chat-im-private.exe" del /q "dist\chat-im-private\chat-im-private.exe"
if exist "dist\status-random\status-random.exe" del /q "dist\status-random\status-random.exe"
if exist "dist\status-marquee\status-marquee.exe" del /q "dist\status-marquee\status-marquee.exe"
if exist "dist\status-changer\status-changer.exe" del /q "dist\status-changer\status-changer.exe"
if exist "dist\im-autoreply\im-autoreply.exe" del /q "dist\im-autoreply\im-autoreply.exe"
if exist "dist\autodj\autodj.exe" del /q "dist\autodj\autodj.exe"
if exist "dist\auto-dj\AutoDJ.exe" del /q "dist\auto-dj\AutoDJ.exe"
if exist "dist\music-dj\music-dj.exe" del /q "dist\music-dj\music-dj.exe"
if exist "dist\music-dj" rmdir /s /q "dist\music-dj"
if exist "dist\web-status\web-status.exe" del /q "dist\web-status\web-status.exe"
if exist "dist\web-status.exe" del /q "dist\web-status.exe"
if exist "dist\cloudbot-manager\CloudBot-Manager.exe" del /q "dist\cloudbot-manager\CloudBot-Manager.exe"
if exist "dist\CloudBot-Manager.exe" del /q "dist\CloudBot-Manager.exe"
if exist "dist\name-color-changer\name-color-changer.exe" del /q "dist\name-color-changer\name-color-changer.exe"
if exist "dist\name-color-changer.exe" del /q "dist\name-color-changer.exe"
if exist "dist\icon-changer\icon-changer.exe" del /q "dist\icon-changer\icon-changer.exe"
if exist "dist\icon-changer.exe" del /q "dist\icon-changer.exe"
if exist "dist\name-changer\name-changer.exe" del /q "dist\name-changer\name-changer.exe"
if exist "dist\name-changer.exe" del /q "dist\name-changer.exe"
if exist "dist\camfrog-auto.exe" del /q "dist\camfrog-auto.exe"
if exist "dist\camfrog-auto-gui.exe" del /q "dist\camfrog-auto-gui.exe"
if exist "dist\zcfato.exe" del /q "dist\zcfato.exe"
if exist "dist\SHA256SUMS.txt" del /q "dist\SHA256SUMS.txt"
if exist "camfrog-features-windows.zip" del /q "camfrog-features-windows.zip"
if exist "camfrog-auto-windows.zip" del /q "camfrog-auto-windows.zip"
rem Remove obsolete single-app documentation and launchers from the old dist layout.
for %%F in (README.md CONFIG.md check-config.bat gui.bat run-background.bat start-background.bat state.bat stop.bat test-rules.bat) do (
    if exist "dist\%%F" del /q "dist\%%F"
)


rem ============================================================
rem Icon
rem ============================================================

set "ICONARG="

if not exist "app.ico" (
    echo ERROR: app.ico not found; refusing to build apps with a default Python icon.
    goto :fail
)
set "ICONARG=--icon app.ico --add-data app.ico;."


rem ============================================================
rem Feature executables
rem ============================================================

echo.
echo === BUILD ROOM CONTROL ===

"%VENV_PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\room-control --workpath build\room-control ^
    --name room-control %ICONARG% ^
    --collect-all pywinauto --collect-all psutil --collect-submodules comtypes ^
    --paths src\camfrog --paths src\features ^
    src\features\room_control_entry.py
if errorlevel 1 goto :fail


echo.
echo === BUILD PRIVATE CHAT WINDOW MANAGER ===

"%VENV_PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\chat-im-private --workpath build\chat-im-private ^
    --name chat-im-private %ICONARG% ^
    --collect-all pywinauto --collect-all psutil --collect-submodules comtypes ^
    --paths src\camfrog --paths src\features ^
    src\features\chat_im_private_entry.py
if errorlevel 1 goto :fail


echo.
echo === BUILD RANDOM STATUS ===

"%VENV_PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\status-random --workpath build\status-random ^
    --name status-random %ICONARG% ^
    --collect-all pywinauto --collect-all psutil --collect-submodules comtypes ^
    --hidden-import camfrog_status_engine --hidden-import camfrog_windows_runtime ^
    --paths src\camfrog --paths src\features ^
    src\features\status_random_entry.py
if errorlevel 1 goto :fail


echo.
echo === BUILD MARQUEE STATUS ===

"%VENV_PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\status-marquee --workpath build\status-marquee ^
    --name status-marquee %ICONARG% ^
    --collect-all pywinauto --collect-all psutil --collect-submodules comtypes ^
    --hidden-import camfrog_status_engine --hidden-import camfrog_windows_runtime ^
    --paths src\camfrog --paths src\features ^
    src\features\status_marquee_entry.py
if errorlevel 1 goto :fail


echo.
echo === BUILD COMBINED STATUS CHANGER ===

"%VENV_PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\status-changer --workpath build\status-changer ^
    --name status-changer %ICONARG% ^
    --collect-all pywinauto --collect-all psutil --collect-submodules comtypes ^
    --hidden-import camfrog_status_engine --hidden-import camfrog_windows_runtime ^
    --paths src\camfrog --paths src\features ^
    src\features\status_changer_entry.py
if errorlevel 1 goto :fail


echo.
echo === BUILD PRIVATE IM AUTO-REPLY ===

"%VENV_PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\im-autoreply --workpath build\im-autoreply ^
    --name im-autoreply %ICONARG% ^
    --collect-all pywinauto --collect-all psutil --collect-submodules comtypes ^
    --paths src\camfrog --paths src\features ^
    src\features\im_autoreply_entry.py
if errorlevel 1 goto :fail


echo.
echo === BUILD WEB STATUS UPDATER (GUI + CONSOLE PROTOTYPE) ===

"%VENV_PY%" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\web-status --workpath build\web-status ^
    --name web-status %ICONARG% ^
    --collect-all psutil ^
    --add-data "assets\zttato.png;assets" ^
    --paths src\camfrog --paths src\features ^
    src\features\web_status_entry.py
if errorlevel 1 goto :fail

echo.
echo === BUILD STANDALONE CLOUD BOT MANAGER (CLOUDBOT ONLY) ===

"%VENV_PY%" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\cloudbot-manager --workpath build\cloudbot-manager ^
    --name CloudBot-Manager %ICONARG% ^
    --collect-all psutil ^
    --add-data "assets\zttato.png;assets" ^
    --paths src\camfrog --paths src\features ^
    src\features\cloudbot_manager_entry.py
if errorlevel 1 goto :fail

echo.
echo === BUILD STANDALONE NAME COLOR CHANGER ===

"%VENV_PY%" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\name-color-changer --workpath build\name-color-changer ^
    --name name-color-changer %ICONARG% ^
    --paths src\camfrog --paths src\features ^
    src\features\name_color_changer_entry.py
if errorlevel 1 goto :fail

echo.
echo === BUILD STANDALONE ICON CHANGER ===

"%VENV_PY%" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\icon-changer --workpath build\icon-changer ^
    --name icon-changer %ICONARG% ^
    --paths src\camfrog --paths src\features ^
    src\features\icon_changer_entry.py
if errorlevel 1 goto :fail

echo.
echo === BUILD STANDALONE NAME CHANGER ===

"%VENV_PY%" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\name-changer --workpath build\name-changer ^
    --name name-changer %ICONARG% ^
    --paths src\camfrog --paths src\features ^
    src\features\name_changer_entry.py
if errorlevel 1 goto :fail


echo === BUILD MUSIC DJ ===

"%VENV_PY%" -W "ignore:invalid escape sequence:SyntaxWarning" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --distpath dist\auto-dj --workpath build\auto-dj ^
    --name AutoDJ %ICONARG% ^
    --add-data "assets\zeazdev-room.png;assets" ^
    --collect-all pywinauto --collect-all psutil --collect-submodules comtypes ^
    --paths src\camfrog --paths src\features ^
    src\features\autodj_entry.py
if errorlevel 1 goto :fail


for %%D in (room-control chat-im-private status-random status-marquee status-changer im-autoreply auto-dj web-status cloudbot-manager name-color-changer icon-changer name-changer) do (
    copy /y "app.ico" "dist\%%D\app.ico" >nul
    if errorlevel 1 goto :fail
)

rem ============================================================
rem Package + SHA256
rem ============================================================

echo.
echo === PACKAGE FEATURE APPS ===

"%VENV_PY%" tools\package.py "%FEATURE_NAME%" --executables-only
if errorlevel 1 goto :fail


rem Final verification
rem ============================================================

echo.
echo === VERIFY FEATURE APPS ===

if not exist "dist\room-control\room-control.exe" goto :fail
if not exist "dist\chat-im-private\chat-im-private.exe" goto :fail
if not exist "dist\status-random\status-random.exe" goto :fail
if not exist "dist\status-marquee\status-marquee.exe" goto :fail
if not exist "dist\status-changer\status-changer.exe" goto :fail
if not exist "dist\im-autoreply\im-autoreply.exe" goto :fail
if not exist "dist\web-status\web-status.exe" goto :fail
if not exist "dist\cloudbot-manager\CloudBot-Manager.exe" goto :fail
if not exist "dist\name-color-changer\name-color-changer.exe" goto :fail
if not exist "dist\icon-changer\icon-changer.exe" goto :fail
if not exist "dist\name-changer\name-changer.exe" goto :fail
if not exist "dist\auto-dj\AutoDJ.exe" goto :fail
if not exist "dist\SHA256SUMS.txt" goto :fail
if not exist "%FEATURE_NAME%-windows.zip" goto :fail

echo.
echo BUILD OK / สร้างสำเร็จ
echo.
echo dist\room-control\room-control.exe
echo dist\chat-im-private\chat-im-private.exe
echo dist\status-random\status-random.exe
echo dist\status-marquee\status-marquee.exe
echo dist\status-changer\status-changer.exe
echo dist\im-autoreply\im-autoreply.exe
echo dist\auto-dj\AutoDJ.exe
echo dist\web-status\web-status.exe
echo dist\cloudbot-manager\CloudBot-Manager.exe
echo dist\name-color-changer\name-color-changer.exe
echo dist\icon-changer\icon-changer.exe
echo dist\name-changer\name-changer.exe
echo dist\SHA256SUMS.txt
echo %FEATURE_NAME%-windows.zip

echo.
echo Each executable creates its own config and runtime data beside itself.

exit /b 0


rem Error handlers
:pywin
%PYSEL% tools\doctor.py

echo.
echo pywin32 has no wheel for this Python.
echo Install CPython 3.12 64-bit, delete .venv, then run build.bat again.
echo.
echo ไม่มี pywin32 สำหรับ Python ตัวนี้
echo ติดตั้ง CPython 3.12 แบบ 64-bit ลบ .venv แล้วรัน build.bat ใหม่
echo.
echo winget install Python.Python.3.12

goto :fail


:nopython
echo.
echo Python not found or unusable.
echo Install CPython 3.12 64-bit:
echo.
echo winget install Python.Python.3.12
echo.
echo ไม่พบ Python หรือ Python ไม่รองรับ

goto :fail


:fail
echo.
echo BUILD FAILED / สร้างไม่สำเร็จ
exit /b 1
