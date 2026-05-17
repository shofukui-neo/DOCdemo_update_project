# DOCdemo 自動化システム — 社内導入ハウトゥー

カジュアル面談エージェント（Brainverse 製管理画面）への企業登録〜FAQ/企業情報コンテンツ生成〜納品URL取得を自動化するツール。

> **本書のゴール**: 新しいメンバーがこのリポジトリを zero から手元で動かせるようになり、CSV に企業名を並べるだけで「納品URL」が手に入る状態を作ること。

---

## 0. このシステムでできること

1. CSV に並べた企業名 1社ずつを Brainverse の管理画面に登録
2. 企業 HP を Yahoo! 検索で自動特定（複数候補がある場合は人間判断に委ねる）
3. HP の画像と内部リンクを自動取得し、コンテンツ生成（FAQ＋企業情報）に渡す
4. 生成内容を 2 段階保存し、コンテンツ管理画面で**対象企業のデータか自動検証**
5. 背景画像を自動アップロード
6. 完成したフロントエンド公開URL（=**納品URL**）を CSV に書き戻し

> 注: 背景画像は Web アプリ側の反映遅延等により、納品URL上で即座に表示されない場合があります。**画像確認は手動チェックリストで担保**します。

---

## 1. 動作環境

| 項目 | バージョン |
|---|---|
| OS | Windows 10/11（PowerShell 5.1 以上） |
| Python | 3.12 系 |
| ブラウザ | Playwright が同梱する Chromium（手動インストール不要） |
| ネットワーク | `*.brainverse-ai.com` および `search.yahoo.co.jp` への外向きアクセスが必要 |

---

## 2. 初回セットアップ（社内マシン1台あたり1回だけ）

### 2-1. リポジトリ取得

```powershell
cd C:\Users\<username>\Desktop
git clone <社内 git リポジトリ URL> DOCdemo_update_project
cd DOCdemo_update_project
```

### 2-2. Python仮想環境の作成

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> 実行ポリシー警告が出た場合: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

### 2-3. 依存パッケージのインストール

```powershell
pip install -r requirements.txt
playwright install chromium
```

### 2-4. ログイン認証情報の設定

`.env` ファイルを **プロジェクトルート直下**に作成（`.gitignore` 済みでコミットされません）:

```ini
DOCDEMO_LOGIN_EMAIL=neocareer.dev@admin
DOCDEMO_LOGIN_PASSWORD=neocareer.dev@admin-pw
DOCDEMO_HEADLESS=false
DOCDEMO_LOG_LEVEL=INFO
```

| 変数 | 意味 | 推奨値 |
|---|---|---|
| `DOCDEMO_LOGIN_EMAIL` | Brainverse 管理画面のメール | 環境依存 |
| `DOCDEMO_LOGIN_PASSWORD` | 同パスワード | 環境依存 |
| `DOCDEMO_HEADLESS` | ブラウザを表示するか | 初回は `false`（動作確認用）、安定稼働後は `true` |
| `DOCDEMO_LOG_LEVEL` | ログレベル | 通常 `INFO`、デバッグ時 `DEBUG` |

> 認証情報は社内パスワードマネージャ等で管理してください。Slack 等に貼らないこと。

### 2-5. 動作確認（任意・推奨）

```powershell
python -m pytest tests/ -q
```

41件のテストが PASS すれば環境は OK。

---

## 3. 日常運用フロー

### 3-1. 初期 CSV の作成

[create_initial_csv.py](create_initial_csv.py) に企業名リストを書き、CSV を生成します。

```powershell
notepad create_initial_csv.py
```

```python
COMPANY_NAMES = [
    "株式会社サンプル",
    "テスト株式会社",
    # ...
]
```

```powershell
python create_initial_csv.py
```

→ `data/company_list.csv` が生成され、全企業のステータスは `未処理` になります。

### 3-2. 自動化実行（基本）

本システムは **2段階構成** になりました（2026-05 以降）。

| 段階 | スクリプト | 役割 |
|---|---|---|
| **Stage 1** | `python select_urls.py` | 各企業のホームページURLを Yahoo!検索で候補抽出し、CSV の `URL候補` 列に書き込む。複数候補があれば自動で `resolve_hold_ui.py`（GUI）を起動して人間に選ばせる |
| **Stage 2** | `python orchestrator.py` | URL確定済の行だけを対象に「企業追加〜コンテンツ生成〜納品URL取得」を実行 |

**実行順序:**

```powershell
# 1. URL選定（候補検索 + HOLD UIで人間判断）
python select_urls.py --no-headless

# 2. デモ作成自動化
python orchestrator.py --no-headless
```

Stage 1 で `URL候補` が複数（=同名企業該当）になった行は自動的に `resolve_hold_ui.py` の GUI が立ち上がり、候補ボタンから正解URLを選ぶか、カスタムURLを入力して採用します。GUIを後から再開したいときは:

