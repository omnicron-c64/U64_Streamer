param([string]$Python = "python")

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$venv = ".build-venv-windows"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    & $Python -m venv $venv
    if ($LASTEXITCODE) { throw "Could not create the build environment." }
}
$buildPython = "$venv\Scripts\python.exe"
& $buildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE) { throw "Could not install the build dependencies." }

& $buildPython -m PyInstaller --noconfirm --clean --onedir --windowed --name UCI-Capture `
    --copy-metadata av --copy-metadata pygame --copy-metadata numpy `
    --specpath build --workpath build\windows --distpath dist\windows uci_capture.py
if ($LASTEXITCODE) { throw "PyInstaller failed." }

$bundle = "dist\windows\UCI-Capture"
Copy-Item -LiteralPath README.md,THIRD_PARTY.md,U64_Stream_Capture_Palette.md,LICENSE -Destination $bundle
$test = Start-Process -FilePath "$bundle\UCI-Capture.exe" -ArgumentList "--self-test" -WindowStyle Hidden -Wait -PassThru
if ($test.ExitCode) { throw "The packaged app failed its self-test." }
$architecture = & $buildPython -c "import platform; print(platform.machine().lower())"
$archive = "dist\UCI-Capture-windows-$architecture.zip"
Compress-Archive -LiteralPath $bundle -DestinationPath $archive -Force
Get-FileHash -LiteralPath $archive -Algorithm SHA256
