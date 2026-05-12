"""
Google Colab 用 DOCdemo 自動化ノートブック生成スクリプト

このスクリプトは `docdemo_automation.ipynb` を生成する。
ノートブック本体を直接編集するより、こちらを編集して再生成する方が
セルの追加・並び替え・本文修正が容易。

実行:
    python colab/_build_notebook.py
"""

import json
from pathlib import Path

OUTPUT = Path(__file__).parent / "docdemo_automation.ipynb"

cells = []


def md(text: str):
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": text,
    })


def code(text: str):
    cells.append({
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text,
    })


# ===== 1. タイトル & 概要 =====
md("""# DOCdemo 自動化フロー — Google Colab 版

**最終更新**: 2026-05-12
**目的**: Brainverse 管理画面への企業登録 → コンテンツ生成 → 納品URL取得を **クラウド (Google Colab) 上で実行** し、チームメンバー全員が同じ環境で再現できるようにする。

---

## チーム向け使い方 (初回のみ)

1. このノートブックを **Colab で開く** (GitHub URL を Colab で開くか、Drive 上で右クリック → アプリで開く → Google Colaboratory)
2. **左サイドバーの 🔑 (Secrets) アイコン** で以下の Secret を **必ず先に登録** し、それぞれ `Notebook access` を ON にする:
   - `DOCDEMO_LOGIN_EMAIL` — Brainverse のログインメール
   - `DOCDEMO_LOGIN_PASSWORD` — 同パスワード
   - `GITHUB_TOKEN` — GitHub の Personal Access Token (`repo` スコープ。private リポジトリ clone 用)
3. メニュー **ランタイム → すべて実行** (`Ctrl+F9`)
4. CSV / 納品URL / ログは Google Drive の `MyDrive/DOCdemo_Colab/` に保存される

> **チーム共有のコツ**: Drive 上の `MyDrive/DOCdemo_Colab` フォルダを、運用するメンバー全員に共有 (編集権限) しておくと、CSV を同じ実体で参照できる。

---

## 何が実行されるか (ローカル版 README と同じ動作)

1. CSV に並べた企業名を 1 社ずつ Brainverse 管理画面に登録
2. 企業 HP を Yahoo! 検索で自動特定 (URL内の企業ID候補が複数あれば「同名企業該当」で人間判断に委ねる)
3. HP 内部リンク + 求人サイトURL (マイナビ等) を収集してコンテンツ生成 (FAQ + 企業情報) に渡す
4. 生成内容を 2 段階保存し、対象企業のデータか自動検証 (Step 4-pre / 4-post)
5. 完成したフロントエンド公開URL (= **納品URL**) を CSV に書き戻し
6. 「企業名 + 納品URL」の 2 列簡易 CSV をクライアント納品用として自動生成
""")

# ===== 2. Drive マウント =====
md("""## 1. Google Drive をマウント

チーム共有用フォルダ `MyDrive/DOCdemo_Colab/` を作成・マウントする。""")

code("""from google.colab import drive
drive.mount('/content/drive')

import os
DRIVE_BASE = '/content/drive/MyDrive/DOCdemo_Colab'
os.makedirs(f'{DRIVE_BASE}/data', exist_ok=True)
os.makedirs(f'{DRIVE_BASE}/logs', exist_ok=True)
os.makedirs(f'{DRIVE_BASE}/screenshots', exist_ok=True)
print(f'✅ Drive ベース: {DRIVE_BASE}')""")

# ===== 3. リポジトリ取得 =====
md("""## 2. リポジトリの取得 (常に最新版を pull)

GitHub の `shofukui-neo/DOCdemo_update_project` をクローン (2 回目以降は `git pull`)。
private リポジトリの場合は Colab Secret `GITHUB_TOKEN` を使用。""")

