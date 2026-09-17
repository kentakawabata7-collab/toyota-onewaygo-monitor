import os
import re
import json
import hashlib
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


# ============================================================
# 基本設定
# ============================================================

URL = (
    "https://cp.toyota.jp/rentacar/"
    "?padid=ag270_fr_sptop_onewayma"
)

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"

STATE_VERSION = 11

NTFY_MAX_BYTES = 3500
DIFF_MAX_BYTES = 2400


# ============================================================
# 関東の都県
# ============================================================

KANTO_PREFECTURES = {
    "東京都",
    "神奈川県",
    "千葉県",
    "埼玉県",
    "茨城県",
    "栃木県",
    "群馬県",
}


# ============================================================
# 正規表現
# ============================================================

PHONE_PATTERN = re.compile(
    r"\d{2,4}-\d{2,4}-\d{3,4}"
)


# ============================================================
# 文字列正規化
# ============================================================

def normalize_line(line: str) -> str:

    line = line.replace("\r", "")
    line = line.replace("\u3000", " ")

    line = re.sub(
        r"[ \t]+",
        " ",
        line,
    )

    return line.strip()


def normalize_lines(text: str) -> list[str]:

    lines = []

    for line in text.splitlines():

        line = normalize_line(line)

        if line:
            lines.append(line)

    return lines


def canonicalize_labels(text: str) -> str:

    replacements = {
        "出発店舗：": "出発店舗=",
        "出発店舗:": "出発店舗=",

        "返却店舗：": "返却店舗=",
        "返却店舗:": "返却店舗=",

        "車種：": "車種=",
        "車種:": "車種=",

        "車両条件：": "車両条件=",
        "車両条件:": "車両条件=",

        "出発期間：": "出発期間=",
        "出発期間:": "出発期間=",

        "予約電話番号：": "予約電話番号=",
        "予約電話番号:": "予約電話番号=",
    }

    for old, new in replacements.items():
        text = text.replace(
            old,
            new,
        )

    return text


# ============================================================
# フィルタークリック
# ============================================================

def click_filter(
    page,
    label: str,
) -> None:

    print(
        f"「{label}」を選択しています..."
    )

    locator = page.get_by_text(
        label,
        exact=True,
    ).first

    count = page.get_by_text(
        label,
        exact=True,
    ).count()

    print(
        f"「{label}」候補数: {count}"
    )

    if count == 0:

        raise RuntimeError(
            f"「{label}」が見つかりません。"
        )

    locator.evaluate(
        "(element) => element.click()"
    )

    page.wait_for_timeout(
        1500
    )

    print(
        f"「{label}」を選択しました"
    )


# ============================================================
# 関東出発か判定
# ============================================================

def is_kanto_departure(
    record: str,
) -> bool:

    for line in record.splitlines():

        if line.startswith(
            "出発店舗="
        ):

            value = line.split(
                "=",
                1,
            )[1]

            for prefecture in KANTO_PREFECTURES:

                if prefecture in value:

                    return True

            return False

    return False


# ============================================================
# 車種文字列正規化
# ============================================================

def canonical_vehicle_text(
    text: str,
) -> str:

    text = normalize_line(
        text
    )

    text = text.replace(
        "ＨＶ",
        "HV",
    )

    text = text.replace(
        "ＡＴ",
        "AT",
    )

    text = text.replace(
        "２ＷＤ",
        "2WD",
    )

    return text


# ============================================================
# 車両一覧解析
#
# Toyotaページでは現在、
#
# 1台につき
#
#   出発店舗
#   返却店舗
#   車種
#   車両条件
#   出発期間
#   予約店舗名
#   電話番号
#
# の7行構造。
#
# ただし、ページ構造変更で行数が増減する可能性があるため、
# 「電話番号」を車両レコードの終端として解析する。
# ============================================================

