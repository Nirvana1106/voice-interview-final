$ErrorActionPreference = 'Stop'

Push-Location (Join-Path $PSScriptRoot '..')
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker is not installed or not on PATH.'
    }

    Write-Output 'Checking model access from the running backend container. The API key will not be printed.'
    $probe = @'
import json
import sys
from urllib.parse import urlsplit

import httpx

from app.config import settings


print("MODEL_HOST=" + (urlsplit(settings.model_base_url).hostname or "invalid"))
print("MODEL_NAME_SET=" + str(bool(settings.model_name)).lower())
print("MODEL_KEY_SET=" + str(bool(settings.model_api_key)).lower())
if not settings.model_name or not settings.model_api_key:
    sys.exit(2)

payload = {
    "model": settings.model_name,
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": True,
    "temperature": 0.5,
    "max_tokens": 16,
}

try:
    timeout = httpx.Timeout(connect=10, read=20, write=10, pool=10)
    with httpx.Client(timeout=timeout) as client:
        with client.stream(
            "POST",
            settings.model_base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + settings.model_api_key},
            json=payload,
        ) as response:
            print("MODEL_HTTP_STATUS=" + str(response.status_code))
            if response.status_code != 200:
                try:
                    error = json.loads(response.read()).get("error", {})
                    if isinstance(error, dict):
                        for field in ("type", "code"):
                            value = error.get(field)
                            if isinstance(value, str):
                                safe = "".join(c for c in value if c.isascii() and (c.isalnum() or c in "_-"))
                                print("MODEL_ERROR_" + field.upper() + "=" + safe[:80])
                except (ValueError, httpx.HTTPError):
                    pass
                sys.exit(3)

            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("error"):
                    print("MODEL_STREAM=error")
                    sys.exit(4)
                for choice in event.get("choices") or []:
                    if (choice.get("delta") or {}).get("content"):
                        print("MODEL_STREAM=ok")
                        sys.exit(0)
            print("MODEL_STREAM=empty")
            sys.exit(4)
except httpx.HTTPError as exc:
    print("MODEL_NETWORK_ERROR=" + type(exc).__name__)
    sys.exit(5)
'@

    $probe | & docker compose exec -T backend python -
    if ($LASTEXITCODE -ne 0) {
        throw "Model connection check failed (exit code $LASTEXITCODE)."
    }
} finally {
    Pop-Location
}
