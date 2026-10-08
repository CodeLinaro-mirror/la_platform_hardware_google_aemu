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
 * Stops a subscription started by {@link subscribe}. Safe to call more than
 * once, and safe to call after the stream has already ended.
 */
export type Unsubscribe = () => void;

/**
 * Adapts a server-streaming RPC to the callback shape React components want.
 *
 * The generated `*ClientImpl` methods return an `AsyncIterable`, which is the
 * right primitive but an awkward fit for `useEffect`, whose cleanup function
 * must be synchronous. This bridges the two.
 *
 * Cancellation goes through the `AbortSignal` rather than through the
 * iterator's `return()`. That is not a stylistic choice: the iterator spends
 * almost all of its time suspended awaiting the next message, and `return()`
 * on a suspended async generator is queued behind that pending `await`. It
 * would therefore not take effect until the server happened to send something,
 * which for an idle stream may be never.
 *
 * @param open Starts the stream. Pass the signal to the generated method:
 *     `(signal) => client.StreamVmState({}, signal)`.
 * @param onMessage Invoked for each decoded message.
 * @param onError Invoked if the stream fails. Not invoked on unsubscribe.
 */
export function subscribe<T>(
  open: (signal: AbortSignal) => AsyncIterable<T>,
  onMessage: (message: T) => void,
  onError?: (error: unknown) => void
): Unsubscribe {
  const controller = new AbortController();

  void (async () => {
    try {
      for await (const message of open(controller.signal)) {
        if (controller.signal.aborted) {
          return;
        }
        onMessage(message);
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        onError?.(error);
      }
    }
  })();

  return () => controller.abort();
}
