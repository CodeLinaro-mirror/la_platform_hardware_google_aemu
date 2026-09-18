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

import { useRef, useEffect, useCallback, CSSProperties, PointerEvent, WheelEvent, KeyboardEvent } from 'react';
import { useDevice } from './hooks';
import { PointerAction, ToolType, KeyAction } from '../core/input_channel';
import { RtcConnectionState } from '../core/rtc_controller';

export interface DeviceViewProps {
  /**
   * Optional CSS class name for the wrapper container.
   */
  className?: string;

  /**
   * Optional inline styles for the container.
   */
  style?: CSSProperties;

  /**
   * Target display ID to interact with (default: 0).
   */
  displayId?: number;

  /**
   * Whether to initiate WebRTC streaming connection automatically on mount (default: true).
   */
  autoConnect?: boolean;

  /**
   * Callback fired when WebRTC connection state changes.
   */
  onStateChange?: (state: RtcConnectionState) => void;

  /**
   * Callback fired when the remote MediaStream becomes available.
   */
  onStream?: (stream: MediaStream) => void;

  /**
   * Callback fired upon WebRTC or transport errors.
   */
  onError?: (err: Error) => void;
}

const defaultContainerStyle: CSSProperties = {
  position: 'relative',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '100%',
  height: '100%',
  backgroundColor: '#000000',
  overflow: 'hidden',
  userSelect: 'none',
  touchAction: 'none',
};

const defaultVideoStyle: CSSProperties = {
  maxWidth: '100%',
  maxHeight: '100%',
  objectFit: 'contain',
  display: 'block',
  pointerEvents: 'auto',
};

/**
 * Computes normalized [0.0, 1.0] coordinates within an object-fit: contain video element,
 * accounting for pillarbox (left/right) or letterbox (top/bottom) black bars.
 */
export function computeNormalizedCoordinates(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  videoWidth = 0,
  videoHeight = 0
): { x: number; y: number } {
  if (rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };

  let contentLeft = rect.left;
  let contentTop = rect.top;
  let contentWidth = rect.width;
  let contentHeight = rect.height;

  if (videoWidth > 0 && videoHeight > 0) {
    const videoRatio = videoWidth / videoHeight;
    const elementRatio = rect.width / rect.height;
    if (elementRatio > videoRatio) {
      // Pillarboxed (black bars on left/right)
      contentWidth = rect.height * videoRatio;
      contentLeft = rect.left + (rect.width - contentWidth) / 2;
    } else if (elementRatio < videoRatio) {
      // Letterboxed (black bars on top/bottom)
      contentHeight = rect.width / videoRatio;
      contentTop = rect.top + (rect.height - contentHeight) / 2;
    }
  }

  const x = Math.max(0, Math.min(1, (clientX - contentLeft) / contentWidth));
  const y = Math.max(0, Math.min(1, (clientY - contentTop) / contentHeight));
  return { x, y };
}

/**
 * Modern React Component for rendering and interacting with Android Devices and Emulators.
 */
