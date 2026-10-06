import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from . import client
from .client import AskenClient, ScrapeError
from .export import atomic_write, export_csv, load_day, save_day


def date_value(text):
    try:
        value = dt.date.fromisoformat(text)
        if value.isoformat() != text:
            raise ValueError
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(
            "日付は YYYY-MM-DD で指定してください。"
        ) from None


def parser():
    root = argparse.ArgumentParser(
        description="自分のあすけん記録をJSON・CSVに保存します。"
    )
    sub = root.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="ブラウザでログインして認証状態を保存")
    login.add_argument(
        "--headless",
        action="store_true",
        help="ASKEN_EMAIL / ASKEN_PASSWORD で画面を開かずログイン",
    )
    sync = sub.add_parser("sync", help="指定期間を取得。保存済みの日はスキップ")
    sync.add_argument(
        "--from", dest="start", type=date_value, required=True, metavar="YYYY-MM-DD"
    )
    sync.add_argument(
        "--to",
        dest="end",
        type=date_value,
        metavar="YYYY-MM-DD",
        help="省略時は開始日のみ",
    )
    sync.add_argument("--output", type=Path, default=Path("asken-export"))
    sync.add_argument(
        "--refresh", action="store_true", help="指定期間の保存済みデータも再取得"
    )
    sync.add_argument(
        "--delay",
        type=float,
        default=2,
        help="ページ取得間隔の下限（秒、1以上。既定2）",
    )
    sync.add_argument(
        "--debug-dir",
        type=Path,
        help="各ページのスクリーンショットを保存（個人情報を含む）",
    )
    export = sub.add_parser("csv", help="保存済みJSONからCSVを再生成（通信なし）")
    export.add_argument("--output", type=Path, default=Path("asken-export"))
    for cmd in (login, sync):
        cmd.add_argument(
            "--state",
            type=Path,
            default=Path.home() / ".asken-scraper" / "auth.json",
            help="認証状態の保存先（既定 ~/.asken-scraper/auth.json）",
        )
    return root


def login(page, args):
    page.goto(client.BASE_URL + "/login/", wait_until="domcontentloaded")
    if args.headless:
        email, password = (
            os.environ.get("ASKEN_EMAIL"),
            os.environ.get("ASKEN_PASSWORD"),
        )
        if not email or not password:
            raise ScrapeError(
                "--headless には ASKEN_EMAIL と ASKEN_PASSWORD が必要です。"
            )
        page.locator("input[name='CustomerMember[email]']").fill(email)
        page.locator("input[name='CustomerMember[passwd_plain]']").fill(password)
        page.locator("input[name='Submit[submit]']").click()
    else:
        print(
            "開いたブラウザでログインしてください。完了したらこの画面でEnterを押してください。",
            flush=True,
        )
        input()
    page.wait_for_url(
        lambda url: not urlparse(url).path.startswith("/login"), timeout=15_000
    )
    # Confirm the session against a protected page before saving it.
    AskenClient(page).body(
        dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    )
    atomic_write(args.state, json.dumps(page.context.storage_state()))


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.command == "sync":
        args.end = args.end or args.start
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
        if args.end < args.start or args.end > today:
            arguments.error("開始日≦終了日≦今日（日本時間）を指定してください。")
        if not 1 <= args.delay <= 3600:
            arguments.error("--delay は1〜3600秒で指定してください。")
    try:
        if args.command == "csv":
            if not (args.output / "json").is_dir():
                raise ScrapeError(
                    "出力先に json/ がありません。先に sync を実行してください。"
                )
            export_csv(args.output)
            print(f"CSVを更新しました: {args.output}")
            return 0
        pending = []
        if args.command == "sync":
            day = args.start
            while day <= args.end:
                date = day.isoformat()
                if args.refresh or load_day(args.output, date) is None:
                    pending.append(date)
                else:
                    print(f"{date} 保存済み", flush=True)
                day += dt.timedelta(days=1)
            if not pending:
                export_csv(args.output)
                print("取得対象はすべて保存済みです。CSVを更新しました。")
                return 0
            if not args.state.is_file():
                raise ScrapeError(
                    "認証状態がありません。先に login を実行してください。"
                )
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=args.command != "login" or args.headless
            )
            try:
                context = browser.new_context(
                    storage_state=str(args.state) if args.command == "sync" else None,
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                )
                page = context.new_page()
                if args.command == "login":
                    login(page, args)
                    print(f"ログイン状態を保存しました: {args.state}")
                else:
                    scraper = AskenClient(
                        page, delay=args.delay, debug_dir=args.debug_dir
                    )
                    try:
                        for date in pending:
                            print(f"{date} 取得中", flush=True)
                            save_day(args.output, scraper.day(date))
                            print(f"{date} 保存しました", flush=True)
                    finally:
                        if not urlparse(page.url).path.startswith("/login"):
                            atomic_write(
                                args.state, json.dumps(context.storage_state())
                            )
                        if (args.output / "json").is_dir():
                            export_csv(args.output)
            finally:
                browser.close()
        return 0
    except ScrapeError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
    except PlaywrightError:
        print(
            "エラー: ブラウザの起動・画面操作に失敗しました。Chromiumのインストール、ログイン状態、サイトの表示を確認してください。",
            file=sys.stderr,
        )
    except (OSError, ValueError, EOFError) as exc:
        print(
            f"エラー: {type(exc).__name__}。ファイルのパス・権限・形式を確認してください。",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        print("中断しました。保存済みの日は次回スキップします。", file=sys.stderr)
        return 130
    return 1
