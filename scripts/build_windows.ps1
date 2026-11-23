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
$ffprobe = Get-Item (Join-Path $ffmpeg.DirectoryName "ffprobe.exe") -ErrorAction SilentlyContinue
if (-not $ffprobe) { throw "ffprobe.exe was not found beside FFmpeg. Install the complete FFmpeg package and run this script again." }
$cloudflaredCommand = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
if (-not $cloudflaredCommand) { throw "cloudflared.exe was not found. Install cloudflared and run this script again." }
$cloudflared = Get-Item $cloudflaredCommand.Source
Write-Host "Bundling FFmpeg from $($ffmpeg.FullName) ($([math]::Round($ffmpeg.Length / 1MB, 1)) MB)"
Write-Host "Bundling Cloudflare Tunnel from $($cloudflared.FullName)"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ChannelForge_Creator --icon "assets\channel-forge-creator.ico" --add-data "assets;assets" --add-data "web;web" --add-data "plugins;plugins" --add-binary "$($ffmpeg.FullName);." --add-binary "$($ffprobe.FullName);." --add-binary "$($cloudflared.FullName);." Public_ChannelForge.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ChannelForge_TV --icon "assets\channel-forge-tv.ico" --add-data "web\hls.min.js;web" --add-data "assets\channel-forge-tv.png;assets" ChannelForge_TV.py

$payload = Join-Path $root "installer\payload"
New-Item -ItemType Directory -Force -Path $payload | Out-Null
Copy-Item -Force (Join-Path $root "dist\ChannelForge_Creator.exe") (Join-Path $payload "ChannelForge_Creator.exe")
Copy-Item -Force (Join-Path $root "dist\ChannelForge_TV.exe") (Join-Path $payload "ChannelForge_TV.exe")
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ChannelForge_Setup --icon "assets\channel-forge-creator.ico" --add-data "installer\payload\ChannelForge_Creator.exe;payload" --add-data "installer\payload\ChannelForge_TV.exe;payload" --add-data "assets\channel-forge-creator-installer.png;assets" --add-data "assets\channel-forge-tv.png;assets" --add-data "assets\channel-forge-creator.ico;assets" --add-data "assets\channel-forge-tv.ico;assets" installer\windows_installer.py

$release = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $release | Out-Null
$suiteSetup = Join-Path $root "dist\ChannelForge_Setup.exe"
Copy-Item -Force $suiteSetup $release
# Product aliases keep both in-app updaters and older public versions working.
Copy-Item -Force $suiteSetup (Join-Path $release "ChannelForge_Creator_Setup.exe")
Copy-Item -Force $suiteSetup (Join-Path $release "ChannelForge_TV_Setup.exe")
Copy-Item -Force $suiteSetup (Join-Path $release "Public_ChannelForge_Setup.exe")
# Keep the portable viewer for users who do not want an installed copy.
Copy-Item -Force (Join-Path $root "dist\ChannelForge_TV.exe") $release
Get-FileHash $suiteSetup -Algorithm SHA256 | Format-List

