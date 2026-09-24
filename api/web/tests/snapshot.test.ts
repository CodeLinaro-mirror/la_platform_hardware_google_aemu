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

import { describe, it, expect, beforeEach } from 'vitest';
import { SnapshotClient } from '../src/core/snapshot';
import {
  ListSnapshotsRequest,
  ListSnapshotsResponse,
  Snapshot,
  CreateSnapshotRequest,
  RestoreSnapshotRequest,
  DeleteSnapshotRequest,
  SetQuickbootTargetRequest,
  ExportSnapshotRequest,
  ImportSnapshotRequest,
} from '@android/emulator-services/hypervisor_service';
import { Operation } from '@android/emulator-services/google/longrunning/operations';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.HypervisorService';

describe('SnapshotClient', () => {
  let transport: FakeTransport;
  let client: SnapshotClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new SnapshotClient(transport.asTransport());
  });

  function encodeOperation(name: string, done: boolean): Uint8Array {
    return Operation.encode(Operation.fromPartial({ name, done })).finish();
  }

  function encodeEmpty(): Uint8Array {
    return new Uint8Array();
  }

  it('decodes the snapshot list', async () => {
    const snapshots: Snapshot[] = [
      Snapshot.fromPartial({
        snapshotId: 'snap_boot',
        displayName: 'Boot Snapshot',
        description: 'Clean quickboot state',
        sizeBytes: 1048576,
        quickbootTarget: true,
      }),
    ];
    transport.respondWith(ListSnapshotsResponse.encode({ snapshots }).finish());

    const response = await client.ListSnapshots({ compatibleOnly: false });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('ListSnapshots');
    expect(ListSnapshotsRequest.decode(transport.lastCall.data).compatibleOnly).toBe(false);
    expect(response.snapshots).toHaveLength(1);
    expect(response.snapshots[0].snapshotId).toBe('snap_boot');
    expect(response.snapshots[0].quickbootTarget).toBe(true);
  });

  it('forwards the compatibility filter to ListSnapshots', async () => {
    transport.respondWith(ListSnapshotsResponse.encode({ snapshots: [] }).finish());

    const response = await client.ListSnapshots({ compatibleOnly: true });

    expect(ListSnapshotsRequest.decode(transport.lastCall.data).compatibleOnly).toBe(true);
    expect(response.snapshots).toEqual([]);
  });

  it('decodes the snapshot returned by GetSnapshot', async () => {
    transport.respondWith(
      Snapshot.encode(Snapshot.fromPartial({ snapshotId: 'snap_boot', sizeBytes: 42 })).finish()
    );

    const result = await client.GetSnapshot({ snapshotId: 'snap_boot' });

    expect(transport.lastCall.method).toBe('GetSnapshot');
    expect(result.snapshotId).toBe('snap_boot');
    expect(result.sizeBytes).toBe(42);
  });

  it('sends the snapshot metadata to CreateSnapshot and decodes the Operation', async () => {
    transport.respondWith(encodeOperation('operations/create/1', false));

    const operation = await client.CreateSnapshot({
      snapshotId: 'test_checkpoint',
      displayName: 'My Checkpoint',
      description: 'Saved mid-test',
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('CreateSnapshot');
    const request = CreateSnapshotRequest.decode(transport.lastCall.data);
    expect(request.snapshotId).toBe('test_checkpoint');
    expect(request.displayName).toBe('My Checkpoint');
    expect(request.description).toBe('Saved mid-test');
    expect(operation.name).toBe('operations/create/1');
    expect(operation.done).toBe(false);
  });

  it('sends only the metadata the caller supplied', async () => {
    transport.respondWith(encodeOperation('operations/create/2', false));

    await client.CreateSnapshot({
      snapshotId: 'bare_id',
      displayName: 'Bare Checkpoint',
    });

    const request = CreateSnapshotRequest.decode(transport.lastCall.data);
    expect(request.snapshotId).toBe('bare_id');
    expect(request.displayName).toBe('Bare Checkpoint');
    expect(request.description).toBe('');
  });

  it('allows zero-argument CreateSnapshot so the server auto-generates a snapshotId', async () => {
    transport.respondWith(encodeOperation('operations/create/3', false));

    const operation = await client.CreateSnapshot();

    const request = CreateSnapshotRequest.decode(transport.lastCall.data);
    expect(request.snapshotId).toBe('');
    expect(operation.name).toBe('operations/create/3');
  });

  it('sends RestoreSnapshotRequest and decodes the Operation', async () => {
    transport.respondWith(encodeOperation('operations/restore/1', true));

    const operation = await client.RestoreSnapshot({ snapshotId: 'snap_boot' });

    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('RestoreSnapshot');
    expect(RestoreSnapshotRequest.decode(transport.lastCall.data).snapshotId).toBe('snap_boot');
    expect(operation.done).toBe(true);
  });

  it('sends DeleteSnapshotRequest', async () => {
    transport.respondWith(encodeEmpty());

    await client.DeleteSnapshot({ snapshotId: 'obsolete_snap' });

    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('DeleteSnapshot');
    expect(DeleteSnapshotRequest.decode(transport.lastCall.data).snapshotId).toBe(
      'obsolete_snap'
    );
  });

  it('promotes a snapshot to the quickboot target', async () => {
    transport.respondWith(encodeEmpty());

    await client.SetQuickbootTarget({ snapshotId: 'snap_boot' });

    expect(transport.lastCall.method).toBe('SetQuickbootTarget');
    expect(SetQuickbootTargetRequest.decode(transport.lastCall.data).snapshotId).toBe(
      'snap_boot'
    );
  });

  it('omits the id when demoting the quickboot target', async () => {
    transport.respondWith(encodeEmpty());

    await client.SetQuickbootTarget();

    expect(SetQuickbootTargetRequest.decode(transport.lastCall.data).snapshotId).toBeUndefined();
  });

  it('sends the storage destination to ExportSnapshot and decodes the Operation', async () => {
    transport.respondWith(encodeOperation('operations/export/1', false));

    const operation = await client.ExportSnapshot({
      snapshotId: 'snap_boot',
      storageUri: 'gs://aemu-snapshots/avd_123/snap.tar.zst',
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('ExportSnapshot');
    const request = ExportSnapshotRequest.decode(transport.lastCall.data);
    expect(request.snapshotId).toBe('snap_boot');
    expect(request.storageUri).toBe('gs://aemu-snapshots/avd_123/snap.tar.zst');
    expect(operation.name).toBe('operations/export/1');
    expect(operation.done).toBe(false);
  });

  it('sends a pre-signed upload url as the export destination', async () => {
    transport.respondWith(encodeOperation('operations/export/2', false));

    await client.ExportSnapshot({
      snapshotId: 'snap_boot',
      presignedUploadUrl: 'https://example.test/upload?sig=abc',
    });

    const request = ExportSnapshotRequest.decode(transport.lastCall.data);
    expect(request.presignedUploadUrl).toBe('https://example.test/upload?sig=abc');
    expect(request.storageUri).toBeUndefined();
  });

  it('sends the storage source to ImportSnapshot and decodes the Operation', async () => {
    transport.respondWith(encodeOperation('operations/import/1', true));

    const operation = await client.ImportSnapshot({
      snapshotId: 'golden_base',
      storageUri: 'gs://aemu-snapshots/golden_images/base.tar.zst',
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('ImportSnapshot');
    const request = ImportSnapshotRequest.decode(transport.lastCall.data);
    expect(request.snapshotId).toBe('golden_base');
    expect(request.storageUri).toBe('gs://aemu-snapshots/golden_images/base.tar.zst');
    expect(operation.name).toBe('operations/import/1');
    expect(operation.done).toBe(true);
  });

  it('sends a pre-signed download url as the import source', async () => {
    transport.respondWith(encodeOperation('operations/import/2', false));

    await client.ImportSnapshot({
      snapshotId: 'golden_base',
      presignedDownloadUrl: 'https://example.test/download?sig=xyz',
    });

    const request = ImportSnapshotRequest.decode(transport.lastCall.data);
    expect(request.presignedDownloadUrl).toBe('https://example.test/download?sig=xyz');
    expect(request.storageUri).toBeUndefined();
  });
});