# Rollback Notes — 2026-05-20

## 背景

直近のリリースで管理画面コンテンツ生成がほぼ全社で失敗するようになった。
`data/company_list.csv` のステータスを見ると、まともに完了している企業がほぼ存在せず、エラー詳細は以下のいずれかに集中している:

- `生成完了検出後、FAQ実体がページに現れません (パターン一致: 1件、120s経過)。生成失敗 or 別企業のデータ残存疑い。`
- `[一時的失敗 1/3] コンテンツ生成がタイムアウト (300.0秒)`
- `コンテンツ生成ボタンが見つかりませんでした (6回試行)`

エラーパターンから、**生成完了検出のロジック (永続マッチ問題) を直す目的で `feaceac` と未コミットの差分** を入れたことが回帰の主因と判断。
**最後に安定動作していた `f593390` (`feat: remove parallel execution from orchestrator to improve content generation reliability`)** を運用ブランチとして切り出す。

## 運用方針 (2026-05-20 確定)

- **`main` は現行実装のまま** (HEAD = `feaceac` + 未コミットの UI 検出絞り込み変更)。
  そのまま `main` でデバッグを継続する。
- **`rollback/stable-2026-05-20` ブランチ** = 安定動作する旧版 (`f593390`)。
  - 他のユーザー / 本番運用は当面このブランチで稼働させる。
  - `main` 側で UI 検出回帰の修正が安定したら、合流して `main` に戻る。
- 退避済みの安全網:
  - `backup/2026-05-20-pre-rollback-feaceac` = `feaceac` (現 HEAD の固定スナップショット)
  - `stash@{0}` = 「pre-rollback uncommitted: ui-progress detection refinement (2026-05-20)」(現 working tree に既に適用済みだが、保険として残置)

### 運用ブランチへの切替方法 (他ユーザー向け)

```powershell
git fetch origin
git switch rollback/stable-2026-05-20      # ローカルにブランチを切る
# (origin に push されていない場合は、シェア相手側で git fetch + checkout)
```

### `main` 側でデバッグするときの開始点

`main` の HEAD には `feaceac` のコミット済み差分と、その上に乗っている未コミット差分があります。
未コミット差分が消えてしまった場合は:

```powershell
git stash list                  # "pre-rollback uncommitted..." を探す
git stash apply stash@{N}
```

## ロールバック対象と理由

| 種別 | コミット / 状態 | 内容 | 疑わしいポイント |
|---|---|---|---|
| HEAD コミット | `feaceac8` | Enhance error handling and CSV generation in Orchestrator; refine UI progress detection in WebAppOperator | `in_progress_pattern` を `stAlert/stStatus/stStatusWidget/stToast/stNotification` の可視要素テキストに限定。これにより従来 `main` 全体を見ていた検出が空振りし、完了判定 → FAQ 実体検出フェーズで失敗する |
| 未コミット変更 | `web_app_operator.py` | 同様の「アクティブステータス要素限定」変更を `_wait_for_in_progress_to_clear` / 検証ループにも横展開。`GENERATION_COMPLETE_PATTERN` をモジュール定数化して "完了シグナルあれば居残り無視" のオーバーライドを追加 | 検出範囲が更に狭まり、現実の生成完了シグナル (Streamlit 内部の表示構造) と乖離 |

## 安定版ブランチ

- 運用ブランチ: **`rollback/stable-2026-05-20`** → `f5933904af53b4a8fbf126e32b4a79ccedae1fb3` (短縮 `f593390`)
  - メッセージ: `feat: remove parallel execution from orchestrator to improve content generation reliability`
  - Author Date: 2026-05-20 10:58 +0900
  - 並列実行を撤回しただけのリリース直後。UI 検出ロジックは未改変で、ここまでは正常生成が確認できていた。

## 復元用ブランチ / スタッシュ一覧

