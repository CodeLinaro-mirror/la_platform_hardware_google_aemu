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
  TelephonyState_Technology,
  TelephonyState_Strength,
  TelephonyState_Registration,
  TransitionCallRequest_Action,
} from '../src/core/telephony';
import {
  TelephonyState,
  SendSmsRequest,
  TransitionCallRequest,
  UpdateTelephonyStateRequest,
} from '@android/emulator-services/telephony_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.TelephonyService';

describe('TelephonyClient', () => {
  let transport: FakeTransport;
  let client: TelephonyClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new TelephonyClient(transport.asTransport());
  });

  function encodeTelephony(state: Partial<TelephonyState>): Uint8Array {
    return TelephonyState.encode(TelephonyState.fromPartial(state)).finish();
  }

  it('decodes the telephony status returned by GetTelephonyState', async () => {
    transport.respondWith(
      encodeTelephony({
        radioPowered: true,
        activeTechnology: TelephonyState_Technology.LTE,
        signalStrength: TelephonyState_Strength.GREAT,
        registrationState: TelephonyState_Registration.HOME_NETWORK,
      })
    );

    const result = await client.GetTelephonyState();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetTelephonyState');
    expect(result.activeTechnology).toBe(TelephonyState_Technology.LTE);
    expect(result.radioPowered).toBe(true);
  });

  it('sends only the updated telephony state fields on the wire', async () => {
    transport.respondWith(
      encodeTelephony({
        radioPowered: false,
        activeTechnology: TelephonyState_Technology.NR5G_SUB6,
        signalStrength: TelephonyState_Strength.POOR,
        registrationState: TelephonyState_Registration.ROAMING,
      })
    );

    const result = await client.UpdateTelephonyState({
      radioPowered: false,
      activeTechnology: TelephonyState_Technology.NR5G_SUB6,
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.method).toBe('UpdateTelephonyState');
    const request = UpdateTelephonyStateRequest.decode(transport.lastCall.data);
    expect(request.radioPowered).toBe(false);
    expect(request.activeTechnology).toBe(TelephonyState_Technology.NR5G_SUB6);
    expect(request.signalStrength).toBeUndefined();
    expect(request.registrationState).toBeUndefined();
    expect(result.radioPowered).toBe(false);
  });

  it('sends every supplied field of a wider update', async () => {
    transport.respondWith(encodeTelephony({}));

    await client.UpdateTelephonyState({
      radioPowered: true,
      activeTechnology: TelephonyState_Technology.LTE,
      signalStrength: TelephonyState_Strength.MODERATE,
      registrationState: TelephonyState_Registration.HOME_NETWORK,
    });

    const request = UpdateTelephonyStateRequest.decode(transport.lastCall.data);
    expect(request.radioPowered).toBe(true);
    expect(request.activeTechnology).toBe(TelephonyState_Technology.LTE);
    expect(request.signalStrength).toBe(TelephonyState_Strength.MODERATE);
    expect(request.registrationState).toBe(TelephonyState_Registration.HOME_NETWORK);
  });

  it('preserves explicit falsy values via proto3 optional wire presence', async () => {
    transport.respondWith(encodeTelephony({}));

    await client.UpdateTelephonyState({ radioPowered: false, signalStrength: undefined });

    const request = UpdateTelephonyStateRequest.decode(transport.lastCall.data);
    expect(request.radioPowered).toBe(false);
    expect(request.signalStrength).toBeUndefined();
  });

  it('encodes the sender and message body of SendSms', async () => {
    transport.respondWith(new Uint8Array());

    await client.SendSms({
      senderPhoneNumber: '+16505551234',
      messageBody: 'Verification code: 4242',
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('SendSms');

    const request = SendSmsRequest.decode(transport.lastCall.data);
    expect(request.senderPhoneNumber).toBe('+16505551234');
    expect(request.messageBody).toBe('Verification code: 4242');
  });

  it('encodes the caller and action of TransitionCall', async () => {
    transport.respondWith(new Uint8Array());

    await client.TransitionCall({
      callerPhoneNumber: '+18005550199',
      action: TransitionCallRequest_Action.RING,
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('TransitionCall');

    const request = TransitionCallRequest.decode(transport.lastCall.data);
    expect(request.callerPhoneNumber).toBe('+18005550199');
    expect(request.action).toBe(TransitionCallRequest_Action.RING);
  });

  it('encodes a non-ringing call action', async () => {
    transport.respondWith(new Uint8Array());

    await client.TransitionCall({
      callerPhoneNumber: '+18005550199',
      action: TransitionCallRequest_Action.DISCONNECT,
    });

    const request = TransitionCallRequest.decode(transport.lastCall.data);
    expect(request.action).toBe(TransitionCallRequest_Action.DISCONNECT);
  });

  it('encodes ACCEPT, HOLD, and UNHOLD call lifecycle actions', async () => {
    for (const action of [
      TransitionCallRequest_Action.ACCEPT,
      TransitionCallRequest_Action.HOLD,
      TransitionCallRequest_Action.UNHOLD,
    ]) {
      transport.respondWith(new Uint8Array());
      await client.TransitionCall({
        callerPhoneNumber: '+18005550199',
        action,
      });
      const request = TransitionCallRequest.decode(transport.lastCall.data);
      expect(request.action).toBe(action);
    }
  });

  it('decodes each state pushed by StreamTelephonyState', async () => {
    const onTelephony = vi.fn();

    const unsubscribe = client.subscribeTelephonyState(onTelephony);
    transport
      .stream()
      .push(encodeTelephony({ activeTechnology: TelephonyState_Technology.LTE }));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamTelephonyState');
    expect(onTelephony).toHaveBeenCalledWith(
      expect.objectContaining({ activeTechnology: TelephonyState_Technology.LTE })
    );

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeTelephonyState(() => {});
    await settle();

    expect(transport.streamCalls[0].signal?.aborted).toBe(false);

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