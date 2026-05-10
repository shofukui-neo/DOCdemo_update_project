"""
納品リスト生成スクリプト

data/company_list.csv から「企業名」と「納品URL」だけを抜き出し、
シンプルなCSV（data/delivery_urls.csv）を生成する。
クライアント納品用・共有用。
"""

import csv
from pathlib import Path

SRC_PATH = Path("data/company_list.csv")
DST_PATH = Path("data/delivery_urls.csv")

if not SRC_PATH.exists():
    print(f"Error: {SRC_PATH} not found.")
    raise SystemExit(1)

# 旧/新カラム名どちらにも対応
URL_COLUMN_CANDIDATES = ["納品URL", "フロントエンドURL"]

with open(SRC_PATH, "r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# 納品URL列を特定
url_column = None
for col in URL_COLUMN_CANDIDATES:
    if rows and col in rows[0]:
        url_column = col
        break

if not url_column:
    print(f"Error: 納品URL列が見つかりません (探した列: {URL_COLUMN_CANDIDATES})")
    raise SystemExit(1)

count = 0
with open(DST_PATH, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["企業名", "納品URL"])
    for row in rows:
        name = row.get("企業名", "").strip()
        url = row.get(url_column, "").strip()
        if not name:
            continue
        writer.writerow([name, url])
        count += 1

print(f"納品リスト生成完了: {DST_PATH}")
print(f"  企業数: {count}社")
print(f"  納品URLあり: {sum(1 for r in rows if r.get(url_column, '').strip())}社")
