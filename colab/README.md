# DOCdemo Colab 共有ガイド

DOCdemo 自動化フローを **Google Colab** で実行し、チーム内で共有するためのセットアップ手順。

---

## ノートブック一覧

| ファイル | 用途 |
|---|---|
| [docdemo_automation.ipynb](docdemo_automation.ipynb) | **自動化フロー実行用** (本体)。CSVに企業名を並べて全自動処理 |
| [docdemo_delivery_review.ipynb](docdemo_delivery_review.ipynb) | 納品URL レビュー用 (CSV を表示するだけの軽量版) |

---

## チームメンバーへの初回案内 (このまま転送可)

### 1. ノートブックを開く

Colab の右上「**Open in Colab**」または以下のURLをブラウザで開きます (GitHub 認証が必要な場合あり):

```
https://colab.research.google.com/github/shofukui-neo/DOCdemo_update_project/blob/main/colab/docdemo_automation.ipynb
```

または、Colab を開いて **ファイル → ノートブックを開く → GitHub** タブから
`shofukui-neo/DOCdemo_update_project` を検索 → `colab/docdemo_automation.ipynb` を選択。

### 2. Colab Secrets に認証情報を登録 (初回のみ)

左サイドバーの 🔑 (Secrets) アイコンをクリックし、以下を登録 (各項目 `Notebook access` を ON):

| Secret 名 | 値 |
|---|---|
| `DOCDEMO_LOGIN_EMAIL`    | Brainverse 管理画面のメール (社内管理者から取得) |
| `DOCDEMO_LOGIN_PASSWORD` | 同パスワード |
| `GITHUB_TOKEN`           | GitHub Personal Access Token (`repo` スコープ) |

> `GITHUB_TOKEN` は GitHub の **Settings → Developer settings → Personal access tokens (classic)** で発行。
> 期限は最大 90 日推奨 (期限切れで `git clone` が 403 になったら再発行)。

### 3. Drive 共有フォルダを準備 (チームリーダーが初回のみ)

`Google Drive > マイドライブ > DOCdemo_Colab` フォルダを作成し、運用チーム全員に **編集権限** で共有。
このフォルダ内で CSV / ログ / スクショ / 納品URL を共通管理する想定。

```
MyDrive/
└── DOCdemo_Colab/
    ├── data/
    │   ├── company_list.csv          ← 進捗台帳 (主たるデータ)
    │   └── company_delivery_urls.csv ← 納品用シンプルCSV (自動生成)
    ├── logs/
    │   └── automation_<timestamp>.log
    ├── screenshots/
    └── verification_checklist.md
```

### 4. 実行

メニュー **ランタイム → すべて実行** (`Ctrl+F9`)。
1社あたり 3〜4分、100社で 6〜7時間。Colab のセッションタイムアウト (12h) 内に収まる規模で運用。

---

## 運用ルール (重要)

- **CSVは聖域**: `MyDrive/DOCdemo_Colab/data/company_list.csv` は手動編集も自動更新もある。**編集する時は orchestrator が実行されていないことを必ず確認**してから開く。
- **再実行は安全**: 「完了」になっている企業は再実行で自動スキップされる。途中エラー / 「同名企業該当」のものだけ続きから処理される。
- **「同名企業該当」のレビュー**: 週末に CSV の `同名企業該当` 行をまとめてレビューし、URL を埋めて再実行が運用しやすい (詳細はノートブックの「11. 同名企業該当への対応」参照)。
- **大量処理時**: 50社を超える場合は **分割実行** 推奨 (Colab セッション切れリスク回避のため、20-30社ずつ別CSVに分けて実行)。
- **認証情報の更新**: Brainverse 側でパスワード変更があったら、各メンバーが自分の Colab Secrets を更新するだけで OK (コード変更不要)。

---

## ノートブック更新時の運用 (リポジトリ管理者向け)

`docdemo_automation.ipynb` を編集するときは、**直接 .ipynb を編集せず**、生成スクリプトを編集する:

```powershell
# 1. セル内容を編集
notepad colab\_build_notebook.py

# 2. ノートブックを再生成
$env:PYTHONIOENCODING="utf-8"; python colab\_build_notebook.py

# 3. コミット & プッシュ
git add colab/_build_notebook.py colab/docdemo_automation.ipynb
git commit -m "colab: <変更内容>"
git push
```

チームメンバーがノートブックを次回 Colab で開いたとき、「2. リポジトリの取得 (常に最新版を pull)」セルが自動的に最新の実装を pull してくれる。

---

## トラブルシューティング

ノートブック内の「12. トラブルシューティング」セクションを参照。
それでも解決しない場合は Slack `#docdemo-auto` まで。
