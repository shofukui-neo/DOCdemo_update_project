# DOCdemo Automation — リファクタリング計画書

**作成日**: 2026-05-20
**目的**: 2026-05-19 のコミット `79f2cbf` で混入した生成ロジック回帰の修正と、コードベース全体の整理

---

## 1. 回帰の経緯 (Root Cause Analysis)

### 1.1 タイムライン

| 日付 | コミット | 動作 | コード行数 (web_app_operator.py) |
|---|---|---|---|
| 2026-05-14 17:30 | `f20a7c0` | ✅ 動作 | 1,815 |
| 2026-05-18 09:14 | `3f08602` | ✅ 動作 | 2,052 |
| **2026-05-19 17:28** | **`79f2cbf`** | **❌ 回帰開始** | **2,734 (+682行)** |
| 2026-05-20 14:47 | `feaceac` | 部分修正試行 | 2,734 |
| 2026-05-20 現在 | (uncommitted) | 修正進行中 | 2,843 |

### 1.2 5/19 18:52 以降の症状

`79f2cbf` コミット直後の本番ランで、コンテンツ生成300秒タイムアウトが全社で発生:
- 2026-05-19 18:52: 9社連続失敗開始
- 2026-05-20: 同パターン継続
- 失敗率: 5/14-18 ≒ 0%、5/19-20 ≒ 100%

### 1.3 何が追加されたか (79f2cbf による変更)

#### 追加された関数 (バグ要因)
1. **`_wait_for_in_progress_to_clear`** — 「生成中」テキストが消えるまで最大300秒待機
2. **`_verify_faq_generation`** に「生成中残留待機」フェーズ追加 — 既存FAQ検証の前段にガードを挿入

#### 追加された定数 (バグ要因)
- `in_progress_pattern` 正規表現 — `main` 全体 inner_text にマッチさせる方針
- `completion_pattern` 正規表現 — 実画面に出ない `生成完了|保存準備` 等のみを検出対象に
- `GEN_TAIL_MAX_MS = CONTENT_GENERATION_TIMEOUT` (= 300,000ms) — 「生成中」待機上限

### 1.4 なぜ壊れたか

DOCdemo 管理画面 (Streamlit) の挙動:
- 生成開始時に `st.info("FAQを生成中...")` / `st.info("企業情報を生成中...")` がレンダリング
- 生成完了後も **これらの info box は消えない** (UI 側の実装上の特性)
- 完了時には別途 `✅ コンテンツが正常に生成されました!` の緑バーが出る

`79f2cbf` で追加されたガードは「`main` 全体に "生成中" 文字列が残っている間は生成継続中」と判定するため、永久に完了しない → 300秒タイムアウト。

実際の成功状態 (`screenshots/gen_verify_fail_athena-kogyo.png` 参照):
- ✅ "21個のコンテンツソースから生成準備完了"
- 🔴 [コンテンツ生成] ボタン (押下可能)
- 🔵 FAQを生成中... ← **残留 (バグの原因)**
- 🔵 企業情報を生成中... ← **残留 (バグの原因)**
- ✅ コンテンツが正常に生成されました!
- ✅ 生成されたFAQ ... (FAQ実体)

---

## 2. リファクタリング方針

**基本方針**: 5/19 以前のシンプルな実装に戻す + 必要最小限の堅牢性を加える。

### 2.1 削除対象 (旧実装の残骸)