export function DeviceView({
  className,
  style,
  displayId = 0,
  autoConnect = true,
  onStateChange,
  onStream,
  onError,
}: DeviceViewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const { state, stream, error, connect, client } = useDevice();

  const onStateChangeRef = useRef(onStateChange);
  onStateChangeRef.current = onStateChange;

  const onStreamRef = useRef(onStream);
  onStreamRef.current = onStream;

  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const prevStateRef = useRef(null as RtcConnectionState | null);
  const prevStreamRef = useRef<MediaStream | null>(null);
  const prevErrorRef = useRef<Error | null>(null);
  const autoConnectAttemptedRef = useRef(false);

  // Handle autoconnect
  useEffect(() => {
    if (autoConnect && state === RtcConnectionState.DISCONNECTED && !autoConnectAttemptedRef.current) {
      autoConnectAttemptedRef.current = true;
      connect().catch((err) => {
        onErrorRef.current?.(err);
      });
    } else if (autoConnect && state === RtcConnectionState.FAILED) {
      const timer = setTimeout(() => {
        connect().catch((err) => {
          onErrorRef.current?.(err);
        });
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [autoConnect, state, connect]);

  // Sync state transitions (only invoke when state actually changes)
  useEffect(() => {
    if (prevStateRef.current !== state) {
      prevStateRef.current = state;
      onStateChangeRef.current?.(state);
    }
  }, [state]);

  // Sync stream changes
  useEffect(() => {
    if (stream && prevStreamRef.current !== stream) {
      prevStreamRef.current = stream;
      onStreamRef.current?.(stream);
      if (videoRef.current) {
        const video = videoRef.current;
        video.srcObject = stream;
        video.muted = false;
        video.play().catch((err) => {
          console.warn('Aquarium unmuted autoplay blocked, falling back to muted until interaction:', err);
          video.muted = true;
          video.play().catch((e) => console.warn('Aquarium video playback warning:', e));
        });
      }
    }
  }, [stream]);

  // Sync error changes
  useEffect(() => {
    if (error && prevErrorRef.current !== error) {
      prevErrorRef.current = error;
      onErrorRef.current?.(error);
    }
  }, [error]);

  // Compute normalized coordinates [0.0, 1.0] from a PointerEvent
  const getNormalizedCoordinates = useCallback((e: PointerEvent<HTMLElement>) => {
    const video = videoRef.current;
    const el = video || containerRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    return computeNormalizedCoordinates(
      e.clientX,
      e.clientY,
      rect,
      video?.videoWidth ?? 0,
      video?.videoHeight ?? 0
    );
  }, []);

  const handlePointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (videoRef.current && videoRef.current.muted) {
        videoRef.current.muted = false;
      }
      if (!client.input.isReady) return;
      e.currentTarget.setPointerCapture?.(e.pointerId);
      const { x, y } = getNormalizedCoordinates(e);

      client.input.sendPointer({
        displayId,
        slot: e.pointerId,
        action: PointerAction.DOWN,
        x,
        y,
        pressure: e.pressure || 1.0,
        toolType: e.pointerType === 'mouse' ? ToolType.MOUSE : ToolType.FINGER,
      });
    },
    [client, displayId, getNormalizedCoordinates]
  );

  const handlePointerMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!client.input.isReady || (e.pointerType === 'mouse' && e.buttons === 0)) return;
      const { x, y } = getNormalizedCoordinates(e);

      client.input.sendPointer({
        displayId,
        slot: e.pointerId,
        action: PointerAction.MOVE,
        x,
        y,
        pressure: e.pressure || 1.0,
        toolType: e.pointerType === 'mouse' ? ToolType.MOUSE : ToolType.FINGER,
      });
    },
    [client, displayId, getNormalizedCoordinates]
  );

  const handlePointerUp = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!client.input.isReady) return;
      e.currentTarget.releasePointerCapture?.(e.pointerId);
      const { x, y } = getNormalizedCoordinates(e);

      client.input.sendPointer({
        displayId,
        slot: e.pointerId,
        action: PointerAction.UP,
        x,
        y,
        pressure: 0,
        toolType: e.pointerType === 'mouse' ? ToolType.MOUSE : ToolType.FINGER,
      });
    },
    [client, displayId, getNormalizedCoordinates]
  );

  const handlePointerCancel = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!client.input.isReady) return;
      const { x, y } = getNormalizedCoordinates(e);

      client.input.sendPointer({
        displayId,
        slot: e.pointerId,
        action: PointerAction.CANCEL,
        x,
        y,
      });
    },
    [client, displayId, getNormalizedCoordinates]
  );

  const handleWheel = useCallback(
    (e: WheelEvent<HTMLDivElement>) => {
      if (!client.input.isReady) return;
      client.input.sendScroll({
        displayId,
        deltaX: e.deltaX,
        deltaY: e.deltaY,
      });
    },
    [client, displayId]
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      const code = e.code || e.key;
      client.input.sendKey({
        domCode: code,
        action: KeyAction.DOWN,
      });
      if (['Tab', 'Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Backspace'].includes(e.code)) {
        e.preventDefault();
      }
    },
    [client]
  );

  const handleKeyUp = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      const code = e.code || e.key;
      client.input.sendKey({
        domCode: code,
        action: KeyAction.UP,
      });
      if (['Tab', 'Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Backspace'].includes(e.code)) {
        e.preventDefault();
      }
    },
    [client]
  );

  return (
    <div
      ref={containerRef}
      className={className ? `android-device-view ${className}` : 'android-device-view'}
      style={{ ...defaultContainerStyle, ...style }}
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      onWheel={handleWheel}
      onKeyDown={handleKeyDown}
      onKeyUp={handleKeyUp}
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        style={defaultVideoStyle}
      />
    </div>
  );
}

// Backward compatibility aliases
export { DeviceView as EmulatorView, type DeviceViewProps as EmulatorViewProps };
