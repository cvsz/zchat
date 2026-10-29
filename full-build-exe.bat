@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo FULL BUILD + CODE SIGNING (ZCoder / ZEAZDEV)
echo ============================================================
echo.

rem --- Load environment variables from .env if present ---
if exist ".env" (
    echo [Config] Loading environment from .env ...
    for /f "delims=" %%i in (.env) do (
        echo %%i | findstr /r "^#" >nul || set "%%i"
    )
)

call full-build.bat %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED: full-build.bat returned error
    exit /b 1
)

echo.
echo [Code Signing] Signing all generated .exe files under dist\ ...
if defined ZCODER_CERT_THUMBPRINT (
    powershell -ExecutionPolicy Bypass -File "tools\sign_exe.ps1" -Thumbprint "%ZCODER_CERT_THUMBPRINT%"
) else (
    powershell -ExecutionPolicy Bypass -File "tools\sign_exe.ps1"
)

if errorlevel 1 (
    echo.
    echo CODE SIGNING FAILED (check certificate / thumbprint)
    exit /b 1
) else (
    echo.
    echo CODE SIGNING SUCCESS: All .exe files signed successfully.
)

endlocal