| 種別 | 名前 / ID | 内容 |
|---|---|---|
| ブランチ | `main` | 現行実装 (HEAD = `feaceac` + 未コミット UI 検出絞り込み) — デバッグ対象 |
| ブランチ | `rollback/stable-2026-05-20` | 安定運用版 (`f593390`) — 他ユーザー稼働用 |
| ブランチ | `backup/2026-05-20-pre-rollback-feaceac` | `feaceac` の固定スナップショット (作業前のセーフティ) |
| stash | `stash@{0}` | 「pre-rollback uncommitted: ui-progress detection refinement (2026-05-20)」(現 main の working tree に適用済み・冗長な保険) |

復元コマンド集:

```powershell
# 1. 運用ブランチで動かす (他ユーザー)
git switch rollback/stable-2026-05-20

# 2. main の現行実装でデバッグを続ける
git switch main

# 3. main の未コミット差分が消えた場合の再適用
git stash list                       # メッセージ "pre-rollback uncommitted..." を探す
git stash apply stash@{N}           # 該当 N を指定

# 4. feaceac のコミット内容だけを別ブランチに移植したい場合
git switch rollback/stable-2026-05-20
git cherry-pick feaceac8365f8326fbc66ffbe6f840b15da3807e
```

## ファイル別 — 何を巻き戻すか

### 1. `models.py`

`feaceac` で `TRANSIENT_UI_ERROR_PATTERNS` に2件追加。**機能的に害は薄い** が、ロールバックで一緒に消える。
復元する場合は以下を `TRANSIENT_UI_ERROR_PATTERNS` 末尾に再追加すれば足りる:

```python
TRANSIENT_UI_ERROR_PATTERNS = (
    # ...既存項目...
    "net::ERR_",
    # コンテンツ生成本体のタイムアウト
    # _wait_for_generation_complete() / _wait_for_in_progress_to_clear() が
    # 投げる TimeoutError。サーバー混雑・URL 数過多で起きるため再試行で復旧する。
    "コンテンツ生成がタイムアウト",
    "生成中…」表示が",
)
```

### 2. `orchestrator.py`

`_write_delivery_urls_csv` を「納品URLが確定した行のみ書き出し」に変更したコミット内容。
**こちらは独立して有用** な改良なので、再適用する場合は以下のように `_write_delivery_urls_csv` を差し替え:

```python
def _write_delivery_urls_csv(self):
    """
    企業リストCSV から「納品URL」が確定している行のみを抜き出し、
    「企業名 / 納品URL」2列の簡易CSVをクライアント納品用に生成する。

    出力先:
        元CSVと同じディレクトリに `<stem>_delivery_urls.csv` を書き出す
        (例: data/company_list.csv → data/company_list_delivery_urls.csv)。
        `<stem>_company_list` 形式の場合は接尾辞を `_delivery_urls` に置換。

    仕様 (2026-05-20 改訂):
        - 納品URL が空の行はスキップする (納品済みのみを書き出す)
        - 該当社が0件の場合でもヘッダ行だけのCSVは生成する
    """
    import csv as csvmod

    src_path = Path(self.sheet_manager.csv_path)
    stem = src_path.stem
    if stem.endswith("_company_list"):
        out_stem = stem[: -len("_company_list")] + "_delivery_urls"
    else:
        out_stem = f"{stem}_delivery_urls"
    out_path = src_path.parent / f"{out_stem}.csv"

    companies = self.sheet_manager.read_company_list()
    delivered_rows = [
        (c.name, c.frontend_app_url)
        for c in companies
        if c.frontend_app_url and c.frontend_app_url.strip()
    ]

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csvmod.writer(f)
        w.writerow(["企業名", "納品URL"])
        w.writerows(delivered_rows)

    logger.info(
        f"納品URL一覧を生成: {out_path} "
        f"({len(companies)}社中、納品URLあり {len(delivered_rows)}社を書き出し)"
    )
```

### 3. `web_app_operator.py` — ★ 回帰原因の本丸 ★

`feaceac` で `_wait_for_generation_complete` 内の UI 検出ロジックを以下のように変更している。
**この変更を恒久的に戻すには `_wait_for_generation_complete` を `f593390` 時点のものに差し替える** こと。

#### 3-a. `feaceac` で入った変更 (コミット済み差分の全文)