```powershell
python resolve_hold_ui.py
```

> **注意**: `resolve_hold_ui.py` を `select_urls.py` 実行前に起動すると「先に Stage 1 を実行してください」と表示されます。必ず `select_urls.py` を先に走らせてください。

特定1社のみのテスト実行（Stage 2 のみ）:

```powershell
python orchestrator.py --test-mode --company "株式会社サンプル" --no-headless
```

ヘッドレス（バックグラウンド・大量処理用）:

```powershell
python select_urls.py --headless
python orchestrator.py --headless
```

> 1社あたり Stage 1 で約 **5秒**、Stage 2 で **3〜4分**。147社で Stage 2 が約7時間（過去実績）。

### 3-3. 進捗の確認

実行中は次の3か所で進捗が分かります:

| 確認場所 | 内容 |
|---|---|
| **コンソール** | リアルタイムログ |
| [logs/automation.log](logs/automation.log) | 全実行履歴の追記ログ |
| [data/company_list.csv](data/company_list.csv) | 1社ごとのステータスと**納品URL**が逐次更新 |

ステータスの読み方:

| ステータス | 意味 | 次にやること |
|---|---|---|
| 未処理 | まだ何もしていない | 自動化実行で進む |
| URL特定済 | HP URL を1件取得した | 自動的に Step 2 へ |
| **同名企業該当** | 検索候補が複数あり、人が判断する必要 | **手動介入（後述 3-4）** |
| 企業追加済 | Brainverse に企業を登録した | 自動的に Step 3 へ |
| コンテンツ生成済 | FAQ・企業情報生成完了 | 自動的に Step 5 へ |
| 画像UP済 | 背景画像アップロード完了 | 自動的に Step 6 へ |
| 完了 | **納品URL 取得済（CSVの「納品URL」列を見る）** | 何もしなくてよい |
| エラー | 何かしらの例外で停止 | エラー詳細列を読む、必要なら再実行 |
| スキップ | HP URL が見つからなかった | 手動で URL を埋めて再実行 |

### 3-4. 「同名企業該当」になったときの手動介入

検索結果に複数のドメインがあり、自動で判定できない場合に発生します。

**推奨フロー（GUIで一括処理）:**

`select_urls.py` 実行後に HOLD 行が 1社以上あれば、`resolve_hold_ui.py`（tkinter製GUI）が自動で立ち上がります。各企業について

- 候補URL横の「このURLを採用」ボタンで採用
- 「ブラウザで開く」で候補を実機確認
- 候補にない場合は下部の「カスタムURL」欄に貼り付け→「このURLを採用」

を選ぶと CSV にその場で書き戻され、次の HOLD 企業に進みます。完了後に `python orchestrator.py` を実行するだけで Stage 2 に進めます。

**従来通り CSV を直接編集する方法（GUIを使わない場合）:**

1. `python select_urls.py --no-popup` で HOLD UI を起動せず CSV だけ更新
2. [data/company_list.csv](data/company_list.csv) を Excel または VS Code で開く
3. 該当行を見つける（ステータス列が「同名企業該当」）
4. `URL候補` 列のパイプ `|` 区切りURLから正解を選び、`ホームページURL` 列に貼り付けて保存
5. `python orchestrator.py` を実行

> Excel で開いた場合は **保存して閉じてから** 再実行してください（Excel がファイルをロックします）。

### 3-5. 納品物の納品先

自動化完了後、[data/company_list.csv](data/company_list.csv) の各行 **「納品URL」列** に納品先が記録されます。

```
https://casual-interview-dev.brainverse-ai.com/<企業ID>
```

このURLをクライアントに渡してください。

### 3-6. 品質検証チェックリストの生成

納品物の手動検証用チェックリストを再生成:

```powershell
python generate_checklist.py
```

→ [verification_checklist.md](verification_checklist.md) が更新され、現在のCSVから「企業名／納品URL／背景画像／企業名一致／FAQ」の表が出力されます。

検証担当者は各 URL を開き、3つのチェック項目を埋めてください:

- **背景画像**: アップロードした画像が表示されているか
- **企業名一致**: ヘッダーの企業名が正しいか
- **FAQ**: AI 面談を起動して FAQ が対象企業の内容か

### 3-7. クライアント納品用シンプルCSVの生成

「企業名」と「納品URL」だけの**2列CSV**を出力（クライアント共有用）:

```powershell
python generate_delivery_list.py
```

→ [data/delivery_urls.csv](data/delivery_urls.csv) が生成されます:

```csv
企業名,納品URL
株式会社サンプル,https://casual-interview-dev.brainverse-ai.com/sample
...
```

---

## 4. システムが自動でやっている品質検証

`orchestrator.py` 実行中、以下4つのポイントで**自動的に**検証されます。失敗時はステータスが「エラー」または「同名企業該当」になり、人に知らせます。