def extract_vehicle_records(
    body_text: str,
) -> list[str]:

    print(
        "表示されている車両一覧を解析しています..."
    )

    # --------------------------------------------------------
    # デバッグファイル保存
    # --------------------------------------------------------

    debug_file = (
        BASE_DIR
        / "logs"
        / "debug_after_filters.txt"
    )

    debug_file.parent.mkdir(
        exist_ok=True
    )

    debug_file.write_text(
        body_text,
        encoding="utf-8",
    )

    print(
        "診断用ページ本文を保存しました: "
        f"{debug_file}"
    )

    # --------------------------------------------------------
    # 正規化
    # --------------------------------------------------------

    text = canonicalize_labels(
        body_text
    )

    lines = normalize_lines(
        text
    )

    # --------------------------------------------------------
    # ヘッダー検索
    # --------------------------------------------------------

    headers = [
        "出発店舗",
        "返却店舗",
        "車種",
        "車両条件",
        "出発期間",
        "予約電話番号",
    ]

    header_index = -1

    for i in range(
        0,
        len(lines) - len(headers) + 1,
    ):

        candidate = lines[
            i:i + len(headers)
        ]

        if candidate == headers:

            header_index = i

            break

    if header_index < 0:

        raise RuntimeError(
            "車両一覧ヘッダーが見つかりません。"
        )

    print(
        "検出した車両一覧ヘッダー位置: "
        f"{header_index}"
    )

    # --------------------------------------------------------
    # 車両データ開始
    # --------------------------------------------------------

    data_lines = lines[
        header_index + len(headers):
    ]

    # --------------------------------------------------------
    # フッター除外
    # --------------------------------------------------------

    footer_markers = [
        "片道GO!返却可能店舗一覧",
        "北海道地区",
        "東北地区",
        "関東地区",
        "中部地区",
        "近畿地区",
        "九州地区",
        "サイトマップ",
        "サイト利用にあたって",
        "当サイトでの個人情報の取扱いについて",
        "©TOYOTA MOTOR CORPORATION",
    ]

    footer_index = None

    for i, line in enumerate(
        data_lines
    ):

        for marker in footer_markers:

            if marker in line:

                footer_index = i

                break

        if footer_index is not None:
            break

    if footer_index is not None:

        data_lines = data_lines[
            :footer_index
        ]

    print(
        "車両データ領域: "
        f"{len(data_lines)} 行"
    )

    # --------------------------------------------------------
    # 車両レコード解析
    #
    # 「予約店舗名」＋「電話番号」の2行を検出して
    # そこまでを1台とする。
    # --------------------------------------------------------

    records = []

    current = []

    for line in data_lines:

        current.append(line)

        # ----------------------------------------------------
        # 電話番号が出現したら1台分終了
        # ----------------------------------------------------

        if PHONE_PATTERN.search(line):

            if len(current) < 7:

                raise RuntimeError(
                    "車両レコードが短すぎます。"
                    "\n"
                    f"レコード行数={len(current)}"
                    "\n"
                    + "\n".join(current)
                )

            # ------------------------------------------------
            # 最低限の7項目を確認
            # ------------------------------------------------

            departure = current[0]
            return_store = current[1]
            vehicle = current[2]
            condition = current[3]
            period = current[4]

            # 電話番号の直前が予約店舗名
            reservation_store = current[-2]

            phone_line = current[-1]

            phone_match = PHONE_PATTERN.search(
                phone_line
            )

            if not phone_match:

                raise RuntimeError(
                    "電話番号の解析に失敗しました。"
                    "\n"
                    f"{phone_line}"
                )

            phone_number = (
                phone_match.group(0)
            )

            # ------------------------------------------------
            # 余分な行がないか確認
            #
            # 現在のToyotaページでは7行が基本。
            # ただし将来の変更に備え、
            # 7行以上なら警告だけ出す。
            # ------------------------------------------------

            if len(current) != 7:

                print(
                    "警告: 車両レコードが"
                    f"{len(current)}行あります。"
                )

                print(
                    "\n".join(current)
                )

            # ------------------------------------------------
            # canonical record
            # ------------------------------------------------

            record = "\n".join(
                [
                    f"出発店舗={departure}",
                    f"返却店舗={return_store}",
                    f"車種={canonical_vehicle_text(vehicle)}",
                    f"車両条件={normalize_line(condition)}",
                    f"出発期間={normalize_line(period)}",
                    (
                        "予約電話番号="
                        f"{reservation_store}"
                        f" / "
                        f"{phone_number}"
                    ),
                ]
            )

            records.append(
                record
            )

            current = []

    # --------------------------------------------------------
    # 電話番号で終わらなかったデータ
    # --------------------------------------------------------

    if current:

        print(
            "警告: 車両レコードとして完結しなかった"
            f"データが{len(current)}行あります。"
        )

        print(
            "\n".join(current)
        )

        raise RuntimeError(
            "車両データの末尾に"
            "未解析データがあります。"
        )

    print(
        "解析できた車両数: "
        f"{len(records)}"
    )

    # --------------------------------------------------------
    # 関東だけ抽出
    # --------------------------------------------------------

    kanto_records = []

    for record in records:

        if is_kanto_departure(
            record
        ):

            kanto_records.append(
                record
            )

    print(
        "関東対象車両数: "
        f"{len(kanto_records)}"
    )

    print(
        "ページ全体の解析済み車両数: "
        f"{len(records)}"
    )

    return kanto_records


