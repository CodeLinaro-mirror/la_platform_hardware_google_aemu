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

/**
 * Dynamic authentication token supplier.
 * Can be a static JWT string, an async token getter, or a Promise.
 */
export type AuthProvider = string | (() => string | Promise<string>);

/**
 * Resolves the token from an AuthProvider if specified.
 */
export async function resolveToken(provider?: AuthProvider): Promise<string | undefined> {
  if (!provider) {
    return undefined;
  }
  if (typeof provider === 'function') {
    return await provider();
  }
  return provider;
}

/**
 * Base error class for all Device exceptions.
 */
export class DeviceError extends Error {
  public readonly statusCode?: number;

  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = 'DeviceError';
    this.statusCode = statusCode;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

// Backward compatibility alias
export { DeviceError as AquariumError };

/**
 * Raised when authentication fails (HTTP 401 or gRPC UNAUTHENTICATED / 16).
 */
export class AuthenticationError extends DeviceError {
  constructor(message = 'Authentication failed: Invalid or expired JWT token') {
    super(message, 16);
    this.name = 'AuthenticationError';
    Object.setPrototypeOf(this, new.target.prototype);
  }
}
