"""Checkpoint each day before producing spreadsheet-friendly CSV files."""

import csv
import io
import json
import os
import tempfile
from pathlib import Path

from .client import MEALS, ScrapeError


def atomic_write(path: Path, content: str, *, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_day(directory: Path, data):
    atomic_write(
        directory / "json" / f"{data['date']}.json",
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )


def load_day(directory: Path, date: str):
    path = directory / "json" / f"{date}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data["schema_version"] != 1
            or data["date"] != date
            or [m["meal"] for m in data["meals"]] != list(MEALS)
            or data["nutrition"]["status"] not in ("available", "not_recorded")
            or not isinstance(data["nutrition"]["nutrients"], list)
            or not isinstance(data["exercise"]["items"], list)
            or "weight_kg" not in data["body"]
        ):
            raise ValueError
        return data
    except (ValueError, KeyError, TypeError):
        raise ScrapeError(
            f"保存済みの {date}.json を読めません。--refresh で再取得してください。"
        ) from None


def write_csv(path, fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        # Food names are user-controlled. Keep spreadsheet software from executing them.
        safe = {
            k: "'" + v
            if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
            else v
            for k, v in row.items()
        }
        writer.writerow(safe)
    atomic_write(path, stream.getvalue(), encoding="utf-8-sig")


def export_csv(directory: Path):
    days = [
        load_day(directory, path.stem)
        for path in sorted((directory / "json").glob("*.json"))
    ]
    nutrient_columns = list(
        dict.fromkeys(
            f"{n['name']} ({n['unit']})"
            for day in days
            for n in day["nutrition"]["nutrients"]
        )
    )
    daily_rows, meal_rows, exercise_rows = [], [], []
    for day in days:
        date = day["date"]
        daily_rows.append(
            {
                "date": date,
                "nutrition_status": day["nutrition"]["status"],
                **day["body"],
                "exercise_calories_kcal": day["exercise"]["calories_kcal"],
                **{
                    f"{n['name']} ({n['unit']})": n["value"]
                    for n in day["nutrition"]["nutrients"]
                },
            }
        )
        for meal in day["meals"]:
            for item in meal["items"] or [{}]:
                meal_rows.append(
                    {
                        "date": date,
                        "meal": meal["meal"],
                        "time": meal["time"],
                        "meal_calories_kcal": meal["calories_kcal"],
                        "name": item.get("name"),
                        "amount": item.get("amount"),
                        "calories_kcal": item.get("calories_kcal"),
                    }
                )
        for item in day["exercise"]["items"]:
            exercise_rows.append({"date": date, **item})
    write_csv(
        directory / "daily.csv",
        [
            "date",
            "nutrition_status",
            "weight_kg",
            "body_fat_percent",
            "exercise_calories_kcal",
            *nutrient_columns,
        ],
        daily_rows,
    )
    write_csv(
        directory / "meals.csv",
        [
            "date",
            "meal",
            "time",
            "meal_calories_kcal",
            "name",
            "amount",
            "calories_kcal",
        ],
        meal_rows,
    )
    write_csv(
        directory / "exercise.csv",
        ["date", "name", "amount", "calories_kcal"],
        exercise_rows,
    )
