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
  ButtonMask,
  InputEvent,
  KeyAction,
  KeyEvent,
  PointerAction,
  PointerEvent_Pointer,
  ToolType,
} from "@android/emulator-services/input_service";

export { ButtonMask, InputEvent, KeyAction, KeyEvent, PointerAction, ToolType };

/**
 * Input source id reserved for the human operator.
 *
 * `InputEvent.input_source_id` is a plain proto3 scalar, so `0` is both the
 * wire default and the identifier for the human driving the device UI. This
 * channel serves exactly that operator, so it always tags its events with `0`;
 * automated agents sharing the same device pick a distinct non-zero id.
 */
const HUMAN_INPUT_SOURCE_ID = 0;

export interface PointerEventPayload {
  displayId?: number;
  pointerId?: number;
  action: PointerAction;
  x?: number;
  y?: number;
  pressure?: number;
  toolType?: ToolType;
  tiltRadians?: number;
  orientationRadians?: number;
  buttonMask?: number;
  scrollX?: number;
  scrollY?: number;
  size?: number;
  deltaX?: number;
  deltaY?: number;
  pointers?: Array<{
    pointerId?: number;
    toolType?: ToolType;
    x?: number;
    y?: number;
    pressure?: number;
    size?: number;
    orientationRadians?: number;
    tiltRadians?: number;
    buttonMask?: number;
    deltaX?: number;
    deltaY?: number;
  }>;
}

export interface KeyEventPayload {
  action: KeyAction;
  domCode?: string;
  androidKeycode?: number;
  evdevCode?: number;
  text?: string;
}

export interface InputChannelOptions {
  maxQueueSize?: number;
  maxBufferedAmount?: number;
}

/**
 * Manages low-latency serialization and transport of input events over WebRTC DataChannels.
 */
export class InputChannel {
  private channel: RTCDataChannel | null = null;
  private readonly maxQueueSize: number;
  private readonly maxBufferedAmount: number;
  private queue: Uint8Array[] = [];

  constructor(options: InputChannelOptions = {}) {
    this.maxQueueSize = options.maxQueueSize || 100;
    this.maxBufferedAmount = options.maxBufferedAmount || 64 * 1024;
  }

  public get isReady(): boolean {
    return this.channel !== null && this.channel.readyState === "open";
  }

  /**
   * Attaches an active RTCDataChannel (SCTP) dedicated to input injection.
   */
  public attach(channel: RTCDataChannel) {
    this.attachDataChannel(channel);
  }

  public decodeEvent(bytes: Uint8Array): InputEvent {
    return InputEvent.decode(bytes);
  }

  public attachDataChannel(channel: RTCDataChannel) {
    this.channel = channel;
    this.channel.binaryType = "arraybuffer";

    this.channel.onopen = () => {
      console.log("[InputChannel] DataChannel onopen event fired.");
      this.flushQueue();
    };

    this.channel.onbufferedamountlow = () => {
      this.flushQueue();
    };

    this.channel.onclose = () => {
      console.log("[InputChannel] DataChannel closed.");
    };

    this.channel.onerror = (err) => {
      console.error("[InputChannel] DataChannel error:", err);
    };

    if (this.channel.readyState === "open") {
      console.log("[InputChannel] DataChannel attached in OPEN state. Flushing queue.");
      this.flushQueue();
    }
  }

  /**
   * Sends a pointer event (touch, mouse click/move, stylus).
   */
  public sendPointer(event: PointerEventPayload) {
    const bytes = this.encodePointerEvent(event);
    this.sendOrQueue(bytes);
  }

  /**
   * Sends a mouse wheel scroll event.
   */
  public sendScroll(event: {
    displayId?: number;
    x?: number;
    y?: number;
    deltaX?: number;
    deltaY?: number;
  }) {
    this.sendPointer({
      displayId: event.displayId ?? 0,
      x: event.x ?? 0,
      y: event.y ?? 0,
      action: PointerAction.POINTER_ACTION_SCROLL,
      scrollX: event.deltaX ?? 0,
      scrollY: event.deltaY ?? 0,
    });
  }

