# ============================================================
# Git ローカル状態チェック & push スクリプト
# ============================================================
# 用途: ローカルにだけ存在するコミット/ブランチを検出し、
#       リモート (GitHub) へ push して共有可能にする
#
# 使い方:
#   1. このスクリプトをプロジェクトのルートに置く
#   2. PowerShell を開き、プロジェクトルートに cd する
#   3. 以下を実行:
#      powershell -ExecutionPolicy Bypass -File .\check_and_push.ps1
# ============================================================

# 共有元リポジトリ (この URL に origin を固定する。fork は使わない)
$CanonicalRepoUrl = "https://github.com/shofukui-neo/DOCdemo_update_project.git"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Git ローカル状態チェック" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# --- 0a. origin URL を共有元に固定 (fork を作ってしまった場合の復旧) ---
Write-Host "`n[0a] origin URL を確認..." -ForegroundColor Yellow
$currentOrigin = git remote get-url origin 2>$null
if (-not $currentOrigin) {
    Write-Host "  origin が未設定のため追加します: $CanonicalRepoUrl" -ForegroundColor Yellow
    git remote add origin $CanonicalRepoUrl
} elseif ($currentOrigin -ne $CanonicalRepoUrl) {
    Write-Host "  origin が共有元と異なります" -ForegroundColor Red
    Write-Host "    現在 : $currentOrigin" -ForegroundColor Red
    Write-Host "    正しい: $CanonicalRepoUrl" -ForegroundColor Green
    Write-Host "  origin を共有元に書き換えます (fork は参照しません)" -ForegroundColor Yellow
    git remote set-url origin $CanonicalRepoUrl
} else {
    Write-Host "  origin OK ✓ ($currentOrigin)" -ForegroundColor Green
}

# fork など、origin 以外の不要な remote を削除
$otherRemotes = git remote | Where-Object { $_ -ne "origin" }
foreach ($r in $otherRemotes) {
    $rUrl = git remote get-url $r 2>$null
    Write-Host "  不要な remote '$r' ($rUrl) を削除" -ForegroundColor Yellow
    git remote remove $r
}

# --- 0b. リモート最新を取得 ---
Write-Host "`n[0b] リモートから最新情報を取得中..." -ForegroundColor Yellow
git fetch --all --prune

# --- 1. リモート URL 確認 ---
Write-Host "`n[1] リモート設定:" -ForegroundColor Yellow
git remote -v

# --- 2. 現在のブランチと作業状態 ---
Write-Host "`n[2] 現在のブランチと作業状態:" -ForegroundColor Yellow
git status -sb

# --- 3. 未コミットの変更 ---
Write-Host "`n[3] 未コミットの変更 (ステージ済み + 未ステージ):" -ForegroundColor Yellow
$uncommitted = git status --porcelain
if ($uncommitted) {
    Write-Host $uncommitted -ForegroundColor Red
    Write-Host "  → これらはコミット&push しないと共有されません" -ForegroundColor Red
} else {
    Write-Host "  なし ✓" -ForegroundColor Green
}

# --- 4. ローカルにしか無いコミット (全ブランチ) ---
Write-Host "`n[4] ローカルにしか無いコミット (未push):" -ForegroundColor Yellow
$unpushed = git log --branches --not --remotes --oneline
if ($unpushed) {
    Write-Host $unpushed -ForegroundColor Red
    Write-Host "  → これらは push 必要です" -ForegroundColor Red
} else {
    Write-Host "  なし ✓" -ForegroundColor Green
}

# --- 5. ローカルブランチ一覧 (リモート追跡状況付き) ---
Write-Host "`n[5] ローカルブランチ一覧:" -ForegroundColor Yellow
git branch -vv

# --- 6. リモートに無いローカルブランチを検出 ---
Write-Host "`n[6] リモートに存在しないローカルブランチ:" -ForegroundColor Yellow
$localBranches = git for-each-ref --format='%(refname:short)' refs/heads/
$remoteBranches = git for-each-ref --format='%(refname:short)' refs/remotes/origin/ | ForEach-Object { $_ -replace '^origin/', '' }
$onlyLocal = $localBranches | Where-Object { $remoteBranches -notcontains $_ }

if ($onlyLocal) {
    foreach ($b in $onlyLocal) {
        Write-Host "  - $b (ローカルのみ)" -ForegroundColor Red
    }
} else {
    Write-Host "  なし ✓ (全ブランチがリモートに存在)" -ForegroundColor Green
}

# --- 7. push 提案 ---
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host " 推奨アクション" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if ($uncommitted) {
    Write-Host "`n▼ 未コミットの変更がある場合:" -ForegroundColor Yellow
    Write-Host "  git add ." -ForegroundColor White
    Write-Host "  git commit -m `"<コミットメッセージ>`"" -ForegroundColor White
}

if ($unpushed -or $onlyLocal) {
    Write-Host "`n▼ 未push のコミット/ブランチを push:" -ForegroundColor Yellow
    Write-Host "  現在のブランチだけ push する場合:" -ForegroundColor Gray
    Write-Host "    git push" -ForegroundColor White
    Write-Host "  ローカルだけのブランチを初めて push する場合:" -ForegroundColor Gray
    foreach ($b in $onlyLocal) {
        Write-Host "    git push -u origin $b" -ForegroundColor White
    }
    Write-Host "  全ブランチを一括 push する場合 (注意して実行):" -ForegroundColor Gray
    Write-Host "    git push --all origin" -ForegroundColor White
}

if (-not $uncommitted -and -not $unpushed -and -not $onlyLocal) {
    Write-Host "`n✅ ローカルとリモートは完全に同期されています。共有OKです。" -ForegroundColor Green
}

Write-Host "`n----- push でエラー (403 / permission denied) が出る場合 -----" -ForegroundColor Yellow
Write-Host "  あなたのGitHubアカウントが共有元リポジトリの collaborator に" -ForegroundColor White
Write-Host "  追加されていない可能性があります。" -ForegroundColor White
Write-Host "  管理者 (sho.fukui@neo-career.co.jp) に依頼して、次のURLから" -ForegroundColor White
Write-Host "  あなたをcollaboratorに招待してもらってください:" -ForegroundColor White
Write-Host "    https://github.com/shofukui-neo/DOCdemo_update_project/settings/access" -ForegroundColor Cyan
Write-Host "  ※ 「Create a fork?」と聞かれても fork は作らないでください。" -ForegroundColor Red
Write-Host "      fork すると共有元に反映されません。" -ForegroundColor Red

Write-Host "`n============================================================`n" -ForegroundColor Cyan