```diff
diff --git a/web_app_operator.py b/web_app_operator.py
index 14d34b4..c14c23e 100644
--- a/web_app_operator.py
+++ b/web_app_operator.py
@@ -2625,14 +2625,35 @@ class WebAppOperator:

         # 「生成中...」表示を判定するためのテキストパターン
         # Streamlit の st.info() / st.status() / 一般 div いずれにも対応
+        #
+        # 注意 (2026-05-20):
+        #   旧パターンに含めていた `⏳` 絵文字 / `スピナー` という単語は、
+        #   画面のヘルプテキスト・ステータス凡例等に静的に存在する場合があり、
+        #   生成完了後も永続マッチして 5分タイムアウトする不具合があった。
+        #   保守的に `_wait_for_in_progress_to_clear` と同じ語句のみに限定する。
         in_progress_pattern = re.compile(
             r"(FAQ.{0,3}生成中|企業情報.{0,3}生成中|生成中\.{2,}|"
-            r"生成しています|処理中\.{2,}|⏳|スピナー)"
+            r"生成しています|処理中\.{2,})"
         )
         # 完了シグナル (これが出たら即完了)
+        # 修正 (2026-05-20):
+        #   実際の画面に表示される成功メッセージを直接拾うように拡充。
+        #   - 「✅ コンテンツが正常に生成されました」
+        #   - 「✅ 生成されたFAQ」「N個のFAQを生成しました」
+        #   - 「✅ 生成された企業情報」「企業情報が正常に生成されました」
+        #   旧パターン (生成完了 / 保存可能 等) はこの画面には出ないため
+        #   完了検出が永続マッチに頼って 300秒タイムアウトしていた。
         completion_pattern = re.compile(
-            r"(生成完了|生成が完了|保存可能|保存準備|"
-            r"プレビューで確認|プレビュー・保存で確認)"
+            r"("
+            r"コンテンツが正常に生成されました|"
+            r"企業情報が正常に生成されました|"
+            r"FAQを生成しました|"           # 「31個のFAQを生成しました」等
+            r"生成されたFAQ|"
+            r"生成された企業情報|"
+            r"生成完了|生成が完了|"
+            r"保存可能|保存準備|"
+            r"プレビューで確認|プレビュー・保存で確認"
+            r")"
         )

         # フェーズ1: スピナー出現を待つ (出なくても最低待機後に消失検出へ移行)
@@ -2662,24 +2683,61 @@ class WebAppOperator:
             except Exception:
                 pass

-            # ----- 生成中テキスト可視性 (新規) -----
+            # ----- 生成中テキスト可視性 -----
+            #
+            # 修正 (2026-05-20):
+            #   main 要素全体 (inner_text) を見ると、生成完了後もページ内に
+            #   残った「FAQを生成中…」「企業情報を生成中…」等のステータス
+            #   履歴やタブ見出しに永続マッチして 300秒タイムアウトする不具合
+            #   があった。
+            #   → st.info / st.status / stAlert / stToast 等、
+            #     「現在進行中であることを示す UI 要素」だけを対象に判定する。
             in_progress_visible = False
-            page_text = ""
+            in_progress_match = None
+            page_text = ""  # 診断ログ用 (マッチ要素のテキストを格納)
+            status_selectors = (
+                "[data-testid='stAlert']",          # st.info / st.warning など
+                "[data-testid='stStatusWidget']",   # st.status の Running 状態
+                "[data-testid='stStatus']",          # 旧 API
+                "[data-testid='stToast']",           # st.toast
+                "[data-testid='stNotification']",
+            )
+            try:
+                status_locator = self.page.locator(", ".join(status_selectors))
+                status_count = await status_locator.count()
+                for i in range(status_count):
+                    el = status_locator.nth(i)
+                    try:
+                        if not await el.is_visible():
+                            continue
+                        text = await el.inner_text(timeout=1500)
+                    except Exception:
+                        continue
+                    if not text:
+                        continue
+                    m = in_progress_pattern.search(text)
+                    if m:
+                        in_progress_visible = True
+                        in_progress_match = m
+                        page_text = text
+                        break
+            except Exception:
+                pass
+
+            # 完了シグナル検出用には main 全体のテキストを別途取得する
+            # (こちらは「居残り」で困らないため main 全体で OK)
             try:
                 main = self.page.locator(
                     "main, [data-testid='stMain'], section[role='main']"
                 )
                 if await main.count() > 0:
-                    page_text = await main.first.inner_text(timeout=3000)
+                    full_main_text = await main.first.inner_text(timeout=3000)
                 else:
-                    page_text = await self.page.locator("body").inner_text(
+                    full_main_text = await self.page.locator("body").inner_text(
                         timeout=3000
                     )
             except Exception:
-                page_text = ""
-
-            if page_text and in_progress_pattern.search(page_text):
-                in_progress_visible = True
+                full_main_text = ""

             # ----- エラー検出 -----
             try:
@@ -2700,7 +2758,7 @@ class WebAppOperator:

             # ----- 完了正シグナル: 最低待機時間後なら即完了に切り上げ -----
             completion_signal = False
-            if page_text and completion_pattern.search(page_text):
+            if full_main_text and completion_pattern.search(full_main_text):
                 completion_signal = True

             # ----- 完了判定 -----
@@ -2722,10 +2780,22 @@ class WebAppOperator:

             # 進捗ログ (10秒ごと、過剰ログ抑制)
             if elapsed - last_in_progress_logged_at >= 10000:
+                match_info = ""
+                # spinner が消えたのに in_progress_text が居残っている場合は
+                # 何が拾われているか診断ログを出す (永続マッチ不具合の調査用)。
+                if not spinner_visible and in_progress_match:
+                    s_pos = max(0, in_progress_match.start() - 40)
+                    e_pos = min(len(page_text), in_progress_match.end() + 40)
+                    snippet = page_text[s_pos:e_pos].replace("\n", " ⏎ ")
+                    match_info = (
+                        f", matched='{in_progress_match.group(0)}'"
+                        f", context='…{snippet}…'"
+                    )
                 logger.info(
                     f"  生成中... (経過: {elapsed / 1000:.0f}秒, "
                     f"spinner={spinner_visible}, "
-                    f"in_progress_text={in_progress_visible})"
+                    f"in_progress_text={in_progress_visible}"
+                    f"{match_info})"
                 )
                 last_in_progress_logged_at = elapsed
```

