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

import { useState, useEffect, useCallback } from 'react';
import { useDeviceClient } from './context';
import { RtcConnectionState } from '../core/rtc_controller';
import { SensorsState, SensorsStateUpdate } from '../core/sensors';
import { LocationState, LocationUpdate } from '../core/location';
import { DeviceCapabilities } from '../core/capabilities';
import { VmState } from '../core/vm';
import { BatteryState, BatteryState_State, ChargerSource } from '../core/battery';
import { PostureState, PostureType } from '../core/posture';
import { Snapshot } from '../core/snapshot';
import { DisplayState } from '../core/display';
import { RadioState, SimulateIncomingCallRequest_Action } from '../core/telephony';
import { ScreenshotOptions } from '../core/screen_capture';
import { InjectFrameOptions } from '../core/camera';
import { SystemState } from '../core/system';

/**
 * Hook to manage the WebRTC connection lifecycle and media stream.
 */
export function useDevice() {
  const client = useDeviceClient();
  const [state, setState] = useState<RtcConnectionState>(client.rtc.state);
  const [stream, setStream] = useState<MediaStream | null>(client.rtc.stream);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const handleStateChange = (newState: RtcConnectionState) => {
      setState(newState);
    };

    const handleStream = (newStream: MediaStream) => {
      setStream(newStream);
    };

    const handleError = (err: Error) => {
      setError(err);
    };

    client.rtc.on('stateChange', handleStateChange);
    client.rtc.on('stream', handleStream);
    client.rtc.on('error', handleError);

    // Sync initial state
    setState(client.rtc.state);
    setStream(client.rtc.stream);

    return () => {
      client.rtc.off('stateChange', handleStateChange);
      client.rtc.off('stream', handleStream);
      client.rtc.off('error', handleError);
    };
  }, [client]);

  const connect = useCallback(async () => {
    setError(null);
    try {
      await client.connect();
    } catch (err: any) {
      setError(err);
      throw err;
    }
  }, [client]);

  const disconnect = useCallback(() => {
    client.disconnect();
    setStream(null);
  }, [client]);

  return {
    state,
    stream,
    error,
    connect,
    disconnect,
    client,
  };
}

/**
 * Hook to read and control emulator sensors per DESIGN.md.
 */
export function useSensors(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [sensors, setSensors] = useState<SensorsState | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchSensors = useCallback(async () => {
    setIsUpdating(true);
    setError(null);
    try {
      const state = await client.sensors.getSensorsState();
      setSensors(state);
      return state;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsUpdating(false);
    }
  }, [client]);

  useEffect(() => {
    fetchSensors().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.sensors.streamSensors(
      (state) => setSensors(state),
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchSensors, options.stream]);

  const updateSensors = useCallback(
    async (update: SensorsStateUpdate) => {
      setIsUpdating(true);
      setError(null);
      try {
        const updated = await client.sensors.updateSensorsState(update);
        setSensors(updated);
        return updated;
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsUpdating(false);
      }
    },
    [client]
  );

  const setAccelerometer = useCallback(
    (vec: { x: number; y: number; z: number }) =>
      updateSensors({ acceleration: vec }),
    [updateSensors]
  );

  const setOrientation = useCallback(
    (rot: { azimuth: number; pitch: number; roll: number }) =>
      updateSensors({
        orientationDegrees: { x: rot.azimuth, y: rot.pitch, z: rot.roll },
      }),
    [updateSensors]
  );

  const setAmbientLight = useCallback(
    (lux: number) => updateSensors({ lightLux: lux }),
    [updateSensors]
  );

  const setProximity = useCallback(
    (distanceCm: number) => updateSensors({ proximityCentimeters: distanceCm }),
    [updateSensors]
  );

  const setTemperature = useCallback(
    (celsius: number) => updateSensors({ ambientTemperatureCelsius: celsius }),
    [updateSensors]
  );

  return {
    sensors,
    fetchSensors,
    updateSensors,
    setAccelerometer,
    setOrientation,
    setAmbientLight,
    setProximity,
    setTemperature,
    isUpdating,
    error,
  };
}

/**
 * Hook to manage geographic location.
 */
export function useLocation(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [location, setLocationState] = useState<LocationState | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchLocation = useCallback(async () => {
    setIsUpdating(true);
    setError(null);
    try {
      const state = await client.location.getLocationState();
      setLocationState(state);
      return state;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsUpdating(false);
    }
  }, [client]);

  useEffect(() => {
    fetchLocation().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.location.streamLocation(
      (state) => setLocationState(state),
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchLocation, options.stream]);

  const setLocation = useCallback(
    async (loc: LocationUpdate) => {
      setIsUpdating(true);
      setError(null);
      try {
        await client.location.setLocation(loc);
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsUpdating(false);
      }
    },
    [client]
  );

  return {
    location,
    fetchLocation,
    setLocation,
    isUpdating,
    error,
  };
}

/**
 * Hook to query device capabilities.
 */
export function useCapabilities() {
  const client = useDeviceClient();
  const [capabilities, setCapabilities] = useState<DeviceCapabilities | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchCapabilities = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const caps = await client.capabilities.getDeviceCapabilities();
      setCapabilities(caps);
      return caps;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  return {
    capabilities,
    isLoading,
    error,
    fetchCapabilities,
  };
}

/**
 * Hook to pause, resume, and inspect the VM lifecycle.
 */
export function useVm(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [vmState, setVmState] = useState<VmState | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (options.stream === false) return;

    const cancel = client.vm.streamVmState(
      (state) => setVmState(state),
      (err) => setError(err)
    );
    return cancel;
  }, [client, options.stream]);

  const pause = useCallback(async () => {
    setIsBusy(true);
    setError(null);
    try {
      await client.vm.pause();
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsBusy(false);
    }
  }, [client]);

  const resume = useCallback(async () => {
    setIsBusy(true);
    setError(null);
    try {
      await client.vm.resume();
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsBusy(false);
    }
  }, [client]);

  return {
    vmState,
    pause,
    resume,
    isBusy,
    error,
  };
}

