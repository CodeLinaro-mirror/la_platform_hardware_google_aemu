// Copyright (C) 2026 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const [entryPoint, outFile] = process.argv.slice(2);
if (!entryPoint || !outFile) {
  throw new Error(
    `Usage: esbuild_runner.mjs <entryPoint> <outFile>. Received: ${process.argv
      .slice(2)
      .join(' ')}`
  );
}

const candidateNodeModules = [
  path.resolve(__dirname, '../../../../../api/web/node_modules'),
  path.resolve(__dirname, '../../../../api/web/node_modules'),
  path.resolve(process.cwd(), 'api/web/node_modules'),
  path.resolve(process.cwd(), 'external/aemu+/api/web/node_modules'),
].filter((dir) => fs.existsSync(dir));

if (candidateNodeModules.length === 0) {
  throw new Error(
    `Failed to locate //api/web:node_modules from cwd='${process.cwd()}' or __dirname='${__dirname}'. Ensure //api/web:node_modules/* targets are listed in data/srcs.`
  );
}

function findNativeEsbuildBinary(nodeModulesDir) {
  const storeDir = path.join(nodeModulesDir, '.aspect_rules_js');
  if (!fs.existsSync(storeDir)) {
    return null;
  }
  for (const entry of fs.readdirSync(storeDir)) {
    if (!entry.startsWith('@esbuild+')) {
      continue;
    }
    const pkgSubdir = entry.split('@')[1].replace('+', '/');
    const binCandidate = path.join(
      storeDir,
      entry,
      'node_modules',
      `@${pkgSubdir}`,
      'bin',
      'esbuild'
    );
    if (fs.existsSync(binCandidate)) {
      return binCandidate;
    }
  }
  return null;
}

const nativeBin = findNativeEsbuildBinary(candidateNodeModules[0]);
if (nativeBin && !process.env.ESBUILD_BINARY_PATH) {
  process.env.ESBUILD_BINARY_PATH = nativeBin;
}

const req = createRequire(path.join(candidateNodeModules[0], 'noop.js'));
const esbuild = req('esbuild');

const resolvedEntry = fs.existsSync(entryPoint)
  ? entryPoint
  : path.join(__dirname, 'index.tsx');
const resolvedOut = outFile.startsWith('../')
  ? path.resolve(process.cwd(), 'external', outFile.slice(3))
  : path.resolve(process.cwd(), outFile);

await esbuild.build({
  entryPoints: [resolvedEntry],
  bundle: true,
  format: 'iife',
  target: 'es2022',
  jsx: 'automatic',
  minifyWhitespace: false,
  minifyIdentifiers: false,
  minifySyntax: true,
  define: {
    'process.env.NODE_ENV': '"production"',
  },
  nodePaths: candidateNodeModules,
  outfile: resolvedOut,
});
