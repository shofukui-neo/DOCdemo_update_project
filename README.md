# DOCdemo 自動化システム — 社内導入ハウトゥー

カジュアル面談エージェント（Brainverse 製管理画面）への企業登録〜FAQ/企業情報コンテンツ生成〜納品URL取得を自動化するツール。

> **本書のゴール**: 新しいメンバーがこのリポジトリを zero から手元で動かせるようになり、CSV に企業名を並べるだけで「納品URL」が手に入る状態を作ること。

---

## 0. このシステムでできること

このシステムは **複数のフェーズ** に分かれています。1列(企業名のみ)のCSVから納品URLまで一気通貫で生成し、最後に自動品質チェックできます。

| フェーズ | 担当スクリプト | 入力 | 出力 |
|---|---|---|---|
| **Stage ① URL選定** | [select_urls.py](select_urls.py) | 企業名のみ / 企業名+URL のCSV | ホームページURL確定済の8列CSV |
| **Stage ② デモ作成** | [orchestrator.py](orchestrator.py) | Stage ① 出力 CSV | 納品URL確定済CSV |
| **Stage ③ 画像差替** *(構築中)* | `select_images.py` *(予定)* | 完了済企業 | 背景画像アップロード完了 |
| **Stage ④ 品質チェック** | [verify_quality.py](verify_quality.py) | 完了済企業 (納品URL有) | CSV「品質チェック」列更新 + NG企業はエラーに戻す |

**Stage ①** は企業名から HP URL を Yahoo! 検索で自動特定。複数候補が見つかった企業は **HOLD UI ポップアップ**で人間が選択。

**Stage ②** は確定 URL を元に Brainverse 管理画面で企業追加〜FAQ生成〜保存〜納品URL取得。生成・保存検証が失敗したら最大3回まで自動再試行。

**Stage ③** *(仕様策定中)*: Web画像検索3枚 + HPスクショ + 企業ロゴ の計5候補から人間が1枚選択→背景画像としてアップロード。

**Stage ④** は納品URL を実機で開き、5項目 (HTTP / 企業名表示 / 背景画像 / FAQ / **AIチャット返信内容**) を自動チェック。NGなら status を「エラー」に戻し、Stage ② 再実行で自動修復。

> 注: Stage ② 完了時点で納品URLは取得できます。Stage ③ の画像反映は別工程として運用してください。

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

### 3-0. 全体の流れ

```
  [入力CSV]
   ├ 1列: 企業名のみ
   ├ 2列: 企業名,ホームページURL
   └ 8列: 既存フル
        ↓
  ┌────────────────────────────────┐
  │ Stage ① select_urls.py         │  URL検索 → HOLD UI → URL確定
  └────────────────────────────────┘
        ↓ (data/company_list.csv は同じものを更新)
  ┌────────────────────────────────┐
  │ Stage ② orchestrator.py        │  企業追加→FAQ生成→保存→納品URL取得
  └────────────────────────────────┘
        ↓
  ┌────────────────────────────────┐
  │ Stage ③ (構築中)                │  背景画像5候補から1選択→アップロード
  └────────────────────────────────┘
        ↓
  [納品URL付きCSV] → クライアント共有
```

CSV は **同じファイル** (`data/company_list.csv` 等) を各ステージで更新していきます。

### 3-1. 入力 CSV の準備

**最小構成 (1列のみ)** — 企業名だけのCSVから始められます:

```csv
企業名
株式会社サンプル
テスト株式会社
```

**2列構成** — URLが分かっている企業はそのまま入れておけば検索を省略:

```csv
企業名,ホームページURL
株式会社サンプル,https://sample.co.jp
テスト株式会社,
```

**既存スキーマ (8列)** も自動認識します。再実行時は前回の進捗を引き継ぎ。

初期CSVを企業名リストから一括生成したい場合:

```powershell
notepad create_initial_csv.py    # COMPANY_NAMES を編集
python create_initial_csv.py     # data/company_list.csv を生成
```

---

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
# 標準実行 (HOLD があれば自動でUI起動)
python select_urls.py --csv data/company_list.csv --no-headless

