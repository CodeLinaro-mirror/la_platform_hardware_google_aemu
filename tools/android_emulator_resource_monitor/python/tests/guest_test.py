"""Tests for finding adb, listing devices and parsing guest stats."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Prepend tests/ (for test_support) and python/ (for the monitor) to sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emulator_resource_monitor import get_devices, run_cmd as _REAL_RUN_CMD
import test_support
from test_support import _guest_model


def _fake_guest_run_cmd(batch_out='', top_out=''):
    """Returns a run_cmd fake that answers the guest batch and top adb calls."""

    def fake_run_cmd(cmd, **_kw):
        joined = ' '.join(cmd)
        if '===ADB_MON_SPLIT===' in joined:
            return batch_out
        return top_out if 'top -b' in joined else ''

    return fake_run_cmd


class TestGuestAdbAndStatsParsing(test_support.MonitorTestCase):

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_devices_keeps_no_permissions_devices(self, mock_run_cmd):
        # Missing udev rules show up as "no permissions"; the device must
        # still be listed instead of silently dropped.
        mock_run_cmd.return_value = (
            "List of devices attached\n"
            "emulator-5554\tdevice\n"
            "0123456789\tno permissions (missing udev rules?); see [...]\n")
        self.assertEqual(get_devices(), {
            'emulator-5554': 'device',
            '0123456789': 'no permissions'
        })

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_devices_mocked(self, mock_run_cmd):
        mock_run_cmd.return_value = """List of devices attached
