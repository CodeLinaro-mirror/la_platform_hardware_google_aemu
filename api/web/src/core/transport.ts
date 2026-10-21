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
   *
   * @param abortSignal Cancels the in-flight request. Supplied by the generated
   *     `*ClientImpl` methods, which take it as their second argument.
   */
  public async request(
    service: string,
    method: string,
    data: Uint8Array,
    abortSignal?: AbortSignal
  ): Promise<Uint8Array> {
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
      signal: abortSignal,
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
   *
   * @param abortSignal Tears the stream down from the outside. Aborting is the
   *     only way to stop a stream promptly: an async iterator parked on its
   *     next message cannot be woken by `return()`.
   */
  public stream(
    service: string,
    method: string,
    data: Uint8Array,
    onMessage: (msg: Uint8Array) => void,
    onError?: (err: any) => void,
    onEnd?: () => void,
    abortSignal?: AbortSignal
  ): () => void {
    const url = `${this.endpoint}/${service}/${method}`;
    const controller = new AbortController();

    if (abortSignal) {
      if (abortSignal.aborted) {
        controller.abort();
      } else {
        abortSignal.addEventListener('abort', () => controller.abort(), { once: true });
      }
    }

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
   *
   * @param abortSignal Ends the iteration. Without it the loop below parks on
   *     `notify` until the server sends something, so a consumer that walks
   *     away would hold the HTTP stream open indefinitely.
   */
  public async *serverStream(
    service: string,
    method: string,
    data: Uint8Array,
    abortSignal?: AbortSignal
  ): AsyncIterable<Uint8Array> {
    const queue: Uint8Array[] = [];
    let error: any = null;
    let done = false;
    let notify: (() => void) | null = null;

    const wake = () => {
      if (notify) {
        notify();
        notify = null;
      }
    };

    const cancel = this.stream(
      service,
      method,
      data,
      (msg) => {
        queue.push(msg);
        wake();
      },
      (err) => {
        error = err;
        wake();
      },
      () => {
        done = true;
        wake();
      },
      abortSignal
    );

    // An aborted fetch reports neither an error nor an end, so the abort has to
    // release the loop itself.
    abortSignal?.addEventListener(
      'abort',
      () => {
        done = true;
        queue.length = 0;
        wake();
      },
      { once: true }
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
   * Alias for {@link serverStream} matching the member name the ts-proto
   * generated `*ClientImpl` classes expect, so they can be constructed directly
   * from this transport.
   */
  public serverStreamingRequest(
    service: string,
    method: string,
    data: Uint8Array,
    abortSignal?: AbortSignal
  ): AsyncIterable<Uint8Array> {
    return this.serverStream(service, method, data, abortSignal);
  }

  /**
   * Present only to satisfy the generated client contract. gRPC-Web frames a
   * single request message and has no way to keep the request body open, so
   * client-streaming RPCs cannot be carried. Callers reaching one of these have
   * picked a method that needs a Node or Electron transport.
   */
  public clientStreamingRequest(
    service: string,
    method: string,
    _data: AsyncIterable<Uint8Array>
  ): Promise<Uint8Array> {
    return Promise.reject(this.unsupportedStreaming(service, method, 'Client-streaming'));
  }

  /** See {@link clientStreamingRequest}. */
  public bidirectionalStreamingRequest(
    service: string,
    method: string,
    _data: AsyncIterable<Uint8Array>
  ): AsyncIterable<Uint8Array> {
    const error = this.unsupportedStreaming(service, method, 'Bidirectional-streaming');
    return {
      // eslint-disable-next-line require-yield
      async *[Symbol.asyncIterator]() {
        throw error;
      },
    };
  }

  private unsupportedStreaming(service: string, method: string, kind: string): DeviceError {
    return new DeviceError(
      `${kind} RPC ${service}/${method} is not supported over gRPC-Web (UNIMPLEMENTED)`,
      12
    );
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
