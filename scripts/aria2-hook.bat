@echo off
setlocal

set API_URL=%ARIA2_PLUS_HOOK_URL%
if "%API_URL%"=="" set API_URL=http://127.0.0.1:8080/api/aria2/hook

set HOOK_TOKEN=%ARIA2_PLUS_HOOK_TOKEN%
if "%HOOK_TOKEN%"=="" set HOOK_TOKEN=change-me-hook-token

curl -s -X POST "%API_URL%" ^
  -H "Content-Type: application/json" ^
  -d "{\"gid\":\"%~1\",\"token\":\"%HOOK_TOKEN%\"}" >nul
