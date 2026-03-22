param(
    [switch]$SkipChroma,
    [switch]$SkipBackend,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"

if (-not (Test-Path $backendDir)) {
    throw "Backend folder not found at: $backendDir"
}

if (-not (Test-Path $frontendDir)) {
    throw "Frontend folder not found at: $frontendDir"
}

function Start-TerminalWindow {
    param(
        [string]$Title,
        [string]$Command
    )

    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoExit",
        "-Command",
        "`$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
    ) | Out-Null
}

function Get-CondaConfig {
    $envName = $env:CONDA_DEFAULT_ENV
    if (-not $envName) {
        $envName = "base"
    }

    $userProfile = [Environment]::GetFolderPath("UserProfile")
    $candidates = @(
        (Join-Path $userProfile "anaconda3\Scripts\conda.exe"),
        (Join-Path $userProfile "miniconda3\Scripts\conda.exe"),
        (Join-Path $userProfile "miniforge3\Scripts\conda.exe")
    )

    $exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $exe) {
        return $null
    }

    return [pscustomobject]@{
        Exe = $exe
        EnvName = $envName
    }
}

Write-Host "Starting Vidhoor stack from: $repoRoot" -ForegroundColor Cyan

if (-not $SkipChroma) {
    Write-Host "[1/3] Starting persistent Chroma via Docker..." -ForegroundColor Yellow
    Push-Location $backendDir
    try {
        docker compose -f docker-compose.chroma.yml up -d
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "[1/3] Skipped Chroma startup" -ForegroundColor DarkYellow
}

if (-not $SkipBackend) {
    Write-Host "[2/3] Starting backend on http://127.0.0.1:8001 ..." -ForegroundColor Yellow

    $condaConfig = Get-CondaConfig

    if ($condaConfig) {
        $backendCommand = @"
Set-Location '$backendDir'
`$env:PYTHONPATH = '$backendDir'
`$env:CHROMA_HOST = '127.0.0.1'
`$env:CHROMA_PORT = '8000'
& '$($condaConfig.Exe)' run -n '$($condaConfig.EnvName)' --no-capture-output python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
"@
    }
    else {
        $backendCommand = @"
Set-Location '$backendDir'
`$env:PYTHONPATH = '$backendDir'
`$env:CHROMA_HOST = '127.0.0.1'
`$env:CHROMA_PORT = '8000'
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
"@
    }

    Start-TerminalWindow -Title "Vidhoor Backend" -Command $backendCommand
}
else {
    Write-Host "[2/3] Skipped backend startup" -ForegroundColor DarkYellow
}

if (-not $SkipFrontend) {
    Write-Host "[3/3] Starting frontend dev server..." -ForegroundColor Yellow

    $frontendCommand = @"
Set-Location '$frontendDir'
npm run dev
"@

    Start-TerminalWindow -Title "Vidhoor Frontend" -Command $frontendCommand
}
else {
    Write-Host "[3/3] Skipped frontend startup" -ForegroundColor DarkYellow
}

Write-Host "Done. Open frontend URL from the frontend terminal output." -ForegroundColor Green
Write-Host "Optional flags: -SkipChroma -SkipBackend -SkipFrontend" -ForegroundColor Gray
