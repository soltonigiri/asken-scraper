// Exercise the packed artifact, including real Python and Chromium installation.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = dirname(dirname(fileURLToPath(import.meta.url)));

test('npm tarball installs, sets up, retries, and forwards CLI arguments and errors', { timeout: 300000 }, () => {
  const work = mkdtempSync(join(tmpdir(), 'asken-npm-'));
  const runtime = join(work, 'runtime with spaces');
  const install = join(work, 'install with spaces');
  const env = { ...process.env, ASKEN_SCRAPER_RUNTIME: runtime };
  delete env.ASKEN_SCRAPER_PYTHON;
  const log = join(work, 'commands.log');
  console.log(`Validation artifacts: ${work}`);

  function run(command, args, { status = 0, extraEnv = {}, cwd = work } = {}) {
    const result = spawnSync(command, args, {
      cwd, env: { ...env, ...extraEnv }, encoding: 'utf8', timeout: 180000,
    });
    appendFileSync(log, `${command} ${args.join(' ')}\n${result.stdout || ''}${result.stderr || ''}\n`);
    assert.equal(result.error, undefined);
    assert.equal(result.status, status, result.stdout + result.stderr);
    return result.stdout + result.stderr;
  }
  function npm(args, options) {
    assert.ok(process.env.npm_execpath, 'Run this test with npm test.');
    return run(process.execPath, [process.env.npm_execpath, ...args], options);
  }
  function cli(args, options) {
    return npm(['exec', '--prefix', install, '--offline', '--', 'asken-scraper', ...args], options);
  }

  const packed = JSON.parse(npm(['pack', '--json', '--pack-destination', work], { cwd: root }))[0];
  writeFileSync(join(work, 'pack.json'), JSON.stringify(packed, null, 2));
  assert.ok(packed.files.some(file => file.path === 'src/asken_scraper/cli.py'));
  assert.ok(packed.files.every(file => /^(package\.json|pyproject\.toml|README\.md|LICENSE|bin\/asken-scraper\.mjs|src\/asken_scraper\/\w+\.py)$/.test(file.path)), 'Unexpected file in npm package');
  npm(['install', '--prefix', install, '--no-audit', '--no-fund', join(work, packed.filename)]);
  const installed = join(install, 'node_modules', 'asken-scraper');
  const { version } = JSON.parse(readFileSync(join(installed, 'package.json')));
  assert.match(readFileSync(join(installed, 'pyproject.toml'), 'utf8'), new RegExp(`version = "${version.replaceAll('.', '\\.')}"`));
  assert.equal(existsSync(runtime), false, 'npm install must not set up Python');
  assert.match(cli(['--help']), /setup/);
  assert.match(cli(['--version']), new RegExp(version.replaceAll('.', '\\.')));
  assert.match(cli(['sync', '--from', '2024-01-01'], { status: 1 }), /setup/);
  assert.match(cli(['setup', '--help']), /Python/);
  cli(['setup', '--unknown'], { status: 2 });
  assert.equal(existsSync(runtime), false);
  assert.match(cli(['setup'], { status: 1, extraEnv: { ASKEN_SCRAPER_PYTHON: join(work, 'missing-python') } }), /Python 3.10/);

  // A failed dependency installation must not mark the runtime ready.
  cli(['setup'], { status: 1, extraEnv: { PIP_NO_INDEX: '1' } });
  assert.equal(existsSync(join(runtime, '.ready')), false);
  cli(['sync', '--from', '2024-01-01'], { status: 1 });
  cli(['setup']);
  cli(['setup']); // Re-running setup must be safe.
  assert.match(cli(['sync', '--help']), /--refresh/);
  cli(['sync', '--from', 'invalid-date'], { status: 2 });
  cli(['csv', '--output', join(work, 'missing-output')], { status: 1 });

  const output = join(work, '日本語 export with spaces');
  mkdirSync(join(output, 'json'), { recursive: true });
  const day = {
    schema_version: 1, date: '2024-01-01',
    body: { weight_kg: 65.4, body_fat_percent: null },
    nutrition: { status: 'not_recorded', nutrients: [] },
    exercise: { calories_kcal: 0, items: [] },
    meals: ['breakfast', 'lunch', 'dinner', 'sweets'].map(meal => ({ meal, time: null, calories_kcal: 0, items: [] })),
  };
  writeFileSync(join(output, 'json', '2024-01-01.json'), JSON.stringify(day));
  cli(['csv', '--output', output]);
  assert.match(readFileSync(join(output, 'daily.csv'), 'utf8'), /2024-01-01,not_recorded,65.4/);
  assert.ok(existsSync(join(output, 'meals.csv')) && existsSync(join(output, 'exercise.csv')));
  assert.match(cli(['sync', '--from', '2024-01-01', '--output', output]), /保存済み/);

  const python = join(runtime, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  run(python, ['-I', '-c', 'from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b = p.chromium.launch()\n page = b.new_page()\n page.set_content("<title>npm package browser check</title>")\n assert page.title() == "npm package browser check"\n b.close()']);
});
