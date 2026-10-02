# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Implementation of `android.emulation.v2.HypervisorService` and `google.longrunning.Operations`."""

import queue
import uuid
from collections.abc import Iterator

import grpc
import hypervisor_service_pb2 as pb
import hypervisor_service_pb2_grpc as pb_grpc
from google.longrunning import operations_pb2, operations_pb2_grpc
from google.protobuf import empty_pb2, timestamp_pb2

from clownfish.renderer import SyntheticFrameRenderer
from clownfish.state import EmulatorState, SnapshotEntry


class HypervisorServiceServicer(pb_grpc.HypervisorServiceServicer):
    """Stateful implementation of `android.emulation.v2.HypervisorService`."""

    def __init__(self, state: EmulatorState, renderer: SyntheticFrameRenderer) -> None:
        self.state = state
        self.renderer = renderer

    def _build_vm_state(self) -> pb.VmState:
        with self.state._lock:
            p = self.state.preset
            return pb.VmState(
                state=self.state.vm_state_v2,
                allocated_cpu_core_count=p.cpu_cores,
                allocated_ram_bytes=p.ram_mb * 1024 * 1024,
            )

    def _to_v2_snapshot(self, entry: SnapshotEntry) -> pb.Snapshot:
        p = self.state.preset
        ts = timestamp_pb2.Timestamp(seconds=entry.creation_time_s)
        return pb.Snapshot(
            snapshot_id=entry.snapshot_id,
            display_name=entry.display_name,
            description=entry.description,
            create_time=ts,
            size_bytes=entry.size_bytes,
            compatibility=pb.CompatibilityFingerprint(
                cpu_architecture=p.cpu_arch,
                cpu_features_hash="clownfish-sha256",
                system_image_build_id=f"API-{p.api_level}",
                emulator_build_id=p.emulator_build,
                gpu_renderer="clownfish-synthetic",
                host_compatible=True,
            ),
            quickboot_target=entry.quickboot_target,
        )

    def _create_completed_lro(
        self, snapshot_proto: pb.Snapshot
    ) -> operations_pb2.Operation:
        op_name = f"operations/snapshots/{uuid.uuid4().hex}"
        meta = pb.SnapshotOperationMetadata(
            phase=pb.SnapshotOperationMetadata.TRANSFERRING,
            progress_percent=100,
            snapshot_id=snapshot_proto.snapshot_id,
            transferred_bytes=snapshot_proto.size_bytes,
            total_bytes=snapshot_proto.size_bytes,
        )
        op = operations_pb2.Operation(name=op_name, done=True)
        op.metadata.Pack(meta)
        op.response.Pack(snapshot_proto)
        with self.state._lock:
            self.state.operations[op_name] = op
        return op

    def GetVmState(
        self, request: pb.GetVmStateRequest, context: grpc.ServicerContext
    ) -> pb.VmState:
        return self._build_vm_state()

    def StreamVmState(
        self, request: pb.StreamVmStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.VmState]:
        with self.state.subscribe("vm") as q:
            yield self._build_vm_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_vm_state()

    def PauseVm(
        self, request: pb.PauseVmRequest, context: grpc.ServicerContext
    ) -> pb.VmState:
        if not self.state.pause_vm_v2():
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "VM is not currently in RUNNING state.",
            )
        return self._build_vm_state()

    def ResumeVm(
        self, request: pb.ResumeVmRequest, context: grpc.ServicerContext
    ) -> pb.VmState:
        if not self.state.resume_vm_v2():
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "VM is not currently in PAUSED state.",
            )
        return self._build_vm_state()

    def ResetVm(
        self, request: pb.ResetVmRequest, context: grpc.ServicerContext
    ) -> pb.VmState:
        self.state.reset_vm_v2(hard=False)
        return self._build_vm_state()

    def HardResetVm(
        self, request: pb.HardResetVmRequest, context: grpc.ServicerContext
    ) -> pb.VmState:
        self.state.reset_vm_v2(hard=True)
        return self._build_vm_state()

    def StopVm(
        self, request: pb.StopVmRequest, context: grpc.ServicerContext
    ) -> pb.VmState:
        png = (
            self.renderer.render_frame(display_id=0, output_format="PNG").data
            if request.save_quickboot_snapshot
            else b""
        )
        self.state.stop_vm_v2(
            save_quickboot=request.save_quickboot_snapshot,
            screenshot_png=png,
        )
        return self._build_vm_state()

    def ListSnapshots(
        self, request: pb.ListSnapshotsRequest, context: grpc.ServicerContext
    ) -> pb.ListSnapshotsResponse:
        with self.state._lock:
            snaps = [
                self._to_v2_snapshot(entry)
                for _, entry in sorted(self.state.snapshots.items())
            ]
        return pb.ListSnapshotsResponse(snapshots=snaps)

    def GetSnapshot(
        self, request: pb.GetSnapshotRequest, context: grpc.ServicerContext
    ) -> pb.Snapshot:
        if not request.snapshot_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "snapshot_id is required.")
        with self.state._lock:
            entry = self.state.snapshots.get(request.snapshot_id)
            if entry is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Snapshot '{request.snapshot_id}' not found.",
                )
            return self._to_v2_snapshot(entry)

    def UpdateSnapshot(
        self, request: pb.UpdateSnapshotRequest, context: grpc.ServicerContext
    ) -> pb.Snapshot:
        if not request.snapshot_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "snapshot_id is required.")
        with self.state._lock:
            entry = self.state.snapshots.get(request.snapshot_id)
            if entry is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Snapshot '{request.snapshot_id}' not found.",
                )
            if request.HasField("display_name"):
                entry.display_name = request.display_name
            if request.HasField("description"):
                entry.description = request.description
            return self._to_v2_snapshot(entry)

    def CreateSnapshot(
        self, request: pb.CreateSnapshotRequest, context: grpc.ServicerContext
    ) -> operations_pb2.Operation:
        snap_id = request.snapshot_id or f"snap_{uuid.uuid4().hex[:8]}"
        if "/" in snap_id or "\\" in snap_id or ".." in snap_id:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Invalid characters in snapshot_id '{snap_id}'.",
            )
        frame = self.renderer.render_frame(display_id=0, output_format="PNG")
        ok, err_msg, entry = self.state.save_snapshot(
            snapshot_id=snap_id,
            display_name=request.display_name,
            description=request.description,
            screenshot_png=frame.data,
        )
        if not ok or entry is None:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, err_msg)
        return self._create_completed_lro(self._to_v2_snapshot(entry))

    def RestoreSnapshot(
        self, request: pb.RestoreSnapshotRequest, context: grpc.ServicerContext
    ) -> operations_pb2.Operation:
        if not request.snapshot_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "snapshot_id is required.")
        ok, err_msg, entry = self.state.load_snapshot(request.snapshot_id)
        if not ok or entry is None:
            context.abort(grpc.StatusCode.NOT_FOUND, err_msg)
        return self._create_completed_lro(self._to_v2_snapshot(entry))

    def DeleteSnapshot(
        self, request: pb.DeleteSnapshotRequest, context: grpc.ServicerContext
    ) -> pb.DeleteSnapshotResponse:
        if not request.snapshot_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "snapshot_id is required.")
        with self.state._lock:
            if request.snapshot_id not in self.state.snapshots:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Snapshot '{request.snapshot_id}' not found.",
                )
            del self.state.snapshots[request.snapshot_id]
        return pb.DeleteSnapshotResponse()

    def SetQuickbootTarget(
        self, request: pb.SetQuickbootTargetRequest, context: grpc.ServicerContext
    ) -> pb.SetQuickbootTargetResponse:
        target_id = request.snapshot_id if request.HasField("snapshot_id") else ""
        with self.state._lock:
            if target_id and target_id not in self.state.snapshots:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Snapshot '{target_id}' not found.",
                )
            for sid, entry in self.state.snapshots.items():
                entry.quickboot_target = bool(target_id and sid == target_id)
        return pb.SetQuickbootTargetResponse()

    def ExportSnapshot(
        self, request: pb.ExportSnapshotRequest, context: grpc.ServicerContext
    ) -> operations_pb2.Operation:
        if not request.snapshot_id or not request.WhichOneof("destination"):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "snapshot_id and destination are required.",
            )
        with self.state._lock:
            entry = self.state.snapshots.get(request.snapshot_id)
            if entry is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Snapshot '{request.snapshot_id}' not found.",
                )
            snap_proto = self._to_v2_snapshot(entry)
        return self._create_completed_lro(snap_proto)

    def ImportSnapshot(
        self, request: pb.ImportSnapshotRequest, context: grpc.ServicerContext
    ) -> operations_pb2.Operation:
        if not request.WhichOneof("source"):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "source is required.")
        snap_id = request.snapshot_id or f"imported_{uuid.uuid4().hex[:8]}"
        frame = self.renderer.render_frame(display_id=0, output_format="PNG")
        ok, err_msg, entry = self.state.save_snapshot(
            snapshot_id=snap_id,
            screenshot_png=frame.data,
            imported=True,
        )
        if not ok or entry is None:
            context.abort(grpc.StatusCode.INTERNAL, err_msg)
        return self._create_completed_lro(self._to_v2_snapshot(entry))


