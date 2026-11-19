$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
$ffmpegCandidates = @()
if ($env:ChocolateyInstall) {
    $ffmpegCandidates += Get-ChildItem (Join-Path $env:ChocolateyInstall "lib") -Recurse -Filter ffmpeg.exe -File -ErrorAction SilentlyContinue
}
$pathFfmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if ($pathFfmpeg) { $ffmpegCandidates += Get-Item $pathFfmpeg.Source }
$ffmpeg = $ffmpegCandidates | Where-Object Length -gt 10000000 | Sort-Object Length -Descending | Select-Object -First 1
if (-not $ffmpeg) { throw "The real FFmpeg binary was not found (a Chocolatey shim is not sufficient). Install ffmpeg and run this script again." }
Write-Host "Bundling FFmpeg from $($ffmpeg.FullName) ($([math]::Round($ffmpeg.Length / 1MB, 1)) MB)"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Public_ChannelForge --add-data "web;web" --add-data "plugins;plugins" --add-binary "$($ffmpeg.FullName);." Public_ChannelForge.py

$payload = Join-Path $root "installer\payload"
New-Item -ItemType Directory -Force -Path $payload | Out-Null
Copy-Item -Force (Join-Path $root "dist\Public_ChannelForge.exe") (Join-Path $payload "Public_ChannelForge.exe")
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Public_ChannelForge_Setup --add-data "installer\payload;payload" installer\windows_installer.py

$release = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $release | Out-Null
Copy-Item -Force (Join-Path $root "dist\Public_ChannelForge_Setup.exe") $release
Get-FileHash (Join-Path $release "Public_ChannelForge_Setup.exe") -Algorithm SHA256 | Format-List

