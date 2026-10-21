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
import ts from 'typescript';

// Enable requiring .ts proto definitions from node_modules/@android/emulator-services
(Module as any)._extensions['.ts'] = function (module: any, filename: string) {
  const source = fs.readFileSync(filename, 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: filename,
  });
  module._compile(outputText, filename);
};

// Redirect 'vitest' imports to our hermetic node:test shim at runtime
const originalResolveFilename = (Module as any)._resolveFilename;
(Module as any)._resolveFilename = function (
  request: string,
  parent: any,
  isMain: boolean,
  options: any
) {
  if (request === 'vitest') {
    return path.join(__dirname, 'vitest_shim.js');
  }
  return originalResolveFilename.call(this, request, parent, isMain, options);
};

// Load every compiled suite.
//
// This was a hand-maintained list of requires, which meant a new test file was
// compiled by Bazel and then never run: the suite passed by not existing. The
// glob in BUILD.bazel already decides what gets compiled, so this just honours
// that decision.
const suites = fs
  .readdirSync(__dirname)
  .filter((entry) => entry.endsWith('.test.js'))
  .sort();

// Guards against a layout change silently emptying the run.
const MINIMUM_SUITES = 18;
if (suites.length < MINIMUM_SUITES) {
  throw new Error(
    `Expected at least ${MINIMUM_SUITES} compiled test suites in ${__dirname}, found ${suites.length}: ${suites.join(', ')}`
  );
}

for (const suite of suites) {
  require(path.join(__dirname, suite));
}

