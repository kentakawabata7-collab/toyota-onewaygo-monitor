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

# 監視方式のバージョン
STATE_VERSION = 12

# GitHub Actions / Windows BAT から設定
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

# TEST_MODE=1 の場合、実際には変更がない場合でも
# 1件目の出発期間を仮変更して通知テストを行う。
TEST_MODE = os.environ.get("TEST_MODE", "0") == "1"


# ============================================================
# 文字列処理
# ============================================================

def normalize_line(text: str) -> str:
    """
    全角スペースや連続する空白を整理する。
    """
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonical_vehicle_text(text: str) -> str:
    """
    車種表記の揺れをある程度統一する。
    """
    text = normalize_line(text)

    # 全角HV表記を半角に統一
    text = text.replace("ＨＶ", "HV")

    return text


def sha256_text(text: str) -> str:
    """
    監視対象データのSHA-256ハッシュを生成。
    """
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ============================================================
# Toyotaページ取得
# ============================================================

def get_toyota_page_text(page) -> str:
    """
    Toyota 片道GO! ページを開き、

        出発
        ↓
        関東

    を選択した後、車両データが表示されるまで待つ。

    Toyota側の表示が遅い場合に備えて、
    最大3回取得を試行する。
    """

    print("Toyotaページを開いています...")

    page.goto(
        TOYOTA_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    print("ページを読み込みました")

    # 初期JavaScript処理を待つ
    page.wait_for_timeout(3000)

    # --------------------------------------------------------
    # 最大3回試行
    # --------------------------------------------------------

    for attempt in range(1, 4):

        print("==============================")
        print(f"車両データ取得試行 {attempt}/3")
        print("==============================")

        try:

            # ------------------------------------------------
            # 「出発」
            # ------------------------------------------------

            print("「出発」を選択しています...")

            departure_locator = page.get_by_text(
                "出発",
                exact=True,
            )

            count = departure_locator.count()

            print(f"「出発」候補数: {count}")

            if count == 0:
                raise RuntimeError(
                    "「出発」が見つかりません。"
                )

            # JavaScript click
            departure_locator.first.evaluate(
                "(element) => element.click()"
            )

            print("「出発」を選択しました")

            # タブ切り替え処理待ち
            page.wait_for_timeout(2000)

            # ------------------------------------------------
            # 「関東」
            # ------------------------------------------------

            print("「関東」を選択しています...")

            kanto_locator = page.get_by_text(
                "関東",
                exact=True,
            )

            count = kanto_locator.count()

            print(f"「関東」候補数: {count}")

            if count == 0:
                raise RuntimeError(
                    "「関東」が見つかりません。"
                )

            # JavaScript click
            kanto_locator.first.evaluate(
                "(element) => element.click()"
            )

            print("「関東」を選択しました")

            # 関東選択後のJavaScript処理待ち
            page.wait_for_timeout(3000)

            # ------------------------------------------------
            # 車両データが表示されるまで待つ
            # ------------------------------------------------

            print(
                "車両データの表示を待っています..."
            )

            vehicle_data_found = False

            # 最大15秒待つ
            for wait_count in range(15):

                body_text = page.locator(
                    "body"
                ).inner_text()

                # 電話番号が存在するか確認
                phone_found = re.search(
                    r"\d{2,4}-\d{2,4}-\d{3,4}",
                    body_text,
                )

                # 車両一覧のヘッダーが存在するか確認
                has_header = (
                    "出発店舗" in body_text
                    and "返却店舗" in body_text
                    and "車種" in body_text
                    and "出発期間" in body_text
                )

                if has_header and phone_found:

                    print(
                        "車両データを検出しました "
                        f"（待機 {wait_count} 秒）"
                    )

                    vehicle_data_found = True
                    break

                if wait_count < 14:

                    print(
                        "車両データ未検出。"
                        f"{wait_count + 1}秒待機します..."
                    )

                    page.wait_for_timeout(1000)

            # ------------------------------------------------
            # 最終的なページ本文取得
            # ------------------------------------------------

            body_text = page.locator(
                "body"
            ).inner_text()

            # デバッグログ保存
            LOG_DIR.mkdir(
                parents=True,
                exist_ok=True,
            )

            debug_file = (
                LOG_DIR
                / "debug_after_filters.txt"
            )

            debug_file.write_text(
                body_text,
                encoding="utf-8",
            )

            print(
                "診断用ページ本文を保存しました: "
                f"{debug_file.resolve()}"
            )

            # ------------------------------------------------
            # 電話番号の存在確認
            # ------------------------------------------------

            phone_found = re.search(
                r"\d{2,4}-\d{2,4}-\d{3,4}",
                body_text,
            )

            if phone_found:

                print(
                    "車両データを確認しました。"
                )

                return body_text

            # 電話番号がない
            print(
                "車両データがまだ取得できませんでした。"
            )

        except Exception as e:

            print(
                f"試行 {attempt}/3 で"
                "エラーが発生しました: "
                f"{e}"
            )

        # ----------------------------------------------------
        # 再試行
        # ----------------------------------------------------

        if attempt < 3:

            print(
                "Toyotaページを再確認します..."
            )

            page.wait_for_timeout(3000)

    # --------------------------------------------------------
    # 3回とも失敗
    # --------------------------------------------------------

    raise RuntimeError(
        "3回試行しましたが、"
        "車両データを取得できませんでした。"
    )


# ============================================================
# 車両情報解析
# ============================================================

def extract_vehicle_records(
    body_text: str,
) -> list[str]:

    """
    Toyotaページ本文から車両情報を抽出する。

    現在のToyotaページでは、1台あたりの取得行数が
    7行とは限らず、12行になる場合がある。

    そのため、電話番号を車両1台分の終端として扱う。
    """

    # --------------------------------------------------------
    # 電話番号
    # --------------------------------------------------------

    PHONE_PATTERN = re.compile(
        r"\d{2,4}-\d{2,4}-\d{3,4}"
    )

    # --------------------------------------------------------
    # 空行・空白を整理
    # --------------------------------------------------------

    lines = []

    for raw_line in body_text.splitlines():

        line = normalize_line(raw_line)

        if line:
            lines.append(line)

    # --------------------------------------------------------
    # フッター以降は解析対象外
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
    # 「出発店舗」を探す
    # --------------------------------------------------------

    start_index = None

    for i, line in enumerate(data_lines):

        if line == "出発店舗":

            start_index = i + 1
            break

    if start_index is None:

        raise RuntimeError(
            "車両データの開始位置"
            "（出発店舗）が見つかりません。"
        )

    print(
        f"車両データ開始位置: "
        f"{start_index}"
    )

    # --------------------------------------------------------
    # 車両データ解析
    # --------------------------------------------------------

    records = []

    current = []

    for line in data_lines[start_index:]:

        current.append(line)

        # 電話番号が見つかったら、
        # その車両データの終端と判断する。

        phone_match = PHONE_PATTERN.search(
            line
        )

        if not phone_match:
            continue

        # 最低限必要な行数
        if len(current) < 7:

            raise RuntimeError(
                "車両データの行数が"
                "少なすぎます: "
                f"{len(current)} 行\n"
                + "\n".join(current)
            )

        # ----------------------------------------------------
        # 現在のページ構造
        #
        # 先頭：
        #   出発店舗
        #   返却店舗
        #   車種
        #   車両条件
        #   出発期間
        #
        # 末尾：
        #   予約店舗
        #   電話番号
        #
        # ----------------------------------------------------

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
                "予約電話番号を"
                "取得できませんでした。\n"
                + "\n".join(current)
            )

        phone_number = phone_match.group(0)

        # ----------------------------------------------------
        # 12行構造になっている場合もあるため、
        # 警告だけ出して処理は継続する。
        # ----------------------------------------------------

        if len(current) != 7:

            print(
                "注意: 1台あたりの"
                f"取得行数が {len(current)} 行です。"
            )

        # ----------------------------------------------------
        # 正規化したレコードを作成
        # ----------------------------------------------------

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

        # 次の車両へ
        current = []

    # --------------------------------------------------------
    # 最後に電話番号まで到達しなかったデータ
    # --------------------------------------------------------

    if current:

        print(
            "警告: 最後に未確定の"
            "車両データがあります:"
        )

        print(
            "\n".join(current)
        )

    # --------------------------------------------------------
    # 0件ならエラー
    # --------------------------------------------------------

    if not records:

        raise RuntimeError(
            "車両情報を1台も解析できませんでした。"
        )

    print(
        f"解析できた車両数: "
        f"{len(records)}"
    )

    return records


