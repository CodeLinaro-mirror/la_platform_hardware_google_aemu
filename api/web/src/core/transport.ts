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

import { AuthProvider, resolveToken, DeviceError, AuthenticationError } from './auth';

/**
 * Options for configuring GrpcWebTransport.
 */
export interface GrpcWebTransportOptions {
  /**
   * The base HTTP/HTTPS URL of the gRPC-Web proxy (e.g., "http://localhost:8080").
   */
  endpoint: string;

  /**
   * Optional static JWT token or dynamic token provider callback.
   */
  authProvider?: AuthProvider;

  /**
   * Optional extra HTTP headers to send on every request.
   */
  headers?: Record<string, string>;

  /**
   * Custom fetch function (useful for tests or specific polyfills).
   */
  fetch?: typeof fetch;
}

/**
 * Generic gRPC-Web HTTP/1.1 client transport implementing ts-proto Rpc interface.
 */
export class GrpcWebTransport {
  private readonly endpoint: string;
  private readonly authProvider?: AuthProvider;
  private readonly customHeaders: Record<string, string>;
  private readonly fetchFn: typeof fetch;

  constructor(options: GrpcWebTransportOptions) {
    // Strip trailing slash from endpoint
    this.endpoint = options.endpoint.replace(/\/+$/, '');
    this.authProvider = options.authProvider;
    this.customHeaders = options.headers || {};
    if (options.fetch) {
      this.fetchFn = options.fetch;
    } else if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
      this.fetchFn = window.fetch.bind(window);
    } else if (typeof globalThis !== 'undefined' && typeof globalThis.fetch === 'function') {
      this.fetchFn = globalThis.fetch.bind(globalThis);
    } else {
      this.fetchFn = (typeof fetch !== 'undefined' ? fetch : (undefined as any));
    }
  }

  /**
   * Sends a unary gRPC request.
   */
  public async request(service: string, method: string, data: Uint8Array): Promise<Uint8Array> {
    const url = `${this.endpoint}/${service}/${method}`;

    // Frame the request (1 byte flags + 4 bytes big endian length + data)
    const framed = new Uint8Array(5 + data.length);
    framed[0] = 0x00; // Uncompressed
    const view = new DataView(framed.buffer, framed.byteOffset, framed.byteLength);
    view.setUint32(1, data.length, false);
    framed.set(data, 5);

    const headers = new Headers(this.customHeaders);
    headers.set('content-type', 'application/grpc-web+proto');
    headers.set('x-grpc-web', '1');
    headers.set('x-user-agent', 'android-web/0.1.0');

    // Injected JWT authentication if configured
    const token = await resolveToken(this.authProvider);
    if (token) {
      headers.set('authorization', `Bearer ${token}`);
    }

    const response = await this.fetchFn(url, {
      method: 'POST',
      headers,
      body: framed,
    });

    if (response.status === 401) {
      throw new AuthenticationError('Unauthorized: Invalid or expired JWT token (HTTP 401)');
    }

    if (!response.ok) {
      throw new DeviceError(`HTTP error ${response.status}: ${response.statusText}`, response.status);
    }

    // Check for gRPC status in headers
    const grpcStatus = response.headers.get('grpc-status');
    const grpcMessage = response.headers.get('grpc-message') || '';

    if (grpcStatus !== null && grpcStatus !== '0') {
      const statusCode = parseInt(grpcStatus, 10);
      if (statusCode === 16) {
        throw new AuthenticationError(`gRPC UNAUTHENTICATED: ${grpcMessage}`);
      }
      throw new DeviceError(`gRPC error (${statusCode}): ${grpcMessage}`, statusCode);
    }

    const buffer = await response.arrayBuffer();
    const bytes = new Uint8Array(buffer);

    return this.parseGrpcWebFrames(bytes);
  }

  /**
   * Subscribes to a server-streaming gRPC request using fetch and ReadableStream reader.
   * Returns an abort/cancel function.
   */
  public stream(
    service: string,
    method: string,
    data: Uint8Array,
    onMessage: (msg: Uint8Array) => void,
    onError?: (err: any) => void,
    onEnd?: () => void
  ): () => void {
    const url = `${this.endpoint}/${service}/${method}`;
    const controller = new AbortController();

    const framed = new Uint8Array(5 + data.length);
    framed[0] = 0x00;
    const view = new DataView(framed.buffer, framed.byteOffset, framed.byteLength);
    view.setUint32(1, data.length, false);
    framed.set(data, 5);

    const headers = new Headers(this.customHeaders);
    headers.set('content-type', 'application/grpc-web+proto');
    headers.set('x-grpc-web', '1');
    headers.set('x-user-agent', 'android-web/0.1.0');

    (async () => {
      try {
        const token = await resolveToken(this.authProvider);
        if (token) {
          headers.set('authorization', `Bearer ${token}`);
        }

        const response = await this.fetchFn(url, {
          method: 'POST',
          headers,
          body: framed,
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new DeviceError(`HTTP error ${response.status}: ${response.statusText}`, response.status);
        }

        if (!response.body) {
          onEnd?.();
          return;
        }

        const reader = response.body.getReader();
        let buffer = new Uint8Array(0);

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const newBuffer = new Uint8Array(buffer.length + value.length);
          newBuffer.set(buffer, 0);
          newBuffer.set(value, buffer.length);
          buffer = newBuffer;

          while (buffer.length >= 5) {
            const flag = buffer[0];
            const v = new DataView(buffer.buffer, buffer.byteOffset, 5);
            const len = v.getUint32(1, false);
            if (buffer.length < 5 + len) {
              break;
            }

            const payload = buffer.slice(5, 5 + len);
            buffer = buffer.slice(5 + len);

            if (flag === 0x00) {
              onMessage(payload);
            } else if (flag === 0x80) {
              const trailersText = new TextDecoder().decode(payload);
              this.checkTrailers(trailersText);
            }
          }
        }

        onEnd?.();
      } catch (err: any) {
        if (controller.signal.aborted) {
          return;
        }
        onError?.(err);
      }
    })();

    return () => {
      controller.abort();
    };
  }

  /**
   * Subscribes to a server-streaming gRPC request, yielding decoded Uint8Array messages as an AsyncIterable.
   */
  public async *serverStream(
    service: string,
    method: string,
    data: Uint8Array
  ): AsyncIterable<Uint8Array> {
    const queue: Uint8Array[] = [];
    let error: any = null;
    let done = false;
    let notify: (() => void) | null = null;

    const cancel = this.stream(
      service,
      method,
      data,
      (msg) => {
        queue.push(msg);
        if (notify) {
          notify();
          notify = null;
        }
      },
      (err) => {
        error = err;
        if (notify) {
          notify();
          notify = null;
        }
      },
      () => {
        done = true;
        if (notify) {
          notify();
          notify = null;
        }
      }
    );

    try {
      while (!done || queue.length > 0) {
        if (queue.length > 0) {
          yield queue.shift()!;
        } else if (error) {
          throw error;
        } else {
          await new Promise<void>((resolve) => {
            notify = resolve;
          });
        }
      }
    } finally {
      cancel();
    }
  }

  /**
   * Unpacks gRPC-Web response frames.
   */
  private parseGrpcWebFrames(bytes: Uint8Array): Uint8Array {
    let offset = 0;
    let payload = new Uint8Array(0);

    while (offset + 5 <= bytes.length) {
      const flag = bytes[offset];
      const view = new DataView(bytes.buffer, bytes.byteOffset + offset, 5);
      const length = view.getUint32(1, false);
      offset += 5;

      if (offset + length > bytes.length) {
        break;
      }

      const chunk = bytes.slice(offset, offset + length);
      offset += length;

      if (flag === 0x00) {
        // Data frame
        payload = chunk;
      } else if (flag === 0x80) {
        // Trailers frame (contains grpc-status, grpc-message)
        const trailersText = new TextDecoder().decode(chunk);
        this.checkTrailers(trailersText);
      }
    }

    return payload;
  }

  private checkTrailers(trailersText: string) {
    const lines = trailersText.split('\r\n');
    let status = 0;
    let message = '';

    for (const line of lines) {
      const colonIndex = line.indexOf(':');
      if (colonIndex === -1) continue;
      const key = line.slice(0, colonIndex).trim().toLowerCase();
      const val = line.slice(colonIndex + 1).trim();
      if (key === 'grpc-status') {
        status = parseInt(val, 10);
      } else if (key === 'grpc-message') {
        message = decodeURIComponent(val);
      }
    }

    if (status !== 0) {
      if (status === 16) {
        throw new AuthenticationError(`gRPC UNAUTHENTICATED: ${message}`);
      }
      throw new DeviceError(`gRPC error (${status}): ${message}`, status);
    }
  }
}
