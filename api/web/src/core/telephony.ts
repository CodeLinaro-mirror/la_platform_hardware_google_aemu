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

import { GrpcWebTransport } from './transport';
import {
  RadioState,
  RadioState_Technology,
  RadioState_Strength,
  RadioState_Registration,
  GetRadioStateRequest,
  StreamRadioStateRequest,
  UpdateRadioStateRequest,
  SendSmsRequest,
  SimulateIncomingCallRequest,
  SimulateIncomingCallRequest_Action,
} from '@android/emulator-services/environment/telephony_service';

export {
  RadioState,
  RadioState_Technology,
  RadioState_Strength,
  RadioState_Registration,
  SimulateIncomingCallRequest_Action,
};

export interface RadioStateUpdate {
  radioPowered?: boolean;
  activeTechnology?: RadioState_Technology;
  signalStrength?: RadioState_Strength;
  registrationState?: RadioState_Registration;
}

/**
 * Headless API client for TelephonyService (Modem, Cellular radio, SMS, and Call simulation).
 */
export class TelephonyClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getRadioState(): Promise<RadioState> {
    const reqBytes = GetRadioStateRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.TelephonyService',
      'GetRadioState',
      reqBytes
    );
    return RadioState.decode(resBytes);
  }

  public streamRadioState(
    onState: (state: RadioState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamRadioStateRequest.encode({}).finish();
    return this.transport.stream(
      'android.emulation.v2.environment.TelephonyService',
      'StreamRadioState',
      reqBytes,
      (bytes) => onState(RadioState.decode(bytes)),
      onError
    );
  }

  public streamRadioStateIterator(): AsyncIterable<RadioState> {
    const reqBytes = StreamRadioStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.environment.TelephonyService',
      'StreamRadioState',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield RadioState.decode(chunkBytes);
        }
      },
    };
  }

  public async updateRadioState(update: RadioStateUpdate): Promise<RadioState> {
    const paths: string[] = [];
    const state: RadioState = {
      radioPowered: update.radioPowered ?? true,
      activeTechnology: update.activeTechnology ?? RadioState_Technology.LTE,
      signalStrength: update.signalStrength ?? RadioState_Strength.GREAT,
      registrationState:
        update.registrationState ?? RadioState_Registration.HOME_NETWORK,
    };

    if (update.radioPowered !== undefined) paths.push('radio_powered');
    if (update.activeTechnology !== undefined) paths.push('active_technology');
    if (update.signalStrength !== undefined) paths.push('signal_strength');
    if (update.registrationState !== undefined)
      paths.push('registration_state');

    const req: UpdateRadioStateRequest = {
      radioState: state,
      updateMask: paths,
    };
    const reqBytes = UpdateRadioStateRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.TelephonyService',
      'UpdateRadioState',
      reqBytes
    );
    return RadioState.decode(resBytes);
  }

  public async sendSms(
    senderPhoneNumber: string,
    messageBody: string
  ): Promise<Uint8Array> {
    const req: SendSmsRequest = {
      senderPhoneNumber,
      messageBody,
    };
    const reqBytes = SendSmsRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.environment.TelephonyService',
      'SendSms',
      reqBytes
    );
  }

  public async simulateIncomingCall(
    callerPhoneNumber: string,
    action: SimulateIncomingCallRequest_Action = SimulateIncomingCallRequest_Action.RING
  ): Promise<Uint8Array> {
    const req: SimulateIncomingCallRequest = {
      callerPhoneNumber,
      action,
    };
    const reqBytes = SimulateIncomingCallRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.environment.TelephonyService',
      'SimulateIncomingCall',
      reqBytes
    );
  }
}