code("""import os, subprocess

REPO_DIR  = '/content/DOCdemo_update_project'
REPO_HOST = 'github.com'
REPO_PATH = 'shofukui-neo/DOCdemo_update_project.git'

token = None
try:
    from google.colab import userdata
    token = userdata.get('GITHUB_TOKEN')
except Exception as e:
    print(f'⚠️ GITHUB_TOKEN Secret 未取得 ({type(e).__name__}) — public リポジトリ想定で続行')

if token:
    repo_url = f'https://{token}@{REPO_HOST}/{REPO_PATH}'
    print('🔑 GITHUB_TOKEN を使用してアクセス')
else:
    repo_url = f'https://{REPO_HOST}/{REPO_PATH}'

if os.path.isdir(os.path.join(REPO_DIR, '.git')):
    print('既存リポジトリを更新 (git pull)...')
    subprocess.run(['git', '-C', REPO_DIR, 'reset', '--hard', 'HEAD'], check=True)
    subprocess.run(['git', '-C', REPO_DIR, 'pull', '--ff-only'], check=True)
else:
    print('リポジトリを clone...')
    subprocess.run(['git', 'clone', '--depth', '1', repo_url, REPO_DIR], check=True)

print('\\n現在のコミット:')
subprocess.run(['git', '-C', REPO_DIR, 'log', '-1', '--oneline'])""")

# ===== 4. 依存パッケージインストール =====
md("""## 3. 依存パッケージのインストール (初回 ~3 分)

`requirements.txt` + Playwright + Chromium + システム依存ライブラリをセットアップ。""")

code("""!pip install -q -r {REPO_DIR}/requirements.txt nest_asyncio
!playwright install chromium
!playwright install-deps chromium 2>/dev/null || true
print('✅ Playwright + 依存パッケージ準備完了')""".replace("{REPO_DIR}", "/content/DOCdemo_update_project"))

# ===== 5. 認証情報 & パス設定 =====
md("""## 4. 認証情報・環境変数のセット

**Colab Secrets** (左サイドバーの 🔑 アイコン) に以下を登録しておくこと (各項目「ノートブックのアクセス」を必ず ON):

| Secret 名 | 値 |
|---|---|
| `DOCDEMO_LOGIN_EMAIL` | Brainverse のログインメール |
| `DOCDEMO_LOGIN_PASSWORD` | 同パスワード |

Secrets が未登録の場合は、その場で手入力するフォールバックに切り替わります (一時的な実行のみ・コミット禁止)。""")

code("""import os, sys, getpass

def _get_secret(name: str, prompt: str, secret_input: bool = False) -> str:
    \"\"\"Colab Secrets から取得、未登録/未認可なら getpass で手入力にフォールバック。\"\"\"
    try:
        from google.colab import userdata
        value = userdata.get(name)
        if value:
            print(f'  🔑 {name}: Colab Secrets から取得')
            return value
    except Exception as e:
        # SecretNotFoundError / NotebookAccessError / その他
        print(f'  ⚠️ {name}: Colab Secrets 未登録 ({type(e).__name__}) → 手入力にフォールバック')
        print(f'     恒久対応は左サイドバー 🔑 で {name} を登録し「ノートブックのアクセス」を ON にしてください')
    # フォールバック
    if secret_input:
        return getpass.getpass(f'{prompt}: ')
    return input(f'{prompt}: ').strip()

os.environ['DOCDEMO_LOGIN_EMAIL']    = _get_secret('DOCDEMO_LOGIN_EMAIL',    'Brainverse メール')
os.environ['DOCDEMO_LOGIN_PASSWORD'] = _get_secret('DOCDEMO_LOGIN_PASSWORD', 'Brainverse パスワード', secret_input=True)
os.environ['DOCDEMO_HEADLESS']       = 'true'     # Colab はヘッドレス必須
os.environ['DOCDEMO_LOG_LEVEL']      = 'INFO'

REPO_DIR = '/content/DOCdemo_update_project'
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)
os.chdir(REPO_DIR)

print()
print('✅ 認証情報セット完了')
print('   cwd     :', os.getcwd())
print('   email   :', os.environ['DOCDEMO_LOGIN_EMAIL'])
print('   headless:', os.environ['DOCDEMO_HEADLESS'])""")

