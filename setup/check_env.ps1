# ==============================================================================
# DOCdemo_update_project - 環境構築 現状調査スクリプト
# ------------------------------------------------------------------------------
# 使い方:
#   PowerShellで以下を実行
#     cd C:\Users\<ユーザー名>\Desktop\DOCdemo_update_project
#     powershell -ExecutionPolicy Bypass -File .\setup\check_env.ps1
#
# 実行内容:
#   - Git / Python / pip / VSCode / Node.js のインストール状況とPATH確認
#   - 仮想環境 (.venv) の存在確認
#   - requirements.txt のPythonパッケージインストール状況確認
#   - Playwright ブラウザの確認
#   - 結果を「setup\env_report.txt」に保存
# ==============================================================================

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ReportPath  = Join-Path $PSScriptRoot "env_report.txt"

# ----- 出力用関数 -----
$Global:Report = @()
function Write-Section($title) {
    $line = "`n========== $title =========="
    Write-Host $line -ForegroundColor Cyan
    $Global:Report += $line
}
function Write-Item($label, $status, $detail) {
    $mark = if ($status -eq "OK") { "[ OK ]" } elseif ($status -eq "NG") { "[MISS]" } else { "[WARN]" }
    $color = if ($status -eq "OK") { "Green" } elseif ($status -eq "NG") { "Red" } else { "Yellow" }
    $line  = "{0} {1,-30} {2}" -f $mark, $label, $detail
    Write-Host $line -ForegroundColor $color
    $Global:Report += $line
}

# ----- ヘルパ -----
function Test-Command($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}
function Get-VersionSafe($cmd, $arg) {
    try {
        $out = & $cmd $arg 2>&1 | Select-Object -First 1
        return ($out | Out-String).Trim()
    } catch { return $null }
}

Write-Host "DOCdemo_update_project 環境調査を開始します..." -ForegroundColor White
Write-Host "プロジェクト: $ProjectRoot"

# ============================================================
# 1. 基本ツール
# ============================================================
Write-Section "基本ツールのインストール状況"

# Git
if (Test-Command git) {
    Write-Item "Git" "OK" (Get-VersionSafe git --version)
} else {
    Write-Item "Git" "NG" "未インストール (winget install --id Git.Git)"
}

# Python (python / py 両方を確認)
$pythonCmd = $null
$pythonPath = $null
foreach ($candidate in @("python", "py")) {
    if (-not (Test-Command $candidate)) { continue }
    try {
        $candidateVer = Get-VersionSafe $candidate --version
        if ($candidateVer) {
            $pythonCmd = $candidate
            $pythonPath = (Get-Command $candidate).Source
            break
        }
    } catch {
        # broken stub or invalid python command; 次の候補を試す
    }
}

if (-not $pythonCmd) {
    Write-Item "Python" "NG" "未インストール (winget install --id Python.Python.3.12)"
} else {
    if ($pythonCmd -eq "python" -and $pythonPath -match "WindowsApps" -and (Test-Command py)) {
        try {
            $candidateVer = Get-VersionSafe py --version
            if ($candidateVer) {
                $pythonCmd = "py"
                $pythonPath = (Get-Command py).Source
            }
        } catch {
            # py の確認に失敗しても python を使い続ける
        }
    }

    $ver = Get-VersionSafe $pythonCmd --version
    Write-Item "Python ($pythonCmd)" "OK" $ver
    Write-Item "  └ 実体パス" "OK" $pythonPath
}

# pip
if ($pythonCmd) {
    $pipOut = & $pythonCmd -m pip --version 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Write-Item "pip" "OK" $pipOut.Trim()
    } else {
        if ($pythonCmd -ne "py" -and (Test-Command py)) {
            $pipOut = & py -m pip --version 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) {
                $pythonCmd = "py"
                Write-Item "pip" "OK" $pipOut.Trim()
            } else {
                Write-Item "pip" "NG" "python -m ensurepip で復旧可能"
            }
        } else {
            Write-Item "pip" "NG" "python -m ensurepip で復旧可能"
        }
    }
}

# VSCode
if (Test-Command code) {
    $codeVer = (& code --version 2>&1 | Select-Object -First 1)
    Write-Item "VSCode (code)" "OK" "v$codeVer"
} else {
    # PATH未登録だがインストール済の可能性
    $vscodePaths = @(
        "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe",
        "$env:ProgramFiles\Microsoft VS Code\Code.exe"
    )
    $found = $vscodePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) {
        Write-Item "VSCode" "WARN" "インストール済だがPATH未登録: $found"
    } else {
        Write-Item "VSCode" "NG" "未インストール (winget install --id Microsoft.VisualStudioCode)"
    }
}

