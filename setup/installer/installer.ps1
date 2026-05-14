# ==============================================================================
# DOCdemo_update_project - インストーラー本体
# ------------------------------------------------------------------------------
# このファイルは「インストール.bat」から自動的に起動されます。
# 直接実行する場合: powershell -ExecutionPolicy Bypass -File installer.ps1
# ==============================================================================

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

# 設定（必要に応じて変更可能）
$RepoUrl       = "https://github.com/shofukui-neo/DOCdemo_update_project.git"
$DefaultDir    = Join-Path $env:USERPROFILE "Desktop\DOCdemo_update_project"
$LogPath       = Join-Path $env:TEMP "DOCdemo_install_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# ----- ヘルパ関数 -----
function Write-Log {
    param([string]$Message, [string]$Color = "White", [switch]$NoNewline)
    $ts = Get-Date -Format "HH:mm:ss"
    $line = "[$ts] $Message"
    if ($NoNewline) {
        Write-Host $line -ForegroundColor $Color -NoNewline
    } else {
        Write-Host $line -ForegroundColor $Color
    }
    Add-Content -Path $LogPath -Value $line -Encoding utf8
}
function Write-Step($num, $total, $title) {
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host " [$num/$total] $title" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Add-Content -Path $LogPath -Value "`n===== [$num/$total] $title =====" -Encoding utf8
}
function Test-Command($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}
function Refresh-Path {
    $sys  = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$sys;$user"
}
function Install-WithWinget {
    param([string]$Id, [string]$Name)
    Write-Log "  winget で $Name をインストールします..." "Yellow"
    $result = winget install --id $Id -e --source winget `
                --accept-source-agreements --accept-package-agreements `
                --silent 2>&1 | Out-String
    Add-Content -Path $LogPath -Value $result -Encoding utf8
    if ($LASTEXITCODE -eq 0 -or $result -match "already installed") {
        Write-Log "  $Name のインストール完了" "Green"
        return $true
    } else {
        Write-Log "  $Name のインストールに失敗 (exit=$LASTEXITCODE)" "Red"
        return $false
    }
}

# ----- 開始 -----
Clear-Host
Write-Host @"

  ╔═══════════════════════════════════════════════════════╗
  ║                                                       ║
  ║     DOCdemo_update_project 自動インストーラー         ║
  ║                                                       ║
  ╚═══════════════════════════════════════════════════════╝

  このインストーラーは以下を自動で行います:

    1. Git のインストール
    2. Python 3.12 のインストール
    3. VSCode のインストール
    4. プロジェクト本体のダウンロード
    5. Pythonパッケージのインストール
    6. Playwright ブラウザのダウンロード

  所要時間: 10～15分 (ネット環境による)
  ログ保存先: $LogPath

"@ -ForegroundColor Cyan

Read-Host "Enter キーで開始します"

$totalSteps = 6

# ============================================================
# 前提チェック: winget
# ============================================================
if (-not (Test-Command winget)) {
    Write-Log "winget が見つかりません" "Red"
    Write-Log "Microsoft Store で「アプリ インストーラー」を入手してください:" "Yellow"
    Write-Log "  https://www.microsoft.com/store/productId/9NBLGGH4NNS1" "Yellow"
    Read-Host "Enter で終了"
    exit 1
}

# ============================================================
# 1. Git
# ============================================================
Write-Step 1 $totalSteps "Git"

if (Test-Command git) {
    $v = (git --version) -replace "git version ", ""
    Write-Log "  Git は既にインストール済み (v$v)" "Green"
} else {
    Install-WithWinget -Id "Git.Git" -Name "Git" | Out-Null
    Refresh-Path
}

# ============================================================
# 2. Python
# ============================================================
Write-Step 2 $totalSteps "Python 3.12"

if (Test-Command python) {
    $v = (python --version) -replace "Python ", ""
    Write-Log "  Python は既にインストール済み (v$v)" "Green"
} else {
    Install-WithWinget -Id "Python.Python.3.12" -Name "Python 3.12" | Out-Null
    Refresh-Path
    if (-not (Test-Command python)) {
        Write-Log "  Python がまだ PATH に通っていません" "Yellow"
        Write-Log "  PCを再起動してから、もう一度このインストーラーを実行してください" "Yellow"
        Read-Host "Enter で終了"
        exit 1
    }
}

# ============================================================
# 3. VSCode
# ============================================================
Write-Step 3 $totalSteps "Visual Studio Code"

if (Test-Command code) {
    Write-Log "  VSCode は既にインストール済み" "Green"
} else {
    Install-WithWinget -Id "Microsoft.VisualStudioCode" -Name "VSCode" | Out-Null
    Refresh-Path
}

# ============================================================
# 4. プロジェクト本体の取得
# ============================================================
Write-Step 4 $totalSteps "プロジェクト本体のダウンロード"

Write-Host ""
Write-Host "  プロジェクトをどこに保存しますか?" -ForegroundColor White
Write-Host "  デフォルト: $DefaultDir" -ForegroundColor Gray
$inputDir = Read-Host "  保存先 (Enterでデフォルト)"
if ([string]::IsNullOrWhiteSpace($inputDir)) {
    $ProjectDir = $DefaultDir
} else {
    $ProjectDir = $inputDir
}
Write-Log "  プロジェクト保存先: $ProjectDir" "Cyan"