| 検証ポイント | 内容 |
|---|---|
| Step 1.5 | 検索結果に複数候補ドメインがあれば人間判断に委ねる |
| Step 2.5 | 企業追加直後、URL から抽出した企業 ID がページに反映されているか |
| Step 4-pre | コンテンツ生成画面のヘッダーが対象企業に切替済か |
| Step 4-post | コンテンツ管理画面で FAQ・企業情報の両タブが対象企業の内容か |

**安定化処理:**
- 企業追加完了後（Step 2.5 後）に **ページを閉じて再ログイン** — ページ内キャッシュ／JavaScript状態が次の企業のコンテンツ生成に混入するのを防止
- 1社処理完了後にもキャッシュクリア＋再ログイン — セッションを毎回フレッシュに保つ

---

## 5. トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `Playwright Browser not found` | `playwright install` を実行していない | `playwright install chromium` |
| `ログイン失敗` | `.env` が存在しない or 値が古い | `.env` を確認、または管理者から最新の認証情報を入手 |
| 全社スキップになる | CSV の `ホームページURL` 列が既に埋まっているのに `未処理` 等 | CSV を確認、`未処理` に戻す or 中間ステータスを設定 |
| 「同名企業該当」が連発 | 検索ノイズが多い業種 | `URL候補` 列を見て手動で正解を入れる |
| エンコード文字化け | Windows コンソール cp932 問題 | 環境変数 `PYTHONIOENCODING=utf-8` を設定 |
| ブラウザが見えない | `DOCDEMO_HEADLESS=true` で起動 | `--no-headless` オプション、または `.env` を `false` に |
| 既存企業に対する再実行でエラー | Brainverse側が稀に重複IDを拒否 | 当該企業のステータスを `完了` のままにして他社のみ処理 |

ログの詳細は [logs/automation.log](logs/automation.log) を確認。エラー時はスクリーンショットが [screenshots/](screenshots/) に保存されています。

---

## 6. ファイル構成リファレンス

```
DOCdemo_update_project/
├── select_urls.py               # ★ Stage 1: URL候補検索 (Stage 1 のエントリポイント)
├── resolve_hold_ui.py           # ★ Stage 1: HOLD候補のGUI選定 (Stage 1 から自動起動)
├── orchestrator.py              # ★ Stage 2: 企業追加〜納品URL取得 (Stage 2 のエントリポイント)
├── config.py                    # 設定（URL・タイムアウト・CSV列名等）
├── models.py                    # CompanyInfo, ProcessStatus
├── spreadsheet_manager.py       # CSV 読み書き
├── url_finder.py                # Yahoo! 検索で URL 候補取得
├── image_fetcher.py             # 企業HPのOGP画像取得
├── link_extractor.py            # 内部リンク抽出
├── web_app_operator.py          # Playwright で Brainverse 管理画面操作
├── create_initial_csv.py        # 初期 CSV 生成スクリプト
├── generate_checklist.py        # 検証チェックリスト生成
├── generate_delivery_list.py    # クライアント納品用シンプルCSV生成
├── verify_delivery.py           # 納品 URL 実機検証スクリプト
├── data/
│   ├── company_list.csv         # ★ 企業リスト・進捗台帳・納品URL（フル列）
│   └── delivery_urls.csv        # ★ 企業名 + 納品URL のみ（納品用シンプル版）
├── screenshots/                 # HP画像・検証スクショ・エラースクショ
├── logs/
│   └── automation.log           # 実行ログ（追記式）
├── tests/                       # ユニットテスト
├── requirements.txt
├── verification_checklist.md    # 手動検証チェックリスト
└── README.md                    # 本書
```

---

## 7. 引き継ぎ・運用ルール

- **CSVは聖域**: `data/company_list.csv` は手動編集も自動更新も両方ある。**編集する時は必ず orchestrator が実行されていないことを確認**してから開く
- **ログは追記式**: `logs/automation.log` は消さない。週次でローテーションする場合は別途運用ルールを決める
- **認証情報の更新**: Brainverse 側でパスワード変更があったら、`.env` を更新するだけで OK（コード変更不要）
- **大量処理時**: 100社を超える場合は `--headless` で実行し、PCをスリープさせない
- **再実行は安全**: 「完了」のものは再実行で自動スキップされる。途中エラーのものだけ自動再開
- **同名企業該当のレビュー**: 週末に CSV の `同名企業該当` 行をまとめてレビュー、URL を埋めて再実行が運用しやすい

---

## 8. 困ったときの連絡先

| 項目 | 連絡先 |
|---|---|
| Brainverse 管理画面の認証 / アカウント | 管理担当者（社内） |
| 自動化スクリプトのバグ | リポジトリ管理者（Slack: #docdemo-auto） |
| 検証チェックリストの運用 | QA チーム |

---

最終更新: 2026-05-18 — 2段階構成 (`select_urls.py` + `orchestrator.py`) に追従
