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
import { subscribe, Unsubscribe } from './streaming';
import {
  CallState,
  CallState_Status,
  DeepPartial,
  GetTelephonyStateRequest,
  SendSmsRequest,
  SendSmsResponse,
  StreamTelephonyStateRequest,
  TelephonyService,
  TelephonyServiceClientImpl,
  TelephonyState,
  TelephonyState_Registration,
  TelephonyState_Strength,
  TelephonyState_Technology,
  TransitionCallRequest,
  TransitionCallRequest_Action,
  TransitionCallResponse,
  UpdateTelephonyStateRequest,
} from '@android/emulator-services/telephony_service';

export {
  CallState,
  CallState_Status,
  TelephonyState,
  TelephonyState_Technology,
  TelephonyState_Strength,
  TelephonyState_Registration,
  SendSmsRequest,
  SendSmsResponse,
  TransitionCallRequest,
  TransitionCallRequest_Action,
  TransitionCallResponse,
  UpdateTelephonyStateRequest,
};

/**
 * The fields of {@link TelephonyState} a caller is allowed to change.
 */
export type TelephonyStateUpdate = DeepPartial<UpdateTelephonyStateRequest>;

/**
 * Headless API client for TelephonyService (modem, cellular radio, SMS and
 * call simulation).
 *
 * Methods named in `PascalCase` are the RPCs of `TelephonyService`, spelled
 * exactly as `telephony_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class TelephonyClient {
  private readonly rpc: TelephonyService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new TelephonyServiceClientImpl(transport);
  }

  public GetTelephonyState(
    request: DeepPartial<GetTelephonyStateRequest> = {}
  ): Promise<TelephonyState> {
    return this.rpc.GetTelephonyState(GetTelephonyStateRequest.fromPartial(request));
  }

  public StreamTelephonyState(
    request: DeepPartial<StreamTelephonyStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<TelephonyState> {
    return this.rpc.StreamTelephonyState(StreamTelephonyStateRequest.fromPartial(request), signal);
  }

  /**
   * Applies a partial update. Only the fields present in `update` are changed;
   * the rest are left alone by the server.
   */
  public UpdateTelephonyState(update: TelephonyStateUpdate): Promise<TelephonyState> {
    return this.rpc.UpdateTelephonyState(UpdateTelephonyStateRequest.fromPartial(update));
  }

  public SendSms(request: DeepPartial<SendSmsRequest>): Promise<SendSmsResponse> {
    return this.rpc.SendSms(SendSmsRequest.fromPartial(request));
  }

  public TransitionCall(
    request: DeepPartial<TransitionCallRequest>
  ): Promise<TransitionCallResponse> {
    return this.rpc.TransitionCall(TransitionCallRequest.fromPartial(request));
  }

  /** Callback form of {@link TelephonyClient.StreamTelephonyState}. */
  public subscribeTelephonyState(
    onState: (state: TelephonyState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamTelephonyStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamTelephonyState(request, signal), onState, onError);
  }
}