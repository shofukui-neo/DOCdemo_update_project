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

**最終更新**: 2026-05-13
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

## 何が実行されるか (6ステップ自動化フロー)

1. **Step 1: ホームページURL検索** — Yahoo! 検索で公式サイトを特定
   - **Step 1.5 URL企業ID不一致検証**: 候補URLから抽出した「企業ID」(URL内のドメインスラッグ) が
     完全一致しない種類が複数あれば自動判定不能 → 「同名企業該当」で人間判断に委ねる
2. **Step 2: 企業追加** — Brainverse 管理画面で「企業の追加」を実行
   - **Step 2.5 ID検証**: 企業IDがURLベースで正しく登録されているか確認
3. **Step 3: HP画像取得** — OGP画像/HPスクショで背景画像候補を取得 + 求人サイトURL収集
4. **Step 4: コンテンツ生成** — FAQ + 企業情報を AI 生成、2段階保存
   - **Step 4-pre/post**: ヘッダー検証 + FAQ・企業情報タブの内容確認
5. **Step 5: 背景画像アップロード** — システム設定ページで画像を登録
   - **UI反映確認**: アップロード後、画面に新しい画像要素が出現するまでポーリング (誤投入防止)
6. **Step 6: 納品URL取得** — 候補者面談ページから「フロントエンドアプリを開く」URLを取得
   - リトライ 60秒、最終フォールバックで URL構造から推定

最後に「企業名 + 納品URL」の 2 列の納品用CSVを自動生成。
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
from google.colab import userdata

REPO_DIR  = '/content/DOCdemo_update_project'
REPO_HOST = 'github.com'
REPO_PATH = 'shofukui-neo/DOCdemo_update_project.git'

try:
    token = userdata.get('GITHUB_TOKEN')
    repo_url = f'https://{token}@{REPO_HOST}/{REPO_PATH}'
    print('🔑 GITHUB_TOKEN を使用してアクセス')
except Exception:
    repo_url = f'https://{REPO_HOST}/{REPO_PATH}'
    print('⚠️ GITHUB_TOKEN Secret が未設定 — public リポジトリ想定で続行')

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

Colab Secrets から `DOCDEMO_LOGIN_EMAIL` / `DOCDEMO_LOGIN_PASSWORD` を取得し、
プロジェクトを Python パスに追加する。""")

code("""import os, sys
from google.colab import userdata

os.environ['DOCDEMO_LOGIN_EMAIL']    = userdata.get('DOCDEMO_LOGIN_EMAIL')
os.environ['DOCDEMO_LOGIN_PASSWORD'] = userdata.get('DOCDEMO_LOGIN_PASSWORD')
os.environ['DOCDEMO_HEADLESS']       = 'true'     # Colab はヘッドレス必須
os.environ['DOCDEMO_LOG_LEVEL']      = 'INFO'

REPO_DIR = '/content/DOCdemo_update_project'
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)
os.chdir(REPO_DIR)

print('✅ 認証情報セット完了')
print('   cwd     :', os.getcwd())
print('   email   :', os.environ['DOCDEMO_LOGIN_EMAIL'])
print('   headless:', os.environ['DOCDEMO_HEADLESS'])""")

# ===== 6. CSV 準備 =====
md("## 5. 企業リストCSVを準備\n\n"
   "**ケースA: 新規作成** — 下のセルの `COMPANY_NAMES` に企業名を並べて実行 → Drive に初期CSVが生成される\n"
   "**ケースB: 既存CSVを使う** — `MyDrive/DOCdemo_Colab/data/company_list.csv` に CSV が既にあれば、それを引き続き使う (途中再開可能)\n\n"
   "> **入力形式 (ケースA)**: `COMPANY_NAMES` は **文字列のリスト**。1要素=1社が原則。\n"
   "> 1要素に改行が含まれていても自動で分割するので、Excel/メモ帳から複数行を3連クォートで括ったヒアドキュメント形式でまとめて貼っても OK:\n"
   ">\n"
   "> ```python\n"
   "> COMPANY_NAMES = ['''\n"
   "> 株式会社A\n"
   "> 株式会社B\n"
   "> 株式会社C\n"
   "> ''']\n"
   "> ```\n"
   ">\n"
   "> または1行1要素でも同じ結果になります:\n"
   "> ```python\n"
   "> COMPANY_NAMES = ['株式会社A', '株式会社B', '株式会社C']\n"
   "> ```")

code("""from pathlib import Path
from spreadsheet_manager import SpreadsheetManager, flatten_company_names
from config import CSV_COLUMNS, LEGACY_COLUMN_ALIASES

