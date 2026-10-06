#!/usr/bin/env node
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { constants, homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const { version } = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const runtime = resolve(process.env.ASKEN_SCRAPER_RUNTIME || join(homedir(), '.asken-scraper', 'runtime', version));
const python = join(runtime, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const ready = join(runtime, '.ready');
const args = process.argv.slice(2);

// Inherit the terminal for interactive login; pass arguments without a shell.
function run(command, argv) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, argv, { stdio: 'inherit' });
    const interrupt = () => child.kill('SIGINT');
    const terminate = () => child.kill('SIGTERM');
    process.on('SIGINT', interrupt);
    process.on('SIGTERM', terminate);
    child.once('error', reject);
    child.once('close', (code, signal) => {
      process.off('SIGINT', interrupt);
      process.off('SIGTERM', terminate);
      resolve(code ?? 128 + (constants.signals[signal] || 1));
    });
  });
}

function findPython() {
  const candidates = process.env.ASKEN_SCRAPER_PYTHON
    ? [[process.env.ASKEN_SCRAPER_PYTHON]]
    : process.platform === 'win32'
      ? [['py', '-3'], ['python'], ['python3']]
      : [['python3'], ['python']];
  for (const [command, ...prefix] of candidates) {
    const result = spawnSync(command, [...prefix, '-I', '-c', 'import sys; sys.exit(sys.version_info < (3, 10))'], {
      stdio: 'ignore', timeout: 10000,
    });
    if (result.status === 0) return [command, prefix];
  }
  throw new Error('Python 3.10以上が必要です。インストール後に setup を再実行してください。実行ファイルは ASKEN_SCRAPER_PYTHON でも指定できます。');
}

async function setup() {
  const [command, prefix] = findPython();
  mkdirSync(runtime, { recursive: true, mode: 0o700 });
  rmSync(ready, { force: true });
  const steps = [
    [command, [...prefix, '-I', '-m', 'venv', runtime]],
    [python, ['-I', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', root]],
    [python, ['-I', '-m', 'playwright', 'install', 'chromium']],
  ];
  for (const [executable, argv] of steps) {
    const code = await run(executable, argv);
    if (code !== 0) {
      console.error('セットアップを完了できませんでした。上のエラーを解消して setup を再実行してください。');
      return code;
    }
  }
  writeFileSync(ready, version, { mode: 0o600 });
  console.log('準備ができました。asken-scraper login でログインしてください。');
  return 0;
}

async function main() {
  if (!args.length || (args.length === 1 && ['--help', '-h'].includes(args[0]))) {
    console.log(`自分のあすけん記録をJSON・CSVに保存します。

使い方: asken-scraper <command> [options]
  setup    Python環境とChromiumを準備（初回・更新後に実行）
  login    ブラウザでログインして認証状態を保存
  sync     指定期間の記録を取得
  csv      保存済みJSONからCSVを再生成

各コマンドの詳細: asken-scraper <command> --help`);
    return 0;
  }
  if (args.length === 1 && args[0] === '--version') {
    console.log(version);
    return 0;
  }
  if (args[0] === 'setup') {
    if (args.length === 2 && ['--help', '-h'].includes(args[1])) {
      console.log('使い方: asken-scraper setup\nPython 3.10以上が必要です。専用環境に依存パッケージとChromiumをダウンロードします。');
      return 0;
    }
    if (args.length !== 1) {
      console.error('使い方: asken-scraper setup');
      return 2;
    }
    return setup();
  }
  if (!existsSync(python) || !existsSync(ready) || readFileSync(ready, 'utf8') !== version) {
    throw new Error('先に asken-scraper setup を実行してください。');
  }
  return run(python, ['-I', '-m', 'asken_scraper', ...args]);
}

try {
  process.exitCode = await main();
} catch (error) {
  console.error(`エラー: ${error.message}`);
  process.exitCode = 1;
}