# ===== 6. CSV 準備 =====
md("""## 5. 企業リストCSVを準備

下の優先順位で CSV を確保する:

| 優先 | 条件 | 動作 |
|---|---|---|
| 1 | Drive 上に既に CSV がある | そのまま使う (途中再開可能) |
| 2 | `COMPANY_NAMES` に企業名が並んでいる | 新規 CSV を Drive に作成 |
| 3 | `SEED_FROM_REPO = True` | リポジトリ同梱の `data/company_list.csv` を Drive にコピーしてシード |
| 4 | `UPLOAD_IF_MISSING = True` | ローカルファイルピッカーを開いてアップロード |
| 5 | いずれも該当しない | エラー (どれかを選んで再実行するよう促す) |""")

code("""from pathlib import Path
import shutil
from spreadsheet_manager import SpreadsheetManager

CSV_PATH = Path(f'{DRIVE_BASE}/data/company_list.csv')

# === 新規作成する場合はここに企業名を並べる ===
COMPANY_NAMES = [
    # "株式会社サンプル",
    # "テスト株式会社",
]

# === 初回フォールバック: リポジトリ同梱の data/company_list.csv をシードとして使う ===
SEED_FROM_REPO = False         # True にすると repo の CSV を Drive にコピー
# === 初回フォールバック: ローカルからアップロード ===
UPLOAD_IF_MISSING = False      # True にすると files.upload() のファイルピッカーが開く

if CSV_PATH.exists():
    print(f'📂 既存のCSVを使用: {CSV_PATH}')
elif COMPANY_NAMES:
    SpreadsheetManager.create_initial_csv(COMPANY_NAMES, csv_path=CSV_PATH)
    print(f'✅ 初期CSV作成: {CSV_PATH} ({len(COMPANY_NAMES)}社)')
elif SEED_FROM_REPO:
    repo_csv = Path(REPO_DIR) / 'data' / 'company_list.csv'
    if not repo_csv.exists():
        raise RuntimeError(f'リポジトリにシードCSVがありません: {repo_csv}')
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(repo_csv, CSV_PATH)
    print(f'✅ リポジトリの CSV をシードとして配置: {CSV_PATH}')
elif UPLOAD_IF_MISSING:
    from google.colab import files
    print('CSV をローカルから選択してください...')
    uploaded = files.upload()
    if not uploaded:
        raise RuntimeError('アップロードがキャンセルされました')
    fname, content = next(iter(uploaded.items()))
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    CSV_PATH.write_bytes(content)
    print(f'✅ アップロードCSV配置: {CSV_PATH} (元ファイル: {fname})')
else:
    raise RuntimeError(
        f'CSV がありません: {CSV_PATH}\\n'
        f'以下のいずれかを行って再実行してください:\\n'
        f'  (A) COMPANY_NAMES に企業名を記入\\n'
        f'  (B) SEED_FROM_REPO = True (リポジトリ同梱のCSVをシードとして使用)\\n'
        f'  (C) UPLOAD_IF_MISSING = True (ローカルファイルをアップロード)\\n'
        f'  (D) Drive 上の {CSV_PATH} に既存CSVを直接配置'
    )

import pandas as pd
df = pd.read_csv(CSV_PATH, encoding='utf-8-sig')
print(f'\\n登録企業数: {len(df)}社')
print('ステータス別:')
for s, c in df['ステータス'].value_counts().items():
    print(f'  {s}: {c}社')
df[['企業名', 'ホームページURL', 'ステータス', '納品URL']].head(20)""")

# ===== 7. オーケストレータ実行 =====
md("""## 6. 自動化フロー実行

1社あたり目安 **3〜4分**。Colab のセッションタイムアウト (連続 12 時間 / アイドル 90 分) を意識し、100社超は分割実行推奨。

- **全件処理**: 下記そのまま実行
- **1社だけテスト**: `TEST_MODE = True` / `TARGET_COMPANY = '株式会社サンプル'` をセット

> 既に「完了」になっている企業は自動的にスキップされる (再実行で続きから処理)。""")