# UIを起動したくない場合 (HOLD は CSV に記録のみ)
python select_urls.py --csv data/company_list.csv --no-popup
```

**処理内容:**

| 状態 | 検出条件 | 結果 |
|---|---|---|
| 自動採用 | 候補URLから抽出した企業IDが 1種類 (TLD違い等) | `URL特定済` + `ホームページURL` 自動入力 |
| **HOLD** | 候補IDが 2種類以上 | `同名企業該当` + 全社処理後に **HOLD UIポップアップ起動** |
| スキップ | 検索結果なし | `スキップ` + エラー詳細 |
| 既済 | 既に `ホームページURL` が入っている | 何もしない (Stage ② の対象) |

**HOLD UI の操作:**

ポップアップで該当企業のURL候補が並びます。各候補は:
- 「このURLを採用」ボタン → URLを確定
- 「ブラウザで開く」ボタン → 候補ページをプレビュー
- 「カスタムURL」欄 → 候補にない正しいURLを直接入力

「終了して保存」でCSVに反映。

> 後で再度 HOLD UI を開きたい場合: `python resolve_hold_ui.py --csv data/company_list.csv`

---

### 3-3. Stage ② — デモ作成 (`orchestrator.py`)

**実行コマンド:**

```powershell
# 全件処理
python orchestrator.py --csv data/company_list.csv --no-headless

# 特定1社のテスト
python orchestrator.py --test-mode --company "株式会社サンプル" --no-headless

# 大量処理 (ヘッドレス)
python orchestrator.py --csv data/company_list.csv --headless
```

**処理内容:**

| Step | 内容 |
|---|---|
| 0 | `ホームページURL` 未入力行は自動スキップ (Stage 1 未完の企業を保護) |
| 2 | Brainverse 管理画面に企業追加 → 企業ID検証 (Step 2.5) |
| 3 | HP内部リンク + 求人サイトURLを収集 (FAQ生成の素材) |
| 4 | コンテンツ生成 → FAQ実体検証 → 保存(2段階) → コンテンツ管理タブで再検証 |
| 5 | フロントエンドアプリURL (= 納品URL) を取得し CSV に書戻し |

**自動リトライ:**
- Step 4 の生成失敗 / 保存検証失敗 (FAQ未生成・別企業データ混入) は **最大3回まで自動再生成** (`config.FAQ_SAVE_MAX_RETRIES`)
- ボタン押下の取り違え (例: 「FAQ保存」ボタンが「プレビュー・保存」タブと誤判定) を防ぐため、AND/除外条件でボタン特定を厳格化
- 1件処理ごとにページクローズ&再ログインで Streamlit セッション state を完全リセット

**大量処理（ヘッドレス）:**

```powershell
python select_urls.py --headless
python orchestrator.py --headless
```

> 1社あたり Stage 1 で約 **5秒**、Stage 2 で **3〜4分**。147社で Stage 2 が約7時間（過去実績）。

> 1社あたり目安 **3〜4分**。1社目はサーバキャッシュが冷えており追加で1〜2分かかることあり。

**1社あたり目安と進捗確認:**

| 確認場所 | 内容 |
|---|---|
| **コンソール** | リアルタイムログ |
| [logs/automation.log](logs/automation.log) | 全実行履歴の追記ログ |
| [data/company_list.csv](data/company_list.csv) | 1社ごとのステータスと**納品URL**が逐次更新 |

ステータスの読み方:

| ステータス | 意味 | 担当ステージ |
|---|---|---|
| 未処理 | まだ何もしていない | Stage ① |
| URL特定済 | HP URLを取得済 | Stage ② |
| **同名企業該当** | 検索候補が複数あり手動判断待ち | Stage ① の HOLD UI |
| 企業追加済 / コンテンツ生成済 / 画像UP済 | Stage ② の中間状態 | Stage ② (再開時に途中から続行) |
| **完了** | **納品URL取得済** | (Stage ③ で画像も差し替えるとベター) |
| エラー | 例外で停止 | エラー詳細列を読んで再実行 |
| スキップ | URL候補なし | 手動でURLを入れて再実行 |

---

### 3-4. Stage ④ — 納品URL品質チェック (`verify_quality.py`)

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

### 3-3. 納品物の納品先

自動化完了後、[data/company_list.csv](data/company_list.csv) の各行 **「納品URL」列** に納品先が記録されます。

```
https://casual-interview-dev.brainverse-ai.com/<企業ID>
```

このURLをクライアントに渡してください。

### 3-4. Stage 4 — 納品URL品質チェック (`verify_quality.py`)

**実行コマンド:**

```powershell
# 全完了企業を再チェック (推奨)
python verify_quality.py --csv data/company_list.csv --no-headless

# 特定1社のみテスト
python verify_quality.py --company "株式会社サンプル" --no-headless

