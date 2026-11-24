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
import Module from 'node:module';
import path from 'node:path';

// Ensure Node's CommonJS resolver locates React/ReactDOM from //api/web/node_modules
const candidateNodeModuleDirs = [
  path.resolve(__dirname, '../../../../api/web/node_modules'),
  path.resolve(__dirname, '../../../../../api/web/node_modules'),
];
const existingNodePath = process.env.NODE_PATH
  ? process.env.NODE_PATH.split(path.delimiter)
  : [];
process.env.NODE_PATH = [...candidateNodeModuleDirs, ...existingNodePath].join(
  path.delimiter
);
(Module as unknown as { _initPaths: () => void })._initPaths();

const MINIMUM_SUITES = 1;

const suites = fs
  .readdirSync(__dirname)
  .filter((entry) => entry.endsWith('.test.js'))
  .sort();

if (suites.length < MINIMUM_SUITES) {
  throw new Error(
    `Expected at least ${MINIMUM_SUITES} compiled test suite in ${__dirname}, found ${suites.length}. Ensure BUILD.bazel includes tests/*.test.tsx.`
  );
}

for (const suite of suites) {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  require(path.join(__dirname, suite));
}
