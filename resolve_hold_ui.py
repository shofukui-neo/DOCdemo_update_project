"""
HOLD（URL企業ID不一致候補複数）状態の企業に対し、候補URLから正解をボタンで
選択する対話GUI（tkinter製、追加依存なし）。

使い方:
    python resolve_hold_ui.py                              # 全HOLD企業をまとめて処理
    python resolve_hold_ui.py --csv data/foo.csv           # CSV指定
    python resolve_hold_ui.py --company-id msksoft         # 1社のみ (orchestrator から呼出時)

動作:
    対象CSVから「同名企業該当」かつ「ホームページURL」が空の企業を抽出し、
    1社ずつ候補URLをボタン表示。クリックで採用、「保存して次へ」で CSV に
    書き戻す。保存先は元のCSVを直接上書き。

    候補URLは サイドカーJSON (data/url_candidates/<企業ID>.json) から
    読み込む (旧CSV「URL候補」列にも後方互換でフォールバック)。

採用後の企業は homepage_url が埋まり、ステータスは URL_FOUND になる。
orchestrator から呼出時は同関数内で続行されるため、再実行は不要。
"""

import argparse
import csv
import sys
import webbrowser
from pathlib import Path
from typing import List, Dict, Optional

import tkinter as tk
from tkinter import ttk, messagebox

from config import COMPANY_LIST_CSV, CSV_COLUMNS
from models import ProcessStatus
from spreadsheet_manager import read_url_candidates


def parse_args():
    p = argparse.ArgumentParser(
        description="HOLD企業のURLを候補から対話的に選択する"
    )
    p.add_argument(
        "--csv",
        type=str,
        default=str(COMPANY_LIST_CSV),
        help="対象CSVファイルパス",
    )
    p.add_argument(
        "--company-id",
        type=str,
        default=None,
        help="単一企業のみを対象にする場合の企業ID (orchestrator からの自動呼出で使用)",
    )
    return p.parse_args()


def load_rows(csv_path: Path) -> List[Dict[str, str]]:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_rows(csv_path: Path, rows: List[Dict[str, str]]):
    fieldnames = list(rows[0].keys()) if rows else list(CSV_COLUMNS.values())
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _get_candidates(row: Dict[str, str]) -> List[str]:
    """
    行からURL候補リストを取得する。
    優先順位: サイドカーJSON > 旧CSV「URL候補」列 (パイプ区切り)
    """
    col = CSV_COLUMNS
    enterprise_id = (row.get(col.get("enterprise_id", "企業ID")) or "").strip()
    if enterprise_id:
        cands = read_url_candidates(enterprise_id)
        if cands:
            return cands
    legacy = (row.get("URL候補") or "").strip()
    if legacy:
        return [c.strip() for c in legacy.split("|") if c.strip()]
    return []