# ============================================================
# Toyotaページ取得
# ============================================================

def get_kanto_departures() -> str:

    print(
        "Toyotaページを開いています..."
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        try:

            page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            print(
                "ページを読み込みました"
            )

            # ------------------------------------------------
            # 出発
            # ------------------------------------------------

            click_filter(
                page,
                "出発",
            )

            # ------------------------------------------------
            # 関東
            # ------------------------------------------------

            click_filter(
                page,
                "関東",
            )

            # ------------------------------------------------
            # 表示更新待ち
            # ------------------------------------------------

            page.wait_for_timeout(
                2000
            )

            body_text = page.locator(
                "body"
            ).inner_text()

            records = extract_vehicle_records(
                body_text
            )

            result = "\n\n".join(
                records
            )

            print(
                "「関東 → 出発」の車両情報を取得しました"
            )

            return result

        finally:

            browser.close()


# ============================================================
# 車両レコード分割
# ============================================================

def split_vehicle_records(
    text: str,
) -> list[str]:

    if not text.strip():

        return []

    return [
        record.strip()
        for record in text.split(
            "\n\n"
        )
        if record.strip()
    ]


# ============================================================
# 車両識別キー
# ============================================================

def vehicle_key(
    record: str,
) -> str:

    values = {}

    for line in record.splitlines():

        if "=" in line:

            key, value = line.split(
                "=",
                1,
            )

            values[key] = value.strip()

    return "|".join(
        [
            values.get(
                "出発店舗",
                "",
            ),
            values.get(
                "返却店舗",
                "",
            ),
            values.get(
                "車種",
                "",
            ),
            values.get(
                "予約電話番号",
                "",
            ),
        ]
    )


# ============================================================
# 車両マップ
# ============================================================

def build_vehicle_map(
    text: str,
):

    result = {}

    for record in split_vehicle_records(
        text
    ):

        key = vehicle_key(
            record
        )

        result[key] = record

    return result


# ============================================================
# 変更内容
# ============================================================

def format_changed_vehicle(
    old_record: str,
    new_record: str,
) -> str:

    old_values = {}
    new_values = {}

    for line in old_record.splitlines():

        if "=" in line:

            key, value = line.split(
                "=",
                1,
            )

            old_values[key] = value

    for line in new_record.splitlines():

        if "=" in line:

            key, value = line.split(
                "=",
                1,
            )

            new_values[key] = value

    labels = [
        "出発店舗",
        "返却店舗",
        "車種",
        "車両条件",
        "出発期間",
        "予約電話番号",
    ]

    changed_lines = []

    for label in labels:

        old_value = old_values.get(
            label,
            "",
        )

        new_value = new_values.get(
            label,
            "",
        )

        if old_value != new_value:

            changed_lines.append(
                f"{label}:"
                f" {old_value}"
                f" → "
                f"{new_value}"
            )

    if not changed_lines:

        return ""

    return (
        "【変更】\n"
        + "\n".join(
            changed_lines
        )
    )


# ============================================================
# 差分検出
# ============================================================

def build_diff(
    old_text: str,
    new_text: str,
):

    old_map = build_vehicle_map(
        old_text
    )

    new_map = build_vehicle_map(
        new_text
    )

    added = []
    removed = []
    changed = []

    # --------------------------------------------------------
    # 追加
    # --------------------------------------------------------

    for key in new_map:

        if key not in old_map:

            added.append(
                new_map[key]
            )

    # --------------------------------------------------------
    # 削除
    # --------------------------------------------------------

    for key in old_map:

        if key not in new_map:

            removed.append(
                old_map[key]
            )

    # --------------------------------------------------------
    # 変更
    # --------------------------------------------------------

    for key in new_map:

        if key not in old_map:

            continue

        old_record = old_map[key]
        new_record = new_map[key]

        diff = format_changed_vehicle(
            old_record,
            new_record,
        )

        if diff:

            changed.append(
                diff
            )

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    sections = []

    if added:

        sections.append(
            "【追加】\n"
            + "\n\n".join(
                added
            )
        )

    if removed:

        sections.append(
            "【削除】\n"
            + "\n\n".join(
                removed
            )
        )

    if changed:

        sections.append(
            "\n\n".join(
                changed
            )
        )

    if not sections:

        return "", False

    diff_text = "\n\n".join(
        sections
    )

    return diff_text, True


# ============================================================
# ntfy通知
# ============================================================

def publish_ntfy(
    topic: str,
    message: str,
) -> None:

    if not topic:

        raise RuntimeError(
            "NTFY_TOPICが設定されていません。"
        )

    encoded = message.encode(
        "utf-8"
    )

    if len(encoded) > NTFY_MAX_BYTES:

        encoded = encoded[
            :NTFY_MAX_BYTES
        ]

        message = encoded.decode(
            "utf-8",
            errors="ignore",
        )

        message += (
            "\n\n"
            "※通知が長いため途中まで表示しています。"
        )

    url = (
        "https://ntfy.sh/"
        + topic
    )

    print(
        "ntfy通知を送信しています..."
    )

    response = requests.post(
        url,
        data=message.encode(
            "utf-8"
        ),
        headers={
            "Title": (
                "Toyota One-way GO Update"
            ),
            "Priority": "high",
            "Tags": "car",
        },
        timeout=30,
    )

    response.raise_for_status()

    print(
        "ntfy通知を送信しました"
    )


# ============================================================
# state読み込み
# ============================================================

def load_state():

    if not STATE_FILE.exists():

        return None

    try:

        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            "state.jsonの読み込みに失敗しました: "
            f"{e}"
        )

        return None