# ============================================================
# 関東フィルタ
# ============================================================

def filter_kanto_records(
    records: list[str],
) -> list[str]:

    """
    現在のページは「関東」をクリックした後の
    表示内容を取得しているため、
    取得されたレコードをそのまま関東対象とする。
    """

    filtered = []

    for record in records:

        if record.strip():

            filtered.append(record)

    print(
        f"関東対象車両数: "
        f"{len(filtered)}"
    )

    return filtered


# ============================================================
# TEST_MODE
# ============================================================

def apply_test_change(
    records: list[str],
) -> list[str]:

    """
    通知テスト用。

    1台目の「出発期間」だけを
    「【テスト変更】」に変更する。

    この変更はメモリ上だけで行い、
    TEST_MODEではstate.jsonへ保存しない。
    """

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
# state.json
# ============================================================

def load_state():

    """
    state.jsonを読み込む。
    """

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
            "state.jsonの読み込みに"
            f"失敗しました: {e}"
        )

        return None


def save_state(
    records: list[str],
    current_hash: str,
):

    """
    現在の正常な監視状態を保存する。
    """

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
# 差分生成
# ============================================================

def make_diff(
    old_records: list[str],
    new_records: list[str],
) -> str:

    """
    前回と今回の車両情報の差分を生成する。
    """

    old_text = "\n\n".join(
        old_records
    ).splitlines()

    new_text = "\n\n".join(
        new_records
    ).splitlines()

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

        # unified diffのヘッダーは除外
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


