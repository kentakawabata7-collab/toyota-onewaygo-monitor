import hashlib
import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://cp.toyota.jp/rentacar/?padid=ag270_fr_sptop_onewayma"
STATE_FILE = Path("state.json")


def normalize(text: str) -> str:
    # Remove whitespace differences that are not meaningful.
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [x for x in lines if x]
    return "\n".join(lines)


def get_kanto_departures() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="ja-JP")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # The Toyota page has explicit "出発/到着" and regional tabs.
        # Click the visible controls by their Japanese labels.
        try:
            page.get_by_role("button", name="出発", exact=True).first.click()
        except Exception:
            # If the control is not exposed as a button, use the first exact text match.
            page.get_by_text("出発", exact=True).first.click()

        page.wait_for_timeout(500)

        try:
            page.get_by_role("button", name="関東", exact=True).click()
        except Exception:
            page.get_by_text("関東", exact=True).click()

        page.wait_for_timeout(1000)

        text = page.locator("body").inner_text()
        browser.close()

    # Keep only the actual list area. This removes the navigation/header.
    marker = "ご利用可能車種一覧"
    if marker in text:
        text = text[text.index(marker):]

    return normalize(text)


def publish_ntfy(message: str) -> None:
    import urllib.request

    topic = os.environ["NTFY_TOPIC"]
    data = message.encode("utf-8")
    req = urllib.request.Request(
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
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


def main() -> None:
    current = get_kanto_departures()
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()

    old_hash = None
    if STATE_FILE.exists():
        try:
            old_hash = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("hash")
        except Exception:
            pass

    # First run establishes the baseline; it does not send a notification.
    if old_hash is not None and old_hash != current_hash:
        # Include the filtered page text so the notification is useful.
        # ntfy supports reasonably large messages, but keep it compact.
        message = (
            "トヨタ「片道GO!」の「関東 → 出発」に変更がありました。\n\n"
            + current[:7000]
            + f"\n\n詳細: {URL}"
        )
        publish_ntfy(message)

    STATE_FILE.write_text(
        json.dumps({"hash": current_hash}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
