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

"""Implementation of `android.emulation.control.SnapshotService` (v1)."""

import io
import os
import tarfile
import tempfile
from collections.abc import Iterator
from pathlib import Path

import grpc
from aemu.proto import snapshot_pb2
from aemu.proto import snapshot_service_pb2 as pb
from aemu.proto import snapshot_service_pb2_grpc as pb_grpc

from clownfish.renderer import SyntheticFrameRenderer
from clownfish.state import EmulatorState, SnapshotEntry

_MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024


class SnapshotServiceServicer(pb_grpc.SnapshotServiceServicer):
    """Stateful implementation of `android.emulation.control.SnapshotService`."""

    def __init__(self, state: EmulatorState, renderer: SyntheticFrameRenderer) -> None:
        self.state = state
        self.renderer = renderer

    @staticmethod
    def _resolve_allowed_snapshot_path(path: str) -> str | None:
        if not path or ".." in Path(path).parts or os.path.islink(path):
            return None
        allowed_dir = os.path.realpath(tempfile.gettempdir())
        candidate = path if os.path.isabs(path) else os.path.join(allowed_dir, path)
        real_path = os.path.realpath(candidate)
        try:
            if (
                os.path.commonpath([real_path, allowed_dir]) != allowed_dir
                or real_path == allowed_dir
            ):
                return None
        except ValueError:
            return None
        return real_path

    def _to_snapshot_details(self, entry: SnapshotEntry) -> pb.SnapshotDetails:
        snap_meta = snapshot_pb2.Snapshot(
            version=1,
            creation_time=entry.creation_time_s,
            logical_name=entry.display_name,
            description=entry.description,
            rotation=entry.rotation_skin,
            folded=entry.folded,
            config=snapshot_pb2.Config(
                cpu_core_count=self.state.preset.cpu_cores,
                ram_size_bytes=self.state.preset.ram_mb * 1024 * 1024,
            ),
        )
        return pb.SnapshotDetails(
            snapshot_id=entry.snapshot_id,
            details=snap_meta,
            status=pb.SnapshotDetails.Compatible,
            size=entry.size_bytes,
        )

    def SaveSnapshot(
        self, request: pb.SnapshotPackage, context: grpc.ServicerContext
    ) -> pb.SnapshotPackage:
        if not request.snapshot_id:
            return pb.SnapshotPackage(
                snapshot_id="",
                success=False,
                err=b"snapshot_id must not be empty",
            )
        frame = self.renderer.render_frame(display_id=0, output_format="PNG")
        ok, err_msg, _ = self.state.save_snapshot(
            snapshot_id=request.snapshot_id,
            screenshot_png=frame.data,
            imported=False,
        )
        return pb.SnapshotPackage(
            snapshot_id=request.snapshot_id,
            success=ok,
            err=err_msg.encode("utf-8"),
        )

    def LoadSnapshot(
        self, request: pb.SnapshotPackage, context: grpc.ServicerContext
    ) -> pb.SnapshotPackage:
        ok, err_msg, _ = self.state.load_snapshot(request.snapshot_id)
        return pb.SnapshotPackage(
            snapshot_id=request.snapshot_id,
            success=ok,
            err=err_msg.encode("utf-8"),
        )

    def ListSnapshots(
        self, request: pb.SnapshotFilter, context: grpc.ServicerContext
    ) -> pb.SnapshotList:
        with self.state._lock:
            details = [
                self._to_snapshot_details(entry)
                for _, entry in sorted(self.state.snapshots.items())
            ]
        return pb.SnapshotList(snapshots=details)

    def DeleteSnapshot(
        self, request: pb.SnapshotPackage, context: grpc.ServicerContext
    ) -> pb.SnapshotPackage:
        with self.state._lock:
            self.state.snapshots.pop(request.snapshot_id, None)
        return pb.SnapshotPackage(snapshot_id=request.snapshot_id, success=True)

    def UpdateSnapshot(
        self, request: pb.SnapshotUpdateDescription, context: grpc.ServicerContext
    ) -> pb.SnapshotDetails:
        if not request.HasField("description") and not request.HasField("logical_name"):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Either description or logical_name must be provided.",
            )
        with self.state._lock:
            entry = self.state.snapshots.get(request.snapshot_id)
            if entry is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Snapshot '{request.snapshot_id}' does not exist.",
                )
            if request.HasField("description"):
                entry.description = request.description.value
            if request.HasField("logical_name"):
                entry.display_name = request.logical_name.value
            return self._to_snapshot_details(entry)

    def GetScreenshot(
        self, request: pb.SnapshotId, context: grpc.ServicerContext
    ) -> pb.SnapshotScreenshotFile:
        with self.state._lock:
            entry = self.state.snapshots.get(request.snapshot_id)
            if entry is None or not entry.screenshot_png:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Screenshot for snapshot '{request.snapshot_id}' not found.",
                )
            return pb.SnapshotScreenshotFile(
                format=pb.SnapshotScreenshotFile.FORMAT_PNG,
                data=entry.screenshot_png,
            )

    def PullSnapshot(
        self, request: pb.SnapshotPackage, context: grpc.ServicerContext
    ) -> Iterator[pb.SnapshotPackage]:
        with self.state._lock:
            entry = self.state.snapshots.get(request.snapshot_id)
            if entry is None:
                yield pb.SnapshotPackage(
                    snapshot_id=request.snapshot_id,
                    success=False,
                    err=b"Snapshot does not exist",
                )
                return
            details = self._to_snapshot_details(entry)
            png_bytes = entry.screenshot_png

        mode = "w:gz" if request.format == pb.SnapshotPackage.TARGZ else "w"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode=mode) as tf:
            pb_bytes = details.details.SerializeToString()
            pb_info = tarfile.TarInfo(name="snapshot.pb")
            pb_info.size = len(pb_bytes)
            tf.addfile(pb_info, io.BytesIO(pb_bytes))

            png_info = tarfile.TarInfo(name="screenshot.png")
            png_info.size = len(png_bytes)
            tf.addfile(png_info, io.BytesIO(png_bytes))

        archive_bytes = buf.getvalue()
        if request.path:
            safe_path = self._resolve_allowed_snapshot_path(request.path)
            if safe_path is None:
                yield pb.SnapshotPackage(
                    snapshot_id=request.snapshot_id,
                    success=False,
                    err=b"Snapshot path must reside within the allowed temporary directory",
                )
                return
            try:
                os.makedirs(os.path.dirname(safe_path), exist_ok=True)
                with open(safe_path, "wb") as f:
                    f.write(archive_bytes)
                yield pb.SnapshotPackage(snapshot_id=request.snapshot_id, success=True)
            except OSError as err:
                yield pb.SnapshotPackage(
                    snapshot_id=request.snapshot_id,
                    success=False,
                    err=str(err).encode("utf-8"),
                )
            return

        yield pb.SnapshotPackage(
            snapshot_id=request.snapshot_id,
            payload=archive_bytes,
            success=True,
        )

    def PushSnapshot(
        self,
        request_iterator: Iterator[pb.SnapshotPackage],
        context: grpc.ServicerContext,
    ) -> pb.SnapshotPackage:
        snapshot_id = ""
        chunks: list[bytes] = []
        file_path = ""
        for msg in request_iterator:
            if msg.snapshot_id:
                snapshot_id = msg.snapshot_id
            if msg.path:
                file_path = msg.path
            if msg.payload:
                chunks.append(msg.payload)

        if not snapshot_id:
            return pb.SnapshotPackage(
                snapshot_id="",
                success=False,
                err=b"Missing snapshot_id in PushSnapshot stream",
            )

        if file_path:
            safe_path = self._resolve_allowed_snapshot_path(file_path)
            if safe_path is None:
                return pb.SnapshotPackage(
                    snapshot_id=snapshot_id,
                    success=False,
                    err=b"Snapshot path must reside within the allowed temporary directory",
                )
            if os.path.isfile(safe_path):
                with open(safe_path, "rb") as f:
                    raw_archive = f.read()
            else:
                raw_archive = b"".join(chunks)
        else:
            raw_archive = b"".join(chunks)

        frame = self.renderer.render_frame(display_id=0, output_format="PNG")
        screenshot_png = frame.data
        if raw_archive:
            try:
                with tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:*") as tf:
                    if "screenshot.png" in tf.getnames():
                        member = tf.getmember("screenshot.png")
                        if (
                            member.isfile()
                            and 0 < member.size <= _MAX_SCREENSHOT_BYTES
                        ):
                            extracted = tf.extractfile(member)
                            if extracted is not None:
                                screenshot_png = extracted.read(_MAX_SCREENSHOT_BYTES)
            except (tarfile.TarError, KeyError):
                pass

        ok, err_msg, _ = self.state.save_snapshot(
            snapshot_id=snapshot_id,
            screenshot_png=screenshot_png,
            imported=True,
        )
        return pb.SnapshotPackage(
            snapshot_id=snapshot_id,
            success=ok,
            err=err_msg.encode("utf-8"),
        )

    def TrackProcess(
        self, request: pb.IceboxTarget, context: grpc.ServicerContext
    ) -> pb.IceboxTarget:
        with self.state._lock:
            self.state.tracked_processes.append(
                (request.pid, request.package_name, request.max_snapshot_number)
            )
        return pb.IceboxTarget(
            pid=request.pid,
            package_name=request.package_name,
            snapshot_id=request.snapshot_id or f"icebox_{request.pid}",
            failed=False,
            max_snapshot_number=request.max_snapshot_number,
        )
