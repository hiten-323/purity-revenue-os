@echo off
REM ============================================================
REM  Purity Beans Founder Revenue OS — boot autostart
REM  Brings up API (:8001), frontend (:3001) and the Cloudflare
REM  tunnel (dashboard.p3online.in) via pm2. Idempotent: safe to
REM  run when already up (startOrReload reloads instead of dupes).
REM  Registered as Scheduled Task "PurityBeans-Autostart" @ logon.
REM ============================================================

set "NODE_DIR=C:\Program Files\nodejs"
set "PM2=C:\Users\hiten\AppData\Roaming\npm\pm2.cmd"
set "PROJ=C:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session"
set "LOG=%PROJ%\autostart.log"

set "PATH=%NODE_DIR%;%PATH%"
cd /d "%PROJ%"

echo [%date% %time%] autostart triggered >> "%LOG%"

REM Free BOTH ports from orphaned processes pm2 does not own, so the services
REM can bind cleanly after an unclean shutdown. Without this the pm2-managed
REM copies crash-loop forever on EADDRINUSE while a stale orphan serves old
REM code (seen at 1604 restarts on :8001 and 165 on :3001).
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3001 " ^| findstr LISTENING') do (
    tasklist /fi "PID eq %%p" | findstr /i "node.exe" >nul && (
        echo [%date% %time%] freeing orphan node on :3001 pid %%p >> "%LOG%"
        taskkill /F /PID %%p >nul 2>&1
    )
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8001 " ^| findstr LISTENING') do (
    tasklist /fi "PID eq %%p" | findstr /i "python.exe" >nul && (
        echo [%date% %time%] freeing orphan python on :8001 pid %%p >> "%LOG%"
        taskkill /F /PID %%p >nul 2>&1
    )
)

REM Self-heal the #1 recurring 502 cause: a missing/broken Next.js production
REM build makes the frontend crash-loop forever. If .next\BUILD_ID is absent,
REM rebuild before starting so pm2 has something to serve.
if not exist "%PROJ%\frontend\.next\BUILD_ID" (
    echo [%date% %time%] .next build missing — running npm run build >> "%LOG%"
    cd /d "%PROJ%\frontend"
    call npm run build >> "%LOG%" 2>&1
    cd /d "%PROJ%"
)

call "%PM2%" startOrReload "%PROJ%\ecosystem.config.js" >> "%LOG%" 2>&1
call "%PM2%" save >> "%LOG%" 2>&1

echo [%date% %time%] autostart complete >> "%LOG%"
