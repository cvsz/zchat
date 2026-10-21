@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem --- Capture the entire build to a local log outside the checkout ---
if defined ZCHAT_FULL_BUILD_LOG_CHILD goto :build_main
if exist ".venv\Scripts\python.exe" goto :use_project_python
where py >nul 2>nul
if errorlevel 1 goto :missing_python
py -3 "tools\full_build_logger.py" "%~f0" %*
exit /b %errorlevel%

:use_project_python
".venv\Scripts\python.exe" "tools\full_build_logger.py" "%~f0" %*
exit /b %errorlevel%

:missing_python
echo ERROR: Python is required for full-build logging.
exit /b 1

:build_main

echo ============================================================
echo FULL BUILD: Camfrog feature apps + line-status-changer + zchat-client
echo ============================================================
echo.
echo Usage: full-build.bat [--release]
echo   --release  publish the GitHub release after a green build
echo              (version from APP_VERSION, needs: gh auth login).
echo Without --release, no GitHub release is created.
echo.

rem --- Capture the source used for this build; release.py checks this marker ---
set "BUILD_SOURCE_COMMIT=unknown"
set "BUILD_SOURCE_CLEAN=false"
set "BUILD_STATUS_FILE=%TEMP%\camfrog-build-source-%RANDOM%-%RANDOM%.tmp"
for /f "delims=" %%i in ('git rev-parse HEAD 2^>nul') do set "BUILD_SOURCE_COMMIT=%%i"
git status --porcelain --untracked-files=normal > "%BUILD_STATUS_FILE%" 2>nul
if errorlevel 1 goto :build_source_status_done
for %%A in ("%BUILD_STATUS_FILE%") do if %%~zA EQU 0 set "BUILD_SOURCE_CLEAN=true"
:build_source_status_done
if exist "%BUILD_STATUS_FILE%" del "%BUILD_STATUS_FILE%" >nul 2>nul

rem --- Build Camfrog feature apps ---
echo [1/4] Building Camfrog feature apps...
call build.bat
if errorlevel 1 (
    echo.
    echo BUILD FAILED: Camfrog feature apps
    exit /b 1
)
echo.

rem --- Build line-status-changer ---
echo [2/4] Building line-status-changer...
cd line
call build.bat
if errorlevel 1 (
    echo.
    echo BUILD FAILED: line-status-changer
    cd ..
    exit /b 1
)
cd ..
echo.

rem --- Build the separate ZChat agent chat client ---
echo [3/4] Building zchat-client...
call zchat-client\build.bat
if errorlevel 1 (
    echo.
    echo BUILD FAILED: zchat-client
    exit /b 1
)
echo.

rem --- Verify all build outputs ---
echo [4/4] Verifying outputs...
if not exist dist\room-control\room-control.exe (
    echo ERROR: dist\room-control\room-control.exe not found
    exit /b 1
)
if not exist dist\chat-im-private\chat-im-private.exe (
    echo ERROR: dist\chat-im-private\chat-im-private.exe not found
    exit /b 1
)
if not exist dist\status-random\status-random.exe (
    echo ERROR: dist\status-random\status-random.exe not found
    exit /b 1
)
if not exist dist\status-marquee\status-marquee.exe (
    echo ERROR: dist\status-marquee\status-marquee.exe not found
    exit /b 1
)
if not exist dist\im-autoreply\im-autoreply.exe (
    echo ERROR: dist\im-autoreply\im-autoreply.exe not found
    exit /b 1
)
if not exist dist\web-status\web-status.exe (
    echo ERROR: dist\web-status\web-status.exe not found
    exit /b 1
)
if not exist dist\name-color-changer\name-color-changer.exe (
    echo ERROR: dist\name-color-changer\name-color-changer.exe not found
    exit /b 1
)
if not exist dist\icon-changer\icon-changer.exe (
    echo ERROR: dist\icon-changer\icon-changer.exe not found
    exit /b 1
)
if not exist dist\name-changer\name-changer.exe (
    echo ERROR: dist\name-changer\name-changer.exe not found
    exit /b 1
)
if not exist dist\music-dj\music-dj.exe (
    echo ERROR: dist\music-dj\music-dj.exe not found
    exit /b 1
)
if not exist dist\status-changer\status-changer.exe (
    echo ERROR: dist\status-changer\status-changer.exe not found
    exit /b 1
)
if not exist line\dist\line-status-changer.exe (
    echo ERROR: line\dist\line-status-changer.exe not found
    exit /b 1
)
if not exist line\dist\SHA256SUMS.txt (
    echo ERROR: line\dist\SHA256SUMS.txt not found
    exit /b 1
)
if not exist line\dist\line_config.json (
    echo ERROR: line\dist\line_config.json not found
    exit /b 1
)
if not exist dist\zchat-client\zchat-client.exe (
    echo ERROR: dist\zchat-client\zchat-client.exe not found
    exit /b 1
)
if not exist dist\zchat-client\SHA256SUMS.txt (
    echo ERROR: dist\zchat-client\SHA256SUMS.txt not found
    exit /b 1
)
if not exist camfrog-features-windows.zip (
    echo ERROR: camfrog-features-windows.zip not found
    exit /b 1
)
if not exist dist\SHA256SUMS.txt (
    echo ERROR: dist\SHA256SUMS.txt not found
    exit /b 1
)
(
    echo commit=%BUILD_SOURCE_COMMIT%
    echo clean=%BUILD_SOURCE_CLEAN%
) > dist\BUILD_SOURCE.txt
if errorlevel 1 (
    echo ERROR: could not write dist\BUILD_SOURCE.txt
    exit /b 1
)
echo All expected outputs found.
echo.

rem --- Publish the GitHub release (opt-in only) ---
if /i "%~1"=="--release" (
    echo [4/4] Publishing GitHub release...
    if not exist ".venv\Scripts\python.exe" (
        echo ERROR: .venv\Scripts\python.exe not found, cannot publish release
        exit /b 1
    )
    ".venv\Scripts\python.exe" tools\release.py
    if errorlevel 1 (
        echo.
        echo RELEASE FAILED: GitHub release was not published
        exit /b 1
    )
    echo.
) else (
    echo Skipping GitHub release ^(pass --release to publish^).
    echo.
)

echo.
echo ============================================================
echo FULL BUILD COMPLETE
echo ============================================================
echo.
echo Outputs:
echo   dist\room-control\room-control.exe
echo   dist\chat-im-private\chat-im-private.exe
echo   dist\status-random\status-random.exe
echo   dist\status-marquee\status-marquee.exe
echo   dist\im-autoreply\im-autoreply.exe
echo   dist\web-status\web-status.exe
echo   dist\name-color-changer\name-color-changer.exe
echo   dist\icon-changer\icon-changer.exe
echo   dist\name-changer\name-changer.exe
echo   dist\music-dj\music-dj.exe
echo   dist\status-changer\status-changer.exe
echo   dist\zchat-client\zchat-client.exe
echo   dist\zchat-client\SHA256SUMS.txt
echo   camfrog-features-windows.zip
echo   dist\SHA256SUMS.txt
echo   dist\BUILD_SOURCE.txt
echo   line\dist\line-status-changer.exe
echo   line\dist\SHA256SUMS.txt
echo   line\dist\line_config.json
echo.
echo Build only: no Git staging, commit, or push is performed.
echo Without --release, no GitHub release is created either.
echo.
