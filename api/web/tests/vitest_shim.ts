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

import {
  describe as nodeDescribe,
  it as nodeIt,
  beforeEach as nodeBeforeEach,
  afterEach as nodeAfterEach,
} from 'node:test';
import assert from 'node:assert/strict';

export const describe = nodeDescribe;
export const it = nodeIt;
export const test = nodeIt;
export const beforeEach = nodeBeforeEach;
export const afterEach = nodeAfterEach;

const OBJECT_CONTAINING_SYMBOL = Symbol('expect.objectContaining');

export interface MockFn<T extends (...args: any[]) => any = (...args: any[]) => any> {
  (...args: Parameters<T>): ReturnType<T>;
  mock: {
    calls: Parameters<T>[];
    results: { type: 'return' | 'throw'; value: any }[];
  };
  mockImplementation: (impl: T) => MockFn<T>;
  mockReturnValue: (val: ReturnType<T>) => MockFn<T>;
  mockResolvedValue: (val: any) => MockFn<T>;
  mockRejectedValue: (err: any) => MockFn<T>;
  mockClear: () => MockFn<T>;
  mockReset: () => MockFn<T>;
}

function createMockFn<T extends (...args: any[]) => any = (...args: any[]) => any>(
  initialImpl?: T
): MockFn<T> {
  let currentImpl: T | undefined = initialImpl;
  const mockState = {
    calls: [] as Parameters<T>[],
    results: [] as { type: 'return' | 'throw'; value: any }[],
  };

  const fn = ((...args: Parameters<T>) => {
    mockState.calls.push(args);
    try {
      const res = currentImpl ? currentImpl(...args) : undefined;
      mockState.results.push({ type: 'return', value: res });
      return res;
    } catch (err) {
      mockState.results.push({ type: 'throw', value: err });
      throw err;
    }
  }) as MockFn<T>;

  fn.mock = mockState;
  fn.mockImplementation = (impl: T) => {
    currentImpl = impl;
    return fn;
  };
  fn.mockReturnValue = (val: ReturnType<T>) => {
    currentImpl = (() => val) as unknown as T;
    return fn;
  };
  fn.mockResolvedValue = (val: any) => {
    currentImpl = (() => Promise.resolve(val)) as unknown as T;
    return fn;
  };
  fn.mockRejectedValue = (err: any) => {
    currentImpl = (() => Promise.reject(err)) as unknown as T;
    return fn;
  };
  fn.mockClear = () => {
    mockState.calls.length = 0;
    mockState.results.length = 0;
    return fn;
  };
  fn.mockReset = () => {
    mockState.calls.length = 0;
    mockState.results.length = 0;
    currentImpl = undefined;
    return fn;
  };

  return fn;
}

export const vi = {
  fn: createMockFn,
  spyOn: <T extends object, K extends keyof T>(obj: T, key: K): MockFn<any> => {
    const original = obj[key];
    const mock = createMockFn(
      typeof original === 'function' ? (original as any).bind(obj) : undefined
    );
    (obj as any)[key] = mock;
    return mock;
  },
  clearAllMocks: () => {},
};

function deepMatches(actual: any, expected: any): boolean {
  if (Object.is(actual, expected)) return true;
  if (expected && typeof expected === 'object' && expected[OBJECT_CONTAINING_SYMBOL]) {
    if (!actual || typeof actual !== 'object') return false;
    const subset = expected.subset;
    for (const key of Object.keys(subset)) {
      if (!deepMatches(actual[key], subset[key])) return false;
    }
    return true;
  }
  if (actual instanceof Uint8Array && expected instanceof Uint8Array) {
    if (actual.byteLength !== expected.byteLength) return false;
    for (let i = 0; i < actual.byteLength; i++) {
      if (actual[i] !== expected[i]) return false;
    }
    return true;
  }
  if (Array.isArray(actual) && Array.isArray(expected)) {
    if (actual.length !== expected.length) return false;
    for (let i = 0; i < actual.length; i++) {
      if (!deepMatches(actual[i], expected[i])) return false;
    }
    return true;
  }
  if (
    actual &&
    expected &&
    typeof actual === 'object' &&
    typeof expected === 'object' &&
    !Array.isArray(actual) &&
    !Array.isArray(expected)
  ) {
    const actualKeys = Object.keys(actual).filter((k) => actual[k] !== undefined);
    const expectedKeys = Object.keys(expected).filter((k) => expected[k] !== undefined);
    if (actualKeys.length !== expectedKeys.length) return false;
    for (const key of expectedKeys) {
      if (!deepMatches(actual[key], expected[key])) return false;
    }
    return true;
  }
  return false;
}

