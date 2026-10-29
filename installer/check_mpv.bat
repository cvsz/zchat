@echo off
chcp 65001 >nul

:: Check if mpv is available in PATH or installed location
:: Returns 0 if found, 1 if not found

echo Checking for mpv...

where mpv >nul 2>nul
if %errorlevel% equ 0 (
    echo mpv found in PATH
    mpv --version
    exit /b 0
)

:: Check common installation locations
set MPV_PATHS="C:\Program Files\mpv\mpv.exe" "C:\Program Files (x86)\mpv\mpv.exe" "%LOCALAPPDATA%\mpv\mpv.exe" "%USERPROFILE%\scoop\apps\mpv\current\mpv.exe" "%USERPROFILE%\scoop\shims\mpv.exe" "%CHOCOLATEYINSTALL%\bin\mpv.exe"

for %%p in (%MPV_PATHS%) do (
    if exist "%%~p" (
        echo mpv found at %%~p
        "%%~p" --version
        exit /b 0
    )
)

echo mpv not found.
echo.
echo Please install mpv from https://mpv.io/installation/
echo Recommended: winget install mpv
echo            or: scoop install mpv
echo            or: choco install mpv
exit /b 1