#### 3-b. 未コミットで上に乗っていた変更 (working tree 差分の全文)

`feaceac` の路線を更に推し進め、次の3点を加えた中途版:

1. `GENERATION_COMPLETE_PATTERN` をモジュールレベル定数に昇格 (verify ループからも参照したいため)
2. **検証ループ (`_verify_generation_actually_occurred` 相当, 1376行付近)** にも同じ「アクティブステータス要素限定 + 完了シグナルあれば居残り無視」のオーバーライドを横展開
3. **`_wait_for_in_progress_to_clear` (1656行付近)** にも同様の限定+オーバーライドを横展開
4. `_wait_for_generation_complete` の `completion_pattern` をモジュール定数に置き換え

```diff
diff --git a/web_app_operator.py b/web_app_operator.py
index c14c23e..390473f 100644
--- a/web_app_operator.py
+++ b/web_app_operator.py
@@ -161,6 +161,23 @@ async def wait_for_server_recovery(
         await asyncio.sleep(min(poll_interval_seconds, max(remaining, 1)))


+# 生成完了の正シグナル (画面上の成功メッセージ)。
+# このいずれかが本文に出現していれば、たとえ st.status の label に
+# 「FAQを生成中」等の旧ラベルが残っていても「生成は完了している」と判定する。
+GENERATION_COMPLETE_PATTERN = re.compile(
+    r"("
+    r"コンテンツが正常に生成されました|"
+    r"企業情報が正常に生成されました|"
+    r"FAQを生成しました|"
+    r"生成されたFAQ|"
+    r"生成された企業情報|"
+    r"生成完了|生成が完了|"
+    r"保存可能|保存準備|"
+    r"プレビューで確認|プレビュー・保存で確認"
+    r")"
+)
+
+
 class WebAppOperator:
     """
     Webアプリ管理画面を自動操作するクラス。
@@ -1376,6 +1393,22 @@ class WebAppOperator:
         last_match_count = 0
         last_progress_log_ms = -15000

+        # 修正 (2026-05-20):
+        #   旧実装は main 全体の inner_text に対して `生成中` パターンを
+        #   照合していたが、画面に「FAQを生成中…」「企業情報を生成中…」の
+        #   ステータス履歴が残ったままだと永続マッチし、300秒の生成尾部待機
+        #   を必ず使い切って ContentSaveVerificationError(generation_stuck)
+        #   になっていた。
+        #   → 「現在進行中」を表すアクティブな UI 要素 (stAlert / stStatus /
+        #     stStatusWidget / stToast / stNotification) のテキストだけを
+        #     対象に判定する。FAQ パターン検証側は引き続き main 全体を使う。
+        status_selectors = (
+            "[data-testid='stAlert']",
+            "[data-testid='stStatusWidget']",
+            "[data-testid='stStatus']",
+            "[data-testid='stToast']",
+            "[data-testid='stNotification']",
+        )
         while True:
             try:
                 main = self.page.locator(
@@ -1388,10 +1421,36 @@ class WebAppOperator:
             except Exception:
                 page_text = ""

-            # まだ「生成中…」が画面に残っている → 生成継続。検証タイマーリセット
-            in_progress_still = bool(
-                page_text and in_progress_pattern.search(page_text)
-            )
+            # 「生成中」判定はアクティブなステータス要素のみを対象とする
+            in_progress_still = False
+            try:
+                status_locator = self.page.locator(", ".join(status_selectors))
+                status_count = await status_locator.count()
+                for i in range(status_count):
+                    el = status_locator.nth(i)
+                    try:
+                        if not await el.is_visible():
+                            continue
+                        text = await el.inner_text(timeout=1500)
+                    except Exception:
+                        continue
+                    if text and in_progress_pattern.search(text):
+                        in_progress_still = True
+                        break
+            except Exception:
+                pass
+
+            # オーバーライド (2026-05-20):
+            #   st.status(state="complete") はラベル文字列を維持するため、
+            #   widget 内に「FAQを生成中」が残るケースがある。
+            #   完了正シグナル (「✅ コンテンツが正常に生成されました」等) が
+            #   本文に出ていれば、in_progress 居残りを無視して検証に進む。
+            if in_progress_still and page_text and GENERATION_COMPLETE_PATTERN.search(page_text):
+                logger.info(
+                    "  [検証] 完了シグナルを検出 — in_progress 居残りを無視して検証に進みます"
+                )
+                in_progress_still = False
+
             if in_progress_still:
                 if gen_tail_elapsed_ms - last_progress_log_ms >= 15000:
                     logger.info(
@@ -1656,27 +1715,55 @@ class WebAppOperator:
         in_progress_pattern = re.compile(
             r"(FAQ.{0,3}生成中|企業情報.{0,3}生成中|生成中\.{2,}|生成しています)"
         )
+        # 修正 (2026-05-20): 永続マッチ防止のためアクティブなステータス要素のみ対象
+        status_selectors = (
+            "[data-testid='stAlert']",
+            "[data-testid='stStatusWidget']",
+            "[data-testid='stStatus']",
+            "[data-testid='stToast']",
+            "[data-testid='stNotification']",
+        )
         poll_interval_ms = 2500
         elapsed_ms = 0
         last_log_ms = -15000

         while elapsed_ms < max_wait_ms:
+            still_running = False
             try:
-                main = self.page.locator(
-                    "main, [data-testid='stMain'], section[role='main']"
-                )
-                if await main.count() > 0:
-                    page_text = await main.first.inner_text(timeout=3000)
-                else:
-                    page_text = await self.page.locator("body").inner_text(
-                        timeout=3000
-                    )
+                status_locator = self.page.locator(", ".join(status_selectors))
+                status_count = await status_locator.count()
+                for i in range(status_count):
+                    el = status_locator.nth(i)
+                    try:
+                        if not await el.is_visible():
+                            continue
+                        text = await el.inner_text(timeout=1500)
+                    except Exception:
+                        continue
+                    if text and in_progress_pattern.search(text):
+                        still_running = True
+                        break
             except Exception:
-                page_text = ""
+                pass
+
+            # オーバーライド: 完了正シグナルが本文にあれば居残りを無視
+            if still_running:
+                try:
+                    main = self.page.locator(
+                        "main, [data-testid='stMain'], section[role='main']"
+                    )
+                    if await main.count() > 0:
+                        main_text = await main.first.inner_text(timeout=2000)
+                    else:
+                        main_text = ""
+                except Exception:
+                    main_text = ""
+                if main_text and GENERATION_COMPLETE_PATTERN.search(main_text):
+                    logger.info(
+                        f"  [{source}] 完了シグナルを検出 — 居残りを無視して継続"
+                    )
+                    still_running = False

-            still_running = bool(
-                page_text and in_progress_pattern.search(page_text)
-            )
             if not still_running:
                 if elapsed_ms > 0:
                     logger.info(
@@ -2635,26 +2722,8 @@ class WebAppOperator:
             r"(FAQ.{0,3}生成中|企業情報.{0,3}生成中|生成中\.{2,}|"
             r"生成しています|処理中\.{2,})"
         )
-        # 完了シグナル (これが出たら即完了)
-        # 修正 (2026-05-20):
-        #   実際の画面に表示される成功メッセージを直接拾うように拡充。
-        #   - 「✅ コンテンツが正常に生成されました」
-        #   - 「✅ 生成されたFAQ」「N個のFAQを生成しました」
-        #   - 「✅ 生成された企業情報」「企業情報が正常に生成されました」
-        #   旧パターン (生成完了 / 保存可能 等) はこの画面には出ないため
-        #   完了検出が永続マッチに頼って 300秒タイムアウトしていた。
-        completion_pattern = re.compile(
-            r"("
-            r"コンテンツが正常に生成されました|"
-            r"企業情報が正常に生成されました|"
-            r"FAQを生成しました|"           # 「31個のFAQを生成しました」等
-            r"生成されたFAQ|"
-            r"生成された企業情報|"
-            r"生成完了|生成が完了|"
-            r"保存可能|保存準備|"
-            r"プレビューで確認|プレビュー・保存で確認"
-            r")"
-        )
+        # 完了シグナル (モジュール定数を再利用)
+        completion_pattern = GENERATION_COMPLETE_PATTERN

         # フェーズ1: スピナー出現を待つ (出なくても最低待機後に消失検出へ移行)
         spinner_seen = False
```