class OperationsServicer(operations_pb2_grpc.OperationsServicer):
    """Implementation of `google.longrunning.Operations` for snapshot LROs."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def GetOperation(
        self,
        request: operations_pb2.GetOperationRequest,
        context: grpc.ServicerContext,
    ) -> operations_pb2.Operation:
        with self.state._lock:
            op = self.state.operations.get(request.name)
            if op is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Operation '{request.name}' not found.",
                )
            return op

    def ListOperations(
        self,
        request: operations_pb2.ListOperationsRequest,
        context: grpc.ServicerContext,
    ) -> operations_pb2.ListOperationsResponse:
        with self.state._lock:
            ops = list(self.state.operations.values())
        return operations_pb2.ListOperationsResponse(operations=ops)

    def WaitOperation(
        self,
        request: operations_pb2.WaitOperationRequest,
        context: grpc.ServicerContext,
    ) -> operations_pb2.Operation:
        return self.GetOperation(
            operations_pb2.GetOperationRequest(name=request.name), context
        )

    def CancelOperation(
        self,
        request: operations_pb2.CancelOperationRequest,
        context: grpc.ServicerContext,
    ) -> empty_pb2.Empty:
        return empty_pb2.Empty()

    def DeleteOperation(
        self,
        request: operations_pb2.DeleteOperationRequest,
        context: grpc.ServicerContext,
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.operations.pop(request.name, None)
        return empty_pb2.Empty()
