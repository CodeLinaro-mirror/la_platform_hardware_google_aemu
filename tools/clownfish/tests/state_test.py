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

"""Unit tests for Clownfish EmulatorState and reactive Pub/Sub invariants."""

import math
import unittest

from clownfish.state import EmulatorState


class EmulatorStateTest(unittest.TestCase):
    """Tests cross-service state coupling, Pub/Sub notifications, and snapshot restore."""

    def test_preset_initialization(self) -> None:
        phone = EmulatorState(preset="phone")
        self.assertEqual(phone.displays[0].width, 1080)
        self.assertEqual(phone.displays[0].height, 2400)
        self.assertFalse(phone.preset.is_foldable)

        foldable = EmulatorState(preset="foldable")
        self.assertTrue(foldable.preset.is_foldable)
        self.assertEqual(foldable.posture, 3)  # POSTURE_OPENED
        self.assertEqual(foldable.hinge_angles, [180.0])

    def test_posture_and_hinge_coupling(self) -> None:
        state = EmulatorState(preset="foldable")
        with state.subscribe("posture") as posture_q, state.subscribe(
            "frame"
        ) as frame_q:
            # Setting hinge angle to 0 deg should snap posture to CLOSED (1)
            ok = state.set_hinge_angle(0, 0.0)
            self.assertTrue(ok)
            self.assertEqual(state.posture, 1)
            self.assertEqual(
                state.get_effective_display_dims(0),
                (state.preset.folded_width, state.preset.folded_height),
            )
            self.assertIsNotNone(posture_q.get(timeout=1.0))
            self.assertIsNotNone(frame_q.get(timeout=1.0))

            # Setting discrete posture to HALF_OPENED (2) should snap hinge angle to 90 deg
            ok = state.set_posture(2)
            self.assertTrue(ok)
            self.assertEqual(state.hinge_angles[0], 90.0)
            self.assertEqual(
                state.get_effective_display_dims(0),
                (state.preset.width, state.preset.height),
            )

    def test_pose_rotation_auto_rotates_display_and_imu_gravity(self) -> None:
        state = EmulatorState(preset="phone")
        # Default upright portrait (0, 0, 0) -> gravity along +Y (0, 9.81, 0)
        self.assertEqual(state.get_effective_display_dims(0), (1080, 2400))
        imu0 = state.compute_imu_telemetry()
        gx0, gy0, gz0 = imu0["accelerometer"]
        self.assertAlmostEqual(gx0, 0.0, places=2)
        self.assertAlmostEqual(gy0, 9.81, places=2)
        self.assertAlmostEqual(gz0, 0.0, places=2)

        # Rotate roll by 90 deg -> display rotates to 90 deg, dims swap to (2400, 1080), gravity shifts to -X
        state.set_pose_rotation_deg(0.0, 0.0, 90.0)
        self.assertEqual(state.displays[0].rotation_deg, 90)
        self.assertEqual(state.get_effective_display_dims(0), (2400, 1080))
        imu90 = state.compute_imu_telemetry()
        gx90, gy90, _ = imu90["accelerometer"]
        self.assertAlmostEqual(gx90, -9.81, places=2)
        self.assertAlmostEqual(gy90, 0.0, places=2)

        # Rotate roll to 180 deg -> display rotates to 180 deg, dims are (1080, 2400)
        state.set_pose_rotation_deg(0.0, 0.0, 180.0)
        self.assertEqual(state.displays[0].rotation_deg, 180)
        self.assertEqual(state.get_effective_display_dims(0), (1080, 2400))

        # Rotate roll to -90 deg (270 deg) -> display rotates to 270 deg, dims swap to (2400, 1080)
        state.set_pose_rotation_deg(0.0, 0.0, -90.0)
        self.assertEqual(state.displays[0].rotation_deg, 270)
        self.assertEqual(state.get_effective_display_dims(0), (2400, 1080))

        # Lock rotation and rotate roll to 0 deg -> display stays at 270 deg
        state.update_display_v2(0, rotation_locked=True)
        state.set_pose_rotation_deg(0.0, 0.0, 0.0)
        self.assertEqual(state.displays[0].rotation_deg, 270)
        self.assertEqual(state.get_effective_display_dims(0), (2400, 1080))

    def test_snapshot_save_and_restore_roundtrip(self) -> None:
        state = EmulatorState(preset="foldable")
        state.update_battery(charge_level=42, charger_v1=2)
        state.update_location(latitude=51.5074, longitude=-0.1278, altitude=25.0)
        state.set_clipboard("snapshot-clip")
        state.set_posture(1)  # CLOSED

        ok, err, entry = state.save_snapshot(
            "snap_1", display_name="Test Snap", description="Saved state"
        )
        self.assertTrue(ok, err)
        self.assertIsNotNone(entry)

        # Mutate state after saving
        state.update_battery(charge_level=99, charger_v1=0)
        state.update_location(latitude=35.6762, longitude=139.6503)
        state.set_clipboard("mutated-clip")
        state.set_posture(3)  # OPENED

        # Restore snapshot and verify all fields revert
        ok, err, _ = state.load_snapshot("snap_1")
        self.assertTrue(ok, err)
        self.assertEqual(state.battery_charge_level, 42)
        self.assertEqual(state.battery_charger_v1, 2)
        self.assertAlmostEqual(state.gps_latitude, 51.5074, places=4)
        self.assertAlmostEqual(state.gps_longitude, -0.1278, places=4)
        self.assertEqual(state.clipboard_text, "snapshot-clip")
        self.assertEqual(state.posture, 1)

    def test_advance_frame_increments_sequence_and_notifies(self) -> None:
        state = EmulatorState(preset="phone")
        initial_seq = state.frame_seq
        with state.subscribe("frame") as q:
            new_seq = state.advance_frame(delta_deg=15.0)
            self.assertEqual(new_seq, initial_seq + 1)
            self.assertTrue(math.isclose(state.animation_angle_deg, 15.0))
            event = q.get(timeout=1.0)
            self.assertEqual(event["seq"], new_seq)


if __name__ == "__main__":
    unittest.main()
