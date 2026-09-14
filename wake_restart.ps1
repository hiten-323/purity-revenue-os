# Wake recovery must use the canonical ecosystem, never an arbitrary saved dump.
$pm2 = (Get-Command pm2 -ErrorAction Stop).Source
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = "$root\wake-restart.log"

Start-Sleep -Seconds 8
Set-Location $root

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') wake recovery -> canonical startOrReload" | Out-File -Append -Encoding utf8 $log
& $pm2 startOrReload "$root\ecosystem.config.js" --update-env >> $log 2>&1
if ($LASTEXITCODE -ne 0) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') canonical PM2 start failed" | Out-File -Append -Encoding utf8 $log
    exit $LASTEXITCODE
}
& $pm2 save >> $log 2>&1
exit $LASTEXITCODE
