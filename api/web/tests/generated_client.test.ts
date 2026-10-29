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

import { describe, it, expect, vi } from 'vitest';

import {
  AudioServiceClientImpl,
} from '@android/emulator-services/audio_service';
import {
  HypervisorServiceClientImpl,
  VmState,
  VmState_State,
} from '@android/emulator-services/hypervisor_service';
import {
  EmulatorControllerClientImpl,
  EmulatorStatus,
} from '../src/deprecated/v1/emulator_controller';
import {
  AvdFlavor,
  AvdInfo,
  AvdServiceClientImpl,
} from '../src/deprecated/v1/incubating/avd_service';
import { CarServiceClientImpl } from '../src/deprecated/v1/incubating/car_service';
import {
  CellInfo,
  CellInfo_CellStandard,
  CellSignalStrength_CellSignalLevel,
  ModemClientImpl,
} from '../src/deprecated/v1/incubating/modem_service';
import { ScreenRecordingClientImpl } from '../src/deprecated/v1/incubating/screen_recording_service';
import {
  PhysicalModelValue,
  PhysicalModelValue_ParameterValueType,
  PhysicalModelValue_PhysicalType,
  SensorServiceClientImpl,
} from '../src/deprecated/v1/incubating/sensor_service';
import { VehicleServiceClientImpl } from '../src/deprecated/v1/incubating/vehicle_service';
import { VirtualSceneServiceClientImpl } from '../src/deprecated/v1/incubating/virtual_scene_service';

import { DeviceError } from '../src/core/auth';
import { GrpcWebTransport } from '../src/core/transport';
import { FakeTransport } from './fake_transport';

