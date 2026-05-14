@echo off
REM ============================================================================
REM  DOCdemo_update_project ワンクリック・インストーラー
REM  ----------------------------------------------------------------------------
REM  これをダブルクリックするだけで、必要なソフト全てを自動インストールします。
REM  - Git
REM  - Python 3.12
REM  - VSCode
REM  - プロジェクト本体 (GitHubから取得)
REM  - Pythonパッケージ (requirements.txt)
REM  - Playwright ブラウザ
REM ============================================================================

chcp 65001 >nul
title DOCdemo_update_project インストーラー

REM ---- 管理者権限への自動昇格 ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo 管理者権限が必要です。UACダイアログが表示されるので「はい」をクリックしてください。
    echo.
    timeout /t 2 >nul
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

REM ---- 同梱されているPowerShellスクリプトを実行 ----
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%installer.ps1"

if not exist "%PS1%" (
    echo.
    echo [エラー] installer.ps1 が見つかりません。
    echo         このファイルと同じフォルダに installer.ps1 が必要です。
    echo.
    pause
    exit /b 1
)

powershell -ExecutionPolicy Bypass -NoProfile -File "%PS1%"

echo.
echo インストーラーが終了しました。このウィンドウを閉じてください。
pause
