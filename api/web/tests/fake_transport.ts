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

import { GrpcWebTransport } from '../src/core/transport';

/** One RPC invocation seen by {@link FakeTransport}. */
export interface RecordedCall {
  service: string;
  method: string;
  data: Uint8Array;
  signal?: AbortSignal;
}

/**
 * A server stream the test drives by hand.
 *
 * Messages pushed before the consumer asks for them are buffered, so a test can
 * push and then await without racing.
 */
export class FakeStream {
  private readonly queue: Uint8Array[] = [];
  private wake: (() => void) | null = null;
  private ended = false;
  private failure: unknown;

  /** Delivers one encoded message to the consumer. */
  public push(bytes: Uint8Array): void {
    this.queue.push(bytes);
    this.notify();
  }

  /** Completes the stream normally. */
  public end(): void {
    this.ended = true;
    this.notify();
  }

  /** Completes the stream with an error. */
  public fail(error: unknown): void {
    this.failure = error;
    this.notify();
  }

  private notify(): void {
    if (this.wake) {
      const wake = this.wake;
      this.wake = null;
      wake();
    }
  }

  /** @internal */
  public iterate(signal?: AbortSignal): AsyncIterable<Uint8Array> {
    const self = this;
    signal?.addEventListener('abort', () => self.end(), { once: true });

    return {
      async *[Symbol.asyncIterator]() {
        for (;;) {
          if (self.queue.length > 0) {
            yield self.queue.shift()!;
            continue;
          }
          if (self.failure) {
            throw self.failure;
          }
          if (self.ended || signal?.aborted) {
            return;
          }
          await new Promise<void>((resolve) => {
            self.wake = resolve;
          });
        }
      },
    };
  }
}

/**
 * Stands in for {@link GrpcWebTransport} in client tests.
 *
 * It implements the same four members the generated `*ClientImpl` classes call,
 * so a test exercises the real generated encode/decode path and only the
 * network is faked. That is deliberate: asserting on the recorded service and
 * method names is then an assertion about what the `.proto` declares.
 */
export class FakeTransport {
  /** Every unary call, in order. */
  public readonly calls: RecordedCall[] = [];

  /** Every server-streaming call, in order. */
  public readonly streamCalls: RecordedCall[] = [];

  private unaryResponse: Uint8Array = new Uint8Array();
  private unaryError: unknown;
  private readonly streams: FakeStream[] = [];

  /** The reply returned by the next unary call. */
  public respondWith(bytes: Uint8Array): void {
    this.unaryResponse = bytes;
    this.unaryError = undefined;
  }

  /** Makes the next unary call reject. */
  public failWith(error: unknown): void {
    this.unaryError = error;
  }

  /** The stream opened by the n-th server-streaming call. */
  public stream(index = 0): FakeStream {
    while (this.streams.length <= index) {
      this.streams.push(new FakeStream());
    }
    return this.streams[index];
  }

  /** The most recent unary call. Throws if there was none. */
  public get lastCall(): RecordedCall {
    if (this.calls.length === 0) {
      throw new Error('No unary call was made');
    }
    return this.calls[this.calls.length - 1];
  }

  public async request(
    service: string,
    method: string,
    data: Uint8Array,
    signal?: AbortSignal
  ): Promise<Uint8Array> {
    this.calls.push({ service, method, data, signal });
    if (this.unaryError) {
      throw this.unaryError;
    }
    return this.unaryResponse;
  }

  public serverStreamingRequest(
    service: string,
    method: string,
    data: Uint8Array,
    signal?: AbortSignal
  ): AsyncIterable<Uint8Array> {
    const index = this.streamCalls.length;
    this.streamCalls.push({ service, method, data, signal });
    return this.stream(index).iterate(signal);
  }

  public clientStreamingRequest(): Promise<Uint8Array> {
    return Promise.reject(new Error('client streaming is not supported over gRPC-Web'));
  }

  public bidirectionalStreamingRequest(): AsyncIterable<Uint8Array> {
    return {
      // eslint-disable-next-line require-yield
      async *[Symbol.asyncIterator]() {
        throw new Error('bidirectional streaming is not supported over gRPC-Web');
      },
    };
  }

  /** The clients are typed against the concrete transport; this narrows it. */
  public asTransport(): GrpcWebTransport {
    return this as unknown as GrpcWebTransport;
  }
}
