param()
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCommand) { throw 'Docker is not installed or not on PATH.' }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable. Start Docker Desktop first.' }
    & docker compose -f compose.test.yaml config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Test Compose configuration is invalid.' }
    & docker compose -f compose.test.yaml run --rm frontend-test
    if ($LASTEXITCODE -ne 0) { throw 'Frontend test container could not complete. Check the Docker output above for image download, startup, or test errors.' }
    & docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from test test
    if ($LASTEXITCODE -ne 0) { throw 'Backend/database test containers could not complete. Check the Docker output above for image download, build, startup, or test errors.' }
    Write-Output 'Automated checks passed. Real speech, Docker persistence and model performance still require acceptance testing.'
} finally {
    # Only the isolated test project is stopped; the application project is untouched.
    if ($dockerCommand) { & docker compose -f compose.test.yaml down }
    Pop-Location
}