CSV_PATH = Path(f'{DRIVE_BASE}/data/company_list.csv')

# === 新規作成する場合はここに企業名を並べる ===
# 1要素=1社が原則。改行混じりで貼り付けても自動分割される。
COMPANY_NAMES = [
    # "株式会社サンプル",
    # "テスト株式会社",
]

if not CSV_PATH.exists():
    # 改行で複数行貼り付けされていた場合に個別企業へ正規化
    normalized = flatten_company_names(COMPANY_NAMES)
    if not normalized:
        raise RuntimeError(
            f'CSV がありません: {CSV_PATH}\\n'
            f'COMPANY_NAMES に企業名を記入して再実行するか、'
            f'Drive 上の {CSV_PATH} に既存CSVを配置してください。'
        )
    SpreadsheetManager.create_initial_csv(normalized, csv_path=CSV_PATH)
    print(f'✅ 初期CSV作成: {CSV_PATH} ({len(normalized)}社)')
    if len(normalized) != len(COMPANY_NAMES):
        print(f'   (入力 {len(COMPANY_NAMES)}要素 → 正規化後 {len(normalized)}社に展開)')
else:
    print(f'📂 既存のCSVを使用: {CSV_PATH}')

import pandas as pd
df = pd.read_csv(CSV_PATH, encoding='utf-8-sig')
print(f'\\n登録企業数: {len(df)}社')
print(f'カラム: {list(df.columns)}')

# 古い形式のCSV (「ステータス」列がない等) でも落ちないようガード
status_col_name = CSV_COLUMNS['status']   # 期待値: "ステータス"
if status_col_name not in df.columns:
    # LEGACY エイリアスをチェック (現状はstatusに別名なしだが将来対応のため)
    legacy_alts = LEGACY_COLUMN_ALIASES.get('status', [])
    found = next((a for a in legacy_alts if a in df.columns), None)
    if found:
        print(f'⚠️ 旧カラム名 "{found}" を検出 → "{status_col_name}" として読み込みます')
        df = df.rename(columns={found: status_col_name})
    else:
        print(f'⚠️ "{status_col_name}" 列が見つかりません。CSV を一度 read_company_list 経由で'
              ' 再保存して最新フォーマットに揃えることを推奨します。')
        print('   → 次セル「6. 自動化フロー実行」を実行すれば、自動的に最新形式へ書き戻されます。')

if status_col_name in df.columns:
    print('\\nステータス別:')
    for s, c in df[status_col_name].value_counts().items():
        print(f'  {s}: {c}社')

# 表示用カラム — 存在する列のみ
display_cols = [c for c in [
    CSV_COLUMNS['company_name'],
    CSV_COLUMNS['homepage_url'],
    CSV_COLUMNS['status'],
    CSV_COLUMNS['frontend_url'],
] if c in df.columns]
df[display_cols].head(20) if display_cols else df.head(20)""")

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

# ===== 12. 同名企業該当 (URL企業ID不一致) の対応 =====
md("""---
## 11. 「同名企業該当」(URL企業ID不一致) になった企業の対応

検索結果のURL候補から抽出した企業IDが完全一致しないものが複数あった場合、
自動採用できないため `同名企業該当` ステータスで一時停止する。
**下の ipywidgets UI** でボタンを押すだけで候補から正しいURLを採用できる。

