@echo off
setlocal
REM Purity Beans Revenue OS — canonical production boot.
REM IMPORTANT: this script owns only the purity-revenue-os tree.
REM It never taskkills ports or attempts to adopt a legacy PM2 tree.

set "NODE_DIR=C:\Program Files\nodejs"
set "PM2=C:\Users\hiten\AppData\Roaming\npm\pm2.cmd"
set "PROJ=C:\Users\hiten\Desktop\ppp\claude\CODE\purity-revenue-os"
set "LOG=%PROJ%\autostart.log"

set "PATH=%NODE_DIR%;%PATH%"
cd /d "%PROJ%"

echo [%date% %time%] canonical autostart triggered >> "%LOG%"

REM Build the frontend only when the production build is genuinely absent.
if not exist "%PROJ%\frontend\.next\BUILD_ID" (
    echo [%date% %time%] .next build missing - running npm run build >> "%LOG%"
    cd /d "%PROJ%\frontend"
    call npm run build >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] frontend build FAILED - aborting PM2 start >> "%LOG%"
        exit /b 1
    )
    cd /d "%PROJ%"
)

REM Use the canonical ecosystem from the canonical cwd. Do not resurrect an old
REM dump here: an old dump is capable of restoring the legacy jules_session tree.
call "%PM2%" startOrReload "%PROJ%\ecosystem.config.js" --update-env >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] PM2 startOrReload FAILED >> "%LOG%"
    exit /b 1
)
call "%PM2%" save >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] PM2 save FAILED >> "%LOG%"
    exit /b 1
)

echo [%date% %time%] canonical autostart complete >> "%LOG%"
exit /b 0
