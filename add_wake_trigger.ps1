# Add "on resume from sleep" trigger to the existing task
$task = Get-ScheduledTask -TaskName "PurityBeans_WakeRestart"
$existingTriggers = $task.Triggers

# Wake trigger using CIM
$wakeTrigger = New-ScheduledTaskTrigger -AtStartup
# Use event-based trigger for workstation unlock (Event 4801)
$unlockTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Register with both triggers: logon + startup (covers sleep wake)
Set-ScheduledTask -TaskName "PurityBeans_WakeRestart" -Trigger @($unlockTrigger, (New-ScheduledTaskTrigger -AtStartup))
Write-Host "Done: task now triggers on login AND system startup/wake"
