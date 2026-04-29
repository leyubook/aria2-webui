@echo off
setlocal

set API_URL=%ARIA2_PLUS_HOOK_URL%
if "%API_URL%"=="" set API_URL=http://127.0.0.1:8080/api/aria2/hook

set HOOK_TOKEN=%ARIA2_PLUS_HOOK_TOKEN%
if "%HOOK_TOKEN%"=="" set HOOK_TOKEN=change-me-hook-token

set "GID=%~1"
if "%GID%"=="" exit /b 1

:: Build JSON safely via PowerShell to avoid injection
powershell -NoProfile -Command ^
  "$body = @{gid='%GID%'; token='%HOOK_TOKEN%'} | ConvertTo-Json -Compress; Invoke-RestMethod -Uri '%API_URL%' -Method Post -ContentType 'application/json' -Body $body | Out-Null"
