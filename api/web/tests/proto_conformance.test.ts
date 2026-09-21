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
import path from 'node:path';
import { describe, it, expect } from 'vitest';

import * as web from '../src/index';

import * as audioService from '@android/emulator-services/audio_service';
import * as batteryService from '@android/emulator-services/battery_service';
import * as cameraService from '@android/emulator-services/camera_service';
import * as capabilitiesService from '@android/emulator-services/capabilities_service';
import * as commonTypes from '@android/emulator-services/common';
import * as displayService from '@android/emulator-services/display_service';
import * as hypervisorService from '@android/emulator-services/hypervisor_service';
import * as inputService from '@android/emulator-services/input_service';
import * as locationService from '@android/emulator-services/location_service';
import * as rtcService from '@android/emulator-services/webrtc_service';
import * as sensorsService from '@android/emulator-services/sensors_service';
import * as systemService from '@android/emulator-services/system_service';
import * as telephonyService from '@android/emulator-services/telephony_service';

interface ServiceDefinition {
  fullName: string;
  methods: Record<string, { name: string; requestStream: boolean; responseStream: boolean }>;
}

/**
 * Every module emitted by `//protos/services_v2` that the clients build on.
 * These are generated from the `.proto` sources at build time, so they are the
 * authoritative description of both the wire API and its types.
 */
const GENERATED_MODULES: Array<Record<string, unknown>> = [
  audioService,
  batteryService,
  cameraService,
  capabilitiesService,
  commonTypes,
  displayService,
  hypervisorService,
  inputService,
  locationService,
  rtcService,
  sensorsService,
  systemService,
  telephonyService,
];

/**
 * Derived rather than listed by hand: a service added to one of the modules
 * above is picked up automatically.
 */
const ALL_SERVICES: ServiceDefinition[] = GENERATED_MODULES.flatMap((module) =>
  Object.entries(module)
    .filter(([name]) => name.endsWith('ServiceDefinition'))
    .map(([, definition]) => definition as ServiceDefinition)
);

/**
 * Locates the directory holding the compiled client sources. Resolving a known
 * module rather than deriving a path from `__dirname` keeps the test working
 * regardless of how the Bazel output tree is laid out.
 */
function coreSourceDir(): string {
  return path.dirname(require.resolve('../src/core/transport'));
}

function readCompiledSources(): Array<{ file: string; source: string }> {
  const dir = coreSourceDir();
  return fs
    .readdirSync(dir)
    .filter((entry) => entry.endsWith('.js'))
    .map((entry) => ({
      file: entry,
      source: fs.readFileSync(path.join(dir, entry), 'utf8'),
    }));
}

/**
 * `transport.js` defines the transport, so it necessarily mentions its own
 * methods. Nothing else in the package is allowed to call them directly.
 */
const TRANSPORT_IMPLEMENTATION = 'transport.js';

