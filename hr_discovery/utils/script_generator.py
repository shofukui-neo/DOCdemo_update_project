from __future__ import annotations


def generate_talk_script(company_name: str, source_label: str, person_name: str, title: str) -> str:
    source_key = (source_label or "").lower()

    if "pr times" in source_key or "pr_times" in source_key:
        return (
            f"{company_name}様の採用関連プレスリリースを拝見し、"
            f"{title}の{person_name}様宛にお役立ていただける情報がありご連絡しました。"
        )

    if "wantedly" in source_key:
        return (
            f"Wantedlyの記事で{person_name}様の発信を拝見し、"
            f"採用広報と母集団形成に関して{company_name}様へ有効なご提案がありお電話しました。"
        )

    if "hellowork" in source_key:
        return (
            f"ハローワークの求人情報を拝見し、{company_name}様の採用活動に合わせて、"
            f"{title}の{person_name}様向けに改善施策をご案内したくご連絡しました。"
        )

    if "linkedin" in source_key or "facebook" in source_key or "sns" in source_key:
        return (
            f"公開されているSNS情報で{company_name}様の採用体制を拝見し、"
            f"{person_name}様に関連性の高い採用改善施策をご共有したくご連絡しました。"
        )

    return (
        f"{company_name}様の採用情報を拝見し、{title}の{person_name}様に"
        f"お力添えできる内容がありご連絡しました。"
    )
