# asken-scraper

自分のあすけん記録をJSON・CSVで取り出す非公式ツール。
食事明細・栄養素・運動・体重・体脂肪率に対応しています。

## 動作要件

- Python 3.10以上
- あすけんのアカウント

## セットアップ

プロジェクトのディレクトリで実行します。

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m playwright install chromium
```

Windows PowerShellでは、2行目を `.venv\Scripts\Activate.ps1` に置き換えてください。

## 使い方

初回は `login` で開いたブラウザにログインし、ターミナルでEnterを押します。

```sh
asken-scraper login
asken-scraper sync --from 2026-09-01 --to 2026-09-30
```

`asken-export/` に日別JSONと、日次集計・食事明細・運動明細のCSVを保存します。
保存済みの日はスキップするので、中断後も同じコマンドで再開できます。記録を追加・修正した日は `--refresh` で取り直します。

```sh
asken-scraper sync --from 2026-09-30 --refresh
```

画面を開けない環境では、環境変数 `ASKEN_EMAIL`・`ASKEN_PASSWORD` を設定して `login --headless` を使えます。その他のオプションは `sync --help` で確認できます。

ログイン状態は `~/.asken-scraper/auth.json` に保存します。認証ファイルと取得した健康記録は公開しないでください。

[あすけん利用規約](https://www.asken.inc/kiyaku)・[利用ルール](https://www.asken.inc/asken-rule/)に従って利用してください。コードのライセンスは [MIT](LICENSE) です。
