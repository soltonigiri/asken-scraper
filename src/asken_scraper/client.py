"""Read the web pages a signed-in Asken user can see."""

import datetime as dt
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

BASE_URL = "https://www.asken.jp"
MEALS = ("breakfast", "lunch", "dinner", "sweets")


class ScrapeError(Exception):
    """Page retrieval or parsing failed."""


def number(value, *, optional=False):
    text = str(value if value is not None else "").strip().replace(",", "")
    if optional and text in ("", "-", "--"):
        return None
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:kcal|kg|%|g|mg|μg|µg)?", text)
    if not match:
        raise ScrapeError("数値の表示形式が変わりました。取得を中止します。")
    return float(match[1])


def check_date(actual, expected):
    if actual != expected:
        raise ScrapeError(f"ページ内の日付が指定日 {expected} と一致しません。")


class AskenClient:
    def __init__(self, page: Page, *, delay=2.0, debug_dir: Path | None = None):
        self.page = page
        self.delay = delay
        self.debug_dir = debug_dir
        self.last_request = 0.0
        page.set_default_timeout(20_000)
        page.set_default_navigation_timeout(30_000)

    def open(self, path):
        for attempt in range(3):
            time.sleep(max(0, self.last_request + self.delay - time.monotonic()))
            self.last_request = time.monotonic()
            try:
                response = self.page.goto(
                    BASE_URL + path, wait_until="domcontentloaded"
                )
            except PlaywrightTimeout:
                if attempt == 2:
                    raise ScrapeError(
                        "ページの読み込みがタイムアウトしました。再実行で続きから取得できます。"
                    ) from None
                time.sleep(2 ** (attempt + 1))
                continue
            if response and response.status >= 500 and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            if not response or response.status >= 400:
                status = response.status if response else "no response"
                raise ScrapeError(
                    f"サイトが HTTP {status} を返しました。取得を中止します。"
                )
            if urlparse(self.page.url).path.startswith("/login"):
                raise ScrapeError("ログインが必要です。login を実行してください。")
            return

    def snapshot(self, date, kind):
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.page.screenshot(
                path=str(self.debug_dir / f"{date}-{kind}.png"), full_page=True
            )

    def body(self, date):
        self.open(f"/wsp/comment/{date}")
        self.page.locator("input[name='Body[record_date]']").wait_for(state="attached")
        data = self.page.evaluate("""() => {
            const field = name => document.querySelector(`input[name='Body[${name}]']`)?.value;
            return {date: field('record_date'), weight: field('weight'), fat: field('body_fat')};
        }""")
        check_date(data["date"], date)
        if data.get("weight") is None or data.get("fat") is None:
            raise ScrapeError("体重・体脂肪の入力欄が見つかりません。")
        self.snapshot(date, "body")
        return {
            "weight_kg": number(data["weight"], optional=True),
            "body_fat_percent": number(data["fat"], optional=True),
        }

    def meal(self, date, meal):
        self.open(f"/wsp/meal/{meal}/{date}")
        self.page.wait_for_function(
            "typeof V2WspMeal !== 'undefined' && V2WspMeal.recordDate && document.querySelector('#meal_type_energy')"
        )
        data = self.page.evaluate("""() => ({
            date: V2WspMeal.recordDate,
            meal: V2WspMeal.mealType,
            items: Object.values(V2WspMeal.eatDatas),
            rendered: document.querySelectorAll('#eat_item a.name_a').length,
            hour: document.querySelector('#RecordDateDateHour')?.value,
            minute: document.querySelector('#RecordDateDateMin')?.value,
            calories: document.querySelector('#meal_type_energy')?.textContent
        })""")
        check_date(data["date"], date)
        if data["meal"] != meal or len(data["items"]) != data["rendered"]:
            raise ScrapeError("食事明細の表示が一致しません。")
        if meal != "sweets" and (
            data.get("hour") is None or data.get("minute") is None
        ):
            raise ScrapeError("食事時刻の入力欄が見つかりません。")
        meal_time = None
        if data.get("hour") not in (None, "") and data.get("minute") not in (None, ""):
            try:
                meal_time = dt.time(int(data["hour"]), int(data["minute"])).strftime(
                    "%H:%M"
                )
            except ValueError:
                raise ScrapeError("食事時刻が読み取れません。") from None
        items = []
        for item in data["items"]:
            if not item.get("menu_name") or "menu_quantity" not in item:
                raise ScrapeError("食事明細の形式が変わりました。")
            items.append(
                {
                    "name": item["menu_name"],
                    "amount": str(item["menu_quantity"]),
                    "calories_kcal": number(item.get("energy")),
                    "parts": [
                        {
                            "name": part["menu_part_name"],
                            "calories_kcal": number(part["menu_part_energy"]),
                        }
                        for part in item.get("Parts", {}).values()
                    ],
                }
            )
        self.snapshot(date, meal)
        return {
            "meal": meal,
            "time": meal_time,
            "calories_kcal": number(data["calories"]),
            "items": items,
        }

    def exercise(self, date):
        self.open(f"/wsp/exercise/{date}")
        self.page.wait_for_function(
            "typeof WspExerciseV2 !== 'undefined' && WspExerciseV2.recordDate && document.querySelector('#exercise_total_energy')"
        )
        data = self.page.evaluate("""() => ({
            date: WspExerciseV2.recordDate,
            items: WspExerciseV2.exeDatas.menus,
            rows: [...document.querySelectorAll('#sports_item .menu_line')]
                .map(e => ({name: e.querySelector('.name')?.textContent.trim(),
                            amount: e.querySelector('.time')?.textContent.trim()}))
                .filter(e => e.name),
            calories: document.querySelector('#exercise_total_energy')?.textContent
        })""")
        check_date(data["date"], date)
        if len(data["items"]) != len(data["rows"]):
            raise ScrapeError("運動明細の表示が一致しません。")
        items = []
        for item, row in zip(data["items"], data["rows"]):
            if item["name"].strip() != row["name"]:
                raise ScrapeError("運動明細の順番が一致しません。")
            items.append(
                {
                    "name": row["name"],
                    "amount": row["amount"],
                    "calories_kcal": number(item["used_calory"]),
                }
            )
        self.snapshot(date, "exercise")
        return {"calories_kcal": number(data["calories"]), "items": items}

    def nutrition(self, date):
        # The trailing /0 selects a single day; the date alone is not reliable.
        self.open(f"/wsp/advice/{date}/0")
        self.page.locator("#fuki").wait_for()
        data = self.page.evaluate("""() => ({
            links: [...document.querySelectorAll('a[href]')].map(e => e.getAttribute('href')),
            message: document.querySelector('#fuki').textContent,
            rows: [...(document.querySelector('div.graph_eiyo')?.querySelectorAll('li.line_left:not(.line_title) > ul.left') || [])]
                .map(e => ({name: e.querySelector('.title')?.textContent.trim(),
                            value: e.querySelector('.val')?.textContent.trim()}))
        })""")
        dates = set()
        for href in data["links"]:
            match = re.search(
                r"/my_diary/edit/(\d{4}-\d{2}-\d{2})|datemode_select\('(\d{4}-\d{2}-\d{2})'",
                href,
            )
            if match:
                dates.add(match[1] or match[2])
        if dates != {date}:
            check_date(None, date)
        if not data["rows"]:
            if "食事記録が無いため" in data["message"]:
                self.snapshot(date, "nutrition")
                return {"status": "not_recorded", "nutrients": []}
            raise ScrapeError(
                "栄養素が表示されていません。会員プラン・記録状態・画面変更を確認してください。"
            )
        nutrients = []
        for row in data["rows"]:
            match = re.fullmatch(r"([\d,.]+)\s*(kcal|g|mg|μg|µg)", row["value"] or "")
            if not row["name"] or not match:
                raise ScrapeError("栄養素の表示形式が変わりました。")
            nutrients.append(
                {"name": row["name"], "value": number(match[1]), "unit": match[2]}
            )
        self.snapshot(date, "nutrition")
        return {"status": "available", "nutrients": nutrients}

    def day(self, date):
        body = self.body(date)
        meals = [self.meal(date, meal) for meal in MEALS]
        exercise = self.exercise(date)
        nutrition = self.nutrition(date)
        if nutrition["status"] == "not_recorded" and any(m["items"] for m in meals):
            raise ScrapeError(
                "食事明細はありますが栄養ページは未記録です。取得を中止します。"
            )
        return {
            "schema_version": 1,
            "date": date,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "body": body,
            "meals": meals,
            "exercise": exercise,
            "nutrition": nutrition,
        }
