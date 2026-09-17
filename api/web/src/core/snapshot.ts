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
  Snapshot,
  ListSnapshotsRequest,
  ListSnapshotsResponse,
  GetSnapshotRequest,
  CreateSnapshotRequest,
  RestoreSnapshotRequest,
  DeleteSnapshotRequest,
  SetQuickbootTargetRequest,
} from '@android/emulator-services/snapshot/snapshot_service';

export { Snapshot };

/**
 * Headless API client for SnapshotService.
 */
export class SnapshotClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async listSnapshots(compatibleOnly: boolean = false): Promise<Snapshot[]> {
    const req: ListSnapshotsRequest = { compatibleOnly };
    const reqBytes = ListSnapshotsRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.snapshot.SnapshotService',
      'ListSnapshots',
      reqBytes
    );
    const res = ListSnapshotsResponse.decode(resBytes);
    return res.snapshots ?? [];
  }

  public async getSnapshot(snapshotId: string): Promise<Snapshot> {
    const req: GetSnapshotRequest = { snapshotId };
    const reqBytes = GetSnapshotRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.snapshot.SnapshotService',
      'GetSnapshot',
      reqBytes
    );
    return Snapshot.decode(resBytes);
  }

  public async createSnapshot(
    snapshotId: string,
    displayName: string = snapshotId,
    description: string = ''
  ): Promise<Uint8Array> {
    const req: CreateSnapshotRequest = {
      snapshot: {
        snapshotId,
        displayName,
        description,
        sizeBytes: 0,
        quickbootTarget: false,
      },
    };
    const reqBytes = CreateSnapshotRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.snapshot.SnapshotService',
      'CreateSnapshot',
      reqBytes
    );
  }

  public async restoreSnapshot(snapshotId: string): Promise<Uint8Array> {
    const req: RestoreSnapshotRequest = { snapshotId };
    const reqBytes = RestoreSnapshotRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.snapshot.SnapshotService',
      'RestoreSnapshot',
      reqBytes
    );
  }

  public async deleteSnapshot(snapshotId: string): Promise<Uint8Array> {
    const req: DeleteSnapshotRequest = { snapshotId };
    const reqBytes = DeleteSnapshotRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.snapshot.SnapshotService',
      'DeleteSnapshot',
      reqBytes
    );
  }

  public async setQuickbootTarget(snapshotId?: string): Promise<Uint8Array> {
    const req: SetQuickbootTargetRequest = { snapshotId };
    const reqBytes = SetQuickbootTargetRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.snapshot.SnapshotService',
      'SetQuickbootTarget',
      reqBytes
    );
  }
}