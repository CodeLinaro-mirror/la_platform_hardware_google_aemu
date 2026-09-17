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
  InputEvent,
  PointerEvent_Pointer,
  ButtonMask,
} from "@android/emulator-services/input/input_service";

export { InputEvent, ButtonMask };

export enum PointerAction {
  UNSPECIFIED = 0,
  DOWN = 1,
  MOVE = 2,
  UP = 3,
  CANCEL = 4,
  HOVER_ENTER = 5,
  HOVER_MOVE = 6,
  HOVER_EXIT = 7,
  SCROLL = 8,
  RELATIVE_MOVE = 9,
  POINTER_ACTION_UNSPECIFIED = 0,
  POINTER_ACTION_DOWN = 1,
  POINTER_ACTION_MOVE = 2,
  POINTER_ACTION_UP = 3,
  POINTER_ACTION_CANCEL = 4,
  POINTER_ACTION_HOVER_ENTER = 5,
  POINTER_ACTION_HOVER_MOVE = 6,
  POINTER_ACTION_HOVER_EXIT = 7,
  POINTER_ACTION_SCROLL = 8,
  POINTER_ACTION_RELATIVE_MOVE = 9,
}

export enum ToolType {
  UNSPECIFIED = 0,
  FINGER = 1,
  STYLUS = 2,
  ERASER = 3,
  MOUSE = 4,
  PALM = 5,
  TOOL_TYPE_UNSPECIFIED = 0,
  TOOL_TYPE_FINGER = 1,
  TOOL_TYPE_STYLUS = 2,
  TOOL_TYPE_ERASER = 3,
  TOOL_TYPE_MOUSE = 4,
  TOOL_TYPE_PALM = 5,
}

export enum KeyAction {
  UNSPECIFIED = 0,
  DOWN = 1,
  UP = 2,
  PRESS = 3,
  KEY_ACTION_UNSPECIFIED = 0,
  KEY_ACTION_DOWN = 1,
  KEY_ACTION_UP = 2,
  KEY_ACTION_PRESS = 3,
}

export enum KeyModifier {
  UNSPECIFIED = 0,
  SHIFT = 1,
  ALT = 2,
  CTRL = 3,
  META = 4,
  CAPS_LOCK = 5,
  NUM_LOCK = 6,
  SCROLL_LOCK = 7,
}

export interface PointerEventPayload {
  displayId?: number;
  slot?: number;
  action: PointerAction;
  x?: number;
  y?: number;
  pressure?: number;
  toolType?: ToolType;
  tiltX?: number;
  tiltY?: number;
  orientation?: number;
  buttons?: number;
  scrollDeltaX?: number;
  scrollDeltaY?: number;
  scrollX?: number;
  scrollY?: number;
  touchMajor?: number;
  touchMinor?: number;
  relativeDeltaX?: number;
  relativeDeltaY?: number;
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
  keycode?: number;
  text?: string;
  modifiers?: KeyModifier[];
  displayId?: number;
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
      scrollDeltaX: event.deltaX ?? 0,
      scrollDeltaY: event.deltaY ?? 0,
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
            toolType: (ptr.toolType ?? ToolType.FINGER) as any,
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
              pointerId: p.slot ?? 0,
              toolType: (p.toolType ?? ToolType.FINGER) as any,
              x: p.x ?? 0,
              y: p.y ?? 0,
              pressure: p.pressure ?? 1.0,
              size: p.touchMajor ?? 0,
              orientationRadians: p.orientation ?? 0,
              tiltRadians: p.tiltX ?? 0,
              buttonMask: p.buttons ?? ButtonMask.BUTTON_MASK_NONE,
              deltaX: p.relativeDeltaX ?? 0,
              deltaY: p.relativeDeltaY ?? 0,
            },
          ];

    const inputEvent: InputEvent = {
      pointer: {
        displayId: p.displayId ?? 0,
        action: p.action as any,
        pointers,
        scrollX: p.scrollDeltaX ?? p.scrollX,
        scrollY: p.scrollDeltaY ?? p.scrollY,
      },
    };

    return InputEvent.encode(inputEvent).finish();
  }

  /**
   * Serializes a KeyEvent into InputEvent protobuf binary using ts-proto.
   */
  private encodeKeyEvent(k: KeyEventPayload): Uint8Array {
    if (k.text && !k.domCode && !k.androidKeycode) {
      return InputEvent.encode({
        text: { text: k.text },
      }).finish();
    }

    const key: any = {
      action: k.action as any,
    };

    if (k.domCode) {
      key.domCode = k.domCode;
    } else if (k.androidKeycode !== undefined || k.keycode !== undefined) {
      key.androidKeycode = k.androidKeycode ?? k.keycode;
    } else if (k.evdevCode !== undefined) {
      key.evdevCode = k.evdevCode;
    }

    const inputEvent: InputEvent = {
      key,
    };

    return InputEvent.encode(inputEvent).finish();
  }
}