# 大量処理 (ヘッドレス)
python verify_quality.py --csv data/company_list.csv --headless
```

**チェック内容 (5項目):**

| 項目 | 検証 |
|---|---|
| HTTP | 納品URLが 2xx で応答するか |
| 企業名 | title / h1〜h3 / header に対象企業名 or 企業ID が表示されているか |
| 背景画像 | 任意要素に `background-image` CSS または大きめ `<img>` (≥200×100) があるか |
| FAQ | 本文に FAQ パターン (FAQ N / Q1: / 質問N / Q&A) + 対象企業名が同時にあるか |
| **AIチャット** | チャット起動ボタンクリック → テスト質問送信 → **返信に対象企業名が含まれるか** |

AIチャットの質問文は [config.py](config.py) `QUALITY_CHECK_CHAT_QUESTION` で変更可。

**判定ロジック:**

| 結果 | 条件 | 影響 |
|---|---|---|
| **OK** | 全項目OK | ステータス維持 (`完了`) |
| **部分OK** | 一部 SKIP (Webアプリ側の制約で判定不能) | ステータス維持 |
| **NG** | 1項目以上 NG | **ステータスを `エラー` に戻す** → 次回 Stage ② で自動再処理 |

**出力:**
- CSV「品質チェック」列: OK / NG / 部分OK
- CSV「品質チェック詳細」列: `HTTP=OK / 企業名=OK / 背景画像=NG(...) / FAQ=OK / AIチャット=OK(...)`
- `screenshots/quality/<企業ID>.png`: 検証時のスクリーンショット
- 全件サマリは標準出力 + `logs/automation.log`

**運用例 (品質保証ループ):**

```pwsh
python orchestrator.py --csv data/company_list.csv      # Stage 2: デモ作成
python verify_quality.py --csv data/company_list.csv    # Stage 4: 品質チェック (NGをエラーに戻す)
python orchestrator.py --csv data/company_list.csv      # Stage 2再実行: エラーになったものを再処理
python verify_quality.py --csv data/company_list.csv    # Stage 4再実行: 再チェック
# OK率が安定するまで繰り返し
```

---

### 3-6. Stage ③ — 背景画像差替 *(構築中)*

仕様策定中。完成すると以下のフローで使えるようになります:

```powershell
python select_images.py --csv data/company_list.csv  # ※未実装
```

**予定動作:**
1. 完了済の各企業について、5候補画像を収集 (Web画像検索3 + HPスクショ1 + 企業ロゴ1)
2. tkinter UI で5枚を並べて表示
3. 人間が1枚選択 → 他4枚を削除 → 選択画像を `upload_background_image` で背景画像にアップロード

現在は [verification_checklist.md](verification_checklist.md) を使って手動で背景画像をチェック・差替してください。

---

### 3-7. 補助スクリプト

| スクリプト | 用途 |
|---|---|
| [resolve_hold_ui.py](resolve_hold_ui.py) | HOLD UI を手動起動 (Stage ① の `--no-popup` で後送り処理用) |
| [generate_delivery_list.py](generate_delivery_list.py) | `企業名,納品URL` の 2列CSV を出力 (クライアント納品用) |
| [generate_checklist.py](generate_checklist.py) | `verification_checklist.md` を再生成 (手動検証用) |
| [verify_delivery.py](verify_delivery.py) | 納品URLの実機HTTPチェック |

```powershell
# クライアント納品用シンプルCSV
python generate_delivery_list.py     # → data/delivery_urls.csv

