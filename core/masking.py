"""個人情報のマスキング（設計書 5.2）。

重要：この処理は完璧ではありません。100%見つけられる保証はないので、
これだけに頼らないでください。一番の防御は「そもそも見られる人を制限すること」
（ログイン制限）と「使う列だけを読み込むこと」で、マスキングはその上に重ねる
もう1つの安全策です。
"""

from __future__ import annotations

import re

# 電話番号（ハイフンあり/なし、市外局番の桁数ゆらぎに対応）
_PHONE = re.compile(r"(?<![0-9])(0\d{1,4}[-ー－]?\d{1,4}[-ー－]?\d{3,4})(?![0-9])")
# メールアドレス
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 郵便番号。うしろに「-4桁」が続く場合は電話番号なので、ここでは拾わない
# （例: 090-1234-5678 の「090-1234」を郵便番号と誤認しないため）
_POSTAL = re.compile(r"(?<![0-9])〒?\d{3}[-ー－]\d{4}(?![0-9\-ー－])")
# 住所（都道府県から番地まで）
_ADDRESS = re.compile(
    r"(北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|"
    r"神奈川県|新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|静岡県|愛知県|三重県|滋賀県|京都府|"
    r"大阪府|兵庫県|奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|徳島県|香川県|愛媛県|高知県|"
    r"福岡県|佐賀県|長崎県|熊本県|大分県|宮崎県|鹿児島県|沖縄県)"
    r"[^\s、。]{0,30}?[0-9０-９]{1,4}[-ー－丁目番地号][^\s、。]{0,15}"
)
# クレジットカード番号らしき並び
_CARD = re.compile(r"(?<![0-9])(?:\d{4}[ -]?){3}\d{3,4}(?![0-9])")

# 日本語の氏名。「様」（漢字）だけでなく「さま」（ひらがな）「サマ」も見る。
# 片方のパターンしかチェックしていないと見逃す（5.2 の注意点）。
#
# 名前部分には助詞（を・は・が など）を含めない。含めてしまうと
# 「使用状況を所定様式」の「所定様」までを名前と誤認する。
# 敬称のうしろに「式・子・相・々」などが続く場合は「様式」「様子」「様々」
# といった熟語なので、名前ではない。
# 名前部分には敬称そのもの（様・殿）を含めない。含めると
# 「田中様も同様」で「田中様も同」までを名前と読んでしまい、
# 本物の「田中様」を隠しそこねる。
_NAME_CHAR = r"(?:(?![様殿])[一-龥ぁ-んァ-ヶa-zA-Zａ-ｚＡ-Ｚ])"

# 敬称の直前は「漢字またはカタカナ」に限る。
# ひらがなまで許すと「たくさん」の“さん”、「みなさま”の“さま”を人名と誤認し、
# マニュアル本文が「[MASKED_NAME]さん摂る」のように壊れてしまう。
# 実際の氏名は漢字・カタカナ表記がほとんどなので、この制限で実害は小さい。
_NAME_TAIL = r"(?:(?![様殿])[一-龥ァ-ヶ])"

# 「殿（どの）」は敬称から外している。現代の業務文書ではほぼ使われない一方、
# 「どのくらい」「どの成分」といった普通の語を人名と誤認する害が大きいため。
# 「山田 太郎様」のように姓と名の間に空白が入る書き方にも対応する。
# 最後の1文字が漢字・カタカナであることは、うしろの (?<=…) で確かめる。
_NAME_HONORIFIC = re.compile(
    r"((?:(?![様殿])[一-龥ァ-ヶ])(?:[\s　]?" + _NAME_CHAR + r"){0,10}(?<=[一-龥ァ-ヶ]))"
    r"(様|さま|サマ|さん|殿)"
    r"(?![々式子相態様なーズ])"
)

# 「様」「殿」の直前に来ても人名ではない字（同様・一様・多様・仕様・御殿 など）
_NOT_NAME_ENDINGS = {"同", "一", "多", "模", "仕", "異", "態", "各", "王", "神", "宮", "御", "殿", "定",
                     "沢", "婆", "爺", "兄", "姉", "父", "母", "叔", "伯"}

# 順番が重要。桁数の多いものから先に消さないと、電話番号の一部が
# 郵便番号として誤って置き換えられてしまう。
# 「〜様」の形でも、個人名ではない一般的な呼び方。ここを隠してしまうと
# 「返送料はお客様負担」→「返送料は[MASKED_NAME]様負担」のようになり、
# マニュアルの文意が壊れてAIの回答精度が落ちる。
_GENERIC_NAMES = {
    "お客", "客", "御客", "皆", "みな", "各位", "諸", "関係者",
    "担当", "担当者", "ご担当", "ご担当者", "責任者", "代表者", "管理者",
    "保護者", "患者", "利用者", "会員", "読者", "受取人", "ご本人", "本人",
}


def _is_generic(name: str) -> bool:
    name = name.strip().replace("　", "")
    if not name:
        return True
    if name[-1] in _NOT_NAME_ENDINGS:
        return True
    return name in _GENERIC_NAMES or any(name.endswith(g) for g in _GENERIC_NAMES)


def _mask_name(match: re.Match[str]) -> str:
    name, honorific = match.group(1), match.group(2)
    if _is_generic(name):
        return match.group(0)
    # 敬称は残して、名前部分だけ置き換える
    return f"[MASKED_NAME]{honorific}"


_RULES: list[tuple[re.Pattern[str], str]] = [
    (_EMAIL, "[MASKED_EMAIL]"),
    (_CARD, "[MASKED_CARD]"),
    (_PHONE, "[MASKED_PHONE]"),
    (_POSTAL, "[MASKED_POSTAL]"),
    (_ADDRESS, "[MASKED_ADDRESS]"),
]


def mask_text(text: str | None) -> str:
    """自由記述の文章から、名前・電話番号・メール・住所などを隠す。"""
    if not text:
        return ""
    masked = str(text)
    for pattern, replacement in _RULES:
        masked = pattern.sub(replacement, masked)
    # 敬称は残して名前部分だけ置き換える（「[MASKED_NAME]様」の形にする）
    masked = _NAME_HONORIFIC.sub(_mask_name, masked)
    return masked


def mask_record(record: dict, keys: list[str] | None = None) -> dict:
    """辞書の値をまとめてマスキングする。"""
    out = dict(record)
    for key, value in record.items():
        if keys is not None and key not in keys:
            continue
        if isinstance(value, str):
            out[key] = mask_text(value)
    return out


def find_leaks(text: str | None) -> list[str]:
    """マスキング後にも残っていそうなパターンを報告する（管理画面の点検用）。"""
    if not text:
        return []
    hits: list[str] = []
    if _EMAIL.search(text):
        hits.append("メールアドレス")
    if _PHONE.search(text):
        hits.append("電話番号")
    if any(not _is_generic(m.group(1)) for m in _NAME_HONORIFIC.finditer(text)):
        hits.append("氏名らしき表記")
    if _ADDRESS.search(text):
        hits.append("住所")
    return hits
