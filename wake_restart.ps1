$pm2 = "C:\Users\hiten\AppData\Roaming\npm\pm2.cmd"
Start-Sleep -Seconds 8
& $pm2 resurrect
Start-Sleep -Seconds 5
& $pm2 restart purity-api --update-env
& $pm2 restart purity-beans
& $pm2 restart purity-tunnel
