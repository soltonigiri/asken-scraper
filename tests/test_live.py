"""Opt-in CLI export, checked against the actual rendered record pages."""

import csv
import json
import os
import re
import subprocess
import sys
import time

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.live
def test_export_matches_account_pages(tmp_path):
    date, state = os.environ.get("ASKEN_LIVE_DATE"), os.environ.get("ASKEN_STATE")
    if not date or not state:
        pytest.skip("Set ASKEN_LIVE_DATE and ASKEN_STATE to read your account.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        pytest.fail("ASKEN_LIVE_DATE must be YYYY-MM-DD")
    output = tmp_path / "export"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "asken_scraper",
            "sync",
            "--from",
            date,
            "--state",
            state,
            "--output",
            str(output),
            "--debug-dir",
            str(tmp_path / "screenshots"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / "commands.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((output / "json" / f"{date}.json").read_text())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(
                storage_state=state, locale="ja-JP", timezone_id="Asia/Tokyo"
            )
            page.goto(
                f"https://www.asken.jp/wsp/comment/{date}",
                wait_until="domcontentloaded",
            )
            weight = page.locator("input[name='Body[weight]']").input_value()
            assert record["body"]["weight_kg"] == (float(weight) if weight else None)
            fat = page.locator("input[name='Body[body_fat]']").input_value()
            assert record["body"]["body_fat_percent"] == (float(fat) if fat else None)
            for meal in record["meals"]:
                time.sleep(2)
                page.goto(
                    f"https://www.asken.jp/wsp/meal/{meal['meal']}/{date}",
                    wait_until="domcontentloaded",
                )
                page.locator("#eat_item").wait_for()
                names = page.locator("#eat_item a.name_a span").all_text_contents()
                assert [i["name"] for i in meal["items"]] == names
                displayed = (
                    page.locator("#meal_type_energy")
                    .inner_text()
                    .replace("kcal", "")
                    .replace(",", "")
                    .strip()
                )
                assert meal["calories_kcal"] == float(displayed)
                if meal["meal"] != "sweets":
                    hour = page.locator("#RecordDateDateHour").input_value()
                    minute = page.locator("#RecordDateDateMin").input_value()
                    expected_time = (
                        f"{int(hour):02d}:{int(minute):02d}"
                        if hour and minute
                        else None
                    )
                    assert meal["time"] == expected_time
            time.sleep(2)
            page.goto(
                f"https://www.asken.jp/wsp/exercise/{date}",
                wait_until="domcontentloaded",
            )
            page.locator("#exercise_total_energy").wait_for()
            names = [
                name.strip()
                for name in page.locator(
                    "#sports_item .menu_line .name"
                ).all_text_contents()
                if name.strip()
            ]
            assert [item["name"] for item in record["exercise"]["items"]] == names
            assert record["exercise"]["calories_kcal"] == float(
                page.locator("#exercise_total_energy").inner_text()
            )
            time.sleep(2)
            page.goto(
                f"https://www.asken.jp/wsp/advice/{date}/0",
                wait_until="domcontentloaded",
            )
            page.locator("#fuki").wait_for()
            if record["nutrition"]["status"] == "available":
                rows = page.locator("div.graph_eiyo").first.locator(
                    "li.line_left:not(.line_title) > ul.left"
                )
                displayed = {
                    rows.nth(i).locator(".title").inner_text().strip(): rows.nth(i)
                    .locator(".val")
                    .inner_text()
                    .strip()
                    for i in range(rows.count())
                }
                assert len(record["nutrition"]["nutrients"]) == len(displayed)
                for nutrient in record["nutrition"]["nutrients"]:
                    text = displayed[nutrient["name"]].replace(",", "")
                    assert (
                        float(text.removesuffix(nutrient["unit"]).strip())
                        == nutrient["value"]
                    )
            else:
                assert "食事記録が無いため" in page.locator("#fuki").inner_text()
        finally:
            browser.close()
    with (output / "daily.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1 and rows[0]["date"] == date