def records_changed(
    old_records: list[str],
    new_records: list[str],
) -> bool:

    """
    車両情報そのものが変更されたか確認。
    """

    return old_records != new_records


# ============================================================
# ntfy通知
# ============================================================

def send_ntfy_notification(
    diff_text: str,
):

    """
    ntfy.shへ通知する。
    """

    if not NTFY_TOPIC:

        print(
            "NTFY_TOPICが設定されていないため、"
            "通知を送信しません。"
        )

        return

    headers = {
        # 日本語を避け、GitHub Actionsの
        # ASCII環境でも安全にする。
        "Title": "Toyota One-way GO Update",

        "Priority": "high",

        "Tags": "car,rotating_light",
    }

    message = (
        "Toyota 片道GO! の車両情報に"
        "変更があります。\n\n"
        + diff_text
    )

    url = (
        f"https://ntfy.sh/"
        f"{NTFY_TOPIC}"
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
# メイン処理
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
                    get_toyota_page_text(page)
                )

            finally:

                browser.close()

        # ----------------------------------------------------
        # 車両情報解析
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
            filter_kanto_records(records)
        )

        print(
            "「関東 → 出発」の"
            "車両情報を取得しました"
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
        # ハッシュ
        # ----------------------------------------------------

        state_text = "\n\n".join(
            records_for_compare
        )

        current_hash = sha256_text(
            state_text
        )

        print(
            f"現在のハッシュ: "
            f"{current_hash}"
        )

        # ----------------------------------------------------
        # 前回状態
        # ----------------------------------------------------

        state = load_state()

        # ----------------------------------------------------
        # 初回
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 前回情報
        # ----------------------------------------------------

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
            f"前回のハッシュ: "
            f"{old_hash}"
        )

        print(
            f"前回の状態バージョン: "
            f"{old_version}"
        )

        # ----------------------------------------------------
        # state version変更
        # ----------------------------------------------------

        if old_version != STATE_VERSION:

            print(
                "監視方式を更新したため、"
                "今回の取得結果を"
                "新しい基準値として登録します。"
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
        # ハッシュ一致
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
        # ハッシュ変化
        # ----------------------------------------------------

        print(
            "ハッシュが変化しました。"
        )

        changed = records_changed(
            old_records,
            records_for_compare,
        )

        # ----------------------------------------------------
        # 実質変更なし
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 実質変更あり
        # ----------------------------------------------------

        print(
            "車両情報に実質的な変更を"
            "検出しました。"
        )

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
        # ntfy通知
        # ----------------------------------------------------

        send_ntfy_notification(
            diff_text
        )

        # ----------------------------------------------------
        # TEST_MODEの場合は保存しない
        # ----------------------------------------------------

        if TEST_MODE:

            print(
                "TEST_MODE=1 のため、"
                "テスト結果はstate.jsonに"
                "保存しません。"
            )

        else:

            save_state(
                kanto_records,
                current_hash,
            )

    # ========================================================
    # エラー処理
    # ========================================================

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