| 関数/変数 | 場所 | 削除理由 |
|---|---|---|
| `_wait_for_in_progress_to_clear()` | [web_app_operator.py:1697-1755](web_app_operator.py#L1697) | 永続マッチによる300秒ハングの根本原因。5/19 追加で動作しなくなった |
| `in_progress_pattern` (FAQ検証側) | [web_app_operator.py:1384-1386](web_app_operator.py#L1384) | 残留 info box にマッチして検証ブロック |
| `gen_tail_elapsed_ms` 累積ロジック | [web_app_operator.py:1390-1419](web_app_operator.py#L1390) | 「生成中」消失待ち設計が破綻 |
| `_wait_for_in_progress_to_clear` 呼出 | [web_app_operator.py:1827-1832](web_app_operator.py#L1827) | save_content での無用な前段ガード |

### 2.2 復元対象 (5/18 動作版)

| 関数 | 復元元 | 目的 |
|---|---|---|
| `_verify_faq_generation()` | `git show 3f08602` | シンプルな FAQ 実体ポーリング (60秒タイムアウト) |
| `_wait_for_generation_complete()` | `git show 3f08602` | spinner ベースのみの完了検出 |

### 2.3 保持する 2026-05-20 修正 (有効な改良)

- [models.py:49-53](models.py#L49-L53) `TRANSIENT_UI_ERROR_PATTERNS` への "コンテンツ生成がタイムアウト" 追加 → 保持
- `completion_pattern` の拡張 (「コンテンツが正常に生成されました」等) → 保持

### 2.4 追加で修正すべきバグ

#### Bug A: リトライカウンタが永遠にリセット
**場所**: [models.py:262-275](models.py#L262-L275) `reset_for_transient_retry`

**現状**:
```python
def reset_for_transient_retry(self):
    if not self.is_transient_error():
        return
    self.status = ProcessStatus.URL_FOUND
    self.error_message = ""  # ← カウンタも消える
```

**問題**: `[一時的失敗 N/3]` プレフィックスが毎リセットで失われ、`mark_transient_error` で常に `N=1` になる。`TRANSIENT_RETRY_MAX = 3` の上限が機能しない → 無限リトライ。

**修正**:
```python
def reset_for_transient_retry(self):
    if not self.is_transient_error():
        return
    prev_count = parse_transient_retry_count(self.error_message)
    self.status = ProcessStatus.URL_FOUND
    # プレフィックスのみ保持して次回の mark_transient_error で正しくインクリメント
    self.error_message = format_transient_error("(リトライ中)", prev_count) if prev_count > 0 else ""
```

#### Bug B: ログイン3回失敗時に続行
**場所**: [web_app_operator.py 内 `close_page_and_relogin` 等](web_app_operator.py)

**現状**: 3回失敗しても `（続行）` で次の Step に進む → 未ログイン状態で操作。

**修正**: 失敗時はバックオフ (30秒待機) → 1回再試行 → それでも失敗なら例外 raise。

#### Bug C: 多重起動防止が無い
**場所**: [orchestrator.py のエントリ部](orchestrator.py)

**現状**: 同時に複数のオーケストレータが起動可能 → 観察された3プロセス並列の原因。

**修正**: `logs/orchestrator.lock` ファイル + PID チェックで多重起動を拒否。

---

## 3. 具体的な変更案

### 3.1 [web_app_operator.py](web_app_operator.py)

#### 変更 1: `_wait_for_generation_complete()` を簡素化 (line 2618-2734 → 約50行に圧縮)

**Before** (現状、約115行):
- in_progress_pattern による「生成中」テキストポーリング
- status_selectors (stAlert 等) の可視性チェック
- completion_signal 検出
- 複雑な完了判定 (`(not spinner AND not in_progress) OR completion_signal`)

**After** (5/18 版相当、約40行):
```python
async def _wait_for_generation_complete(self):
    """spinner ベースの完了検出 (シンプル版)"""
    MIN_WAIT_MS = 15000
    SPINNER_APPEAR_TIMEOUT = 15000
    poll_interval = 3000
    elapsed = 0
    spinner_selector = "[data-testid='stSpinner'], .stSpinner"

    # Phase 1: spinner 出現を待つ
    spinner_seen = False
    try:
        await self.page.locator(spinner_selector).first.wait_for(
            state="visible", timeout=SPINNER_APPEAR_TIMEOUT
        )
        spinner_seen = True
    except Exception:
        pass

    # Phase 2: spinner 消失をポーリング + 明示的成功メッセージ検出
    success_pattern = re.compile(
        r"コンテンツが正常に生成されました|"
        r"企業情報が正常に生成されました|"
        r"FAQを生成しました|"
        r"生成されたFAQ"
    )
    while elapsed < CONTENT_GENERATION_TIMEOUT:
        await self.page.wait_for_timeout(poll_interval)
        elapsed += poll_interval

        # エラー検出
        try:
            error = self.page.locator("[data-testid='stAlert']")
            if await error.count() > 0:
                text = await error.first.text_content() or ""
                if ("エラー" in text or "Error" in text) and "生成中" not in text:
                    raise RuntimeError(f"コンテンツ生成エラー: {text}")
        except RuntimeError:
            raise
        except Exception:
            pass

        # 成功メッセージ早期検出
        try:
            page_text = await self.page.locator("main").first.inner_text(timeout=2000)
            if success_pattern.search(page_text or ""):
                logger.info(f"生成完了検出 (経過: {elapsed/1000}秒, 完了メッセージ検出)")
                return
        except Exception:
            pass

        # spinner 消失 + 最低待機経過
        spinner_visible = False
        try:
            spinner_visible = await self.page.locator(spinner_selector).is_visible()
        except Exception:
            pass

        if not spinner_visible and elapsed >= MIN_WAIT_MS:
            logger.info(f"生成完了検出 (経過: {elapsed/1000}秒, spinner_seen={spinner_seen})")
            return

    raise TimeoutError(f"コンテンツ生成がタイムアウト ({CONTENT_GENERATION_TIMEOUT/1000}秒)")
```

#### 変更 2: `_verify_faq_generation()` を 5/18 版に戻す

**削除**: 「生成中残留待機」フェーズ全体 (line 1384-1422)

**復元**: シンプルな FAQ 実体ポーリング (60秒タイムアウト)

#### 変更 3: `_wait_for_in_progress_to_clear()` 関数を **削除**

呼び出し元 (save_content) も削除し、save_content は直接「プレビュー・保存」タブクリックに進む。

### 3.2 [models.py](models.py)

#### 変更 1: `reset_for_transient_retry()` でカウンタ保持 (Bug A)

```python
def reset_for_transient_retry(self):
    if not self.is_transient_error():
        return
    prev_count = parse_transient_retry_count(self.error_message)
    self.status = ProcessStatus.URL_FOUND
    if prev_count > 0:
        self.error_message = f"[一時的失敗 {prev_count}/{TRANSIENT_RETRY_MAX}] (リトライ待ち)"
    else:
        self.error_message = ""
```

### 3.3 [orchestrator.py](orchestrator.py)

#### 変更 1: 多重起動防止 (Bug C)

`main()` の冒頭に PID ファイルチェックを追加:

```python
import os, atexit
LOCK_FILE = "logs/orchestrator.pid"

def acquire_lock():
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)  # シグナル0 で存在確認
            logger.error(f"既に別の orchestrator が動作中 (PID {old_pid})")
            sys.exit(1)
        except OSError:
            pass  # 古いプロセスは終了済 → 続行
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(LOCK_FILE) and os.remove(LOCK_FILE))
```

#### 変更 2: ログイン失敗時の挙動 (Bug B)

ログイン3回失敗時は続行ではなく中断。

```python
# 該当箇所
if not await operator.ensure_logged_in():
    logger.error("ログイン失敗。30秒待機して1回だけ再試行")
    await asyncio.sleep(30)
    if not await operator.ensure_logged_in():
        raise RuntimeError("ログイン復旧に失敗。ランを中断します")
```

---

## 4. リスク評価

| リスク | 影響 | 軽減策 |
|---|---|---|
| 5/19 で追加された改善も巻き戻す可能性 | 中 | git show 79f2cbf で意図された改善内容を確認、必要な部分のみ移植 |
| `save_content` 後の生成中ガード削除で誤動作 | 低 | save タブクリック前に短時間 (5s) の `_wait_for_streamlit_load` 待機を入れる |
| 既存テスト (`tests/test_faq_generation_robustness.py`) が失敗 | 中 | テスト内容を確認し、必要に応じて更新 |

---

## 5. 実施手順

1. ✅ **計画書レビュー** (この文書) ← **現在のステップ**
2. **動作中の Python プロセスを停止** (PID 27452, 27616, 31004)
3. **ブランチ作成**: `git checkout -b refactor/restore-generation-logic`
4. **作業用バックアップ**: `cp web_app_operator.py web_app_operator.py.bak`
5. **変更 3.1** (web_app_operator.py): `_wait_for_generation_complete` 簡素化
6. **変更 3.2** (models.py): `reset_for_transient_retry` 修正
7. **変更 3.3** (orchestrator.py): 多重起動防止 + ログイン失敗時中断
8. **テスト実行**: `python orchestrator.py` を1企業だけで dry-run
9. **本番再開**: 全 11社のリトライ予算をリセットして再ラン
10. **PR / コミット**: 1論理変更 = 1コミットで分割

---

## 6. 影響範囲

### 6.1 関連ファイル

| ファイル | 変更行数 (推定) |
|---|---|
| [web_app_operator.py](web_app_operator.py) | -200, +50 (差し引き -150 行) |
| [models.py](models.py) | -2, +5 |
| [orchestrator.py](orchestrator.py) | +30 |
| `data/company_list.csv` | 既存のエラー状態 11社を URL_FOUND にリセット (手動 or スクリプト) |

### 6.2 テストファイル

- [tests/test_faq_generation_robustness.py](tests/test_faq_generation_robustness.py) — 削除対象関数を参照している可能性。確認して更新が必要

---

## 7. 確認事項 (ユーザーへ)

実施前に以下を確認してください:

- [ ] この計画書の内容を承認する
- [ ] Python プロセス停止 (`Stop-Process -Id 27452,27616,31004 -Force`) を実行
- [ ] ブランチ作成して着手することに同意する (main 直接修正は避ける)
- [ ] [tests/test_faq_generation_robustness.py](tests/test_faq_generation_robustness.py) の役割について追加情報があれば共有

承認後に変更を実施します。
