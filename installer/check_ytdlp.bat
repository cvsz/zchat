@echo off
chcp 65001 >nul

:: Check if yt-dlp is available in PATH or next to mpv
:: Returns 0 if found, 1 if not found

echo Checking for yt-dlp...

where yt-dlp >nul 2>nul
if %errorlevel% equ 0 (
    echo yt-dlp found in PATH
    yt-dlp --version
    exit /b 0
)

:: Check next to mpv (common setup)
where mpv >nul 2>nul
if %errorlevel% equ 0 (
    for %%i in (mpv.exe) do (
        if exist "%%~dpi\yt-dlp.exe" (
            echo yt-dlp found next to mpv at %%~dpi\yt-dlp.exe
            "%%~dpi\yt-dlp.exe" --version
            exit /b 0
        )
    )
)

:: Check common installation locations
set YTDLP_PATHS="C:\Program Files\yt-dlp\yt-dlp.exe" "C:\Program Files (x86)\yt-dlp\yt-dlp.exe" "%LOCALAPPDATA%\yt-dlp\yt-dlp.exe" "%USERPROFILE%\scoop\apps\yt-dlp\current\yt-dlp.exe" "%USERPROFILE%\scoop\shims\yt-dlp.exe" "%CHOCOLATEYINSTALL%\bin\yt-dlp.exe"

for %%p in (%YTDLP_PATHS%) do (
    if exist "%%~p" (
        echo yt-dlp found at %%~p
        "%%~p" --version
        exit /b 0
    )
)

echo yt-dlp not found.
echo.
echo Please install yt-dlp from https://github.com/yt-dlp/yt-dlp/releases/latest
echo Recommended: winget install yt-dlp
echo            or: scoop install yt-dlp
echo            or: choco install yt-dlp
echo            or: pip install yt-dlp
exit /b 1