code("""TEST_MODE       = False    # 1社だけテストするなら True
TARGET_COMPANY  = None     # TEST_MODE=True のとき対象企業名 (部分一致)

import nest_asyncio
nest_asyncio.apply()

from orchestrator import Orchestrator, setup_logging
setup_logging()

orchestrator = Orchestrator(
    csv_path=CSV_PATH,
    headless=True,
    test_mode=TEST_MODE,
    target_company=TARGET_COMPANY,
)
await orchestrator.run()""")

# ===== 8. 結果サマリ =====
md("""## 7. 実行結果サマリ & 納品URL一覧""")

code("""import pandas as pd
from IPython.display import HTML, display

df = pd.read_csv(CSV_PATH, encoding='utf-8-sig')

print('=== ステータス別件数 ===')
for s, c in df['ステータス'].value_counts().items():
    print(f'  {s}: {c}社')

print('\\n=== 完了企業の納品URL ===')
completed = df[df['ステータス'] == '完了'][['企業名', '企業ID', '納品URL']].copy()

def _link(u):
    if pd.isna(u) or not u:
        return ''
    return f'<a href="{u}" target="_blank">{u}</a>'

completed['納品URL'] = completed['納品URL'].apply(_link)
display(HTML(
    '<style>table {font-size:14px; border-collapse:collapse;}'
    'th, td {padding:6px 10px; border:1px solid #ccc;}'
    'a {color:#1a73e8; text-decoration:none;}</style>'
    + completed.to_html(escape=False, index=False)
))""")

# ===== 9. 納品用シンプルCSV =====
md("""## 8. クライアント納品用シンプルCSV (企業名 + 納品URL の 2 列)

orchestrator 実行時に自動生成されているが、手動で再生成することも可能。""")

code("""import csv as _csv
from pathlib import Path

src = Path(CSV_PATH)
stem = src.stem
if stem.endswith('_company_list'):
    out_stem = stem[:-len('_company_list')] + '_delivery_urls'
else:
    out_stem = stem + '_delivery_urls'
delivery_csv = src.parent / f'{out_stem}.csv'

with open(src, encoding='utf-8-sig') as f_in, \\
     open(delivery_csv, 'w', encoding='utf-8-sig', newline='') as f_out:
    reader = _csv.DictReader(f_in)
    writer = _csv.writer(f_out)
    writer.writerow(['企業名', '納品URL'])
    for row in reader:
        writer.writerow([row.get('企業名', ''), row.get('納品URL', '')])

print(f'✅ クライアント納品用CSV: {delivery_csv}')""")

# ===== 10. 検証チェックリスト =====
md("""## 9. 検証チェックリスト (verification_checklist.md) を生成

QA チームが手動検証に使うチェックリストを生成 → Drive に保存。""")

code("""import subprocess, shutil
from pathlib import Path

result = subprocess.run(
    ['python', 'generate_checklist.py'],
    cwd=REPO_DIR, capture_output=True, text=True
)
print(result.stdout)
if result.returncode != 0:
    print('STDERR:', result.stderr)

src_md = Path(REPO_DIR) / 'verification_checklist.md'
dst_md = Path(DRIVE_BASE) / 'verification_checklist.md'
if src_md.exists():
    shutil.copy(src_md, dst_md)
    print(f'✅ チェックリストを Drive にコピー: {dst_md}')""")

# ===== 11. ログ・スクショ同期 =====
md("""## 10. ログ・スクリーンショットを Drive に保存

Colab ランタイムは終了時に消えるため、実行履歴・エラー解析用ファイルを Drive に書き戻す。""")

code("""import shutil, time
from datetime import datetime
from pathlib import Path

ts = datetime.now().strftime('%Y%m%d_%H%M%S')

src_log = Path(REPO_DIR) / 'logs' / 'automation.log'
dst_log = Path(DRIVE_BASE) / 'logs' / f'automation_{ts}.log'
if src_log.exists():
    shutil.copy(src_log, dst_log)
    print(f'✅ ログ保存: {dst_log}')

# 直近 24h に作成されたスクリーンショットだけ Drive にコピー (容量節約)
src_ss = Path(REPO_DIR) / 'screenshots'
dst_ss = Path(DRIVE_BASE) / 'screenshots'
dst_ss.mkdir(parents=True, exist_ok=True)
cutoff = time.time() - 86400
new_files = [p for p in src_ss.glob('*') if p.is_file() and p.stat().st_mtime > cutoff]
for p in new_files:
    shutil.copy(p, dst_ss / p.name)
print(f'✅ 直近24hのスクショ {len(new_files)} 件を Drive に保存: {dst_ss}')""")