emulator-5554\tdevice
emulator-5556\toffline
192.168.1.15:5555\tdevice
unauthorized_dev\tunauthorized
"""
        devices = get_devices()
        self.assertEqual(len(devices), 4)
        self.assertIn("emulator-5554", devices)
        self.assertIn("emulator-5556", devices)
        self.assertIn("192.168.1.15:5555", devices)
        self.assertIn("unauthorized_dev", devices)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_devices_keeps_transitional_states(self, mock_run_cmd):
        # Same states as the Rust parse_adb_devices.
        mock_run_cmd.return_value = ("List of devices attached\n"
                                     "emulator-5554\tsideload\n"
                                     "emulator-5556\tauthorizing\n"
                                     "192.168.1.15:5555\tconnecting\n")
        self.assertEqual(
            get_devices(), {
                'emulator-5554': 'sideload',
                'emulator-5556': 'authorizing',
                '192.168.1.15:5555': 'connecting'
            })

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_success(self, mock_run_cmd):

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if 'ro.product.model' in cmd_str:
                return "Mock Phone|!|14|!|34|!|BuildID|!|Size: 1080x2400|!|1"
            if 'cat /proc/meminfo' in cmd_str:
                return "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/data space\n===ADB_MON_SPLIT===\n0 errors\n===ADB_MON_SPLIT===\n1"
            if 'sys.boot_completed' in cmd_str:
                return "1"
            if 'top -b' in cmd_str or 'top -n' in cmd_str:
                return "Tasks: 100\n400% cpu 200% idle"
            return ""

        mock_run_cmd.side_effect = side_effect

        from emulator_resource_monitor import get_guest_stats
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", None, True, "device")
        self.assertIn("Mock Phone", model)
        self.assertIn("Android", model)
        self.assertFalse(is_booting)
        self.assertEqual(total_cpu, "200%")
        self.assertIn("50.0%",
                      ram)  # Used = Total - Available = 2GB / 4GB = 50%

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_with_io_wait(self, mock_run_cmd):

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if 'ro.product.model' in cmd_str:
                return "Mock Phone|!|14|!|34|!|BuildID|!|Size: 1080x2400|!|1"
            if 'cat /proc/meminfo' in cmd_str:
                return "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/data space\n===ADB_MON_SPLIT===\n0 errors\n===ADB_MON_SPLIT===\n1"
            if 'sys.boot_completed' in cmd_str:
                return "1"
            if 'top -b' in cmd_str or 'top -n' in cmd_str:
                return "400%cpu  5%user  1%nice  7%sys 215%idle 167%iow   5%irq   0%sirq   0%host"
            return ""

        mock_run_cmd.side_effect = side_effect

        from emulator_resource_monitor import get_guest_stats
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", None, True, "device")
        self.assertEqual(total_cpu, "185%")
        self.assertEqual(adj_cpu, "46.2% (over 4 cores)")

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_resolution_retry(self, mock_run_cmd):
        from emulator_resource_monitor import get_guest_stats

        # Setup mock for other calls to prevent failures
        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if 'ro.product.model' in cmd_str:
                return "Mock Phone|!|14|!|34|!|BuildID|!|Size: 1080x2400|!|1"
            return "MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0 errors\n===ADB_MON_SPLIT===\n1"

        mock_run_cmd.side_effect = side_effect

        # Case 1: "Res: N/A" and is_booting=True -> Should retry
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", "Android 12 | Res: N/A", True, "device")
        self.assertIn("1080x2400", model)

        # Verify it called the script
        called_with_prop = False
        for call in mock_run_cmd.call_args_list:
            if 'ro.product.model' in " ".join(call[0][0]):
                called_with_prop = True
                break
        self.assertTrue(called_with_prop)

        # Reset mock
        mock_run_cmd.reset_mock()

        # Case 2: "Res: N/A" and is_booting=False -> Should NOT retry
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", "Android 12 | Res: N/A", False, "device")
        self.assertEqual(model, "Android 12 | Res: N/A")

        # Verify it did NOT call the script
        called_with_prop = False
        for call in mock_run_cmd.call_args_list:
            if 'ro.product.model' in " ".join(call[0][0]):
                called_with_prop = True
                break
        self.assertFalse(called_with_prop)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_uses_logcat_filter(self, mock_run_cmd):
        from emulator_resource_monitor import get_guest_stats

        mock_run_cmd.return_value = "MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n1"

        get_guest_stats("emulator-5554", None, True, "device")

        called_with_filter = False
        for call in mock_run_cmd.call_args_list:
            args, kwargs = call
            if len(args) > 0 and isinstance(args[0], list) and len(args[0]) > 4:
                script = args[0][4]
                if 'grep -v "^---------"' in script:
                    called_with_filter = True
                    # API 24 logcat fails on `-b main,crash` ("Unable to open
                    # log device"), which left the error count at 0, and
                    # vmstat cannot read /proc/vmstat on API 28-29.
                    self.assertIn('logcat -d -t 500 -b main -b crash *:E',
                                  script)
                    self.assertNotIn('main,crash', script)
                    self.assertNotIn('vmstat', script)
                    break
        self.assertTrue(called_with_filter)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_batch_script_ends_with_separator(
            self, mock_run_cmd):
        # The trailing separator lets an empty getprop read as booting instead
        # of a truncated reply (len(parts) >= 5).
        from emulator_resource_monitor import get_guest_stats
        mock_run_cmd.side_effect = _fake_guest_run_cmd()
        get_guest_stats("emulator-5554", None, True, "device")
        script = next(c.args[0][4]
                      for c in mock_run_cmd.call_args_list
                      if '===ADB_MON_SPLIT===' in ' '.join(c.args[0]))
        self.assertEqual(script.count('===ADB_MON_SPLIT==='), 4)
        self.assertTrue(script.endswith("; echo '===ADB_MON_SPLIT==='"))

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_boot_detection_self_correction(self, mock_run_cmd):

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if 'ro.product.model' in cmd_str:
                return "Mock Phone|!|14|!|34|!|BuildID|!|Size: 1080x2400|!|1"  # Say it was booted in init
            if 'cat /proc/meminfo' in cmd_str:
                # Return split parts, last one is sys.boot_completed = "0" (regressed to booting or newly discovered)
                return "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/data space\n===ADB_MON_SPLIT===\n0 errors\n===ADB_MON_SPLIT===\n0"
            return ""

        mock_run_cmd.side_effect = side_effect

        from emulator_resource_monitor import get_guest_stats

        # Start with is_booting=False
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", "Android 12", False, "device")

        # Verify that it self-corrected to is_booting=True because sys.boot_completed was "0"
        self.assertTrue(is_booting)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_unsafe_separator(self, mock_run_cmd):

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if 'ro.product.model' in cmd_str:
                return "Mock Phone|!|14|!|34|!|BuildID|!|Size: 1080x2400|!|1"
            if 'batch_script' in cmd_str or 'cat /proc/meminfo' in cmd_str:
                # Simulate output where '---' appears in the df output (e.g. volume name)
                return "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n---\n/dev/mapper/vol---group 10G 5G 5G 50% /data\n---\n5"
            if 'top -b' in cmd_str or 'top -n' in cmd_str:
                return "Tasks: 100\n400% cpu 200% idle"
            return ""

        mock_run_cmd.side_effect = side_effect

        from emulator_resource_monitor import get_guest_stats
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", None, True, "device")

        self.assertIn(
            "50.0%", ram)  # RAM should still be parsed because it's in parts[0]
        self.assertIn(
            "N/A data", ram
        )  # Disk should be N/A because it couldn't find '/data' in parts[1]

    def test_get_guest_stats_offline_preserves_is_booting(self):
        from emulator_resource_monitor import get_guest_stats
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", "Android 12", False, "offline")
        self.assertFalse(is_booting)
        self.assertEqual(total_cpu, "N/A (offline)")

        _, booting_still_true, *_ = get_guest_stats("emulator-5554",
                                                    "Android 12", True,
                                                    "offline")
        self.assertTrue(booting_still_true)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_failed_query_preserves_booted_state(
            self, mock_run_cmd):
        from emulator_resource_monitor import get_guest_stats
        known = "Pixel 8 | Android 14 (API 34) | Build: UD1A | Res: 1080x2400"

        for bad_out in [
                "",
                "adb: error: closed",
            ("MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n"
             "===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n"
             "adb: error: closed"),
            ("MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n"
             "===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n"),
        ]:
            mock_run_cmd.return_value = bad_out
            _, is_booting, *_ = get_guest_stats("emulator-5554", known, False,
                                                "device")
            self.assertFalse(is_booting, f"reset to True for out={bad_out!r}")

        # A completed batch with empty sys.boot_completed (before separator #4)
        # still detects booting.
        mock_run_cmd.return_value = (
            "MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n"
            "===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n\n"
            "===ADB_MON_SPLIT===\n")
        _, is_booting, *_ = get_guest_stats("emulator-5554", known, False,
                                            "device")
        self.assertTrue(is_booting)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_busy_device_retains_booting(self, mock_run_cmd):
        mock_run_cmd.return_value = ""  # Simulate failure to run shell commands
        from emulator_resource_monitor import get_guest_stats
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", "Android 12", True, "device")
        self.assertTrue(is_booting)  # Should retain True

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_ignores_empty_getprop_model(self, mock_run_cmd):
        # Early in boot getprop can print nothing. That must not become a cached
        # " | Android  (API ) ..." banner, which would never be re-queried.
        from emulator_resource_monitor import get_guest_stats

        def side_effect(cmd, **kwargs):
            if 'ro.product.model' in " ".join(cmd):
                return "|!||!||!||!|Physical size: 1080x2400|!|1"
            return ""

        mock_run_cmd.side_effect = side_effect
        model, is_booting, *_ = get_guest_stats("emulator-5554", "", True,
                                                "device")
        self.assertNotIn("android", model.lower())
        self.assertTrue(is_booting)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_keeps_known_model_when_query_fails(
            self, mock_run_cmd):
        from emulator_resource_monitor import get_guest_stats
        mock_run_cmd.return_value = ""
        known = "Pixel 8 | Android 14 (API 34) | Build: UD1A | Res: N/A"
        model, *_ = get_guest_stats("emulator-5554", known, True, "device")
        self.assertEqual(model, known)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_clamps_ram_used_at_zero(self, mock_run_cmd):
        # MemAvailable can exceed MemTotal for a moment while memory is hot-added.
        from emulator_resource_monitor import get_guest_stats
        mock_run_cmd.return_value = (
            "MemTotal: 4000000 kB\nMemAvailable: 4100000 kB\n"
            "===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0\n"
            "===ADB_MON_SPLIT===\n1")
        _, _, _, _, ram, _ = get_guest_stats(
            "emulator-5554",
            "Pixel 8 | Android 14 (API 34) | Build: UD1A | Res: 1080x2400",
            False, "device")
        self.assertTrue(ram.startswith("0.0% (0.0GB/3.8GB"), ram)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_df_skips_non_row_data_lines(self, mock_run_cmd):
        from emulator_resource_monitor import get_guest_stats
        df_section = ("Filesystem      Size  Used Avail Use% Mounted on\n"
                      "df: /data/preload: Permission denied\n"
                      "/data 100% bad row\n"
                      "/dev/block/dm-5  10G    2G    8G  20% /data")
        mock_run_cmd.return_value = (
            "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n"
            f"===ADB_MON_SPLIT===\n{df_section}\n"
            "===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n1")
        _, _, _, _, ram, _ = get_guest_stats(
            "emulator-5554",
            "Pixel 8 | Android 14 (API 34) | Build: UD1A | Res: 1080x2400",
            False, "device")
        self.assertIn("20% (2G used/8G avail) data", ram)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_api33_df_names_bind_mount_instead_of_data(
            self, mock_run_cmd):
        # Captured from an API 33 google_apis x86_64 emulator (b/583871630):
        # `df -h /data` reports the /data device under its last bind mount, so
        # the row never mentions /data. Guest RAM and data usage must both
        # still parse.
        from emulator_resource_monitor import get_guest_stats
        batch_out = (
            "MemTotal:        2012912 kB\nMemFree:          173820 kB\n"
            "MemAvailable:     557316 kB\n"
            "SwapTotal:       1509680 kB\nSwapFree:        1054000 kB\n"
            "===ADB_MON_SPLIT===\n"
            "Filesystem       Size Used Avail Use% Mounted on\n"
            "/dev/block/dm-34  10G 574M  8.9G   6% "
            "/storage/emulated/0/Android/obb\n"
            "===ADB_MON_SPLIT===\n26\n===ADB_MON_SPLIT===\n1\n"
            "===ADB_MON_SPLIT===\n")
        mock_run_cmd.side_effect = _fake_guest_run_cmd(batch_out)
        _, is_booting, _, _, ram, swap = get_guest_stats(
            "emulator-5554",
            "sdk_gphone64_x86_64 | Android 13 (API 33) | Build: TE1A | "
            "Res: 1080x2400", False, "device")
        self.assertEqual(
            ram, "72.3% (1.4GB/1.9GB, 26 errs, 6% (574M used/8.9G avail) data)")
        self.assertEqual(swap, "445.0 MB")
        self.assertFalse(is_booting)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_df_prefers_row_naming_data(self, mock_run_cmd):
        # Synthetic: no captured image prints more than one row for
        # `df -h /data`.
        from emulator_resource_monitor import get_guest_stats
        mock_run_cmd.side_effect = _fake_guest_run_cmd(
            "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n"
            "===ADB_MON_SPLIT===\n"
            "/dev/block/dm-34 10G 574M 8.9G 6% "
            "/storage/emulated/0/Android/obb\n"
            "/dev/block/dm-35 20G 2.0G 18G 10% /data\n"
            "===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n1\n"
            "===ADB_MON_SPLIT===\n")
        _, _, _, _, ram, _ = get_guest_stats(
            "emulator-5554",
            "Pixel 8 | Android 14 (API 34) | Build: UD1A | Res: 1080x2400",
            False, "device")
        self.assertEqual(
            ram, "50.0% (1.9GB/3.8GB, 0 errs, 10% (2.0G used/18G avail) data)")

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_top_id_regex_ignores_idmap2d(self, mock_run_cmd):
        from emulator_resource_monitor import get_guest_stats
        known = "Pixel 8 | Android 14 (API 34) | Build: UD1A | Res: 1080x2400"

        def side_effect_idmap2d(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if 'top' in cmd_str:
                return "512 system 20 0 10G 12M 8M S 0.0 0.3 0:00.12 idmap2d"
            return ""

        mock_run_cmd.side_effect = side_effect_idmap2d
        _, _, total_cpu, adj_cpu, _, _ = get_guest_stats(
            "emulator-5554", known, False, "device")
        self.assertEqual(total_cpu, "N/A")
        self.assertEqual(adj_cpu, "N/A")

        def side_effect_real_id(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if 'top' in cmd_str:
                return "%Cpu(s):  5.0 us,  3.5 sy,  0.0 ni, 91.5 id,  0.0 wa"
            return ""

        mock_run_cmd.side_effect = side_effect_real_id
        _, _, total_cpu, adj_cpu, _, _ = get_guest_stats(
            "emulator-5554", known, False, "device")
        self.assertEqual(total_cpu, "8.5%")
        self.assertEqual(adj_cpu, "8.5% (Cores unknown)")

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_reads_ram_and_swap_from_meminfo(
            self, mock_run_cmd):
        # The meminfo sections are captured from API 29 and API 28 google_apis
        # x86_64 emulators, where vmstat fails with "Bad '/proc/vmstat':
        # Permission denied". The API 29 guest had 380 MB swapped out.
        from emulator_resource_monitor import get_guest_stats
        known = ("sdk_gphone_x86_64 | Android 10 (API 29) | Build: QSR1 | "
                 "Res: 1080x2400")
        rest = ("===ADB_MON_SPLIT===\n"
                "Filesystem     Size  Used Avail Use% Mounted on\n"
                "/dev/block/vdc  10G  161M  9.4G   2% /data\n"
                "===ADB_MON_SPLIT===\n52\n===ADB_MON_SPLIT===\n1\n"
                "===ADB_MON_SPLIT===\n")
        api29 = ("MemTotal:        2040256 kB\nMemFree:          123444 kB\n"
                 "MemAvailable:     700088 kB\nBuffers:           22828 kB\n"
                 "Cached:           743748 kB\nSwapCached:        11812 kB\n"
                 "SwapTotal:       1530188 kB\nSwapFree:        1141068 kB\n")
        api28 = ("MemTotal:        2046740 kB\nMemFree:          289244 kB\n"
                 "MemAvailable:    1152960 kB\n"
                 "SwapTotal:             0 kB\nSwapFree:              0 kB\n")
        no_swap_lines = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n"
        for name, meminfo, want_ram, want_swap in (
            ('api29', api29, "65.7% (1.3GB/1.9GB, 52 errs, "
             "2% (161M used/9.4G avail) data)", "380.0 MB"),
            ('api28', api28, "43.7% (0.9GB/2.0GB, 52 errs, "
             "2% (161M used/9.4G avail) data)", "0.0 MB"),
            ('no_swap_lines', no_swap_lines, "50.0% (1.9GB/3.8GB, 52 errs, "
             "2% (161M used/9.4G avail) data)", "N/A"),
        ):
            with self.subTest(name):
                mock_run_cmd.side_effect = _fake_guest_run_cmd(meminfo + rest)
                _, is_booting, _, _, ram, swap = get_guest_stats(
                    "emulator-5554", known, False, "device")
                self.assertEqual((ram, swap), (want_ram, want_swap))
                self.assertFalse(is_booting)

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_parses_toolbox_top_on_api24(self, mock_run_cmd):
        # Captured from an API 24 google_apis x86_64 emulator: toolbox top
        # rejects -b, and the `top -n 2 -d 1` frames carry tick deltas instead
        # of a %cpu line. The first frame spans a single tick.
        from emulator_resource_monitor import get_guest_stats
        known = ("Android SDK built for x86_64 | Android 7.0 (API 24) | "
                 "Build: NYC | Res: 1080x2400")
        top_out = (
            "Invalid argument \"-b\".\n"
            "Usage: top [ -m max_procs ] [ -n iterations ] [ -d delay ] "
            "[ -s sort_column ] [ -t ] [ -h ]\n"
            "User 0%, System 0%, IOW 0%, IRQ 0%\n"
            "User 0 + Nice 0 + Sys 0 + Idle 1 + IOW 0 + IRQ 0 + SIRQ 0 = 1\n"
            "\n"
            "  PID USER     PR  NI CPU% S  #THR     VSS     RSS PCY Name\n"
            " 4283 shell    20   0 200% R     1   9868K   2024K  fg top\n"
            "\n\n"
            "User 1%, System 0%, IOW 0%, IRQ 0%\n"
            "User 3 + Nice 1 + Sys 3 + Idle 391 + IOW 0 + IRQ 0 + "
            "SIRQ 0 = 398\n"
            "\n"
            "  PID USER     PR  NI CPU% S  #THR     VSS     RSS PCY Name\n"
            " 1707 system   20   0   0% S    86 1766384K 114392K  fg "
            "system_server\n")
        mock_run_cmd.side_effect = _fake_guest_run_cmd(top_out=top_out)
        _, _, total_cpu, adj_cpu, _, _ = get_guest_stats(
            "emulator-5554", known, False, "device")
        self.assertEqual(total_cpu, "1.8%")
        self.assertEqual(adj_cpu, "1.8% (Cores unknown)")
        commands = [' '.join(c.args[0]) for c in mock_run_cmd.call_args_list]
        self.assertEqual([c for c in commands if 'top' in c], [
            "adb -s emulator-5554 shell "
            "top -b -n 2 -d 0.2 || top -n 2 -d 1 || top -n 2 -d 1"
        ])
        self.assertFalse(any('dumpsys' in c for c in commands))

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_toolbox_top_failures_read_na(self, mock_run_cmd):
        # A failed toolbox top leaves the CPU unknown instead of falling back to
        # a stale dumpsys cpuinfo window.
        from emulator_resource_monitor import get_guest_stats
        known = ("Android SDK built for x86_64 | Android 7.0 (API 24) | "
                 "Build: NYC | Res: 1080x2400")
        # The one-tick first frame, captured on API 24.
        first_frame = ("User 0 + Nice 0 + Sys 0 + Idle 1 + IOW 0 + IRQ 0 + "
                       "SIRQ 0 = 1\n")
        # Synthetic: toolbox dies of its divide-by-zero before it prints a
        # zero-tick frame, so this guard is defensive.
        zero_tick_frame = ("User 0 + Nice 0 + Sys 0 + Idle 0 + IOW 0 + "
                           "IRQ 0 + SIRQ 0 = 0\n")
        # Captured on API 24 with a single fallback attempt, which died of the
        # divide-by-zero.
        sigfpe_reply = (
            "\nInvalid argument \"-b\".\n"
            "Usage: top [ -m max_procs ] [ -n iterations ] [ -d delay ] "
            "[ -s sort_column ] [ -t ] [ -h ]\n"
            "    -m num  Maximum number of processes to display.\n"
            "    -n num  Updates to show before exiting.\n"
            "    -d num  Seconds to wait between updates.\n"
            "    -s col  Column to sort by (cpu,vss,rss,thr).\n"
            "    -H      Show threads instead of processes.\n"
            "    -h      Display this help screen.\n"
            "Floating point exception \n\n")
        for name, top_out in (
            ('first_frame_only', first_frame),
            ('zero_tick_last_frame', first_frame + zero_tick_frame),
            ('sigfpe', sigfpe_reply),
        ):
            with self.subTest(name):
                mock_run_cmd.reset_mock()
                mock_run_cmd.side_effect = _fake_guest_run_cmd(top_out=top_out)
                _, _, total_cpu, adj_cpu, _, _ = get_guest_stats(
                    "emulator-5554", known, False, "device")
                self.assertEqual((total_cpu, adj_cpu), ("N/A", "N/A"))
                commands = [
                    ' '.join(c.args[0]) for c in mock_run_cmd.call_args_list
                ]
                self.assertFalse(any('dumpsys' in c for c in commands))

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_toolbox_top_accepts_negative_deltas(
            self, mock_run_cmd):
        # Synthetic (no capture shows a negative delta): the kernel's aggregate
        # iowait can go backwards, and toolbox prints signed deltas. Busy CPU
        # is clamped to [0, 100]. The first frame is captured on API 24.
        from emulator_resource_monitor import get_guest_stats
        known = ("Android SDK built for x86_64 | Android 7.0 (API 24) | "
                 "Build: NYC | Res: 1080x2400")
        first_frame = ("User 0 + Nice 0 + Sys 0 + Idle 1 + IOW 0 + IRQ 0 + "
                       "SIRQ 0 = 1\n")
        for name, top_out, want_total, want_adjusted in (
            ('negative_iowait', first_frame +
             "User 3 + Nice 1 + Sys 3 + Idle 391 + IOW -1 + IRQ 0 + "
             "SIRQ 0 = 397\n", "1.5%", "1.5% (Cores unknown)"),
            ('negative_idle_clamped_to_100', first_frame +
             "User 200 + Nice 0 + Sys 200 + Idle -2 + IOW 0 + IRQ 0 + "
             "SIRQ 0 = 398\n", "100.0%", "100.0% (Cores unknown)"),
            ('iowait_drop_clamped_to_0', first_frame +
             "User 1 + Nice 0 + Sys 1 + Idle 402 + IOW -3 + IRQ 0 + "
             "SIRQ 0 = 401\n", "0.0%", "0.0% (Cores unknown)"),
            ('negative_total_first_frame',
             "User 0 + Nice 0 + Sys 0 + Idle 1 + IOW -2 + IRQ 0 + "
             "SIRQ 0 = -1\n"
             "User 3 + Nice 1 + Sys 3 + Idle 391 + IOW 0 + IRQ 0 + "
             "SIRQ 0 = 398\n", "1.8%", "1.8% (Cores unknown)"),
        ):
            with self.subTest(name):
                mock_run_cmd.side_effect = _fake_guest_run_cmd(top_out=top_out)
                _, _, total_cpu, adj_cpu, _, _ = get_guest_stats(
                    "emulator-5554", known, False, "device")
                self.assertEqual((total_cpu, adj_cpu),
                                 (want_total, want_adjusted))

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_toybox_07_top_falls_back_in_guest_shell(
            self, mock_run_cmd):
        # Captured from an API 26 google_apis x86_64 emulator: toybox 0.7 top
        # rejects `-d 0.2` without printing usage, so the integer-delay
        # fallback has to run in the guest shell. Its interactive frames carry
        # ANSI escapes and CRLF line endings, separated by a bare CR.
        from emulator_resource_monitor import get_guest_stats
        known = ("Android SDK built for x86_64 | Android 8.0.0 (API 26) | "
                 "Build: OSR1.180418.026 | Res: 1080x2400")
        top_out = (
            "top: not integer: 0.2\n"
            "\x1b[?25l\x1b[0m\x1b[H\x1b[J\x1b[s\x1b[999C\x1b[999B\x1b[6n\x1b[u"
            "Tasks: 172 total,   1 running, 171 sleeping,   0 stopped,   "
            "0 zombie            \r\n"
            "Mem:   2046764k total,  1758436k used,   288328k free,    "
            "30552k buffers        \r\n"
            "Swap:        0k total,        0k used,        0k free,  "
            "1010980k cached         \r\n"
            "400%cpu   4%user   0%nice  12%sys 381%idle   0%iow   0%irq   "
            "4%sirq   0%host    \r\n"
            "\x1b[7m  PID USER         PR  NI VIRT  RES  SHR S[%CPU] %MEM     "
            "TIME+ ARGS            \x1b[0m\r\n"
            " 3008 u0_a11       20   0 1.6G  96M  77M S  0.0   4.8   0:01.87 "
            "android.process+\r"
            "\x1b[H\x1b[JTasks: 172 total,   2 running, 170 sleeping,   "
            "0 stopped,   0 zombie            \r\n"
            "Mem:   2046764k total,  1758544k used,   288220k free,    "
            "30552k buffers        \r\n"
            "Swap:        0k total,        0k used,        0k free,  "
            "1010980k cached         \r\n"
            "400%cpu   1%user   0%nice   2%sys 397%idle   0%iow   0%irq   "
            "0%sirq   0%host    \r\n")
        mock_run_cmd.side_effect = _fake_guest_run_cmd(top_out=top_out)
        _, _, total_cpu, adj_cpu, _, _ = get_guest_stats(
            "emulator-5554", known, False, "device")
        self.assertEqual(total_cpu, "3%")
        self.assertEqual(adj_cpu, "0.8% (over 4 cores)")
        commands = [' '.join(c.args[0]) for c in mock_run_cmd.call_args_list]
        self.assertFalse(any('dumpsys' in c for c in commands))

    def test_find_adb_shutil(self):
        from emulator_resource_monitor import find_adb
        import shutil

        with patch('shutil.which', return_value='/usr/bin/adb'):
            res = find_adb()

        self.assertEqual(res, '/usr/bin/adb')

    def test_find_adb_env(self):
        from emulator_resource_monitor import find_adb
        import os
        import shutil

        expected_adb = os.path.join('/path/to/sdk', 'platform-tools',
                                    'adb' + ('.exe' if os.name == 'nt' else ''))
        real_exists = os.path.exists
        real_which = shutil.which
        with patch('shutil.which',
                   side_effect=lambda cmd, *a, **kw: None
                   if cmd == 'adb' else real_which(cmd, *a, **kw)):
            with patch('os.environ.get',
                       side_effect=lambda k, d=None: '/path/to/sdk'
                       if k == 'ANDROID_HOME' else None):
                with patch('os.path.exists',
                           side_effect=lambda p: True
                           if p == expected_adb else real_exists(p)):
                    res = find_adb()

        self.assertEqual(res, expected_adb)

    def test_df_parsing_variations(self):
        from emulator_resource_monitor import get_guest_stats

        def mock_run_cmd(cmd, **kwargs):
            return "MemTotal: 1000 kB\nMemAvailable: 500 kB\n===ADB_MON_SPLIT===\n" + mock_df_output + "\n===ADB_MON_SPLIT===\n10"

        with patch('emulator_resource_monitor.run_cmd',
                   side_effect=mock_run_cmd):
            mock_df_output = "/dev/block/dm-5   10G   2G   8G  20% /data"
            model, is_booting, g_diff, g_real, g_ram, swap = get_guest_stats(
                "emulator-5554", "Android 12", False, "device")
            self.assertIn("20% (2G used/8G avail)", g_ram)

            mock_df_output = "10G   2G   8G  20% /data"
            model, is_booting, g_diff, g_real, g_ram, swap = get_guest_stats(
                "emulator-5554", "Android 12", False, "device")
            self.assertIn("20% (2G used/8G avail)", g_ram)

    def test_guest_cpu_cores_calculation(self):
        from emulator_resource_monitor import get_guest_stats

        def mock_run_cmd(cmd, **kwargs):
            if 'top -b' in ' '.join(cmd):
                return "400% cpu, 100% idle"
            return "Android 12|!|12|!|31|!|BuildID|!|1080x1920|!|1"

        with patch('emulator_resource_monitor.run_cmd',
                   side_effect=mock_run_cmd):
            model, is_booting, g_diff, g_real, g_ram, swap = get_guest_stats(
                "emulator-5554", "Android 12", False, "device")
            self.assertEqual(g_diff, "300%")
            self.assertIn("over 4 cores", g_real)

    def test_guest_cpu_cores_rounding(self):
        from emulator_resource_monitor import get_guest_stats

        def mock_run_cmd(cmd, **kwargs):
            if 'top -b' in ' '.join(cmd):
                return "398% cpu, 2% idle"
            return "Android 12|!|12|!|31|!|BuildID|!|1080x1920|!|1"

        with patch('emulator_resource_monitor.run_cmd',
                   side_effect=mock_run_cmd):
            model, is_booting, g_diff, g_real, g_ram, swap = get_guest_stats(
                "emulator-5554", "Android 12", False, "device")
            self.assertIn("over 4 cores", g_real)


class TestGuestAdbErrorAndSudoValidation(test_support.MockedCmdTestCase):

    def test_get_guest_stats_rejects_adb_colon_errors_without_flipping_booting(
            self):
        import emulator_resource_monitor as erm

        mid_batch_closed = (
            'MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n'
            '===ADB_MON_SPLIT===\n/dev/block/dm-0 6G 1G 5G 20% /data\n'
            '===ADB_MON_SPLIT===\n0\n'
            '===ADB_MON_SPLIT===\nadb: closed\n')
        with patch('emulator_resource_monitor.run_cmd',
                   side_effect=[mid_batch_closed, '', '', '']):
            _, is_booting, *_ = erm.get_guest_stats('emulator-5554',
                                                    _guest_model('Pixel 8'),
                                                    False, 'device')
        self.assertFalse(is_booting)

        adb_err_model = 'adb: device \'emulator-5554\' not found|!|14|!|34|!|UP1A|!||!|0'
        with patch('emulator_resource_monitor.run_cmd',
                   side_effect=[adb_err_model, 'adb: closed', '', '', '']):
            model, is_booting2, *_ = erm.get_guest_stats(
                'emulator-5554', '', False, 'device')
        self.assertEqual(model, 'Unknown Model')
        self.assertFalse(is_booting2)

    def test_sudo_user_validation_in_run_cmd_and_find_adb(self):
        import emulator_resource_monitor as erm

        seen_cmds = []

        def fake_subproc_run(cmd, *args, **kwargs):
            seen_cmds.append(list(cmd))
            res = MagicMock()
            res.stdout = 'ok'
            return res

        with patch.object(erm, 'run_cmd', _REAL_RUN_CMD), \
                patch('subprocess.run', side_effect=fake_subproc_run), \
                patch('os.geteuid', return_value=0, create=True), \
                patch.object(erm, 'ADB_PATH', '/sdk/adb'):
            for bad_user in ('root', '-u#0', 'bad user', 'alice;id',
                             'non_ascii_ä'):
                seen_cmds.clear()
                with patch.object(erm, 'sudo_user', bad_user):
                    erm.run_cmd(['adb', 'devices'])
                self.assertEqual(seen_cmds, [['/sdk/adb', 'devices']], bad_user)

            seen_cmds.clear()
            with patch.object(erm, 'sudo_user', 'alice.dev-1_ok'):
                erm.run_cmd(['adb', 'devices'])
            self.assertEqual(
                seen_cmds,
                [['sudo', '-n', '-u', 'alice.dev-1_ok', '/sdk/adb', 'devices']])

        if os.name != 'posix' or not hasattr(os, 'getuid'):
            self.skipTest('the rest of this test needs POSIX ownership')

        import pwd
        pwnam_calls = []
        real_exists = os.path.exists
        real_path_exists = erm.pathlib.Path.exists
        real_which = erm.shutil.which
        with patch('shutil.which',
                   side_effect=lambda cmd, *a, **kw: None
                   if cmd == 'adb' else real_which(cmd, *a, **kw)), \
                patch.object(pwd,
                             'getpwnam',
                             side_effect=lambda u: pwnam_calls.append(u) or
                             (_ for _ in ()).throw(KeyError(u))), \
                patch('os.path.exists',
                      side_effect=lambda p: False
                      if 'platform-tools' in str(p) else real_exists(p)), \
                patch.object(erm.pathlib.Path,
                             'exists',
                             autospec=True,
                             side_effect=lambda p: False
                             if 'platform-tools' in str(p) else
                             real_path_exists(p)):
            self.assertTrue(erm.pathlib.Path(__file__).exists())
            with patch('os.geteuid', return_value=1000, create=True), \
                    patch.dict(os.environ, {'SUDO_USER': 'alice'}, clear=False):
                erm.find_adb()
            with patch('os.geteuid', return_value=0, create=True), \
                    patch.dict(os.environ, {'SUDO_USER': 'root'}, clear=False):
                erm.find_adb()
            with patch('os.geteuid', return_value=0, create=True), \
                    patch.dict(os.environ, {'SUDO_USER': '-bad'}, clear=False):
                erm.find_adb()
        self.assertEqual(pwnam_calls, [])


if __name__ == '__main__':
    unittest.main()
