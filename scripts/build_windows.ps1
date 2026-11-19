$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --name Public_ChannelForge --add-data "web;web" --add-data "plugins;plugins" Public_ChannelForge.py

$payload = Join-Path $root "installer\payload"
New-Item -ItemType Directory -Force -Path $payload | Out-Null
Copy-Item -Force (Join-Path $root "dist\Public_ChannelForge.exe") (Join-Path $payload "Public_ChannelForge.exe")
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Public_ChannelForge_Setup --add-data "installer\payload;payload" installer\windows_installer.py

$release = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $release | Out-Null
Copy-Item -Force (Join-Path $root "dist\Public_ChannelForge_Setup.exe") $release
Get-FileHash (Join-Path $release "Public_ChannelForge_Setup.exe") -Algorithm SHA256 | Format-List

