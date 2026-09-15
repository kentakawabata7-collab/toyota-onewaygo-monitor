import hashlib
import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


URL = "https://cp.toyota.jp/rentacar/?padid=ag270_fr_sptop_onewayma"
STATE_FILE = Path("state.json")


def normalize(text: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
    ]

    lines = [
        line for line in lines
        if line
    ]

    return "\n".join(lines)


def get_kanto_departures() -> str:

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="ja-JP"
        )

        try:

            print("Toyotaページを開いています...")

            page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(5000)

            print("ページを読み込みました")


            # ==================================================
            # 「出発」を選択
            # ==================================================

            print("「出発」を選択しています...")

            departure = page.get_by_text(
                "出発",
                exact=True
            ).first

            # 通常のclickではなくJavaScriptでクリック
            # Toyotaページでは別の要素がクリックを遮るため
            departure.evaluate(
                "(element) => element.click()"
            )

            page.wait_for_timeout(1500)

            print("「出発」を選択しました")


            # ==================================================
            # 「関東」を選択
            # ==================================================

            print("「関東」を選択しています...")

            kanto = page.get_by_text(
                "関東",
                exact=True
            ).first

            # JavaScriptから直接クリック
            kanto.evaluate(
                "(element) => element.click()"
            )

            page.wait_for_timeout(2000)

            print("「関東」を選択しました")


            # ==================================================
            # ページ本文を取得
            # ==================================================

            text = page.locator(
                "body"
            ).inner_text()

            print("ページ内容を取得しました")


        finally:

            browser.close()


    # ==========================================================
    # 「ご利用可能車種一覧」より後だけを使用
    # ==========================================================

    marker = "ご利用可能車種一覧"

    if marker in text:
        text = text[text.index(marker):]


    return normalize(text)


def publish_ntfy(message: str) -> None:

    import urllib.request


    topic = os.environ["NTFY_TOPIC"]


    data = message.encode(
        "utf-8"
    )


    request = urllib.request.Request(

        f"https://ntfy.sh/{topic}",

        data=data,

        method="POST",

        headers={
            "Title": "片道GO! 関東・出発に変更",
            "Priority": "high",
            "Tags": "car,rotating_light",
            "Click": URL,
        },
    )


    print("ntfyへ通知を送信しています...")


    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        if response.status >= 300:

            raise RuntimeError(
                f"ntfy returned HTTP {response.status}"
            )


    print("ntfyへの通知を送信しました")


def main() -> None:

    print("----------------------------------------")
    print("Toyota 片道GO! 監視開始")
    print("----------------------------------------")


    # ==========================================================
    # 現在のToyotaページを取得
    # ==========================================================

    current = get_kanto_departures()


    # ==========================================================
    # 現在の状態のハッシュ値を作成
    # ==========================================================

    current_hash = hashlib.sha256(
        current.encode("utf-8")
    ).hexdigest()


    print(
        f"現在のハッシュ: {current_hash}"
    )


    # ==========================================================
    # 前回の状態を読み込む
    # ==========================================================

    old_hash = None


    if STATE_FILE.exists():

        try:

            state = json.loads(
                STATE_FILE.read_text(
                    encoding="utf-8"
                )
            )

            old_hash = state.get(
                "hash"
            )

            print(
                f"前回のハッシュ: {old_hash}"
            )

        except Exception as error:

            print(
                f"state.jsonの読み込みに失敗しました: {error}"
            )


    # ==========================================================
    # 初回
    # ==========================================================

    if old_hash is None:

        print(
            "初回実行です。"
        )

        print(
            "現在の状態を保存します。"
        )

        print(
            "今回は通知を送信しません。"
        )


    # ==========================================================
    # 変更あり
    # ==========================================================

    elif old_hash != current_hash:

        print(
            "変更を検出しました！"
        )


        message = (
            "トヨタ「片道GO!」の\n"
            "「関東 → 出発」に変更がありました。\n"
            "\n"
            "現在の掲載内容：\n"
            "\n"
            + current[:7000]
            + "\n\n"
            "詳細：\n"
            + URL
        )


        publish_ntfy(
            message
        )


    # ==========================================================
    # 変更なし
    # ==========================================================

    else:

        print(
            "変更はありません。"
        )


    # ==========================================================
    # 現在の状態を保存
    # ==========================================================

    STATE_FILE.write_text(

        json.dumps(
            {
                "hash": current_hash
            },
            ensure_ascii=False,
            indent=2
        ),

        encoding="utf-8"
    )


    print(
        "現在の状態をstate.jsonに保存しました。"
    )

    print("----------------------------------------")
    print("監視終了")
    print("----------------------------------------")


if __name__ == "__main__":
    main()
