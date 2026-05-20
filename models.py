"""
DOCdemo 自動化フロー — データモデル

企業情報と処理ステータスを表すデータクラス。
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# =============================================================================
# 一時的失敗 (transient UI / timeout / network error) の分類とリトライ予算
#
# orchestrator.py が一般 Exception を捕捉した際、
#   ServerDownError      → サーバー復旧待ち + 同企業再試行 (既存)
#   一時的失敗            → mark_transient_error() でプレフィックス付与、
#                          次回 orchestrator 実行で自動リトライ (新規)
#   それ以外 (業務エラー)  → mark_error() で永続エラー、自動リトライしない (既存)
# =============================================================================

# 一時的失敗とみなすメッセージパターン (部分一致)。
# 生成プロセスが Streamlit UI の読み込み遅延・要素レンダ遅延で失敗した場合に
# 表面化する Playwright タイムアウト系がこの分類に入る。
TRANSIENT_UI_ERROR_PATTERNS = (
    # Playwright タイムアウト系
    "Timeout 10000ms exceeded",
    "Timeout 30000ms exceeded",
    "Timeout 60000ms exceeded",
    "Timeout exceeded",
    "wait_for_selector",
    "Page.wait_for_selector",
    "Locator.click: Timeout",
    "Locator.fill: Timeout",
    "Locator.wait_for: Timeout",
    # Streamlit UI 起因 (ログインフォーム / サイドバー)
    "stSidebar",
    "メールアドレス",
    # Browser / Page 状態異常 (大抵は再ログインで復旧)
    "Target closed",
    "Target page",
    "Page closed",
    "Browser closed",
    "Browser has been closed",
    "has been closed",
    # ネットワーク一時障害 (server-down 検出と一部重なるが、ここは UI 側からも判定)
    "net::ERR_",
    # コンテンツ生成本体のタイムアウト
    # _wait_for_generation_complete() / _wait_for_in_progress_to_clear() が
    # 投げる TimeoutError。サーバー混雑・URL 数過多で起きるため再試行で復旧する。
    "コンテンツ生成がタイムアウト",
    "生成中…」表示が",
    # FAQ 実体検証失敗 — 旧コードで preview タブ切替前に検証していた残骸。
    # 新コード (即 preview 切替 + textarea/get_by_text リトライ) で再試行すれば
    # 高確率で成功するため自動リトライに含める。
    "FAQ実体がページに現れません",
    "生成完了検出後、FAQ実体",
    # コンテンツ管理タブ検証失敗 — radio UI 未対応バグの残骸。
    # 新コード (radio button + get_by_text リトライ) で再試行可能。
    "対象企業の名前/IDがタブ本文に見つからない",
    "タブ/ラジオが見つからない",
    "FAQ/企業情報タブのコンテンツ検証に失敗",
    # サイドバーナビゲーション瞬時失敗 (ページ遷移直後のフラッシュ)
    "サイドバーに '",
)

# 自動リトライ上限 (この回数だけ連続失敗したら手動確認に回す)
TRANSIENT_RETRY_MAX = 3

# エラーメッセージ先頭に埋め込むリトライ回数プレフィックスの正規表現
_TRANSIENT_PREFIX_RE = re.compile(r"^\[一時的失敗 (\d+)/(\d+)\]\s*")


def is_transient_error_message(message) -> bool:
    """エラーメッセージが UI/タイムアウト/ネットワーク起因の一時的失敗パターンを含むか。

    `[一時的失敗 N/M]` プレフィックス付きメッセージは既に一時的失敗と確定しているので
    パターン照合せずとも True を返す (mark_transient_error → is_transient_error の冪等性確保)。
    """
    if not message:
        return False
    text = str(message)
    if _TRANSIENT_PREFIX_RE.match(text):
        return True
    return any(pat in text for pat in TRANSIENT_UI_ERROR_PATTERNS)


def parse_transient_retry_count(message) -> int:
    """エラーメッセージ先頭の `[一時的失敗 N/M]` プレフィックスから N を取り出す。

    プレフィックスが無ければ 0。これにより、既存の素のタイムアウト
    メッセージは「まだ 1 度も自動リトライしていない (count=0)」として扱われる。
    """
    if not message:
        return 0
    m = _TRANSIENT_PREFIX_RE.match(str(message))
    return int(m.group(1)) if m else 0


def format_transient_error(message, attempt: int, max_attempts: int = TRANSIENT_RETRY_MAX) -> str:
    """一時的失敗メッセージにリトライ回数プレフィックスを付ける。

    既にプレフィックスがあれば剥がしてから付け直す (重ね掛け防止)。
    """
    text = str(message) if message else ""
    cleaned = _TRANSIENT_PREFIX_RE.sub("", text).strip()
    return f"[一時的失敗 {attempt}/{max_attempts}] {cleaned}".rstrip()


class ProcessStatus(Enum):
    """企業ごとの処理進捗ステータス"""
    PENDING = "未処理"
    URL_FOUND = "URL特定済"
    DUPLICATE_DETECTED = "同名企業該当"  # URL内の企業IDが完全一致しない候補が複数、手動確認待ち（CSV互換のためラベル名は据置）
    COMPANY_ADDED = "企業追加済"
    CONTENT_GENERATED = "コンテンツ生成済"
    IMAGE_UPLOADED = "画像UP済"
    COMPLETED = "完了"
    ERROR = "エラー"
    SKIPPED = "スキップ"


@dataclass
class CompanyInfo:
    """1社分の企業情報を保持するデータクラス"""

    # 必須フィールド
    row_index: int                    # スプレッドシートの行番号 (0-indexed)
    name: str                         # 企業名（例: "one-hat株式会社"）

    # 自動生成・取得されるフィールド
    enterprise_id: str = ""           # 企業ID（例: "one-hat"）
    homepage_url: str = ""            # ホームページURL（採用済み）
    url_candidates: list = field(default_factory=list)   # 同名検出時の候補URL（手動選択用）
    screenshot_path: str = ""         # スクリーンショットファイルパス
    extracted_links: list = field(default_factory=list)  # 抽出リンク一覧
    frontend_app_url: str = ""        # フロントエンドアプリURL

    # ステータス管理
    status: ProcessStatus = ProcessStatus.PENDING
    error_message: str = ""           # エラー発生時の詳細メッセージ

    # Stage 4 (verify_quality.py) の結果
    quality_check: str = ""           # "OK" / "NG" / "部分OK" / "" (未チェック)
    quality_detail: str = ""          # 項目別の OK/NG 内訳 (例: "HTTP=OK / 企業名=OK / 背景画像=NG / FAQ=OK / AIチャット=OK")

    def __post_init__(self):
        """初期化後に企業IDを自動生成（未設定の場合）"""
        if not self.enterprise_id and self.name:
            self.enterprise_id = self.generate_enterprise_id(self.name)

    @staticmethod
    def generate_enterprise_id(company_name: str) -> str:
        """
        企業名から企業IDを自動生成する。

        ルール:
        1. 法人格サフィックス（株式会社、一般社団法人 等）を除去
        2. 英字の場合はそのまま小文字化
        3. 日本語の場合はそのままハイフン区切り
        4. 特殊文字を除去、スペースをハイフンに変換

        例:
        - "one-hat株式会社" → "one-hat"
        - "株式会社Felnis" → "felnis"
        - "伊勢住宅株式会社" → "伊勢住宅"
        - "医療法人社団日生会" → "日生会"
        """
        import re

        name = company_name.strip()

        # 法人格プレフィックスの除去（先頭にある場合）
        prefixes = [
            "一般財団法人", "公益財団法人", "一般社団法人", "公益社団法人",
            "医療法人社団", "医療法人", "社会福祉法人", "学校法人",
            "特定非営利活動法人", "NPO法人",
        ]
        for prefix in prefixes:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break

        # 法人格サフィックスの除去
        suffixes = [
            "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
            "一般財団法人", "公益財団法人", "一般社団法人", "公益社団法人",
            "医療法人社団", "医療法人", "税理士法人", "司法書士法人",
            "弁護士法人",
        ]
        for suffix in suffixes:
            name = name.replace(suffix, "")

        # 前後の空白・中黒等を除去
        name = name.strip().strip("・").strip()

        # 英字のみの場合は小文字化
        if re.match(r'^[a-zA-Z0-9\s\-_.]+$', name):
            name = name.lower().strip()
            # スペースをハイフンに変換
            name = re.sub(r'\s+', '-', name)
        else:
            # 日本語混在: スペースをハイフンに
            name = re.sub(r'\s+', '-', name)

        # 特殊文字の除去（英数字、日本語、ハイフン以外）
        name = re.sub(r'[^\w\-\u3000-\u9fff\uff00-\uffef]', '', name)

        # 連続ハイフンの正規化
        name = re.sub(r'-+', '-', name).strip('-')

        return name if name else "unknown"

    def is_processable(self) -> bool:
        """
        処理可能(未処理または途中)かどうかを判定。

        DUPLICATE_DETECTED は人間の手動介入待ちなので、ホームページURLが
        手動で確定された場合のみ処理対象とする。

        ERROR でも「一時的失敗」と分類されたものは自動リトライ対象に含める。
        (Playwright タイムアウト等の UI 起因失敗は再ログイン+再試行で
        高確率で復旧するため、手動介入なしでオーケーストレータが拾えるようにする)
        """
        if self.status == ProcessStatus.DUPLICATE_DETECTED:
            return bool(self.homepage_url)
        if self.status == ProcessStatus.ERROR:
            return self.transient_retry_remaining() > 0
        return self.status in (
            ProcessStatus.PENDING,
            ProcessStatus.URL_FOUND,
            ProcessStatus.COMPANY_ADDED,
            ProcessStatus.CONTENT_GENERATED,
            ProcessStatus.IMAGE_UPLOADED,
        )

    def is_transient_error(self) -> bool:
        """ERROR ステータス かつ メッセージが一時的失敗パターンに合致するか。"""
        return (
            self.status == ProcessStatus.ERROR
            and is_transient_error_message(self.error_message)
        )

    def transient_retry_remaining(self) -> int:
        """
        この企業に残っている自動リトライ回数を返す。
        - 一時的失敗でなければ 0 (永続エラー / 完了済 / 未処理 等)
        - 一時的失敗なら max - 累計失敗回数 を返す (最低 0)
        """
        if not self.is_transient_error():
            return 0
        count = parse_transient_retry_count(self.error_message)
        return max(0, TRANSIENT_RETRY_MAX - count)

    def mark_error(self, message: str):
        """エラー状態にする"""
        self.status = ProcessStatus.ERROR
        self.error_message = message

    def mark_transient_error(self, message: str):
        """
        一時的失敗としてマーク。`[一時的失敗 N/MAX]` プレフィックスを付け、
        N (累計失敗回数) を 1 増やす。既にプレフィックスがあれば置換。

        N が上限に達した時点で is_processable() は False を返し、
        以降の自動リトライは止まる (手動確認に回す)。
        """
        prev_count = parse_transient_retry_count(self.error_message)
        new_count = min(prev_count + 1, TRANSIENT_RETRY_MAX)
        self.status = ProcessStatus.ERROR
        self.error_message = format_transient_error(message, new_count)

    def reset_for_transient_retry(self):
        """
        一時的失敗の企業を再処理パスに乗せるため、ステータスを URL_FOUND に戻す。
        オーケストレータは ERROR ステータスのまま渡されると Step 2 をスキップするため、
        ループの先頭で本メソッドを呼んで通常の処理経路に合流させる。

        永続エラー / 完了 / スキップ 等はリセット対象外 (no-op)。
        累計失敗回数 (プレフィックスの N) は mark_transient_error 側で
        次回失敗時に再カウントされるので、ここで履歴を破棄しても整合は崩れない。
        """
        if not self.is_transient_error():
            return
        self.status = ProcessStatus.URL_FOUND
        self.error_message = ""

    def mark_skipped(self, reason: str):
        """スキップ状態にする"""
        self.status = ProcessStatus.SKIPPED
        self.error_message = reason

    def mark_duplicate(self, candidates: list, reason: str = ""):
        """
        URL内の企業IDが完全一致しない候補が複数あり、人間の手動確認待ちの状態にする。

        Args:
            candidates: 候補URLのリスト
            reason: 理由メッセージ
        """
        self.status = ProcessStatus.DUPLICATE_DETECTED
        self.url_candidates = list(candidates)
        self.error_message = reason or (
            f"URL内の企業IDが完全一致しない候補が {len(candidates)} 件検出されました。"
            "「ホームページURL」列に正しいURLを入力して再実行してください。"
        )

    def __str__(self) -> str:
        return (
            f"CompanyInfo(name={self.name!r}, id={self.enterprise_id!r}, "
            f"status={self.status.value}, url={self.homepage_url!r})"
        )
