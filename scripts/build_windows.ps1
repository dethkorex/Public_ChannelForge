$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Never mix stale executables or checksums into a new public release.
$buildRoot = Join-Path $root "build"
$distRoot = Join-Path $root "dist"
$releaseRoot = Join-Path $root "release"
$payloadRoot = Join-Path $root "installer\payload"
foreach ($target in @($buildRoot, $distRoot, $releaseRoot, $payloadRoot)) {
    $resolvedParent = [IO.Path]::GetFullPath((Split-Path -Parent $target))
    if (-not $resolvedParent.StartsWith([IO.Path]::GetFullPath($root), [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe build cleanup path: $target" }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
}

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
$sshArchive = Join-Path $buildRoot "OpenSSH-Win64.zip"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
Invoke-WebRequest "https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.4.0.0p1-Preview/OpenSSH-Win64.zip" -OutFile $sshArchive
if ((Get-FileHash -LiteralPath $sshArchive -Algorithm SHA256).Hash -ne "B67FF8BECD203502190EC42AC7787C24C2CD3325ED7700485FE7A1C83D2A7957") {
    throw "OpenSSH archive checksum mismatch."
}
Expand-Archive -LiteralPath $sshArchive -DestinationPath $buildRoot -Force
$sshSource = Join-Path $buildRoot "OpenSSH-Win64"
$sshArguments = @(
    "--add-binary", "$sshSource\ssh.exe;openssh",
    "--add-binary", "$sshSource\ssh-keygen.exe;openssh",
    "--add-binary", "$sshSource\libcrypto.dll;openssh",
    "--add-data", "$sshSource\LICENSE.txt;openssh",
    "--add-data", "$sshSource\NOTICE.txt;openssh"
)
Write-Host "Bundling FFmpeg from $($ffmpeg.FullName) ($([math]::Round($ffmpeg.Length / 1MB, 1)) MB)"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ChannelForge_Creator --icon "assets\channel-forge-creator.ico" --add-data "assets;assets" --add-data "web;web" --add-data "plugins;plugins" --add-binary "$($ffmpeg.FullName);." --add-binary "$($ffprobe.FullName);." @sshArguments Public_ChannelForge.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ChannelForge_TV --icon "assets\channel-forge-tv.ico" --add-data "web\hls.min.js;web" --add-data "web\cinema.css;web" --add-data "web\playback-diagnostics.js;web" --add-data "web\desktop-playback.js;web" --add-data "assets\channel-forge-tv.png;assets" --add-data "assets\cinema-hero.webp;assets" --add-data "assets\cinema-tapes.webp;assets" --add-data "assets\cinema-cassette-case.png;assets" --add-data "assets\cinema-vhs-spines.png;assets" ChannelForge_TV.py

$payload = Join-Path $root "installer\payload"
New-Item -ItemType Directory -Force -Path $payload | Out-Null
Copy-Item -Force (Join-Path $root "dist\ChannelForge_Creator.exe") (Join-Path $payload "ChannelForge_Creator.exe")
Copy-Item -Force (Join-Path $root "dist\ChannelForge_TV.exe") (Join-Path $payload "ChannelForge_TV.exe")
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ChannelForge_Setup --paths "$root" --icon "assets\channel-forge-creator.ico" --add-data "installer\payload\ChannelForge_Creator.exe;payload" --add-data "installer\payload\ChannelForge_TV.exe;payload" --add-data "assets\channel-forge-creator-installer.png;assets" --add-data "assets\channel-forge-tv.png;assets" --add-data "assets\channel-forge-creator.ico;assets" --add-data "assets\channel-forge-tv.ico;assets" installer\windows_installer.py

$release = $releaseRoot
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

