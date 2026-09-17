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
import { PostureClient, PostureType } from '../src/core/posture';
import { PostureState, UpdatePostureStateRequest } from '@android/emulator-services/environment/posture_service';

describe('PostureClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('calls GetPostureState RPC correctly', async () => {
    const client = new PostureClient(mockTransport);

    const mockResponse: PostureState = {
      posture: PostureType.POSTURE_TYPE_HALF_OPENED,
      hingeAngleDegrees: 90.0,
      timestampNanos: 123456789,
    };
    mockTransport.request.mockResolvedValue(PostureState.encode(mockResponse).finish());

    const result = await client.getPostureState();
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.PostureService');
    expect(method).toBe('GetPostureState');
    expect(result.posture).toBe(PostureType.POSTURE_TYPE_HALF_OPENED);
    expect(result.hingeAngleDegrees).toBe(90.0);
  });

  it('setHingeAngle sends hinge_angle_degrees update with field mask', async () => {
    const client = new PostureClient(mockTransport);

    const mockResponse: PostureState = {
      posture: PostureType.POSTURE_TYPE_OPENED,
      hingeAngleDegrees: 180.0,
      timestampNanos: 0,
    };
    mockTransport.request.mockResolvedValue(PostureState.encode(mockResponse).finish());

    const result = await client.setHingeAngle(180.0);
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.PostureService');
    expect(method).toBe('UpdatePostureState');

    const decoded = UpdatePostureStateRequest.decode(data);
    expect(decoded.postureState?.hingeAngleDegrees).toBe(180.0);
    expect(decoded.updateMask).toContain('hinge_angle_degrees');
    expect(result.hingeAngleDegrees).toBe(180.0);
  });

  it('setDevicePosture updates posture type and field mask', async () => {
    const client = new PostureClient(mockTransport);

    mockTransport.request.mockResolvedValue(
      PostureState.encode({
        posture: PostureType.POSTURE_TYPE_CLOSED,
        hingeAngleDegrees: 0.0,
        timestampNanos: 0,
      }).finish()
    );

    const result = await client.setDevicePosture(PostureType.POSTURE_TYPE_CLOSED);
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [, , data] = mockTransport.request.mock.calls[0];
    const decoded = UpdatePostureStateRequest.decode(data);
    expect(result.posture).toBe(PostureType.POSTURE_TYPE_CLOSED);
  });

  it('streamPostureState subscribes to live updates via transport.stream', () => {
    mockTransport.stream = vi.fn();
    const client = new PostureClient(mockTransport);

    const onState = vi.fn();
    client.streamPostureState(onState);

    expect(mockTransport.stream).toHaveBeenCalledTimes(1);
    const [service, method, , onMsg] = mockTransport.stream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.PostureService');
    expect(method).toBe('StreamPostureState');

    const sampleState: PostureState = {
      posture: PostureType.POSTURE_TYPE_HALF_OPENED,
      hingeAngleDegrees: 90.0,
      timestampNanos: 123456789,
    };
    onMsg(PostureState.encode(sampleState).finish());
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ hingeAngleDegrees: 90.0 }));
  });
});