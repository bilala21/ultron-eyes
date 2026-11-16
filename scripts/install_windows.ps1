# Ultron Eyes: Windows setup. Run from the project folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
# Installs uv (Python manager) if missing, installs dependencies, and registers a
# scheduled task that starts Ultron Eyes at logon with below-normal CPU priority.

$ErrorActionPreference = "Stop"
$Project = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Project

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not (Test-Path "$Project\config.yaml")) {
    Copy-Item "$Project\config.example.yaml" "$Project\config.yaml"
    Write-Host "Created config.yaml from the example. Fill it in, then run this script again."
    notepad "$Project\config.yaml"
    exit 1
}

Write-Host "Installing dependencies..."
uv sync --no-dev

Write-Host "Testing cameras once..."
uv run ultron-eyes scan

$uv = (Get-Command uv).Source
# Run hidden (no console window to close by accident); output goes to data\run.log.
New-Item -ItemType Directory -Force "$Project\data" | Out-Null
$cmd = "& '$uv' run ultron-eyes run *> '$Project\data\run.log'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$cmd`"" -WorkingDirectory $Project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -Priority 7
Register-ScheduledTask -TaskName "Ultron Eyes" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Private AI search for home camera events" -Force | Out-Null
Start-ScheduledTask -TaskName "Ultron Eyes"
Write-Host "Ultron Eyes is running and will start automatically at logon."
Write-Host "Search page: http://127.0.0.1:8765 (or this PC's IP if web.host is 0.0.0.0)"
