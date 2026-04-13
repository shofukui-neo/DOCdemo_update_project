import csv
import os

csv_path = 'data/company_list.csv'
checklist_path = 'verification_checklist.md'

if not os.path.exists(csv_path):
    print(f"Error: {csv_path} not found.")
    exit(1)

# Shift-JIS or UTF-8? Let's try to detect or use utf-8 with fallback
rows = []
try:
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
except UnicodeDecodeError:
    with open(csv_path, 'r', encoding='cp932') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

with open(checklist_path, 'w', encoding='utf-8') as f:
    f.write("# 納品物品質検証チェックリスト\n\n")
    f.write("各URLにアクセスし、背景画像、企業名の一致、およびFAQが正しく設定されているかを網羅的に確認します。\n\n")
    f.write("| 企業名 | 納品URL | 背景画像 | 企業名一致 | FAQ | 備考 |\n")
    f.write("| :--- | :--- | :---: | :---: | :---: | :--- |\n")
    
    for row in rows:
        name = row.get('企業名', '').strip()
        url = row.get('フロントエンドURL', '').strip()
        # URLが空でも未処理でも (生成中) と表示
        display_url = url if url and url.startswith('http') else "(生成中)"
        if not name:
             continue
        f.write(f"| {name} | {display_url} | [ ] | [ ] | [ ] | |\n")

print(f"Checklist regenerated: {checklist_path}")
