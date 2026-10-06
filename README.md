# asken-scraper

自分のあすけん記録をJSON・CSVで取り出す非公式ツール。
食事明細・栄養素・運動・体重・体脂肪率に対応しています。

Node.js 22以上・Python 3.10以上が必要です。

## セットアップ

```sh
npm install -g asken-scraper
asken-scraper setup
```

## 使い方

`login` で開いたブラウザにログインし、ターミナルでEnterを押します。

```sh
asken-scraper login
asken-scraper sync --from 2026-09-01 --to 2026-09-30
```

結果は `asken-export/` に保存します。保存済みの日はスキップし、`--refresh` で再取得できます。詳しいオプションは `sync --help` を参照してください。

[MIT](LICENSE)
