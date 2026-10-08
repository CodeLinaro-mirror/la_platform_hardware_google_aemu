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

import { describe, it, expect, vi } from 'vitest';
import { GrpcWebTransport } from '../src/core/transport';
import { AuthenticationError, DeviceError } from '../src/core/auth';

describe('GrpcWebTransport & AuthProvider (TDD)', () => {
  const dummyPayload = new Uint8Array([1, 2, 3, 4]);

  it('sends unary request without authorization header when unauthenticated', async () => {
    let capturedHeaders: HeadersInit | undefined;

    const mockFetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedHeaders = init?.headers;
      // Return a valid gRPC-Web framed response (0x00 + 4-byte length + payload)
      const respBuffer = new Uint8Array([0x00, 0x00, 0x00, 0x00, 0x02, 0xAA, 0xBB]);
      return new Response(respBuffer, {
        status: 200,
        headers: {
          'content-type': 'application/grpc-web+proto',
          'grpc-status': '0',
        },
      });
    });

    const transport = new GrpcWebTransport({
      endpoint: 'http://localhost:8080',
      fetch: mockFetch as any,
    });

    const response = await transport.request('TestService', 'TestMethod', dummyPayload);
    expect(response).toEqual(new Uint8Array([0xAA, 0xBB]));
    expect(mockFetch).toHaveBeenCalledTimes(1);

    const headers = new Headers(capturedHeaders);
    expect(headers.get('authorization')).toBeNull();
    expect(headers.get('content-type')).toBe('application/grpc-web+proto');
    expect(headers.get('x-grpc-web')).toBe('1');
  });

  it('attaches static string JWT token as Bearer header', async () => {
    let capturedHeaders: HeadersInit | undefined;

    const mockFetch = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
      capturedHeaders = init?.headers;
      const respBuffer = new Uint8Array([0x00, 0x00, 0x00, 0x00, 0x01, 0x42]);
      return new Response(respBuffer, {
        status: 200,
        headers: {
          'content-type': 'application/grpc-web+proto',
          'grpc-status': '0',
        },
      });
    });

    const transport = new GrpcWebTransport({
      endpoint: 'http://localhost:8080',
      authProvider: 'my-jwt-token-123',
      fetch: mockFetch as any,
    });

    await transport.request('TestService', 'TestMethod', dummyPayload);
    const headers = new Headers(capturedHeaders);
    expect(headers.get('authorization')).toBe('Bearer my-jwt-token-123');
  });

  it('invokes dynamic async AuthProvider function per request', async () => {
    let tokenCounter = 0;
    const tokenProvider = vi.fn().mockImplementation(async () => {
      tokenCounter++;
      return `dynamic-token-${tokenCounter}`;
    });

    let lastAuthHeader = '';
    const mockFetch = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      lastAuthHeader = headers.get('authorization') || '';
      const respBuffer = new Uint8Array([0x00, 0x00, 0x00, 0x00, 0x00]);
      return new Response(respBuffer, {
        status: 200,
        headers: {
          'content-type': 'application/grpc-web+proto',
          'grpc-status': '0',
        },
      });
    });

    const transport = new GrpcWebTransport({
      endpoint: 'http://localhost:8080',
      authProvider: tokenProvider,
      fetch: mockFetch as any,
    });

    await transport.request('TestService', 'Method1', dummyPayload);
    expect(lastAuthHeader).toBe('Bearer dynamic-token-1');
    expect(tokenProvider).toHaveBeenCalledTimes(1);

    await transport.request('TestService', 'Method2', dummyPayload);
    expect(lastAuthHeader).toBe('Bearer dynamic-token-2');
    expect(tokenProvider).toHaveBeenCalledTimes(2);
  });

  it('throws AuthenticationError when server returns HTTP 401 Unauthorized', async () => {
    const mockFetch = vi.fn().mockImplementation(async () => {
      return new Response('Unauthorized token', {
        status: 401,
        statusText: 'Unauthorized',
      });
    });

    const transport = new GrpcWebTransport({
      endpoint: 'http://localhost:8080',
      authProvider: 'expired-token',
      fetch: mockFetch as any,
    });

    await expect(transport.request('TestService', 'TestMethod', dummyPayload)).rejects.toThrow(
      AuthenticationError
    );
  });

  it('throws AuthenticationError when server returns grpc-status 16 (UNAUTHENTICATED)', async () => {
    const mockFetch = vi.fn().mockImplementation(async () => {
      return new Response(new Uint8Array([]), {
        status: 200,
        headers: {
          'content-type': 'application/grpc-web+proto',
          'grpc-status': '16',
          'grpc-message': 'Token has expired',
        },
      });
    });

    const transport = new GrpcWebTransport({
      endpoint: 'http://localhost:8080',
      authProvider: 'expired-token',
      fetch: mockFetch as any,
    });

    await expect(transport.request('TestService', 'TestMethod', dummyPayload)).rejects.toThrow(
      AuthenticationError
    );
  });

  it('throws DeviceError with descriptive status message on general gRPC error', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([]), {
        status: 200,
        headers: {
          'content-type': 'application/grpc-web+proto',
          'grpc-status': '13',
          'grpc-message': 'Internal Server Error',
        },
      })
    );

    const transport = new GrpcWebTransport({
      endpoint: 'http://localhost:8080',
      fetch: mockFetch as any,
    });

    await expect(
      transport.request('android.emulation.v2.VmService', 'Pause', dummyPayload)
    ).rejects.toThrowError(
      DeviceError
    );
  });
});