if (Test-Path (Join-Path $ProjectDir "requirements.txt")) {
    Write-Log "  既にプロジェクトが存在するため、ダウンロードをスキップします" "Green"
} else {
    if (Test-Path $ProjectDir) {
        Write-Log "  フォルダは存在しますが requirements.txt がないため上書きします" "Yellow"
    }
    $parent = Split-Path -Parent $ProjectDir
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Write-Log "  GitHub からクローン中: $RepoUrl" "Yellow"
    git clone $RepoUrl $ProjectDir 2>&1 | Tee-Object -FilePath $LogPath -Append | Out-Host
    if (-not (Test-Path (Join-Path $ProjectDir "requirements.txt"))) {
        Write-Log "  クローンに失敗しました。ログを確認してください: $LogPath" "Red"
        Read-Host "Enter で終了"
        exit 1
    }
    Write-Log "  ダウンロード完了" "Green"
}

# ============================================================
# 5. 仮想環境 + requirements.txt
# ============================================================
Write-Step 5 $totalSteps "Pythonパッケージのインストール"

$VenvDir    = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir   "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Log "  仮想環境 (.venv) を作成中..." "Yellow"
    python -m venv $VenvDir 2>&1 | Out-Null
}
Write-Log "  pip を最新化..." "Yellow"
& $VenvPython -m pip install --upgrade pip 2>&1 | Out-Null

Write-Log "  requirements.txt をインストール中 (数分かかります)..." "Yellow"
& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt") 2>&1 |
    Tee-Object -FilePath $LogPath -Append | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Log "  パッケージインストールでエラーが発生しました" "Red"
}

# ============================================================
# 6. Playwright ブラウザ
# ============================================================
Write-Step 6 $totalSteps "Playwright ブラウザ"

Write-Log "  Chromium / Firefox / WebKit をダウンロード中 (数分かかります)..." "Yellow"
& $VenvPython -m playwright install 2>&1 | Tee-Object -FilePath $LogPath -Append | Out-Host
if ($LASTEXITCODE -eq 0) {
    Write-Log "  Playwright ブラウザ準備完了" "Green"
} else {
    Write-Log "  Playwright インストールでエラーが発生しました" "Red"
}

# ============================================================
# Git ユーザー設定 (まだなら聞く)
# ============================================================
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Git のユーザー情報を設定" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$gitUser  = (git config --global user.name) 2>$null
$gitEmail = (git config --global user.email) 2>$null

if (-not $gitUser) {
    $inputName = Read-Host "  GitHubのユーザー名を入力 (例: shofukui-neo)"
    if ($inputName) { git config --global user.name $inputName }
}
if (-not $gitEmail) {
    $inputMail = Read-Host "  GitHubに登録したメールアドレスを入力"
    if ($inputMail) { git config --global user.email $inputMail }
}

# ============================================================
# origin URL を共有元に固定 (fork ではなく shofukui-neo を参照)
# ============================================================
Push-Location $ProjectDir
$currentOrigin = git remote get-url origin 2>$null
if ($currentOrigin -ne $RepoUrl) {
    Write-Log "  origin URL を共有元に再設定: $RepoUrl" "Yellow"
    if ($currentOrigin) {
        git remote set-url origin $RepoUrl 2>&1 | Out-Null
    } else {
        git remote add origin $RepoUrl 2>&1 | Out-Null
    }
}
Write-Log "  origin = $(git remote get-url origin)" "Green"
Write-Host ""
Write-Host "  ※ push 時に「Create a fork?」と聞かれても fork は作らないでください。" -ForegroundColor Red
Write-Host "     共有元リポジトリへの書き込み権限がない場合は、管理者に" -ForegroundColor Red
Write-Host "     collaborator として追加してもらう必要があります。" -ForegroundColor Red
Pop-Location

# ============================================================
# 完了サマリ
# ============================================================
Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                                                       ║" -ForegroundColor Green
Write-Host "║          インストールが完了しました!                  ║" -ForegroundColor Green
Write-Host "║                                                       ║" -ForegroundColor Green
Write-Host "╚═══════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  プロジェクト場所: $ProjectDir" -ForegroundColor White
Write-Host "  ログファイル    : $LogPath" -ForegroundColor Gray
Write-Host ""
Write-Host "  次のステップ:" -ForegroundColor Cyan
Write-Host "    1. VSCode を起動" -ForegroundColor White
Write-Host "    2. ファイル → フォルダーを開く → '$ProjectDir' を選択" -ForegroundColor White
Write-Host "    3. 左下の人型アイコンから GitHub にサインイン" -ForegroundColor White
Write-Host ""

# VSCode を起動するか聞く
$openVS = Read-Host "  VSCodeでプロジェクトを今すぐ開きますか? (y/N)"
if ($openVS -eq "y" -or $openVS -eq "Y") {
    Start-Process "code" -ArgumentList "`"$ProjectDir`""
}

Write-Host ""
Read-Host "Enter キーで終了"
