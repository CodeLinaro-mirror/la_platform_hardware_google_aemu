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

import {
  DeepPartial,
  UpdateSensorsStateRequest,
} from '@android/emulator-services/sensors_service';

export interface SensorsChannelOptions {
  maxQueueSize?: number;
  maxBufferedAmount?: number;
}

/**
 * Manages low-latency serialization and transport of high-frequency sensor
 * and 3D pose updates over the WebRTC "sensors_v2" SCTP DataChannel.
 */
export class SensorsChannel {
  private channel: RTCDataChannel | null = null;
  private readonly maxQueueSize: number;
  private readonly maxBufferedAmount: number;
  private queue: Uint8Array[] = [];

  constructor(options: SensorsChannelOptions = {}) {
    this.maxQueueSize = options.maxQueueSize ?? 64;
    this.maxBufferedAmount = options.maxBufferedAmount ?? 64 * 1024;
  }

  public get isReady(): boolean {
    return this.channel !== null && this.channel.readyState === 'open';
  }

  /**
   * Attaches an active RTCDataChannel (SCTP) dedicated to sensors telemetry.
   * Cleans up event listeners on any previous channel to prevent handler leaks
   * or stale dispatches on detached channels during ICE restart or reconnection.
   */
  public attachDataChannel(channel: RTCDataChannel) {
    if (this.channel) {
      this.channel.onopen = null;
      this.channel.onbufferedamountlow = null;
    }
    this.channel = channel;
    this.channel.binaryType = 'arraybuffer';

    this.channel.onopen = () => {
      this.flushQueue();
    };

    this.channel.onbufferedamountlow = () => {
      this.flushQueue();
    };

    if (this.channel.readyState === 'open') {
      this.flushQueue();
    }
  }

  /**
   * Detaches the active RTCDataChannel and cleans up all event handlers.
   */
  public detach(): void {
    if (this.channel) {
      this.channel.onopen = null;
      this.channel.onbufferedamountlow = null;
      this.channel = null;
    }
  }

  /**
   * Encodes and sends an UpdateSensorsStateRequest over the SCTP DataChannel.
   * Returns true if sent immediately over an open channel, or false if unavailable or congested.
   */
  public sendUpdate(update: DeepPartial<UpdateSensorsStateRequest>): boolean {
    const msg = UpdateSensorsStateRequest.fromPartial(update);
    const bytes = UpdateSensorsStateRequest.encode(msg).finish();

    if (this.channel && this.channel.readyState === 'open') {
      if (this.channel.bufferedAmount <= this.maxBufferedAmount) {
        this.channel.send(bytes as any);
        return true;
      }
    }

    if (this.maxQueueSize > 0) {
      if (this.queue.length >= this.maxQueueSize) {
        this.queue.shift();
      }
      this.queue.push(bytes);
    }
    return false;
  }

  public decodeUpdate(bytes: Uint8Array): UpdateSensorsStateRequest {
    return UpdateSensorsStateRequest.decode(bytes);
  }

  private flushQueue() {
    if (!this.channel || this.channel.readyState !== 'open') {
      return;
    }
    while (this.queue.length > 0) {
      const bytes = this.queue.shift()!;
      this.channel.send(bytes as any);
    }
  }
}