/**
 * Hook to read and control emulator battery state.
 */
export function useBattery(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [battery, setBattery] = useState<BatteryState | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchBattery = useCallback(async () => {
    setIsUpdating(true);
    setError(null);
    try {
      const state = await client.battery.getBatteryState();
      setBattery(state);
      return state;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsUpdating(false);
    }
  }, [client]);

  useEffect(() => {
    fetchBattery().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.battery.streamBatteryState(
      (state) => setBattery(state),
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchBattery, options.stream]);

  const setBatteryLevel = useCallback(
    async (percent: number) => {
      setIsUpdating(true);
      setError(null);
      try {
        const state = await client.battery.setBatteryLevel(percent);
        setBattery(state);
        return state;
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsUpdating(false);
      }
    },
    [client]
  );

  const setChargingState = useCallback(
    async (state: BatteryState_State, charger?: ChargerSource) => {
      setIsUpdating(true);
      setError(null);
      try {
        const res = await client.battery.setChargingState(state, charger);
        setBattery(res);
        return res;
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsUpdating(false);
      }
    },
    [client]
  );

  return {
    battery,
    fetchBattery,
    setBatteryLevel,
    setChargingState,
    isUpdating,
    error,
  };
}

/**
 * Hook to read and control foldable device posture and hinge angle.
 */
export function usePosture(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [posture, setPosture] = useState<PostureState | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchPosture = useCallback(async () => {
    setIsUpdating(true);
    setError(null);
    try {
      const state = await client.posture.getPostureState();
      setPosture(state);
      return state;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsUpdating(false);
    }
  }, [client]);

  useEffect(() => {
    fetchPosture().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.posture.streamPostureState(
      (state) => setPosture(state),
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchPosture, options.stream]);

  const setHingeAngle = useCallback(
    async (degrees: number) => {
      setIsUpdating(true);
      setError(null);
      try {
        const state = await client.posture.setHingeAngle(degrees);
        setPosture(state);
        return state;
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsUpdating(false);
      }
    },
    [client]
  );

  const setDevicePosture = useCallback(
    async (type: PostureType) => {
      setIsUpdating(true);
      setError(null);
      try {
        const state = await client.posture.setDevicePosture(type);
        setPosture(state);
        return state;
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsUpdating(false);
      }
    },
    [client]
  );

  return {
    posture,
    fetchPosture,
    setHingeAngle,
    setDevicePosture,
    isUpdating,
    error,
  };
}

/**
 * Hook to read and write guest clipboard content.
 */
export function useClipboard(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [clipboardText, setClipboardTextState] = useState<string>('');
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchClipboard = useCallback(async () => {
    setIsUpdating(true);
    setError(null);
    try {
      const text = await client.clipboard.getClipboardText();
      setClipboardTextState(text);
      return text;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsUpdating(false);
    }
  }, [client]);

  useEffect(() => {
    fetchClipboard().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.clipboard.streamClipboard(
      (state) => {
        if (state.data && state.data.length > 0) {
          setClipboardTextState(new TextDecoder().decode(state.data));
        } else {
          setClipboardTextState('');
        }
      },
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchClipboard, options.stream]);

  const setClipboardText = useCallback(
    async (text: string) => {
      setIsUpdating(true);
      setError(null);
      try {
        await client.clipboard.setClipboardText(text);
        setClipboardTextState(text);
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsUpdating(false);
      }
    },
    [client]
  );

  return {
    clipboardText,
    fetchClipboard,
    setClipboardText,
    isUpdating,
    error,
  };
}

/**
 * Hook to manage emulator VM snapshots and quickboot targets.
 */
export function useSnapshots() {
  const client = useDeviceClient();
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchSnapshots = useCallback(
    async (compatibleOnly: boolean = false) => {
      setIsLoading(true);
      setError(null);
      try {
        const list = await client.snapshots.listSnapshots(compatibleOnly);
        setSnapshots(list);
        return list;
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [client]
  );

  const createSnapshot = useCallback(
    async (snapshotId: string, displayName?: string, description?: string) => {
      setIsLoading(true);
      setError(null);
      try {
        await client.snapshots.createSnapshot(snapshotId, displayName, description);
        await fetchSnapshots();
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [client, fetchSnapshots]
  );

  const restoreSnapshot = useCallback(
    async (snapshotId: string) => {
      setIsLoading(true);
      setError(null);
      try {
        await client.snapshots.restoreSnapshot(snapshotId);
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [client]
  );

  const deleteSnapshot = useCallback(
    async (snapshotId: string) => {
      setIsLoading(true);
      setError(null);
      try {
        await client.snapshots.deleteSnapshot(snapshotId);
        await fetchSnapshots();
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [client, fetchSnapshots]
  );

  return {
    snapshots,
    isLoading,
    error,
    fetchSnapshots,
    createSnapshot,
    restoreSnapshot,
    deleteSnapshot,
  };
}

/**
 * Hook to inspect and control emulator displays and multi-display setups.
 */
export function useDisplay(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [displays, setDisplays] = useState<DisplayState[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchDisplays = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const list = await client.display.listDisplays();
      setDisplays(list);
      return list;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    fetchDisplays().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.display.streamDisplays(
      (list) => setDisplays(list),
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchDisplays, options.stream]);

  const createDisplay = useCallback(
    async (width: number, height: number, densityDpi: number) => {
      setIsLoading(true);
      setError(null);
      try {
        const created = await client.display.createDisplay(width, height, densityDpi);
        await fetchDisplays();
        return created;
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [client, fetchDisplays]
  );

  const deleteDisplay = useCallback(
    async (displayId: number) => {
      setIsLoading(true);
      setError(null);
      try {
        await client.display.deleteDisplay(displayId);
        await fetchDisplays();
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [client, fetchDisplays]
  );

  return {
    displays,
    isLoading,
    error,
    fetchDisplays,
    createDisplay,
    deleteDisplay,
  };
}

/**
 * Hook to manage audio streaming and microphone input.
 */
export function useAudio() {
  const client = useDeviceClient();
  const [error, setError] = useState<Error | null>(null);

  const sendMicrophoneData = useCallback(
    async (pcmData: Uint8Array, sampleRateHz: number = 16000) => {
      setError(null);
      try {
        await client.audio.sendMicrophoneChunk({ pcmData, sampleRateHz });
      } catch (err: any) {
        setError(err);
        throw err;
      }
    },
    [client]
  );

  return {
    error,
    sendMicrophoneData,
  };
}

/**
 * Hook to inject biometric fingerprint events into the guest OS.
 */
export function useBiometrics() {
  const client = useDeviceClient();
  const [isInjecting, setIsInjecting] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const injectFingerprint = useCallback(
    async (fingerId: number = 1) => {
      setIsInjecting(true);
      setError(null);
      try {
        await client.biometrics.sendFingerprint(fingerId, true);
        setTimeout(() => {
          client.biometrics.sendFingerprint(fingerId, false).catch(() => {});
        }, 100);
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsInjecting(false);
      }
    },
    [client]
  );

  return {
    isInjecting,
    error,
    injectFingerprint,
  };
}

/**
 * Hook to read and control cellular radio state, send SMS, and simulate calls.
 */
export function useTelephony(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [radio, setRadio] = useState<RadioState | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchRadioState = useCallback(async () => {
    setIsUpdating(true);
    setError(null);
    try {
      const state = await client.telephony.getRadioState();
      setRadio(state);
      return state;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsUpdating(false);
    }
  }, [client]);

  useEffect(() => {
    fetchRadioState().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.telephony.streamRadioState(
      (state) => setRadio(state),
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchRadioState, options.stream]);

  const sendSms = useCallback(
    async (sender: string, message: string) => {
      setError(null);
      try {
        await client.telephony.sendSms(sender, message);
      } catch (err: any) {
        setError(err);
        throw err;
      }
    },
    [client]
  );

  const simulateIncomingCall = useCallback(
    async (caller: string, action?: SimulateIncomingCallRequest_Action) => {
      setError(null);
      try {
        await client.telephony.simulateIncomingCall(caller, action);
      } catch (err: any) {
        setError(err);
        throw err;
      }
    },
    [client]
  );

  return {
    radio,
    fetchRadioState,
    sendSms,
    simulateIncomingCall,
    isUpdating,
    error,
  };
}

/**
 * Hook to capture static screenshots from the emulator.
 */
export function useScreenCapture() {
  const client = useDeviceClient();
  const [isCapturing, setIsCapturing] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const captureScreenshot = useCallback(
    async (options?: ScreenshotOptions) => {
      setIsCapturing(true);
      setError(null);
      try {
        return await client.screenCapture.getScreenshot(options);
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsCapturing(false);
      }
    },
    [client]
  );

  return {
    isCapturing,
    error,
    captureScreenshot,
  };
}

/**
 * Hook to inject images or QR codes into the camera feed and reset camera scenes.
 */
export function useCamera() {
  const client = useDeviceClient();
  const [isInjecting, setIsInjecting] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const injectImage = useCallback(
    async (options: InjectFrameOptions) => {
      setIsInjecting(true);
      setError(null);
      try {
        await client.camera.injectFrame(options);
      } catch (err: any) {
        setError(err);
        throw err;
      } finally {
        setIsInjecting(false);
      }
    },
    [client]
  );

  const resetCamera = useCallback(
    async (cameraId: string = '0') => {
      setError(null);
      try {
        await client.camera.resetCamera(cameraId);
      } catch (err: any) {
        setError(err);
        throw err;
      }
    },
    [client]
  );

  return {
    isInjecting,
    error,
    injectImage,
    resetCamera,
  };
}

/**
 * Hook to inspect guest OS lifecycle, adb connection status, and trigger reboots.
 */
export function useSystem(options: { stream?: boolean } = { stream: true }) {
  const client = useDeviceClient();
  const [systemState, setSystemState] = useState<SystemState | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchSystemState = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const state = await client.system.getSystemState();
      setSystemState(state);
      return state;
    } catch (err: any) {
      setError(err);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    fetchSystemState().catch(() => {});
    if (options.stream === false) return;

    const cancel = client.system.subscribeSystemState(
      (state) => setSystemState(state),
      (err) => setError(err)
    );
    return cancel;
  }, [client, fetchSystemState, options.stream]);

  const rebootDevice = useCallback(async () => {
    setError(null);
    try {
      await client.system.rebootDevice();
    } catch (err: any) {
      setError(err);
      throw err;
    }
  }, [client]);

  return {
    systemState,
    fetchSystemState,
    rebootDevice,
    isLoading,
    error,
  };
}

// Backward compatibility aliases
export { useDevice as useAquarium };
