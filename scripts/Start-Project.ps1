param()
$ErrorActionPreference = 'Stop'

Push-Location (Join-Path $PSScriptRoot '..')
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker is not installed or not on PATH.'
    }
    & docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Application Compose configuration is invalid.' }
    & docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw 'Application containers could not start. Check the Docker output above.' }
    & docker compose ps
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the application container status.' }
    Write-Output 'Application containers started. Open http://localhost:8080 in your browser.'
} finally {
    Pop-Location
}