# Node.js (Playwrightの一部機能で利用)
if (Test-Command node) {
    Write-Item "Node.js" "OK" (Get-VersionSafe node --version)
} else {
    Write-Item "Node.js" "WARN" "未インストール (任意。Playwright Python版だけなら不要)"
}

# ============================================================
# 2. 仮想環境 (.venv)
# ============================================================
Write-Section "仮想環境 (.venv) 確認"

$venvPath   = Join-Path $ProjectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (Test-Path $venvPython) {
    $venvVer = & $venvPython --version 2>&1 | Out-String
    Write-Item ".venv" "OK" $venvVer.Trim()
    Write-Item "  └ パス" "OK" $venvPython
} else {
    Write-Item ".venv" "NG" "未作成 (python -m venv .venv で作成)"
}

# ============================================================
# 3. requirements.txt のパッケージ確認
# ============================================================
Write-Section "Pythonパッケージ (requirements.txt)"

$reqFile = Join-Path $ProjectRoot "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Write-Item "requirements.txt" "NG" "ファイルが見つかりません"
} else {
    # 仮想環境があればそちらを優先、なければシステムpython
    $pyForCheck = if (Test-Path $venvPython) { $venvPython } else { $pythonCmd }

    if ($pyForCheck) {
        $installedRaw = & $pyForCheck -m pip list --format=freeze 2>&1
        $installed    = @{}
        foreach ($line in $installedRaw) {
            if ($line -match "^([A-Za-z0-9_\-\.]+)==(.+)$") {
                $installed[$matches[1].ToLower()] = $matches[2]
            }
        }

        $requirements = Get-Content $reqFile | Where-Object { $_ -and $_ -notmatch "^\s*#" }
        foreach ($req in $requirements) {
            $pkgName = ($req -split "[=<>~!]")[0].Trim().ToLower()
            if ($installed.ContainsKey($pkgName)) {
                Write-Item $pkgName "OK" ("v" + $installed[$pkgName])
            } else {
                Write-Item $pkgName "NG" "未インストール"
            }
        }
    } else {
        Write-Item "確認スキップ" "WARN" "Pythonが見つからないためチェック不可"
    }
}

# ============================================================
# 4. Playwright ブラウザ
# ============================================================
Write-Section "Playwright ブラウザ"

$pwCachePath = Join-Path $env:USERPROFILE "AppData\Local\ms-playwright"
if (Test-Path $pwCachePath) {
    $browsers = Get-ChildItem $pwCachePath -Directory -ErrorAction SilentlyContinue
    if ($browsers.Count -gt 0) {
        foreach ($b in $browsers) {
            Write-Item $b.Name "OK" $b.FullName
        }
    } else {
        Write-Item "Playwrightブラウザ" "NG" "ディレクトリは存在するが中身が空 (playwright install を実行)"
    }
} else {
    Write-Item "Playwrightブラウザ" "NG" "未インストール (playwright install)"
}

# ============================================================
# 5. Git ユーザー設定
# ============================================================
Write-Section "Git ユーザー設定"

if (Test-Command git) {
    $gitUserRaw  = & git config --global user.name 2>$null
    $gitEmailRaw = & git config --global user.email 2>$null
    $gitUser  = if ($gitUserRaw)  { $gitUserRaw.ToString().Trim()  } else { "" }
    $gitEmail = if ($gitEmailRaw) { $gitEmailRaw.ToString().Trim() } else { "" }

    if ($gitUser)  { Write-Item "user.name"  "OK" $gitUser  } else { Write-Item "user.name"  "NG" "未設定" }
    if ($gitEmail) { Write-Item "user.email" "OK" $gitEmail } else { Write-Item "user.email" "NG" "未設定" }
}

# ============================================================
# 6. サマリ
# ============================================================
Write-Section "サマリ"

$missing = $Global:Report | Where-Object { $_ -match "^\[MISS\]" }
if ($missing.Count -eq 0) {
    Write-Host "`nすべて揃っています! 環境構築は完了済みです。" -ForegroundColor Green
    $Global:Report += "`nALL OK"
} else {
    Write-Host "`n以下が不足しています:" -ForegroundColor Yellow
    foreach ($m in $missing) {
        Write-Host "  $m" -ForegroundColor Yellow
    }
    Write-Host "`n→ setup\setup_env.bat を実行すれば自動で構築できます。" -ForegroundColor Cyan
}

# ----- レポート保存 -----
$Global:Report | Out-File -FilePath $ReportPath -Encoding utf8
Write-Host "`nレポート保存: $ReportPath" -ForegroundColor Gray
