import os
import re
import json
import hashlib
from pathlib import Path
from difflib import unified_diff

import requests
from playwright.sync_api import sync_playwright


# ============================================================
# 設定
# ============================================================

TOYOTA_URL = (
    "https://cp.toyota.jp/rentacar/"
    "?padid=ag270_fr_sptop_onewayma"
)

STATE_FILE = Path("state.json")
LOG_DIR = Path("logs")

STATE_VERSION = 12

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

TEST_MODE = os.environ.get("TEST_MODE", "0") == "1"


# ============================================================
# 共通処理
# ============================================================

def normalize_line(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonical_vehicle_text(text: str) -> str:
    text = normalize_line(text)
    text = text.replace("ＨＶ", "HV")
    return text


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ============================================================
# Toyotaページ取得
# ============================================================

def get_toyota_page_text(page) -> str:

    print("Toyotaページを開いています...")

    page.goto(
        TOYOTA_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    print("ページを読み込みました")

    page.wait_for_timeout(3000)

    # --------------------------------------------------------
    # 出発
    # --------------------------------------------------------

    print("「出発」を選択しています...")

    departure_locator = page.get_by_text(
        "出発",
        exact=True,
    )

    count = departure_locator.count()

    print(
        f"「出発」候補数: {count}"
    )

    if count == 0:
        raise RuntimeError(
            "「出発」が見つかりません。"
        )

    departure_locator.first.evaluate(
        "(element) => element.click()"
    )

    print(
        "「出発」を選択しました"
    )

    page.wait_for_timeout(2000)

    # --------------------------------------------------------
    # 関東
    # --------------------------------------------------------

    print(
        "「関東」を選択しています..."
    )

    kanto_locator = page.get_by_text(
        "関東",
        exact=True,
    )

    count = kanto_locator.count()

    print(
        f"「関東」候補数: {count}"
    )

    if count == 0:
        raise RuntimeError(
            "「関東」が見つかりません。"
        )

    kanto_locator.first.evaluate(
        "(element) => element.click()"
    )

    print(
        "「関東」を選択しました"
    )

    page.wait_for_timeout(3000)

    # --------------------------------------------------------
    # 本文取得
    # --------------------------------------------------------

    print(
        "表示されている車両一覧を解析しています..."
    )

    body_text = page.locator(
        "body"
    ).inner_text()

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    debug_file = (
        LOG_DIR / "debug_after_filters.txt"
    )

    debug_file.write_text(
        body_text,
        encoding="utf-8",
    )

    print(
        "診断用ページ本文を保存しました: "
        f"{debug_file.resolve()}"
    )

    return body_text


# ============================================================
# 車両情報解析
# ============================================================

def extract_vehicle_records(
    body_text: str,
) -> list[str]:

    PHONE_PATTERN = re.compile(
        r"\d{2,4}-\d{2,4}-\d{3,4}"
    )

    # --------------------------------------------------------
    # 行を整理
    # --------------------------------------------------------

    lines = []

    for raw_line in body_text.splitlines():

        line = normalize_line(
            raw_line
        )

        if line:
            lines.append(line)

    # --------------------------------------------------------
    # フッター以降を除外
    # --------------------------------------------------------

    footer_marker = (
        "片道GO!返却可能店舗一覧"
    )

    if footer_marker in lines:

        footer_index = lines.index(
            footer_marker
        )

        data_lines = lines[
            :footer_index
        ]

    else:

        data_lines = lines

    # --------------------------------------------------------
    # 出発店舗を探す
    # --------------------------------------------------------

    start_index = None

    for i, line in enumerate(
        data_lines
    ):

        if line == "出発店舗":

            start_index = i + 1
            break

    if start_index is None:

        raise RuntimeError(
            "車両データの開始位置"
            "（出発店舗）が見つかりません。"
        )

    print(
        f"車両データ開始位置: {start_index}"
    )

    # --------------------------------------------------------
    # 車両情報を解析
    # --------------------------------------------------------

    records = []

    current = []

    for line in data_lines[
        start_index:
    ]:

        current.append(line)

        phone_match = PHONE_PATTERN.search(
            line
        )

        if not phone_match:
            continue

        # ----------------------------------------------------
        # 1台分完成
        # ----------------------------------------------------

        if len(current) < 7:

            raise RuntimeError(
                "車両データの行数が"
                f"少なすぎます: {len(current)} 行\n"
                + "\n".join(current)
            )

        departure = current[0]
        return_store = current[1]
        vehicle = current[2]
        condition = current[3]
        period = current[4]

        reservation_store = current[-2]
        phone_line = current[-1]

        phone_match = PHONE_PATTERN.search(
            phone_line
        )

        if not phone_match:

            raise RuntimeError(
                "予約電話番号を取得できませんでした。\n"
                + "\n".join(current)
            )

        phone_number = phone_match.group(0)

        if len(current) != 7:

            print(
                "注意: 1台あたりの取得行数が "
                f"{len(current)} 行です。"
            )

        record = "\n".join(
            [
                f"出発店舗={departure}",
                f"返却店舗={return_store}",
                f"車種={canonical_vehicle_text(vehicle)}",
                f"車両条件={normalize_line(condition)}",
                f"出発期間={normalize_line(period)}",
                (
                    "予約電話番号="
                    f"{reservation_store} / "
                    f"{phone_number}"
                ),
            ]
        )

        records.append(record)

        current = []

    # --------------------------------------------------------
    # 未確定データ
    # --------------------------------------------------------

    if current:

        print(
            "警告: 最後に未確定の車両データがあります:"
        )

        print(
            "\n".join(current)
        )

    if not records:

        raise RuntimeError(
            "車両情報を1台も解析できませんでした。"
        )

    print(
        f"解析できた車両数: {len(records)}"
    )

    return records


# ============================================================
# 関東
# ============================================================

def filter_kanto_records(
    records: list[str],
) -> list[str]:

    filtered = []

    for record in records:

        if record.strip():

            filtered.append(record)

    print(
        f"関東対象車両数: {len(filtered)}"
    )

    return filtered


# ============================================================
# TEST_MODE
# ============================================================

def apply_test_change(
    records: list[str],
) -> list[str]:

    if not records:
        return records

    test_records = list(records)

    first = test_records[0]

    match = re.search(
        r"出発期間=(.*)",
        first,
    )

    if match:

        old_period = match.group(1)

        new_first = re.sub(
            r"出発期間=.*",
            "出発期間=【テスト変更】",
            first,
            count=1,
        )

        test_records[0] = new_first

        print(
            "TEST_MODE=1"
        )

        print(
            f"変更前: {old_period}"
        )

        print(
            "変更後: 【テスト変更】"
        )

    return test_records


# ============================================================
# State
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


def save_state(
    records: list[str],
    current_hash: str,
):

    state = {
        "version": STATE_VERSION,
        "hash": current_hash,
        "records": records,
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
        "現在の状態をstate.jsonに保存しました"
    )


# ============================================================
# 差分
# ============================================================

def make_diff(
    old_records: list[str],
    new_records: list[str],
) -> str:

    old_text = (
        "\n\n".join(
            old_records
        ).splitlines()
    )

    new_text = (
        "\n\n".join(
            new_records
        ).splitlines()
    )

    diff = unified_diff(
        old_text,
        new_text,
        fromfile="前回",
        tofile="現在",
        lineterm="",
    )

    diff_lines = list(diff)

    if not diff_lines:

        return ""

    result = []

    for line in diff_lines:

        if line.startswith("---"):
            continue

        if line.startswith("+++"):
            continue

        if line.startswith("@@"):
            continue

        if line.startswith("+"):

            result.append(
                "【追加】" + line[1:]
            )

        elif line.startswith("-"):

            result.append(
                "【削除】" + line[1:]
            )

    return "\n".join(result)


# ============================================================
# 変更判定
# ============================================================

def records_changed(
    old_records: list[str],
    new_records: list[str],
) -> bool:

    return old_records != new_records


# ============================================================
# ntfy
# ============================================================

def send_ntfy_notification(
    diff_text: str,
):

    if not NTFY_TOPIC:

        print(
            "NTFY_TOPICが設定されていないため、"
            "通知を送信しません。"
        )

        return

    headers = {
        "Title": "Toyota One-way GO Update",
        "Priority": "high",
        "Tags": "car,rotating_light",
    }

    message = (
        "Toyota 片道GO! の車両情報に変更があります。\n\n"
        + diff_text
    )

    url = (
        f"https://ntfy.sh/{NTFY_TOPIC}"
    )

    print(
        "ntfy通知を送信しています..."
    )

    response = requests.post(
        url,
        data=message.encode("utf-8"),
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()

    print(
        "ntfy通知を送信しました"
    )


# ============================================================
# メイン
# ============================================================

def main():

    print(
        "Toyota 片道GO! 監視開始"
    )

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    body_text = None

    try:

        # ----------------------------------------------------
        # Playwright
        # ----------------------------------------------------

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True
            )

            try:

                page = browser.new_page(
                    viewport={
                        "width": 1920,
                        "height": 1080,
                    },
                    locale="ja-JP",
                )

                body_text = (
                    get_toyota_page_text(
                        page
                    )
                )

            finally:

                browser.close()

        # ----------------------------------------------------
        # 車両解析
        # ----------------------------------------------------

        records = extract_vehicle_records(
            body_text
        )

        print(
            "ページ全体の解析済み車両数: "
            f"{len(records)}"
        )

        # ----------------------------------------------------
        # 関東
        # ----------------------------------------------------

        kanto_records = (
            filter_kanto_records(
                records
            )
        )

        print(
            "「関東 → 出発」の車両情報を取得しました"
        )

        # ----------------------------------------------------
        # TEST_MODE
        # ----------------------------------------------------

        records_for_compare = (
            kanto_records
        )

        if TEST_MODE:

            records_for_compare = (
                apply_test_change(
                    kanto_records
                )
            )

        # ----------------------------------------------------
        # Hash
        # ----------------------------------------------------

        state_text = (
            "\n\n".join(
                records_for_compare
            )
        )

        current_hash = sha256_text(
            state_text
        )

        print(
            f"現在のハッシュ: {current_hash}"
        )

        # ----------------------------------------------------
        # State
        # ----------------------------------------------------

        state = load_state()

        if state is None:

            print(
                "前回の状態が存在しません。"
            )

            if TEST_MODE:

                print(
                    "TEST_MODE=1 のため、"
                    "テスト結果をstate.jsonに"
                    "保存しません。"
                )

                return

            save_state(
                kanto_records,
                current_hash,
            )

            print(
                "初回取得のため、"
                "今回は通知を送信しません。"
            )

            return

        old_version = state.get(
            "version",
            0,
        )

        old_hash = state.get(
            "hash",
            "",
        )

        old_records = state.get(
            "records",
            [],
        )

        print(
            f"前回のハッシュ: {old_hash}"
        )

        print(
            "前回の状態バージョン: "
            f"{old_version}"
        )

        # ----------------------------------------------------
        # Version変更
        # ----------------------------------------------------

        if old_version != STATE_VERSION:

            print(
                "監視方式を更新したため、"
                "今回の取得結果を新しい基準値として登録します。"
            )

            if TEST_MODE:

                print(
                    "TEST_MODE=1 のため、"
                    "テスト結果をstate.jsonに"
                    "保存しません。"
                )

                return

            save_state(
                kanto_records,
                current_hash,
            )

            print(
                "今回は通知を送信しません。"
            )

            return

        # ----------------------------------------------------
        # Hash一致
        # ----------------------------------------------------

        if current_hash == old_hash:

            print(
                "変更はありません。"
            )

            if not TEST_MODE:

                save_state(
                    kanto_records,
                    current_hash,
                )

            else:

                print(
                    "TEST_MODE=1 のため、"
                    "state.jsonは変更しません。"
                )

            return

        # ----------------------------------------------------
        # Hash変更
        # ----------------------------------------------------

        print(
            "ハッシュが変化しました。"
        )

        changed = records_changed(
            old_records,
            records_for_compare,
        )

        if not changed:

            print(
                "ハッシュは変化しましたが、"
                "車両情報に実質的な変更はありません。"
            )

            if not TEST_MODE:

                save_state(
                    kanto_records,
                    current_hash,
                )

            return

        print(
            "車両情報に実質的な変更を検出しました。"
        )

        # ----------------------------------------------------
        # Diff
        # ----------------------------------------------------

        diff_text = make_diff(
            old_records,
            records_for_compare,
        )

        if diff_text:

            print(
                diff_text
            )

        else:

            print(
                "差分内容を生成できませんでした。"
            )

        # ----------------------------------------------------
        # ntfy
        # ----------------------------------------------------

        send_ntfy_notification(
            diff_text
        )

        # ----------------------------------------------------
        # State保存
        # ----------------------------------------------------

        if TEST_MODE:

            print(
                "TEST_MODE=1 のため、"
                "テスト結果はstate.jsonに保存しません。"
            )

        else:

            save_state(
                kanto_records,
                current_hash,
            )

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


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    main()
