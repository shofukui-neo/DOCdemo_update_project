@echo off
REM ============================================================================
REM DOCdemo_update_project - 自動環境構築バッチ
REM ----------------------------------------------------------------------------
REM 動作概要:
REM   1. winget で Git / Python / VSCode をインストール（既にあればスキップ）
REM   2. PATH を再読み込み
REM   3. プロジェクト直下に .venv を作成
REM   4. requirements.txt のパッケージを pip install
REM   5. playwright install でブラウザをダウンロード
REM
REM 前提:
REM   - Windows 10 (1809以降) / Windows 11
REM   - winget が利用可能 (Microsoft Store からインストール可能)
REM
REM 使い方:
REM   このファイルを「右クリック → 管理者として実行」
REM ============================================================================

setlocal EnableDelayedExpansion
chcp 65001 >nul

echo.
echo ==================================================
echo  DOCdemo_update_project 自動環境構築
echo ==================================================
echo.

REM プロジェクトルート = このバッチの1つ上の階層
set "PROJECT_ROOT=%~dp0.."
pushd "%PROJECT_ROOT%"
set "PROJECT_ROOT=%CD%"
echo プロジェクトルート: %PROJECT_ROOT%
echo.

REM ----- 管理者権限チェック -----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 管理者権限がありません。winget のインストールが失敗する場合があります。
    echo        右クリック ^→ 「管理者として実行」 で再実行してください。
    echo.
    pause
)

REM ----- winget の存在確認 -----
where winget >nul 2>&1
if %errorlevel% neq 0 (
    echo [エラー] winget が見つかりません。
    echo         Microsoft Store から「アプリ インストーラー」を入手してください。
    pause
    exit /b 1
)

REM ============================================================
REM 1. Git
REM ============================================================
echo.
echo [1/5] Git の確認・インストール
where git >nul 2>&1
if %errorlevel% equ 0 (
    echo   - Git は既にインストール済み
    git --version
) else (
    echo   - Git をインストールします...
    winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
)

REM ============================================================
REM 2. Python
REM ============================================================
echo.
echo [2/5] Python の確認・インストール
where python >nul 2>&1
if %errorlevel% equ 0 (
    echo   - Python は既にインストール済み
    python --version
) else (
    echo   - Python 3.12 をインストールします...
    winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
)

REM ============================================================
REM 3. VSCode
REM ============================================================
echo.
echo [3/5] VSCode の確認・インストール
where code >nul 2>&1
if %errorlevel% equ 0 (
    echo   - VSCode は既にインストール済み
) else (
    echo   - VSCode をインストールします...
    winget install --id Microsoft.VisualStudioCode -e --source winget --accept-source-agreements --accept-package-agreements
)

REM ----- PATH の再読み込み -----
echo.
echo PATH を再読み込みします...
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul ^| findstr /i "PATH"') do set "SYS_PATH=%%B"
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul ^| findstr /i "PATH"') do set "USR_PATH=%%B"
set "PATH=%SYS_PATH%;%USR_PATH%"

REM Python が認識できない場合は再起動が必要
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [IMPORTANT] Python is not recognized in PATH.
    echo        Close this window, restart your PC, and run the batch again.
    pause
    exit /b 1
)

REM ============================================================
REM 4. 仮想環境 + requirements.txt
REM ============================================================
echo.
echo [4/5] 仮想環境 (.venv) を作成し依存パッケージをインストール

if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    echo   - .venv は既に存在します
) else (
    echo   - .venv を作成します...
    python -m venv "%PROJECT_ROOT%\.venv"
    if %errorlevel% neq 0 (
        echo [エラー] .venv の作成に失敗しました
        pause
        exit /b 1
    )
)

set "VENV_PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

echo   - pip を最新化...
"%VENV_PY%" -m pip install --upgrade pip

echo   - requirements.txt をインストール...
"%VENV_PY%" -m pip install -r "%PROJECT_ROOT%\requirements.txt"
if %errorlevel% neq 0 (
    echo [エラー] パッケージインストールに失敗しました
    pause
    exit /b 1
)

REM ============================================================
REM 5. Playwright ブラウザ
REM ============================================================
echo.
echo [5/5] Playwright ブラウザをインストール
"%VENV_PY%" -m playwright install
if %errorlevel% neq 0 (
    echo [警告] Playwright ブラウザのインストールに失敗しました
)

REM ============================================================
REM Git ユーザー設定の確認
REM ============================================================
echo.
echo Git ユーザー設定を確認...
for /f "delims=" %%i in ('git config --global user.name 2^>nul') do set "GIT_USER=%%i"
for /f "delims=" %%i in ('git config --global user.email 2^>nul') do set "GIT_EMAIL=%%i"

if "!GIT_USER!"=="" (
    echo.
    echo Git のユーザー名が未設定です。設定してください:
    set /p "INPUT_USER=  ユーザー名: "
    if not "!INPUT_USER!"=="" git config --global user.name "!INPUT_USER!"
)
if "!GIT_EMAIL!"=="" (
    echo.
    echo Git のメールアドレスが未設定です。設定してください:
    set /p "INPUT_EMAIL=  メールアドレス: "
    if not "!INPUT_EMAIL!"=="" git config --global user.email "!INPUT_EMAIL!"
)

REM ============================================================
REM 完了
REM ============================================================
echo.
echo ==================================================
echo  環境構築が完了しました
echo ==================================================
echo.
echo 確認のため check_env.ps1 を実行することを推奨します:
echo   powershell -ExecutionPolicy Bypass -File "%~dp0check_env.ps1"
echo.
popd
pause
endlocal
