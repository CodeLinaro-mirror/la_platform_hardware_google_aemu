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

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PostureClient, PostureType, TransitionMode } from '../src/core/posture';
import {
  PostureState,
  UpdatePostureStateRequest,
} from '@android/emulator-services/sensors_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.SensorsService';

describe('PostureClient', () => {
  let transport: FakeTransport;
  let client: PostureClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new PostureClient(transport.asTransport());
  });

  function encodeState(state: Partial<PostureState>): Uint8Array {
    return PostureState.encode(PostureState.fromPartial(state)).finish();
  }

  it('calls GetPostureState and decodes multi-hinge & rollable reply', async () => {
    transport.respondWith(
      encodeState({
        posture: PostureType.POSTURE_TYPE_HALF_OPENED,
        hinges: [
          { hingeId: 0, angleDegrees: 90.0 },
          { hingeId: 1, angleDegrees: 180.0 },
        ],
        rollables: [{ rollableId: 0, extensionPercent: 75.0 }],
        timestampNanos: 123456789,
      })
    );

    const result = await client.GetPostureState();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetPostureState');
    expect(result.posture).toBe(PostureType.POSTURE_TYPE_HALF_OPENED);
    expect(result.hinges).toEqual([
      { hingeId: 0, angleDegrees: 90.0 },
      { hingeId: 1, angleDegrees: 180.0 },
    ]);
    expect(result.rollables).toEqual([{ rollableId: 0, extensionPercent: 75.0 }]);
  });

  it('sends multi-hinge angles and transition mode on the wire', async () => {
    transport.respondWith(
      encodeState({
        posture: PostureType.POSTURE_TYPE_OPENED,
        hinges: [{ hingeId: 0, angleDegrees: 180.0 }],
      })
    );

    const result = await client.UpdatePostureState({
      hinges: [{ hingeId: 0, angleDegrees: 180.0 }],
      transition: TransitionMode.TRANSITION_MODE_SMOOTH,
    });

    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('UpdatePostureState');
    const request = UpdatePostureStateRequest.decode(transport.lastCall.data);
    expect(request.hinges).toEqual([{ hingeId: 0, angleDegrees: 180.0 }]);
    expect(request.transition).toBe(TransitionMode.TRANSITION_MODE_SMOOTH);
    expect(request.posture).toBeUndefined();
    expect(result.hinges[0].angleDegrees).toBe(180.0);
  });

  it('sends only the posture when only the posture is updated', async () => {
    transport.respondWith(encodeState({ posture: PostureType.POSTURE_TYPE_CLOSED }));

    const result = await client.UpdatePostureState({
      posture: PostureType.POSTURE_TYPE_CLOSED,
    });

    const request = UpdatePostureStateRequest.decode(transport.lastCall.data);
    expect(request.posture).toBe(PostureType.POSTURE_TYPE_CLOSED);
    expect(request.hinges).toHaveLength(0);
    expect(result.posture).toBe(PostureType.POSTURE_TYPE_CLOSED);
  });

  it('decodes each state pushed by StreamPostureState', async () => {
    const onState = vi.fn();

    const unsubscribe = client.subscribePostureState(onState);
    transport.stream().push(
      encodeState({
        posture: PostureType.POSTURE_TYPE_HALF_OPENED,
        hinges: [{ hingeId: 0, angleDegrees: 90.0 }],
        timestampNanos: 123456789,
      })
    );
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamPostureState');
    expect(onState).toHaveBeenCalledWith(
      expect.objectContaining({
        hinges: [{ hingeId: 0, angleDegrees: 90.0 }],
      })
    );

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribePostureState(() => {});
    await settle();

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}