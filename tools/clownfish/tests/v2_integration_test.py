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

"""End-to-end gRPC integration tests for all 12 android.emulation.v2 services and v1-v2 coupling."""

import time
import unittest

import audio_service_pb2 as audio_pb
import audio_service_pb2_grpc as audio_grpc
import battery_service_pb2 as bat_pb
import battery_service_pb2_grpc as bat_grpc
import camera_service_pb2 as cam_pb
import camera_service_pb2_grpc as cam_grpc
import capabilities_service_pb2 as cap_pb
import capabilities_service_pb2_grpc as cap_grpc
import common_pb2
import display_service_pb2 as disp_pb
import display_service_pb2_grpc as disp_grpc
import grpc
import hypervisor_service_pb2 as hyp_pb
import hypervisor_service_pb2_grpc as hyp_grpc
import input_service_pb2 as inp_pb
import input_service_pb2_grpc as inp_grpc
import location_service_pb2 as loc_pb
import location_service_pb2_grpc as loc_grpc
import sensors_service_pb2 as sens_pb
import sensors_service_pb2_grpc as sens_grpc
import system_service_pb2 as sys_pb
import system_service_pb2_grpc as sys_grpc
import telephony_service_pb2 as tel_pb
import telephony_service_pb2_grpc as tel_grpc
import webrtc_service_pb2 as rtc_pb
import webrtc_service_pb2_grpc as rtc_grpc
from aemu.proto import emulator_controller_pb2 as ec_pb
from aemu.proto import emulator_controller_pb2_grpc as ec_grpc
from clownfish.server import ClownfishServer
from google.longrunning import operations_pb2, operations_pb2_grpc
from google.protobuf import empty_pb2


