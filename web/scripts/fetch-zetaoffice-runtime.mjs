#!/usr/bin/env node
/**
 * Fetch the ZetaOffice WebAssembly runtime into `public/zetaviewer/runtime/`.
 *
 * The viewer works straight from the CDN, but an air-gapped install cannot reach it, and the
 * offline bundle has to carry these files. Run this on a connected machine before packaging:
 *
 *   node scripts/fetch-zetaoffice-runtime.mjs [--force]
 *
 * `ZETAOFFICE_RUNTIME_URL` overrides the source (for example a mirror or a specific build).
 * The four files are ~250 MiB on disk; the CDN serves them Brotli-compressed, so a download is
 * roughly 53 MB. They are gitignored because they are build inputs, not source.
 */

import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, rename, rm, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, '..', 'public', 'zetaviewer', 'runtime');
const baseUrl = (process.env.ZETAOFFICE_RUNTIME_URL
  || 'https://cdn.zetaoffice.net/zetaoffice_latest/').replace(/\/?$/, '/');
const force = process.argv.includes('--force');

const wantCjkFont = process.argv.includes('--cjk-font');

const FILES = [
  'soffice.js',
  'soffice.wasm',
  'soffice.data',
  'soffice.data.js.metadata',
];

async function exists(path) {
  try {
    const info = await stat(path);
    return info.size > 0;
  } catch {
    return false;
  }
}

async function download(name) {
  const target = join(outDir, name);
  if (!force && await exists(target)) {
    process.stdout.write(`skip   ${name} (already present, --force to replace)\n`);
    return;
  }

  const url = baseUrl + name;
  const response = await fetch(url, { redirect: 'follow' });
  if (!response.ok || !response.body) {
    throw new Error(`${name}: HTTP ${response.status} from ${url}`);
  }

  // Write beside the target and rename, so an interrupted run never leaves a truncated wasm.
  const temp = `${target}.part`;
  await mkdir(dirname(target), { recursive: true });
  await pipeline(Readable.fromWeb(response.body), createWriteStream(temp));
  const written = await stat(temp);
  if (written.size === 0) throw new Error(`${name}: downloaded 0 bytes`);
  await rename(temp, target);
  await rm(temp, { force: true });

  process.stdout.write(
    `ok     ${name} (${(written.size / 1024 / 1024).toFixed(1)} MiB)\n`,
  );
}

await mkdir(outDir, { recursive: true });
process.stdout.write(`runtime <- ${baseUrl}\n`);
for (const name of FILES) await download(name);

/**
 * The bundled runtime has 138 fonts and not one covers CJK, so Chinese text is drawn as tofu
 * boxes. The viewer installs `fonts/cjk.ttf` before LibreOffice starts, which is the only
 * point where the font list is still open.
 *
 * Point `ZETAOFFICE_CJK_FONT_URL` at any TTF/OTF you may redistribute, or pass `--cjk-font`
 * to pull Noto Sans SC Regular (SIL OFL 1.1) out of the @expo-google-fonts package.
 */
if (wantCjkFont || process.env.ZETAOFFICE_CJK_FONT_URL) {
  const fontTarget = join(outDir, 'fonts', 'cjk.ttf');
  if (!force && await exists(fontTarget)) {
    process.stdout.write('skip   fonts/cjk.ttf (already present, --force to replace)\n');
  } else {
    await mkdir(dirname(fontTarget), { recursive: true });
    const directUrl = process.env.ZETAOFFICE_CJK_FONT_URL;
    if (directUrl) {
      const response = await fetch(directUrl, { redirect: 'follow' });
      if (!response.ok || !response.body) {
        throw new Error(`CJK font: HTTP ${response.status} from ${directUrl}`);
      }
      await pipeline(Readable.fromWeb(response.body), createWriteStream(fontTarget));
    } else {
      await fetchNotoSansSc(fontTarget);
    }
    // The face is OFL-licensed, so the notice travels with it.
    await writeFile(
      join(dirname(fontTarget), 'LICENSE.txt'),
      'cjk.ttf is Noto Sans SC (Regular), Copyright 2014-2024 Adobe and Google.\n'
      + 'Licensed under the SIL Open Font License, Version 1.1.\n'
      + 'https://openfontlicense.org\n',
      'utf8',
    );
    process.stdout.write(
      `ok     fonts/cjk.ttf (${((await stat(fontTarget)).size / 1024 / 1024).toFixed(1)} MiB)\n`,
    );
  }
}

/** `npm pack` the Expo Google Fonts package and lift the Regular weight out of the tarball. */
async function fetchNotoSansSc(target) {
  const { execFile } = await import('node:child_process');
  const { promisify } = await import('node:util');
  const { readdir } = await import('node:fs/promises');
  const run = promisify(execFile);

  const staging = await mkdtemp(join(tmpdir(), 'zetafont-'));
  const entry = 'package/400Regular/NotoSansSC_400Regular.ttf';
  await run('npm', ['pack', '@expo-google-fonts/noto-sans-sc', '--pack-destination', staging],
    { stdio: 'inherit', shell: process.platform === 'win32' });
  const tarball = (await readdir(staging)).find((name) => name.endsWith('.tgz'));
  if (!tarball) throw new Error('npm pack produced no tarball');
  await run('tar', ['-xzf', join(staging, tarball), '-C', staging, entry], { stdio: 'inherit' });
  await rename(join(staging, entry), target);
  await rm(staging, { recursive: true, force: true });
}

process.stdout.write(`\nRuntime ready in ${outDir}\n`);
