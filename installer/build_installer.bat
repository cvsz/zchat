@echo off
chcp 65001 >nul
setlocal EnableExtensions

:: ============================================================
:: ZChat Installer Build Script
:: Builds NSIS installer for Windows 11
:: ============================================================

echo ============================================================
echo ZChat Installer Build
echo ============================================================

:: ------------------------------------------------------------
:: Configuration
:: ------------------------------------------------------------
set "NSIS_PATH=%ProgramFiles%\NSIS\makensis.exe"
if not exist "%NSIS_PATH%" (
    set "NSIS_PATH=%ProgramFiles(x86)%\NSIS\makensis.exe"
)
if not exist "%NSIS_PATH%" (
    echo ERROR: NSIS not found. Install from https://nsis.sourceforge.io/
    exit /b 1
)

set "SRC_ROOT=%CD%\.."
set "INSTALLER_DIR=%CD%"
set "OUT_DIR=%SRC_ROOT%\dist\installer"
set "NSI_FILE=%INSTALLER_DIR%\ZChatInstaller.nsi"
set "VERSION=2.20.0"

:: ------------------------------------------------------------
:: Prerequisites check
:: ------------------------------------------------------------
echo.
echo === Checking Prerequisites ===

if not exist "%NSI_FILE%" (
    echo ERROR: NSI file not found: %NSI_FILE%
    exit /b 1
)

if not exist "%SRC_ROOT%\dist\auto-dj\AutoDJ.exe" (
    echo WARNING: AutoDJ.exe not found. Run full-build.bat first.
)

if not exist "%SRC_ROOT%\LICENSE.txt" (
    copy "%INSTALLER_DIR%\LICENSE.txt" "%SRC_ROOT%\LICENSE.txt" >nul 2>&1
)

:: ------------------------------------------------------------
:: Prepare installer staging directory
:: ------------------------------------------------------------
echo.
echo === Preparing Staging ===
if exist "%OUT_DIR%" rmdir /s /q "%OUT_DIR%"
mkdir "%OUT_DIR%"

:: Copy built EXEs to installer staging
echo Copying built applications...
xcopy /s /y "%SRC_ROOT%\dist\auto-dj" "%INSTALLER_DIR%\staging\dist\auto-dj" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\room-control" "%INSTALLER_DIR%\staging\dist\room-control" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\chat-im-private" "%INSTALLER_DIR%\staging\dist\chat-im-private" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\status-random" "%INSTALLER_DIR%\staging\dist\status-random" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\status-marquee" "%INSTALLER_DIR%\staging\dist\status-marquee" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\status-changer" "%INSTALLER_DIR%\staging\dist\status-changer" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\im-autoreply" "%INSTALLER_DIR%\staging\dist\im-autoreply" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\web-status" "%INSTALLER_DIR%\staging\dist\web-status" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\cloudbot-manager" "%INSTALLER_DIR%\staging\dist\cloudbot-manager" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\name-color-changer" "%INSTALLER_DIR%\staging\dist\name-color-changer" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\icon-changer" "%INSTALLER_DIR%\staging\dist\icon-changer" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\name-changer" "%INSTALLER_DIR%\staging\dist\name-changer" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\line" "%INSTALLER_DIR%\staging\dist\line" 2>nul
xcopy /s /y "%SRC_ROOT%\dist\zchat-client" "%INSTALLER_DIR%\staging\dist\zchat-client" 2>nul

:: Copy support files
copy /y "%SRC_ROOT%\app.ico" "%INSTALLER_DIR%\staging\app.ico" 2>nul
copy /y "%SRC_ROOT%\config.example.toml" "%INSTALLER_DIR%\staging\config.example.toml" 2>nul
copy /y "%SRC_ROOT%\README.md" "%INSTALLER_DIR%\staging\README.md" 2>nul
xcopy /s /y "%SRC_ROOT%\tools" "%INSTALLER_DIR%\staging\tools" 2>nul
xcopy /s /y "%SRC_ROOT%\assets" "%INSTALLER_DIR%\staging\assets" 2>nul

:: ------------------------------------------------------------
:: Build NSIS Installer
:: ------------------------------------------------------------
echo.
echo === Building NSIS Installer ===
cd /d "%INSTALLER_DIR%"

"%NSIS_PATH%" /V2 /NOCD /X "SetCompressor /SOLID lzma" ^
    /DVERSION=2.20.0 ^
    /DOUTPUT="%OUT_DIR%\ZChat-2.20.0-Setup.exe" ^
    "ZChatInstaller.nsi"

if errorlevel 1 (
    echo ERROR: NSIS build failed
    exit /b 1
)

:: ------------------------------------------------------------
:: Code Signing (Optional)
:: ------------------------------------------------------------
echo.
echo === Code Signing ===
set "INSTALLER_EXE=%OUT_DIR%\ZChat-2.20.0-Setup.exe"

if exist "%INSTALLER_EXE%" (
    if defined ZCODER_CERT_THUMBPRINT (
        echo Signing with certificate thumbprint: %ZCODER_CERT_THUMBPRINT%
        signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /sha1 "%ZCODER_CERT_THUMBPRINT%" "%INSTALLER_EXE%"
        if errorlevel 1 (
            echo WARNING: Signing failed
        ) else (
            echo Signing successful
        )
    ) else if defined ZCODER_PFX (
        echo Signing with PFX: %ZCODER_PFX%
        signtool sign /f "%ZCODER_PFX%" /p "%ZCODER_PFX_PASSWORD%" /tr http://timestamp.digicert.com /td sha256 /fd sha256 "%INSTALLER_EXE%"
        if errorlevel 1 (
            echo WARNING: Signing failed
        ) else (
            echo Signing successful
        )
    ) else (
        echo Skipping code signing (no certificate configured)
    )
)

:: ------------------------------------------------------------
:: Verify
:: ------------------------------------------------------------
echo.
echo === Verification ===
if exist "%INSTALLER_EXE%" (
    echo Installer created: %INSTALLER_EXE%
    for %%F in ("%INSTALLER_EXE%") do echo Size: %%~zF bytes
    
    :: Verify signature if signed
    signtool verify /pa /v "%INSTALLER_EXE%" 2>nul
    if %errorlevel% equ 0 (
        echo Signature: VALID
    ) else (
        echo Signature: NOT SIGNED or INVALID
    )
) else (
    echo ERROR: Installer not created
    exit /b 1
)

:: ------------------------------------------------------------
:: Cleanup
:: ------------------------------------------------------------
echo.
echo === Cleanup ===
if exist "%INSTALLER_DIR%\staging" rmdir /s /q "%INSTALLER_DIR%\staging"

:: ------------------------------------------------------------
:: Summary
:: ------------------------------------------------------------
echo.
echo ============================================================
echo BUILD SUCCESSFUL
echo ============================================================
echo Installer: %INSTALLER_EXE%
echo Version: 2.20.0
echo.
echo To install silently:
echo   %INSTALLER_EXE% /S /D=C:\Path\To\ZChat
echo.
echo To install with specific components:
echo   %INSTALLER_EXE% /S /COMPONENTS="AutoDJ,RoomControl,CloudBotManager"
echo.
echo To uninstall silently:
echo   "C:\Path\To\ZChat\uninstall.exe" /S
echo ============================================================