# ============================================================
# state保存
# ============================================================

def save_state(
    text: str,
    hash_value: str,
) -> None:

    state = {
        "version": STATE_VERSION,
        "hash": hash_value,
        "text": text,
    }

    print(
        "現在の状態をstate.jsonに保存します..."
    )

    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        "現在の状態をstate.jsonに保存しました..."
    )


# ============================================================
# テストモード
# ============================================================

def apply_test_change(
    text: str,
) -> str:

    records = split_vehicle_records(
        text
    )

    if not records:

        raise RuntimeError(
            "テスト対象の車両がありません。"
        )

    record = records[0]

    old_value = None

    lines = []

    for line in record.splitlines():

        if line.startswith(
            "出発期間="
        ):

            old_value = line.split(
                "=",
                1,
            )[1]

            lines.append(
                "出発期間=【テスト変更】"
            )

        else:

            lines.append(
                line
            )

    if old_value is None:

        raise RuntimeError(
            "テスト対象車両に"
            "「出発期間」が見つかりません。"
        )

    records[0] = "\n".join(
        lines
    )

    print(
        "========================================"
    )

    print(
        "TEST_MODE=1"
    )

    print(
        "テスト用に1台目の出発期間を"
        "内部的に変更しました。"
    )

    print(
        f"変更前: {old_value}"
    )

    print(
        "変更後: 【テスト変更】"
    )

    print(
        "========================================"
    )

    return "\n\n".join(
        records
    )


