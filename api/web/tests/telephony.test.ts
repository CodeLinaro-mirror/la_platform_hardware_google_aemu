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
import {
  TelephonyClient,
  RadioState_Technology,
  RadioState_Strength,
  RadioState_Registration,
  SimulateIncomingCallRequest_Action,
} from '../src/core/telephony';
import {
  RadioState,
  SendSmsRequest,
  SimulateIncomingCallRequest,
  UpdateRadioStateRequest,
} from '@android/emulator-services/environment/telephony_service';

describe('TelephonyClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('getRadioState decodes radio status', async () => {
    const client = new TelephonyClient(mockTransport);

    const mockRadio: RadioState = {
      radioPowered: true,
      activeTechnology: RadioState_Technology.LTE,
      signalStrength: RadioState_Strength.GREAT,
      registrationState: RadioState_Registration.HOME_NETWORK,
    };
    mockTransport.request.mockResolvedValue(RadioState.encode(mockRadio).finish());

    const result = await client.getRadioState();
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.TelephonyService');
    expect(method).toBe('GetRadioState');
    expect(result.activeTechnology).toBe(RadioState_Technology.LTE);
    expect(result.radioPowered).toBe(true);
  });

  it('updateRadioState sends updateMask and state values', async () => {
    const client = new TelephonyClient(mockTransport);

    mockTransport.request.mockResolvedValue(
      RadioState.encode({
        radioPowered: false,
        activeTechnology: RadioState_Technology.NR5G_SUB6,
        signalStrength: RadioState_Strength.POOR,
        registrationState: RadioState_Registration.ROAMING,
      }).finish()
    );

    const result = await client.updateRadioState({
      radioPowered: false,
      activeTechnology: RadioState_Technology.NR5G_SUB6,
    });

    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [, , data] = mockTransport.request.mock.calls[0];
    const decoded = UpdateRadioStateRequest.decode(data);
    expect(decoded.updateMask).toContain('radio_powered');
    expect(decoded.updateMask).toContain('active_technology');
    expect(result.radioPowered).toBe(false);
  });

  it('sendSms encodes sender and message body', async () => {
    const client = new TelephonyClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.sendSms('+16505551234', 'Verification code: 4242');
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.TelephonyService');
    expect(method).toBe('SendSms');

    const decoded = SendSmsRequest.decode(data);
    expect(decoded.senderPhoneNumber).toBe('+16505551234');
    expect(decoded.messageBody).toBe('Verification code: 4242');
  });

  it('simulateIncomingCall encodes caller and action', async () => {
    const client = new TelephonyClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.simulateIncomingCall('+18005550199', SimulateIncomingCallRequest_Action.RING);
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.TelephonyService');
    expect(method).toBe('SimulateIncomingCall');

    const decoded = SimulateIncomingCallRequest.decode(data);
    expect(decoded.callerPhoneNumber).toBe('+18005550199');
    expect(decoded.action).toBe(SimulateIncomingCallRequest_Action.RING);
  });

  it('streamRadioState subscribes to live updates via transport.stream', () => {
    mockTransport.stream = vi.fn();
    const client = new TelephonyClient(mockTransport);

    const onRadio = vi.fn();
    client.streamRadioState(onRadio);

    expect(mockTransport.stream).toHaveBeenCalledTimes(1);
    const [service, method, , onMsg] = mockTransport.stream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.TelephonyService');
    expect(method).toBe('StreamRadioState');

    const sampleRadio: RadioState = {
      radioPowered: true,
      activeTechnology: RadioState_Technology.LTE,
      signalStrength: RadioState_Strength.GREAT,
      registrationState: RadioState_Registration.HOME_NETWORK,
    };
    onMsg(RadioState.encode(sampleRadio).finish());
    expect(onRadio).toHaveBeenCalledWith(expect.objectContaining({ activeTechnology: RadioState_Technology.LTE }));
  });
});