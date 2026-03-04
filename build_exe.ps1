Param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

if ($Python -eq "python") {
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $Python = $venvPython
    }
}

Write-Host "Using Python: $Python"

$TempRoot = Join-Path $env:TEMP "swp2tex-pyinstaller"
$WorkPath = Join-Path $TempRoot "work"
$DistPath = Join-Path $TempRoot "dist"
$SpecPath = Join-Path $TempRoot "spec"
$LocalDist = Join-Path $PSScriptRoot "dist"
$SrcPath = Join-Path $PSScriptRoot "src"
$AssetsPath = Join-Path $PSScriptRoot "src\swp2tex\assets"
$CliLauncher = Join-Path $PSScriptRoot "launcher_cli.py"
$GuiLauncher = Join-Path $PSScriptRoot "launcher_gui.py"
$AddDataArg = "$AssetsPath;swp2tex\assets"

New-Item -ItemType Directory -Force -Path $WorkPath, $DistPath, $SpecPath, $LocalDist | Out-Null
Get-ChildItem -Path $SpecPath -Filter *.spec -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

function Invoke-Step {
    param(
        [scriptblock]$Command,
        [string]$ErrorMessage
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

Invoke-Step -Command { & $Python -m pip install pyinstaller } `
    -ErrorMessage "Failed to install pyinstaller."

Invoke-Step -Command { & $Python -m pip install -e ".[gui-dnd]" } `
    -ErrorMessage "Failed to install project dependencies."

Invoke-Step -Command { & $Python -c "import PyInstaller; print('PyInstaller available')" } `
    -ErrorMessage "Python environment is missing PyInstaller after install."

Invoke-Step -Command {
    & $Python -m PyInstaller `
      --name swp2tex-cli `
      --onefile `
      --clean `
      --noconfirm `
      --workpath $WorkPath `
      --distpath $DistPath `
      --specpath $SpecPath `
      --paths $SrcPath `
      --add-data $AddDataArg `
      $CliLauncher
} -ErrorMessage "PyInstaller CLI build failed."

Invoke-Step -Command {
    & $Python -m PyInstaller `
      --name swp2tex `
      --onefile `
      --windowed `
      --clean `
      --noconfirm `
      --workpath $WorkPath `
      --distpath $DistPath `
      --specpath $SpecPath `
      --paths $SrcPath `
      --add-data $AddDataArg `
      $GuiLauncher
} -ErrorMessage "PyInstaller GUI build failed."

Copy-Item -Force (Join-Path $DistPath "swp2tex.exe") (Join-Path $LocalDist "swp2tex.exe")
Copy-Item -Force (Join-Path $DistPath "swp2tex-cli.exe") (Join-Path $LocalDist "swp2tex-cli.exe")

if (-not (Test-Path (Join-Path $LocalDist "swp2tex.exe"))) {
    throw "Build finished without producing dist\swp2tex.exe."
}

if (-not (Test-Path (Join-Path $LocalDist "swp2tex-cli.exe"))) {
    throw "Build finished without producing dist\swp2tex-cli.exe."
}

Write-Host "Built dist\swp2tex.exe (GUI)"
Write-Host "Built dist\swp2tex-cli.exe (CLI)"
