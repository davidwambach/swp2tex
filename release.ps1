Param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[vV]?\d+\.\d+\.\d+([\-+][0-9A-Za-z\.-]+)?$')]
    [string]$Version,
    [switch]$RunTests,
    [switch]$BuildExe,
    [switch]$Commit,
    [string]$CommitMessage,
    [switch]$VersionFilesOnly,
    [switch]$Tag,
    [switch]$Push,
    [switch]$All
)

$ErrorActionPreference = "Stop"

if ($All) {
    $RunTests = $true
    $BuildExe = $true
    $Commit = $true
    $Tag = $true
    $Push = $true
}

$normalizedVersion = $Version
if ($normalizedVersion.StartsWith("v") -or $normalizedVersion.StartsWith("V")) {
    $normalizedVersion = $normalizedVersion.Substring(1)
}
$tagName = "v$normalizedVersion"

$repoRoot = $PSScriptRoot
$pyprojectPath = Join-Path $repoRoot "pyproject.toml"
$initPath = Join-Path $repoRoot "src\swp2tex\__init__.py"

if (-not (Test-Path $pyprojectPath)) {
    throw "Missing pyproject.toml at $pyprojectPath"
}
if (-not (Test-Path $initPath)) {
    throw "Missing __init__.py at $initPath"
}

function Update-RegexValue {
    param(
        [string]$Path,
        [string]$Pattern,
        [scriptblock]$Replace
    )
    $content = Get-Content -Raw -Path $Path
    if (-not [System.Text.RegularExpressions.Regex]::IsMatch($content, $Pattern)) {
        throw "No match for pattern in ${Path}: $Pattern"
    }
    $updated = [System.Text.RegularExpressions.Regex]::Replace($content, $Pattern, $Replace)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $updated, $utf8NoBom)
}

Update-RegexValue `
    -Path $pyprojectPath `
    -Pattern '(?m)^(\s*version\s*=\s*")[^"]+(")\s*$' `
    -Replace { param($m) "$($m.Groups[1].Value)$normalizedVersion$($m.Groups[2].Value)" }

Update-RegexValue `
    -Path $initPath `
    -Pattern '(?m)^(\s*__version__\s*=\s*")[^"]+(")\s*$' `
    -Replace { param($m) "$($m.Groups[1].Value)$normalizedVersion$($m.Groups[2].Value)" }

Write-Host "Updated version to $normalizedVersion in:"
Write-Host " - pyproject.toml"
Write-Host " - src/swp2tex/__init__.py"

if ($RunTests) {
    Write-Host "Running tests..."
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed."
    }
}

if ($BuildExe) {
    Write-Host "Building Windows executables..."
    & (Join-Path $repoRoot "build_exe.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "EXE build failed."
    }
}

if ($Commit) {
    $resolvedCommitMessage = $CommitMessage
    if ([string]::IsNullOrWhiteSpace($resolvedCommitMessage)) {
        $resolvedCommitMessage = "chore(release): $tagName"
    } else {
        $resolvedCommitMessage = $resolvedCommitMessage.Replace("{version}", $normalizedVersion)
        $resolvedCommitMessage = $resolvedCommitMessage.Replace("{tag}", $tagName)
    }
    Write-Host "Creating release commit..."
    if ($VersionFilesOnly) {
        git add pyproject.toml src/swp2tex/__init__.py
    } else {
        git add -A
    }
    git commit -m $resolvedCommitMessage
    if ($LASTEXITCODE -ne 0) {
        throw "Commit failed."
    }
}

if ($Tag) {
    Write-Host "Creating tag $tagName..."
    git tag -a $tagName -m $tagName
    if ($LASTEXITCODE -ne 0) {
        throw "Tag creation failed."
    }
}

if ($Push) {
    Write-Host "Pushing main branch..."
    git push origin main
    if ($LASTEXITCODE -ne 0) {
        throw "Push main failed."
    }
    if ($Tag) {
        Write-Host "Pushing tag $tagName..."
        git push origin $tagName
        if ($LASTEXITCODE -ne 0) {
            throw "Push tag failed."
        }
    }
}

Write-Host "Done. Version is set to $normalizedVersion."