# 手動検証チェックリスト
python generate_checklist.py         # → verification_checklist.md
```

検証担当者は各URLを開き、3つのチェック項目を埋めてください:
- **背景画像**: アップロードした画像が表示されているか
- **企業名一致**: ヘッダーの企業名が正しいか
- **FAQ**: AI面談を起動して FAQ が対象企業の内容か

---

## 4. システムが自動でやっている品質検証

実行中、以下の検証ポイントで**自動的に**検証されます。失敗時はステータスが「エラー」または「同名企業該当」になり、人に知らせます。

### Stage ① `select_urls.py` の検証
| 検証ポイント | 内容 |
|---|---|
| URL候補ID重複 | 検索結果から抽出した企業IDが2種類以上あれば HOLD UI で人間判断に委ねる |

### Stage ② `orchestrator.py` の検証
| 検証ポイント | 内容 | 失敗時の挙動 |
|---|---|---|
| Step 2.5 | 企業追加直後、URLから抽出した企業IDがページに反映されているか | 例外で停止 |
| Step 4-pre | コンテンツ生成画面のヘッダーが対象企業に切替済か | 例外で停止 |
| **Step 4-gen** | 生成完了検出後、FAQ実体 (FAQ N / Q1: パターン2件以上 + 対象企業名) が DOM 上に現れたか | **Step 4 から自動再試行** |
| **Step 4-post** | コンテンツ管理タブのFAQ・企業情報両タブに対象企業データ + 実体テキストが存在するか | **Step 4 から自動再試行** |

リトライ回数は `config.FAQ_SAVE_MAX_RETRIES = 2` (合計3回試行)。

**安定化処理:**
- ボタン特定は AND/除外条件の厳密マッチ (Playwright `.first` が DOM 順で「プレビュー・保存」タブを誤掴みしないように)
- 生成完了待機は **最低15秒 + スピナー出現確認** (前回ページ残骸を「完了」と誤判定しないように)
- 企業追加完了後 / 1社処理完了後に **ページクローズ&再ログイン** — Streamlit セッション state を完全リセット

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
| **「サーバーダウン検出」ログが出て処理が中断する** | Brainverse 側の管理画面サーバー (`*.brainverse-ai.com`) が一時的にダウン or 502/503/504 を返している | 自動で最大 30分まで復旧をポーリング待機 → 復旧したら同じ企業から再開。30分経っても復旧しなければ中断して途中ステータスを保持するので、復旧後にもう一度同じコマンドを再実行すれば続きから自動再開する |

### 5-1. サーバーダウン時の自動挙動 (Stage 2 / Stage 4)

Brainverse 管理画面サーバーが落ちた場合、`orchestrator.py` および `verify_quality.py` は以下の手順で自動対応します:

1. 5xx (500/502/503/504 等) または接続不可 (`ERR_CONNECTION_REFUSED` 等) を検知したら `ServerDownError` を送出
2. その企業のステータスは **「エラー」に落とさず途中状態のまま保持** (再実行時に続きから自動再開できるように)
3. サーバー復旧を **30秒おきにヘルスチェック (最大 30分)** 待機
4. 復旧を検出 → 再ログイン → **同じ企業を最初から再試行**
5. 30分以内に復旧しなければ → 残り企業数を表示してクリーンに終了

「全件が一斉にエラー化」「セッションが壊れたまま走り続ける」といった事故を防ぎます。

各パラメータは [config.py](config.py) で調整可能:

| 設定 | デフォルト | 意味 |
|---|---|---|
| `SERVER_RECOVERY_MAX_WAIT_MINUTES` | 30 | 最大復旧待機時間 (分) |
| `SERVER_RECOVERY_POLL_INTERVAL_SECONDS` | 30 | ヘルスチェック間隔 (秒) |
| `SERVER_HEALTH_CHECK_TIMEOUT_SECONDS` | 10 | 1回のヘルスチェックタイムアウト (秒) |
| `SERVER_DOWN_HTTP_STATUSES` | `{500,502,503,504,521-524}` | サーバーダウン扱いとする HTTP ステータス |

ログの詳細は [logs/automation.log](logs/automation.log) を確認。エラー時はスクリーンショットが [screenshots/](screenshots/) に保存されています。

---

## 6. ファイル構成リファレンス

```
DOCdemo_update_project/
├── select_urls.py               # ★ Stage 1: URL候補検索 (Stage 1 のエントリポイント)
├── resolve_hold_ui.py           # ★ Stage 1: HOLD候補のGUI選定 (Stage 1 から自動起動)
├── orchestrator.py              # ★ Stage 2: 企業追加〜納品URL取得 (Stage 2 のエントリポイント)
├── verify_quality.py            # ★ Stage 4: 納品URL品質チェック (5項目+AIチャット)
│
├── config.py                    # 設定（URL・タイムアウト・CSV列名・リトライ等）
├── models.py                    # CompanyInfo, ProcessStatus
├── spreadsheet_manager.py       # CSV 読み書き (1/2/8列入力 + null-safe)
├── url_finder.py                # Yahoo! 検索で URL 候補取得 (Stage ①)
├── recruit_url_finder.py        # 求人サイトURL収集 (Stage ② Step 3)
├── image_fetcher.py             # 企業HPのOGP画像・スクショ取得
├── link_extractor.py            # 内部リンク抽出
├── web_app_operator.py          # Playwright で Brainverse 管理画面操作
│
├── create_initial_csv.py        # 初期 CSV 生成スクリプト
├── generate_checklist.py        # 検証チェックリスト生成
├── generate_delivery_list.py    # クライアント納品用シンプルCSV生成
├── verify_delivery.py           # 納品 URL 実機検証スクリプト
├── data/
│   ├── company_list.csv         # ★ 企業リスト・進捗台帳・納品URL（フル列）
│   └── delivery_urls.csv        # ★ 企業名 + 納品URL のみ（納品用シンプル版）
├── screenshots/                 # HP画像・検証スクショ・エラースクショ
│   └── quality/                 # Stage ④ 品質チェック時の納品URLスクショ
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

最終更新: 2026-05-18 — 2段階構成および品質チェック追加
