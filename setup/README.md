# setup/ — 環境構築ツール

新しいPCにこのプロジェクトをセットアップする際に使用します。

## ファイル構成

| ファイル | 用途 |
|---------|------|
| `check_env.ps1` | 現在のインストール状況を調査して `env_report.txt` に保存 |
| `setup_env.bat` | 不足分を自動でインストール（winget + pip + playwright） |
| `setup_env_py312.bat` | Python 3.12.10 で開発環境を自動構築 |
| `env_report.txt` | `check_env.ps1` が出力する調査結果（実行後に生成） |

---

## 使い方

### ① 現状調査（先に実行）

PowerShell を開いて以下を実行:

```powershell
cd C:\Users\<ユーザー名>\Desktop\DOCdemo_update_project
powershell -ExecutionPolicy Bypass -File .\setup\check_env.ps1
```

出力結果:
- 画面に色付きで表示（OK=緑 / MISS=赤 / WARN=黄）
- `setup\env_report.txt` にも保存

確認項目:
- Git / Python / pip / VSCode / Node.js
- `.venv` 仮想環境の有無
- `requirements.txt` の9パッケージ個別チェック
- Playwright ブラウザのキャッシュ
- Git のユーザー名・メール設定

### ② 自動構築（不足があれば実行）

`setup_env.bat` または `setup_env_py312.bat` を **右クリック → 管理者として実行**

実行ステップ:
1. **winget** で Git / Python 3.12 / VSCode をインストール（既にあればスキップ）
2. `.venv` を作成
3. `requirements.txt` のパッケージを pip install
4. `playwright install` でChromium/Firefox/WebKitをダウンロード
5. Git のユーザー名・メールが未設定なら対話入力

### ③ 再度 ① を実行して確認

```powershell
powershell -ExecutionPolicy Bypass -File .\setup\check_env.ps1
```

すべて `[ OK ]` になれば完了。

---

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `winget` が見つからない | Microsoft Storeで「アプリ インストーラー」をインストール |
| Python インストール後も `python` が認識されない | PCを再起動してから `setup_env.bat` を再実行 |
| `ExecutionPolicy` エラーで PS1 が動かない | `-ExecutionPolicy Bypass` オプションを付けて起動（上記の例参照） |
| `playwright install` でネットワークエラー | プロキシ環境の場合は `HTTPS_PROXY` 環境変数を設定後にリトライ |
| `.venv` 作成に失敗 | Pythonのバージョンを確認（3.10以上推奨） |

---

## 想定環境

- Windows 10 (1809以降) / Windows 11
- PowerShell 5.1 以上
- 管理者権限（winget使用のため）
