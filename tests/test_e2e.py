"""Exercise the CLI through Chromium against a small, fictional Asken website."""

import csv
import datetime as dt
import html
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

FIRST, SECOND = "2024-01-01", "2024-01-02"


@pytest.fixture
def site():
    state = SimpleNamespace(
        requests=[], blocked=SECOND, wrong_date=False, expired=False, calories=120
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, status=200, **headers):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(
                f"<!doctype html><html><body>{body}</body></html>".encode()
            )

        def do_POST(self):
            assert self.path == "/login/", f"Unexpected POST endpoint: {self.path}"
            data = parse_qs(
                self.rfile.read(int(self.headers["Content-Length"])).decode()
            )
            if data.get("CustomerMember[email]") != [
                "person@example.invalid"
            ] or data.get("CustomerMember[passwd_plain]") != ["fictional-password"]:
                return self.send("Login failed", 401)
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
            self.send(
                "",
                303,
                **{
                    "Location": f"/wsp/comment/{today}",
                    "Set-Cookie": "session=fixture; Path=/; HttpOnly",
                },
            )

        def do_GET(self):
            state.requests.append(self.path)
            if self.path == "/favicon.ico":
                return self.send("", 404)
            if self.path == "/login/":
                return self.send("""<form method="post" action="/login/">
                    <input name="CustomerMember[email]">
                    <input type="password" name="CustomerMember[passwd_plain]">
                    <input name="Submit[submit]" type="submit" value="ログイン">
                    </form>""")
            if state.expired or "session=fixture" not in self.headers.get("Cookie", ""):
                return self.send("", 302, Location="/login/")
            parts = self.path.strip("/").split("/")
            kind = parts[1]
            date = parts[3] if kind == "meal" else parts[2]
            if date == state.blocked:
                return self.send("Unavailable", 403)
            actual = FIRST if state.wrong_date == kind else date
            empty = date == SECOND
            if kind == "comment":
                weight = "" if empty else "65.4"
                return self.send(f'''<input name="Body[record_date]" value="{actual}">
                    <input name="Body[weight]" value="{weight}">
                    <input name="Body[body_fat]" value="">''')
            if kind == "meal":
                meal = parts[2]
                items = (
                    {}
                    if empty or meal != "breakfast"
                    else {
                        "meal-1": {
                            "menu_name": '=架空の食品,"A"',
                            "menu_quantity": "1.5",
                            "energy": str(state.calories),
                            "Parts": {
                                "part-1": {
                                    "menu_part_name": "架空の材料",
                                    "menu_part_energy": str(state.calories),
                                }
                            },
                        }
                    }
                )
                calories = state.calories if items else 0
                clock = (
                    ""
                    if meal == "sweets"
                    else """<select id="RecordDateDateHour"><option value="">--</option><option value="7">07</option></select>
                    <select id="RecordDateDateMin"><option value="">--</option><option value="5">05</option></select>"""
                )
                rendered = '<a class="name_a">架空の食品</a>' if items else ""
                # The time is assigned in JavaScript, as opposed to selected HTML attributes.
                return self.send(f"""<div id="eat_item">{rendered}</div>
                    <div id="meal_type_energy">{calories}kcal</div>{clock}
                    <script>var V2WspMeal = {{recordDate: '{actual}', mealType: '{meal}', eatDatas: {json.dumps(items)}}};
                    if ({str(bool(items)).lower()}) {{document.querySelector('#RecordDateDateHour').value='7';
                    document.querySelector('#RecordDateDateMin').value='5';}}</script>""")
            if kind == "exercise":
                items = [] if empty else [{"name": "ウォーキング", "used_calory": "45"}]
                row = (
                    ""
                    if empty
                    else '<div class="menu_line"><span class="name">ウォーキング</span><span class="time">15分</span></div>'
                )
                return self.send(f"""<div id="sports_item">{row}</div><div id="exercise_total_energy">{0 if empty else 45}</div>
                    <script>var WspExerciseV2={{recordDate:'{actual}',exeDatas:{{menus:{json.dumps(items)}}}}};</script>""")
            if kind == "advice":
                assert parts[3] == "0", "The daily view must be explicit."
                link = f"javascript:WspAdvice.datemode_select('{actual}', 1, 0);"
                message = (
                    "食事記録が無いためアドバイスが計算できません。"
                    if empty
                    else "アドバイス"
                )
                rows = (
                    ""
                    if empty
                    else "".join(
                        f'<li class="line_left"><ul class="left"><li class="title">{name}</li><li class="val">{value}</li></ul></li>'
                        for name, value in [
                            ("エネルギー", f"{state.calories}kcal"),
                            ("タンパク質", "8.5g"),
                            ("脂質", "4g"),
                            ("炭水化物", "15g"),
                        ]
                    )
                )
                return self.send(
                    f'<a href="{html.escape(link)}">平均</a><div id="fuki">{message}</div><div class="graph_eiyo">{rows}</div>'
                )
            self.send("Not found", 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def test_cli_checkpoint_resume_refresh_and_refuse_wrong_data(site, tmp_path):
    output, auth = tmp_path / "export", tmp_path / "auth.json"
    env = dict(
        os.environ,
        ASKEN_EMAIL="person@example.invalid",
        ASKEN_PASSWORD="fictional-password",
    )

    def run(*args, success=True):
        # Only the site address changes. All browser, parsing and disk operations are real.
        entry = "import sys; from asken_scraper import client, cli; client.BASE_URL=sys.argv.pop(1); sys.exit(cli.main())"
        result = subprocess.run(
            [sys.executable, "-c", entry, site.url, *map(str, args)],
            env=env,
            text=True,
            capture_output=True,
            timeout=90,
        )
        with (tmp_path / "commands.log").open("a") as log:
            log.write(result.stdout + result.stderr)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert "fictional-password" not in result.stdout + result.stderr
        return result

    run("login", "--headless", "--state", auth)
    assert json.loads(auth.read_text())["cookies"]
    args = (
        "sync",
        "--from",
        FIRST,
        "--to",
        SECOND,
        "--state",
        auth,
        "--output",
        output,
        "--delay",
        "1",
    )

    run(*args, success=False)
    first = output / "json" / f"{FIRST}.json"
    second = output / "json" / f"{SECOND}.json"
    assert first.is_file() and not second.exists()
    saved = first.read_bytes()
    assert [r["date"] for r in read_csv(output / "daily.csv")] == [FIRST]

    site.blocked = None
    site.requests.clear()
    run(*args)
    assert not any(FIRST in path for path in site.requests)
    assert first.read_bytes() == saved
    day = json.loads(first.read_text())
    assert day["body"] == {"weight_kg": 65.4, "body_fat_percent": None}
    assert day["meals"][0]["time"] == "07:05"
    assert day["meals"][0]["items"][0]["name"] == '=架空の食品,"A"'
    assert day["meals"][3]["time"] is None
    assert day["exercise"]["items"] == [
        {"name": "ウォーキング", "amount": "15分", "calories_kcal": 45.0}
    ]
    empty = json.loads(second.read_text())
    assert empty["nutrition"] == {"status": "not_recorded", "nutrients": []}
    assert empty["body"]["weight_kg"] is None
    rows = read_csv(output / "daily.csv")
    assert (
        len(rows) == 2
        and rows[0]["タンパク質 (g)"] == "8.5"
        and rows[1]["エネルギー (kcal)"] == ""
    )
    assert read_csv(output / "meals.csv")[0]["name"] == '\'=架空の食品,"A"'
    assert len(read_csv(output / "exercise.csv")) == 1

    site.requests.clear()
    run(*args)
    assert site.requests == []

    site.calories = 150
    run(*args, "--refresh")
    assert read_csv(output / "daily.csv")[0]["エネルギー (kcal)"] == "150.0"
    preserved = second.read_bytes()
    site.wrong_date = "advice"
    result = run(
        "sync",
        "--from",
        SECOND,
        "--refresh",
        "--state",
        auth,
        "--output",
        output,
        success=False,
    )
    assert "日付" in result.stderr
    assert second.read_bytes() == preserved

    site.wrong_date, site.expired = False, True
    result = run(
        "sync",
        "--from",
        SECOND,
        "--refresh",
        "--state",
        auth,
        "--output",
        output,
        success=False,
    )
    assert "ログイン" in result.stderr
    assert second.read_bytes() == preserved
    before = (output / "daily.csv").read_bytes()
    (output / "daily.csv").unlink()
    site.requests.clear()
    run("csv", "--output", output)
    assert (output / "daily.csv").read_bytes() == before and site.requests == []