describe('generated client compatibility', () => {
  it('constructs a generated client from the real transport', () => {
    const transport = new GrpcWebTransport({ endpoint: 'http://localhost:8554' });

    // A compile error here is the point of the test.
    const hypervisor = new HypervisorServiceClientImpl(transport);
    const v1Controller = new EmulatorControllerClientImpl(transport);
    const avdService = new AvdServiceClientImpl(transport);
    const carService = new CarServiceClientImpl(transport);
    const modemService = new ModemClientImpl(transport);
    const screenRecording = new ScreenRecordingClientImpl(transport);
    const sensorService = new SensorServiceClientImpl(transport);
    const vehicleService = new VehicleServiceClientImpl(transport);
    const virtualScene = new VirtualSceneServiceClientImpl(transport);

    expect(hypervisor).toBeDefined();
    expect(v1Controller).toBeDefined();
    expect(avdService).toBeDefined();
    expect(carService).toBeDefined();
    expect(modemService).toBeDefined();
    expect(screenRecording).toBeDefined();
    expect(sensorService).toBeDefined();
    expect(vehicleService).toBeDefined();
    expect(virtualScene).toBeDefined();
  });

  it('routes a unary call to the service and method named in the proto', async () => {
    const request = vi.fn().mockResolvedValue(
      VmState.encode({
        state: VmState_State.PAUSED,
        allocatedCpuCoreCount: 4,
        allocatedRamBytes: 2048,
      }).finish()
    );
    const hypervisor = new HypervisorServiceClientImpl({
      request,
      clientStreamingRequest: vi.fn(),
      serverStreamingRequest: vi.fn(),
      bidirectionalStreamingRequest: vi.fn(),
    });

    const state = await hypervisor.PauseVm({});

    const [service, method] = request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.HypervisorService');
    expect(method).toBe('PauseVm');
    expect(state.state).toBe(VmState_State.PAUSED);
    expect(state.allocatedCpuCoreCount).toBe(4);
  });

  it('routes a deprecated v1 EmulatorController unary call', async () => {
    const request = vi.fn().mockResolvedValue(
      EmulatorStatus.encode(
        EmulatorStatus.fromPartial({
          version: '35.1.0',
          uptime: 1000,
          booted: true,
        })
      ).finish()
    );
    const controller = new EmulatorControllerClientImpl({
      request,
      clientStreamingRequest: vi.fn(),
      serverStreamingRequest: vi.fn(),
      bidirectionalStreamingRequest: vi.fn(),
    });

    const status = await controller.getStatus({});

    const [service, method] = request.mock.calls[0];
    expect(service).toBe('android.emulation.control.EmulatorController');
    expect(method).toBe('getStatus');
    expect(status.version).toBe('35.1.0');
    expect(status.booted).toBe(true);
  });

  it('rejects client-streaming RPCs with UNIMPLEMENTED', async () => {
    const transport = new GrpcWebTransport({ endpoint: 'http://localhost:8554' });
    const audio = new AudioServiceClientImpl(transport);

    const attempt = audio.StreamMicrophoneAudio(
      (async function* () {
        yield { pcmData: new Uint8Array(0), sampleRateHz: 16000, channelCount: 1, format: 0, timestampNanos: 0 };
      })()
    );

    await expect(attempt).rejects.toThrow(DeviceError);
    await expect(attempt).rejects.toThrow(/not supported over gRPC-Web/);
  });

  it('routes an incubating AvdService unary call', async () => {
    const transport = new FakeTransport();
    transport.respondWith(
      AvdInfo.encode(
        AvdInfo.fromPartial({
          deviceName: 'Pixel_9_Pro_Fold',
          apiLevel: 37,
          flavor: AvdFlavor.AVD_FLAVOR_PHONE,
          skinName: 'pixel_9_pro_fold',
          configIni: { 'hw.sensor.posture_list': '1, 2, 3' },
        })
      ).finish()
    );

    const info = await new AvdServiceClientImpl(transport).getAvdInfo({});

    expect(transport.lastCall.service).toBe('android.emulation.control.incubating.AvdService');
    expect(transport.lastCall.method).toBe('getAvdInfo');
    expect(info.deviceName).toBe('Pixel_9_Pro_Fold');
    expect(info.apiLevel).toBe(37);
    expect(info.configIni['hw.sensor.posture_list']).toBe('1, 2, 3');
  });

  it('routes an incubating SensorService unary call', async () => {
    const transport = new FakeTransport();
    transport.respondWith(
      PhysicalModelValue.encode(
        PhysicalModelValue.fromPartial({
          target: PhysicalModelValue_PhysicalType.PHYSICAL_TYPE_POSTURE,
          value: { data: [3] },
          valueType: PhysicalModelValue_ParameterValueType.PARAMETER_VALUE_TYPE_CURRENT,
        })
      ).finish()
    );

    const model = await new SensorServiceClientImpl(transport).getPhysicalModel(
      PhysicalModelValue.fromPartial({
        target: PhysicalModelValue_PhysicalType.PHYSICAL_TYPE_POSTURE,
        valueType: PhysicalModelValue_ParameterValueType.PARAMETER_VALUE_TYPE_CURRENT,
      })
    );

    expect(transport.lastCall.service).toBe('android.emulation.control.incubating.SensorService');
    expect(transport.lastCall.method).toBe('getPhysicalModel');
    expect(model.target).toBe(PhysicalModelValue_PhysicalType.PHYSICAL_TYPE_POSTURE);
    expect(model.value?.data).toEqual([3]);
  });

  it('routes an incubating Modem unary call', async () => {
    const transport = new FakeTransport();
    transport.respondWith(
      CellInfo.encode(
        CellInfo.fromPartial({
          cellStandard: CellInfo_CellStandard.CELL_STANDARD_5G,
          cellSignalStrength: {
            level: CellSignalStrength_CellSignalLevel.SIGNAL_STRENGTH_GREAT,
          },
        })
      ).finish()
    );

    const cell = await new ModemClientImpl(transport).getCellInfo({});

    expect(transport.lastCall.service).toBe('android.emulation.control.incubating.Modem');
    expect(transport.lastCall.method).toBe('getCellInfo');
    expect(cell.cellStandard).toBe(CellInfo_CellStandard.CELL_STANDARD_5G);
    expect(cell.cellSignalStrength?.level).toBe(
      CellSignalStrength_CellSignalLevel.SIGNAL_STRENGTH_GREAT
    );
  });
});