## 再挑戦するときの注意点 (デバッグ仮説)

`feaceac` 系の変更は「完了後も画面に残る `FAQを生成中…` 文字列に永続マッチして 300 秒タイムアウト」を直すためのもの。
方向性自体は妥当だが、Streamlit 側の DOM 構造を仮定して `data-testid` を絞り込みすぎ、**完了後の FAQ 実体が `main` に出る前に "in_progress なし＝完了" と判定 → FAQ 実体検証に進む → 検証側が 120秒 で空振り** という新しい失敗モードを生んだ可能性が高い。

再挑戦するなら以下のいずれかを試す:

1. `data-testid='stAlert'` 等のセレクタが実画面に本当に出ているかを Playwright Inspector / 録画で実機確認する
2. 「居残り対策」は完了シグナル検出 (`completion_pattern`) 強化だけに留め、`in_progress_pattern` の検出スコープは旧来の `main` 全体に戻す
3. 検出のスコープは旧来通り `main` だが、`in_progress_pattern` から「FAQを生成中」のような恒久ラベルを除外し、`.{2,}` を要求する語 (例: `生成中…`) に限る

## 参考: 同期間のファイル別変更サマリ

```
.claude/scheduled_tasks.lock        |    2 +-
data/company_list.csv               |   24 +-   ← 自動更新の運用結果
data/company_list_delivery_urls.csv |   63 --   ← 同上 (再生成される)
logs/automation.log                 | 1592 +    ← 実行ログ (再生成される)
models.py                           |    5 +    ← TRANSIENT_UI_ERROR_PATTERNS 追加
orchestrator.py                     |   25 +-   ← _write_delivery_urls_csv 改良
web_app_operator.py                 |   96 ++-  ← 回帰原因 (UI 進行検出)
```

> `data/*.csv` と `logs/automation.log` は実行のたびに上書きされるため、ロールバック対象外として無視する。
