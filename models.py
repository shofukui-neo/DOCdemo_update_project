"""
DOCdemo 自動化フロー — データモデル

企業情報と処理ステータスを表すデータクラス。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ProcessStatus(Enum):
    """企業ごとの処理進捗ステータス"""
    PENDING = "未処理"
    URL_FOUND = "URL特定済"
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
    homepage_url: str = ""            # ホームページURL
    screenshot_path: str = ""         # スクリーンショットファイルパス
    extracted_links: list = field(default_factory=list)  # 抽出リンク一覧
    frontend_app_url: str = ""        # フロントエンドアプリURL

    # ステータス管理
    status: ProcessStatus = ProcessStatus.PENDING
    error_message: str = ""           # エラー発生時の詳細メッセージ

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
        """処理可能(未処理または途中)かどうかを判定"""
        return self.status in (
            ProcessStatus.PENDING,
            ProcessStatus.URL_FOUND,
            ProcessStatus.COMPANY_ADDED,
            ProcessStatus.CONTENT_GENERATED,
            ProcessStatus.IMAGE_UPLOADED,
        )

    def mark_error(self, message: str):
        """エラー状態にする"""
        self.status = ProcessStatus.ERROR
        self.error_message = message

    def mark_skipped(self, reason: str):
        """スキップ状態にする"""
        self.status = ProcessStatus.SKIPPED
        self.error_message = reason

    def __str__(self) -> str:
        return (
            f"CompanyInfo(name={self.name!r}, id={self.enterprise_id!r}, "
            f"status={self.status.value}, url={self.homepage_url!r})"
        )