function createMatchers(actual: any, isNot = false): any {
  const check = (pass: boolean, message: string) => {
    if (isNot ? pass : !pass) {
      throw new assert.AssertionError({
        message,
        actual,
      });
    }
  };

  const matchers = {
    toBe(expected: any) {
      check(
        Object.is(actual, expected),
        `Expected ${JSON.stringify(actual)} ${isNot ? 'not ' : ''}to be ${JSON.stringify(expected)}`
      );
    },
    toEqual(expected: any) {
      check(
        deepMatches(actual, expected),
        `Expected ${JSON.stringify(actual)} ${isNot ? 'not ' : ''}to equal ${JSON.stringify(expected)}`
      );
    },
    toContain(expected: any) {
      const pass =
        typeof actual === 'string' || Array.isArray(actual)
          ? actual.includes(expected)
          : false;
      check(
        pass,
        `Expected ${JSON.stringify(actual)} ${isNot ? 'not ' : ''}to contain ${JSON.stringify(expected)}`
      );
    },
    toHaveLength(expected: number) {
      const len = actual?.length;
      check(
        len === expected,
        `Expected length ${len} ${isNot ? 'not ' : ''}to be ${expected}`
      );
    },
    toBeDefined() {
      check(actual !== undefined, `Expected value ${isNot ? 'not ' : ''}to be defined`);
    },
    toBeUndefined() {
      check(actual === undefined, `Expected value ${isNot ? 'not ' : ''}to be undefined`);
    },
    toBeNull() {
      check(actual === null, `Expected ${JSON.stringify(actual)} ${isNot ? 'not ' : ''}to be null`);
    },
    toBeTruthy() {
      check(Boolean(actual), `Expected ${JSON.stringify(actual)} ${isNot ? 'not ' : ''}to be truthy`);
    },
    toBeFalsy() {
      check(!actual, `Expected ${JSON.stringify(actual)} ${isNot ? 'not ' : ''}to be falsy`);
    },
    toBeGreaterThan(expected: number) {
      check(
        typeof actual === 'number' && actual > expected,
        `Expected ${actual} ${isNot ? 'not ' : ''}to be greater than ${expected}`
      );
    },
    toBeGreaterThanOrEqual(expected: number) {
      check(
        typeof actual === 'number' && actual >= expected,
        `Expected ${actual} ${isNot ? 'not ' : ''}to be >= ${expected}`
      );
    },
    toBeLessThan(expected: number) {
      check(
        typeof actual === 'number' && actual < expected,
        `Expected ${actual} ${isNot ? 'not ' : ''}to be less than ${expected}`
      );
    },
    toBeCloseTo(expected: number, precision = 2) {
      const diff = Math.abs(actual - expected);
      const maxDiff = Math.pow(10, -precision) / 2;
      check(
        diff < maxDiff,
        `Expected ${actual} ${isNot ? 'not ' : ''}to be close to ${expected} (precision ${precision})`
      );
    },
    toHaveBeenCalled() {
      const calls = actual?.mock?.calls;
      if (!Array.isArray(calls)) {
        throw new Error('Expected a mock function');
      }
      check(calls.length > 0, `Expected mock function ${isNot ? 'not ' : ''}to have been called`);
    },
    toHaveBeenCalledTimes(expected: number) {
      const calls = actual?.mock?.calls;
      if (!Array.isArray(calls)) {
        throw new Error('Expected a mock function');
      }
      check(
        calls.length === expected,
        `Expected mock function ${isNot ? 'not ' : ''}to have been called ${expected} times, but was called ${calls.length} times`
      );
    },
    toHaveBeenCalledWith(...expectedArgs: any[]) {
      const calls: any[][] | undefined = actual?.mock?.calls;
      if (!Array.isArray(calls)) {
        throw new Error('Expected a mock function');
      }
      const matched = calls.some((callArgs) => deepMatches(callArgs, expectedArgs));
      check(
        matched,
        `Expected mock function ${isNot ? 'not ' : ''}to have been called with ${JSON.stringify(expectedArgs)}, actual calls: ${JSON.stringify(calls)}`
      );
    },
    toThrow(expected?: string | RegExp | Error) {
      let threw = false;
      let thrownError: any = null;
      try {
        actual();
      } catch (err) {
        threw = true;
        thrownError = err;
      }
      let pass = threw;
      if (threw && expected !== undefined) {
        const msg = thrownError instanceof Error ? thrownError.message : String(thrownError);
        if (typeof expected === 'string') {
          pass = msg.includes(expected);
        } else if (expected instanceof RegExp) {
          pass = expected.test(msg);
        } else if (expected instanceof Error) {
          pass = msg.includes(expected.message);
        }
      }
      check(
        pass,
        `Expected function ${isNot ? 'not ' : ''}to throw ${expected ? String(expected) : ''}, got ${thrownError}`
      );
    },
    toThrowError(expected?: string | RegExp | Error) {
      matchers.toThrow(expected);
    },
  };

  return {
    ...matchers,
    get not() {
      return createMatchers(actual, !isNot);
    },
    get resolves() {
      return {
        async toBe(expected: any) {
          const val = await actual;
          createMatchers(val, isNot).toBe(expected);
        },
        async toEqual(expected: any) {
          const val = await actual;
          createMatchers(val, isNot).toEqual(expected);
        },
      };
    },
    get rejects() {
      return {
        async toThrow(expected?: string | RegExp | Error) {
          let threw = false;
          let thrownError: any = null;
          try {
            await actual;
          } catch (err) {
            threw = true;
            thrownError = err;
          }
          createMatchers(() => {
            if (threw) throw thrownError;
          }, isNot).toThrow(expected);
        },
        async toThrowError(expected?: string | RegExp | Error) {
          return this.toThrow(expected);
        },
      };
    },
  };
}

export function expect(actual: any) {
  return createMatchers(actual, false);
}

expect.objectContaining = (subset: Record<string, any>) => ({
  [OBJECT_CONTAINING_SYMBOL]: true,
  subset,
});
