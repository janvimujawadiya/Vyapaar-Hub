@echo off
setlocal enabledelayedexpansion

title Vyapar Hub - 1-Click Update to Render

echo ===================================================
echo        Vyapar Hub - One-Click Render Update
echo ===================================================
echo.

:: Ensure Git is available in PATH
set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Git\cmd;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Render.CLI_Microsoft.Winget.Source_8wekyb3d8bbwe"

:: Switch to the current folder of this script
cd /d "%~dp0"

echo [1/3] Staging updated files...
git add .

echo [2/3] Checking and committing changes...
git diff-index --quiet HEAD --
if %ERRORLEVEL% EQU 0 (
    echo [INFO] No new changes to commit. Proceeding with sync...
) else (
    git commit -m "Update website"
)

echo [3/3] Pushing to GitHub (Triggers Render auto-deploy)...
git push origin main
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Git push encountered an issue. Check your internet connection or Git credentials.
    echo.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo  [SUCCESS] Code successfully pushed to GitHub!
echo  Render is now automatically updating your website.
echo.
echo  Live Website: https://vyapaar-hub.onrender.com
echo ===================================================
echo.
pause