  /**
   * Sends a keyboard event.
   */
  public sendKey(event: KeyEventPayload) {
    const bytes = this.encodeKeyEvent(event);
    this.sendOrQueue(bytes);
  }

  private sendOrQueue(bytes: Uint8Array) {
    if (this.channel && this.channel.readyState === "open") {
      if (this.channel.bufferedAmount <= this.maxBufferedAmount) {
        console.log(`[InputChannel] Dispatched ${bytes.byteLength} bytes over open DataChannel.`);
        this.channel.send(bytes as any);
        return;
      }
    }

    console.log(
      `[InputChannel] DataChannel not ready (state=${this.channel?.readyState}). Queuing ${bytes.byteLength} bytes.`
    );
    if (this.queue.length >= this.maxQueueSize) {
      this.queue.shift();
    }
    this.queue.push(bytes);
  }

  private flushQueue() {
    if (!this.channel || this.channel.readyState !== "open") {
      return;
    }
    console.log(`[InputChannel] Flushing ${this.queue.length} queued messages.`);
    while (this.queue.length > 0) {
      const bytes = this.queue.shift()!;
      this.channel.send(bytes as any);
    }
  }

  /**
   * Serializes a PointerEvent into InputEvent protobuf binary using ts-proto.
   */
  private encodePointerEvent(p: PointerEventPayload): Uint8Array {
    const pointers: PointerEvent_Pointer[] =
      p.pointers && p.pointers.length > 0
        ? p.pointers.map((ptr) => ({
            pointerId: ptr.pointerId ?? 0,
            toolType: ptr.toolType ?? ToolType.TOOL_TYPE_FINGER,
            x: ptr.x ?? 0,
            y: ptr.y ?? 0,
            pressure: ptr.pressure ?? 1.0,
            size: ptr.size ?? 0,
            orientationRadians: ptr.orientationRadians ?? 0,
            tiltRadians: ptr.tiltRadians ?? 0,
            buttonMask: ptr.buttonMask ?? ButtonMask.BUTTON_MASK_NONE,
            deltaX: ptr.deltaX ?? 0,
            deltaY: ptr.deltaY ?? 0,
          }))
        : [
            {
              pointerId: p.pointerId ?? 0,
              toolType: p.toolType ?? ToolType.TOOL_TYPE_FINGER,
              x: p.x ?? 0,
              y: p.y ?? 0,
              pressure: p.pressure ?? 1.0,
              size: p.size ?? 0,
              orientationRadians: p.orientationRadians ?? 0,
              tiltRadians: p.tiltRadians ?? 0,
              buttonMask: p.buttonMask ?? ButtonMask.BUTTON_MASK_NONE,
              deltaX: p.deltaX ?? 0,
              deltaY: p.deltaY ?? 0,
            },
          ];

    const inputEvent: InputEvent = {
      inputSourceId: HUMAN_INPUT_SOURCE_ID,
      pointer: {
        displayId: p.displayId ?? 0,
        action: p.action,
        pointers,
        scrollX: p.scrollX,
        scrollY: p.scrollY,
      },
    };

    return InputEvent.encode(inputEvent).finish();
  }

  /**
   * Serializes a KeyEvent into InputEvent protobuf binary using ts-proto.
   */
  private encodeKeyEvent(k: KeyEventPayload): Uint8Array {
    if (k.text && !k.domCode && k.androidKeycode === undefined && k.evdevCode === undefined) {
      return InputEvent.encode({
        inputSourceId: HUMAN_INPUT_SOURCE_ID,
        text: { text: k.text },
      }).finish();
    }

    const key: KeyEvent = {
      action: k.action,
    };

    if (k.domCode) {
      key.domCode = k.domCode;
    } else if (k.androidKeycode !== undefined) {
      key.androidKeycode = k.androidKeycode;
    } else if (k.evdevCode !== undefined) {
      key.evdevCode = k.evdevCode;
    }

    const inputEvent: InputEvent = {
      inputSourceId: HUMAN_INPUT_SOURCE_ID,
      key,
    };

    return InputEvent.encode(inputEvent).finish();
  }
}