class V2ServicesIntegrationTest(unittest.TestCase):
    """Tests all 12 android.emulation.v2 services and cross-version state synchronization."""

    def setUp(self) -> None:
        self.server = ClownfishServer(preset="foldable", port=0, fps=30.0).start()
        _max_msg = 64 * 1024 * 1024
        self.channel = grpc.insecure_channel(
            self.server.address,
            options=[
                ("grpc.max_receive_message_length", _max_msg),
                ("grpc.max_send_message_length", _max_msg),
            ],
        )
        self.ec_v1 = ec_grpc.EmulatorControllerStub(self.channel)

        self.cap = cap_grpc.CapabilitiesServiceStub(self.channel)
        self.disp = disp_grpc.DisplayServiceStub(self.channel)
        self.inp = inp_grpc.InputServiceStub(self.channel)
        self.sens = sens_grpc.SensorsServiceStub(self.channel)
        self.loc = loc_grpc.LocationServiceStub(self.channel)
        self.bat = bat_grpc.BatteryServiceStub(self.channel)
        self.sys = sys_grpc.SystemServiceStub(self.channel)
        self.tel = tel_grpc.TelephonyServiceStub(self.channel)
        self.cam = cam_grpc.CameraServiceStub(self.channel)
        self.audio = audio_grpc.AudioServiceStub(self.channel)
        self.hyp = hyp_grpc.HypervisorServiceStub(self.channel)
        self.ops = operations_pb2_grpc.OperationsStub(self.channel)
        self.rtc = rtc_grpc.RtcServiceStub(self.channel)

    def tearDown(self) -> None:
        self.channel.close()
        self.server.stop()

    def test_capabilities_and_display_service(self) -> None:
        caps = self.cap.GetDeviceCapabilities(cap_pb.GetDeviceCapabilitiesRequest())
        self.assertEqual(
            caps.metadata.display_name, self.server.state.preset.display_name
        )
        self.assertGreater(len(caps.sensors.posture.hinges), 0)

        # Create, list, and delete virtual display
        created = self.disp.CreateDisplay(
            disp_pb.CreateDisplayRequest(width=1280, height=720, density_dpi=240)
        )
        self.assertGreater(created.display_id, 0)
        displays = self.disp.ListDisplays(disp_pb.ListDisplaysRequest()).displays
        self.assertEqual(len(displays), 2)

        # GetScreenshot on v2 DisplayService
        shot = self.disp.GetScreenshot(
            disp_pb.GetScreenshotRequest(
                display_id=0,
                format=common_pb2.IMAGE_FORMAT_PNG,
            )
        )
        self.assertTrue(shot.image_data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(shot.width, 0)

        # StreamScreenshots animated frames
        stream = self.disp.StreamScreenshots(
            disp_pb.StreamScreenshotsRequest(
                display_id=0,
                format=common_pb2.IMAGE_FORMAT_RAW_RGBA8888,
            )
        )
        f1 = next(stream)
        self.server.state.advance_frame(delta_deg=36.0)
        f2 = next(stream)
        stream.cancel()
        self.assertGreater(f2.frame_sequence_number, f1.frame_sequence_number)
        self.assertNotEqual(f1.image_data, f2.image_data)

        self.disp.DeleteDisplay(
            disp_pb.DeleteDisplayRequest(display_id=created.display_id)
        )

        # CreateDisplay and UpdateDisplay reject excessive dimensions
        with self.assertRaises(grpc.RpcError) as create_ctx:
            self.disp.CreateDisplay(
                disp_pb.CreateDisplayRequest(
                    width=100000, height=100000, density_dpi=240
                )
            )
        self.assertEqual(
            create_ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT
        )
        with self.assertRaises(grpc.RpcError) as update_ctx:
            self.disp.UpdateDisplay(
                disp_pb.UpdateDisplayRequest(display_id=0, width=100000)
            )
        self.assertEqual(
            update_ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT
        )

    def test_v1_and_v2_cross_coupled_state(self) -> None:
        # Mutate battery via v2 -> verify via v1
        self.bat.UpdateBatteryState(
            bat_pb.UpdateBatteryStateRequest(
                level_percent=64,
                charger=bat_pb.CHARGER_SOURCE_WIRELESS,
            )
        )
        v1_bat = self.ec_v1.getBattery(empty_pb2.Empty())
        self.assertEqual(v1_bat.chargeLevel, 64)
        self.assertEqual(v1_bat.charger, ec_pb.BatteryState.WIRELESS)

        # Mutate posture via v2 SensorsService -> verify hinge angle & v1 posture
        posture_state = self.sens.UpdatePostureState(
            sens_pb.UpdatePostureStateRequest(posture=sens_pb.POSTURE_TYPE_HALF_OPENED)
        )
        self.assertEqual(posture_state.posture, sens_pb.POSTURE_TYPE_HALF_OPENED)
        self.assertAlmostEqual(posture_state.hinges[0].angle_degrees, 90.0)

        # Update 3D pose roll via v2 SensorsService -> verify IMU gravity and display rotation
        sens_state = self.sens.UpdateSensorsState(
            sens_pb.UpdateSensorsStateRequest(
                pose=sens_pb.DevicePose(
                    rotation_degrees=sens_pb.Vector3f(x=0.0, y=0.0, z=90.0)
                )
            )
        )
        self.assertAlmostEqual(sens_state.imu.accelerometer.x, -9.81, places=2)
        disp0 = self.disp.GetDisplay(disp_pb.GetDisplayRequest(display_id=0))
        self.assertEqual(disp0.rotation, common_pb2.ROTATION_DEG90)
        self.assertEqual(
            (disp0.width, disp0.height),
            (self.server.state.preset.height, self.server.state.preset.width),
        )

        # Verify UpdateDisplay and GetScreenshot across 0, 90, 180, 270 degrees
        for rot_enum in (
            common_pb2.ROTATION_DEG0,
            common_pb2.ROTATION_DEG90,
            common_pb2.ROTATION_DEG180,
            common_pb2.ROTATION_DEG270,
        ):
            updated_disp = self.disp.UpdateDisplay(
                disp_pb.UpdateDisplayRequest(display_id=0, user_rotation=rot_enum)
            )
            self.assertEqual(updated_disp.rotation, rot_enum)
            shot_rgba = self.disp.GetScreenshot(
                disp_pb.GetScreenshotRequest(
                    display_id=0,
                    format=common_pb2.IMAGE_FORMAT_RAW_RGBA8888,
                )
            )
            self.assertEqual(shot_rgba.rotation, rot_enum)
            self.assertEqual(
                (shot_rgba.width, shot_rgba.height),
                (updated_disp.width, updated_disp.height),
            )
            self.assertEqual(shot_rgba.image_data[0:4], bytes((0, 255, 0, 255)))

    def test_location_system_input_and_telephony_v2(self) -> None:
        # Location update
        loc = self.loc.UpdateLocationState(
            loc_pb.UpdateLocationStateRequest(
                latitude=37.7749,
                longitude=-122.4194,
                altitude_meters=12.0,
            )
        )
        self.assertAlmostEqual(loc.latitude, 37.7749, places=4)

        # System & Clipboard
        sys_state = self.sys.GetSystemState(sys_pb.GetSystemStateRequest())
        self.assertEqual(sys_state.lifecycle_state, sys_pb.SystemState.BOOT_COMPLETED)
        clip = self.sys.UpdateClipboardState(
            sys_pb.UpdateClipboardStateRequest(text="v2-clipboard-sync")
        )
        self.assertEqual(clip.text, "v2-clipboard-sync")

        # InputService unary key event
        self.inp.SendInputEvents(
            inp_pb.SendInputEventsRequest(
                events=[
                    inp_pb.InputEvent(
                        key=inp_pb.KeyEvent(
                            dom_code="Home",
                            action=inp_pb.KEY_ACTION_PRESS,
                        )
                    )
                ]
            )
        )
        self.assertEqual(self.server.state.last_key_event, "Home")

        # Telephony call lifecycle (RING -> ACCEPT -> DISCONNECT)
        c1 = self.tel.TransitionCall(
            tel_pb.TransitionCallRequest(
                caller_phone_number="+16505559999",
                action=tel_pb.TransitionCallRequest.RING,
            )
        )
        self.assertEqual(c1.call.status, tel_pb.CallState.INCOMING)
        c2 = self.tel.TransitionCall(
            tel_pb.TransitionCallRequest(
                caller_phone_number="+16505559999",
                action=tel_pb.TransitionCallRequest.ACCEPT,
            )
        )
        self.assertEqual(c2.call.status, tel_pb.CallState.ACTIVE)
        self.tel.TransitionCall(
            tel_pb.TransitionCallRequest(
                caller_phone_number="+16505559999",
                action=tel_pb.TransitionCallRequest.DISCONNECT,
            )
        )

    def test_camera_audio_webrtc_and_hypervisor_lro(self) -> None:
        # Camera
        cam_state = self.cam.GetCameraState(cam_pb.GetCameraStateRequest())
        self.assertGreaterEqual(len(cam_state.sensors), 1)

        # Audio
        speaker_stream = self.audio.StreamSpeakerAudio(
            audio_pb.StreamSpeakerAudioRequest(sample_rate_hz=48000, channel_count=2)
        )
        packet = next(speaker_stream)
        speaker_stream.cancel()
        self.assertGreater(len(packet.pcm_data), 0)

        # WebRTC session + JSEP loopback (initial OFFER + client OFFER -> ANSWER)
        rtc_res = self.rtc.RequestRtcStream(
            rtc_pb.RtcStreamRequest(
                requested_tracks=[common_pb2.TRACK_TYPE_SCREEN_PRIMARY]
            )
        )
        self.assertTrue(rtc_res.handle.session_id)
        jsep_stream = self.rtc.ReceiveJsepMessageStream(rtc_res.handle)
        first_jsep = next(jsep_stream)
        self.assertTrue(first_jsep.HasField("sdp"))
        self.assertIn("v=0", first_jsep.sdp.sdp)
        second_jsep = next(jsep_stream)
        self.assertTrue(second_jsep.HasField("ice_candidate"))

        # Wait longer than the 50ms queue poll timeout to ensure the stream stays open
        time.sleep(0.12)

        self.rtc.SendJsepMessage(
            rtc_pb.JsepMessage(
                handle=rtc_res.handle,
                sdp=rtc_pb.SessionDescription(
                    type=rtc_pb.SessionDescription.OFFER,
                    sdp="v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n",
                ),
            )
        )
        third_jsep = next(jsep_stream)
        jsep_stream.cancel()
        self.assertTrue(third_jsep.HasField("sdp"))
        self.assertEqual(third_jsep.sdp.type, rtc_pb.SessionDescription.ANSWER)
        self.assertIn("v=0", third_jsep.sdp.sdp)

        # HypervisorService CreateSnapshot, ImportSnapshot & RestoreSnapshot via Operations
        op = self.hyp.CreateSnapshot(
            hyp_pb.CreateSnapshotRequest(
                snapshot_id="v2_lro_snap",
                display_name="V2 Snapshot",
                description="Created via HypervisorService",
            )
        )
        self.assertTrue(op.done)
        fetched_op = self.ops.GetOperation(
            operations_pb2.GetOperationRequest(name=op.name)
        )
        self.assertTrue(fetched_op.done)

        snap_info = self.hyp.GetSnapshot(
            hyp_pb.GetSnapshotRequest(snapshot_id="v2_lro_snap")
        )
        self.assertEqual(snap_info.display_name, "V2 Snapshot")
        self.assertFalse(self.server.state.snapshots["v2_lro_snap"].imported)

        imp_op = self.hyp.ImportSnapshot(
            hyp_pb.ImportSnapshotRequest(
                snapshot_id="v2_imported_snap",
                storage_uri="gs://aemu-snapshots/golden_images/base.tar.zst",
            )
        )
        self.assertTrue(imp_op.done)
        self.assertTrue(self.server.state.snapshots["v2_imported_snap"].imported)


if __name__ == "__main__":
    unittest.main()