# ============================================================
# メイン
# ============================================================

def main():

    print(
        "Toyota 片道GO! 監視開始"
    )

    # --------------------------------------------------------
    # Toyotaページ取得
    # --------------------------------------------------------

    current = get_kanto_departures()

    # --------------------------------------------------------
    # テストモード
    # --------------------------------------------------------

    test_mode = os.environ.get(
        "TEST_MODE",
        "",
    ).strip()

    if test_mode == "1":

        current = apply_test_change(
            current
        )

    # --------------------------------------------------------
    # ハッシュ
    # --------------------------------------------------------

    current_hash = hashlib.sha256(
        current.encode(
            "utf-8"
        )
    ).hexdigest()

    print(
        "現在のハッシュ: "
        f"{current_hash}"
    )

    # --------------------------------------------------------
    # 前回状態
    # --------------------------------------------------------

    state = load_state()

    if state is None:

        print(
            "前回の状態がありません。"
        )

        print(
            "今回の取得結果を基準値として登録します。"
        )

        if test_mode == "1":

            print(
                "TEST_MODE=1 のため、"
                "テスト結果はstate.jsonに保存しません。"
            )

            print(
                "今回は通知を送信しません。"
            )

            return

        save_state(
            current,
            current_hash,
        )

        print(
            "今回は通知を送信しません。"
        )

        return

    old_hash = state.get(
        "hash",
        "",
    )

    old_text = state.get(
        "text",
        "",
    )

    old_version = state.get(
        "version",
        0,
    )

    print(
        "前回のハッシュ: "
        f"{old_hash}"
    )

    print(
        "前回の状態バージョン: "
        f"{old_version}"
    )

    # --------------------------------------------------------
    # バージョン変更
    # --------------------------------------------------------

    if old_version != STATE_VERSION:

        print(
            "監視方式を更新したため、"
            "今回の取得結果を新しい基準値として登録します。"
        )

        if test_mode == "1":

            print(
                "TEST_MODE=1 のため、"
                "テスト結果はstate.jsonに保存しません。"
            )

            print(
                "今回は通知を送信しません。"
            )

            return

        save_state(
            current,
            current_hash,
        )

        print(
            "今回は通知を送信しません。"
        )

        return

    # --------------------------------------------------------
    # ハッシュ一致
    # --------------------------------------------------------

    if old_hash == current_hash:

        print(
            "変更はありません。"
        )

        if test_mode == "1":

            print(
                "TEST_MODE=1 のため、"
                "state.jsonは変更しません。"
            )

            return

        save_state(
            current,
            current_hash,
        )

        return

    # --------------------------------------------------------
    # ハッシュ変更
    # --------------------------------------------------------

    print(
        "ハッシュが変化しました。"
    )

    diff_text, has_real_change = build_diff(
        old_text,
        current,
    )

    if not has_real_change:

        print(
            "ハッシュは変化しましたが、"
            "車両情報に実質的な変更はありません。"
        )

        print(
            "ntfy通知は送信しません。"
        )

    else:

        print(
            "車両情報に実質的な変更を検出しました。"
        )

        print(
            "----------------------------------------"
        )

        print(
            diff_text[:DIFF_MAX_BYTES]
        )

        print(
            "----------------------------------------"
        )

        topic = os.environ.get(
            "NTFY_TOPIC",
            "",
        ).strip()

        if not topic:

            raise RuntimeError(
                "NTFY_TOPICが設定されていません。"
            )

        publish_ntfy(
            topic,
            diff_text,
        )

    # --------------------------------------------------------
    # state保存
    # --------------------------------------------------------

    if test_mode == "1":

        print(
            "TEST_MODE=1 のため、"
            "テスト結果はstate.jsonに保存しません。"
        )

        return

    save_state(
        current,
        current_hash,
    )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "========================================"
        )

        print(
            "エラーが発生しました。"
        )

        print(
            str(e)
        )

        print(
            "========================================"
        )

        raise
