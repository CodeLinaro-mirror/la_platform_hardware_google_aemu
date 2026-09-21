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
  CreateSnapshotRequest,
  DeepPartial,
  DeleteSnapshotRequest,
  DeleteSnapshotResponse,
  ExportSnapshotRequest,
  GetSnapshotRequest,
  HypervisorService,
  HypervisorServiceClientImpl,
  ImportSnapshotRequest,
  ListSnapshotsRequest,
  ListSnapshotsResponse,
  RestoreSnapshotRequest,
  SetQuickbootTargetRequest,
  SetQuickbootTargetResponse,
  Snapshot,
  UpdateSnapshotRequest,
} from '@android/emulator-services/hypervisor_service';
import { Operation } from '@android/emulator-services/google/longrunning/operations';

export {
  Snapshot,
  ListSnapshotsResponse,
  Operation,
  DeleteSnapshotResponse,
  SetQuickbootTargetResponse,
  UpdateSnapshotRequest,
};

/**
 * Snapshot management client for the Android Emulator.
 *
 * Wraps snapshot RPCs on the gRPC-Web `HypervisorService`.
 */
export class SnapshotClient {
  private readonly rpc: HypervisorService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new HypervisorServiceClientImpl(transport);
  }

  public ListSnapshots(
    request: DeepPartial<ListSnapshotsRequest> = {}
  ): Promise<ListSnapshotsResponse> {
    return this.rpc.ListSnapshots(ListSnapshotsRequest.fromPartial(request));
  }

  public GetSnapshot(request: DeepPartial<GetSnapshotRequest> = {}): Promise<Snapshot> {
    return this.rpc.GetSnapshot(GetSnapshotRequest.fromPartial(request));
  }

  public UpdateSnapshot(request: DeepPartial<UpdateSnapshotRequest>): Promise<Snapshot> {
    return this.rpc.UpdateSnapshot(UpdateSnapshotRequest.fromPartial(request));
  }

  /** Starts a snapshot capture. Omitting `snapshotId` requests a server-generated ID. */
  public CreateSnapshot(request: DeepPartial<CreateSnapshotRequest> = {}): Promise<Operation> {
    return this.rpc.CreateSnapshot(CreateSnapshotRequest.fromPartial(request));
  }

  /** Starts a restore. */
  public RestoreSnapshot(request: DeepPartial<RestoreSnapshotRequest>): Promise<Operation> {
    return this.rpc.RestoreSnapshot(RestoreSnapshotRequest.fromPartial(request));
  }

  public DeleteSnapshot(
    request: DeepPartial<DeleteSnapshotRequest>
  ): Promise<DeleteSnapshotResponse> {
    return this.rpc.DeleteSnapshot(DeleteSnapshotRequest.fromPartial(request));
  }

  /**
   * Promotes a snapshot to the quickboot target. Omitting `snapshotId` clears
   * the target, forcing a cold boot on the next launch.
   */
  public SetQuickbootTarget(
    request: DeepPartial<SetQuickbootTargetRequest> = {}
  ): Promise<SetQuickbootTargetResponse> {
    return this.rpc.SetQuickbootTarget(SetQuickbootTargetRequest.fromPartial(request));
  }

  /** Starts an upload of a snapshot to the requested destination. */
  public ExportSnapshot(request: DeepPartial<ExportSnapshotRequest>): Promise<Operation> {
    return this.rpc.ExportSnapshot(ExportSnapshotRequest.fromPartial(request));
  }

  /** Starts a download of a snapshot from the requested source. */
  public ImportSnapshot(request: DeepPartial<ImportSnapshotRequest>): Promise<Operation> {
    return this.rpc.ImportSnapshot(ImportSnapshotRequest.fromPartial(request));
  }
}