> Step 5 の背景画像 UI反映確認、Step 6 の納品URL取得もこの新ロジックが前提。
> サイドバー「⚙️ システム設定」が折りたたみ済みでも自動で展開する対応済み。""")

code("""# 同名企業該当の社をボタンで対話的に解消する Colab UI
# tkinter は Colab で動かないため ipywidgets を使用
import csv as _csv
import ipywidgets as widgets
from IPython.display import display, clear_output
from pathlib import Path
from config import CSV_COLUMNS
from models import ProcessStatus

_csv_path = Path(CSV_PATH)
_col = CSV_COLUMNS

def _load_rows():
    with open(_csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        return list(_csv.DictReader(f))

def _save_rows(rows):
    fieldnames = list(rows[0].keys()) if rows else list(_col.values())
    with open(_csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

_all_rows = _load_rows()
_hold_indices = [
    i for i, r in enumerate(_all_rows)
    if r[_col['status']] == ProcessStatus.DUPLICATE_DETECTED.value
    and not r[_col['homepage_url']].strip()
]
_state = {'pos': 0, 'resolved': 0, 'skipped': 0}
_out = widgets.Output()

def _render():
    with _out:
        clear_output()
        if _state['pos'] >= len(_hold_indices):
            print(f'✅ 解消完了: 採用 {_state["resolved"]}社 / スキップ {_state["skipped"]}社')
            print(f'CSV: {_csv_path}')
            print('続きを処理するには上の「6. 自動化フロー実行」セルを再実行してください。')
            return

        row_idx = _hold_indices[_state['pos']]
        row = _all_rows[row_idx]
        name = row[_col['company_name']]
        candidates = [c.strip() for c in row[_col['url_candidates']].split('|') if c.strip()]

        display(widgets.HTML(
            f'<h3>[{_state["pos"]+1}/{len(_hold_indices)}] {name}</h3>'
            f'<p>候補ドメイン: {len(candidates)}件 — 採用するURLのボタンを押してください</p>'
        ))

        def _adopt(url):
            _all_rows[row_idx][_col['homepage_url']] = url
            _all_rows[row_idx][_col['error_message']] = ''
            _save_rows(_all_rows)
            _state['resolved'] += 1
            _state['pos'] += 1
            _render()

        for cand in candidates:
            btn = widgets.Button(
                description=f'採用: {cand[:55]}',
                tooltip=cand,
                layout=widgets.Layout(width='95%'),
            )
            btn.on_click(lambda b, u=cand: _adopt(u))
            display(btn)

        custom = widgets.Text(
            placeholder='候補にない正解URLを入力 (https://...)',
            layout=widgets.Layout(width='75%'),
        )
        custom_btn = widgets.Button(description='このURLを採用', button_style='primary')
        custom_btn.on_click(lambda b: _adopt(custom.value.strip()) if custom.value.strip() else None)
        display(widgets.HBox([custom, custom_btn]))

        skip_btn = widgets.Button(description='スキップ →', button_style='warning')
        def _on_skip(b):
            _state['skipped'] += 1
            _state['pos'] += 1
            _render()
        skip_btn.on_click(_on_skip)
        display(skip_btn)

display(_out)
if _hold_indices:
    _render()
else:
    with _out:
        print('✅ 同名企業該当 (URL企業ID不一致) の社はありません。')""")

md("""**選択完了後**:
1. すぐ上の「6. 自動化フロー実行」セルを **再実行** すると、URLを採用した社だけ Step 2 から続きが処理される
2. CSV は採用するたびに即時保存されるので、Colab セッションが切れても作業内容は保たれる
3. ステータス列で `完了` になっている企業は再実行時に自動スキップされる

> CSVを Google スプレッドシートで直接編集することも可能。ただし保存時に必ず **カンマ区切り CSV** で出力すること。

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
