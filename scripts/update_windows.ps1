# Ultron Eyes: update to the latest version from GitHub, keeping config.yaml and data\.
#   powershell -ExecutionPolicy Bypass -File scripts\update_windows.ps1
$ErrorActionPreference = "Stop"
$Project = (Resolve-Path "$PSScriptRoot\..").Path
# uv's installer puts it here; windows opened before the install don't have it on PATH yet
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv not found. Run scripts\install_windows.ps1 first." }
$tmp = Join-Path $env:TEMP "ultron-eyes-update"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $tmp | Out-Null

Write-Host "Downloading latest version..."
Invoke-WebRequest https://github.com/bilala21/ultron-eyes/archive/refs/heads/main.zip -OutFile "$tmp\main.zip"
Expand-Archive "$tmp\main.zip" -DestinationPath $tmp -Force

Write-Host "Stopping Ultron Eyes..."
Stop-ScheduledTask -TaskName "Ultron Eyes" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*ultron-eyes run*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2

# Copy new code over the old, never touching config.yaml or data\
robocopy "$tmp\ultron-eyes-main" $Project /E /XF config.yaml /XD data .venv /NFL /NDL /NJH /NJS /NP | Out-Null

Set-Location $Project
uv sync --no-dev
Start-ScheduledTask -TaskName "Ultron Eyes"
Start-Sleep 25
Get-Content "$Project\data\run.log" -Tail 5
Write-Host "Updated. Look for 'listening to 10 motion sensors' above."
