"""
Google Colab ノートブック (colab/docdemo_automation.ipynb) の構造・内容を
検証するテスト。チームメンバーがColabにアクセスするだけで自動化が完走できる
ことを保証する。

実行: python -m pytest tests/test_colab_notebook.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = REPO_ROOT / "colab" / "docdemo_automation.ipynb"
BUILDER = REPO_ROOT / "colab" / "_build_notebook.py"


@pytest.fixture(scope="module", autouse=True)
def rebuild_notebook():
    """テスト前に必ずノートブックをビルドし直す。
    Windows cp932 環境でも絵文字を含む print が動くよう UTF-8 を強制。
    """
    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, str(BUILDER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert result.returncode == 0, f"ビルド失敗:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert NOTEBOOK.exists(), f"ノートブック未生成: {NOTEBOOK}"


@pytest.fixture(scope="module")
def nb():
    """ビルドされたノートブックの JSON を読み込む。"""
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def all_source(nb):
    """全セルの source を結合した文字列。検索しやすくするため。"""
    parts = []
    for c in nb["cells"]:
        s = c["source"]
        if isinstance(s, list):
            parts.append("".join(s))
        else:
            parts.append(s)
    return "\n".join(parts)


# ===== Test 1: ノートブック構造 =====

class TestNotebookStructure:
    """nbformat に従ったvalid な ipynb であることを確認。"""

    def test_is_valid_json(self):
        json.loads(NOTEBOOK.read_text(encoding="utf-8"))

    def test_has_nbformat_4(self, nb):
        assert nb["nbformat"] == 4

    def test_has_cells(self, nb):
        assert isinstance(nb["cells"], list)
        assert len(nb["cells"]) > 0

    def test_kernel_python3(self, nb):
        assert nb["metadata"]["kernelspec"]["name"] == "python3"

    def test_has_colab_metadata(self, nb):
        assert "colab" in nb["metadata"]


# ===== Test 2: 必須セルの存在 =====

class TestRequiredCells:
    """チームメンバーが上から実行するだけで完走するための必須セルが揃っているか。"""

    def test_drive_mount(self, all_source):
        assert "drive.mount" in all_source, "Google Drive マウントセルが必要"

    def test_secrets_loading(self, all_source):
        assert "userdata.get" in all_source, "Colab Secrets 取得が必要"
        assert "DOCDEMO_LOGIN_EMAIL" in all_source
        assert "DOCDEMO_LOGIN_PASSWORD" in all_source

    def test_git_clone(self, all_source):
        assert "git" in all_source.lower() and "clone" in all_source.lower(), \
            "GitHub からの clone セルが必要"

    def test_playwright_install(self, all_source):
        assert "playwright install" in all_source, "Playwright インストールが必要"

    def test_requirements_install(self, all_source):
        assert "pip install" in all_source and "requirements.txt" in all_source, \
            "依存関係インストールが必要"

    def test_orchestrator_invocation(self, all_source):
        assert "Orchestrator" in all_source, "Orchestrator の呼び出しが必要"
        assert "orchestrator.run()" in all_source, "orchestrator.run() の実行が必要"

    def test_nest_asyncio(self, all_source):
        assert "nest_asyncio" in all_source, \
            "Colab で playwright async を動かすには nest_asyncio が必要"

    def test_headless_true(self, all_source):
        assert "DOCDEMO_HEADLESS" in all_source, "ヘッドレス指定が必要 (Colab はGUI不可)"

    def test_csv_path_setup(self, all_source):
        assert "company_list.csv" in all_source, "CSV パスの設定が必要"

    def test_csv_cell_handles_missing_status_column(self, all_source):
        """既存CSVに「ステータス」列が無い古い形式でも KeyError で落ちないこと。
        df['ステータス'] を直接インデックスしてはいけない。
        """
        # df['ステータス'] のような直接アクセスがあれば、その近辺で
        # 'ステータス' in df.columns / get / try except のいずれかでガードされている必要がある
        # 簡易チェック: カラム存在判定のキーワードが必須
        guarded = (
            "in df.columns" in all_source
            or ".get('ステータス'" in all_source
            or 'LEGACY_COLUMN_ALIASES' in all_source
            or "read_company_list" in all_source
        )
        assert guarded, (
            "CSV読み込みセルは「ステータス」列の有無をガードして KeyError を防ぐ必要がある"
        )


# ===== Test 3: HOLD解消UI (ipywidgets) =====

class TestHoldResolverUI:
    """同名企業該当の社をColab内でボタン選択できる ipywidgets UI が含まれているか。
    tkinter は Colab で動かないため、ipywidgets ベースの代替UIが必須。
    """

    def test_ipywidgets_imported(self, all_source):
        assert "ipywidgets" in all_source, \
            "Colab で HOLD 解消UIを表示するには ipywidgets が必要"

    def test_hold_resolver_section_present(self, all_source):
        # マークダウンセルに「同名企業該当」「URL企業ID不一致」関連の見出しが必要
        assert ("同名企業該当" in all_source or "URL企業ID不一致" in all_source), \
            "HOLD 解消セクションの見出しが必要"

    def test_hold_resolver_uses_radio_or_button(self, all_source):
        # 候補から選択するためのUI要素 (Radio/Button) が使われているか
        assert (
            "widgets.RadioButtons" in all_source
            or "widgets.Button" in all_source
            or "widgets.Dropdown" in all_source
        ), "ipywidgets の選択UI (Radio/Button/Dropdown) が必要"

    def test_hold_resolver_saves_csv(self, all_source):
        # 選択結果をCSVに書き戻す処理がコード内にあるか
        # （csv.DictWriter or to_csv or save_company_list 等の書き込み呼び出し）
        assert (
            "DictWriter" in all_source
            or "to_csv" in all_source
            or "save_company_list" in all_source
        ), "選択結果をCSVに書き戻す処理が必要"


# ===== Test 4: 最新機能の反映 =====

class TestRecentFeatureCoverage:
    """最近実装された機能がノートブックの説明や運用フローに反映されているか。"""

    def test_url_company_id_mismatch_description(self, all_source):
        # 2026-05-12: 「同名企業」→「URL企業ID不一致」へ判定条件変更
        assert "URL" in all_source and "企業ID" in all_source, \
            "URL企業ID判定の説明が必要"

    def test_image_upload_ui_reflection(self, all_source):
        # 2026-05-12: 画像アップロード後のUI反映確認
        assert ("UI反映" in all_source or "背景画像" in all_source), \
            "Step 5 背景画像UI反映確認の言及が必要"

    def test_sidebar_expand_note(self, all_source):
        # 2026-05-12: サイドバー「システム設定」折りたたみ対応
        assert "システム設定" in all_source, \
            "システム設定ページに関する記述が必要"

    def test_frontend_url_fallback_note(self, all_source):
        # Step 6 フォールバック・推定URLの存在
        assert ("納品URL" in all_source or "フロントエンド" in all_source), \
            "Step 6 納品URL/フロントエンドアプリURL の言及が必要"


# ===== Test 5: トラブルシューティング =====

class TestTroubleshooting:
    """よくあるエラーへの対処がドキュメント化されているか。"""

    def test_troubleshooting_section(self, all_source):
        assert "トラブルシューティング" in all_source, "トラブルシューティング章が必要"

    def test_secret_error_mentioned(self, all_source):
        assert "Secret" in all_source, "Secrets 関連エラー対処が必要"

    def test_timeout_mentioned(self, all_source):
        assert ("タイムアウト" in all_source or "12h" in all_source or "90分" in all_source), \
            "Colab セッションタイムアウトに関する記述が必要"


# ===== Test 6: 実行不可な構文エラーがないか (静的チェック) =====

class TestCodeCellSyntax:
    """各 code セルが Python として構文的に正しいか軽く検証。
    （Colab固有の `!pip` や `await` はトップレベルで合法のため、
    まずトップレベル await のみ ast.parse ではなく compile で確認）"""

    def test_code_cells_compile(self, nb):
        import ast
        for i, c in enumerate(nb["cells"]):
            if c["cell_type"] != "code":
                continue
            src = c["source"]
            if isinstance(src, list):
                src = "".join(src)

            # `!pip ...` 行を除外 (Colab IPython 拡張構文)
            cleaned = "\n".join(
                line for line in src.split("\n")
                if not line.strip().startswith("!")
            )
            if not cleaned.strip():
                continue
            # トップレベル await を許容するため flags を使う
            try:
                compile(
                    cleaned,
                    f"<cell-{i}>",
                    "exec",
                    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                )
            except SyntaxError as e:
                pytest.fail(f"cell {i} 構文エラー: {e}\nsource:\n{src}")
