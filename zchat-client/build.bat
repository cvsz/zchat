@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo ERROR: Run the repository build.bat first to create .venv.
    exit /b 1
)

"%VENV_PY%" tools\require_non_admin.py
if errorlevel 1 exit /b 1

if not exist "zchat-client\app.ico" (
    echo ERROR: zchat-client\app.ico is required to build the application icon.
    exit /b 1
)

"%VENV_PY%" -m pip install -r zchat-client\requirements-build.txt
if errorlevel 1 exit /b 1

"%VENV_PY%" -m py_compile zchat-client\main.py zchat-client\src\zchat_client\__init__.py zchat-client\src\zchat_client\session.py zchat-client\src\zchat_client\ui.py zchat-client\src\zchat_client\process_monitor.py src\features\status_changer_entry.py src\features\status_changer_gui.py src\features\chat_im_private_gui.py control-server\src\camfrog_control_server\transport.py
if errorlevel 1 exit /b 1

"%VENV_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name zchat-client --icon zchat-client\app.ico --add-data "zchat-client\app.ico;." --distpath dist\zchat-client --workpath build\zchat-client --paths zchat-client\src --paths control-server\src --paths src\camfrog --paths src\features --collect-all pywinauto --collect-all psutil --collect-submodules comtypes --hidden-import camfrog_windows_runtime --hidden-import chat_im_private_gui zchat-client\main.py
if errorlevel 1 exit /b 1

if not exist dist\zchat-client\zchat-client.exe (
    echo ERROR: dist\zchat-client\zchat-client.exe was not created.
    exit /b 1
)

"%VENV_PY%" -c "import hashlib,pathlib; p=pathlib.Path('dist/zchat-client/zchat-client.exe'); digest=hashlib.sha256(p.read_bytes()).hexdigest(); pathlib.Path('dist/zchat-client/SHA256SUMS.txt').write_text(f'{digest}  {p.name}\n',encoding='ascii')"
if errorlevel 1 exit /b 1

echo Built dist\zchat-client\zchat-client.exe