const RPC_INVOCATION_RE = /transport\.(?:request|stream|serverStream)\(/g;
const SERVICE_NAME_RE = /['"]android\.emulation\.v2\.[\w.]+['"]/g;

describe('proto conformance', () => {
  const sources = readCompiledSources();

  it('reads the compiled clients', () => {
    // Without this every assertion below would pass vacuously if the path ever
    // stopped resolving.
    expect(sources.length).toBeGreaterThan(15);
    expect(sources.some(({ file }) => file === TRANSPORT_IMPLEMENTATION)).toBe(true);
  });

  // The generated `*ClientImpl` classes take the service name, the method name,
  // the request encoder and the response decoder from the `.proto`. Code that
  // calls the transport directly re-states all four by hand, and every drift
  // bug this package has had came from one of those hand-written restatements.
  // Rather than test the restatements for correctness, forbid them.
  it('routes every RPC through a generated client', () => {
    const offenders = sources
      .filter(({ file }) => file !== TRANSPORT_IMPLEMENTATION)
      .flatMap(({ file, source }) =>
        Array.from(source.matchAll(RPC_INVOCATION_RE)).map(
          (match) => `${file}: ${match[0]}`
        )
      );

    expect(offenders).toEqual([]);
  });

  it('never names a service with a string literal', () => {
    const offenders = sources.flatMap(({ file, source }) =>
      Array.from(source.matchAll(SERVICE_NAME_RE)).map((match) => `${file}: ${match[0]}`)
    );

    expect(offenders).toEqual([]);
  });

  // Catches a service being added to the protos without a client following it.
  // Per-RPC coverage no longer needs checking: a generated client implements
  // every method of its service by construction.
  it('has a client for every generated service', () => {
    // Services this package reaches by some means other than a gRPC client.
    const NOT_SERVED_OVER_GRPC: Record<string, string> = {
      InputService: 'input is delivered over the WebRTC input_v2 DataChannel',
    };

    const instantiated = new Set<string>();
    for (const { source } of sources) {
      // TypeScript emits `new vm_service_1.VmServiceClientImpl(...)`, so the
      // module qualifier has to be tolerated here.
      for (const match of source.matchAll(/new (?:[\w$]+\.)?(\w+ServiceClientImpl)\(/g)) {
        instantiated.add(match[1]);
      }
    }

    // Guards against the regex silently matching nothing again.
    expect(instantiated.size).toBeGreaterThan(10);

    const missing = ALL_SERVICES.map((service) => service.fullName)
      .map((fullName) => fullName.slice(fullName.lastIndexOf('.') + 1))
      .filter((serviceName) => !(serviceName in NOT_SERVED_OVER_GRPC))
      .filter((serviceName) => !instantiated.has(`${serviceName}ClientImpl`))
      .sort();

    expect(missing).toEqual([]);
  });
});

describe('generated type shadowing', () => {
  it('re-exports generated types instead of re-declaring them', () => {
    // Both VmStateEnum and the hand-written PointerAction / ToolType / KeyAction
    // duplicated a generated type under a name the package also re-exports. The
    // copies drifted, or stood ready to. A re-export is the same object as the
    // generated one; a re-declaration is a different object with the same name.
    const shadowed: string[] = [];
    let reExported = 0;

    for (const module of GENERATED_MODULES) {
      for (const [name, generated] of Object.entries(module)) {
        const exported = (web as Record<string, unknown>)[name];
        if (exported === undefined) {
          continue; // Not surfaced by the package at all.
        }
        if (exported === generated) {
          reExported++;
        } else {
          shadowed.push(name);
        }
      }
    }

    // Without this the check would also pass if `web` failed to load, or if the
    // package stopped re-exporting generated types altogether.
    expect(reExported).toBeGreaterThan(20);
    expect(Array.from(new Set(shadowed)).sort()).toEqual([]);
  });
});

/**
 * True for an ES class, false for an ordinary function.
 *
 * Needed because `useDeviceClient` is a React hook whose name happens to end in
 * `Client`. A class's `prototype` property is non-writable; a function
 * declaration's is writable, and an arrow function has none at all.
 */
function isClass(value: unknown): boolean {
  if (typeof value !== 'function') {
    return false;
  }
  const prototype = Object.getOwnPropertyDescriptor(value, 'prototype');
  return prototype !== undefined && prototype.writable === false;
}

/**
 * Every exported class whose name ends in `Client`, paired with the service it
 * wraps. Both halves are discovered rather than listed: the classes come from
 * the package's own exports, and the service comes from the
 * `new *ServiceClientImpl(...)` the class's compiled source contains. A client
 * added tomorrow is therefore covered without touching this file.
 */
function discoverClients(sources: Array<{ file: string; source: string }>) {
  const servicesByImpl = new Map<string, ServiceDefinition>(
    ALL_SERVICES.map((service) => {
      const shortName = service.fullName.slice(service.fullName.lastIndexOf('.') + 1);
      return [`${shortName}ClientImpl`, service];
    })
  );

  return Object.entries(web)
    .filter(([name, value]) => isClass(value) && name.endsWith('Client') && name !== 'DeviceClient')
    .map(([name, ctor]) => {
      const declaring = sources.find(({ source }) =>
        new RegExp(`class ${name}\\b`).test(source)
      );
      const impl = declaring?.source.match(/new (?:[\w$]+\.)?(\w+ServiceClientImpl)\(/)?.[1];
      return {
        name,
        ctor: ctor as new (transport: unknown) => object,
        impl,
        service: impl ? servicesByImpl.get(impl) : undefined,
      };
    });
}

/** Public, non-inherited instance methods of a class. */
function methodsOf(ctor: new (transport: unknown) => object): string[] {
  // The transport is never touched by a constructor; every client only stores
  // the generated stub it builds from it.
  const instance = new ctor(new web.GrpcWebTransport({ endpoint: 'http://127.0.0.1:0' }));
  return Object.getOwnPropertyNames(Object.getPrototypeOf(instance)).filter(
    (name) => name !== 'constructor' && !name.startsWith('_')
  );
}

// The package's one naming rule, made mechanical: a method spelled in
// PascalCase is an RPC, named exactly as the .proto names it, and a method
// spelled in camelCase is not an RPC. That distinction is the only thing
// telling a reader which calls cross the wire, so it has to hold everywhere
// rather than almost everywhere.
describe('method naming mirrors the proto', () => {
  const clients = discoverClients(readCompiledSources());

  it('finds a service for every client', () => {
    // Without this the two checks below would pass vacuously the moment the
    // discovery regexes stopped matching.
    expect(clients.length).toBeGreaterThan(10);
    expect(clients.filter(({ service }) => service === undefined).map(({ name }) => name)).toEqual(
      []
    );
  });

  it('gives every PascalCase method the name of an RPC on its own service', () => {
    const offenders: string[] = [];
    let checked = 0;

    for (const { name, ctor, service } of clients) {
      if (!service) continue;
      const rpcNames = new Set(Object.values(service.methods).map((method) => method.name));

      for (const method of methodsOf(ctor)) {
        if (!/^[A-Z]/.test(method)) continue;
        checked++;
        if (!rpcNames.has(method)) {
          offenders.push(`${name}.${method} is not an RPC of ${service.fullName}`);
        }
      }
    }

    expect(checked).toBeGreaterThan(30);
    expect(offenders).toEqual([]);
  });

  it('never hides an RPC behind a camelCase name', () => {
    // The failure this catches is a method reverting to `getBatteryState`,
    // which reads like a local accessor but is a round trip.
    const offenders: string[] = [];

    for (const { name, ctor, service } of clients) {
      if (!service) continue;
      const rpcNames = new Set(Object.values(service.methods).map((method) => method.name));

      for (const method of methodsOf(ctor)) {
        if (/^[A-Z]/.test(method)) continue;
        const asRpcName = method.charAt(0).toUpperCase() + method.slice(1);
        if (rpcNames.has(asRpcName)) {
          offenders.push(`${name}.${method} should be spelled ${asRpcName}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});

describe('proto docstring cross-references', () => {
  const validServiceRpcs = new Set<string>(
    ALL_SERVICES.flatMap((service) => {
      const shortName = service.fullName.slice(service.fullName.lastIndexOf('.') + 1);
      return Object.values(service.methods).map((method) => `${shortName}.${method.name}`);
    })
  );

  const validEnumValues = new Set<string>(
    GENERATED_MODULES.flatMap((module) =>
      Object.values(module).flatMap((entry) => {
        if (typeof entry !== 'object' || entry === null) return [];
        return Object.entries(entry as Record<string, unknown>)
          .filter(([key, value]) => /^[A-Z][A-Z0-9_]+$/.test(key) && typeof value === 'number')
          .map(([key]) => key);
      })
    )
  );

  function readProtoCommentLines(): Array<{ file: string; content: string }> {
    const protosRoot = path.resolve(coreSourceDir(), '../../../../../protos/services_v2');
    const results: Array<{ file: string; content: string }> = [];

    function walk(dir: string) {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(fullPath);
        } else if (entry.name.endsWith('.proto')) {
          const commentLines = fs
            .readFileSync(fullPath, 'utf8')
            .split('\n')
            .filter((line) => line.trimStart().startsWith('//'))
            .join('\n');
          results.push({
            file: path.relative(protosRoot, fullPath),
            content: commentLines,
          });
        }
      }
    }

    walk(protosRoot);
    return results;
  }

  it('references only valid ServiceName.RpcName methods in docstrings', () => {
    const docs = readProtoCommentLines();
    expect(docs.length).toBeGreaterThan(10);

    const offenders: string[] = [];
    const rpcCallRefRe = /\b([A-Z]\w*Service)\.([A-Z]\w+)\s*\(/g;

    for (const { file, content } of docs) {
      for (const match of content.matchAll(rpcCallRefRe)) {
        const ref = `${match[1]}.${match[2]}`;
        if (!validServiceRpcs.has(ref)) {
          offenders.push(`${file}: unknown RPC reference "${ref}()"`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it('references only valid package-level enum values in docstrings', () => {
    const docs = readProtoCommentLines();
    expect(docs.length).toBeGreaterThan(10);
    const offenders: string[] = [];
    const enumTokenRe =
      /\b((?:POSTURE|SENSOR|IMAGE_FORMAT|SAMPLE_FORMAT|ACCESS_MODE|CHARGER_SOURCE|FOLD_AXIS|ROTATION|DISPLAY_FLAG|POINTER_ACTION|TOOL_TYPE|KEY_ACTION|BUTTON_MASK|AXIS_TYPE|TRACK_TYPE)_[A-Z0-9_]+)\b/g;

    for (const { file, content } of docs) {
      for (const match of content.matchAll(enumTokenRe)) {
        const token = match[1];
        if (!validEnumValues.has(token)) {
          offenders.push(`${file}: unknown enum token "${token}"`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});


