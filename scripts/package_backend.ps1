$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
$Dist = Join-Path $Root "dist"
$BackendAssets = Join-Path $Root "backend\assets"
$Launcher = Join-Path $Root "backend_launcher.py"
$BackendRelease = Join-Path $Root "release\backend"
$WorkPath = Join-Path $Root "build\pyinstaller"
$SpecPath = Join-Path $Root "build\pyinstaller"

if (!(Test-Path $PyInstaller)) {
  throw "PyInstaller not found at $PyInstaller"
}
if (!(Test-Path $Dist)) {
  throw "Frontend dist not found. Run npm run build first."
}

& $PyInstaller `
  --noconfirm `
  --clean `
  --name amadeus-backend `
  --distpath $BackendRelease `
  --workpath $WorkPath `
  --specpath $SpecPath `
  --add-data "$Dist;dist" `
  --add-data "$BackendAssets;backend\assets" `
  $Launcher
