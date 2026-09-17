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

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { SnapshotClient } from '../src/core/snapshot';
import {
  ListSnapshotsResponse,
  Snapshot,
  CreateSnapshotRequest,
  RestoreSnapshotRequest,
  DeleteSnapshotRequest,
} from '@android/emulator-services/snapshot/snapshot_service';

describe('SnapshotClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('listSnapshots decodes snapshot list', async () => {
    const client = new SnapshotClient(mockTransport);

    const mockSnapshots: Snapshot[] = [
      {
        snapshotId: 'snap_boot',
        displayName: 'Boot Snapshot',
        description: 'Clean quickboot state',
        sizeBytes: 1048576,
        quickbootTarget: true,
      },
    ];

    mockTransport.request.mockResolvedValue(
      ListSnapshotsResponse.encode({
        snapshots: mockSnapshots,
      }).finish()
    );

    const result = await client.listSnapshots(false);
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.snapshot.SnapshotService');
    expect(method).toBe('ListSnapshots');
    expect(result).toHaveLength(1);
    expect(result[0].snapshotId).toBe('snap_boot');
    expect(result[0].quickbootTarget).toBe(true);
  });

  it('createSnapshot encodes CreateSnapshotRequest', async () => {
    const client = new SnapshotClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.createSnapshot('test_checkpoint', 'My Checkpoint', 'Saved mid-test');
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.snapshot.SnapshotService');
    expect(method).toBe('CreateSnapshot');

    const decoded = CreateSnapshotRequest.decode(data);
    expect(decoded.snapshot?.snapshotId).toBe('test_checkpoint');
    expect(decoded.snapshot?.displayName).toBe('My Checkpoint');
    expect(decoded.snapshot?.description).toBe('Saved mid-test');
  });

  it('restoreSnapshot sends RestoreSnapshotRequest', async () => {
    const client = new SnapshotClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.restoreSnapshot('snap_boot');
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.snapshot.SnapshotService');
    expect(method).toBe('RestoreSnapshot');

    const decoded = RestoreSnapshotRequest.decode(data);
    expect(decoded.snapshotId).toBe('snap_boot');
  });

  it('deleteSnapshot sends DeleteSnapshotRequest', async () => {
    const client = new SnapshotClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.deleteSnapshot('obsolete_snap');
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.snapshot.SnapshotService');
    expect(method).toBe('DeleteSnapshot');

    const decoded = DeleteSnapshotRequest.decode(data);
    expect(decoded.snapshotId).toBe('obsolete_snap');
  });
});