class HoldResolverApp:
    """tkinter ベースの HOLD 解消UI。"""

    def __init__(
        self,
        root: tk.Tk,
        csv_path: Path,
        company_id_filter: Optional[str] = None,
    ):
        self.root = root
        self.csv_path = csv_path
        self.col = CSV_COLUMNS
        self.company_id_filter = company_id_filter

        self.all_rows = load_rows(csv_path)
        self.hold_indices = self._find_hold_indices()
        self.current_pos = 0  # hold_indices内の位置
        self.resolved_count = 0
        self.skipped_count = 0

        title = "HOLD企業のURL選定"
        if company_id_filter:
            title += f" — {company_id_filter}"
        self.root.title(title)
        self.root.geometry("780x620")
        # ポップアップとして前面に持ってくる
        self.root.attributes("-topmost", True)
        self.root.after(500, lambda: self.root.attributes("-topmost", False))
        self._build_ui()

        if not self.hold_indices:
            self._show_empty()
            return

        self._show_current()

    def _find_hold_indices(self) -> List[int]:
        """HOLD状態 (同名企業該当 かつ homepage_url 未設定) の行インデックスを返す"""
        col = self.col
        indices = []
        for i, r in enumerate(self.all_rows):
            if r.get(col["status"], "") != ProcessStatus.DUPLICATE_DETECTED.value:
                continue
            if (r.get(col["homepage_url"], "") or "").strip():
                continue
            # 企業IDフィルタ
            if self.company_id_filter:
                if (r.get(col.get("enterprise_id", "企業ID"), "") or "").strip() \
                        != self.company_id_filter:
                    continue
            indices.append(i)
        return indices

    # ===== UI構築 =====
    def _build_ui(self):
        self.header_frame = ttk.Frame(self.root, padding=12)
        self.header_frame.pack(fill="x")

        self.title_label = ttk.Label(
            self.header_frame, text="", font=("", 14, "bold")
        )
        self.title_label.pack(anchor="w")

        self.progress_label = ttk.Label(self.header_frame, text="")
        self.progress_label.pack(anchor="w", pady=(2, 0))

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=8)

        # 候補ボタン領域（スクロール可）
        self.buttons_frame = ttk.Frame(self.root, padding=12)
        self.buttons_frame.pack(fill="both", expand=True)

        # カスタムURL入力
        self.custom_frame = ttk.Frame(self.root, padding=(12, 4, 12, 8))
        self.custom_frame.pack(fill="x")
        ttk.Label(self.custom_frame, text="カスタムURL:").pack(side="left")
        self.custom_entry = ttk.Entry(self.custom_frame)
        self.custom_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.custom_btn = ttk.Button(
            self.custom_frame, text="このURLを採用", command=self._on_custom
        )
        self.custom_btn.pack(side="left")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=8)

        # 下部コントロール
        self.footer_frame = ttk.Frame(self.root, padding=12)
        self.footer_frame.pack(fill="x")

        self.skip_btn = ttk.Button(
            self.footer_frame, text="スキップ →", command=self._on_skip
        )
        self.skip_btn.pack(side="left")

        self.quit_btn = ttk.Button(
            self.footer_frame, text="終了して保存", command=self._on_quit
        )
        self.quit_btn.pack(side="right")

        self.status_label = ttk.Label(self.footer_frame, text="")
        self.status_label.pack(side="left", padx=(16, 0))

    def _clear_buttons(self):
        for w in self.buttons_frame.winfo_children():
            w.destroy()

    def _show_current(self):
        self._clear_buttons()
        row_idx = self.hold_indices[self.current_pos]
        row = self.all_rows[row_idx]
        name = row[self.col["company_name"]]
        candidates = _get_candidates(row)

        self.title_label.config(text=name)
        self.progress_label.config(
            text=f"[{self.current_pos + 1} / {len(self.hold_indices)}] 候補ドメイン: {len(candidates)}件"
        )
        self.status_label.config(
            text=f"採用済: {self.resolved_count} / スキップ: {self.skipped_count}"
        )

        if not candidates:
            ttk.Label(
                self.buttons_frame,
                text="(候補URLが見つかりません。カスタムURL入力で採用してください)",
            ).pack(pady=10)

        # 候補ごとに行を作成: [採用]ボタン + URLラベル + [プレビュー]ボタン
        for cand in candidates:
            row_frame = ttk.Frame(self.buttons_frame)
            row_frame.pack(fill="x", pady=2)

            adopt = ttk.Button(
                row_frame,
                text="このURLを採用",
                width=16,
                command=lambda u=cand: self._on_adopt(u),
            )
            adopt.pack(side="left")

            preview = ttk.Button(
                row_frame,
                text="ブラウザで開く",
                width=14,
                command=lambda u=cand: webbrowser.open(u),
            )
            preview.pack(side="left", padx=(6, 8))

            url_label = ttk.Label(row_frame, text=cand, anchor="w")
            url_label.pack(side="left", fill="x", expand=True)

        self.custom_entry.delete(0, tk.END)

    def _show_empty(self):
        self._clear_buttons()
        if self.company_id_filter:
            msg = f"{self.company_id_filter} は HOLD 状態ではありません"
        else:
            msg = "HOLD中の企業はありません"
        self.title_label.config(text=msg)
        self.progress_label.config(text="")
        ttk.Label(
            self.buttons_frame,
            text="✓ すべての企業が処理済または未HOLD状態です。",
        ).pack(pady=20)
        self.custom_entry.config(state="disabled")
        self.custom_btn.config(state="disabled")
        self.skip_btn.config(state="disabled")

    # ===== ハンドラ =====
    def _adopt_url(self, url: str):
        row_idx = self.hold_indices[self.current_pos]
        self.all_rows[row_idx][self.col["homepage_url"]] = url
        # エラー詳細はクリア（採用済なので）
        if self.col.get("error_message") in self.all_rows[row_idx]:
            self.all_rows[row_idx][self.col["error_message"]] = ""
        self.resolved_count += 1
        self._save()
        self._advance()

    def _on_adopt(self, url: str):
        self._adopt_url(url)

    def _on_custom(self):
        url = self.custom_entry.get().strip()
        if not url:
            messagebox.showwarning("入力エラー", "URLを入力してください。")
            return
        if not url.startswith(("http://", "https://")):
            if not messagebox.askyesno(
                "確認", f"`{url}` は http(s):// で始まっていません。このまま採用しますか？"
            ):
                return
        self._adopt_url(url)

    def _on_skip(self):
        self.skipped_count += 1
        self._advance()

    def _on_quit(self):
        self._save()
        # 単一企業モードではダイアログを出さず即座に閉じる (orchestrator 続行のため)
        if not self.company_id_filter:
            messagebox.showinfo(
                "保存完了",
                f"採用済: {self.resolved_count}社\n"
                f"スキップ: {self.skipped_count}社\n\n"
                f"CSV: {self.csv_path}\n\n"
                "URLを採用した企業は orchestrator 再実行で Step 2 以降が処理されます。",
            )
        self.root.destroy()

    def _advance(self):
        self.current_pos += 1
        if self.current_pos >= len(self.hold_indices):
            self._on_quit()
            return
        self._show_current()

    def _save(self):
        save_rows(self.csv_path, self.all_rows)


def run_ui(csv_path: Path, company_id_filter: Optional[str] = None):
    """
    HOLD UIを起動する (subprocess 経由でも、直接呼出でも使える共通エントリ)。
    """
    if not csv_path.exists():
        print(f"CSVファイルが見つかりません: {csv_path}", file=sys.stderr)
        return 1

    root = tk.Tk()
    HoldResolverApp(root, csv_path, company_id_filter=company_id_filter)
    root.mainloop()
    return 0


def main():
    args = parse_args()
    csv_path = Path(args.csv)
    sys.exit(run_ui(csv_path, company_id_filter=args.company_id))


if __name__ == "__main__":
    main()
