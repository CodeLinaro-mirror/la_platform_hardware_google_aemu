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

"""End-to-end gRPC integration tests for v1 EmulatorController, SnapshotService, and ScreenRecording."""

import tempfile
import unittest
from pathlib import Path

import clownfish  # noqa: F401 - extends google namespace path
import grpc
from aemu.proto import emulator_controller_pb2 as ec_pb
from aemu.proto import emulator_controller_pb2_grpc as ec_grpc
from aemu.proto import screen_recording_service_pb2 as rec_pb
from aemu.proto import screen_recording_service_pb2_grpc as rec_grpc
from aemu.proto import snapshot_service_pb2 as snap_pb
from aemu.proto import snapshot_service_pb2_grpc as snap_grpc
from clownfish.server import ClownfishServer
from google.protobuf import empty_pb2


class V1ServicesIntegrationTest(unittest.TestCase):
    """Tests v1 EmulatorController, SnapshotService, and ScreenRecording over live gRPC."""

    def setUp(self) -> None:
        self.server = ClownfishServer(preset="foldable", port=0, fps=30.0).start()
        self.channel = grpc.insecure_channel(self.server.address)
        self.ec = ec_grpc.EmulatorControllerStub(self.channel)
        self.snap = snap_grpc.SnapshotServiceStub(self.channel)
        self.rec = rec_grpc.ScreenRecordingStub(self.channel)

    def tearDown(self) -> None:
        self.channel.close()
        self.server.stop()

    def test_status_battery_gps_clipboard_and_posture(self) -> None:
        status = self.ec.getStatus(empty_pb2.Empty())
        self.assertTrue(status.booted)
        hw_map = {e.key: e.value for e in status.hardwareConfig.entry}
        self.assertEqual(hw_map["hw.sensor.hinge"], "yes")

        # Battery round-trip
        self.ec.setBattery(
            ec_pb.BatteryState(
                hasBattery=True,
                isPresent=True,
                charger=ec_pb.BatteryState.USB,
                chargeLevel=33,
            )
        )
        bat = self.ec.getBattery(empty_pb2.Empty())
        self.assertEqual(bat.chargeLevel, 33)
        self.assertEqual(bat.charger, ec_pb.BatteryState.USB)

        # GPS round-trip
        self.ec.setGps(
            ec_pb.GpsState(latitude=48.8566, longitude=2.3522, altitude=35.0)
        )
        gps = self.ec.getGps(empty_pb2.Empty())
        self.assertAlmostEqual(gps.latitude, 48.8566, places=4)
        self.assertAlmostEqual(gps.longitude, 2.3522, places=4)

        # Clipboard round-trip
        self.ec.setClipboard(ec_pb.ClipData(text="hello-clownfish-v1"))
        clip = self.ec.getClipboard(empty_pb2.Empty())
        self.assertEqual(clip.text, "hello-clownfish-v1")

        # Posture & folded screenshot dimensions
        self.ec.setPosture(ec_pb.Posture(value=ec_pb.Posture.POSTURE_CLOSED))
        img = self.ec.getScreenshot(
            ec_pb.ImageFormat(format=ec_pb.ImageFormat.PNG, width=200, height=200)
        )
        self.assertTrue(img.image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(img.format.foldedDisplay.width, 0)

    def test_screenshot_file_mmap_and_streaming_animation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mmap_file = Path(tmpdir) / "v1_frame.bin"
            w, h = 120, 100
            stride = (w * 4 + 3) & ~3
            mmap_file.write_bytes(b"\x00" * (h * stride))

            img = self.ec.getScreenshot(
                ec_pb.ImageFormat(
                    format=ec_pb.ImageFormat.RGBA8888,
                    width=w,
                    height=h,
                    transport=ec_pb.ImageTransport(
                        channel=ec_pb.ImageTransport.MMAP,
                        handle=mmap_file.as_uri(),
                    ),
                )
            )
            self.assertEqual(img.image, b"")
            self.assertEqual(img.format.transport.channel, ec_pb.ImageTransport.MMAP)
            raw_data = mmap_file.read_bytes()[: img.width * img.height * 4]
            self.assertTrue(any(b != 0 for b in raw_data))

        # Stream 2 consecutive animated frames and verify sequence progression
        stream = self.ec.streamScreenshot(
            ec_pb.ImageFormat(format=ec_pb.ImageFormat.RGB888, width=80, height=80)
        )
        first = next(stream)
        self.server.state.advance_frame(delta_deg=30.0)
        second = next(stream)
        stream.cancel()
        self.assertGreater(second.seq, first.seq)
        self.assertNotEqual(first.image, second.image)

        # Verify 0, 90, 180, 270 degree rotations via PhysicalModelValue.ROTATION
        for roll_deg, expected_skin in (
            (0.0, ec_pb.Rotation.PORTRAIT),
            (90.0, ec_pb.Rotation.LANDSCAPE),
            (180.0, ec_pb.Rotation.REVERSE_PORTRAIT),
            (-90.0, ec_pb.Rotation.REVERSE_LANDSCAPE),
        ):
            self.ec.setPhysicalModel(
                ec_pb.PhysicalModelValue(
                    target=ec_pb.PhysicalModelValue.ROTATION,
                    value=ec_pb.ParameterValue(data=[0.0, 0.0, roll_deg]),
                )
            )
            rot_img = self.ec.getScreenshot(
                ec_pb.ImageFormat(format=ec_pb.ImageFormat.RGB888, width=110, height=92)
            )
            self.assertEqual(rot_img.format.rotation.rotation, expected_skin)
            self.assertEqual(rot_img.image[0:3], bytes((0, 255, 0)))
            if expected_skin in (
                ec_pb.Rotation.LANDSCAPE,
                ec_pb.Rotation.REVERSE_LANDSCAPE,
            ):
                self.assertLess(rot_img.width, rot_img.height)
            else:
                self.assertGreater(rot_img.width, rot_img.height)

    def test_snapshot_service_lifecycle(self) -> None:
        self.ec.setBattery(ec_pb.BatteryState(chargeLevel=21))
        save_res = self.snap.SaveSnapshot(
            snap_pb.SnapshotPackage(snapshot_id="v1_snap")
        )
        self.assertTrue(save_res.success)

        snap_list = self.snap.ListSnapshots(snap_pb.SnapshotFilter())
        ids = [s.snapshot_id for s in snap_list.snapshots]
        self.assertIn("v1_snap", ids)

        shot = self.snap.GetScreenshot(snap_pb.SnapshotId(snapshot_id="v1_snap"))
        self.assertTrue(shot.data.startswith(b"\x89PNG\r\n\x1a\n"))

        # Mutate battery then restore snapshot
        self.ec.setBattery(ec_pb.BatteryState(chargeLevel=95))
        load_res = self.snap.LoadSnapshot(
            snap_pb.SnapshotPackage(snapshot_id="v1_snap")
        )
        self.assertTrue(load_res.success)
        self.assertEqual(self.ec.getBattery(empty_pb2.Empty()).chargeLevel, 21)

        # PullSnapshot / PushSnapshot reject paths outside allowed temporary directory
        bad_pull = list(
            self.snap.PullSnapshot(
                snap_pb.SnapshotPackage(
                    snapshot_id="v1_snap", path="/etc/disallowed_snap.tar"
                )
            )
        )
        self.assertFalse(bad_pull[0].success)
        bad_push = self.snap.PushSnapshot(
            iter(
                [
                    snap_pb.SnapshotPackage(
                        snapshot_id="bad_snap", path="/etc/passwd"
                    )
                ]
            )
        )
        self.assertFalse(bad_push.success)

        # setDisplayConfigurations rejects unbounded dimensions
        with self.assertRaises(grpc.RpcError) as disp_ctx:
            self.ec.setDisplayConfigurations(
                ec_pb.DisplayConfigurations(
                    displays=[
                        ec_pb.DisplayConfiguration(
                            display=1, width=100000, height=100000, dpi=320
                        )
                    ]
                )
            )
        self.assertEqual(disp_ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    def test_screen_recording_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            webm_path = str(Path(tmpdir) / "capture.webm")
            started = self.rec.StartRecording(
                rec_pb.RecordingInfo(file_name=webm_path, fps=24)
            )
            self.assertEqual(
                started.state, rec_pb.RecordingInfo.RECORDER_STATE_RECORDING
            )
            self.assertTrue(Path(webm_path).exists())

            stopped = self.rec.StopRecording(rec_pb.RecordingInfo(file_name=webm_path))
            self.assertEqual(stopped.state, rec_pb.RecordingInfo.RECORDER_STATE_STOPPED)

            listing = self.rec.ListRecordings(rec_pb.RecordingInfo())
            self.assertEqual(len(listing.recordings), 1)

        with self.assertRaises(grpc.RpcError) as rec_ctx:
            self.rec.StartRecording(
                rec_pb.RecordingInfo(file_name="/etc/disallowed.webm", fps=24)
            )
        self.assertEqual(rec_ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    def test_fault_injection_interceptor(self) -> None:
        self.server.inject_error(
            "getBattery", grpc.StatusCode.UNAVAILABLE, "Simulated outage"
        )
        with self.assertRaises(grpc.RpcError) as ctx:
            self.ec.getBattery(empty_pb2.Empty())
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNAVAILABLE)

        self.server.clear_errors()
        bat = self.ec.getBattery(empty_pb2.Empty())
        self.assertGreaterEqual(bat.chargeLevel, 0)


if __name__ == "__main__":
    unittest.main()