# ===== 12. 同名企業該当への対応 =====
md("""---
## 11. 「同名企業該当」(URL企業ID不一致) になった企業への対応

検索結果に複数のドメインがあり、URL内の企業IDが一致しない候補が複数検出された場合、ステータスは `同名企業該当` になる。

**対応手順:**

1. Drive 上の `MyDrive/DOCdemo_Colab/data/company_list.csv` を **Google スプレッドシート** で開く (右クリック → アプリで開く → Google スプレッドシート)
2. ステータス列が `同名企業該当` の行を見つける
3. `URL候補` 列を確認 — パイプ `|` 区切りで最大 5 件のURLが入っている

   例:
   ```
   https://akiyama-group.com|https://kei-ichiman.com|https://www.c-c-akiyama.com
   ```

4. 各候補を実際にブラウザで開き、対象企業の公式サイトを判別
5. 正しい URL を `ホームページURL` 列に貼り付け
6. ファイル → ダウンロード → カンマ区切り形式 で保存し、Drive の同じ場所に `company_list.csv` として上書き
   - または Google スプレッドシートで保存 → 「6. 自動化フロー実行」セルを再実行
7. 該当企業のみ続きから処理される (他の `完了` 企業は自動スキップ)

> Google スプレッドシートで開いた CSV を直接編集する場合、ファイル形式が変わらないよう注意 (必ずカンマ区切り CSV で書き出し)。

---""")

# ===== 13. トラブルシューティング =====
md("""## 12. トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `SecretNotFoundError` | Colab Secrets 未登録 / Notebook access が OFF | 左サイドバー 🔑 で Secret を登録し、Notebook access をオン |
| `git clone` で 403 | private リポジトリで `GITHUB_TOKEN` が無い/期限切れ | GitHub で `repo` スコープの PAT を発行し Secret に登録 |
| `playwright install` が途中で失敗 | 一時的ネットワーク or Colab 制限 | セルを再実行 |
| ログイン失敗 | `DOCDEMO_LOGIN_PASSWORD` が古い | 管理担当者から最新値を取得し Secret 更新 |
| `same-name` が大量発生 | 検索ノイズが多い業種 | 「11. 同名企業該当への対応」の手順で手動補正 |
| ランタイムが切れた | 12h / アイドル90分のタイムアウト | 既処理分は CSV に残るので「6. 自動化フロー実行」を再実行 |
| Drive のCSVが更新されない | Drive の同期遅延 | 数十秒待つ or `drive.flush_and_unmount()` 後に再マウント |
| `Browser closed unexpectedly` | Playwright が落ちた | セル再実行 (CSV に途中ステータスが残っているので続きから) |

詳細ログは Drive の `logs/automation_<timestamp>.log` を確認。エラー時のスクリーンショットは `screenshots/` に保存される。

---

**お問い合わせ**: 自動化スクリプトのバグ → リポジトリ管理者 (Slack: #docdemo-auto) / Brainverse 認証 → 管理担当者""")


def serialize_cell(c: dict) -> dict:
    """nbformat 4 では source は文字列または文字列配列。改行を保つため配列に変換。"""
    if isinstance(c["source"], str):
        lines = c["source"].splitlines(keepends=True)
        c["source"] = lines if lines else [""]
    return c


nb = {
    "cells": [serialize_cell(c) for c in cells],
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3"
        },
        "language_info": {
            "name": "python",
            "version": "3.10"
        },
        "colab": {
            "provenance": [],
            "toc_visible": True
        }
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUTPUT.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"✅ 生成: {OUTPUT} ({len(cells)} cells)")
