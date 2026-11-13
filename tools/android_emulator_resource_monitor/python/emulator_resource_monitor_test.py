import unittest
from unittest.mock import patch, MagicMock
import io
import math
import os
import socket
import sys
import tempfile
import time
from collections import namedtuple

# Append current path to sys.path to import our script
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from emulator_resource_monitor import get_val, get_devices, RE_ANSI, parse_cmdline, run_cmd as _REAL_RUN_CMD


def _fake_guest_run_cmd(batch_out='', top_out=''):
    """Returns a run_cmd fake that answers the guest batch and top adb calls."""

    def fake_run_cmd(cmd, **_kw):
        joined = ' '.join(cmd)
        if '===ADB_MON_SPLIT===' in joined:
            return batch_out
        return top_out if 'top -b' in joined else ''

    return fake_run_cmd


def _try_symlink(src, dst, target_is_directory=False):
    """Creates a symlink; returns False only where the OS cannot make one.

    POSIX always can, so a failure there is an error and must not skip the
    assertions that depend on the link.
    """
    try:
        os.symlink(src, dst, target_is_directory=target_is_directory)
        return True
    except (OSError, NotImplementedError):
        if os.name == 'posix':
            raise
        return False


class TestMonitor(unittest.TestCase):

    def setUp(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()
        emulator_resource_monitor._NVIDIA_SMI_FAILED = False
        emulator_resource_monitor._NVIDIA_PMON_FAILED = False
        emulator_resource_monitor._LINUX_INTEL_GPU_FAILED = False
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)

    def tearDown(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()
        emulator_resource_monitor._NVIDIA_SMI_FAILED = False
        emulator_resource_monitor._NVIDIA_PMON_FAILED = False
        emulator_resource_monitor._LINUX_INTEL_GPU_FAILED = False

    def test_parse_cmdline_legacy(self):
        port, avd = parse_cmdline(
            ['emulator', '-avd', 'Pixel_6_API_31', '-port', '5554'])
        self.assertEqual(port, 5554)
        self.assertEqual(avd, 'Pixel_6_API_31')

        port, avd = parse_cmdline(['emulator', '-avdPixel_6_API_31'])
        self.assertIsNone(port)
        self.assertEqual(avd, 'Pixel_6_API_31')

        port, avd = parse_cmdline([
            'emulator-netdelaynone-netspeedfull-avdMedium_Phone_37_Pro-qt-hide-window'
        ])
        self.assertIsNone(port)
        self.assertEqual(avd, 'Medium_Phone_37_Pro')

        port, avd = parse_cmdline(['emulator', '@Pixel_7'])
        self.assertIsNone(port)
        self.assertEqual(avd, 'Pixel_7')

    def test_parse_cmdline_qemu_next_device_args(self):
        cmdline = [
            'qemu-system-x86_64', '-device',
            'avdstart,serial_number=5554,avd_name=Pixel_7', '-device',
            'virtio-goldfish-adb,host_port=5555'
        ]
        port, avd = parse_cmdline(cmdline)
        self.assertEqual(port, 5554)
        self.assertEqual(avd, 'Pixel_7')

        cmdline_hp = [
            'qemu-system-aarch64', '-name', 'Pixel_Tablet', '-device',
            'virtio-goldfish-adb,host_port=5557'
        ]
        port, avd = parse_cmdline(cmdline_hp)
        self.assertEqual(port, 5556)
        self.assertEqual(avd, 'Pixel_Tablet')

    def test_parse_cmdline_details_netsim_endpoint(self):
        import emulator_resource_monitor
        details = emulator_resource_monitor._parse_cmdline_details
        # launch_qemu.cc passes the endpoint as a -device property.
        worker = [
            'qemu-system-x86_64', '-device',
            'avdstart,serial_number=5554,avd_name=Pixel_7', '-device',
            'netsim-connection,id=netsim,grpc_endpoint=localhost:8877'
        ]
        self.assertEqual(
            details(worker),
            (5554, 'Pixel_7', None, 'Pixel_7', 'localhost:8877', False))
        quoted = [
            '-device', "netsim-connection,grpc_endpoint='[::1]:8877',id=netsim"
        ]
        self.assertEqual(details(quoted)[4], '[::1]:8877')
        # A -no-netsim worker has no netsim-connection device.
        self.assertEqual(
            details(['qemu-system-x86_64', '-no-netsim'])[4:], (None, True))
        # Option values equal to -no-netsim / --no-netsim are consumed by their
        # parent option and must not flip no_netsim.
        self.assertEqual(
            details(['emulator', '-avd', '-no-netsim', '-port', '5554']),
            (5554, '-no-netsim', '-no-netsim', None, None, False))
        self.assertEqual(
            details([
                'qemu-system-x86_64', '-device', '--no-netsim', '-port', '5554'
            ]), (5554, None, None, None, None, False))

    def test_parse_cmdline_details_strips_avd_equals_prefix(self):
        from emulator_resource_monitor import _parse_cmdline_details
        self.assertEqual(
            _parse_cmdline_details(['emulator', '-avd=Pixel_8'])[1], 'Pixel_8')
        self.assertEqual(
            _parse_cmdline_details(['emulator', '-avd=guest=Pixel_8'])[1],
            'Pixel_8')

    def test_parse_cmdline_fishtank(self):
        cmdline = ['fishtank', '@Pixel_7', '-fishtank', '5554']
        port, avd = parse_cmdline(cmdline)
        self.assertEqual(port, 5554)
        self.assertEqual(avd, 'Pixel_7')

        cmdline_no_avd = ['fishtank', '-fishtank', '5556']
        port, avd = parse_cmdline(cmdline_no_avd)
        self.assertEqual(port, 5556)
        self.assertIsNone(avd)

    def test_parse_cmdline_flag_variants(self):
        # -name with guest= prefix and comma options
        cmdline_name_guest = [
            'qemu-system-x86_64', '-name',
            'guest=Pixel_7_API_36,debug-threads=on', '-port', '5554'
        ]
        port, avd = parse_cmdline(cmdline_name_guest)
        self.assertEqual(port, 5554)
        self.assertEqual(avd, 'Pixel_7_API_36')

        # -name= syntax with quotes
        cmdline_name_eq = [
            'qemu-system-x86_64', '-name="Pixel_Tablet"', '-port=5556'
        ]
        port, avd = parse_cmdline(cmdline_name_eq)
        self.assertEqual(port, 5556)
        self.assertEqual(avd, 'Pixel_Tablet')

        # -device= format with split properties
        cmdline_dev_eq = [
            'qemu-system-x86_64',
            '-device=avdstart,serial_number=5554,avd_name=Pixel_7',
            '-device=virtio-goldfish-adb,host_port=5555'
        ]
        port, avd = parse_cmdline(cmdline_dev_eq)
        self.assertEqual(port, 5554)
        self.assertEqual(avd, 'Pixel_7')

        # -fishtank= syntax
        cmdline_fish_eq = ['fishtank', '@Pixel_7', '-fishtank=5554']
        port, avd = parse_cmdline(cmdline_fish_eq)
        self.assertEqual(port, 5554)
        self.assertEqual(avd, 'Pixel_7')

    @patch('psutil.process_iter')
    def test_get_process_stats_qemu_next_supervisor_worker_and_fishtank(
            self, mock_iter):
        import emulator_resource_monitor
        from collections import namedtuple
        MemoryInfo = namedtuple('MemoryInfo', ['rss'])
        Ctx = namedtuple('Ctx', ['voluntary', 'involuntary'])

        # Supervisor process
        mock_supervisor = MagicMock()
        mock_supervisor.info = {
            'name': 'emulator',
            'pid': 1000,
            'create_time': 1000.0,
        }
        mock_supervisor.cmdline.return_value = [
            'emulator', '@Pixel_7', '-port', '5554'
        ]
        mock_supervisor.cpu_percent.return_value = 0.0
        mock_supervisor.memory_info.return_value = MemoryInfo(rss=30 * 1024 *
                                                              1024)
        mock_supervisor.num_threads.return_value = 5
        mock_supervisor.num_ctx_switches.return_value = Ctx(voluntary=8,
                                                            involuntary=2)

        # Compute worker process
        mock_worker = MagicMock()
        mock_worker.info = {
            'name': 'qemu-system-x86_64',
            'pid': 1001,
            'create_time': 1001.0,
        }
        mock_worker.cmdline.return_value = [
            'qemu-system-x86_64', '-device',
            'avdstart,serial_number=5554,avd_name=Pixel_7', '-device',
            'virtio-goldfish-adb,host_port=5555'
        ]
        mock_worker.cpu_percent.return_value = 65.5
        mock_worker.memory_info.return_value = MemoryInfo(rss=4000 * 1024 *
                                                          1024)
        mock_worker.num_threads.return_value = 30
        mock_worker.num_ctx_switches.return_value = Ctx(voluntary=4500,
                                                        involuntary=500)

        # Fishtank GUI process
        mock_fishtank = MagicMock()
        mock_fishtank.info = {
            'name': 'fishtank',
            'pid': 1002,
            'create_time': 1002.0,
        }
        mock_fishtank.cmdline.return_value = [
            'fishtank', '@Pixel_7', '-fishtank', '5554'
        ]
        mock_fishtank.cpu_percent.return_value = 5.0
        mock_fishtank.memory_info.return_value = MemoryInfo(rss=200 * 1024 *
                                                            1024)
        mock_fishtank.num_threads.return_value = 8
        mock_fishtank.num_ctx_switches.return_value = Ctx(voluntary=180,
                                                          involuntary=20)

        mock_iter.return_value = [mock_supervisor, mock_worker, mock_fishtank]

        from emulator_resource_monitor import get_process_stats
        # Warm up cache
        get_process_stats()

        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = get_process_stats(
        )

        self.assertIn('emulator-5554', qemu_stats)
        cpu_str, ram_str = qemu_stats['emulator-5554']
        self.assertEqual(cpu_str, '70.5%')  # 0.0 + 65.5 + 5.0
        self.assertIn('4230.0MB', ram_str)  # 30 + 4000 + 200 = 4230MB
        self.assertIn('43 thr', ram_str)  # 5 + 30 + 8 = 43
        self.assertIn('5210 cs', ram_str)  # 10 + 5000 + 200 = 5210

        # Primary compute PID is the worker VM process (1001)
        self.assertEqual(qemu_pids['emulator-5554'], 1001)
        self.assertEqual(qemu_avds['emulator-5554'], 'Pixel_7')
        self.assertEqual(unmapped_qemu, [])

    @patch('psutil.process_iter')
    def test_get_process_stats_process_order_and_ram_tiebreak(self, mock_iter):
        from collections import namedtuple
        MemoryInfo = namedtuple('MemoryInfo', ['rss'])
        Ctx = namedtuple('Ctx', ['voluntary', 'involuntary'])

        # Process 1: Worker (CPU 0.0, RAM 4000MB, no avd_name) - processed FIRST
        mock_worker = MagicMock()
        mock_worker.info = {
            'name': 'qemu-system-x86_64',
            'pid': 2001,
            'create_time': 2001.0,
        }
        mock_worker.cmdline.return_value = [
            'qemu-system-x86_64', '-device',
            'virtio-goldfish-adb,host_port=5555'
        ]
        mock_worker.cpu_percent.return_value = 0.0
        mock_worker.memory_info.return_value = MemoryInfo(rss=4000 * 1024 *
                                                          1024)
        mock_worker.num_threads.return_value = 20
        mock_worker.num_ctx_switches.return_value = Ctx(voluntary=100,
                                                        involuntary=10)

        # Process 2: Supervisor (CPU 0.0, RAM 30MB, @Pixel_7, port 5554) - processed SECOND
        mock_supervisor = MagicMock()
        mock_supervisor.info = {
            'name': 'emulator',
            'pid': 2000,
            'create_time': 2000.0,
        }
        mock_supervisor.cmdline.return_value = [
            'emulator', '@Pixel_7', '-port', '5554'
        ]
        mock_supervisor.cpu_percent.return_value = 0.0
        mock_supervisor.memory_info.return_value = MemoryInfo(rss=30 * 1024 *
                                                              1024)
        mock_supervisor.num_threads.return_value = 4
        mock_supervisor.num_ctx_switches.return_value = Ctx(voluntary=5,
                                                            involuntary=1)

        # Worker is first in process list
        mock_iter.return_value = [mock_worker, mock_supervisor]

        from emulator_resource_monitor import get_process_stats
        get_process_stats()  # warm up
        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = get_process_stats(
        )

        self.assertIn('emulator-5554', qemu_stats)
        # Even though worker was first and both had 0.0% CPU, RAM tiebreak selects worker (2001) as primary PID
        self.assertEqual(qemu_pids['emulator-5554'], 2001)
        # AVD name from supervisor is preserved despite worker being processed first
        self.assertEqual(qemu_avds['emulator-5554'], 'Pixel_7')

    def test_get_val_percentages(self):
        self.assertEqual(get_val("45.2%"), 45.2)
        self.assertEqual(get_val("90.0%"), 90.0)
        self.assertEqual(get_val("0%"), 0.0)
        self.assertEqual(get_val("Unsupported"), 0.0)

    def test_get_val_paging(self):
        self.assertEqual(get_val("In: 450 / Out: 1200 (pages/s)"), 1200.0)
        self.assertEqual(get_val("In: 5000 / Out: 0 (pages/s)"), 5000.0)

    def test_get_val_secondary_na_metadata(self):
        self.assertEqual(get_val("25% (N/A used/N/A avail)", True), 25.0)
        self.assertEqual(get_val("50.0% (2.0GB/4.0GB, 7 errs, N/A data)", True),
                         50.0)
        self.assertTrue(math.isnan(get_val("N/A", True)))
        self.assertTrue(math.isnan(get_val("N/A (offline)", True)))
        self.assertTrue(math.isnan(get_val("  N/A (Requires sudo)", True)))
        self.assertTrue(math.isnan(get_val("50.0% (Timeout)", True)))

    def test_regex_ansi_strip(self):
        ansi_str = "\033[91mHello\033[0m"
        stripped = RE_ANSI.sub("", ansi_str)
        self.assertEqual(stripped, "Hello")

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

    def test_get_val_fallback(self):
        self.assertEqual(get_val("N/A"), 0.0)
        self.assertEqual(get_val("Error: restricted permissions"), 0.0)

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

    @patch('psutil.process_iter')
    def test_get_process_stats_mocked(self, mock_iter):
        mock_proc = MagicMock()
        mock_proc.info = {
            'name': 'qemu-system-x86_64',
            'cpu_percent': 15.0,
            'memory_info': MagicMock(rss=3000000000),
            'pid': 1234,
            'create_time': 10000.0
        }
        # Mock network connections
        mock_conn = MagicMock(status='LISTEN', laddr=MagicMock(port=5554))
        mock_proc.net_connections.return_value = [mock_conn]
        mock_proc.num_ctx_switches.return_value = MagicMock(voluntary=100,
                                                            involuntary=50)
        mock_proc.memory_info.return_value = MagicMock(rss=3000000000)
        mock_proc.num_threads.return_value = 254
        mock_proc.cpu_percent.return_value = 15.0
        mock_iter.return_value = [mock_proc]

        from emulator_resource_monitor import get_process_stats
        get_process_stats()  # Warm up cache, first tick returns 0.0%
        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = get_process_stats(
        )
        self.assertIn("emulator-5554", qemu_stats)
        self.assertEqual(qemu_stats["emulator-5554"][0], "15.0%")
        self.assertIn("2861.0MB", qemu_stats["emulator-5554"][1])

    @patch('psutil.process_iter')
    def test_get_process_stats_avd_extraction(self, mock_iter):
        mock_proc = MagicMock()
        mock_proc.info = {
            'name': 'qemu-system-x86_64',
            'cpu_percent': 15.0,
            'memory_info': MagicMock(rss=3000000000),
            'pid': 1234,
            'create_time': 10000.0
        }
        mock_proc.cmdline.return_value = [
            'emulator', '-avd', 'Pixel_6_API_31', '-port', '5554'
        ]
        mock_proc.num_ctx_switches.return_value = MagicMock(voluntary=100,
                                                            involuntary=50)
        mock_proc.memory_info.return_value = MagicMock(rss=3000000000)
        mock_proc.num_threads.return_value = 254
        mock_proc.cpu_percent.return_value = 15.0
        mock_iter.return_value = [mock_proc]

        from emulator_resource_monitor import get_process_stats
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()

        get_process_stats()  # Warm up cache
        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = get_process_stats(
        )

        self.assertIn("emulator-5554", qemu_avds)
        self.assertEqual(qemu_avds["emulator-5554"], "Pixel_6_API_31")

    @patch('psutil.process_iter')
    def test_get_process_stats_skips_zombie_and_dead_processes(self, mock_iter):
        import emulator_resource_monitor as erm

        requested_attrs = []

        def make_zombie_proc(name, pid, status_in_info, raise_on_methods=False):
            p = MagicMock()
            p.info = {
                'name': name,
                'pid': pid,
                'create_time': 20000.0,
                'ppid': 1,
                'status': status_in_info,
            }
            if raise_on_methods:
                err = erm.psutil.ZombieProcess(pid)
                p.exe.side_effect = err
                p.cmdline.side_effect = err
                p.cpu_percent.side_effect = err
                p.memory_info.side_effect = err
                p.num_threads.side_effect = err
                p.num_ctx_switches.side_effect = err
                p.net_connections.side_effect = err
                p.status.return_value = erm.psutil.STATUS_ZOMBIE
            else:
                p.exe.return_value = f'/sdk/emulator/{name}'
                p.cmdline.return_value = [name, '-port', '5554']
                p.cpu_percent.return_value = 0.0
                p.memory_info.return_value = MagicMock(rss=0)
                p.num_threads.return_value = 1
                p.num_ctx_switches.return_value = MagicMock(voluntary=0,
                                                            involuntary=0)
                p.status.return_value = status_in_info
            return p

        z_netsim_info = make_zombie_proc('netsimd', 7001,
                                         erm.psutil.STATUS_ZOMBIE)
        z_netsim_late = make_zombie_proc('netsimd',
                                         7002,
                                         erm.psutil.STATUS_RUNNING,
                                         raise_on_methods=True)
        z_emu_dead = make_zombie_proc('qemu-system-x86_64', 7003,
                                      erm.psutil.STATUS_DEAD)
        z_emu_late = make_zombie_proc('qemu-system-x86_64',
                                      7004,
                                      erm.psutil.STATUS_RUNNING,
                                      raise_on_methods=True)

        def fake_iter(attrs=None):
            requested_attrs.extend(attrs or ())
            return [z_netsim_info, z_netsim_late, z_emu_dead, z_emu_late]

        mock_iter.side_effect = fake_iter
        erm._PID_PORT_CACHE.clear()
        erm._PROCESS_CACHE.clear()

        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, _ = (
            erm.get_process_stats())
        self.assertEqual(netsim, "N/A")
        self.assertEqual(netsim_ram, "N/A")
        self.assertEqual(erm._LAST_NETSIMD_PROCS, [])
        self.assertEqual(qemu_stats, {})
        self.assertEqual(unmapped_qemu, [])
        self.assertEqual(qemu_pids, {})
        self.assertEqual(erm._PROCESS_CACHE, {})
        self.assertIn('status', requested_attrs)

    @patch('psutil.process_iter')
    def test_get_process_stats_excludes_truncated_and_shebang_monitors(
            self, mock_iter):
        import emulator_resource_monitor as erm

        self.assertIsNone(erm._classify_process('emulator_resour', '', ''))
        self.assertIsNone(erm._classify_process('emulator-resour', '', ''))

        def make_proc(name, pid, exe, cmdline):
            p = MagicMock()
            p.info = {
                'name': name,
                'pid': pid,
                'create_time': 30000.0,
                'ppid': 1,
                'status': erm.psutil.STATUS_RUNNING,
            }
            p.exe.return_value = exe
            p.cmdline.return_value = cmdline
            p.cpu_percent.return_value = 5.0
            p.memory_info.return_value = MagicMock(rss=20 * 1024 * 1024)
            p.num_threads.return_value = 2
            p.num_ctx_switches.return_value = MagicMock(voluntary=1,
                                                        involuntary=0)
            p.net_connections.return_value = []
            p.status.return_value = erm.psutil.STATUS_RUNNING
            return p

        # 1. Linux 15-byte comm truncation on ./emulator_resource_monitor.py shebang
        p_shebang = make_proc('emulator_resour', 8001, '/usr/bin/python3.13',
                              ['python3', './emulator_resource_monitor.py'])
        # 2. Renamed/custom shebang monitor where comm lacks 'emulator_resour'
        p_custom = make_proc('my_emulator_res', 8002, '/usr/bin/python3.13', [
            '/usr/bin/env', 'python3', '-u', './my_emulator_resource_monitor.py'
        ])
        # 3. Unreadable /proc/<pid>/exe (EACCES) on sibling monitor binary/script
        p_no_exe = make_proc('emulator-resour', 8003, '',
                             ['python3', './emulator-resource-monitor'])
        # 4. Real emulator whose -avd happens to contain 'resource_monitor'
        p_real_emu = make_proc('qemu-system-x86_64', 8004,
                               '/sdk/emulator/qemu-system-x86_64', [
                                   'qemu-system-x86_64', '-avd',
                                   'resource_monitor_avd', '-port', '5554'
                               ])

        mock_iter.return_value = [p_shebang, p_custom, p_no_exe, p_real_emu]
        erm._PID_PORT_CACHE.clear()
        erm._PROCESS_CACHE.clear()

        erm.get_process_stats()
        qemu_stats, unmapped_qemu, _, _, _, qemu_avds = erm.get_process_stats()
        self.assertEqual(unmapped_qemu, [])
        self.assertIn('emulator-5554', qemu_stats)
        self.assertEqual(qemu_avds.get('emulator-5554'), 'resource_monitor_avd')

    @patch('psutil.disk_io_counters')
    @patch('psutil.sensors_temperatures')
    @patch('psutil.virtual_memory')
    @patch('psutil.swap_memory')
    @patch('emulator_resource_monitor.host_cpu_percent')
    def test_get_host_stats_expansion(self, mock_cpu, mock_swap, mock_mem,
                                      mock_temps, mock_disk):
        mock_cpu.return_value = 50.0
        mock_mem.return_value = MagicMock(percent=60.0,
                                          used=6000000000,
                                          total=10000000000)
        mock_swap.return_value = MagicMock(percent=10.0,
                                           used=1000000000,
                                           total=10000000000)
        mock_temps.return_value = {'cpu_thermal': [MagicMock(current=45.5)]}
        mock_disk.return_value = MagicMock(read_bytes=2000000,
                                           write_bytes=1000000)

        from emulator_resource_monitor import get_host_stats
        res = get_host_stats()
        self.assertEqual(len(res), 19)
        self.assertEqual(res[0], "50.0%")
        self.assertEqual(res[13], "45.5°C")
        self.assertIn(
            "Read: 0.0 MB/s", res[14]
        )  # initial iteration elapsed gap triggers 0.0 MB/s naturally safely

    def test_get_host_stats_thermal_ignores_non_positive_readings(self):
        import emulator_resource_monitor as erm
        with tempfile.TemporaryDirectory() as tmpdir:
            z0 = os.path.join(tmpdir, 'tz0_temp')
            z1 = os.path.join(tmpdir, 'tz1_temp')
            with open(z0, 'w', encoding='utf-8') as f:
                f.write('0\n')
            with open(z1, 'w', encoding='utf-8') as f:
                f.write('-273150\n')

            with patch('platform.system', return_value='Linux'), \
                    patch('emulator_resource_monitor.host_cpu_percent', return_value=10.0), \
                    patch('emulator_resource_monitor.get_process_stats', return_value=({}, [], 'N/A', 'N/A', {}, {})), \
                    patch('emulator_resource_monitor.get_gpu_stats', return_value=('N/A', 'N/A')), \
                    patch('emulator_resource_monitor.get_emu_gpu_stats', return_value={}), \
                    patch('emulator_resource_monitor.read_disk_io', return_value=erm.DiskIo(0, 0)), \
                    patch('psutil.sensors_temperatures', return_value={'acpitz': [MagicMock(current=0.0), MagicMock(current=-5.0)]}), \
                    patch('glob.glob', side_effect=lambda pat: [z0, z1] if 'thermal_zone' in pat else []):
                res = erm.get_host_stats()
                self.assertEqual(res[13], 'N/A')

                # When psutil only reports 0.0°C dummy zones, a positive sysfs zone wins.
                with open(z1, 'w', encoding='utf-8') as f:
                    f.write('52500\n')
                res2 = erm.get_host_stats()
                self.assertEqual(res2[13], '52.5°C')

    @patch('time.sleep')
    @patch('psutil.cpu_times')
    def test_host_cpu_percent_uses_own_cpu_times_baseline(
            self, mock_times, mock_sleep):
        import emulator_resource_monitor as erm
        Times = namedtuple(
            'Times', 'user nice system idle iowait irq softirq steal guest '
            'guest_nice')
        mock_times.side_effect = [
            Times(10, 0, 5, 80, 5, 0, 0, 0, 2, 0),
            # +30 user (+10 of it guest, already in user), +10 system,
            # +50 idle, +10 iowait: busy 40 of total 100 on Linux.
            Times(40, 0, 15, 130, 15, 0, 0, 0, 12, 0),
            Times(40, 0, 15, 130, 15, 0, 0, 0, 12, 0),
        ]
        with patch.object(erm, '_LAST_CPU_TIMES', None), \
                patch.object(erm.sys, 'platform', 'linux'):
            self.assertAlmostEqual(erm.host_cpu_percent(), 40.0)
            mock_sleep.assert_called_once_with(0.2)
            # No time passed: no division by zero, no stale value.
            self.assertEqual(erm.host_cpu_percent(), 0.0)

    def test_physical_disk_names_skips_partitions_and_virtual_devices(self):
        import emulator_resource_monitor as erm
        with patch('os.listdir',
                   side_effect=lambda p: ['sda', 'dm-0', 'loop0']
                   if p == '/sys/block' else []), \
                patch('os.path.exists',
                      side_effect=lambda p: p == '/sys/block/sda/device'):
            self.assertEqual(erm.physical_disk_names(), {'sda'})

    @patch('psutil.disk_io_counters')
    def test_read_disk_io_counts_only_physical_disks(self, mock_counters):
        import emulator_resource_monitor as erm
        io = namedtuple('io', 'read_bytes write_bytes')
        # dm-0 is LVM on top of sda: its I/O is already counted on sda.
        mock_counters.side_effect = lambda perdisk=False: {
            'sda': io(100, 200),
            'sda1': io(100, 200),
            'dm-0': io(100, 200),
        } if perdisk else io(300, 600)
        with patch.object(erm, 'physical_disk_names', return_value={'sda'}):
            self.assertEqual(erm.read_disk_io('Linux'), (100, 200))
        # Container hosts where /sys/block has only virtual devices (empty set)
        # or per-disk counters omit the physical disk return DiskIo(0, 0) rather
        # than falling back to perdisk=False and summing virtual devices.
        with patch.object(erm, 'physical_disk_names', return_value=set()):
            self.assertEqual(erm.read_disk_io('Linux'), (0, 0))
        with patch.object(erm, 'physical_disk_names', return_value={'nvme0n1'}):
            self.assertEqual(erm.read_disk_io('Linux'), (0, 0))
        with patch.object(erm, 'physical_disk_names', return_value=None):
            self.assertEqual(erm.read_disk_io('Linux'), (300, 600))
        self.assertEqual(erm.read_disk_io('Darwin'), (300, 600))

    def test_save_summary_coverage(self):
        from emulator_resource_monitor import save_summary
        mock_summary = {
            'iterations':
                1,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                1,
            'host_cpu_sum':
                45.0,
            'host_cpu_max':
                45.0,
            'host_ram_iterations':
                1,
            'host_ram_sum':
                60.0,
            'host_ram_max':
                60.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 45.0,
                'host_ram': 60.0
            }]
        }
        # Should run without throwing Exceptions smoothly
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                save_summary(mock_summary,
                             1005.0,
                             save_csv=True,
                             out_dir=tmpdir)
            except Exception as e:
                self.fail(f"save_summary threw unexpected Exception: {e}")

    def test_save_summary_writes_boot_progression_log(self):
        # An emulator that booted during the session has boot log entries;
        # rendering them used to raise and lose the whole summary.
        from emulator_resource_monitor import format_boot_log, save_summary
        self.assertEqual(
            format_boot_log([('+5s', '12.0%', '30%', '0MB'),
                             ('+10s', '20%', '35%')]),
            [
                '    +5s | CPU: 12.0%           | RAM: 30% | Swap: 0MB',
                '    +10s | CPU: 20%             | RAM: 35%'
            ])
        summary = {
            'iterations':
                1,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                1,
            'host_cpu_sum':
                45.0,
            'host_cpu_max':
                45.0,
            'host_ram_iterations':
                1,
            'host_ram_sum':
                60.0,
            'host_ram_max':
                60.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {
                'emulator-5554': {
                    'name': 'Pixel_8',
                    'cpu_sum': 10.0,
                    'cpu_iterations': 1,
                    'ram_sum': 30.0,
                    'ram_iterations': 1,
                    'ram_max': 30.0,
                    'boot_log': [('+5s', '12.0%', '30%', '0MB')],
                }
            },
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 45.0,
                'host_ram': 60.0
            }]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            save_summary(summary, 1005.0, save_csv=False, out_dir=tmpdir)
            [txt] = [f for f in os.listdir(tmpdir) if f.endswith('.txt')]
            with open(os.path.join(tmpdir, txt), encoding='utf-8') as f:
                text = f.read()
        self.assertIn(
            '  Boot Progression Log:\n'
            '    +5s | CPU: 12.0%           | RAM: 30% | Swap: 0MB', text)

    def test_is_virtual_machine_coverage(self):
        from emulator_resource_monitor import save_summary
        mock_summary = {
            'iterations': 1,
            'start_time': 1000.0,
            'host_cpu_iterations': 1,
            'host_cpu_sum': 45.0,
            'host_cpu_max': 45.0,
            'host_ram_iterations': 1,
            'host_ram_sum': 60.0,
            'host_ram_max': 60.0,
            'netsim_cpu_iterations': 0,
            'netsim_cpu_sum': 0.0,
            'netsim_cpu_max': 0.0,
            'netsim_ram_iterations': 0,
            'netsim_ram_sum': 0.0,
            'netsim_ram_max': 0.0,
            'emulators': {},
            'time_series': []
        }
        real_exists = os.path.exists
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == '/proc/cpuinfo':
                return io.StringIO("hypervisor flags\n")
            return real_open(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch('os.path.exists',
                      side_effect=lambda p: True
                      if p == '/proc/cpuinfo' else real_exists(p)), \
                patch('builtins.open', side_effect=fake_open), \
                patch('emulator_resource_monitor.run_cmd', return_value=''), \
                patch('sys.stdout', new_callable=io.StringIO):
            try:
                save_summary(mock_summary,
                             1005.0,
                             save_csv=False,
                             out_dir=tmpdir)
            except Exception as e:
                self.fail(f"VM Test threw unexpected Exception: {e}")
            txt_files = [f for f in os.listdir(tmpdir) if f.endswith('.txt')]
            self.assertEqual(len(txt_files), 1)
            with real_open(os.path.join(tmpdir, txt_files[0]),
                           encoding='utf-8') as f:
                self.assertIn('Host Virtual: True', f.read())

    def test_main_once_flag(self):
        from emulator_resource_monitor import main
        with patch('sys.argv',
                   ['emulator_resource_monitor.py', '--once', '--no-summary']):
            with patch('emulator_resource_monitor.get_devices', return_value={}), \
                 patch('emulator_resource_monitor.get_host_stats', return_value=("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {})), \
                 patch('os._exit', return_value=None):
                try:
                    main()
                except SystemExit:
                    pass
                except Exception as e:
                    self.fail(f"Main threw Exception: {e}")

    def test_main_headless_flag(self):
        from emulator_resource_monitor import main
        with patch('sys.argv', [
                'emulator_resource_monitor.py', '--headless', '--once',
                '--no-summary'
        ]):
            with patch('emulator_resource_monitor.get_devices', return_value={}), \
                 patch('emulator_resource_monitor.get_host_stats', return_value=("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {})), \
                 patch('os._exit', return_value=None):
                try:
                    main()
                except SystemExit:
                    pass
                except Exception as e:
                    self.fail(f"Main threw Exception: {e}")

    def test_main_csv_flag(self):
        from emulator_resource_monitor import main
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--csv', '-o', tmpdir,
                    '--once', '--no-summary'
            ]):
                with patch('emulator_resource_monitor.get_devices', return_value={}), \
                     patch('emulator_resource_monitor.get_host_stats', return_value=("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {})), \
                     patch('os._exit', return_value=None):
                    try:
                        main()
                    except SystemExit:
                        pass
                    except Exception as e:
                        self.fail(f"Main threw Exception: {e}")

    def test_main_out_dir_flag(self):
        from emulator_resource_monitor import main
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                    'sys.argv',
                ['emulator_resource_monitor.py', '-o', tmpdir, '--once']):
                with patch('emulator_resource_monitor.get_devices', return_value={}), \
                     patch('emulator_resource_monitor.get_host_stats', return_value=("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {})), \
                     patch('os._exit', return_value=None):
                    try:
                        main()
                    except SystemExit:
                        pass
                    except Exception as e:
                        self.fail(f"Main threw Exception: {e}")

    @patch('emulator_resource_monitor.print_dashboard')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_avd_name_ui_connected(self, mock_host, mock_devices,
                                        mock_guest, mock_print):
        from emulator_resource_monitor import main

        # Mock connected device
        mock_devices.return_value = {"emulator-5554": "device"}

        # Mock host stats returning AVD name for the serial
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", {
                                      "emulator-5554": "Pixel_7_API_36"
                                  })

        # Mock guest stats
        mock_guest.return_value = ("Pixel_7_API_36", False, "10%", "5%",
                                   "50.0%", "0.0 MB")

        with patch('sys.argv',
                   ['emulator_resource_monitor.py', '--once', '--no-summary']):
            with patch('os._exit', return_value=None):
                try:
                    main()
                except SystemExit:
                    pass
                except Exception as e:
                    self.fail(f"Main threw Exception: {e}")

        # Verify that print_dashboard was called
        self.assertTrue(mock_print.called)

        # Get the guest_results passed to print_dashboard
        args, kwargs = mock_print.call_args
        guest_results = args[4]  # 5th argument

        # Verify that the key is the serial (current_serial)
        self.assertIn("emulator-5554", guest_results)

        # Verify it is NOT marked as DISCONNECTED
        disp, tags, g_diff, g_real, g_ram, g_swap = guest_results[
            "emulator-5554"]
        self.assertNotIn("[DISCONNECTED]", tags)
        # Verify the display name contains the AVD name
        self.assertIn("Pixel_7_API_36", disp)

    @patch('emulator_resource_monitor.print_dashboard')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_qemu_stats_display(self, mock_host, mock_devices, mock_guest,
                                     mock_print):
        from emulator_resource_monitor import main

        mock_devices.return_value = {"emulator-5554": "device"}
        mock_host.return_value = ("10%", {
            "emulator-5554": ("50.0%", "100MB")
        }, "2%", "30%", "0%", 0, 0, [], "10MB", {
            "emulator-5554": 1234
        }, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {
            "emulator-5554": "Pixel_7_API_36"
        })
        mock_guest.return_value = ("Pixel_7_API_36", False, "10%", "5%",
                                   "50.0%", "0.0 MB")

        with patch('sys.argv',
                   ['emulator_resource_monitor.py', '--once', '--no-summary']):
            with patch('os._exit', return_value=None):
                try:
                    main()
                except SystemExit:
                    pass
                except Exception as e:
                    self.fail(f"Main threw Exception: {e}")

        self.assertTrue(mock_print.called)
        args, kwargs = mock_print.call_args
        qemu_stats_passed = args[2]
        guest_results_passed = args[4]

        self.assertIn("emulator-5554", guest_results_passed)
        self.assertIn("emulator-5554", qemu_stats_passed)
        self.assertEqual(qemu_stats_passed["emulator-5554"], ("50.0%", "100MB"))

    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_loop_lookup_by_avd_name(self, mock_host, mock_devices,
                                          mock_guest):
        from emulator_resource_monitor import main
        import time

        # Mock connected device
        mock_devices.return_value = {"emulator-5554": "device"}

        # Mock host stats returning AVD name for the serial
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", {
                                      "emulator-5554": "Pixel_7_API_36"
                                  })

        # Mock guest stats to return is_booting=True
        mock_guest.return_value = ("Pixel_7_API_36", True, "10%", "5%", "50.0%",
                                   "0.0 MB")

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 3:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass
                    except Exception as e:
                        self.fail(f"Main threw Exception: {e}")

        # Verify that get_guest_stats was called at least twice
        self.assertGreaterEqual(mock_guest.call_count, 2)

        # Verify that the SECOND call to get_guest_stats had is_booting=True
        args, kwargs = mock_guest.call_args_list[1]
        self.assertTrue(args[2])  # 3rd argument is is_booting

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_loop_migration_serial_to_avd(self, mock_host, mock_devices,
                                               mock_guest, mock_save):
        from emulator_resource_monitor import main
        import time

        # Mock connected device
        mock_devices.return_value = {"emulator-5554": "device"}

        # First iteration: No AVD name
        # Second iteration: AVD name resolved
        # Third iteration: Keep AVD name
        mock_host.side_effect = [
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {}),
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {
                 "emulator-5554": "Pixel_7_API_36"
             }),
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {
                 "emulator-5554": "Pixel_7_API_36"
             })
        ]

        # Mock guest stats
        mock_guest.return_value = ("Pixel_7_API_36", True, "10%", "5%", "50.0%",
                                   "0.0 MB")

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 3:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass
                    except Exception as e:
                        self.fail(f"Main threw Exception: {e}")

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        # Verify that "emulator-5554" is NOT in summary_data['emulators']
        self.assertNotIn("emulator-5554", summary_data['emulators'])

        # Verify that "Pixel_7_API_36_emulator-5554" IS in summary_data['emulators']
        self.assertIn("Pixel_7_API_36_emulator-5554", summary_data['emulators'])

        # Verify that data was preserved (e.g. is_booting was True)
        e_data = summary_data['emulators']["Pixel_7_API_36_emulator-5554"]

        self.assertTrue(e_data['is_booting'])

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_boot_progression_logic(self, mock_host, mock_devices, mock_guest,
                                    mock_save):
        from emulator_resource_monitor import main

        mock_devices.return_value = {"emulator-5554": "device"}
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", {})
        mock_guest.return_value = ("Mock Model", True, "50%", "25%",
                                   "50% (2.0GB/4.0GB, 0 errs, 10% data)",
                                   "0.0 MB")

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 2:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    main()

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        self.assertIn("emulator-5554", summary_data['emulators'])
        e_data = summary_data['emulators']["emulator-5554"]

        self.assertTrue(e_data['track_boot_stats'])
        self.assertTrue(e_data['is_booting'])
        self.assertGreater(len(e_data['boot_log']), 0)

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_boot_progression_logic_non_booting(self, mock_host, mock_devices,
                                                mock_guest, mock_save):
        from emulator_resource_monitor import main

        mock_devices.return_value = {"emulator-5554": "device"}
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", {})
        mock_guest.return_value = ("Mock Model", False, "50%", "25%",
                                   "50% (2.0GB/4.0GB, 0 errs, 10% data)",
                                   "0.0 MB")

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 2:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    main()

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        self.assertIn("emulator-5554", summary_data['emulators'])
        e_data = summary_data['emulators']["emulator-5554"]

        self.assertFalse(e_data['track_boot_stats'])
        self.assertFalse(e_data['is_booting'])
        self.assertEqual(len(e_data['boot_log']), 0)

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_boot_progression_logic_transition(self, mock_host, mock_devices,
                                               mock_guest, mock_save):
        from emulator_resource_monitor import main

        mock_devices.return_value = {"emulator-5554": "device"}
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", {})

        # Mock guest stats to return is_booting=True first, then False
        guest_stats_responses = [
            ("Mock Model", True, "50%", "25%",
             "50% (2.0GB/4.0GB, 0 errs, 10% data)", "0.0 MB"),
            ("Mock Model", False, "50%", "25%",
             "50% (2.0GB/4.0GB, 0 errs, 10% data)", "0.0 MB")
        ]

        def mock_guest_side_effect(*args, **kwargs):
            if guest_stats_responses:
                return guest_stats_responses.pop(0)
            return ("Mock Model", False, "50%", "25%",
                    "50% (2.0GB/4.0GB, 0 errs, 10% data)", "0.0 MB")

        mock_guest.side_effect = mock_guest_side_effect

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 3:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    main()

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        self.assertIn("emulator-5554", summary_data['emulators'])
        e_data = summary_data['emulators']["emulator-5554"]

        # Should STILL track boot stats even after transition to non-booting
        self.assertTrue(e_data['track_boot_stats'])
        self.assertFalse(
            e_data['is_booting'])  # But is_booting should be False now
        self.assertGreater(len(e_data['boot_log']), 0)

    def test_graphs_generation(self):
        from emulator_resource_monitor import save_summary

        mock_figure = MagicMock()
        mock_figure.show.return_value = "mocked graph content"
        mock_figure_class = MagicMock(return_value=mock_figure)

        mock_summary = {
            'iterations':
                2,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                2,
            'host_cpu_sum':
                90.0,
            'host_cpu_max':
                50.0,
            'host_ram_iterations':
                2,
            'host_ram_sum':
                120.0,
            'host_ram_max':
                60.0,
            'netsim_cpu_iterations':
                2,
            'netsim_cpu_sum':
                4.0,
            'netsim_cpu_max':
                2.0,
            'netsim_ram_iterations':
                2,
            'netsim_ram_sum':
                20.0,
            'netsim_ram_max':
                10.0,
            'emulators': {
                'emulator-5554': {
                    'name': 'Mock Device',
                    'cpu_iterations': 2,
                    'cpu_sum': 100.0,
                    'ram_iterations': 2,
                    'ram_sum': 100.0,
                    'ram_max': 50.0
                }
            },
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 40.0,
                'host_ram': 60.0,
                'netsim_cpu': 2.0,
                'netsim_ram': 10.0,
                'host_thermal_c': 45.0,
                'host_swap': 10.0,
                'host_gpu': 20.0,
                'host_gpu_mem': 100.0,
                'host_disk_read_mbs': 5.0,
                'host_disk_write_mbs': 2.0,
                'emu_cpu_tot_emulator-5554': 50.0,
                'emu_cpu_adj_emulator-5554': 25.0,
                'emu_ram_emulator-5554': 50.0,
                'qemu_cpu_emulator-5554': 15.0,
                'qemu_ram_emulator-5554': 3000.0,
                'emu_disk_pct_emulator-5554': 10.0,
                'emu_errs_emulator-5554': 5.0,
                'qemu_cs_emulator-5554': 100.0
            }, {
                'time': 1005.0,
                'host_cpu': 50.0,
                'host_ram': 61.0,
                'netsim_cpu': 3.0,
                'netsim_ram': 11.0,
                'host_thermal_c': 46.0,
                'host_swap': 11.0,
                'host_gpu': 25.0,
                'host_gpu_mem': 101.0,
                'host_disk_read_mbs': 6.0,
                'host_disk_write_mbs': 3.0,
                'emu_cpu_tot_emulator-5554': 51.0,
                'emu_cpu_adj_emulator-5554': 26.0,
                'emu_ram_emulator-5554': 51.0,
                'qemu_cpu_emulator-5554': 16.0,
                'qemu_ram_emulator-5554': 3001.0,
                'emu_disk_pct_emulator-5554': 11.0,
                'emu_errs_emulator-5554': 6.0,
                'qemu_cs_emulator-5554': 101.0
            }]
        }

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(sys.modules,
                           {'plotille': MagicMock(Figure=mock_figure_class)}), \
                patch('emulator_resource_monitor.run_cmd',
                      return_value="Android Debug Bridge version 1.0.41"), \
                patch('sys.stdout', new_callable=io.StringIO):
            save_summary(mock_summary,
                         1010.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir=tmpdir)

        # 10 host charts + 7 emulator charts = 17 charts total!
        self.assertEqual(mock_figure.plot.call_count, 17)

    def test_graphs_generation_limits(self):
        from emulator_resource_monitor import save_summary

        mock_figure = MagicMock()
        mock_figure.show.return_value = "mocked graph content"
        mock_figure_class = MagicMock(return_value=mock_figure)

        mock_summary = {
            'iterations':
                2,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                2,
            'host_cpu_sum':
                90.0,
            'host_cpu_max':
                50.0,
            'host_ram_iterations':
                2,
            'host_ram_sum':
                120.0,
            'host_ram_max':
                60.0,
            'netsim_cpu_iterations':
                2,
            'netsim_cpu_sum':
                4.0,
            'netsim_cpu_max':
                2.0,
            'netsim_ram_iterations':
                2,
            'netsim_ram_sum':
                20.0,
            'netsim_ram_max':
                10.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 40.0
            }, {
                'time': 1005.0,
                'host_cpu': 50.0
            }]
        }

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(sys.modules,
                           {'plotille': MagicMock(Figure=mock_figure_class)}), \
                patch('emulator_resource_monitor.run_cmd', return_value=""), \
                patch('sys.stdout', new_callable=io.StringIO):
            save_summary(mock_summary,
                         1010.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir=tmpdir)

        mock_figure.set_x_limits.assert_called_with(min_=0, max_=5.0)

    def test_print_dashboard(self):
        from emulator_resource_monitor import print_dashboard

        mock_stdout = MagicMock()
        written_content = []

        def mock_write(s):
            written_content.append(s)

        mock_stdout.write.side_effect = mock_write

        with patch('sys.stdout', mock_stdout):
            print_dashboard(os_name="Linux",
                            host_total="10%",
                            qemu_stats={"emulator-5554": ("15%", "3000MB")},
                            netsim_cpu="2%",
                            guest_results={
                                "emulator-5554":
                                    ("Mock Device", "[TEST TAG]", "50%", "25%",
                                     "50% (2.0GB/4.0GB, 5 errs, 10% data)",
                                     "0.0 MB")
                            },
                            host_ram="30%",
                            host_swap="0%",
                            paging_str="In: 0 / Out: 0 (KB/s)",
                            iter_count=1,
                            term_width=80,
                            force_clear=False,
                            unmapped_qemu=[],
                            netsim_ram="10MB",
                            gpu_load="20%",
                            gpu_ram="10%",
                            emu_gpu_stats={"emulator-5554": "5%"},
                            is_once=True)

        output = "".join(written_content)

        self.assertIn("Android Emulator & Device Resource Monitor", output)
        self.assertIn("HOST SYSTEM (Linux)", output)
        self.assertIn("Complete System CPU", output)
        self.assertIn("Guest RAM Usage", output)
        self.assertIn("[TEST TAG]", output)
        self.assertIn("Host QEMU CPU", output)
        self.assertIn("Host QEMU RAM", output)

    def test_get_static_hardware_info(self):
        from emulator_resource_monitor import get_static_hardware_info

        real_exists = os.path.exists
        real_open = open

        with patch('platform.system', return_value='Linux'):
            with patch('platform.processor', return_value='Mock Processor'):
                with patch('os.path.exists',
                           side_effect=lambda p: True
                           if p == '/etc/os-release' else real_exists(p)):

                    def fake_open(path, *args, **kwargs):
                        if path == '/etc/os-release':
                            return io.StringIO('PRETTY_NAME="Mock OS"\n')
                        if path == '/proc/cpuinfo':
                            return io.StringIO('model name : Mock CPU\n')
                        return real_open(path, *args, **kwargs)

                    with patch('builtins.open', side_effect=fake_open):
                        with patch('psutil.virtual_memory') as mock_mem:
                            mock_mem.return_value.total = 16 * (1024**3)

                            with patch('emulator_resource_monitor.HAS_GPUTIL',
                                       False):
                                with patch('emulator_resource_monitor.run_cmd',
                                           return_value="Mock GPU, 4096"):
                                    res = get_static_hardware_info()

        self.assertIn("Mock OS", res)
        self.assertIn("Mock CPU", res)
        self.assertIn("16.0 GB RAM", res)
        self.assertIn("Mock GPU (4.0GB)", res)

    def test_get_gpu_stats_nvidia(self):
        from emulator_resource_monitor import get_gpu_stats

        with patch('platform.system', return_value='Linux'):
            with patch('emulator_resource_monitor.HAS_GPUTIL', False):
                with patch('emulator_resource_monitor.run_cmd',
                           return_value="30, 1000\n"):
                    gpu_load, gpu_mem = get_gpu_stats()

        self.assertEqual(gpu_load, "30.0%")
        self.assertEqual(gpu_mem, "1000.0MB")

    def test_get_gpu_stats_linux_sysfs(self):
        import emulator_resource_monitor as erm
        from emulator_resource_monitor import get_gpu_stats
        busy_path = '/sys/class/drm/card0/device/gpu_busy_percent'
        real_exists = os.path.exists
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == busy_path:
                return io.StringIO("45\n")
            return real_open(path, *args, **kwargs)

        try:
            with patch('platform.system', return_value='Linux'), \
                    patch('emulator_resource_monitor.HAS_GPUTIL', False), \
                    patch('emulator_resource_monitor.run_cmd',
                          return_value=""), \
                    patch('glob.glob',
                          side_effect=lambda pat: [busy_path]
                          if 'gpu_busy_percent' in pat else []), \
                    patch('os.path.exists',
                          side_effect=lambda p: p == busy_path
                          if str(p).startswith('/sys/class/drm/') else
                          real_exists(p)), \
                    patch('builtins.open', side_effect=fake_open):
                gpu_load, gpu_mem = get_gpu_stats()
        finally:
            erm._NVIDIA_SMI_FAILED = False
            erm._NVIDIA_PMON_FAILED = False

        self.assertEqual(gpu_load, "45%")
        self.assertEqual(gpu_mem, "N/A")

    def test_get_emu_gpu_stats(self):
        from emulator_resource_monitor import get_emu_gpu_stats

        mock_output = """
# gpu        pid  type    sm   mem   enc   dec   command
    0       1234     C    30    10     0     0   qemu-system-x86
    0       5678     C    45    20     0     0   qemu-system-x86
"""
        with patch('emulator_resource_monitor.run_cmd',
                   return_value=mock_output):
            from emulator_resource_monitor import update_emu_gpu
            update_emu_gpu()
            res = get_emu_gpu_stats({
                "emulator-5554": 1234,
                "emulator-5556": 5678
            })

        self.assertEqual(res["emulator-5554"], "30.0%")
        self.assertEqual(res["emulator-5556"], "45.0%")

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

    def test_handle_sigterm(self):
        from emulator_resource_monitor import handle_sigterm
        with self.assertRaises(KeyboardInterrupt):
            handle_sigterm(None, None)

    def test_main_shutdown_masks_signals_in_finally_and_guards_early_alt_screen(
            self):
        import signal
        import emulator_resource_monitor as erm
        observed_handlers = []

        def cleanup_sending_signals(self_tracker):
            observed_handlers.append((signal.getsignal(signal.SIGINT),
                                      signal.getsignal(signal.SIGTERM)))
            os.kill(os.getpid(), signal.SIGINT)

        with patch('emulator_resource_monitor.get_devices', return_value={}), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=_host_tuple()), \
                patch.object(erm.NetsimTracker,
                             'cleanup',
                             cleanup_sending_signals), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('time.sleep', side_effect=[KeyboardInterrupt(), None]), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            try:
                erm.main()
            except KeyboardInterrupt:
                pass
        self.assertTrue(mock_save.called)
        self.assertEqual(observed_handlers, [(signal.SIG_IGN, signal.SIG_IGN)])

        alt_calls = []
        restore_calls = []
        with patch('emulator_resource_monitor.setup_alt_screen',
                   side_effect=lambda *a: alt_calls.append(a)), \
                patch('emulator_resource_monitor.restore_screen',
                      side_effect=lambda *a: restore_calls.append(a)), \
                patch('emulator_resource_monitor.get_static_hardware_info',
                      side_effect=KeyboardInterrupt), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            with self.assertRaises(KeyboardInterrupt):
                erm.main()
        self.assertEqual(len(alt_calls), len(restore_calls))

    def test_main_trends(self):
        from emulator_resource_monitor import main

        mock_host_stats = [
            ("10%", {}, "2%", "30%", "5%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {}),
            ("20%", {}, "4%", "40%", "15%", 0, 0, [], "20MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {})
        ]

        mock_host = MagicMock(side_effect=mock_host_stats)

        mock_stdout = MagicMock()
        written_content = []

        def mock_write(s):
            written_content.append(s)

        mock_stdout.write.side_effect = mock_write

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 2:
                raise KeyboardInterrupt

        with patch('sys.stdout', mock_stdout):
            with patch('emulator_resource_monitor.get_devices',
                       return_value={}):
                with patch('emulator_resource_monitor.get_host_stats',
                           mock_host):
                    with patch('time.sleep', side_effect=mock_sleep):
                        with patch('sys.argv', [
                                'emulator_resource_monitor.py', '--interval',
                                '0.1', '--no-summary'
                        ]):
                            with patch('os._exit', return_value=None):
                                try:
                                    main()
                                except KeyboardInterrupt:
                                    pass
                                except SystemExit:
                                    pass

        output = "".join(written_content)
        self.assertIn("↑", output)
        self.assertIn("15% ↑", RE_ANSI.sub("", output))

    def test_setup_alt_screen(self):
        from emulator_resource_monitor import setup_alt_screen
        mock_stdout = MagicMock()
        mock_stdout.isatty.return_value = True
        with patch('sys.stdout', mock_stdout):
            setup_alt_screen(False, False)
        mock_stdout.write.assert_called_with('\033[?1049h\033[?25l\033[H')

    def test_restore_screen(self):
        from emulator_resource_monitor import restore_screen
        mock_stdout = MagicMock()
        mock_stdout.isatty.return_value = True
        with patch('sys.stdout', mock_stdout):
            restore_screen(False, False)
        mock_stdout.write.assert_called_with('\033[?1049l\033[?25h')

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_static_hardware_info_multi_gpu(self, mock_run_cmd):

        def side_effect(cmd, **kwargs):
            if 'nvidia-smi' in cmd:
                return "NVIDIA GeForce RTX 3090, 24576\nNVIDIA GeForce RTX 3090, 24576"
            return ""

        mock_run_cmd.side_effect = side_effect

        from emulator_resource_monitor import get_static_hardware_info
        with patch('platform.system', return_value="Linux"):
            with patch('emulator_resource_monitor.HAS_GPUTIL', False):
                info = get_static_hardware_info()
                self.assertIn("NVIDIA GeForce RTX 3090", info)
                self.assertIn("24.0GB", info)

    def test_main_minimum_sleep(self):
        from emulator_resource_monitor import main

        mock_stdout = MagicMock()
        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 1:
                raise KeyboardInterrupt  # Stop loop

        # Generator to provide indefinite values for time.monotonic
        def monotonic_gen():
            val = 100.0
            while True:
                yield val
                val += 2.0  # Increment to ensure elapsed time > interval

        gen = monotonic_gen()

        with patch('sys.stdout', mock_stdout):
            with patch('emulator_resource_monitor.get_devices',
                       return_value={}):
                with patch('emulator_resource_monitor.get_host_stats',
                           return_value=("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                         "10MB", {}, "0%", "0%", {}, "N/A",
                                         "0MB/s", 0.0, 0.0, "N/A", {})):
                    with patch('time.sleep', side_effect=mock_sleep):
                        with patch('time.monotonic',
                                   side_effect=lambda: next(gen)):
                            with patch('sys.argv', [
                                    'emulator_resource_monitor.py',
                                    '--interval', '1.0', '--no-summary'
                            ]):
                                with patch('os._exit', return_value=None):
                                    try:
                                        main()
                                    except KeyboardInterrupt:
                                        pass

        self.assertGreater(len(sleep_calls), 0)
        self.assertEqual(sleep_calls[0], 0.1)  # Should be clamped to 0.1

    @patch('emulator_resource_monitor.get_process_stats')
    @patch('psutil.disk_io_counters')
    @patch('psutil.sensors_temperatures')
    @patch('psutil.virtual_memory')
    @patch('psutil.swap_memory')
    @patch('emulator_resource_monitor.host_cpu_percent')
    @patch('platform.system', return_value="Linux")
    def test_get_host_stats_paging(self, mock_system, mock_cpu, mock_swap,
                                   mock_mem, mock_temps, mock_disk,
                                   mock_proc_stats):
        mock_cpu.return_value = 50.0
        mock_mem.return_value = MagicMock(percent=60.0,
                                          used=6000000000,
                                          total=10000000000)
        mock_swap.return_value = MagicMock(percent=10.0,
                                           used=1000000000,
                                           total=10000000000)
        mock_temps.return_value = {}
        mock_disk.return_value = MagicMock(read_bytes=2000000,
                                           write_bytes=1000000)
        mock_proc_stats.return_value = ({}, {}, "N/A", "N/A", {}, {})

        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == '/proc/vmstat':
                return io.StringIO("pgpgin 1000\npgpgout 2000\n")
            return real_open(path, *args, **kwargs)

        from emulator_resource_monitor import get_host_stats

        with patch('builtins.open', side_effect=fake_open):
            res = get_host_stats()

        self.assertEqual(res[5], 1000)  # pgin
        self.assertEqual(res[6], 2000)  # pgout

    @patch('psutil.process_iter')
    def test_process_cpu_initial_zero(self, mock_iter):
        mock_proc = MagicMock()
        from collections import namedtuple
        MemoryInfo = namedtuple('MemoryInfo', ['rss'])
        Ctx = namedtuple('Ctx', ['voluntary', 'involuntary'])

        mock_proc.info = {
            'name': 'qemu-system-x86_64',
            'pid': 1234,
            'create_time': 10000.0
        }
        mock_proc.cmdline.return_value = ['emulator', '-port', '5554']
        mock_proc.cpu_percent.return_value = 15.0
        mock_proc.memory_info.return_value = MemoryInfo(rss=3000000000)
        mock_proc.num_threads.return_value = 4
        mock_proc.num_ctx_switches.return_value = Ctx(voluntary=100,
                                                      involuntary=50)

        mock_iter.return_value = [mock_proc]

        from emulator_resource_monitor import get_process_stats

        import emulator_resource_monitor
        emulator_resource_monitor._PROCESS_CACHE.clear()

        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = get_process_stats(
        )
        self.assertEqual(qemu_stats["emulator-5554"][0],
                         "0.0%")  # First tick should be 0.0%

        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = get_process_stats(
        )
        self.assertEqual(qemu_stats["emulator-5554"][0],
                         "15.0%")  # Second tick should be mocked value

    def test_graphs_generation_flat_data(self):
        from emulator_resource_monitor import save_summary

        mock_figure = MagicMock()
        mock_figure_class = MagicMock(return_value=mock_figure)

        mock_summary = {
            'iterations':
                2,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                2,
            'host_cpu_sum':
                90.0,
            'host_cpu_max':
                50.0,
            'host_ram_iterations':
                2,
            'host_ram_sum':
                120.0,
            'host_ram_max':
                60.0,
            'netsim_cpu_iterations':
                2,
            'netsim_cpu_sum':
                4.0,
            'netsim_cpu_max':
                2.0,
            'netsim_ram_iterations':
                2,
            'netsim_ram_sum':
                20.0,
            'netsim_ram_max':
                10.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 40.0,
                'host_ram': 60.0,
            }, {
                'time': 1005.0,
                'host_cpu': 40.0,
                'host_ram': 60.0,
            }]
        }

        mock_figure.plot.side_effect = Exception("Plotille crash")
        written_lines = []

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(sys.modules,
                           {'plotille': MagicMock(Figure=mock_figure_class)}), \
                patch('emulator_resource_monitor.run_cmd', return_value=''), \
                patch('sys.stdout', new_callable=io.StringIO):
            save_summary(mock_summary,
                         1010.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir=tmpdir)
            txt_files = [f for f in os.listdir(tmpdir) if f.endswith('.txt')]
            with open(os.path.join(tmpdir, txt_files[0]),
                      encoding='utf-8') as f:
                written_lines = f.read().splitlines()

        self.assertGreater(mock_figure.plot.call_count, 0)
        self.assertFalse(
            any("HOST CPU HISTORY CHART:" in line for line in written_lines))

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

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_loop_flaky_resolution_no_duplicates(self, mock_host,
                                                      mock_devices, mock_guest,
                                                      mock_save):
        from emulator_resource_monitor import main
        import time

        mock_devices.return_value = {"emulator-5554": "device"}

        mock_host.side_effect = [
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {
                 "emulator-5554": "Pixel_7"
             }),
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {}),
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {
                 "emulator-5554": "Pixel_7"
             })
        ]

        mock_guest.return_value = ("Pixel_7", False, "10%", "5%", "50.0%",
                                   "0.0 MB")

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 3:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        self.assertNotIn("emulator-5554", summary_data['emulators'])
        self.assertIn("Pixel_7_emulator-5554", summary_data['emulators'])
        self.assertEqual(len(summary_data['emulators']), 1)

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_loop_offline_start_tracks_boot(self, mock_host, mock_devices,
                                                 mock_guest, mock_save):
        from emulator_resource_monitor import main
        import time

        mock_devices.side_effect = [{
            "emulator-5554": "offline"
        }, {
            "emulator-5554": "device"
        }, {
            "emulator-5554": "device"
        }]

        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", {})

        mock_guest.side_effect = [
            ("Unknown (offline)", False, "N/A", "N/A", "N/A", "N/A"),
            ("Pixel_7", True, "50%", "25%", "50%", "0.0 MB"),
            ("Pixel_7", True, "50%", "25%", "50%", "0.0 MB")
        ]

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 3:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('time.monotonic', return_value=100.0):
                with patch('sys.argv', [
                        'emulator_resource_monitor.py', '--interval', '0.1',
                        '--no-summary'
                ]):
                    with patch('os._exit', return_value=None):
                        try:
                            main()
                        except KeyboardInterrupt:
                            pass

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_loop_boot_stats_duration_extended(self, mock_host,
                                                    mock_devices, mock_guest,
                                                    mock_save):
        from emulator_resource_monitor import main
        import time

        # adb lists no device on the first tick.
        mock_devices.side_effect = [{}] + [{"emulator-5554": "device"}] * 5
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", {})

        # Iter 1: t=100, devices empty.
        # Iter 2: t=200, devices={dev}, is_booting=True. Logs +0s.
        # Iter 3: t=300, devices={dev}, is_booting=False. Boot finishes. Logs +100s.
        # Iter 4: t=500, devices={dev}, is_booting=False. 500-300=200 < 300. Logs +300s.
        # Iter 5: t=650, devices={dev}, is_booting=False. 650-300=350 > 300. Stops tracking.
        mock_guest.side_effect = [
            ("Pixel_7", True, "50%", "25%", "50%", "0.0 MB"),
            ("Pixel_7", False, "60%", "30%", "50%", "0.0 MB"),
            ("Pixel_7", False, "40%", "20%", "50%", "0.0 MB"),
            ("Pixel_7", False, "30%", "15%", "50%", "0.0 MB")
        ]

        current_time = 100.0
        iteration = 0

        def mock_sleep(seconds):
            nonlocal iteration, current_time
            iteration += 1
            if iteration == 1:
                current_time = 200.0
            elif iteration == 2:
                current_time = 300.0
            elif iteration == 3:
                current_time = 500.0
            elif iteration == 4:
                current_time = 650.0
            elif iteration == 5:
                raise KeyboardInterrupt

        def get_time():
            return current_time

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('time.monotonic', side_effect=get_time):
                with patch('sys.argv', [
                        'emulator_resource_monitor.py', '--interval', '0.1',
                        '--no-summary'
                ]):
                    with patch('os._exit', return_value=None):
                        try:
                            main()
                        except KeyboardInterrupt:
                            pass

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        e_data = summary_data['emulators']["emulator-5554"]

        boot_log = e_data['boot_log']
        self.assertEqual(len(boot_log), 3)

        self.assertEqual(boot_log[0][0], "+0s")
        self.assertEqual(boot_log[1][0], "+100s")
        self.assertEqual(boot_log[2][0], "+300s")

    @patch('concurrent.futures.ThreadPoolExecutor')
    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_boot_tracking_glitch_ignored(self, mock_host, mock_devices,
                                          mock_guest, mock_save, mock_pool):
        from emulator_resource_monitor import main
        import time
        from unittest.mock import MagicMock

        mock_executor = MagicMock()
        mock_pool.return_value = mock_executor

        def fake_submit(func, *args, **kwargs):
            res = func(*args, **kwargs)
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = res
            return f

        mock_executor.submit.side_effect = fake_submit

        mock_devices.return_value = {"emulator-5554": "device"}
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {
                                      "emulator-5554": 1234
                                  }, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0,
                                  "N/A", {})

        mock_guest.side_effect = [
            ("Pixel_7", False, "50%", "25%", "50%", "0.0 MB"),
            ("Pixel_7", True, "60%", "30%", "50%", "0.0 MB"),
        ]

        iteration = 0

        def mock_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration == 2:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        e_data = summary_data['emulators']["emulator-5554"]
        self.assertTrue(e_data.get('boot_completed'))
        self.assertFalse(e_data.get('track_boot_stats'))

    @patch('concurrent.futures.ThreadPoolExecutor')
    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_boot_tracking_ignores_flaky_is_booting(self, mock_host,
                                                    mock_devices, mock_guest,
                                                    mock_save, mock_pool):
        from emulator_resource_monitor import main
        import time
        from unittest.mock import MagicMock

        mock_executor = MagicMock()
        mock_pool.return_value = mock_executor

        def fake_submit(func, *args, **kwargs):
            res = func(*args, **kwargs)
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = res
            return f

        mock_executor.submit.side_effect = fake_submit

        # adb lists no device on the first tick.
        mock_devices.side_effect = [{}] + [{"emulator-5554": "device"}] * 5
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {
                                      "emulator-5554": 1234
                                  }, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0,
                                  "N/A", {})

        # Iter 1: is_booting=True
        # Iter 2: is_booting=True
        # Iter 3: is_booting=False (boot completes)
        # Iter 4: is_booting=True (glitch!)
        mock_guest.side_effect = [
            ("Pixel_7", True, "50%", "25%", "50%", "0.0 MB"),
            ("Pixel_7", True, "50%", "25%", "50%", "0.0 MB"),
            ("Pixel_7", False, "60%", "30%", "50%", "0.0 MB"),
            ("Pixel_7", True, "40%", "20%", "50%", "0.0 MB"),
        ]

        current_time = 100.0
        iteration = 0

        def mock_sleep(seconds):
            nonlocal iteration, current_time
            iteration += 1
            if iteration == 1:
                current_time = 200.0
            elif iteration == 2:
                current_time = 300.0
            elif iteration == 3:
                current_time = 700.0  # Expire window (700-300=400 > 300)
            elif iteration == 4:
                current_time = 800.0

            if iteration == 4:
                raise KeyboardInterrupt

        def get_time():
            return current_time

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('time.monotonic', side_effect=get_time):
                with patch('sys.argv', [
                        'emulator_resource_monitor.py', '--interval', '0.1',
                        '--no-summary'
                ]):
                    with patch('os._exit', return_value=None):
                        try:
                            main()
                        except KeyboardInterrupt:
                            pass

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        e_data = summary_data['emulators']["emulator-5554"]
        boot_log = e_data['boot_log']

        # Should have logs from Iter 2 and Iter 3, but NOT Iter 4!
        self.assertEqual(len(boot_log), 3)
        self.assertEqual(boot_log[0][0], "+0s")
        self.assertEqual(boot_log[1][0], "+100s")
        self.assertEqual(boot_log[2][0], "+500s")

    @patch('concurrent.futures.ThreadPoolExecutor')
    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_boot_tracking_restart_detected(self, mock_host, mock_devices,
                                            mock_guest, mock_save, mock_pool):
        from emulator_resource_monitor import main
        import time
        from unittest.mock import MagicMock

        mock_executor = MagicMock()
        mock_pool.return_value = mock_executor

        def fake_submit(func, *args, **kwargs):
            res = func(*args, **kwargs)
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = res
            return f

        mock_executor.submit.side_effect = fake_submit

        mock_devices.return_value = {"emulator-5554": "device"}

        mock_host.side_effect = [
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {
                "emulator-5554": 1234
            }, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {}),
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {
                "emulator-5554": 1234
            }, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {}),
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {
                "emulator-5554": 5678
            }, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {}),
            # The guest query of the tick that first shows pid 5678 started
            # for pid 1234 and is dropped, so the new process reports here.
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {
                "emulator-5554": 5678
            }, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {})
        ]

        guest_calls = 0

        def fake_guest(*args, **kwargs):
            nonlocal guest_calls
            guest_calls += 1
            if guest_calls == 1:
                return ("Pixel_7", False, "50%", "25%", "50%", "0.0 MB")
            else:
                return ("Pixel_7", True, "60%", "30%", "50%", "0.0 MB")

        mock_guest.side_effect = fake_guest

        iteration = 0

        def mock_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration == 4:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary', '--headless'
            ]):
                with patch('os._exit', return_value=None):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        e_data = summary_data['emulators']["emulator-5554"]
        self.assertFalse(e_data.get('boot_completed'))
        self.assertTrue(e_data.get('track_boot_stats'))

    def test_disconnected_card_expires_after_120s_and_boot_log_skips_placeholder_ram(
            self):
        from emulator_resource_monitor import main

        # Part A: emulator-5554 boots with placeholder RAM on tick 1 (must not
        # enter boot_log) and disconnects while still booting; after 600s (>120s)
        # its [DISCONNECTED] card must expire.
        # Part B: emulator-5556 is already booted on tick 0, goes 'offline' on
        # tick 1 (where get_guest_stats returns is_booting=True), and must keep
        # is_booting=False.
        dev_seq = [
            {
                'emulator-5554': 'device',
                'emulator-5556': 'device'
            },
            {
                'emulator-5554': 'device',
                'emulator-5556': 'offline'
            },
            {},
        ]
        guest_by_serial = {
            ('emulator-5554', 0): ('Pixel_7', True, '50%', '25%', 'N/A', 'N/A'),
            ('emulator-5554', 1): ('Pixel_7', True, '50%', '25%', 'N/A', 'N/A'),
            ('emulator-5556', 0):
                ('Pixel_8', False, '20%', '10%', '40%', '0.0 MB'),
            ('emulator-5556', 1):
                ('Pixel_8', True, 'N/A (offline)', 'N/A (offline)',
                 'N/A (offline)', 'N/A (offline)'),
        }
        now_t = [100.0]
        tick_i = [0]
        dash_guests = []

        def fake_sleep(_):
            tick_i[0] += 1
            if tick_i[0] == 1:
                now_t[0] = 110.0
            elif tick_i[0] == 2:
                now_t[0] = 800.0
            elif tick_i[0] == 3:
                raise KeyboardInterrupt

        with patch('emulator_resource_monitor.get_devices',
                   side_effect=lambda: dev_seq[min(tick_i[0], 2)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=_host_tuple()), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=lambda s, *_: guest_by_serial[(s, tick_i[0])]), \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=lambda *a, **kw: dash_guests.append(dict(a[4]))), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-summary', '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            main()

        summary_data = mock_save.call_args[0][0]
        self.assertEqual(summary_data['emulators']['emulator-5554']['boot_log'],
                         [])
        self.assertFalse(
            summary_data['emulators']['emulator-5556']['is_booting'])
        self.assertEqual(dash_guests[-1], {})

    def test_csv_timestamp_calculation(self):
        from emulator_resource_monitor import save_summary
        import tempfile
        import csv
        import time

        mock_summary = {
            'iterations': 1,
            'start_time': 1000.0,
            'start_time_monotonic': 500.0,
            'host_cpu_iterations': 1,
            'host_cpu_sum': 45.0,
            'host_cpu_max': 45.0,
            'host_ram_iterations': 1,
            'host_ram_sum': 60.0,
            'host_ram_max': 60.0,
            'netsim_cpu_iterations': 0,
            'netsim_cpu_sum': 0.0,
            'netsim_cpu_max': 0.0,
            'netsim_ram_iterations': 0,
            'netsim_ram_sum': 0.0,
            'netsim_ram_max': 0.0,
            'emulators': {},
            'time_series': [{
                'time': 600.0,
                'host_cpu': 45.0
            }]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            save_summary(mock_summary,
                         1005.0,
                         save_csv=True,
                         save_txt=False,
                         out_dir=tmpdir)

            csv_files = [f for f in os.listdir(tmpdir) if f.endswith('.csv')]
            self.assertEqual(len(csv_files), 1)

            with open(os.path.join(tmpdir, csv_files[0]), 'r') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertEqual(len(rows), 1)
                expected_time_str = time.strftime('%Y-%m-%d %H:%M:%S',
                                                  time.localtime(1100.0))
                self.assertEqual(rows[0]['time'], expected_time_str)

    @patch('threading.Thread')
    @patch('emulator_resource_monitor.run_cmd')
    def test_emu_gpu_stats_caching(self, mock_run_cmd, mock_thread):
        from emulator_resource_monitor import get_emu_gpu_stats, _EMU_GPU_CACHE

        _EMU_GPU_CACHE["time"] = 0
        _EMU_GPU_CACHE["val"] = {}
        _EMU_GPU_CACHE["fetching"] = False

        mock_run_cmd.return_value = "0 1234 C 30 10 0 0"

        res = get_emu_gpu_stats({"emulator-5554": 1234})

        self.assertTrue(_EMU_GPU_CACHE["fetching"])
        self.assertTrue(mock_thread.called)
        self.assertEqual(res, {})

        from emulator_resource_monitor import update_emu_gpu
        update_emu_gpu()

        self.assertIn(1234, _EMU_GPU_CACHE["val"])

        res = get_emu_gpu_stats({"emulator-5554": 1234})
        self.assertEqual(res, {"emulator-5554": "30.0%"})

    @patch('psutil.process_iter')
    def test_process_stats_access_denied(self, mock_iter):
        import psutil
        mock_proc = MagicMock()
        mock_proc.info = {
            'name': 'qemu-system-x86_64',
            'pid': 1234,
            'create_time': 10000.0
        }
        mock_proc.cmdline.return_value = ['emulator', '-port', '5554']
        mock_proc.oneshot.return_value.__enter__.return_value = mock_proc

        # AccessDenied on cpu_percent
        mock_proc.cpu_percent.side_effect = psutil.AccessDenied()
        mock_proc.memory_info.return_value = MagicMock(rss=3000000000)
        mock_proc.num_threads.return_value = 4
        mock_proc.num_ctx_switches.return_value = MagicMock(voluntary=100,
                                                            involuntary=50)

        mock_iter.return_value = [mock_proc]

        from emulator_resource_monitor import get_process_stats
        import emulator_resource_monitor
        emulator_resource_monitor._PROCESS_CACHE.clear()

        get_process_stats()

        qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = get_process_stats(
        )

        self.assertIn("emulator-5554", qemu_stats)
        self.assertEqual(qemu_stats["emulator-5554"][0], "0.0%")
        self.assertIn("2861.0MB", qemu_stats["emulator-5554"][1])

    def test_print_row_nan_placeholder(self):
        from emulator_resource_monitor import print_dashboard
        import sys
        import io

        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            print_dashboard(
                "Linux",
                "10%", {},
                "N/A", {
                    "emulator-5554":
                        ("Mock Device", "", "NaN%", "NaN%", "NaN%", "NaN")
                },
                "30%",
                "0%",
                "In: 0 / Out: 0",
                1,
                80,
                False, [],
                "N/A",
                "N/A",
                "N/A", {},
                is_once=True)
        finally:
            sys.stdout = sys.__stdout__

        output = captured_output.getvalue()
        self.assertIn("Total Device CPU Usage", output)

    def test_ansi_stripping_truncation(self):
        from emulator_resource_monitor import print_dashboard
        import sys
        import io

        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            ansi_val = "\033[91mVery Long String That Will Be Truncated\033[0m"
            print_dashboard(
                "Linux",
                "10%", {},
                "N/A", {
                    "emulator-5554":
                        ("Mock Device", "", "10%", "5%", ansi_val, "0.0 MB")
                },
                "30%",
                "0%",
                "In: 0 / Out: 0",
                1,
                80,
                False, [],
                "N/A",
                "N/A",
                "N/A", {},
                is_once=True)
        finally:
            sys.stdout = sys.__stdout__

        output = captured_output.getvalue()
        self.assertIn("Very Long", output)

    def test_print_row_host_qemu_preserves_warning_colors(self):
        from emulator_resource_monitor import Colors, print_dashboard

        def render_qemu(cpu_str, gpu_str):
            captured = io.StringIO()
            with patch('sys.stdout', captured):
                print_dashboard(
                    "Linux",
                    "10.0%", {"emulator-5554": (cpu_str, "2048.0 MB")},
                    "N/A", {
                        "emulator-5554":
                            ("Pixel 8", "", "10%", "5%", "50.0%", "0.0 MB")
                    },
                    "30.0%",
                    "0.0%",
                    "In: 0 / Out: 0",
                    1,
                    80,
                    False, [],
                    "N/A",
                    "N/A",
                    "N/A", {"emulator-5554": gpu_str},
                    is_once=True)
            lines = captured.getvalue().splitlines()
            cpu_row = next(l for l in lines if "Host QEMU CPU" in l)
            ram_row = next(l for l in lines if "Host QEMU RAM" in l)
            gpu_row = next(l for l in lines if "Host QEMU GPU Load" in l)
            return cpu_row, ram_row, gpu_row

        # Calm tier: GREEN/CYAN overridden to BLUE
        cpu_calm, ram_calm, gpu_calm = render_qemu("80.0%", "20.0%")
        self.assertIn(f"{Colors.BLUE}[", cpu_calm)
        self.assertIn(f"{Colors.BLUE}2048.0 MB", ram_calm)
        self.assertIn(f"{Colors.BLUE}[", gpu_calm)

        # YELLOW warning tier (>150% QEMU CPU, >40% QEMU GPU) preserved
        cpu_warn, _, gpu_warn = render_qemu("200.0%", "55.0%")
        self.assertIn(f"{Colors.YELLOW}[", cpu_warn)
        self.assertIn(f"{Colors.YELLOW}[", gpu_warn)

        # RED warning tier (>350% QEMU CPU, >70% QEMU GPU) preserved
        cpu_red, _, gpu_red = render_qemu("500.0%", "80.0%")
        self.assertIn(f"{Colors.RED}[", cpu_red)
        self.assertIn(f"{Colors.RED}[", gpu_red)

    def test_print_row_placeholder_skips_bar_and_alert_and_fits_alert_suffix(
            self):
        from emulator_resource_monitor import print_dashboard
        captured = io.StringIO()
        with patch('sys.stdout', captured):
            # term_width=80 -> width=79 -> val_width=19 -> max_base_len=14.
            # "95.0% (14ch!!)" is 14 chars and fits " [!!]" (5 chars) without '…'.
            print_dashboard("Linux",
                            "95.0% (Timeout)", {},
                            "N/A", {},
                            "95.0% (14ch!!)",
                            "95.0% (15char!)",
                            "In: 0 / Out: 0",
                            1,
                            80,
                            False, [],
                            "N/A",
                            "N/A",
                            "N/A", {},
                            is_once=True,
                            batt_str="Calculating...")
        plain = RE_ANSI.sub("", captured.getvalue())
        cpu_line = next(
            l for l in plain.splitlines() if "Complete System CPU" in l)
        ram_line = next(
            l for l in plain.splitlines() if "Complete System RAM" in l)
        swap_line = next(
            l for l in plain.splitlines() if "Backup Memory (Swap) Usage" in l)
        batt_line = next(
            l for l in plain.splitlines() if "Laptop Battery Status" in l)
        top_line = next(l for l in plain.splitlines() if "┌" in l)

        self.assertIn(f"[{' ' * 20}] 95.0% (Timeout)", cpu_line)
        self.assertNotIn("[!!]", cpu_line)
        self.assertNotIn("[!!]", batt_line)
        self.assertIn("95.0% (14ch!!) [!!]", ram_line)
        self.assertIn("95.0% (15char… [!!]", swap_line)
        self.assertEqual(len(ram_line), len(top_line))
        self.assertEqual(len(swap_line), len(top_line))

    def test_plotille_limits_floor(self):
        from emulator_resource_monitor import save_summary

        mock_summary = {
            'iterations': 1,
            'start_time': 1000.0,
            'start_time_monotonic': 1000.0,
            'host_cpu_iterations': 1,
            'host_cpu_sum': 45.0,
            'host_cpu_max': 45.0,
            'host_ram_iterations': 1,
            'host_ram_sum': 60.0,
            'host_ram_max': 60.0,
            'netsim_cpu_iterations': 0,
            'netsim_cpu_sum': 0.0,
            'netsim_cpu_max': 0.0,
            'netsim_ram_iterations': 0,
            'netsim_ram_sum': 0.0,
            'netsim_ram_max': 0.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 0.0
            }]
        }

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch('emulator_resource_monitor.run_cmd', return_value=''), \
                patch('sys.stdout', new_callable=io.StringIO):
            try:
                save_summary(mock_summary,
                             1005.0,
                             save_csv=False,
                             save_txt=True,
                             out_dir=tmpdir)
            except ValueError as e:
                self.fail(f"save_summary crashed with ValueError: {e}")

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

    def test_unmapped_qemu_ui_clamping(self):
        from emulator_resource_monitor import print_dashboard
        import sys
        import io

        captured_output = io.StringIO()
        sys.stdout = captured_output

        unmapped = [(1234, "10%", "100MB"), (5678, "20%", "200MB")]

        try:
            print_dashboard("Linux",
                            "10%", {},
                            "N/A", {},
                            "30%",
                            "0%",
                            "In: 0 / Out: 0",
                            1,
                            80,
                            False,
                            unmapped,
                            "N/A",
                            "N/A",
                            "N/A", {},
                            is_once=True)
        finally:
            sys.stdout = sys.__stdout__

        output = captured_output.getvalue()
        self.assertIn("Unmapped Emulator CPU", output)
        self.assertIn("30.0%", output)

    def test_save_summary_qemu_gpu_chart(self):
        from emulator_resource_monitor import save_summary
        import tempfile

        mock_summary = {
            'iterations':
                2,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                2,
            'host_cpu_sum':
                90.0,
            'host_cpu_max':
                50.0,
            'host_ram_iterations':
                2,
            'host_ram_sum':
                120.0,
            'host_ram_max':
                60.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {
                'emulator-5554': {
                    'name': 'Mock Device',
                    'cpu_sum': 100.0,
                    'cpu_iterations': 2,
                    'ram_sum': 100.0,
                    'ram_iterations': 2,
                    'ram_max': 50.0
                }
            },
            'time_series': [{
                'time': 1000.0,
                'qemu_gpu_emulator-5554': 10.0
            }, {
                'time': 1005.0,
                'qemu_gpu_emulator-5554': 20.0
            }]
        }

        fake_fig = MagicMock()
        fake_fig.show.return_value = 'mock_chart_plot'
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(sys.modules,
                           {'plotille': MagicMock(Figure=lambda: fake_fig)}), \
                patch('sys.stdout', new_callable=io.StringIO):
            save_summary(mock_summary,
                         1010.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir=tmpdir)

            txt_files = [f for f in os.listdir(tmpdir) if f.endswith('.txt')]
            self.assertEqual(len(txt_files), 1)

            with open(os.path.join(tmpdir, txt_files[0]), 'r',
                      encoding='utf-8') as f:
                content = f.read()
                self.assertIn("HOST QEMU GPU LOAD HISTORY", content)

    def test_file_generation_shared_timestamp(self):
        from emulator_resource_monitor import save_summary
        import tempfile

        mock_summary = {
            'iterations': 1,
            'start_time': 1000.0,
            'start_time_monotonic': 1000.0,
            'host_cpu_iterations': 1,
            'host_cpu_sum': 45.0,
            'host_cpu_max': 45.0,
            'host_ram_iterations': 1,
            'host_ram_sum': 60.0,
            'host_ram_max': 60.0,
            'netsim_cpu_iterations': 0,
            'netsim_cpu_sum': 0.0,
            'netsim_cpu_max': 0.0,
            'netsim_ram_iterations': 0,
            'netsim_ram_sum': 0.0,
            'netsim_ram_max': 0.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0
            }]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            save_summary(mock_summary,
                         1005.0,
                         save_csv=True,
                         save_txt=True,
                         out_dir=tmpdir)

            files = os.listdir(tmpdir)
            self.assertEqual(len(files), 2)

            ts1 = files[0].split('_')[-1].split('.')[0]
            ts2 = files[1].split('_')[-1].split('.')[0]

            self.assertEqual(ts1, ts2)

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_avd_key_collision(self, mock_host, mock_devices, mock_guest,
                               mock_save):
        from emulator_resource_monitor import main
        import time

        # Mock two connected devices with same AVD name but different serials
        mock_devices.return_value = {
            "emulator-5554": "device",
            "emulator-5556": "device"
        }

        # Mock host stats returning same AVD name for both serials
        qemu_avds = {
            "emulator-5554": "Pixel_6_API_31",
            "emulator-5556": "Pixel_6_API_31"
        }
        mock_host.return_value = ("10%", {}, "2%", "30%", "0%", 0, 0, [],
                                  "10MB", {}, "0%", "0%", {}, "N/A", "0MB/s",
                                  0.0, 0.0, "N/A", qemu_avds)

        # Mock guest stats
        mock_guest.return_value = ("Pixel_6_API_31", False, "10%", "5%",
                                   "50.0%", "0.0 MB")

        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 2:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep):
            with patch('sys.argv', [
                    'emulator_resource_monitor.py', '--interval', '0.1',
                    '--no-summary'
            ]):
                with patch('os._exit', return_value=None):
                    try:
                        main()
                    except KeyboardInterrupt:
                        pass

        self.assertTrue(mock_save.called)
        args, kwargs = mock_save.call_args
        summary_data = args[0]

        # Verify that both devices have distinct keys in summary_data['emulators']
        self.assertIn("Pixel_6_API_31_emulator-5554", summary_data['emulators'])
        self.assertIn("Pixel_6_API_31_emulator-5556", summary_data['emulators'])


class TestCacheInvalidation(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)

    def test_normal_operation(self):
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {}
        serial_pids = {'emulator-5554': 100}
        connected_serials = {'emulator-5554'}
        qemu_pids = {'emulator-5554': 100}

        update_device_cache(device_names, disconnect_times, serial_pids,
                            connected_serials, qemu_pids, 1000.0)

        self.assertEqual(device_names, {'emulator-5554': 'Model A'})
        self.assertEqual(serial_pids, {'emulator-5554': 100})
        self.assertEqual(disconnect_times, {})

    def test_pid_changed(self):
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {}
        serial_pids = {'emulator-5554': 100}
        connected_serials = {'emulator-5554'}
        qemu_pids = {'emulator-5554': 101}  # New PID

        update_device_cache(device_names, disconnect_times, serial_pids,
                            connected_serials, qemu_pids, 1000.0)

        self.assertEqual(device_names, {})  # Invalidated
        self.assertEqual(serial_pids, {'emulator-5554': 101})  # Updated
        self.assertEqual(disconnect_times, {})

    def test_pid_forgotten_when_serial_has_no_host_process(self):
        # Without this, a later unrelated process reusing PID 100 would look
        # like the same emulator and keep its stale name.
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        serial_pids = {'emulator-5554': 100}
        update_device_cache(device_names, {}, serial_pids, {'emulator-5554'},
                            {}, 1000.0)
        self.assertEqual(serial_pids, {})
        self.assertEqual(device_names, {'emulator-5554': 'Model A'})

    def test_disconnect_starts_timer(self):
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {}
        serial_pids = {'emulator-5554': 100}
        connected_serials = set()  # Disconnected
        qemu_pids = {}

        update_device_cache(device_names, disconnect_times, serial_pids,
                            connected_serials, qemu_pids, 1000.0)

        self.assertEqual(device_names,
                         {'emulator-5554': 'Model A'})  # Still there
        self.assertEqual(disconnect_times,
                         {'emulator-5554': 1000.0})  # Timer started

    def test_disconnect_timeout_expires(self):
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {'emulator-5554': 1000.0}
        serial_pids = {'emulator-5554': 100}
        connected_serials = set()
        qemu_pids = {}

        update_device_cache(device_names, disconnect_times, serial_pids,
                            connected_serials, qemu_pids, 1006.0)  # Delta > 5

        self.assertEqual(device_names, {})  # Removed
        self.assertEqual(disconnect_times, {})
        self.assertEqual(serial_pids, {})

    def test_disconnect_waiting(self):
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {'emulator-5554': 1000.0}
        serial_pids = {'emulator-5554': 100}
        connected_serials = set()
        qemu_pids = {}

        update_device_cache(device_names, disconnect_times, serial_pids,
                            connected_serials, qemu_pids, 1004.0)  # Delta <= 5

        self.assertEqual(device_names,
                         {'emulator-5554': 'Model A'})  # Still there
        self.assertEqual(disconnect_times, {'emulator-5554': 1000.0})

    def test_reconnect_before_timeout(self):
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {'emulator-5554': 1000.0}
        serial_pids = {'emulator-5554': 100}
        connected_serials = {'emulator-5554'}
        qemu_pids = {'emulator-5554': 100}

        update_device_cache(device_names, disconnect_times, serial_pids,
                            connected_serials, qemu_pids, 1004.0)

        self.assertEqual(disconnect_times, {})  # Cleared
        self.assertEqual(device_names,
                         {'emulator-5554': 'Model A'})  # Preserved

    def test_new_pid_after_offline_tick_invalidates_cached_name(self):
        from emulator_resource_monitor import update_device_cache
        device_names = {'emulator-5554': 'Model A'}
        serial_pids = {'emulator-5554': 100}
        # Emulator A exits while adb still lists the serial as offline.
        update_device_cache(device_names, {}, serial_pids, {'emulator-5554'},
                            {}, 1000.0)
        self.assertEqual(serial_pids, {})
        # Emulator B starts on the same serial.
        forgotten = update_device_cache(device_names, {}, serial_pids,
                                        {'emulator-5554'},
                                        {'emulator-5554': 200}, 1002.0)
        self.assertEqual(device_names, {})
        self.assertEqual(forgotten, {'emulator-5554'})
        self.assertEqual(serial_pids, {'emulator-5554': 200})

    def test_first_pid_and_eviction_report_forgotten_serials(self):
        from emulator_resource_monitor import update_device_cache
        serial_pids = {}
        # The first PID seen for a serial with no cached name is not a change.
        self.assertEqual(
            update_device_cache({}, {}, serial_pids, {'emulator-5554'},
                                {'emulator-5554': 100}, 1000.0), set())
        self.assertEqual(serial_pids, {'emulator-5554': 100})
        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {'emulator-5554': 1000.0}
        self.assertEqual(
            update_device_cache(device_names, disconnect_times, serial_pids,
                                set(), {}, 1006.0), {'emulator-5554'})

    def test_replaced_emulator_does_not_inherit_old_model_or_record(self):
        # Pixel 7 (pid 1000) runs for two ticks, then Pixel 8 (pid 2000)
        # takes the same serial. The guest query for the replacing tick starts
        # before the host scan shows the new pid, so it still carries the old
        # cached model.
        host = [
            _host_tuple({'emulator-5554': 1000}, {'emulator-5554': 'Pixel_7'},
                        qemu_stats={'emulator-5554': ('20.0%', '1000.0MB')})
        ] * 2 + [
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={'emulator-5554': ('200.0%', '3000.0MB')})
        ] * 3
        summary_data, _ = _run_main_sync(host, ['Pixel 7', 'Pixel 7'] +
                                         ['Pixel 8'] * 3)
        emus = summary_data['emulators']
        self.assertIn('Pixel 8 | Android',
                      emus['Pixel_8_emulator-5554']['name'])
        self.assertNotIn('Pixel 7', emus['Pixel_8_emulator-5554']['name'])
        # The retired Pixel 7 record keeps only its own sample.
        self.assertEqual(emus['Pixel_7_emulator-5554']['qemu_cpu_iterations'],
                         1)
        self.assertEqual(emus['Pixel_7_emulator-5554']['qemu_cpu_max'], 20.0)
        self.assertEqual(emus['Pixel_8_emulator-5554']['qemu_cpu_max'], 200.0)
        for row in list(summary_data['time_series'])[1:]:
            self.assertTrue(math.isnan(row['qemu_cpu_Pixel_7_emulator-5554']))
            self.assertTrue(math.isnan(
                row['emu_cpu_adj_Pixel_7_emulator-5554']))

    def test_replacing_emulator_with_unknown_avd_gets_own_record(self):
        host = [
            _host_tuple({'emulator-5554': 1000}, {'emulator-5554': 'Pixel_7'},
                        qemu_stats={'emulator-5554': ('20.0%', '1000.0MB')})
        ] * 2 + [
            _host_tuple({'emulator-5554': 2000}, {},
                        qemu_stats={'emulator-5554': ('200.0%', '3000.0MB')}),
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={'emulator-5554': ('200.0%', '3000.0MB')}),
        ]
        summary_data, _ = _run_main_sync(host, ['Pixel 7', 'Pixel 7'] +
                                         ['Pixel 8'] * 2)
        emus = summary_data['emulators']
        self.assertEqual(sorted(emus),
                         ['Pixel_7_emulator-5554', 'Pixel_8_emulator-5554'])
        self.assertEqual(emus['Pixel_7_emulator-5554']['current_pid'], 1000)
        self.assertEqual(emus['Pixel_7_emulator-5554']['qemu_cpu_iterations'],
                         1)
        self.assertEqual(emus['Pixel_8_emulator-5554']['current_pid'], 2000)
        self.assertEqual(emus['Pixel_8_emulator-5554']['qemu_cpu_iterations'],
                         2)

    def test_qemu_next_group_pid_shifts_preserve_name_boot_state_and_single_record(
            self):
        import emulator_resource_monitor as erm
        from emulator_resource_monitor import update_device_cache

        # End-to-end main(): launcher(1000) -> launcher+worker(1001, Pixel_8) ->
        # worker exits leaving launcher(1000, avd_name=None) while adb=offline.
        tick = [0]
        dev_seq = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'offline'
            },
        ]

        def fake_host():
            idx = min(tick[0], 2)
            if idx == 0:
                erm._LAST_EMU_GROUP_PIDS['emulator-5554'] = {1000}
                erm._LAST_PRIMARY_PROC_ID['emulator-5554'] = (1000, 10.0)
                return _host_tuple(
                    {'emulator-5554': 1000}, {},
                    qemu_stats={'emulator-5554': ('10.0%', '500.0MB')})
            if idx == 1:
                erm._LAST_EMU_GROUP_PIDS['emulator-5554'] = {1000, 1001}
                erm._LAST_PRIMARY_PROC_ID['emulator-5554'] = (1001, 10.5)
                return _host_tuple(
                    {'emulator-5554': 1001}, {'emulator-5554': 'Pixel_8'},
                    qemu_stats={'emulator-5554': ('30.0%', '2000.0MB')})
            erm._LAST_EMU_GROUP_PIDS['emulator-5554'] = {1000}
            erm._LAST_PRIMARY_PROC_ID['emulator-5554'] = (1000, 10.0)
            return _host_tuple(
                {'emulator-5554': 1000}, {},
                qemu_stats={'emulator-5554': ('5.0%', '200.0MB')})

        def fake_guest(serial, known_model, is_booting, status):
            if status != 'device':
                return (known_model if known_model else f'Unknown ({status})',
                        is_booting, f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})')
            return (_guest_model('Pixel 8'), False, '40.0%', '30.0%',
                    '50.0% (2.0GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            if tick[0] == 3:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 2)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emus = mock_save.call_args[0][0]['emulators']
        self.assertEqual(list(emus.keys()), ['Pixel_8_emulator-5554'])
        emu = emus['Pixel_8_emulator-5554']
        self.assertFalse(emu.get('retired', False))
        self.assertEqual(emu['cpu_iterations'], 1)
        self.assertTrue(emu['boot_completed'])
        self.assertIn('Android 14', emu['name'])
        self.assertNotIn('Unknown', emu['name'])

        device_names = {'emulator-5554': 'Model A'}
        disconnect_times = {}
        serial_pids = {'emulator-5554': 1000}
        serial_group_pids = {'emulator-5554': {1000}}
        self.assertEqual(
            update_device_cache(device_names,
                                disconnect_times,
                                serial_pids, {'emulator-5554'},
                                {'emulator-5554': 1000},
                                100.0,
                                serial_group_pids=serial_group_pids,
                                live_group_pids={'emulator-5554': {1000}}),
            set())
        # Startup shift: launcher 1000 -> worker 1001 in same group.
        self.assertEqual(
            update_device_cache(device_names,
                                disconnect_times,
                                serial_pids, {'emulator-5554'},
                                {'emulator-5554': 1001},
                                102.0,
                                serial_group_pids=serial_group_pids,
                                live_group_pids={'emulator-5554': {1000,
                                                                   1001}}),
            set())
        self.assertEqual(device_names, {'emulator-5554': 'Model A'})
        # Shutdown shift: worker 1001 exits -> surviving launcher 1000.
        self.assertEqual(
            update_device_cache(device_names,
                                disconnect_times,
                                serial_pids, {'emulator-5554'},
                                {'emulator-5554': 1000},
                                104.0,
                                serial_group_pids=serial_group_pids,
                                live_group_pids={'emulator-5554': {1000}}),
            set())
        self.assertEqual(device_names, {'emulator-5554': 'Model A'})
        # Unrelated replacement PID 2000 invalidates cache.
        self.assertEqual(
            update_device_cache(device_names,
                                disconnect_times,
                                serial_pids, {'emulator-5554'},
                                {'emulator-5554': 2000},
                                106.0,
                                serial_group_pids=serial_group_pids,
                                live_group_pids={'emulator-5554': {2000}}),
            {'emulator-5554'})
        self.assertEqual(device_names, {})

    def test_bare_serial_from_previous_pid_is_retired_not_merged_into_new_avd(
            self):
        host = [
            _host_tuple({'emulator-5554': 1000}, {},
                        qemu_stats={'emulator-5554': ('300.0%', '1000.0MB')})
        ] * 2 + [
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={'emulator-5554': ('20.0%', '2000.0MB')})
        ] * 2
        summary_data, _ = _run_main_sync(
            host, ['Generic', 'Generic', 'Pixel 8', 'Pixel 8'])
        emus = summary_data['emulators']
        self.assertEqual(sorted(emus.keys()),
                         ['Pixel_8_emulator-5554', 'emulator-5554'])
        self.assertTrue(emus['emulator-5554'].get('retired'))
        self.assertEqual(emus['emulator-5554']['qemu_cpu_max'], 300.0)
        self.assertEqual(emus['Pixel_8_emulator-5554']['qemu_cpu_max'], 20.0)
        self.assertEqual(emus['Pixel_8_emulator-5554']['qemu_cpu_iterations'],
                         2)

    def test_disconnect_evicts_unnamed_serial_pids_and_clears_replacement_trends(
            self):
        import emulator_resource_monitor as erm
        from emulator_resource_monitor import update_device_cache

        device_names = {}
        disconnect_times = {}
        serial_pids = {}
        serial_group_pids = {}
        self.assertEqual(
            update_device_cache(device_names,
                                disconnect_times,
                                serial_pids, {'emulator-5554'},
                                {'emulator-5554': 1000},
                                100.0,
                                serial_group_pids=serial_group_pids), set())
        self.assertEqual(serial_pids, {'emulator-5554': 1000})
        self.assertEqual(
            update_device_cache(device_names,
                                disconnect_times,
                                serial_pids,
                                set(), {},
                                101.0,
                                serial_group_pids=serial_group_pids), set())
        self.assertEqual(
            update_device_cache(device_names,
                                disconnect_times,
                                serial_pids,
                                set(), {},
                                108.0,
                                serial_group_pids=serial_group_pids),
            {'emulator-5554'})
        self.assertEqual(serial_pids, {})
        self.assertEqual(serial_group_pids, {})
        self.assertEqual(disconnect_times, {})

        tick = [0]
        frames = []
        host_seq = [
            _host_tuple({'emulator-5554': 1000}, {'emulator-5554': 'Pixel_7'}),
            _host_tuple({'emulator-5554': 1000}, {'emulator-5554': 'Pixel_7'}),
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'}),
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'}),
        ]

        def fake_guest(serial, known_model, is_booting, status):
            if tick[0] < 2:
                return (_guest_model('Pixel 7'), False, '90.0%', '90.0%',
                        '80.0% (3.2GB/4.0GB, 0 errs, 10% data)', '50.0 MB')
            return (_guest_model('Pixel 8'), False, '10.0%', '10.0%',
                    '20.0% (0.8GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      return_value={'emulator-5554': 'device'}), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=lambda: host_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary'), \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=lambda *a, **kw: frames.append(dict(a[4]))), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        # Frame 3 is Pixel 8's first fresh dashboard tuple (tick 2 dropped the
        # pre-replacement query started for Pixel 7).
        p8_tuple = frames[3]['emulator-5554']
        self.assertTrue(p8_tuple[3].endswith('→'), p8_tuple)
        self.assertTrue(p8_tuple[4].endswith('→'), p8_tuple)
        self.assertTrue(p8_tuple[5].endswith('→'), p8_tuple)


def _guest_model(name):
    return f"{name} | Android 14 (API 34) | Build: UP1A | Res: 1080x2400"


def _run_main_sync(host_results,
                   guest_names,
                   devices=None,
                   hang_guest_ticks=(),
                   hang_host_ticks=(),
                   offline_guest_ticks=(),
                   argv=(),
                   sleep=None):
    """Runs main() for len(host_results) ticks with synchronous futures.

    Tick i returns host_results[i] and a guest that reports guest_names[i]
    unless it is given a known model, like get_guest_stats. Queries remain
    unfinished on ticks in hang_guest_ticks or hang_host_ticks and complete on
    the first non-hanging tick. `devices` is the adb device table, or a list
    with one table per tick. `sleep` replaces the time.sleep() stand-in that
    counts the ticks: every tick then gets the first results, and the run
    lasts until `sleep` raises KeyboardInterrupt. Returns (summary_data,
    guest_calls).
    """
    import emulator_resource_monitor as erm
    tick = [0]
    guest_calls = []

    def fake_host():
        return host_results[min(tick[0], len(host_results) - 1)]

    def fake_devices():
        if isinstance(devices, list):
            return devices[min(tick[0], len(devices) - 1)]
        return devices or {'emulator-5554': 'device'}

    def fake_guest(serial, known_model, is_booting, status):
        guest_calls.append((tick[0], serial, known_model))
        model = known_model
        if not model or 'android' not in model.lower():
            model = _guest_model(guest_names[min(tick[0],
                                                 len(guest_names) - 1)])
        if tick[0] in offline_guest_ticks:
            return (model, False, 'N/A (offline)', 'N/A (offline)',
                    'N/A (offline)', 'N/A (offline)')
        return (model, False, '40.0%', '30.0%',
                '50.0% (2.0GB/4.0GB, 5 errs, 13% (1G used/7G avail) data)',
                '10.0 MB')

    hung_futures = []

    def fake_submit(fn, *args, **kwargs):
        f = MagicMock()
        if fn is erm.get_guest_stats and tick[0] in hang_guest_ticks:
            res = fn(*args, **kwargs)
            done_flag = [False]
            harvested = [False]
            f.done.side_effect = lambda: done_flag[0]
            f.result.side_effect = lambda: (harvested.__setitem__(0, True), res
                                           )[1]
            f._maybe_finish = lambda: done_flag.__setitem__(
                0, tick[0] not in hang_guest_ticks)
            hung_futures.append((f, done_flag, harvested))
            return f
        if fn is erm.get_host_stats and tick[0] in hang_host_ticks:
            res = fn(*args, **kwargs)
            done_flag = [False]
            harvested = [False]
            f.done.side_effect = lambda: done_flag[0]
            f.result.side_effect = lambda: (harvested.__setitem__(0, True), res
                                           )[1]
            f._maybe_finish = lambda: done_flag.__setitem__(
                0, tick[0] not in hang_host_ticks)
            hung_futures.append((f, done_flag, harvested))
            return f
        f.done.return_value = True
        f.result.return_value = fn(*args, **kwargs)
        return f

    def fake_wait(fs, timeout=None, *args, **kwargs):
        for f in fs:
            finish = getattr(f, '_maybe_finish', None)
            if finish:
                finish()
        return set(fs), set()

    executor = MagicMock()
    executor.submit.side_effect = fake_submit

    def fake_sleep(_):
        tick[0] += 1
        if tick[0] == len(host_results):
            raise KeyboardInterrupt

    with patch('concurrent.futures.ThreadPoolExecutor',
               return_value=executor), \
            patch('concurrent.futures.wait', side_effect=fake_wait), \
            patch('emulator_resource_monitor.run_cmd', return_value=''), \
            patch('emulator_resource_monitor.get_host_stats',
                  side_effect=fake_host), \
            patch('emulator_resource_monitor.get_devices',
                  side_effect=fake_devices), \
            patch('emulator_resource_monitor.get_guest_stats',
                  side_effect=fake_guest), \
            patch('emulator_resource_monitor.save_summary') as mock_save, \
            patch('emulator_resource_monitor.print_dashboard'), \
            patch('time.sleep', side_effect=sleep or fake_sleep), \
            patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                               '--no-netsim-log', *argv]), \
            patch('os._exit', return_value=None):
        erm.main()
    for f, done_flag, harvested in hung_futures:
        if done_flag[0] and not f.cancel.called:
            assert harvested[0], 'Completed hung future was never harvested'
    return mock_save.call_args[0][0], guest_calls


_MemInfo = namedtuple('_MemInfo', ['rss'])
_CtxSwitches = namedtuple('_CtxSwitches', ['voluntary', 'involuntary'])


def _fake_proc(name,
               pid,
               cmdline,
               cpu=0.0,
               rss_mb=0,
               threads=1,
               ctx=(0, 0),
               exe=None,
               ppid=None,
               conns=None):
    """Returns a psutil.Process stand-in with explicit return values."""
    proc = MagicMock()
    info = {'name': name, 'pid': pid, 'create_time': float(pid)}
    if exe is not None:
        info['exe'] = exe
    if ppid is not None:
        info['ppid'] = ppid
    proc.info = info
    proc.pid = pid
    proc.cmdline.return_value = cmdline
    proc.exe.return_value = exe if exe is not None else ''
    if callable(cpu):
        proc.cpu_percent.side_effect = cpu
    else:
        proc.cpu_percent.return_value = cpu
    proc.memory_info.return_value = _MemInfo(rss=rss_mb * 1024 * 1024)
    proc.num_threads.return_value = threads
    proc.num_ctx_switches.return_value = _CtxSwitches(*ctx)
    proc.net_connections.return_value = conns or []
    proc.environ.return_value = {}
    return proc


def _host_tuple(qemu_pids=None, qemu_avds=None, qemu_stats=None):
    return ("10%", qemu_stats or
            {}, "2%", "30%", "0%", 0, 0, [], "10MB", qemu_pids or
            {}, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", qemu_avds or
            {})


class TestCorrectnessFixes(unittest.TestCase):

    def setUp(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()
        emulator_resource_monitor._NVIDIA_SMI_FAILED = False
        emulator_resource_monitor._NVIDIA_PMON_FAILED = False
        emulator_resource_monitor._LINUX_INTEL_GPU_FAILED = False
        # Point emulator discovery files at an empty directory.
        discovery = tempfile.TemporaryDirectory()
        self.addCleanup(discovery.cleanup)
        env = patch.dict(
            os.environ, {
                'ANDROID_EMULATOR_DISCOVERY_DIR': discovery.name,
                'XDG_RUNTIME_DIR': discovery.name
            })
        env.start()
        self.addCleanup(env.stop)
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)

    def tearDown(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()
        emulator_resource_monitor._NVIDIA_SMI_FAILED = False
        emulator_resource_monitor._NVIDIA_PMON_FAILED = False
        emulator_resource_monitor._LINUX_INTEL_GPU_FAILED = False

    @patch('psutil.process_iter')
    def test_primary_stays_worker_when_gui_and_launcher_cpu_spike(
            self, mock_iter):
        from emulator_resource_monitor import get_process_stats
        launcher, worker, gui = 3000, 3001, 3002
        # Sample 0 primes cpu_percent. In sample 2 the launcher and the GUI
        # both use more CPU than the worker.
        cpu_by_pid = {
            launcher: [0.0, 0.0, 70.0, 0.0],
            worker: [0.0, 40.0, 10.0, 40.0],
            gui: [0.0, 5.0, 60.0, 5.0],
        }
        sample = [0]

        def cpu_for(pid):
            return lambda interval=None: cpu_by_pid[pid][sample[0]]

        mock_iter.return_value = [
            _fake_proc('emulator',
                       launcher,
                       ['emulator', '-avd', 'Pixel_8_API_35', '-port', '5554'],
                       cpu=cpu_for(launcher),
                       rss_mb=40),
            _fake_proc('qemu-system-x86_64',
                       worker, [
                           'qemu-system-x86_64', '-device',
                           'avdstart,serial_number=5554,avd_name=Pixel 8 API 35'
                       ],
                       cpu=cpu_for(worker),
                       rss_mb=3000),
            _fake_proc('fishtank',
                       gui,
                       ['fishtank', '@Pixel_8_API_35', '-fishtank', '5554'],
                       cpu=cpu_for(gui),
                       rss_mb=300),
        ]

        get_process_stats()
        for i in (1, 2, 3):
            sample[0] = i
            _, _, _, _, qemu_pids, qemu_avds = get_process_stats()
            self.assertEqual(qemu_pids['emulator-5554'], worker, f'sample {i}')
            # The AVD id from the launcher wins over the worker's display name.
            self.assertEqual(qemu_avds['emulator-5554'], 'Pixel_8_API_35',
                             f'sample {i}')

    def test_parse_cmdline_edge_cases(self):
        cases = [
            # An explicit console port wins over the adb host_port heuristic.
            ([
                'qemu-system-x86_64', '-ports', '5554,5557', '-device',
                'virtio-goldfish-adb,host_port=5557'
            ], (5554, None)),
            (['emulator', '-ports', '5554,5557'], (5554, None)),
            # A value containing "-avd" is not parsed as a glued -avd flag.
            (['emulator', '-avd', 'my-avd'], (None, 'my-avd')),
            (['emulator', '-avd', 'my-avd', '-port', '5556'], (5556, 'my-avd')),
            (['emulator', '-port', '5558', '-avd',
              'test-avd-2'], (5558, 'test-avd-2')),
            (['emulator', '-avdPixel_6_API_31'], (None, 'Pixel_6_API_31')),
            ([
                'qemu-system-aarch64', '-name', 'Pixel_Tablet', '-device',
                'virtio-goldfish-adb,host_port=5557'
            ], (5556, 'Pixel_Tablet')),
        ]
        for cmdline, expected in cases:
            with self.subTest(cmdline=cmdline):
                self.assertEqual(parse_cmdline(cmdline), expected)

    def test_save_summary_p95_uses_nearest_rank(self):
        from emulator_resource_monitor import save_summary
        summary = {
            'iterations':
                20,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                20,
            'host_cpu_sum':
                210.0,
            'host_cpu_max':
                20.0,
            'host_ram_iterations':
                20,
            'host_ram_sum':
                1000.0,
            'host_ram_max':
                50.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0 + i,
                'host_cpu': float(i + 1),
                'host_ram': 50.0
            } for i in range(20)]
        }
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch('emulator_resource_monitor.run_cmd', return_value=''), \
                patch('sys.stdout', new_callable=io.StringIO):
            save_summary(summary,
                         1030.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir=tmpdir)
            txt_files = [f for f in os.listdir(tmpdir) if f.endswith('.txt')]
            with open(os.path.join(tmpdir, txt_files[0]),
                      encoding='utf-8') as f:
                text = f.read()
        # Nearest-rank p95 of 1..20 is the 19th value, not the maximum.
        self.assertIn('P95 CPU Usage:     19.0%', text)

    @patch('psutil.process_iter')
    def test_netsim_cpu_counts_only_netsimd(self, mock_iter):
        from emulator_resource_monitor import get_process_stats
        me = os.getpid()
        mock_iter.return_value = [
            _fake_proc('netsimd',
                       4000, ['/sdk/emulator/bin/netsimd'],
                       cpu=10.0,
                       rss_mb=100,
                       exe='/sdk/emulator/bin/netsimd'),
            _fake_proc('netsim-ui', 4001, ['netsim-ui'], cpu=20.0, rss_mb=50),
            # A netsim CLI call made by this monitor.
            _fake_proc('netsim',
                       4002, ['/sdk/emulator/bin/netsim', 'capture', 'list'],
                       cpu=30.0,
                       rss_mb=10,
                       ppid=me),
            # A netsimd inside this monitor's own process tree.
            _fake_proc('netsimd',
                       4003, ['netsimd'],
                       cpu=40.0,
                       rss_mb=20,
                       ppid=4002),
        ]
        get_process_stats()
        _, _, netsim_cpu, netsim_ram, _, _ = get_process_stats()
        self.assertEqual(netsim_cpu, '10.0% (1 processes)')
        self.assertEqual(netsim_ram, '100.0MB (1 processes)')

    @patch('psutil.process_iter')
    def test_qemu_img_and_crashpad_are_not_emulators(self, mock_iter):
        from emulator_resource_monitor import get_process_stats
        mock_iter.return_value = [
            _fake_proc(
                'qemu-img',
                5000,
                ['qemu-img', 'convert', '-O', 'qcow2', 'a.img', 'b.qcow2'],
                cpu=50.0,
                rss_mb=20),
            _fake_proc('crashpad_handler',
                       5001, ['crashpad_handler'],
                       cpu=1.0,
                       rss_mb=5),
        ]
        get_process_stats()
        qemu_stats, unmapped_qemu, _, _, _, _ = get_process_stats()
        self.assertEqual(qemu_stats, {})
        self.assertEqual(unmapped_qemu, [])

    @patch('psutil.process_iter')
    def test_launcher_named_launcherloop_joins_worker_group(self, mock_iter):
        from emulator_resource_monitor import get_process_stats
        # The QEMU-next launcher renames its main thread, so its process name
        # is LauncherLoop. It owns the console port.
        console = MagicMock(status='LISTEN',
                            laddr=MagicMock(ip='::1', port=5554))
        launcher = _fake_proc('LauncherLoop',
                              6000,
                              ['/sdk/emulator/emulator', '-avd', 'Pixel_7'],
                              cpu=3.0,
                              rss_mb=50,
                              threads=10,
                              ctx=(10, 0),
                              exe='/sdk/emulator/emulator',
                              conns=[console])
        worker = _fake_proc(
            'qemu-system-x86_64',
            6001, [
                '/sdk/emulator/bin/qemu-system-x86_64', '-device',
                'avdstart,serial_number=5554,avd_name=Pixel 7'
            ],
            cpu=40.0,
            rss_mb=2000,
            threads=30,
            ctx=(100, 0),
            exe='/sdk/emulator/bin/qemu-system-x86_64')
        mock_iter.return_value = [launcher, worker]

        get_process_stats()
        qemu_stats, unmapped, _, _, qemu_pids, qemu_avds = get_process_stats()

        self.assertEqual(qemu_stats['emulator-5554'][0], '43.0%')
        self.assertIn('2050.0MB (40 thr, 110 cs)',
                      qemu_stats['emulator-5554'][1])
        self.assertEqual(qemu_pids['emulator-5554'], 6001)
        self.assertEqual(qemu_avds['emulator-5554'], 'Pixel_7')
        self.assertEqual(unmapped, [])

    @patch('psutil.process_iter')
    def test_late_pid_ini_resolves_avd_id_and_display_name(self, mock_iter):
        import emulator_resource_monitor as erm
        # The worker's console port is on its cmdline before the emulator
        # writes avd/running/pid_<pid>.ini, so the ini is read again until it
        # appears. As in Rust, avd.name (with '_' shown as spaces) is the
        # display name, and a cmdline avd_name= is only a fallback AVD id.
        mock_iter.return_value = [
            _fake_proc('qemu-system-x86_64',
                       6100, [
                           'qemu-system-x86_64', '-device',
                           'avdstart,serial_number=5554,avd_name="Pixel 8 Pro"'
                       ],
                       cpu=10.0,
                       rss_mb=100)
        ]
        erm.get_process_stats()
        info = erm._snapshot_process_state()[2]['emulator-5554']
        self.assertEqual(info['avd_id'], 'Pixel 8 Pro')
        self.assertIsNone(info['display_name'])

        running = os.path.join(os.environ['ANDROID_EMULATOR_DISCOVERY_DIR'],
                               'avd', 'running')
        os.makedirs(running)
        with open(os.path.join(running, 'pid_6100.ini'), 'w') as f:
            f.write('avd.id=Pixel_8\navd.name=Pixel_8_Pro\n'
                    'netsim.endpoint=localhost:8877\n')
        _, _, _, _, _, qemu_avds = erm.get_process_stats()
        info = erm._snapshot_process_state()[2]['emulator-5554']
        self.assertEqual(qemu_avds['emulator-5554'], 'Pixel_8')
        self.assertEqual(info['avd_id'], 'Pixel_8')
        self.assertEqual(info['display_name'], 'Pixel 8 Pro')
        self.assertEqual(info['grpc_endpoint'], 'localhost:8877')
        self.assertEqual(
            erm.parse_cmdline([
                'qemu-system-x86_64', '-device',
                'avdstart,serial_number=5554,avd_id=Pixel_8,avd_name="Pixel 8 Pro"'
            ]), (5554, 'Pixel_8'))

    @patch('psutil.process_iter')
    def test_emu_gpu_summed_over_process_group(self, mock_iter):
        import emulator_resource_monitor as erm
        mock_iter.return_value = [
            _fake_proc('emulator',
                       7000, ['emulator', '-avd', 'Pixel_7', '-port', '5554'],
                       cpu=1.0,
                       rss_mb=40),
            _fake_proc('qemu-system-x86_64',
                       7001, [
                           'qemu-system-x86_64', '-device',
                           'avdstart,serial_number=5554,avd_name=Pixel 7'
                       ],
                       cpu=50.0,
                       rss_mb=3000),
            _fake_proc('fishtank',
                       7002, ['fishtank', '@Pixel_7', '-fishtank', '5554'],
                       cpu=5.0,
                       rss_mb=300),
        ]
        erm.get_process_stats()
        _, _, _, _, qemu_pids, _ = erm.get_process_stats()

        saved = dict(erm._EMU_GPU_CACHE)
        # The GUI process renders, so pmon reports the load on it and not on
        # the worker. 'fetching' stops a real nvidia-smi refresh.
        erm._EMU_GPU_CACHE.update({
            'time': time.monotonic(),
            'fetching': True,
            'val': {
                7001: 0.0,
                7002: 45.0
            }
        })
        try:
            res = erm.get_emu_gpu_stats(qemu_pids)
        finally:
            erm._EMU_GPU_CACHE.clear()
            erm._EMU_GPU_CACHE.update(saved)
        self.assertEqual(res, {'emulator-5554': '45.0%'})

    @patch('psutil.process_iter')
    def test_port_fallback_finds_ipv6_console_listener(self, mock_iter):
        from emulator_resource_monitor import get_process_stats
        v6_listener = MagicMock(status='LISTEN',
                                laddr=MagicMock(ip='::1', port=5556))
        proc = _fake_proc('emulator',
                          8000, ['emulator', '-avd', 'Pixel_7'],
                          cpu=2.0,
                          rss_mb=40)
        proc.net_connections.side_effect = (
            lambda kind='inet': []
            if kind in ('tcp4', 'inet4', 'udp4') else [v6_listener])
        mock_iter.return_value = [proc]

        get_process_stats()
        qemu_stats, unmapped, _, _, _, _ = get_process_stats()
        self.assertIn('emulator-5556', qemu_stats)
        self.assertEqual(unmapped, [])

    @patch('emulator_resource_monitor.save_summary')
    @patch('emulator_resource_monitor.get_guest_stats')
    @patch('emulator_resource_monitor.get_devices')
    @patch('emulator_resource_monitor.get_host_stats')
    def test_main_loop_keeps_one_key_when_avd_name_source_changes(
            self, mock_host, mock_devices, mock_guest, mock_save):
        from emulator_resource_monitor import main
        pids = {'emulator-5554': 4242}
        mock_devices.return_value = {'emulator-5554': 'device'}
        # Same worker pid throughout; the name first comes from the display
        # name, then from the AVD id.
        mock_host.side_effect = [
            _host_tuple(pids, {}),
            _host_tuple(pids, {'emulator-5554': 'Pixel 8 API 35'}),
            _host_tuple(pids, {'emulator-5554': 'Pixel_8_API_35'}),
        ]
        mock_guest.return_value = ('Pixel 8', False, '10%', '5%', '50.0%',
                                   '0.0 MB')
        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 3:
                raise KeyboardInterrupt

        with patch('time.sleep', side_effect=mock_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--interval', '0.1', '--no-summary']), \
                patch('os._exit', return_value=None):
            try:
                main()
            except KeyboardInterrupt:
                pass

        summary_data = mock_save.call_args[0][0]
        self.assertEqual(list(summary_data['emulators']),
                         ['Pixel 8 API 35_emulator-5554'])

    def test_main_loop_queries_guest_on_tick_adb_lists_it(self):
        host = [_host_tuple({'emulator-5554': 1000})] * 2
        _, guest_calls = _run_main_sync(host, ['Pixel 8'] * 2)
        self.assertEqual([t for t, _, _ in guest_calls], [0, 1])

    def test_timed_out_samples_and_placeholder_errs_are_not_recorded(self):
        # Tick 0 primes baselines; tick 1 records a fresh sample; tick 2 hangs
        # the guest query; tick 3 hangs the host query; tick 4 reports an
        # offline guest whose RAM is a placeholder.
        host = [
            _host_tuple(
                {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
                qemu_stats={'emulator-5554': ('20.0%', '1000.0MB (50 cs/s)')})
        ] * 5
        summary_data, guest_calls = _run_main_sync(host, ['Pixel 8'] * 5,
                                                   hang_guest_ticks=(2,),
                                                   hang_host_ticks=(3,),
                                                   offline_guest_ticks=(4,))
        self.assertEqual([t for t, _, _ in guest_calls], [0, 1, 2, 4])
        emu = summary_data['emulators']['Pixel_8_emulator-5554']
        # Ticks 1 and 3 had fresh non-placeholder guest completions (tick 2
        # hung and finished on tick 3; tick 4 was offline). Ticks 1, 2, 4 had
        # fresh host completions (tick 3 hung and finished on tick 4).
        self.assertEqual(emu['cpu_iterations'], 2)
        self.assertEqual(emu['ram_iterations'], 2)
        self.assertEqual(emu['swap_iterations'], 2)
        self.assertEqual(summary_data['host_cpu_iterations'], 3)
        self.assertEqual(summary_data['host_ram_iterations'], 3)
        self.assertEqual(emu['qemu_cpu_iterations'], 3)

        rows = list(summary_data['time_series'])
        self.assertEqual(len(rows), 4)
        # Tick 1 (rows[0]): both fresh.
        self.assertEqual(rows[0]['emu_ram_Pixel_8_emulator-5554'], 50.0)
        self.assertEqual(rows[0]['emu_errs_Pixel_8_emulator-5554'], 5.0)
        self.assertEqual(rows[0]['host_cpu'], 10.0)
        self.assertEqual(rows[0]['qemu_cpu_Pixel_8_emulator-5554'], 20.0)
        # Tick 2 (rows[1]): guest timed out -> all guest CSV columns NaN.
        for prefix in ('emu_cpu_tot_', 'emu_cpu_adj_', 'emu_ram_', 'emu_swap_',
                       'emu_errs_', 'emu_disk_pct_'):
            self.assertTrue(
                math.isnan(rows[1][f'{prefix}Pixel_8_emulator-5554']), prefix)
        self.assertEqual(rows[1]['host_cpu'], 10.0)
        # Tick 3 (rows[2]): host timed out -> host and QEMU CSV columns NaN.
        for col in ('host_cpu', 'host_ram', 'netsim_cpu', 'netsim_ram',
                    'host_swap', 'host_disk_read_mbs', 'host_disk_write_mbs',
                    'host_paging_in', 'host_paging_out',
                    'qemu_cpu_Pixel_8_emulator-5554',
                    'qemu_ram_Pixel_8_emulator-5554',
                    'qemu_cs_Pixel_8_emulator-5554',
                    'qemu_gpu_Pixel_8_emulator-5554'):
            self.assertTrue(math.isnan(rows[2][col]), col)
        self.assertEqual(rows[2]['emu_ram_Pixel_8_emulator-5554'], 50.0)
        # Tick 4 (rows[3]): fresh offline guest -> placeholder RAM gives NaN errs.
        self.assertTrue(math.isnan(rows[3]['emu_ram_Pixel_8_emulator-5554']))
        self.assertTrue(math.isnan(rows[3]['emu_errs_Pixel_8_emulator-5554']))

    def test_main_loop_harvests_between_tick_futures_and_waits_on_first_tick_and_once(
            self):
        import emulator_resource_monitor as erm
        tick = [0]
        between_tick_futures = []
        harvested_between_ticks = []
        wait_timeouts = []
        dev_seq = [
            {
                'emulator-5554': 'device',
                'emulator-5556': 'device'
            },
            {
                'emulator-5554': 'device',
                'emulator-5556': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device',
                'emulator-5556': 'device'
            },
        ]

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            if fn is erm.get_devices:
                f.done.return_value = True
                f.result.return_value = dev_seq[min(tick[0], len(dev_seq) - 1)]
                return f
            res = fn(*args, **kwargs)
            if tick[0] == 1:
                # Simulates queries that miss wait() on tick 1 and finish
                # during sleep() before tick 2's top-of-loop submit().
                done_flag = [False]
                f.done.side_effect = lambda: done_flag[0]

                def mark_harvested(val=res, serial=args[0] if args else 'host'):
                    harvested_between_ticks.append(serial)
                    return val

                f.result.side_effect = mark_harvested
                between_tick_futures.append((done_flag, f))
                return f
            # Keep pending until wait() runs so wait_timeouts records tick 0
            # and steady-state timeouts.
            done_flag = [False]
            f.done.side_effect = lambda: done_flag[0]
            f.result.return_value = res
            f._finish_in_wait = lambda: done_flag.__setitem__(0, True)
            return f

        def fake_wait(fs, timeout=None, *args, **kwargs):
            wait_timeouts.append((tick[0], timeout))
            for f in fs:
                finish = getattr(f, '_finish_in_wait', None)
                if finish:
                    finish()
            return set(fs), set()

        def fake_sleep(_):
            for done_flag, _ in between_tick_futures:
                done_flag[0] = True
            tick[0] += 1
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        host_val = _host_tuple(
            {
                'emulator-5554': 1000,
                'emulator-5556': 2000
            },
            {
                'emulator-5554': 'Pixel_8',
                'emulator-5556': 'Pixel_7'
            },
            qemu_stats={
                'emulator-5554': ('25.0%', '1000.0MB'),
                'emulator-5556': ('15.0%', '900.0MB'),
            },
        )
        guest_val = (_guest_model('Pixel 8'), False, '40.0%', '30.0%',
                     '50.0% (2.0GB/4.0GB, 3 errs, 10% data)', '5.0 MB')

        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('concurrent.futures.wait', side_effect=fake_wait), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=host_val), \
                patch('emulator_resource_monitor.get_guest_stats',
                      return_value=guest_val), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary_data = mock_save.call_args[0][0]
        # Tick 1's host + emulator-5554 futures finished between tick 1 and 2
        # and must be harvested on tick 2; emulator-5556 disconnected on tick 2
        # so its stale tick-1 future must be dropped instead of harvested.
        self.assertIn('host', harvested_between_ticks)
        self.assertIn('emulator-5554', harvested_between_ticks)
        self.assertNotIn('emulator-5556', harvested_between_ticks)
        self.assertEqual(summary_data['host_cpu_iterations'], 2)
        self.assertEqual(
            summary_data['emulators']['Pixel_8_emulator-5554']
            ['cpu_iterations'], 2)
        # Tick 0 waits without an interval deadline (timeout=None).
        tick0_timeouts = [t for tk, t in wait_timeouts if tk == 0]
        self.assertEqual(tick0_timeouts, [None])

        # --once also waits without an interval deadline on both priming and
        # output ticks.
        once_timeouts = []
        once_tick = [0]

        def once_submit(fn, *args, **kwargs):
            f = MagicMock()
            done_flag = [False]
            f.done.side_effect = lambda: done_flag[0]
            f.result.return_value = ({} if fn is erm.get_devices else host_val)
            f._finish_in_wait = lambda: done_flag.__setitem__(0, True)
            return f

        def once_wait(fs, timeout=None, *args, **kwargs):
            once_timeouts.append((once_tick[0], timeout))
            for f in fs:
                f._finish_in_wait()
            return set(fs), set()

        once_exec = MagicMock()
        once_exec.submit.side_effect = once_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=once_exec), \
                patch('concurrent.futures.wait', side_effect=once_wait), \
                patch('time.sleep',
                      side_effect=lambda _: once_tick.__setitem__(0, once_tick[0] + 1)), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--once', '--no-summary', '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()
        self.assertTrue(once_timeouts)
        self.assertTrue(all(t is None for _, t in once_timeouts), once_timeouts)

    def test_main_loop_na_data_preserves_guest_ram_errs_boot_log_and_trend(
            self):
        import emulator_resource_monitor as erm
        tick = [0]
        mono = [100.0]
        rendered_guest_ram = []
        guest_rams = [
            '50.0% (2.0GB/4.0GB, 7 errs, N/A data)',
            '50.0% (2.0GB/4.0GB, 7 errs, N/A data)',
            '60.0% (2.4GB/4.0GB, 9 errs, N/A data)',
        ]
        host_val = _host_tuple(
            {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
            qemu_stats={'emulator-5554': ('20.0%', '1000.0MB')})

        def fake_guest(serial, known_model, is_booting, status):
            idx = min(tick[0], len(guest_rams) - 1)
            return (_guest_model('Pixel 8'), True, '40.0%', '30.0%',
                    guest_rams[idx], '5.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_dashboard(*args, **kwargs):
            guest_results = args[4]
            if 'emulator-5554' in guest_results:
                rendered_guest_ram.append(guest_results['emulator-5554'][4])

        def fake_sleep(_):
            tick[0] += 1
            mono[0] += 10.0
            if tick[0] == 3:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      return_value={'emulator-5554': 'device'}), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=host_val), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=fake_dashboard), \
                patch('time.monotonic', side_effect=lambda: mono[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary_data = mock_save.call_args[0][0]
        emu = summary_data['emulators']['Pixel_8_emulator-5554']
        self.assertEqual(emu['ram_iterations'], 2)
        self.assertEqual(emu['ram_max'], 60.0)
        self.assertGreaterEqual(len(emu['boot_log']), 1)
        rows = list(summary_data['time_series'])
        self.assertEqual(rows[0]['emu_ram_Pixel_8_emulator-5554'], 50.0)
        self.assertEqual(rows[0]['emu_errs_Pixel_8_emulator-5554'], 7.0)
        self.assertTrue(
            math.isnan(rows[0]['emu_disk_pct_Pixel_8_emulator-5554']))
        self.assertTrue(rendered_guest_ram[-1].endswith('↑'),
                        rendered_guest_ram)

    def test_save_summary_ignores_non_finite_points_in_stats_charts_and_csv(
            self):
        from emulator_resource_monitor import save_summary
        summary = {
            'iterations':
                3,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                2,
            'host_cpu_sum':
                30.0,
            'host_cpu_max':
                20.0,
            'host_ram_iterations':
                2,
            'host_ram_sum':
                100.0,
            'host_ram_max':
                50.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {},
            'time_series': [
                {
                    'time': 1000.0,
                    'host_cpu': 10.0,
                    'host_ram': 50.0,
                    'host_thermal_c': 40.0,
                },
                {
                    'time': 1005.0,
                    'host_cpu': float('inf'),
                    'host_ram': 50.0,
                    'host_thermal_c': float('inf'),
                },
                {
                    'time': 1010.0,
                    'host_cpu': 20.0,
                    'host_ram': 50.0,
                    'host_thermal_c': 50.0,
                },
            ],
        }
        plotted_series = []

        def check_finite_plot(xs, ys, *_a, **_kw):
            self.assertTrue(
                all(
                    math.isfinite(x) and math.isfinite(y)
                    for x, y in zip(xs, ys)))
            plotted_series.append((list(xs), list(ys)))

        fake_fig = MagicMock()
        fake_fig.plot.side_effect = check_finite_plot
        fake_fig.show.return_value = 'mock_chart_plot'
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(sys.modules,
                           {'plotille': MagicMock(Figure=lambda: fake_fig)}), \
                patch('emulator_resource_monitor.run_cmd', return_value=''), \
                patch('sys.stdout', new_callable=io.StringIO):
            save_summary(summary,
                         1020.0,
                         save_csv=True,
                         save_txt=True,
                         out_dir=tmpdir,
                         is_autosave=True)
            txt = os.path.join(tmpdir, 'device_monitor_summary_autosave.txt')
            csv_path = os.path.join(tmpdir,
                                    'device_monitor_summary_autosave.csv')
            with open(txt, encoding='utf-8') as f:
                text = f.read()
            with open(csv_path, encoding='utf-8') as f:
                csv_text = f.read()
            summary['time_series'].append({
                'time': float('nan'),
                'host_cpu': 15.0,
                'host_ram': 50.0,
                'host_thermal_c': 45.0,
            })
            save_summary(summary,
                         1020.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir=tmpdir,
                         is_autosave=True)
            with open(txt, encoding='utf-8') as f:
                text_nan_time = f.read()
        self.assertGreater(len(plotted_series), 0)
        self.assertIn('Average Thermal:   45.0°C', text)
        self.assertIn('Peak Thermal:      50.0°C', text)
        self.assertIn('HOST CPU HISTORY CHART:', text)
        self.assertIn('HOST CPU HISTORY CHART:', text_nan_time)
        self.assertNotIn('inf', csv_text.lower())

    def test_save_summary_handles_empty_out_dir_and_skips_mkdir_when_disabled(
            self):
        from emulator_resource_monitor import save_summary
        summary = {
            'iterations':
                1,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                1,
            'host_cpu_sum':
                10.0,
            'host_cpu_max':
                10.0,
            'host_ram_iterations':
                1,
            'host_ram_sum':
                50.0,
            'host_ram_max':
                50.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 10.0,
                'host_ram': 50.0,
            }],
        }
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch('emulator_resource_monitor.run_cmd', return_value=''), \
                patch('sys.stdout', new_callable=io.StringIO):
            unused_dir = os.path.join(tmpdir, 'should_not_exist')
            save_summary(summary,
                         1010.0,
                         save_csv=False,
                         save_txt=False,
                         out_dir=unused_dir,
                         is_autosave=True)
            self.assertFalse(os.path.exists(unused_dir))

            with patch('os.makedirs', wraps=os.makedirs) as spy_makedirs:
                cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    save_summary(summary,
                                 1010.0,
                                 save_csv=False,
                                 save_txt=True,
                                 out_dir='',
                                 is_autosave=True)
                finally:
                    os.chdir(cwd)
                spy_makedirs.assert_called_once_with('.', exist_ok=True)
            self.assertTrue(
                os.path.isfile(
                    os.path.join(tmpdir,
                                 'device_monitor_summary_autosave.txt')))

    def test_save_summary_autosave_quiet_csv_and_preserves_backup_on_failure(
            self):
        from emulator_resource_monitor import save_summary
        summary = {
            'iterations':
                1,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                1,
            'host_cpu_sum':
                10.0,
            'host_cpu_max':
                10.0,
            'host_ram_iterations':
                1,
            'host_ram_sum':
                50.0,
            'host_ram_max':
                50.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 10.0,
                'host_ram': 50.0,
            }],
        }
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch('emulator_resource_monitor.run_cmd', return_value=''):
            buf = io.StringIO()
            with patch('sys.stdout', buf):
                save_summary(summary,
                             1010.0,
                             save_csv=True,
                             save_txt=True,
                             out_dir=tmpdir,
                             is_autosave=True)
            self.assertEqual(buf.getvalue(), '')
            autosave_txt = os.path.join(tmpdir,
                                        'device_monitor_summary_autosave.txt')
            autosave_csv = os.path.join(tmpdir,
                                        'device_monitor_summary_autosave.csv')
            self.assertTrue(os.path.isfile(autosave_txt))
            self.assertTrue(os.path.isfile(autosave_csv))

            real_open = open
            blocked_txt = os.path.join(
                tmpdir, 'device_monitor_summary_20261109_093000.txt')

            def path_specific_open(path, *args, **kwargs):
                if path == blocked_txt:
                    raise OSError('disk full')
                return real_open(path, *args, **kwargs)

            with patch('time.strftime', return_value='20261109_093000'), \
                    patch('builtins.open', side_effect=path_specific_open), \
                    patch('sys.stdout', io.StringIO()):
                save_summary(summary,
                             1020.0,
                             save_csv=True,
                             save_txt=True,
                             out_dir=tmpdir,
                             is_autosave=False)
            # Final txt failed, so autosave.txt must survive; final csv
            # succeeded, so autosave.csv is cleaned up.
            self.assertTrue(os.path.isfile(autosave_txt))
            self.assertFalse(os.path.exists(autosave_csv))

    def test_parse_cmdline_ignores_path_avd_substring_and_empty_avd(self):
        self.assertEqual(
            parse_cmdline([
                '/opt/my-avd_sdk/qemu-system-x86_64',
                '-initrd=/tmp/my-avd/ramdisk.img', '-device',
                'avdstart,serial_number=5554,avd_name=Pixel_7'
            ]), (5554, 'Pixel_7'))
        self.assertEqual(
            parse_cmdline([
                'qemu-system-x86_64', '-avd', '', '-device',
                'avdstart,serial_number=5554,avd_name=Pixel_7'
            ]), (5554, 'Pixel_7'))

    def test_get_gpu_stats_reads_non_card0_drm_sysfs(self):
        from emulator_resource_monitor import get_gpu_stats
        card1_busy = '/sys/class/drm/card1/device/gpu_busy_percent'
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == card1_busy:
                return io.StringIO('55\n')
            return real_open(path, *args, **kwargs)

        with patch('platform.system', return_value='Linux'), \
                patch('emulator_resource_monitor.HAS_GPUTIL', False), \
                patch('emulator_resource_monitor.run_cmd', return_value=''), \
                patch('glob.glob',
                      side_effect=lambda pat: [card1_busy]
                      if 'gpu_busy_percent' in pat else []), \
                patch('os.path.exists',
                      side_effect=lambda p: p == card1_busy), \
                patch('builtins.open', side_effect=fake_open):
            self.assertEqual(get_gpu_stats(), ('55%', 'N/A'))

    def test_nvidia_and_windows_gpu_failure_latches_decouple_pmon_and_query_gpu(
            self):
        import emulator_resource_monitor as erm

        class _SyncThread:

            def __init__(self, target, daemon=True):
                self.target = target

            def start(self):
                self.target()

        def fake_run(cmd, *args, **kwargs):
            if cmd[:2] == ['nvidia-smi', 'pmon']:
                return ''
            if cmd and cmd[0] == 'nvidia-smi':
                return '35, 1200\n'
            return ''

        saved_emu = dict(erm._EMU_GPU_CACHE)
        saved_win = dict(erm._WIN_GPU_CACHE)
        try:
            erm._EMU_GPU_CACHE.update({'time': 0, 'val': {}, 'fetching': False})
            with patch('platform.system', return_value='Linux'), \
                    patch('emulator_resource_monitor.HAS_GPUTIL', False), \
                    patch('threading.Thread', side_effect=lambda target, daemon=True: _SyncThread(target, daemon)), \
                    patch('emulator_resource_monitor.run_cmd', side_effect=fake_run) as mock_run:
                erm.get_emu_gpu_stats({'emulator-5554': 1234})
                erm._EMU_GPU_CACHE['time'] = 0
                erm.get_emu_gpu_stats({'emulator-5554': 1234})
                pmon_calls = sum(
                    1 for c in mock_run.call_args_list
                    if c.args and c.args[0][:2] == ['nvidia-smi', 'pmon'])
                self.assertEqual(pmon_calls, 1)
                # pmon failing must not disable --query-gpu.
                self.assertEqual(erm.get_gpu_stats(), ('35.0%', '1200.0MB'))

            # Once --query-gpu fails, neither --query-gpu nor pmon re-spawns.
            real_exists = os.path.exists
            real_glob = erm.glob.glob
            with patch('platform.system', return_value='Linux'), \
                    patch('emulator_resource_monitor.HAS_GPUTIL', False), \
                    patch('glob.glob',
                          side_effect=lambda pat, *a, **kw: []
                          if str(pat).startswith('/sys/class/drm/') else
                          real_glob(pat, *a, **kw)), \
                    patch('os.path.exists',
                          side_effect=lambda p: False
                          if str(p).startswith('/sys/class/drm/') else
                          real_exists(p)), \
                    patch('threading.Thread', side_effect=lambda target, daemon=True: _SyncThread(target, daemon)), \
                    patch('emulator_resource_monitor.run_cmd', return_value='') as mock_run2:
                erm.get_gpu_stats()
                erm.get_gpu_stats()
                erm._EMU_GPU_CACHE['time'] = 0
                erm.get_emu_gpu_stats({'emulator-5554': 1234})
                smi_calls = sum(1 for c in mock_run2.call_args_list
                                if c.args and c.args[0][0] == 'nvidia-smi')
                self.assertEqual(smi_calls, 1)

            # Windows GPU counter failure latches so powershell is not re-spawned.
            erm._WIN_GPU_CACHE.update({
                'time': 0,
                'val': 'N/A',
                'fetching': False,
                'failed': False,
            })
            with patch('platform.system', return_value='Windows'), \
                    patch('emulator_resource_monitor.HAS_GPUTIL', False), \
                    patch('threading.Thread', side_effect=lambda target, daemon=True: _SyncThread(target, daemon)), \
                    patch('emulator_resource_monitor.run_cmd', return_value='') as mock_win_run:
                erm.get_gpu_stats()
                erm._WIN_GPU_CACHE['time'] = 0
                erm.get_gpu_stats()
                ps_calls = sum(1 for c in mock_win_run.call_args_list
                               if c.args and c.args[0][0] == 'powershell')
                self.assertEqual(ps_calls, 1)
        finally:
            erm._NVIDIA_SMI_FAILED = False
            erm._NVIDIA_PMON_FAILED = False
            erm._LINUX_INTEL_GPU_FAILED = False
            erm._EMU_GPU_CACHE.clear()
            erm._EMU_GPU_CACHE.update(saved_emu)
            erm._WIN_GPU_CACHE.clear()
            erm._WIN_GPU_CACHE.update(saved_win)

    def test_host_and_summary_cli_bounds_and_autosave_skip(self):
        import subprocess
        import emulator_resource_monitor as erm
        real_check_output = subprocess.check_output
        sysctl_timeouts = []

        def fake_check_output(cmd, *args, **kwargs):
            if cmd and cmd[0] == 'sysctl':
                sysctl_timeouts.append(kwargs.get('timeout'))
                return b'Apple M3 Max\n'
            return real_check_output(cmd, *args, **kwargs)

        with patch('platform.system', return_value='Darwin'), \
                patch('platform.processor', return_value='i386'), \
                patch('subprocess.check_output', side_effect=fake_check_output), \
                patch('emulator_resource_monitor.HAS_GPUTIL', False), \
                patch('emulator_resource_monitor.run_cmd', return_value=''):
            hw = erm.get_static_hardware_info()
            self.assertIn('Apple M3 Max', hw)
            self.assertEqual(sysctl_timeouts, [2])

        saved_mac = dict(erm._MAC_GPU_CACHE)
        try:
            erm._MAC_GPU_CACHE.update({
                'time': 0,
                'val': 'N/A',
                'fetching': False,
                'failed': False,
            })

            class _SyncThread:

                def __init__(self, target, daemon=True):
                    self.target = target

                def start(self):
                    self.target()

            with patch('platform.system', return_value='Darwin'), \
                    patch('emulator_resource_monitor.HAS_GPUTIL', False), \
                    patch('threading.Thread', side_effect=lambda target, daemon=True: _SyncThread(target, daemon)), \
                    patch('emulator_resource_monitor.run_cmd', return_value='') as mock_run:
                self.assertEqual(erm.get_gpu_stats(), ('N/A', 'N/A'))
                self.assertTrue(erm._MAC_GPU_CACHE.get('failed'))
                pm_calls = sum(1 for c in mock_run.call_args_list
                               if c.args and c.args[0][0] == 'powermetrics')
                self.assertEqual(pm_calls, 1)
                erm._MAC_GPU_CACHE['time'] = 0
                self.assertEqual(erm.get_gpu_stats(), ('N/A', 'N/A'))
                # Once powermetrics fails, Darwin must not re-spawn it.
                pm_calls_after = sum(
                    1 for c in mock_run.call_args_list
                    if c.args and c.args[0][0] == 'powermetrics')
                self.assertEqual(pm_calls_after, 1)
        finally:
            erm._MAC_GPU_CACHE.clear()
            erm._MAC_GPU_CACHE.update(saved_mac)

        summary = {
            'iterations':
                1,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                1,
            'host_cpu_sum':
                10.0,
            'host_cpu_max':
                10.0,
            'host_ram_iterations':
                1,
            'host_ram_sum':
                50.0,
            'host_ram_max':
                50.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {},
            'time_series': [{
                'time': 1000.0,
                'host_cpu': 10.0,
                'host_ram': 50.0,
            }],
        }
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch('emulator_resource_monitor.run_cmd',
                      return_value='adb: error: failed\n') as mock_adb, \
                patch('sys.stdout', new_callable=io.StringIO):
            # CSV-only autosave must not invoke `adb version` or build text.
            erm.save_summary(summary,
                             1010.0,
                             save_csv=True,
                             save_txt=False,
                             out_dir=tmpdir,
                             is_autosave=True)
            self.assertEqual(mock_adb.call_count, 0)

            # Error output from `adb version` falls back to N/A.
            erm.save_summary(summary,
                             1010.0,
                             save_csv=False,
                             save_txt=True,
                             out_dir=tmpdir,
                             is_autosave=True)
            txt = os.path.join(tmpdir, 'device_monitor_summary_autosave.txt')
            with open(txt, encoding='utf-8') as f:
                self.assertIn('ADB Version:  N/A', f.read())

    def test_paging_and_disk_rates_guard_zero_interval(self):
        import emulator_resource_monitor as erm
        saved_io = erm._LAST_DISK_IO
        saved_time = erm._LAST_DISK_TIME
        try:
            erm._LAST_DISK_IO = erm.DiskIo(0, 0)
            erm._LAST_DISK_TIME = 100.0
            with patch('time.monotonic', return_value=100.0), \
                    patch('emulator_resource_monitor.host_cpu_percent', return_value=10.0), \
                    patch('emulator_resource_monitor.get_process_stats', return_value=({}, [], 'N/A', 'N/A', {}, {})), \
                    patch('emulator_resource_monitor.get_gpu_stats', return_value=('N/A', 'N/A')), \
                    patch('emulator_resource_monitor.get_emu_gpu_stats', return_value={}), \
                    patch('emulator_resource_monitor.read_disk_io', return_value=erm.DiskIo(1024 * 1024, 2 * 1024 * 1024)):
                res = erm.get_host_stats()
            self.assertEqual(res[15], 0.0)
            self.assertEqual(res[16], 0.0)
        finally:
            erm._LAST_DISK_IO = saved_io
            erm._LAST_DISK_TIME = saved_time

        paging_str, in_rate, out_rate, lp_in, lp_out = erm.compute_paging_stats(
            200, 100, 100, 0, 0.0, 'Linux', 1)
        self.assertEqual((in_rate, out_rate), (0.0, 0.0))
        self.assertEqual(paging_str, 'In: 0 / Out: 0 (KB/s)')
        self.assertEqual((lp_in, lp_out), (200, 100))

        paging_str2, in_rate2, out_rate2, _, _ = erm.compute_paging_stats(
            200, 100, 100, 0, 2.0, 'Linux', 1)
        self.assertEqual((in_rate2, out_rate2), (50.0, 50.0))
        self.assertEqual(paging_str2, 'In: 50 / Out: 50 (KB/s)')

    def test_compute_paging_stats_unsupported_counters_return_na_and_nan(self):
        import math
        import emulator_resource_monitor as erm
        for iter_count in (0, 1, 5):
            p_str, in_r, out_r, lp_in, lp_out = erm.compute_paging_stats(
                0, 0, 100, 200, 2.0, 'Windows', iter_count)
            self.assertEqual(p_str, 'N/A')
            self.assertTrue(math.isnan(in_r))
            self.assertTrue(math.isnan(out_r))
            self.assertEqual((lp_in, lp_out), (100, 200))

    def test_csv_export_guards_invalid_epoch_time_and_key_migration_replaces_nan(
            self):
        import emulator_resource_monitor as erm
        summary = {
            'iterations':
                2,
            'start_time':
                100.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                1,
            'host_cpu_sum':
                10.0,
            'host_cpu_max':
                10.0,
            'host_ram_iterations':
                1,
            'host_ram_sum':
                50.0,
            'host_ram_max':
                50.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {
                'emulator-5554': {
                    'name': 'emulator-5554 | Pixel 7',
                    'boot_log': [('+5s', '10%', '40%')],
                },
                'Pixel_7_emulator-5554': {
                    'name': 'Pixel_7 | Pixel 7',
                    'boot_log': [],
                },
            },
            'time_series': [
                {
                    'time': float('nan'),
                    'host_cpu': 10.0,
                    'emu_cpu_tot_emulator-5554': 25.0,
                    'emu_cpu_tot_Pixel_7_emulator-5554': float('nan'),
                },
                {
                    'time': 500.0,
                    'host_cpu': 20.0,
                    'emu_cpu_tot_emulator-5554': 0.0,
                    'emu_cpu_tot_Pixel_7_emulator-5554': 55.0,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch('sys.stdout', new_callable=io.StringIO):
            erm.save_summary(summary,
                             200.0,
                             save_csv=True,
                             save_txt=False,
                             out_dir=tmpdir,
                             is_autosave=True)
            csv_path = os.path.join(tmpdir,
                                    'device_monitor_summary_autosave.csv')
            self.assertTrue(os.path.isfile(csv_path))
            with open(csv_path, encoding='utf-8') as f:
                csv_rows = f.read().strip().splitlines()
            self.assertEqual(len(csv_rows), 3)

        erm._migrate_summary_keys(summary, 'emulator-5554',
                                  'Pixel_7_emulator-5554')
        self.assertEqual(
            summary['time_series'][0]['emu_cpu_tot_Pixel_7_emulator-5554'],
            25.0)
        self.assertEqual(
            summary['time_series'][1]['emu_cpu_tot_Pixel_7_emulator-5554'],
            55.0)
        self.assertEqual(
            summary['emulators']['Pixel_7_emulator-5554']['boot_log'],
            [('+5s', '10%', '40%')])

    def test_pre_adb_host_qemu_metrics_recorded_and_migrated_without_disconnected_card(
            self):
        import emulator_resource_monitor as erm
        tick = [0]
        dashboard_guest_frames = []
        dev_seq = [{}, {}, {}, {'emulator-5554': 'device'}]
        host_seq = [
            _host_tuple({'emulator-5554': 1000}, {},
                        qemu_stats={
                            'emulator-5554':
                                ('400.0%', '2000.0MB (5 thr, 100 cs)')
                        }),
            _host_tuple({'emulator-5554': 1000}, {},
                        qemu_stats={
                            'emulator-5554':
                                ('400.0%', '2000.0MB (5 thr, 100 cs)')
                        }),
            _host_tuple({'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={
                            'emulator-5554':
                                ('350.0%', '2100.0MB (5 thr, 120 cs)')
                        }),
            _host_tuple({'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={
                            'emulator-5554':
                                ('20.0%', '2200.0MB (5 thr, 140 cs)')
                        }),
        ]

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_dashboard(*args, **kwargs):
            dashboard_guest_frames.append(dict(args[4]))

        def fake_sleep(_):
            tick[0] += 1
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=lambda: host_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      return_value=(_guest_model('Pixel 8'), False, '30.0%',
                                    '20.0%', '45.0%', '0.0 MB')), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=fake_dashboard), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary_data = mock_save.call_args[0][0]
        emus = summary_data['emulators']
        self.assertEqual(list(emus.keys()), ['Pixel_8_emulator-5554'])
        emu = emus['Pixel_8_emulator-5554']
        self.assertEqual(emu['qemu_cpu_max'], 400.0)
        self.assertEqual(emu['qemu_cpu_iterations'], 3)
        rows = list(summary_data['time_series'])
        self.assertEqual(rows[0]['qemu_cpu_Pixel_8_emulator-5554'], 400.0)
        self.assertEqual(rows[0]['qemu_cs_Pixel_8_emulator-5554'], 100.0)
        # Pre-ADB ticks (frames 0..2) must not show a bogus [DISCONNECTED] card.
        self.assertEqual(dashboard_guest_frames[:3], [{}, {}, {}])

    def test_migrate_summary_keys_merges_existing_record_and_host_only_bare_key(
            self):
        import emulator_resource_monitor as erm
        summary = {
            'emulators': {
                'Pixel_7_emulator-5554': {
                    'name': 'Pixel_7 | Pixel 7',
                    'current_serial': 'emulator-5554',
                    'current_pid': 1000,
                    'retired': True,
                    'cpu_iterations': 1,
                    'cpu_sum': 10.0,
                    'cpu_max': 10.0,
                    'ram_iterations': 1,
                    'ram_sum': 30.0,
                    'ram_max': 30.0,
                    'swap_iterations': 1,
                    'swap_sum': 5.0,
                    'swap_max': 5.0,
                    'qemu_cpu_iterations': 1,
                    'qemu_cpu_sum': 20.0,
                    'qemu_cpu_max': 20.0,
                    'qemu_ram_iterations': 1,
                    'qemu_ram_sum': 1000.0,
                    'qemu_ram_max': 1000.0,
                    'boot_log': [('+0s', '10%', '30%', '5MB')],
                    'boot_completed': True,
                    'boot_finished_time': 50.0,
                },
                'emulator-5554': {
                    'name': 'emulator-5554 | Pixel 7',
                    'current_serial': 'emulator-5554',
                    'current_pid': 2000,
                    'cpu_iterations': 1,
                    'cpu_sum': 50.0,
                    'cpu_max': 50.0,
                    'ram_iterations': 1,
                    'ram_sum': 60.0,
                    'ram_max': 60.0,
                    'swap_iterations': 1,
                    'swap_sum': 15.0,
                    'swap_max': 15.0,
                    'qemu_cpu_iterations': 1,
                    'qemu_cpu_sum': 80.0,
                    'qemu_cpu_max': 80.0,
                    'qemu_ram_iterations': 1,
                    'qemu_ram_sum': 2000.0,
                    'qemu_ram_max': 2000.0,
                    'boot_log': [('+2s', '50%', '60%', '15MB')],
                    'is_booting': True,
                    'boot_completed': False,
                },
            },
            'time_series': [{
                'time': 100.0,
                'netsim_state_Pixel_7_emulator-5554': '',
                'netsim_state_emulator-5554': 'CONNECTED',
            }],
        }
        erm._migrate_summary_keys(summary, 'emulator-5554',
                                  'Pixel_7_emulator-5554')
        dst = summary['emulators']['Pixel_7_emulator-5554']
        self.assertEqual(dst['cpu_iterations'], 2)
        self.assertEqual(dst['cpu_sum'], 60.0)
        self.assertEqual(dst['cpu_max'], 50.0)
        self.assertEqual(dst['qemu_cpu_iterations'], 2)
        self.assertEqual(dst['qemu_cpu_sum'], 100.0)
        self.assertEqual(dst['qemu_cpu_max'], 80.0)
        self.assertEqual(len(dst['boot_log']), 2)
        self.assertEqual(dst['current_pid'], 2000)
        self.assertFalse(dst.get('retired', False))
        self.assertNotIn('boot_finished_time', dst)
        self.assertEqual(
            summary['time_series'][0]['netsim_state_Pixel_7_emulator-5554'],
            'CONNECTED')

        # Host-only tick 1 records netsim_state_emulator-5554 before adb lists
        # the serial; tick 2 brings both adb and the AVD name at once.
        def setup_group(pid, avd):
            erm._LAST_EMU_GROUP_PIDS['emulator-5554'] = {pid}
            erm._LAST_EMU_GROUP_INFO['emulator-5554'] = {
                'primary_pid': pid,
                'avd_id': avd,
                'avd_name': avd,
                'display_name': avd,
                'pids': {pid},
            }

        host_seq = [
            _host_tuple({'emulator-5554': 3000}, {}),
            _host_tuple({'emulator-5554': 3000}, {'emulator-5554': 'Pixel_8'}),
            _host_tuple({'emulator-5554': 3000}, {'emulator-5554': 'Pixel_8'}),
        ]
        dev_seq = [{}, {}, {'emulator-5554': 'device'}]
        call_i = [0]

        def host_with_group():
            idx = min(call_i[0], len(host_seq) - 1)
            avd = 'Pixel_8' if idx >= 1 else None
            setup_group(3000, avd)
            call_i[0] += 1
            return host_seq[idx]

        dev_i = [0]

        def devs_by_tick():
            res = dev_seq[min(dev_i[0], len(dev_seq) - 1)]
            dev_i[0] += 1
            return res

        with patch('emulator_resource_monitor.get_devices',
                   side_effect=devs_by_tick), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=host_with_group), \
                patch('emulator_resource_monitor.get_guest_stats',
                      return_value=(_guest_model('Pixel 8'), False, '20%',
                                    '10%', '40.0%', '0.0 MB')), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.sleep',
                      side_effect=[None, None, KeyboardInterrupt()]), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--headless', '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()
        ts = list(mock_save.call_args[0][0]['time_series'])
        self.assertTrue(all('netsim_state_emulator-5554' not in r for r in ts),
                        ts)
        self.assertIn('netsim_state_Pixel_8_emulator-5554', ts[0])

    def test_get_process_stats_requests_ppid_in_process_iter(self):
        import emulator_resource_monitor as erm
        requested_attrs = []

        def fake_iter(attrs=None):
            requested_attrs.extend(attrs or ())
            return []

        with patch('psutil.process_iter', side_effect=fake_iter):
            erm.get_process_stats()
        self.assertIn('ppid', requested_attrs)


class TestNetsimTracker(unittest.TestCase):

    def setUp(self):
        # Tests assign the monitor's per-tick globals; restore them afterwards.
        import emulator_resource_monitor as erm
        for name in ('_LAST_NETSIMD_PROCS', '_LAST_EMU_GROUP_PIDS',
                     '_LAST_EMU_GROUP_INFO'):
            patcher = patch.object(erm, name, getattr(erm, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_parse_proc_locks_double_spaces(self):
        from emulator_resource_monitor import parse_proc_locks
        raw = ("1: POSIX  ADVISORY  WRITE 100 00:1a:1234 0 EOF\n"
               "60: FLOCK  ADVISORY  WRITE 1859832 00:2b:99248 0 EOF\n"
               "61: FLOCK  ADVISORY  READ  1859900 00:2b:99249 0 EOF\n")
        holders = parse_proc_locks(raw)
        self.assertEqual(holders, {'00:2b:99248': 1859832})

    def test_parse_proc_locks_ignores_blocked_waiters(self):
        # "->" lines are requests waiting for the lock, not holders.
        from emulator_resource_monitor import parse_proc_locks
        raw = ("60: FLOCK  ADVISORY  WRITE 100 00:2b:99248 0 EOF\n"
               "60: -> FLOCK  ADVISORY  WRITE 200 00:2b:99248 0 EOF\n")
        self.assertEqual(parse_proc_locks(raw), {'00:2b:99248': 100})

    def test_sanitize_netsim_filename_replaces_invalid_chars(self):
        # netsimd maps every character other than alphanumerics, '-' and '_'
        # to '_' (capture-actor/src/service.rs), as the Rust monitor does.
        from emulator_resource_monitor import sanitize_netsim_filename
        self.assertEqual(sanitize_netsim_filename('Pixel 8 Pro!'),
                         'Pixel_8_Pro_')

    def test_parse_netsim_ini_skips_token(self):
        from emulator_resource_monitor import parse_netsim_ini
        with tempfile.TemporaryDirectory() as tmpdir:
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            with open(ini_path, 'w', encoding='utf-8') as f:
                f.write(
                    "grpc.port=43929\nhci.port=6402\npid=1859832\ngrpc.token=secret123\n"
                )
            parsed = parse_netsim_ini(ini_path)
            self.assertEqual(parsed, {
                'grpc.port': 43929,
                'hci.port': 6402,
                'pid': 1859832,
            })
            self.assertNotIn('grpc.token', parsed)

    def test_log_tailer_1024_and_1025_and_truncation(self):
        import calendar
        from emulator_resource_monitor import (NetsimLogTailer,
                                               parse_netsim_log_timestamp)
        # Year rollover: log line from Dec 31 when now_utc is Jan 1 2026
        jan1_2026 = calendar.timegm((2026, 1, 1, 0, 5, 0))
        lvl, ts, msg = parse_netsim_log_timestamp(
            "netsim I 12-31 23:59:50.500 netsimd.rs:100 - Wrote to INI file /tmp/netsim.ini",
            now_utc=jan1_2026,
        )
        self.assertEqual(lvl, 'I')
        self.assertAlmostEqual(ts,
                               calendar.timegm(
                                   (2025, 12, 31, 23, 59, 50)) + 0.5,
                               places=2)
        self.assertIn("Wrote to INI file", msg)

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, 'netsim_stderr.log')
            now_utc = calendar.timegm((2026, 9, 25, 22, 38, 0))
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join([
                    'netsim I 09-25 22:37:05.610 netsimd.rs:451 - netsim artifacts path: "/tmp/erm_qn/android-user/netsimd"',
                    'Args {',
                    '    no_cli_ui: true,',
                    '    pcap: true,',
                    '}',
                    'netsim I 09-25 22:37:05.620 netsimd.rs:456 - Netsim Version: 1.0.24, OS: linux, Arch: x86_64',
                    'netsim I 09-25 22:37:05.680 netsimd.rs:750 - gRPC port: 43929',
                    'netsim I 09-25 22:37:05.683 netsimd.rs:757 - Wrote to INI file /tmp/erm_qn/netsim.ini',
                    'netsim I 09-25 22:37:06.552 netsimd.rs:106 - Handling new connection for "", device ERM QN Phone 37',
                    'netsim I 09-25 22:37:06.552 service.rs:671 - DeviceActor: AddChipByGuid for device ipv6:%5B::1%5D:59604',
                    'netsim I 09-25 22:37:06.552 service.rs:642 - DeviceActor: Create device ERM QN Phone 37',
                    'netsim I 09-25 22:37:06.552 service.rs:579 - DeviceActor: AddChip ERM QN Phone 37 (EMULATOR, ERM QN Phone 37) to device ERM QN Phone 37',
                    'netsim I 09-25 22:37:41.889 ap_actor.rs:120 - ApActor: Associated 02:15:B2:00:00:00',
                    'netsim I 09-25 22:37:50.000 netsimd.rs:800 - Idle timeout reached',
                ]) + '\n')

            tailer = NetsimLogTailer(log_path)
            self.assertTrue(tailer.poll(now_utc=now_utc))
            self.assertEqual(tailer.version, '1.0.24')
            self.assertEqual(tailer.grpc_port_logged, 43929)
            self.assertTrue(tailer.frontend_disabled)
            self.assertTrue(tailer.pcap_flag)
            self.assertIsNotNone(tailer.ready_ts)
            self.assertEqual(len(tailer.chip_adds), 1)
            self.assertEqual(tailer.chip_adds[0]['kind'], '?')
            self.assertEqual(tailer.chip_adds[0]['peer_port'], 59604)
            self.assertEqual(len(tailer.wifi_associations), 1)
            self.assertEqual(tailer.exit_reason_hint, 'idle_timeout')

            # Truncate file to simulate second daemon start with HEAD 1.0.25 log format
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join([
                    'netsim I 09-25 22:38:00.100 netsimd.rs:456 - Netsim Version: 1.0.25, OS: linux, Arch: x86_64',
                    'netsim I 09-25 22:38:01.000 service.rs:585 - DeviceActor: AddChip 10 (kind WIFI, name "wlan0") to device 1 (Pixel 9)',
                    'netsim I 09-25 22:38:05.000 service.rs:456 - DeviceActor: Removed chip 10 (kind WIFI) from device 1',
                ]) + '\n')

            self.assertTrue(tailer.poll(now_utc=now_utc))
            self.assertEqual(tailer.truncation_count, 1)
            self.assertEqual(tailer.version, '1.0.25')
            self.assertEqual(len(tailer.chip_adds), 1)
            self.assertEqual(tailer.chip_adds[0]['kind'], 'WIFI')
            self.assertEqual(tailer.chip_adds[0]['chip_id'], '10')
            self.assertEqual(len(tailer.chip_removals), 1)

    def test_log_tailer_holds_partial_line_and_detects_replaced_file(self):
        import calendar
        from emulator_resource_monitor import NetsimLogTailer
        now_utc = calendar.timegm((2026, 9, 25, 22, 38, 0))
        add_chip = ('netsim I 09-25 22:37:06.000 service.rs:585 - '
                    'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n')
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, 'netsim_stderr.log')
            # netsimd is mid-write: the AddChip line has no newline yet.
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(add_chip[:40])
            tailer = NetsimLogTailer(log_path)
            self.assertTrue(tailer.poll(now_utc=now_utc))
            self.assertEqual(tailer.chip_adds, [])
            self.assertEqual(tailer.offset, 0)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(add_chip[40:])
            self.assertTrue(tailer.poll(now_utc=now_utc))
            self.assertEqual([a['device_name'] for a in tailer.chip_adds],
                             ['Phone'])

            # netsimd restarts: the log is unlinked and recreated, and the new
            # file is already longer than the old offset.
            os.remove(log_path)
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(add_chip.replace('Phone', 'Glasses') * 2)
            self.assertTrue(tailer.poll(now_utc=now_utc))
            self.assertEqual(tailer.truncation_count, 1)
            # Bare CR or NEL inside a line does not split it, CRLF strips one CR,
            # and a trailing fragment containing CR is still held back.
            cr_line = ('netsim I 09-25 22:37:07.000 service.rs:585 - step\x85\r'
                       'DeviceActor: AddChip 2 (kind WIFI) to device 2 '
                       '(Watch)\r\n').encode('utf-8')
            with open(log_path, 'ab') as f:
                f.write(cr_line + b'part a\rpart b')
            expected_off = tailer.offset + len(cr_line)
            self.assertTrue(tailer.poll(now_utc=now_utc))
            self.assertEqual(tailer.offset, expected_off)
            self.assertEqual([a['device_name'] for a in tailer.chip_adds],
                             ['Glasses', 'Glasses', 'Watch'])

    def test_resolve_session_stats_chips_exact_and_approx(self):
        import json
        from emulator_resource_monitor import resolve_session_stats_chips
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_path = os.path.join(tmpdir, 'netsim_session_stats.json')
            payload = {
                'duration_secs':
                    '130',
                'device_stats': [{
                    'device_id': 1,
                    'kind': 'EMULATOR'
                }],
                'radio_stats': [
                    {
                        'device_id': 1,
                        'kind': 'WIFI',
                        'duration_secs': '129'
                    },
                    {
                        'device_id': 1,
                        'kind': 'UWB',
                        'duration_secs': '129'
                    },
                    {
                        'device_id': 1,
                        'kind': 'BLUETOOTH_LOW_ENERGY',
                        'duration_secs': '118'
                    },
                    {
                        'device_id': 1,
                        'kind': 'BLUETOOTH_CLASSIC',
                        'duration_secs': '115'
                    },
                ]
            }
            with open(stats_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
            os.utime(stats_path, (2000.0, 2000.0))

            # WIFI est = 1871.0, UWB est = 1871.0, BT est = 1882.0.
            # Provide exact log events for UWB/WIFI (1871.5, 1871.6) and a far event for BT.
            num_devs, resolved, cache = resolve_session_stats_chips(
                stats_path, [1871.5, 1871.6], joined_emulator_count=1)
            self.assertEqual(num_devs, 1)
            self.assertEqual(set(resolved.keys()), {'BT', 'UWB', 'WIFI'})
            self.assertEqual(resolved['UWB'], (1871.5, False))
            self.assertEqual(resolved['WIFI'], (1871.6, False))
            self.assertEqual(resolved['BT'], (1882.0, True))

            # Multi-emulator ambiguity: never guess when joined_emulator_count > 1
            _, resolved_multi, _ = resolve_session_stats_chips(
                stats_path, [1871.5],
                joined_emulator_count=2,
                cached_state=cache)
            self.assertIsNone(resolved_multi)

            # A daemon that started after the file was written ignores it: an
            # earlier session left it in the same artifact dir.
            num_devs, resolved_stale, stale_cache = resolve_session_stats_chips(
                stats_path, [1871.5, 1871.6],
                joined_emulator_count=1,
                not_before=2001.0)
            self.assertEqual((num_devs, resolved_stale, stale_cache),
                             (0, None, None))

    def test_resolve_session_stats_chips_skips_unspecified_kind(self):
        import json
        from emulator_resource_monitor import resolve_session_stats_chips
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_path = os.path.join(tmpdir, 'netsim_session_stats.json')
            with open(stats_path, 'w', encoding='utf-8') as f:
                json.dump(
                    {
                        'device_stats': [{
                            'device_id': 1,
                            'kind': 'EMULATOR'
                        }],
                        'radio_stats': [
                            {
                                'device_id': 1,
                                'kind': 'UNSPECIFIED',
                                'duration_secs': '129'
                            },
                            {
                                'device_id': 1,
                                'kind': '?',
                                'duration_secs': '129'
                            },
                            {
                                'device_id': 1,
                                'kind': '   ',
                                'duration_secs': '129'
                            },
                            {
                                'device_id': 1,
                                'kind': 'WIFI',
                                'duration_secs': '129'
                            },
                        ],
                    }, f)
            os.utime(stats_path, (2000.0, 2000.0))
            num_devs, resolved, _ = resolve_session_stats_chips(
                stats_path, [1871.42], joined_emulator_count=1)
            self.assertEqual(num_devs, 1)
            self.assertEqual(resolved, {'WIFI': (1871.42, False)})

    def test_capture_list_filter_simulation_and_pcap_manager(self):
        from emulator_resource_monitor import (NetsimPcapManager,
                                               parse_netsim_capture_list,
                                               select_safe_capture_patterns)
        cli_out = (
            "ID | Device Name   | Chip Kind | State | Timestamp | Records | Size (bytes)\n"
            "1  | Pixel_8       | WIFI      | off   | 12:00:00  | 0       | 0\n"
            "2  | Pixel_8_Pro   | WIFI      | off   | 12:00:00  | 0       | 0\n"
        )
        rows, err = parse_netsim_capture_list(cli_out)
        self.assertIsNone(err)
        self.assertEqual(len(rows), 2)
        # Pattern ['Pixel_8'] alone would match both Pixel_8 and Pixel_8_Pro
        self.assertIsNone(select_safe_capture_patterns(rows, 'Pixel_8'))
        # Disambiguated with target_row (id + device_name + chip_kind)
        safe_pats = select_safe_capture_patterns(rows,
                                                 'Pixel_8',
                                                 target_row=rows[0])
        self.assertEqual(safe_pats, ['1', 'Pixel_8', 'WIFI'])

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'art')
            pcaps_dir = os.path.join(art_dir, 'pcaps')
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(pcaps_dir, exist_ok=True)
            pcap_file = os.path.join(pcaps_dir, 'netsim-1-Pixel_8-WIFI.pcap')
            with open(pcap_file, 'wb') as f:
                f.write(b'\xd4\xc3\xb2\xa1' + b'\x00' * 100)
            zip_file = os.path.join(art_dir, 'netsim_artifacts_123.zip')
            with open(zip_file, 'wb') as f:
                f.write(b'PK\x03\x04' + b'\x00' * 50)

            mgr = NetsimPcapManager(pcap_dir='', max_mb=1, out_dir=out_dir)
            cli_calls = []

            def fake_cli(netsim_bin, grpc_port, args, timeout=5.0, **_):
                cli_calls.append(list(args))
                if args[:2] == ['capture', 'list']:
                    return cli_out
                return "OK"

            mgr._run_netsim_cli = fake_cli
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            with open(ini_path, 'w', encoding='utf-8') as f:
                f.write('grpc.port=43929\npid=9000\n')
            daemon_info = {
                'daemon_key': (9000, 1000.0),
                'pid':
                    9000,
                'start_wall':
                    1000.0,
                'grpc_port':
                    43929,
                'ini_path':
                    ini_path,
                'artifact_dir':
                    art_dir,
                'same_user':
                    True,
                'exited':
                    False,
                'joined_emulators': [{
                    'serial': 'emulator-5554',
                    'device_name': 'Pixel_8',
                    'chip_ids': {'1'},
                }],
            }
            mgr.poll_and_manage_daemon(daemon_info,
                                       daemon_info['joined_emulators'],
                                       now_mono=100.0)
            # Never invokes `capture get`
            for call in cli_calls:
                self.assertNotEqual(call[:2], ['capture', 'get'])
            self.assertIn(['capture', 'patch', 'on', '1', 'Pixel_8', 'WIFI'],
                          cli_calls)
            expected_copy = os.path.join(out_dir, 'netsim_pcaps', '9000-1000',
                                         'emulator-5554',
                                         'netsim-1-Pixel_8-WIFI.pcap')
            self.assertTrue(os.path.isfile(expected_copy))
            expected_zip = os.path.join(out_dir, 'netsim_pcaps', '9000-1000',
                                        'netsim_artifacts_123.zip')
            self.assertTrue(os.path.isfile(expected_zip))

            mgr.cleanup({(9000, 1000.0): daemon_info})
            self.assertIn(['capture', 'patch', 'off', '1', 'Pixel_8', 'WIFI'],
                          cli_calls)

    def _harvest_fixture(self, tmpdir):
        """An artifact dir with one real pcap, one real zip, and symlinked
        look-alikes pointing at a secret file outside the artifact dir."""
        from emulator_resource_monitor import NetsimPcapManager
        art_dir = os.path.join(tmpdir, 'art')
        pcaps_dir = os.path.join(art_dir, 'pcaps')
        out_dir = os.path.join(tmpdir, 'out')
        os.makedirs(pcaps_dir, exist_ok=True)
        secret = os.path.join(tmpdir, 'secret.txt')
        with open(secret, 'wb') as f:
            f.write(b'SECRET-DATA')
        with open(os.path.join(pcaps_dir, 'netsim-1-Pixel_8-WIFI.pcap'),
                  'wb') as f:
            f.write(b'\xd4\xc3\xb2\xa1' + b'\x00' * 16)
        with open(os.path.join(art_dir, 'netsim_artifacts_1.zip'), 'wb') as f:
            f.write(b'PK\x03\x04' + b'\x00' * 16)
        _try_symlink(secret, os.path.join(pcaps_dir, 'netsim-2-Evil-BT.pcap'))
        _try_symlink(secret, os.path.join(art_dir, 'netsim_artifacts_2.zip'))
        ini_path = os.path.join(tmpdir, 'netsim.ini')
        with open(ini_path, 'w', encoding='utf-8') as f:
            f.write('grpc.port=43929\npid=9001\n')
        mgr = NetsimPcapManager(pcap_dir='', max_mb=1, out_dir=out_dir)
        daemon_info = {
            'daemon_key': (9001, 1000.0),
            'pid': 9001,
            'start_wall': 1000.0,
            'ini_path': ini_path,
            'artifact_dir': art_dir,
            'same_user': True,
        }
        return mgr, daemon_info, out_dir, secret

    def _copied_paths(self, out_dir):
        found = []
        for root, _, files in os.walk(out_dir):
            for f in files:
                found.append(os.path.join(root, f))
        return found

    @unittest.skipUnless(os.name == 'posix', 'uses symlinks')
    def test_harvest_never_follows_symlinks_in_artifact_dir(self):
        # A symlink planted in the daemon's artifact dir must not turn the
        # copy into a read of an arbitrary file; real files still copy.
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, daemon_info, out_dir, _ = self._harvest_fixture(tmpdir)
            joined = [{
                'serial': 'emulator-5554',
                'device_name': 'Pixel_8',
                'chip_ids': {'1'}
            }]
            mgr.harvest_daemon_files(daemon_info, joined)
            names = sorted(
                os.path.basename(p) for p in self._copied_paths(out_dir))
            self.assertEqual(
                names, ['netsim-1-Pixel_8-WIFI.pcap', 'netsim_artifacts_1.zip'])
            for p in self._copied_paths(out_dir):
                with open(p, 'rb') as f:
                    self.assertNotIn(b'SECRET', f.read())
            # The live counters still describe what is on disk: the planted
            # link counts as itself, not as the file it points at.
            self.assertEqual(daemon_info['pcap_live_files'], 2)
            link = os.path.join(daemon_info['artifact_dir'], 'pcaps',
                                'netsim-2-Evil-BT.pcap')
            self.assertEqual(daemon_info['pcap_live_bytes'],
                             20 + os.lstat(link).st_size)

    @unittest.skipUnless(os.name == 'posix', 'uses symlinks')
    def test_harvest_rescues_same_named_zip_from_restarted_daemon(self):
        # A restarted daemon reuses netsim_artifacts_1.zip; its zip is a new
        # file and must be harvested into the new daemon's directory too.
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, first, out_dir, _ = self._harvest_fixture(tmpdir)
            joined = [{
                'serial': 'emulator-5554',
                'device_name': 'Pixel_8',
                'chip_ids': {'1'}
            }]
            mgr.harvest_daemon_files(first, joined)
            with open(
                    os.path.join(first['artifact_dir'],
                                 'netsim_artifacts_1.zip'), 'wb') as f:
                f.write(b'PK\x03\x04' + b'\x01' * 16)
            second = dict(first,
                          daemon_key=(9002, 2000.0),
                          pid=9002,
                          start_wall=2000.0)
            mgr.harvest_daemon_files(second, joined)
            zips = [
                p for p in self._copied_paths(out_dir)
                if p.endswith('netsim_artifacts_1.zip')
            ]
            self.assertEqual(len(zips), 2)

    def test_harvest_counts_current_bytes_of_a_pcap_that_shrank(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, daemon_info, _, _ = self._harvest_fixture(tmpdir)
            pcap = os.path.join(daemon_info['artifact_dir'], 'pcaps',
                                'netsim-1-Pixel_8-WIFI.pcap')
            zip_bytes = os.path.getsize(
                os.path.join(daemon_info['artifact_dir'],
                             'netsim_artifacts_1.zip'))

            def set_pcap(size, mtime):
                with open(pcap, 'wb') as f:
                    f.write(b'\xd4' * size)
                os.utime(pcap, (mtime, mtime))

            set_pcap(800 * 1024, 1000.0)
            mgr.harvest_daemon_files(daemon_info, [])
            # Capture restarts: the file shrinks, then grows again within 1 MB.
            set_pcap(100 * 1024, 1010.0)
            mgr.harvest_daemon_files(daemon_info, [])
            set_pcap(500 * 1024, 1020.0)
            mgr.harvest_daemon_files(daemon_info, [])
            self.assertFalse(mgr.cap_reached)
            self.assertEqual(mgr.total_copied_bytes, 500 * 1024 + zip_bytes)

    def test_harvest_sends_pcap_of_a_shared_device_name_to_shared(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, daemon_info, _, _ = self._harvest_fixture(tmpdir)
            joined = [
                {
                    'serial': 'emulator-5554',
                    'device_name': 'Pixel 8',
                    'state': 'CONNECTED',
                    'chip_ids': set(),
                },
                {
                    'serial': 'emulator-5556',
                    'device_name': 'Pixel 8',
                    'state': 'CONNECTED',
                    'chip_ids': set(),
                },
            ]
            mgr.harvest_daemon_files(daemon_info, joined)
            pcap_keys = [k for k in mgr.copied_files if k.endswith('.pcap')]
            self.assertEqual(len(pcap_keys), 1)
            self.assertIn(os.sep + 'shared' + os.sep, pcap_keys[0])

    @unittest.skipUnless(hasattr(os, 'O_NONBLOCK'), 'needs O_NONBLOCK')
    def test_copy_regular_file_opens_source_nonblocking(self):
        # A FIFO swapped in after the lstat check must not block the open.
        import emulator_resource_monitor as erm
        real_open = os.open
        flags_seen = []

        def spy_open(path, flags, *args, **kwargs):
            flags_seen.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, 'src.bin')
            with open(src, 'wb') as f:
                f.write(b'abc')
            with patch('os.open', side_effect=spy_open):
                st = erm._copy_regular_file(src, os.path.join(tmpdir, 'dst'))
        self.assertEqual(st.st_size, 3)
        self.assertTrue(flags_seen[0] & os.O_NONBLOCK)

    @unittest.skipUnless(os.name == 'posix', 'uses symlinks')
    def test_copy_regular_file_refuses_symlink_and_copies_plain_file(self):
        import emulator_resource_monitor as erm
        with tempfile.TemporaryDirectory() as tmpdir:
            plain = os.path.join(tmpdir, 'plain.bin')
            with open(plain, 'wb') as f:
                f.write(b'abc')
            link = os.path.join(tmpdir, 'link.bin')
            os.symlink(plain, link)
            self.assertIsNone(
                erm._copy_regular_file(link, os.path.join(tmpdir, 'o1')))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, 'o1')))
            self.assertIsNone(
                erm._copy_regular_file(tmpdir, os.path.join(tmpdir, 'o2')))
            st = erm._copy_regular_file(plain, os.path.join(tmpdir, 'o3'))
            self.assertEqual(st.st_size, 3)
            with open(os.path.join(tmpdir, 'o3'), 'rb') as f:
                self.assertEqual(f.read(), b'abc')

    def test_harvest_skips_daemons_of_other_users(self):
        # Another user's daemon can point ANDROID_TMP anywhere; the monitor
        # only reads pcap sizes and never copies from its artifact dir.
        with tempfile.TemporaryDirectory() as tmpdir:
            from emulator_resource_monitor import NetsimPcapManager
            art_dir = os.path.join(tmpdir, 'art')
            pcaps_dir = os.path.join(art_dir, 'pcaps')
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(pcaps_dir)
            with open(os.path.join(pcaps_dir, 'netsim-1-P-WIFI.pcap'),
                      'wb') as f:
                f.write(b'x' * 8)
            mgr = NetsimPcapManager(pcap_dir='', max_mb=1, out_dir=out_dir)
            daemon_info = {
                'daemon_key': (9002, 1000.0),
                'pid': 9002,
                'start_wall': 1000.0,
                'artifact_dir': art_dir,
                'same_user': False,
            }
            mgr.harvest_daemon_files(daemon_info, [])
            self.assertEqual(daemon_info['pcap_live_files'], 1)
            self.assertEqual(daemon_info['pcap_live_bytes'], 8)
            self.assertEqual(self._copied_paths(out_dir), [])
            self.assertEqual(mgr.total_copied_bytes, 0)

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX ownership')
    def test_harvest_skips_artifact_dir_owned_by_another_user(self):
        import emulator_resource_monitor as erm
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, daemon_info, out_dir, _ = self._harvest_fixture(tmpdir)
            with patch.object(erm, '_current_uid',
                              return_value=os.getuid() + 1):
                mgr.harvest_daemon_files(daemon_info, [])
            self.assertEqual(self._copied_paths(out_dir), [])

    def test_is_same_user(self):
        import emulator_resource_monitor as erm
        with patch.object(erm, '_current_uid', return_value=1000):
            self.assertTrue(erm._is_same_user(1000))
            self.assertFalse(erm._is_same_user(1001))
            self.assertFalse(erm._is_same_user(None))
        # Windows has no uids; every daemon is the monitor's own there.
        with patch.object(erm, '_current_uid', return_value=None):
            self.assertTrue(erm._is_same_user(None))

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX permissions')
    def test_resolve_netsim_cli_bin_only_adopts_trusted_sibling(self):
        # The daemon's exe dir comes from another process; only a same-user
        # daemon may nominate a sibling, and only one nobody else can replace.
        import emulator_resource_monitor as erm
        with tempfile.TemporaryDirectory() as tmpdir:
            sdk = os.path.join(tmpdir, 'sdk')
            os.makedirs(sdk)
            os.chmod(sdk, 0o755)
            daemon_exe = os.path.join(sdk, 'netsimd')
            cli = os.path.join(sdk, 'netsim')
            for p in (daemon_exe, cli):
                with open(p, 'wb') as f:
                    f.write(b'#!/bin/sh\n')
                os.chmod(p, 0o755)

            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True), cli)
            # Foreign daemon: never trust its neighbourhood.
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, False),
                             'netsim')
            self.assertEqual(erm.resolve_netsim_cli_bin('', True), 'netsim')
            # A relative daemon path (argv[0]) is relative to the daemon's
            # working directory, so a file found from here is no sibling.
            self.assertEqual(
                erm.resolve_netsim_cli_bin(os.path.relpath(daemon_exe), True),
                'netsim')
            # Group- or world-writable binary.
            os.chmod(cli, 0o775)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                             'netsim')
            os.chmod(cli, 0o755)
            # World-writable directory (anyone could swap the file).
            os.chmod(sdk, 0o1777)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                             'netsim')
            os.chmod(sdk, 0o755)
            # Owned by some other user.
            with patch.object(erm, '_current_uid',
                              return_value=os.getuid() + 1):
                self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                                 'netsim')
            # A directory above the sibling's own counts too: when others, or
            # the user's group, can write to it without the sticky bit, they
            # can swap `sdk` itself.
            open_dir = os.path.join(tmpdir, 'open')
            open_sdk = os.path.join(open_dir, 'sdk')
            os.makedirs(open_sdk)
            os.chmod(open_sdk, 0o755)
            open_daemon = os.path.join(open_sdk, 'netsimd')
            open_cli = os.path.join(open_sdk, 'netsim')
            for p in (open_daemon, open_cli):
                with open(p, 'wb') as f:
                    f.write(b'#!/bin/sh\n')
                os.chmod(p, 0o755)
            for mode in (0o777, 0o775):
                os.chmod(open_dir, mode)
                self.assertEqual(erm.resolve_netsim_cli_bin(open_daemon, True),
                                 'netsim', oct(mode))
            for mode in (0o755, 0o1777):
                os.chmod(open_dir, mode)
                self.assertEqual(erm.resolve_netsim_cli_bin(open_daemon, True),
                                 open_cli, oct(mode))
            # A symlink named netsim is judged by its target, including the
            # directories on the way to it.
            os.remove(cli)
            os.symlink(open_cli, cli)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True), cli)
            # Only a link the user or root made is followed.
            link_paths = (cli, os.path.join(os.path.realpath(sdk), 'netsim'))
            real_lstat = os.lstat

            def lstat_foreign_link(path, *args, **kwargs):
                st = real_lstat(path, *args, **kwargs)
                if path not in link_paths:
                    return st
                vals = list(st)
                vals[4] = os.getuid() + 1
                return os.stat_result(vals)

            with patch('os.lstat', side_effect=lstat_foreign_link):
                self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                                 'netsim')
            os.chmod(open_dir, 0o777)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                             'netsim')
            # A link to a trusted file next to it is fine.
            os.remove(cli)
            real = os.path.join(sdk, 'netsim-real')
            with open(real, 'wb') as f:
                f.write(b'#!/bin/sh\n')
            os.chmod(real, 0o755)
            os.symlink(real, cli)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True), cli)
            # A target spelled as a directory is one the kernel cannot run.
            os.remove(cli)
            os.symlink('netsim-real' + os.sep, cli)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                             'netsim')
            # Missing sibling falls back to PATH.
            os.remove(cli)
            os.remove(real)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                             'netsim')

    def test_netsim_tracker_standalone_and_emulator_lifecycle(self):
        import emulator_resource_monitor as erm
        # Clock and PIDs as in the other netsim tests: the log lines below are
        # dated after the daemon and the emulator start, and no /proc lookup
        # reaches a real process.
        daemon_start = float(_START - 5)
        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            log_path = os.path.join(art_dir, 'netsim_stderr.log')
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            with open(ini_path, 'w', encoding='utf-8') as f:
                f.write(f"grpc.port=43929\npid={_DAEMON_A}\n")
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    'netsim I 09-25 22:37:05.683 netsimd.rs:757 - Wrote to INI file '
                    + ini_path + '\n')

            erm._LAST_NETSIMD_PROCS = [{
                'pid': _DAEMON_A,
                'create_time': daemon_start,
                'ppid': 1,
                'cmdline': ['/sdk/emulator/bin/netsimd', '--no-web-ui'],
                'exe': '/sdk/emulator/bin/netsimd',
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                },
            }]
            erm._LAST_EMU_GROUP_PIDS = {}
            erm._LAST_EMU_GROUP_INFO = {}

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            with patch.object(tracker,
                              '_discover_ini_files',
                              return_value=[ini_path]), \
                    patch.object(tracker,
                                 '_read_proc_locks',
                                 return_value={}):
                tracker.update({}, {}, now_wall=_START + 0.0, now_mono=10.0)
                # After 30s with 0 devices, daemon transitions to ORPHANED
                tracker.update({}, {}, now_wall=_START + 35.0, now_mono=45.0)
                d = tracker.daemons[(_DAEMON_A, daemon_start)]
                self.assertEqual(d['spawner'], 'STANDALONE')
                self.assertTrue(d['orphaned'])

                # Now attach an emulator and verify CONNECTED state + CSV/summary
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(
                        'netsim I 09-25 22:37:10.000 service.rs:585 - '
                        'DeviceActor: AddChip 1 (kind WIFI) to device 1 (ERM QN Phone 37)\n'
                    )
                erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': [_WORKER_A]}
                erm._LAST_EMU_GROUP_INFO = {
                    'emulator-5554': {
                        'primary_pid': _WORKER_A,
                        'primary_create_time': float(_START + 1),
                        'avd_id': 'erm_qn_phone37',
                        'display_name': 'ERM QN Phone 37',
                        'grpc_endpoint': 'localhost:43929',
                        'no_netsim': False,
                    }
                }
                emulators = {
                    'erm_qn_phone37_emulator-5554': {
                        'name': 'erm_qn_phone37 | ERM QN Phone 37',
                        'current_serial': 'emulator-5554',
                        'current_pid': _WORKER_A,
                        'first_seen': 12.0,
                    }
                }
                tracker.update(emulators, {'emulator-5554': 'device'},
                               now_wall=_START + 45.0,
                               now_mono=55.0)
                rec = tracker.emulator_records['erm_qn_phone37_emulator-5554']
                self.assertEqual(rec['state'], 'CONNECTED')
                self.assertIn('WIFI', rec['chip_attach_s'])
                csv_fields = tracker.get_csv_fields()
                self.assertEqual(csv_fields['netsim_up'], 1)
                self.assertEqual(
                    csv_fields['netsim_state_erm_qn_phone37_emulator-5554'],
                    'CONNECTED')
                summary_lines = '\n'.join(
                    tracker.format_summary_sections(now_mono=55.0))
                self.assertIn('Netsim daemon lifecycle:', summary_lines)
                self.assertIn('Netsim attach:', summary_lines)
                self.assertIn('Netsim pcap manifest:', summary_lines)

    def test_netsim_tracker_unreadable_env_uses_monitor_env(self):
        import emulator_resource_monitor as erm
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(os.environ, {'TMPDIR': tmpdir, 'USER': 'user'}):
            os.environ.pop('ANDROID_TMP', None)
            erm._LAST_NETSIMD_PROCS = [{
                'pid': 9600,
                'create_time': 1000.0,
                'ppid': 1,
                'cmdline': ['/sdk/emulator/bin/netsimd'],
                'exe': '/sdk/emulator/bin/netsimd',
                'environ': {},
            }]
            erm._LAST_EMU_GROUP_PIDS = {}
            erm._LAST_EMU_GROUP_INFO = {}
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [tmpdir]
            with patch.object(tracker, '_discover_ini_files', return_value=[]), \
                    patch.object(tracker, '_read_proc_locks', return_value={}):
                tracker.update({}, {}, now_wall=1005.0, now_mono=10.0)
            d = tracker.daemons[(9600, 1000.0)]
            self.assertEqual(
                d['artifact_dir'],
                erm.resolve_netsim_artifact_dir({
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                }))

    def test_pcap_manager_throttles_per_daemon_and_after_ambiguous_skip(self):
        from emulator_resource_monitor import NetsimPcapManager
        with tempfile.TemporaryDirectory() as tmp1, \
                tempfile.TemporaryDirectory() as tmp2:
            mgr, first, _, _ = self._harvest_fixture(tmp1)
            _, second, _, _ = self._harvest_fixture(tmp2)
            second['daemon_key'] = (9002, 1000.0)
            second['pid'] = 9002
            second['grpc_port'] = 43930

            # Polling first daemon at t=100 must not prevent second daemon from
            # harvesting on the same tick.
            mgr.poll_and_manage_daemon(first, [], now_mono=100.0)
            mgr.poll_and_manage_daemon(second, [], now_mono=100.0)
            self.assertGreater(first.get('pcap_live_bytes', 0), 0)
            self.assertGreater(second.get('pcap_live_bytes', 0), 0)

            # Ambiguous CLI substring match on second daemon must not cause CLI
            # calls on every subsequent tick within the 10s interval.
            cli_calls = [0]

            def fake_cli(_bin, _port, _args, timeout=5.0, **_):
                cli_calls[0] += 1
                return (
                    'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                    '1  | Phone       | WIFI      | off   | 12:00:00  | 0       | 0\n'
                    '10 | Phone 2     | WIFI      | off   | 12:00:00  | 0       | 0\n'
                )

            mgr._run_netsim_cli = fake_cli
            joined = [{
                'serial': 'emulator-5554',
                'device_name': 'Phone',
                'state': 'CONNECTED',
                'chip_ids': {'1'},
            }]
            mgr.poll_and_manage_daemon(second, joined, now_mono=102.0)
            self.assertEqual(cli_calls[0], 1)
            self.assertEqual(len(mgr.skipped_notes), 1)
            mgr.poll_and_manage_daemon(second, joined, now_mono=103.0)
            self.assertEqual(cli_calls[0], 1)

    def test_netsim_cli_closes_stdin_refuses_capture_get_and_cleans_up_once(
            self):
        import emulator_resource_monitor as erm
        mgr = erm.NetsimPcapManager(pcap_dir='', out_dir=self.id())
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append((cmd, kwargs))
            return MagicMock(stdout='', stderr='')

        with patch.object(erm.subprocess, 'run', side_effect=fake_run):
            # `capture get` makes netsimd panic, and `capture` subcommands
            # without `ini_dir` are refused so the CLI never consults ambient
            # discovery directories.
            self.assertIsNone(
                mgr._run_netsim_cli('netsim',
                                    43929, ['capture', 'get', '1'],
                                    ini_dir=self.id()))
            self.assertIsNone(
                mgr._run_netsim_cli('netsim', 43929, ['capture', 'list']))
            self.assertEqual(runs, [])
            self.assertEqual(
                mgr._run_netsim_cli('netsim',
                                    43929, ['capture', 'list'],
                                    ini_dir=self.id()), '')
        self.assertEqual(len(runs), 1)
        self.assertIs(runs[0][1].get('stdin'), erm.subprocess.DEVNULL)
        self.assertEqual(runs[0][1]['env'].get('TMPDIR'), self.id())
        self.assertEqual(runs[0][1]['env'].get('NETSIM_INSTANCE'), '1')

        # Cleanup runs at most once, and not at all without --netsim-pcap.
        for pcap_dir, expected_calls in (('', 1), (None, 0)):
            tracker = erm.NetsimTracker(pcap_dir=pcap_dir,
                                        enable_log=False,
                                        out_dir=self.id())
            with patch.object(tracker.pcap_mgr, 'cleanup') as mgr_cleanup:
                tracker.cleanup()
                tracker.cleanup()
            self.assertEqual(mgr_cleanup.call_count, expected_calls, pcap_dir)

    def test_pcap_manager_skips_cli_when_disabled_and_lists_once_per_daemon(
            self):
        list_out = (
            'ID | Device Name | Chip Kind            | State | Timestamp | Records | Size\n'
            '1  | erm_live37  | WIFI                 | off   | 00:00:00  | 0       | 0\n'
            '2  | erm_live37  | BLUETOOTH_LOW_ENERGY | off   | 00:00:00  | 0       | 0\n'
        )
        calls = []

        def fake_cli(_bin, _port, args, timeout=5.0, **_):
            calls.append(list(args))
            return list_out

        joined = [{
            'serial': 'emulator-5554',
            'device_name': 'erm_live37',
            'state': 'CONNECTED',
            'chip_ids': set(),
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, daemon_info, _, _ = self._harvest_fixture(tmpdir)
            daemon_info['grpc_port'] = 43929
            # Without --netsim-pcap the CLI never runs.
            from emulator_resource_monitor import NetsimPcapManager
            disabled = NetsimPcapManager(pcap_dir=None)
            disabled._run_netsim_cli = fake_cli
            disabled.poll_and_manage_daemon(daemon_info, joined, now_mono=10.0)
            self.assertEqual(calls, [])

            mgr._run_netsim_cli = fake_cli
            mgr.poll_and_manage_daemon(daemon_info, joined, now_mono=10.0)
            self.assertEqual(len(mgr.enabled_by_monitor), 2)
            del calls[:]
            mgr.cleanup({daemon_info['daemon_key']: daemon_info})
        self.assertEqual(calls.count(['capture', 'list']), 1)
        self.assertEqual(
            sum(1 for c in calls if c[:3] == ['capture', 'patch', 'off']), 2)

    def test_pcap_cleanup_restores_prefix_colliding_chips_and_skips_exited_daemons(
            self):
        from emulator_resource_monitor import simulate_netsim_capture_filter
        rows_state = [{
            'id': 1,
            'device_name': 'erm_live37',
            'chip_kind': 'WIFI',
            'raw_chip_kind': 'WIFI',
            'state': 'off',
        }]
        calls = []

        def render_rows():
            hdr = 'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
            body = ''.join(
                f"{r['id']} | {r['device_name']} | {r['raw_chip_kind']} | {r['state']} | 00:00:00 | 0 | 0\n"
                for r in rows_state)
            return hdr + body

        def fake_cli(_bin, _port, args, timeout=5.0, **_):
            calls.append(list(args))
            if args[:2] == ['capture', 'list']:
                return render_rows()
            if args[:2] == ['capture', 'patch']:
                new_state = args[2]
                for m in simulate_netsim_capture_filter(rows_state, args[3:]):
                    m['state'] = new_state
                return 'OK'
            return ''

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, live_daemon, out_dir, _ = self._harvest_fixture(tmpdir)
            live_daemon['grpc_port'] = 43929
            pcap = os.path.join(live_daemon['artifact_dir'], 'pcaps',
                                'netsim-1-Pixel_8-WIFI.pcap')
            with open(pcap, 'wb') as f:
                f.write(b'\xd4' * (600 * 1024))
            mgr._run_netsim_cli = fake_cli

            emu_a = {
                'serial': 'emulator-5554',
                'device_name': 'erm_live37',
                'state': 'CONNECTED',
                'chip_ids': {'1'},
            }
            mgr.poll_and_manage_daemon(live_daemon, [emu_a], now_mono=10.0)
            self.assertEqual(rows_state[0]['state'], 'on')

            # Second emulator joins with prefix-colliding name & chip id (1 vs 10).
            rows_state.append({
                'id': 10,
                'device_name': 'erm_live37b',
                'chip_kind': 'WIFI',
                'raw_chip_kind': 'WIFI',
                'state': 'off',
            })
            emu_b = {
                'serial': 'emulator-5556',
                'device_name': 'erm_live37b',
                'state': 'CONNECTED',
                'chip_ids': {'10'},
            }
            mgr.poll_and_manage_daemon(live_daemon, [emu_a, emu_b],
                                       now_mono=25.0)
            self.assertEqual([r['state'] for r in rows_state], ['on', 'on'])

            # Exited daemon sharing artifact_dir must not steal live pcaps on cleanup.
            exited_daemon = dict(
                live_daemon,
                daemon_key=(5000, 100.0),
                pid=5000,
                start_wall=100.0,
                exited=True,
                all_joined_emulators=[{
                    'serial': 'emulator-5558',
                    'device_name': 'Pixel_8',
                    'state': 'DISCONNECTED',
                    'chip_ids': {'1'},
                }],
            )
            live_daemon['all_joined_emulators'] = [emu_a, emu_b]
            os.utime(pcap, (2000.0, 2000.0))
            mgr.cleanup({
                exited_daemon['daemon_key']: exited_daemon,
                live_daemon['daemon_key']: live_daemon,
            })

            self.assertEqual([r['state'] for r in rows_state], ['off', 'off'])
            copied = self._copied_paths(out_dir)
            self.assertFalse(any('5000-100' in p for p in copied), copied)
            self.assertFalse(mgr.cap_reached)

            mgr.forget_daemon(live_daemon['daemon_key'])
            self.assertEqual(mgr.enabled_by_monitor, {})
            self.assertEqual(mgr.checked_emulators, set())
            self.assertEqual(mgr.last_harvest_by_daemon, {})

    def test_pcap_manager_guards_foreign_cli_and_throttles_normalized_errors(
            self):
        import emulator_resource_monitor as erm
        calls = []

        def failing_cli(_bin, _port, args, timeout=5.0, ini_dir=None, **_):
            n = len(calls)
            calls.append((ini_dir, list(args)))
            return (f'netsim E 11-09 08:24:10.{600 + n:03d} ini_file.rs:139 - '
                    'Unable to find netsim ini file: "/tmp/netsim.ini"\n'
                    f'netsim E 11-09 08:24:10.{600 + n:03d} main.rs:349 - '
                    'error=Failed to connect\n')

        joined = [{
            'serial': 'emulator-5554',
            'device_name': 'erm_live37',
            'state': 'CONNECTED',
            'chip_ids': {'1'},
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, daemon_info, _, _ = self._harvest_fixture(tmpdir)
            ini_dir = os.path.join(tmpdir, 'ini_dir')
            os.makedirs(ini_dir, exist_ok=True)
            ini_path = os.path.join(ini_dir, 'netsim.ini')
            _touch(ini_path, 'grpc.port=43929\npid=9001\n')
            daemon_info['ini_path'] = ini_path
            daemon_info['grpc_port'] = 43929
            mgr._run_netsim_cli = failing_cli

            # Foreign-user daemon, unowned ini_dir, or no live named non-DISABLED
            # emulator must never invoke the netsim CLI.
            foreign = dict(daemon_info,
                           daemon_key=(9002, 1000.0),
                           same_user=False)
            mgr.poll_and_manage_daemon(foreign, joined, now_mono=1.0)
            mgr.poll_and_manage_daemon(daemon_info, [{
                'serial': 'emulator-5554',
                'device_name': 'erm_live37',
                'state': 'DISABLED',
                'chip_ids': set(),
            }],
                                       now_mono=1.0)
            mgr.poll_and_manage_daemon(daemon_info, [{
                'serial': 'emulator-5554',
                'device_name': None,
                'state': 'CONNECTED',
                'chip_ids': set(),
            }],
                                       now_mono=1.0)
            if os.name == 'posix' and hasattr(os, 'getuid'):
                with patch.object(erm,
                                  '_current_uid',
                                  return_value=os.getuid() + 1):
                    mgr.poll_and_manage_daemon(daemon_info,
                                               joined,
                                               now_mono=1.0)
            self.assertEqual(calls, [])

            # Repeated 2s ticks on CLI error: 1 initial + 1 immediate retry,
            # then throttled for 10s, with a single single-line normalized note.
            mgr.last_harvest_by_daemon.clear()
            for t in (10.0, 12.0, 14.0, 16.0, 18.0):
                mgr.poll_and_manage_daemon(daemon_info, joined, now_mono=t)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(mgr.skipped_notes), 1, mgr.skipped_notes)
            self.assertNotIn('\n', mgr.skipped_notes[0])
            self.assertIn('Unable to find netsim ini file',
                          mgr.skipped_notes[0])

            # None CLI return (missing binary / timeout) also records a note
            # and throttles after 1 immediate retry.
            none_calls = []
            mgr._run_netsim_cli = lambda *a, **kw: (none_calls.append(1) and
                                                    None)
            d2 = dict(daemon_info, daemon_key=(9003, 2000.0), grpc_port=43930)
            for t in (30.0, 32.0, 34.0, 36.0):
                mgr.poll_and_manage_daemon(d2, joined, now_mono=t)
            self.assertEqual(len(none_calls), 2)
            self.assertTrue(any('43930' in n for n in mgr.skipped_notes),
                            mgr.skipped_notes)

    def test_pcap_manager_passes_ini_tmpdir_and_instance_and_records_cli_errors(
            self):
        from emulator_resource_monitor import (build_netsim_cli_args,
                                               parse_netsim_capture_list)
        missing_ini_out = (
            'netsim E 11-09 08:06:41.842 ini_file.rs:139 - '
            'Unable to find netsim ini file: "/tmp/netsim_2.ini"\n'
            'netsim E 11-09 08:06:41.842 main.rs:349 - '
            'error=Failed to connect to : invalid socket address\n')
        rows, err = parse_netsim_capture_list(missing_ini_out)
        self.assertEqual(rows, [])
        self.assertIsNotNone(err)
        self.assertIsNotNone(
            parse_netsim_capture_list('Failed to connect to server')[1])
        self.assertIsNotNone(
            parse_netsim_capture_list('error=connection failed')[1])

        self.assertEqual(build_netsim_cli_args(44709, 1, ['capture', 'list']),
                         ['-v', '-p', '44709', 'capture', 'list'])
        self.assertEqual(build_netsim_cli_args(42747, 2, ['capture', 'list']),
                         ['-v', '-i', '2', '-p', '42747', 'capture', 'list'])

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr, daemon_info, _, _ = self._harvest_fixture(tmpdir)
            ini_dir = os.path.join(tmpdir, 'emu_tmp')
            os.makedirs(ini_dir, exist_ok=True)
            ini_path = os.path.join(ini_dir, 'netsim_2.ini')
            _touch(ini_path, 'grpc.port=42747\npid=9001\n')
            daemon_info['ini_path'] = ini_path
            daemon_info['grpc_port'] = 42747

            calls = []
            fail_first = [True]
            list_ok = (
                'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                '0  | erm_live37  | WIFI      | off   | 00:00:00  | 0       | 0\n'
            )

            def fake_cli(netsim_bin,
                         grpc_port,
                         args,
                         timeout=5.0,
                         ini_dir=None,
                         instance_num=None):
                calls.append(
                    (netsim_bin, grpc_port, ini_dir, instance_num, list(args)))
                if fail_first[0]:
                    fail_first[0] = False
                    return missing_ini_out
                return list_ok

            mgr._run_netsim_cli = fake_cli
            joined = [{
                'serial': 'emulator-5554',
                'device_name': 'erm_live37',
                'state': 'CONNECTED',
                'chip_ids': {'0'},
            }]

            mgr.poll_and_manage_daemon(daemon_info, joined, now_mono=10.0)
            self.assertTrue(
                any('Unable to find netsim ini file' in n
                    for n in mgr.skipped_notes))
            self.assertEqual(mgr.enabled_by_monitor, {})

            mgr.poll_and_manage_daemon(daemon_info, joined, now_mono=11.0)
            self.assertIn((daemon_info['daemon_key'], 0),
                          mgr.enabled_by_monitor)

            mgr.cleanup({daemon_info['daemon_key']: daemon_info})
            self.assertTrue(
                all((b, p, d, i) == ('netsim', 42747, ini_dir, 2)
                    for b, p, d, i, _ in calls))
            self.assertIn(['capture', 'patch', 'on', '0', 'erm_live37', 'WIFI'],
                          [a for *_, a in calls])
            self.assertIn(
                ['capture', 'patch', 'off', '0', 'erm_live37', 'WIFI'],
                [a for *_, a in calls])


# PIDs above Linux's PID_MAX_LIMIT (2^22), as in the rust/src/netsim.rs tests:
# no /proc lookup or liveness probe reaches a real process, so a daemon's
# start_wall equals its create_time.
_DAEMON_A = 5_000_100
_DAEMON_B = 5_000_200
_WORKER_A = 5_000_300
_WORKER_B = 5_000_400
_DEAD_PID = 5_000_999
_START = 1_790_375_825

# `lsof -n -P -F pn` output: `p` starts a process, `f` a file, `n` names it.
# Besides the expected rows it has a non-loopback peer, a duplicate, a
# non-target port, a listener and a PID outside the emulator groups.
_LSOF_OUT = ('p4001\n'
             'f42\nn127.0.0.1:59604->127.0.0.1:43929\n'
             'f43\nn[::1]:59605->[::1]:43929\n'
             'f44\nn10.0.0.5:59606->10.0.0.9:43929\n'
             'f45\nn127.0.0.1:59604->127.0.0.1:43929\n'
             'f46\nn127.0.0.1:59607->127.0.0.1:8554\n'
             'f47\nn127.0.0.1:43929\n'
             'p9999\n'
             'f3\nn127.0.0.1:60000->127.0.0.1:43929\n')

# `netstat -ano` output with the same kinds of noise as _LSOF_OUT, plus a
# TIME_WAIT row and a UDP row (whose State column is blank).
_NETSTAT_OUT = (
    '\nActive Connections\n\n'
    '  Proto  Local Address          Foreign Address        State           PID\n'
    '  TCP    127.0.0.1:59604        127.0.0.1:43929        ESTABLISHED     4001\n'
    '  TCP    [::1]:59605            [::1]:43929            ESTABLISHED     4001\n'
    '  TCP    127.0.0.1:59604        127.0.0.1:43929        ESTABLISHED     4001\n'
    '  TCP    192.168.1.5:59606      192.168.1.9:43929      ESTABLISHED     4001\n'
    '  TCP    127.0.0.1:59607        127.0.0.1:8554         ESTABLISHED     4001\n'
    '  TCP    127.0.0.1:60000        127.0.0.1:43929        TIME_WAIT       4001\n'
    '  TCP    127.0.0.1:60001        127.0.0.1:43929        ESTABLISHED     9999\n'
    '  TCP    127.0.0.1:43929        0.0.0.0:0              LISTENING       7000\n'
    '  UDP    127.0.0.1:43929        *:*                                    4001\n'
)

# Connections from emulator PID 4001 to 43929 in _LSOF_OUT and _NETSTAT_OUT.
_EXPECTED_CONNS = [('emulator-5554', 4001, 59604),
                   ('emulator-5554', 4001, 59605)]

_LSOF_ARGS_4001_4002 = [
    'lsof', '-n', '-P', '-F', 'pn', '-iTCP', '-sTCP:ESTABLISHED', '-a', '-p',
    '4001,4002'
]


def _conn_summary(conns):
    return [(c['serial'], c['pid'], c['peer_port']) for c in conns]


def _touch(path, content=''):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def _write_ini(path, content, mtime):
    _touch(path, content)
    os.utime(path, (mtime, mtime))


def _netsimd(pid, start, tmp, **fields):
    """A get_process_stats() netsimd record whose TMPDIR is `tmp`."""
    proc = {
        'pid': pid,
        'create_time': float(start),
        'ppid': 1,
        'cmdline': ['/sdk/emulator/netsimd'],
        'exe': '/sdk/emulator/netsimd',
        'environ': {
            'TMPDIR': tmp,
            'USER': 'test'
        },
    }
    proc.update(fields)
    return proc


def _fail_cli(args):
    raise AssertionError(f'unexpected tool run: {args}')


# Phone and Glasses on one netsimd, whose log also has a device named after a
# serial.
_SHARED_DAEMON_LOG = (
    'netsim I 09-25 22:37:06.000 netsimd.rs:106 - '
    'Handling new connection for "", device Phone\n'
    'netsim I 09-25 22:37:06.000 service.rs:585 - '
    'DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n'
    'netsim I 09-25 22:37:08.000 netsimd.rs:106 - '
    'Handling new connection for "", device Glasses\n'
    'netsim I 09-25 22:37:08.000 service.rs:585 - '
    'DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Glasses)\n'
    'netsim I 09-25 22:37:09.000 service.rs:585 - '
    'DeviceActor: AddChip 3 (kind WIFI) to device 3 (emulator-5556)\n')


def _emu_group(pid, avd_id, display_name=None, ctime=None):
    """A get_process_stats() emulator group without a netsim endpoint."""
    return {
        'primary_pid': pid,
        'primary_ctime': float(_START if ctime is None else ctime),
        'avd_id': avd_id,
        'display_name': display_name,
        'avd_name': avd_id or display_name,
        'grpc_endpoint': None,
        'no_netsim': False,
    }


class TestNetsimCrossPlatform(unittest.TestCase):
    """Linux, macOS and Windows behavior of the netsim tracking.

    Mirrors the rust/src/netsim.rs tests of the same functions. target_os and
    os_family select the other OSes, so every branch runs on any host.
    """

    def setUp(self):
        import emulator_resource_monitor
        self.erm = emulator_resource_monitor
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def _mkdirs(self, *names):
        paths = [os.path.join(self.root, name) for name in names]
        for path in paths:
            os.makedirs(path, exist_ok=True)
        return paths

    def _symlink_dir_or_skip(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            # Windows needs a privilege to create symlinks.
            self.skipTest(f'cannot create a symlink: {e}')

    def _tracker_for(self, dirs, lock_holders=None):
        """A tracker that scans only `dirs`.

        It reads `lock_holders` instead of /proc/locks (none by default, as on
        macOS and Windows) and never runs the netsim CLI.
        """
        tracker = self.erm.NetsimTracker(pcap_dir=None,
                                         enable_log=False,
                                         out_dir=self.root,
                                         os_family='linux')
        tracker.custom_ini_dirs = list(dirs)
        for target, name, value in (
            (tracker, '_read_proc_locks', dict(lock_holders or {})),
            (tracker.pcap_mgr, '_run_netsim_cli', ''),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return tracker

    def _tick(self, tracker, procs, now_wall):
        with patch.object(self.erm, '_LAST_NETSIMD_PROCS', list(procs)), \
                patch.object(self.erm, '_LAST_EMU_GROUP_PIDS', {}), \
                patch.object(self.erm, '_LAST_EMU_GROUP_INFO', {}):
            tracker.update({}, {},
                           now_wall=float(now_wall),
                           now_mono=float(now_wall - _START))

    def _collect(self,
                 os_family,
                 emu_pids,
                 daemon_pids,
                 run_cli,
                 ports=(43929,)):
        tracker = self._tracker_for([])
        return tracker._collect_loopback_grpc_sockets(
            set(ports), {'emulator-5554': list(emu_pids)},
            set(daemon_pids),
            os_family=os_family,
            run_cli=run_cli)

    def _denied(self, pid):
        raise self.erm.psutil.AccessDenied(pid)

    @staticmethod
    def _count_events(tracker, prefix):
        return sum(1 for _, text in tracker.timeline if text.startswith(prefix))

    def test_os_family_maps_platform_names(self):
        # Other Unixes use the Linux paths, as Rust HostOs::current() does.
        for name, family in (('linux', 'linux'), ('freebsd14', 'linux'),
                             ('darwin', 'darwin'), ('macos', 'darwin'),
                             ('win32', 'win32'), ('nt', 'win32')):
            self.assertEqual(self.erm._os_family(name), family, name)
        self.assertEqual(self.erm._os_family(),
                         self.erm._os_family(sys.platform))

    def test_get_netsim_discovery_dirs_linux(self):
        env = {
            'ANDROID_EMULATOR_DISCOVERY_DIR': '/disc',
            'TMPDIR': '/tmpdir',
            'XDG_RUNTIME_DIR': '/run/user/1000',
            'ANDROID_TMP': '/android_tmp',
            'HOME': '/home/alice',
            'LOCALAPPDATA': '/ignored',
        }
        self.assertEqual(
            self.erm.get_netsim_discovery_dirs(env, target_os='linux'),
            ['/disc', '/tmpdir', '/run/user/1000', '/tmp', '/android_tmp'])

    def test_get_netsim_discovery_dirs_macos(self):
        # netsim appends Library/Caches/TemporaryItems to $HOME, or to /tmp.
        env = {'TMPDIR': '/var/folders/xy/T/', 'HOME': '/Users/alice'}
        items = ('Library', 'Caches', 'TemporaryItems')
        self.assertEqual(
            self.erm.get_netsim_discovery_dirs(env, target_os='darwin'), [
                '/var/folders/xy/T/',
                os.path.join('/Users/alice', *items),
                os.path.join('/tmp', *items),
            ])

    def test_get_netsim_discovery_dirs_windows(self):
        # netsim uses LOCALAPPDATA plus Temp and never reads TEMP or TMP.
        local = r'C:\Users\alice\AppData\Local'
        env = {
            'LOCALAPPDATA': local,
            'TEMP': r'C:\Temp',
            'TMP': r'C:\Tmp',
            'ANDROID_TMP': r'D:\android_tmp',
        }
        self.assertEqual(
            self.erm.get_netsim_discovery_dirs(env, target_os='win32'), [
                os.path.join(local, 'Temp'),
                os.path.join('/tmp', 'Temp'),
                r'D:\android_tmp',
            ])

    def test_get_netsim_discovery_dirs_skips_empty_and_duplicate_values(self):
        env = {'TMPDIR': '', 'XDG_RUNTIME_DIR': '/tmp', 'ANDROID_TMP': '/tmp/'}
        self.assertEqual(
            self.erm.get_netsim_discovery_dirs(env, target_os='linux'),
            ['/tmp'])

    def test_host_discovery_dirs_adds_run_user_on_linux_only(self):
        dirs = self.erm.host_discovery_dirs
        self.assertEqual(dirs({}, target_os='linux', uid=1000),
                         ['/tmp', '/run/user/1000'])
        self.assertEqual(dirs({}, target_os='linux', uid=None), ['/tmp'])
        self.assertEqual(
            dirs({}, target_os='darwin', uid=501),
            [os.path.join('/tmp', 'Library', 'Caches', 'TemporaryItems')])
        self.assertEqual(dirs({}, target_os='win32', uid=1),
                         [os.path.join('/tmp', 'Temp')])
        # XDG_RUNTIME_DIR usually is /run/user/<uid> already; it is listed once.
        self.assertEqual(
            dirs({'XDG_RUNTIME_DIR': '/run/user/1000'},
                 target_os='linux',
                 uid=1000), ['/run/user/1000', '/tmp'])

    def test_current_uid(self):
        # Windows has no os.getuid().
        with patch.object(self.erm.os, 'getuid', None, create=True):
            self.assertIsNone(self.erm._current_uid())
        if hasattr(os, 'getuid'):
            self.assertEqual(self.erm._current_uid(), os.getuid())

    def test_resolve_netsim_artifact_dir_windows(self):

        def check(env, base):
            self.assertEqual(
                self.erm.resolve_netsim_artifact_dir(env, target_os='win32'),
                os.path.join(base, 'netsimd'), env)

        # GetTempPath() order is TMP, TEMP, USERPROFILE, then the Windows dir.
        # It is already per-user, so there is no android-$USER level.
        check({'TEMP': r'C:\T', 'USER': 'alice'}, r'C:\T')
        check({'TEMP': r'C:\T', 'TMP': r'C:\M'}, r'C:\M')
        check({'TMP': '', 'TEMP': r'C:\T'}, r'C:\T')
        check({'USERPROFILE': r'C:\Users\alice'}, r'C:\Users\alice')
        check({'SystemRoot': r'C:\WINDOWS'}, r'C:\WINDOWS')
        check({'ANDROID_TMP': r'D:\A', 'TMP': r'C:\M'}, r'D:\A')
        # An empty env falls back to the monitor's, which is empty here too.
        with patch.dict(os.environ, {}, clear=True):
            check({}, 'C:\\Windows')

    def test_resolve_netsim_artifact_dir_unix(self):

        def check(env, *parts):
            for target_os in ('linux', 'darwin'):
                self.assertEqual(
                    self.erm.resolve_netsim_artifact_dir(env,
                                                         target_os=target_os),
                    os.path.join(*parts, 'netsimd'), (target_os, env))

        alice = {'TMPDIR': '/var/tmp', 'USER': 'alice'}
        check(alice, '/var/tmp', 'android-alice')
        check({**alice, 'ANDROID_TMP': '/a'}, '/a', 'android-alice')
        # temp_dir() ignores TEMP and TMP off Windows; an empty USER counts as
        # unset.
        no_tmpdir = {'TEMP': '/ignored', 'TMP': '/ignored', 'USER': ''}
        check(no_tmpdir, '/tmp', 'android')

    def test_resolve_netsim_artifact_dir_falls_back_to_host_env(self):
        # An unreadable (empty or missing) daemon env resolves from the
        # monitor's env, which netsimd normally inherits.
        host_env = {'TMPDIR': '/host_tmp', 'USER': 'bob'}
        with patch.dict(os.environ, host_env, clear=True):
            for env in ({}, None):
                self.assertEqual(
                    self.erm.resolve_netsim_artifact_dir(env,
                                                         target_os='linux'),
                    os.path.join('/host_tmp', 'android-bob', 'netsimd'))
            # A readable daemon env is used as-is.
            self.assertEqual(
                self.erm.resolve_netsim_artifact_dir({'USER': 'alice'},
                                                     target_os='linux'),
                os.path.join('/tmp', 'android-alice', 'netsimd'))

    def test_extract_ini_instance_num(self):
        num = self.erm.extract_ini_instance_num
        self.assertEqual(num('netsim.ini'), self.erm.DEFAULT_NETSIM_INSTANCE)
        self.assertEqual(num('/tmp/netsim_2.ini'), 2)
        self.assertEqual(num('netsim_10.ini'), 10)
        for other in ('netsim.ini.lock', 'netsim_2.ini.lock',
                      'netsim_2.ini.init.lock', 'netsim_.ini', 'netsim_+3.ini',
                      'netsim_x.ini', 'netsim_99999999999.ini', 'pid_1234.ini',
                      'netsim', '', None):
            self.assertIsNone(num(other), other)
        # A digit run longer than int() converts is not a number either.
        self.assertIsNone(num('netsim_' + '9' * 5000 + '.ini'))

    def test_read_ini_observation_validates_pid_and_port(self):
        ini = os.path.join(self.root, 'netsim_3.ini')
        _write_ini(ini, 'pid=0\ngrpc.port=70000\ngrpc.token=secret\n', _START)
        obs = self.erm._read_ini_observation(ini, {})
        self.assertEqual((obs['path'], obs['instance'], obs['mtime']),
                         (ini, 3, _START))
        self.assertEqual(obs['holders'], [])
        self.assertIsNone(obs['pid'])
        self.assertIsNone(obs['port'])
        self.assertNotIn('grpc.token', obs['data'])

        _write_ini(ini, 'pid=abc\ngrpc.port=-1\n', _START)
        obs = self.erm._read_ini_observation(ini, {})
        self.assertEqual((obs['pid'], obs['port']), (None, None))

        # Only what Rust's str::parse() takes is a number.
        for odd in ('1_0', '\uff11\uff12', '0x10', '1e3', '9' * 5000):
            _write_ini(ini, f'pid={odd}\ngrpc.port={odd}\n', _START)
            obs = self.erm._read_ini_observation(ini, {})
            self.assertEqual((obs['pid'], obs['port']), (None, None), odd[:8])
        _write_ini(ini, 'pid=+12\ngrpc.port=+43929\n', _START)
        obs = self.erm._read_ini_observation(ini, {})
        self.assertEqual((obs['pid'], obs['port']), (12, 43929))

        _write_ini(ini, f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START)
        obs = self.erm._read_ini_observation(ini, {})
        self.assertEqual((obs['pid'], obs['port']), (_DAEMON_A, 43929))

        missing = self.erm._read_ini_observation(
            os.path.join(self.root, 'netsim.ini'), {})
        self.assertEqual((missing['data'], missing['mtime']), ({}, None))

    @unittest.skipUnless(hasattr(os, 'major'), 'no os.major() on this host')
    def test_get_file_lock_key_and_ini_lock_holders(self):
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, 'grpc.port=43929\n', _START)
        for suffix in ('.lock', '.init.lock'):
            _touch(ini + suffix)
        st = os.stat(ini + '.lock')
        self.assertEqual(
            self.erm.get_file_lock_key(ini + '.lock'),
            f'{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}')
        self.assertIsNone(self.erm.get_file_lock_key(ini + '.missing'))
        # Windows has no os.major() and no /proc/locks. patch.object puts
        # back the attribute that the del removes.
        with patch.object(self.erm.os, 'major'):
            del self.erm.os.major
            self.assertIsNone(self.erm.get_file_lock_key(ini + '.lock'))

        # Holders of <ini>.lock come before those of <ini>.init.lock.
        holders = {
            self.erm.get_file_lock_key(ini + '.init.lock'): _DAEMON_B,
            self.erm.get_file_lock_key(ini + '.lock'): _DAEMON_A,
        }
        self.assertEqual(
            self.erm._read_ini_observation(ini, holders)['holders'],
            [_DAEMON_A, _DAEMON_B])

    def test_loopback_endpoint_port(self):
        port = self.erm._loopback_endpoint_port
        for ep, expected in (
            ('127.0.0.1:43929', 43929),
            ('127.8.9.10:1', 1),
            ('[::1]:43929', 43929),
            ('[::ffff:127.0.0.1]:9', 9),
            ('LOCALHOST:80', 80),
        ):
            self.assertEqual(port(ep), expected, ep)
        for ep in ('10.0.0.5:43929', '0.0.0.0:43929', '[::]:0', '*:*',
                   '127.0.0.1:x', '127.0.0.1:70000', '127.0.0.1', '', None):
            self.assertIsNone(port(ep), ep)

    def test_parse_lsof_established_loopback(self):
        res = self.erm.parse_lsof_established_loopback(_LSOF_OUT, {43929},
                                                       {4001: 'emulator-5554'})
        self.assertEqual(list(res), [43929])
        self.assertEqual(_conn_summary(res[43929]), _EXPECTED_CONNS)
        self.assertEqual(
            self.erm.parse_lsof_established_loopback(None, {43929}, {}),
            {43929: []})

    def test_parse_netstat_established_loopback(self):
        res = self.erm.parse_netstat_established_loopback(
            _NETSTAT_OUT, {43929}, {4001: 'emulator-5554'})
        self.assertEqual(list(res), [43929])
        self.assertEqual(_conn_summary(res[43929]), _EXPECTED_CONNS)
        self.assertEqual(
            self.erm.parse_netstat_established_loopback(None, {43929}, {}),
            {43929: []})

    def test_psutil_loopback_rows_keeps_established_loopback_only(self):
        addr = namedtuple('addr', 'ip port')
        conn = namedtuple('conn', 'laddr raddr status')
        fake = MagicMock()
        fake.net_connections.return_value = [
            conn(addr('127.0.0.1', 59604), addr('127.0.0.1', 43929),
                 'ESTABLISHED'),
            conn(addr('::1', 59605), addr('::1', 43929), 'ESTABLISHED'),
            conn(addr('10.0.0.5', 59606), addr('127.0.0.1', 43929),
                 'ESTABLISHED'),
            conn(addr('127.0.0.1', 59607), addr('127.0.0.1', 43929),
                 'TIME_WAIT'),
            conn(addr('127.0.0.1', 43929), (), 'LISTEN'),
        ]
        real_process = self.erm.psutil.Process
        with patch.object(self.erm.psutil,
                          'Process',
                          side_effect=lambda pid: fake
                          if pid == 4001 else real_process(pid)):
            rows = self.erm._psutil_loopback_rows(4001)
        self.assertEqual(rows, [(4001, 59604, 43929), (4001, 59605, 43929)])
        fake.net_connections.assert_called_once_with(kind='tcp')

    def test_collect_loopback_grpc_sockets_finds_own_connection(self):
        # A real loopback connection owned by this process, found by psutil.
        with socket.create_server(('127.0.0.1', 0)) as listener, \
                socket.create_connection(listener.getsockname()) as client:
            port = listener.getsockname()[1]
            me = os.getpid()
            res = self._collect(None, [me], [], _fail_cli, ports=[port])
            self.assertEqual(_conn_summary(res[port]),
                             [('emulator-5554', me, client.getsockname()[1])])
            # A daemon PID is never attributed to an emulator.
            res = self._collect(None, [me], [me], _fail_cli, ports=[port])
            self.assertEqual(res, {port: []})

    def test_collect_loopback_grpc_sockets_asks_lsof_on_macos(self):
        # psutil is denied, so lsof runs once for the sorted emulator PIDs;
        # netsimd (7000) is left out.
        calls = []

        def run(args):
            calls.append(args)
            return _LSOF_OUT

        with patch.object(self.erm,
                          '_psutil_loopback_rows',
                          side_effect=self._denied):
            res = self._collect('darwin', [4002, 7000, 4001], [7000], run)
        self.assertEqual(calls, [_LSOF_ARGS_4001_4002])
        self.assertEqual(_conn_summary(res[43929]), _EXPECTED_CONNS)

    def test_collect_loopback_grpc_sockets_asks_netstat_on_windows(self):
        calls = []

        def run(args):
            calls.append(args)
            return _NETSTAT_OUT

        with patch.object(self.erm,
                          '_psutil_loopback_rows',
                          side_effect=self._denied):
            res = self._collect('win32', [4001], [], run)
        # No '-p tcp', which would hide the [::1] connection.
        self.assertEqual(calls, [['netstat', '-ano']])
        self.assertEqual(_conn_summary(res[43929]), _EXPECTED_CONNS)

    def test_collect_loopback_grpc_sockets_merges_psutil_and_cli_rows(self):
        # 4001 is readable and 4002 is not; 4001's row is also in the lsof
        # output and is kept once.
        def rows(pid):
            if pid == 4002:
                self._denied(pid)
            return [(4001, 59604, 43929)]

        with patch.object(self.erm, '_psutil_loopback_rows', side_effect=rows):
            res = self._collect('darwin', [4001, 4002], [],
                                lambda args: _LSOF_OUT)
        self.assertEqual(_conn_summary(res[43929]), _EXPECTED_CONNS)

    def test_collect_loopback_grpc_sockets_cli_failure_yields_no_conns(self):
        # A tool that fails or times out yields no connections.
        for os_family in ('darwin', 'win32'):
            with patch.object(self.erm,
                              '_psutil_loopback_rows',
                              side_effect=self._denied):
                res = self._collect(os_family, [4001], [],
                                    lambda args: None,
                                    ports=[43929, 43930])
            self.assertEqual(res, {43929: [], 43930: []}, os_family)

    def test_collect_loopback_grpc_sockets_runs_no_tool_unless_denied(self):
        # With no emulator PID to attribute connections to, nothing runs.
        with patch.object(self.erm, '_psutil_loopback_rows') as rows:
            res = self._collect('darwin', [7000], [7000], _fail_cli)
        rows.assert_not_called()
        self.assertEqual(res, {43929: []})
        for os_family, error in (
                # psutil answered, even if nothing is connected yet: the
                # 250 ms CONNECTING sub-poll must not start a tool each time.
            ('darwin', None),
            ('win32', None),
                # Linux lsof reads the same /proc files as psutil.
            ('linux', self.erm.psutil.AccessDenied(4001)),
                # The process exited.
            ('darwin', self.erm.psutil.NoSuchProcess(4001)),
        ):
            with patch.object(self.erm,
                              '_psutil_loopback_rows',
                              return_value=[],
                              side_effect=error):
                res = self._collect(os_family, [4001], [], _fail_cli)
            self.assertEqual(res, {43929: []}, (os_family, error))

    @unittest.skipUnless(os.name == 'posix', 'uses sh')
    def test_run_cli_with_timeout(self):
        run = self.erm._run_cli_with_timeout
        self.assertEqual(run(['sh', '-c', 'echo hi'], timeout=5), 'hi\n')
        # The exit status is ignored.
        self.assertEqual(run(['sh', '-c', 'echo out; exit 3'], timeout=5),
                         'out\n')
        self.assertIsNone(run(['/nonexistent/netstat'], timeout=5))
        # A hung tool is killed at the deadline.
        started = time.monotonic()
        self.assertIsNone(run(['sh', '-c', 'exec sleep 5'], timeout=0.2))
        self.assertLess(time.monotonic() - started, 3)

    def test_same_file_resolves_symlinks(self):
        real, = self._mkdirs('real')
        _touch(os.path.join(real, 'netsim.ini'))
        _touch(os.path.join(self.root, 'other.ini'))
        alias = os.path.join(self.root, 'alias')
        self._symlink_dir_or_skip(real, alias)
        same = self.erm._same_file
        self.assertTrue(
            same(os.path.join(alias, 'netsim.ini'),
                 os.path.join(real, 'netsim.ini')))
        self.assertFalse(
            same(os.path.join(real, 'netsim.ini'),
                 os.path.join(self.root, 'other.ini')))
        # Missing files compare by path.
        self.assertTrue(same('/nonexistent/a.ini', '/nonexistent/a.ini'))
        self.assertFalse(same('/nonexistent/a.ini', '/nonexistent/b.ini'))

    def test_discover_ini_files_dedupes_aliases_and_honors_custom_dirs(self):
        real, other = self._mkdirs('real', 'other')
        for name in ('netsim.ini', 'netsim_2.ini', 'netsim.ini.lock',
                     'netsim_2.ini.init.lock', 'netsim_x.ini', 'pid_42.ini'):
            _touch(os.path.join(real, name))
        # macOS reaches one dir as both /tmp and /private/tmp, for example.
        alias = os.path.join(self.root, 'alias')
        self._symlink_dir_or_skip(real, alias)
        # A daemon's own TMPDIR is not searched while custom dirs are set.
        _touch(os.path.join(other, 'netsim_3.ini'))

        tracker = self._tracker_for([alias, real])
        found = tracker._discover_ini_files([{'TMPDIR': other}])
        # One path per file, spelled as first seen.
        self.assertEqual(found, [
            os.path.join(alias, 'netsim.ini'),
            os.path.join(alias, 'netsim_2.ini')
        ])

    def test_discover_ini_files_searches_monitor_and_daemon_dirs(self):
        host, daemon, missing = self._mkdirs('host', 'daemon', 'missing')
        os.rmdir(missing)
        _touch(os.path.join(host, 'netsim.ini'))
        _touch(os.path.join(daemon, 'netsim_2.ini'))
        tracker = self._tracker_for([])
        tracker.socket_os_family = None
        with patch.object(self.erm,
                          'host_discovery_dirs',
                          return_value=[host, missing]) as host_dirs, \
                patch.object(self.erm,
                             'get_netsim_discovery_dirs',
                             side_effect=lambda env: [env['TMPDIR']]
                             if env else []) as daemon_dirs:
            found = tracker._discover_ini_files([{'TMPDIR': daemon}, None])
        host_dirs.assert_called_once_with(os.environ,
                                          uid=self.erm._current_uid())
        daemon_dir_args = [c.args for c in daemon_dirs.call_args_list]
        self.assertEqual(daemon_dir_args, [({'TMPDIR': daemon},), ({},)])
        self.assertEqual(found, [
            os.path.join(daemon, 'netsim_2.ini'),
            os.path.join(host, 'netsim.ini')
        ])

    def test_update_searches_dirs_of_live_daemons(self):
        tracker = self._tracker_for([])
        proc_a = _netsimd(_DAEMON_A, _START, '/a')
        proc_b = _netsimd(_DAEMON_B, _START + 5, '/b')
        with patch.object(tracker, '_discover_ini_files',
                          return_value=[]) as discover:
            self._tick(tracker, [proc_b, proc_a], _START + 10)
            # Once A exits, only B's environment is searched.
            self._tick(tracker, [proc_b], _START + 20)
        self.assertEqual(
            [c.args[0] for c in discover.call_args_list],
            [[proc_a['environ'], proc_b['environ']], [proc_b['environ']]])

    def test_read_emulator_discovery_ini_searches_worker_dirs_first(self):
        pid = 5_000_300
        early, late = self._mkdirs(os.path.join('early', 'avd', 'running'),
                                   os.path.join('late', 'avd', 'running'))
        name = f'pid_{pid}.ini'
        # An empty file is skipped, and a pid_<pid>.ini outside avd/running
        # is not the emulator's.
        _touch(os.path.join(early, name))
        _touch(os.path.join(self.root, 'early', name), 'avd.id=wrong\n')
        pixel_ini = ('avd.id=Pixel_8\navd.name=Pixel 8\ngrpc.port=8554\n'
                     'netsim.endpoint=localhost:8877\ngrpc.token=secret\n')
        _touch(os.path.join(late, name), pixel_ini)
        worker = MagicMock()
        worker.environ.return_value = {
            'ANDROID_EMULATOR_DISCOVERY_DIR': os.path.join(self.root, 'early'),
            'TMPDIR': os.path.join(self.root, 'late'),
        }
        expected = {
            'avd.id': 'Pixel_8',
            'avd.name': 'Pixel 8',
            'grpc.port': '8554',
            'netsim.endpoint': 'localhost:8877',
        }
        with patch.object(self.erm, 'host_discovery_dirs', return_value=[]):
            self.assertEqual(self.erm._read_emulator_discovery_ini(pid, worker),
                             expected)
        # An unreadable worker env falls back to the monitor's dirs.
        worker.environ.side_effect = self.erm.psutil.AccessDenied(pid)
        with patch.object(self.erm,
                          'host_discovery_dirs',
                          return_value=[os.path.join(self.root, 'late')]):
            self.assertEqual(self.erm._read_emulator_discovery_ini(pid, worker),
                             expected)
        self.assertEqual(self.erm._read_emulator_discovery_ini(0, worker), {})

    def test_classify_process_handles_windows_and_launcher_names(self):
        classify = self.erm._classify_process
        self.assertEqual(classify('netsimd.exe', 'netsimd.exe', 'netsimd.exe'),
                         'netsimd')
        self.assertEqual(
            classify('emulator.exe', 'emulator.exe', 'emulator.exe'),
            'emulator')
        self.assertEqual(classify('main', 'emulator.exe', ''), 'emulator')
        self.assertEqual(classify('qemu-system-x86_64.exe', '', ''), 'worker')
        # On Linux the launcher names its main thread LauncherLoop.
        self.assertEqual(classify('launcherloop', '', ''), 'emulator')
        # The netsim CLI is not the daemon.
        self.assertIsNone(classify('netsim.exe', 'netsim.exe', 'netsim.exe'))
        # Monitor binaries and SDK capability check helpers are not emulators.
        self.assertIsNone(
            classify('emulator-resource-monitor', 'emulator-resource-monitor',
                     'emulator-resource-monitor'))
        self.assertIsNone(
            classify('emulator-check', 'emulator-check', 'emulator-check'))

    @patch('psutil.process_iter')
    def test_get_process_stats_windows_exe_quick_hit_and_monitor_exclusions(
            self, mock_iter):
        win_emu = _fake_proc('main',
                             9100, [
                                 'C:\\Sdk\\emulator\\emulator.exe', '-avd',
                                 'Pixel_8', '-port', '5554'
                             ],
                             cpu=12.0,
                             rss_mb=100,
                             exe='C:\\Sdk\\emulator\\emulator.exe')
        win_netsim = _fake_proc('main',
                                9101, ['C:\\Sdk\\emulator\\netsimd.exe'],
                                cpu=3.0,
                                rss_mb=40,
                                exe='C:\\Sdk\\emulator\\netsimd.exe')
        rust_mon = _fake_proc('emulator-resource-monitor',
                              9102, ['/usr/bin/emulator-resource-monitor'],
                              cpu=1.0,
                              rss_mb=15,
                              exe='/usr/bin/emulator-resource-monitor')
        emu_check = _fake_proc('emulator-check',
                               9103, ['/sdk/emulator/emulator-check', 'accel'],
                               cpu=1.0,
                               rss_mb=5,
                               exe='/sdk/emulator/emulator-check')
        mock_iter.return_value = [win_emu, win_netsim, rust_mon, emu_check]

        self.erm._PID_PORT_CACHE.clear()
        self.erm._PROCESS_CACHE.clear()
        self.erm.get_process_stats()
        qemu_stats, unmapped, netsim_cpu, _, _, qemu_avds = self.erm.get_process_stats(
        )
        self.assertIn('emulator-5554', qemu_stats)
        self.assertEqual(qemu_avds['emulator-5554'], 'Pixel_8')
        self.assertEqual(netsim_cpu, '3.0% (1 processes)')
        self.assertEqual(unmapped, [])

    def test_classify_daemon_spawner_by_parent(self):
        spawner = self._tracker_for([])._classify_daemon_spawner
        real_process = self.erm.psutil.Process
        for name, expected in (
            ('qemu-system-x86_64.exe', 'qemu'),
            ('crosvm', 'qemu'),
            ('emulator.exe', 'emulator'),
            ('LauncherLoop', 'emulator'),
            ('bash', 'STANDALONE'),
        ):
            parent = MagicMock(**{'name.return_value': name})
            with patch.object(self.erm.psutil,
                              'Process',
                              side_effect=lambda pid, parent=parent: parent
                              if pid == 8000 else real_process(pid)):
                self.assertEqual(spawner(8000, {}), expected, name)
        # A parent in an emulator group needs no process lookup.
        self.assertEqual(spawner(_DEAD_PID, {'emulator-5554': [_DEAD_PID]}),
                         'emulator')
        self.assertEqual(spawner(1, {}), 'STANDALONE')

    def test_netsim_tracker_stale_ini_does_not_steal_live_daemon(self):
        # A leftover INI from a dead daemon must not override the live INI,
        # whichever sorts first and whether or not netsim wrote pid=.
        for with_pid in (False, True):
            for stale_first in (False, True):
                ctx = f'with_pid={with_pid} stale_first={stale_first}'
                with tempfile.TemporaryDirectory() as root:
                    dir_a = os.path.join(root, 'a')
                    dir_b = os.path.join(root, 'b')
                    os.makedirs(dir_a)
                    os.makedirs(dir_b)
                    stale_dir, live_dir = ((dir_a, dir_b) if stale_first else
                                           (dir_b, dir_a))
                    stale_ini = os.path.join(stale_dir, 'netsim.ini')
                    live_ini = os.path.join(live_dir, 'netsim.ini')
                    stale_pid = f'pid={_DEAD_PID}\n' if with_pid else ''
                    live_pid = f'pid={_DAEMON_A}\n' if with_pid else ''
                    _write_ini(stale_ini, stale_pid + 'grpc.port=1111\n',
                               _START - 600)
                    _write_ini(live_ini, live_pid + 'grpc.port=43929\n',
                               _START + 1)

                    tracker = self._tracker_for([dir_a, dir_b])
                    procs = [_netsimd(_DAEMON_A, _START, live_dir)]
                    for now in (_START + 10, _START + 20):
                        self._tick(tracker, procs, now)
                        d = tracker.daemons[(_DAEMON_A, _START)]
                        self.assertEqual(d['grpc_port'], 43929, ctx)
                        self.assertEqual(d['ini_path'], live_ini, ctx)
                        self.assertIn(stale_ini, tracker.stale_inis, ctx)
                        self.assertNotIn(live_ini, tracker.stale_inis, ctx)
                    self.assertEqual(self._count_events(tracker, 'STALE_INI('),
                                     1, ctx)
                    self.assertEqual(tracker.restart_count, 0, ctx)

    def test_netsim_tracker_rejects_ini_with_reused_pid(self):
        # The INI predates the daemon whose PID it names, so that PID was
        # reused by a new netsimd and the INI is stale.
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, f'pid={_DAEMON_A}\ngrpc.port=1111\n', _START - 600)
        tracker = self._tracker_for([self.root])
        self._tick(tracker, [_netsimd(_DAEMON_A, _START, self.root)],
                   _START + 10)
        d = tracker.daemons[(_DAEMON_A, _START)]
        self.assertIsNone(d['grpc_port'])
        self.assertIsNone(d['ini_path'])
        self.assertIn(ini, tracker.stale_inis)

    def test_netsim_tracker_reused_pid_of_live_daemon_is_stale(self):
        # As above, but a process with the reused PID is alive: it is the
        # tracked daemon, not the INI's writer, so the INI is still stale.
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, f'pid={_DAEMON_A}\ngrpc.port=1111\n', _START - 600)
        tracker = self._tracker_for([self.root])
        real_pid_exists = self.erm.psutil.pid_exists
        with patch.object(self.erm.psutil,
                          'pid_exists',
                          side_effect=lambda pid: pid == _DAEMON_A or
                          real_pid_exists(pid)):
            self._tick(tracker, [_netsimd(_DAEMON_A, _START, self.root)],
                       _START + 10)
        self.assertIsNone(tracker.daemons[(_DAEMON_A, _START)]['ini_path'])
        self.assertIn(ini, tracker.stale_inis)

    def test_netsim_tracker_counts_restart_once(self):
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        tracker = self._tracker_for([self.root])
        self._tick(tracker, [_netsimd(_DAEMON_A, _START, self.root)],
                   _START + 10)

        # netsimd B replaces A and rewrites the INI; later ticks see no restart.
        b_start = _START + 60
        _write_ini(ini, f'pid={_DAEMON_B}\ngrpc.port=43930\n', b_start + 1)
        procs_b = [_netsimd(_DAEMON_B, b_start, self.root)]
        self._tick(tracker, procs_b, b_start + 5)
        self._tick(tracker, procs_b, b_start + 10)

        self.assertEqual(tracker.restart_count, 1)
        self.assertEqual(self._count_events(tracker, 'RESTARTED('), 1)
        self.assertIn(
            f'RESTARTED(ini={ini}, old_pid={_DAEMON_A}, new_pid={_DAEMON_B})',
            [text for _, text in tracker.timeline])
        self.assertEqual(tracker.daemons[(_DAEMON_B, b_start)]['grpc_port'],
                         43930)
        self.assertTrue(tracker.daemons[(_DAEMON_A, _START)]['exited'])
        self.assertEqual(tracker.stale_inis, {})

    def test_client_mode_and_exiting_daemon_log_isolation(self):
        art_dir, = self._mkdirs(os.path.join('android-test', 'netsimd'))
        log_path = os.path.join(art_dir, 'netsim_stderr.log')
        _touch(
            log_path,
            'netsim I 09-25 22:37:05.612 netsimd.rs:451 - '
            'Netsim Version: 1.0.25, OS: linux, Arch: x86_64\n'
            'Args {\n    no_cli_ui: true,\n    pcap: true,\n}\n'
            'netsim I 09-25 22:37:05.650 netsimd.rs:700 - gRPC port: 43929\n'
            'netsim I 09-25 22:37:05.683 netsimd.rs:744 - '
            'Wrote to INI file /tmp/netsim.ini\n',
        )
        tracker = self._tracker_for([self.root])
        tracker.enable_log = True
        proc_a = _netsimd(_DAEMON_A, _START, self.root)
        self._tick(tracker, [proc_a], _START + 5)
        self.assertFalse(tracker.daemons[(_DAEMON_A,
                                          _START)].get('client_mode_recorded'))
        self.assertEqual(tracker.daemons[(_DAEMON_A, _START)]['grpc_port'],
                         43929)

        # Client-mode DAEMON_B spawns at _START + 15 and appends to shared log.
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('netsim W 09-25 22:37:20.500 netsimd.rs:500 - '
                    'Lock held by another process\n')
        proc_b = _netsimd(_DAEMON_B, _START + 15, self.root)
        self._tick(tracker, [proc_a, proc_b], _START + 16)
        da = tracker.daemons[(_DAEMON_A, _START)]
        db = tracker.daemons[(_DAEMON_B, _START + 15)]
        self.assertFalse(da.get('client_mode_recorded'))
        self.assertTrue(db.get('client_mode_recorded'))
        self.assertEqual(
            (db['version'], db['grpc_port'], db['ini_path'],
             db['frontend_disabled'], db['pcap_flag']),
            (None, None, None, False, False),
        )

        # Final log lines written right before daemon exit still sync fields.
        c_start = _START + 30
        _touch(log_path, '')
        proc_c = _netsimd(_DAEMON_B, c_start, self.root)
        self._tick(tracker, [proc_c], c_start + 1)
        _touch(
            log_path,
            'netsim I 09-25 22:37:37.000 netsimd.rs:451 - '
            'Netsim Version: 1.0.31, OS: linux, Arch: x86_64\n'
            'netsim I 09-25 22:37:37.100 netsimd.rs:700 - gRPC port: 44000\n'
            'netsim I 09-25 22:37:37.200 netsimd.rs:800 - '
            'Shutting down gracefully\n',
        )
        self._tick(tracker, [], c_start + 3)
        dc = tracker.daemons[(_DAEMON_B, c_start)]
        self.assertEqual((dc['version'], dc['grpc_port'], dc['exit_reason']),
                         ('1.0.31', 44000, 'sigterm_sigint'))

    def test_shared_log_races_ready_port_reconciliation_and_stats_version(self):
        import json
        art_dir, alt_art = self._mkdirs(os.path.join('android-test', 'netsimd'),
                                        'custom_art')
        log_path = os.path.join(art_dir, 'netsim_stderr.log')
        ini_path = os.path.join(self.root, 'netsim.ini')

        # 1) 1.0.31 `Artifacts:` line + `Wrote to INI` without a `gRPC port:` log
        # line reconciles port=43929 from netsim.ini before formatting READY,
        # and backfills version="1.0.30" from netsim_session_stats.json.
        _write_ini(ini_path, f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        _write_ini(os.path.join(alt_art, 'netsim_session_stats.json'),
                   json.dumps({
                       'version': '1.0.30',
                       'device_stats': []
                   }), _START + 1)
        _touch(
            log_path,
            f'netsim I 09-25 22:37:05.500 netsimd.rs:451 - Artifacts: "{alt_art}"\n'
            f'netsim I 09-25 22:37:05.600 netsimd.rs:744 - Wrote to INI file {ini_path}\n'
            'netsim I 09-25 22:37:08.000 netsimd.rs:800 - Shutting down gracefully\n',
        )
        tracker = self._tracker_for([self.root])
        tracker.enable_log = True
        proc_a = _netsimd(_DAEMON_A, _START, self.root)
        self._tick(tracker, [proc_a], _START + 5)
        da = tracker.daemons[(_DAEMON_A, _START)]
        self.assertEqual(da['artifact_dir'], alt_art)
        self.assertEqual(da['version'], '1.0.30')
        ready_evs = [
            ev for _, ev in tracker.timeline if ev.startswith('READY(')
        ]
        self.assertEqual(ready_evs,
                         [f'READY(pid={_DAEMON_A}, port=43929, ready_s=0.60s)'])

        # 2) Replacement DAEMON_B truncates shared log while DAEMON_A is still
        # in procs: DAEMON_A freezes its tailer, keeping port=43929 & sigterm.
        b_start = _START + 10
        _write_ini(ini_path, f'pid={_DAEMON_B}\ngrpc.port=43930\n', b_start + 1)
        _touch(
            log_path,
            'netsim I 09-25 22:37:16.000 netsimd.rs:700 - gRPC port: 43930\n',
        )
        proc_b = _netsimd(_DAEMON_B, b_start, self.root)
        self._tick(tracker, [proc_a, proc_b], b_start + 2)
        self._tick(tracker, [proc_b], b_start + 4)
        self.assertEqual((da['grpc_port'], da['exit_reason']),
                         (43929, 'sigterm_sigint'))

        # 3) Short-lived client-mode DAEMON_C appends Lock held + main loop
        # exited and exits while DAEMON_B stays alive: DAEMON_C records
        # CLIENT_MODE_SPAWN on exit, and DAEMON_B still reports killed_or_crashed
        # if it dies later while still owning netsim.ini.
        c_start = _START + 20
        proc_c = _netsimd(_DEAD_PID, c_start, self.root)
        self._tick(tracker, [proc_b, proc_c], c_start + 1)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('netsim W 09-25 22:37:26.500 netsimd.rs:500 - '
                    'Lock held by another process\n'
                    'netsim I 09-25 22:37:26.600 netsimd.rs:900 - '
                    'NetsimDaemon main loop exited\n')
        self._tick(tracker, [proc_b], c_start + 2)
        self.assertTrue(tracker.daemons[(_DEAD_PID,
                                         c_start)].get('client_mode_recorded'))
        self._tick(tracker, [], c_start + 4)
        self.assertEqual(tracker.daemons[(_DAEMON_B, b_start)]['exit_reason'],
                         'killed_or_crashed')

        # 4) Unknown port in READY formats as port=N/A.
        d_start = _START + 40
        os.remove(ini_path)
        _touch(
            log_path,
            'netsim I 09-25 22:37:46.000 netsimd.rs:744 - '
            'Wrote to INI file /nonexistent/netsim.ini\n',
        )
        self._tick(tracker, [_netsimd(_DAEMON_A, d_start, self.root)],
                   d_start + 2)
        self.assertIn(f'READY(pid={_DAEMON_A}, port=N/A, ready_s=1.00s)',
                      [ev for _, ev in tracker.timeline])

    @unittest.skipUnless(hasattr(os, 'major'), 'no os.major() on this host')
    def test_netsim_tracker_lock_holder_beats_conflicting_pid(self):
        # The INI's lock holder (B) wins over the daemon its pid= names (A).
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        _touch(ini + '.lock')
        lock_key = self.erm.get_file_lock_key(ini + '.lock')

        tracker = self._tracker_for([self.root], {lock_key: _DAEMON_B})
        self._tick(tracker, [
            _netsimd(_DAEMON_A, _START, self.root),
            _netsimd(_DAEMON_B, _START, self.root)
        ], _START + 10)

        a = tracker.daemons[(_DAEMON_A, _START)]
        b = tracker.daemons[(_DAEMON_B, _START)]
        self.assertEqual((b['ini_path'], b['grpc_port']), (ini, 43929))
        self.assertEqual((a['ini_path'], a['grpc_port']), (None, None))
        self.assertEqual(tracker.stale_inis, {})

    @unittest.skipUnless(hasattr(os, 'major'), 'no os.major() on this host')
    def test_netsim_tracker_ini_with_untracked_holder_ignores_pid(self):
        # An INI with lock holders pairs only through them: an untracked
        # holder (B) keeps the daemon its pid= names (A) from taking it,
        # and a held INI is not stale.
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        _touch(ini + '.lock')
        lock_key = self.erm.get_file_lock_key(ini + '.lock')

        tracker = self._tracker_for([self.root], {lock_key: _DAEMON_B})
        self._tick(tracker, [_netsimd(_DAEMON_A, _START, self.root)],
                   _START + 10)

        a = tracker.daemons[(_DAEMON_A, _START)]
        self.assertEqual((a['ini_path'], a['grpc_port']), (None, None))
        self.assertEqual(tracker.stale_inis, {})

    def test_netsim_tracker_instance_fallback_pairs_by_ini_name(self):
        # Without lock or pid evidence, `--instance 2` pairs with
        # netsim_2.ini; the default-instance INI has no daemon and is stale.
        ini1 = os.path.join(self.root, 'netsim.ini')
        ini2 = os.path.join(self.root, 'netsim_2.ini')
        _write_ini(ini1, 'grpc.port=1111\n', _START + 1)
        _write_ini(ini2, 'grpc.port=43929\n', _START + 1)
        proc = _netsimd(_DAEMON_A,
                        _START,
                        self.root,
                        cmdline=['/sdk/emulator/netsimd', '--instance', '2'])

        tracker = self._tracker_for([self.root])
        self._tick(tracker, [proc], _START + 10)
        d = tracker.daemons[(_DAEMON_A, _START)]
        self.assertEqual(d['instance_num'], 2)
        self.assertEqual(d['grpc_port'], 43929)
        self.assertEqual(d['ini_path'], ini2)
        self.assertIn(ini1, tracker.stale_inis)

    def test_netsim_tracker_pairs_same_instance_daemons_by_start_time(self):
        # Two older-netsim daemons (no pid=) with different TMPDIRs each pair
        # with the INI written after their own start. B's INI sorts first, so
        # taking INIs in path order would give A the wrong one.
        dir_a, dir_b = self._mkdirs('a', 'b')
        b_start = _START + 100
        _write_ini(os.path.join(dir_a, 'netsim.ini'), 'grpc.port=2222\n',
                   b_start + 1)
        _write_ini(os.path.join(dir_b, 'netsim.ini'), 'grpc.port=1111\n',
                   _START + 1)

        tracker = self._tracker_for([dir_a, dir_b])
        self._tick(tracker, [
            _netsimd(_DAEMON_A, _START, dir_b),
            _netsimd(_DAEMON_B, b_start, dir_a)
        ], b_start + 5)
        self.assertEqual(tracker.daemons[(_DAEMON_A, _START)]['grpc_port'],
                         1111)
        self.assertEqual(tracker.daemons[(_DAEMON_B, b_start)]['grpc_port'],
                         2222)
        self.assertEqual(tracker.stale_inis, {})

    def test_netsim_tracker_newest_daemon_takes_oldest_ini(self):
        # Both pid-less INIs were written after both daemons started, so
        # either daemon could own either one. B, the newer daemon, chooses
        # first and takes the INI written closest to its start.
        dir_a, dir_b = self._mkdirs('a', 'b')
        b_start = _START + 100
        _write_ini(os.path.join(dir_a, 'netsim.ini'), 'grpc.port=1111\n',
                   b_start + 50)
        _write_ini(os.path.join(dir_b, 'netsim.ini'), 'grpc.port=2222\n',
                   b_start + 1)

        tracker = self._tracker_for([dir_a, dir_b])
        self._tick(tracker, [
            _netsimd(_DAEMON_A, _START, dir_a),
            _netsimd(_DAEMON_B, b_start, dir_b)
        ], b_start + 60)
        self.assertEqual(tracker.daemons[(_DAEMON_A, _START)]['grpc_port'],
                         1111)
        self.assertEqual(tracker.daemons[(_DAEMON_B, b_start)]['grpc_port'],
                         2222)
        self.assertEqual(tracker.stale_inis, {})

    def test_netsim_tracker_ini_instance_fallback_without_proc_locks(self):
        # A Windows netsimd spawned by the qemu worker, with no /proc/locks.
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, 'grpc.port=43929\n', _START + 1)
        exe = r'C:\Android\Sdk\emulator\netsimd.exe'
        proc = _netsimd(_DAEMON_A,
                        _START,
                        self.root,
                        ppid=8000,
                        cmdline=[exe],
                        exe=exe)
        proc['environ'] = {'TMPDIR': self.root}
        parent = MagicMock()
        parent.name.return_value = 'qemu-system-x86_64.exe'
        real_process = self.erm.psutil.Process

        tracker = self._tracker_for([self.root])
        with patch.object(self.erm.psutil,
                          'Process',
                          side_effect=lambda pid: parent
                          if pid == 8000 else real_process(pid)):
            self._tick(tracker, [proc], _START + 10)
        d = tracker.daemons[(_DAEMON_A, _START)]
        self.assertEqual(d['spawner'], 'qemu')
        self.assertEqual(d['grpc_port'], 43929)
        self.assertEqual(d['ini_path'], ini)
        self.assertEqual(tracker.stale_inis, {})

    def test_deleted_stale_ini_is_pruned_from_dashboard(self):
        ini = os.path.join(self.root, 'netsim.ini')
        _write_ini(ini, f'pid={_DEAD_PID}\ngrpc.port=8554\n', _START - 60)
        tracker = self._tracker_for([self.root])

        self._tick(tracker, [], _START + 10)
        self.assertEqual(
            tracker.get_dashboard_snapshot(
                now_wall=float(_START + 10))['daemon_status_row'],
            'STALE_INI (netsim.ini)')

        os.remove(ini)
        self._tick(tracker, [], _START + 20)
        self.assertEqual(tracker.stale_inis, {})
        self.assertEqual(
            tracker.get_dashboard_snapshot(
                now_wall=float(_START + 20))['daemon_status_row'], 'N/A')

    def test_stale_ini_reached_through_symlink_alias_is_listed_once(self):
        real, = self._mkdirs('real')
        alias = os.path.join(self.root, 'alias')
        self._symlink_dir_or_skip(real, alias)
        _write_ini(os.path.join(real, 'netsim.ini'),
                   f'pid={_DEAD_PID}\ngrpc.port=8554\n', _START - 60)

        tracker = self._tracker_for([real])
        self._tick(tracker, [], _START + 10)
        tracker.custom_ini_dirs = [alias]
        self._tick(tracker, [], _START + 20)
        self.assertEqual(len(tracker.stale_inis), 1, tracker.stale_inis)
        self.assertEqual(self._count_events(tracker, 'STALE_INI('), 1)

        # Live daemon claims via alias, replacement claims via real -> 1 restart.
        _write_ini(os.path.join(real, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 21)
        self._tick(tracker, [_netsimd(_DAEMON_A, _START + 20, alias)],
                   _START + 25)
        self.assertEqual(tracker.stale_inis, {})
        tracker.custom_ini_dirs = [real]
        _write_ini(os.path.join(real, 'netsim.ini'),
                   f'pid={_DAEMON_B}\ngrpc.port=43930\n', _START + 31)
        self._tick(tracker, [_netsimd(_DAEMON_B, _START + 30, real)],
                   _START + 35)
        self.assertEqual(tracker.restart_count, 1)
        self.assertEqual(len(tracker._last_active_by_ini), 1)


class TestNetsimEmulatorSessions(unittest.TestCase):
    """How the netsim tracking follows emulators through a session.

    Tests that share a name with one in rust/src/netsim.rs check the same
    behavior.
    """

    def setUp(self):
        import emulator_resource_monitor
        self.erm = emulator_resource_monitor
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.tracker = self.erm.NetsimTracker(pcap_dir=None,
                                              enable_log=True,
                                              out_dir=self.root,
                                              os_family='linux')
        self.tracker.custom_ini_dirs = [self.root]
        for target, name, value in (
            (self.tracker, '_read_proc_locks', {}),
            (self.tracker.pcap_mgr, '_run_netsim_cli', ''),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _tick(self, now_mono, groups, session, procs=(), adb=None):
        """Runs one update at _START + now_mono.

        groups maps serial -> emulator group in this tick's host scan, and
        session maps storage key -> (serial, pid). adb lists the serials in
        groups as 'device' unless `adb` gives other serials, or a dict of
        serial -> state.
        """
        emulators = {
            key: {
                'name': key,
                'current_serial': serial,
                'current_pid': pid,
                'first_seen': float(now_mono),
            } for key, (serial, pid) in session.items()
        }
        group_pids = {
            serial: list(g.get('pids') or [g['primary_pid']])
            for serial, g in groups.items()
        }
        if isinstance(adb, dict):
            devices = dict(adb)
        else:
            devices = {
                serial: 'device' for serial in (groups if adb is None else adb)
            }
        with patch.object(self.erm, '_LAST_NETSIMD_PROCS', list(procs)), \
                patch.object(self.erm, '_LAST_EMU_GROUP_PIDS', group_pids), \
                patch.object(self.erm, '_LAST_EMU_GROUP_INFO', dict(groups)):
            self.tracker.update(emulators,
                                devices,
                                now_wall=float(_START + now_mono),
                                now_mono=float(now_mono))

    def _daemon_with_log(self, log):
        """A netsimd whose netsim_stderr.log under self.root holds `log`."""
        art_dir = os.path.join(self.root, 'android-test', 'netsimd')
        os.makedirs(art_dir, exist_ok=True)
        _touch(os.path.join(art_dir, 'netsim_stderr.log'), log)
        return _netsimd(_DAEMON_A, _START, self.root)

    def test_netsim_tracker_exited_emulator_disconnects(self):
        # The emulator was connected from t=10 and has left the host scan and
        # adb by t=40.
        key = 'pixel_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(10.0, {'emulator-5554': _emu_group(_WORKER_A, 'pixel')},
                   session)
        rec = self.tracker.emulator_records[key]
        rec.update(state='CONNECTED',
                   first_transport_ts=_START + 10.0,
                   connected_start_mono=10.0)

        # The first missed tick may be a scan hiccup, so the record is held.
        self._tick(40.0, {}, session)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))

        # The second settles it as of the first miss, so the hold adds no
        # connected time.
        self._tick(45.0, {}, session)
        self.assertEqual(rec['state'], 'DISCONNECTED')
        self.assertEqual(rec['disconnect_count'], 1)
        self.assertEqual(rec['connected_total_s'], 30.0)
        self.assertIsNone(rec['connected_start_mono'])
        self.assertIsNone(rec['adb_online_since_mono'])

        # Later ticks neither count the disconnect again nor add connected
        # time, and the CSV keeps reporting how the emulator ended.
        self._tick(50.0, {}, session)
        self.assertEqual(
            (rec['state'], rec['disconnect_count'], rec['connected_total_s']),
            ('DISCONNECTED', 1, 30.0))
        fields = self.tracker.get_csv_fields()
        self.assertEqual(fields[f'netsim_state_{key}'], 'DISCONNECTED')

    def test_netsim_tracker_exited_emulator_that_never_connected_fails(self):
        key = 'pixel_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(1.0, {'emulator-5554': _emu_group(_WORKER_A, 'pixel')},
                   session)
        self.assertEqual(self.tracker.emulator_records[key]['state'],
                         'CONNECTING')

        self._tick(2.0, {}, session)
        self.assertEqual(self.tracker.emulator_records[key]['state'],
                         'CONNECTING')
        self._tick(3.0, {}, session)
        rec = self.tracker.emulator_records[key]
        self.assertEqual(rec['state'], 'FAILED(worker_exit_no_connect)')
        self.assertEqual(rec['disconnect_count'], 0)

    def test_netsim_tracker_replaced_emulator_settles(self):
        # Another AVD takes the serial before adb lists it: the old record
        # settles as exited instead of taking over the new worker, which gets
        # its own record from the host scan.
        session = {'pixel_emulator-5554': ('emulator-5554', _WORKER_A)}
        self._tick(1.0,
                   {'emulator-5554': _emu_group(_WORKER_A, 'pixel', 'Pixel')},
                   session)

        self._tick(2.0,
                   {'emulator-5554': _emu_group(_WORKER_B, 'other', 'Other')},
                   session,
                   adb=())
        old = self.tracker.emulator_records['pixel_emulator-5554']
        self.assertEqual(
            (old['avd_id'], old['device_name'], old['worker_pid'],
             old['state']),
            ('pixel', 'Pixel', _WORKER_A, 'FAILED(worker_exit_no_connect)'))
        new = self.tracker.emulator_records['other_emulator-5554']
        self.assertEqual((new['avd_id'], new['device_name'], new['worker_pid'],
                          new['state']),
                         ('other', 'Other', _WORKER_B, 'CONNECTING'))

    def test_netsim_tracker_replaced_emulator_disconnects(self):
        # As above, but adb lists the new AVD, and the old emulator was
        # connected to a netsimd that stays up. The serial's socket and the
        # only daemon now belong to the new worker, so the old record
        # disconnects instead of staying connected.
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        procs = [self._daemon_with_log('')]
        session = {'pixel_emulator-5554': ('emulator-5554', _WORKER_A)}

        def tick(now_mono, worker, avd_id):
            conn = {
                'serial': 'emulator-5554',
                'pid': worker,
                'peer_port': 59604
            }
            with patch.object(self.tracker,
                              '_collect_loopback_grpc_sockets',
                              return_value={43929: [conn]}):
                self._tick(now_mono,
                           {'emulator-5554': _emu_group(worker, avd_id)},
                           session, procs)

        tick(10.0, _WORKER_A, 'pixel')
        old = self.tracker.emulator_records['pixel_emulator-5554']
        self.assertEqual(old['state'], 'CONNECTED')

        session['other_emulator-5554'] = ('emulator-5554', _WORKER_B)
        tick(40.0, _WORKER_B, 'other')
        self.assertEqual((old['avd_id'], old['worker_pid'], old['state'],
                          old['disconnect_count'], old['connected_total_s'],
                          old['adb_online_since_mono']),
                         ('pixel', _WORKER_A, 'DISCONNECTED', 1, 30.0, None))
        new = self.tracker.emulator_records['other_emulator-5554']
        self.assertEqual(
            (new['worker_pid'], new['state'], new['adb_online_since_mono']),
            (_WORKER_B, 'CONNECTED', 40.0))
        joined = self.tracker.daemons[(_DAEMON_A, _START)]['joined_emulators']
        self.assertEqual([r['storage_key'] for r in joined],
                         ['other_emulator-5554'])

    def test_netsim_tracker_host_only_emulator_that_exits_fails(self):
        # An emulator that exits before adb lists it is known only from the
        # host scan, and its record still settles.
        self._tick(1.0, {'emulator-5554': _emu_group(_WORKER_A, 'pixel')}, {},
                   adb=())
        key = 'pixel_emulator-5554'
        self.assertEqual(self.tracker.emulator_records[key]['state'],
                         'CONNECTING')

        self._tick(2.0, {}, {})
        self.assertEqual(self.tracker.emulator_records[key]['state'],
                         'CONNECTING')
        self._tick(3.0, {}, {})
        self.assertEqual(self.tracker.emulator_records[key]['state'],
                         'FAILED(worker_exit_no_connect)')

    def test_netsim_tracker_csv_covers_emulators_adb_does_not_list(self):
        # netsim can connect before adb lists the emulator, so the CSV has a
        # column for every record, not just the session's emulators.
        self._tick(1.0, {'emulator-5554': _emu_group(_WORKER_A, 'pixel')}, {},
                   adb=())
        fields = self.tracker.get_csv_fields()
        self.assertEqual(fields['netsim_state_pixel_emulator-5554'],
                         'CONNECTING')

    def test_netsim_tracker_host_only_key_matches_session_key(self):
        # With no AVD id known, the session keys the emulator by its display
        # name once adb lists it. The host-only record must already use that
        # key, or it would settle as exited and a second record would start.
        group = _emu_group(_WORKER_A, None, 'Pixel Test')
        key = 'Pixel Test_emulator-5554'
        self._tick(1.0, {'emulator-5554': group}, {}, adb=())
        rec = self.tracker.emulator_records[key]

        self._tick(2.0, {'emulator-5554': group},
                   {key: ('emulator-5554', _WORKER_A)})
        self.assertEqual(list(self.tracker.emulator_records), [key])
        self.assertIs(self.tracker.emulator_records[key], rec)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTING', 0))

    def test_netsim_tracker_never_downgrades_display_name(self):
        key = 'pixel_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        records = self.tracker.emulator_records

        # Before pid_<pid>.ini is written only the AVD id is known.
        no_ini = {'emulator-5554': _emu_group(_WORKER_A, 'pixel')}
        self._tick(1.0, no_ini, session)
        self.assertEqual(records[key]['device_name'], 'pixel')

        with_ini = {
            'emulator-5554': _emu_group(_WORKER_A, 'pixel', 'Pixel Test')
        }
        self._tick(2.0, with_ini, session)
        self.assertEqual(records[key]['device_name'], 'Pixel Test')

        # The emulator deletes the ini while the worker shuts down.
        self._tick(3.0, no_ini, session)
        self.assertEqual(records[key]['device_name'], 'Pixel Test')

    @unittest.skipUnless(os.path.exists('/proc/uptime'), 'needs /proc')
    def test_netsim_tracker_worker_start_comes_from_proc(self):
        # The worker's start time is the kernel's, whatever the tick's clock
        # says: here the tick is dated days before this process started.
        now = time.time()
        proc_ctime = self.erm.psutil.Process(os.getpid()).create_time()
        self._tick(10.0, {
            'emulator-5554': _emu_group(os.getpid(), 'pixel', ctime=proc_ctime)
        }, {})
        rec = self.tracker.emulator_records['pixel_emulator-5554']
        self.assertLessEqual(rec['worker_start_ts'], now)
        self.assertGreater(rec['worker_start_ts'], now - 3600)

    def test_new_emulator_ignores_daemon_log_from_before_its_start(self):
        # netsimd already served an earlier emulator. A new, still unnamed
        # emulator started at _START + 20 is the only live one, but the old
        # session's connection and chip lines predate it and are not its own.
        procs = [
            self._daemon_with_log(
                'netsim I 09-25 22:37:06.000 netsimd.rs:106 - '
                'Handling new connection for "", device OldPhone\n'
                'netsim I 09-25 22:37:06.000 service.rs:585 - '
                'DeviceActor: AddChip 1 (kind WIFI) to device 1 (OldPhone)\n')
        ]
        unnamed = _emu_group(_WORKER_A, None, ctime=_START + 20)
        self._tick(25.0, {'emulator-5554': unnamed},
                   {'emulator-5554': ('emulator-5554', _WORKER_A)}, procs)
        rec = self.tracker.emulator_records['emulator-5554']
        self.assertEqual((rec['state'], rec['chip_attach_s'], rec['chip_ids'],
                          rec['first_transport_ts'], rec['device_name']),
                         ('CONNECTING', {}, set(), None, None))
        self.assertEqual(rec['min_log_ts'], float(_START + 20))

    def test_netsim_tracker_keeps_first_daemon_ready_latency(self):
        # The emulator started 1 s before the netsimd it spawned, which wrote
        # its ini 1 s after it started: the emulator waited 2 s for a ready
        # daemon. Later ticks and a daemon restart keep that first latency.
        key = 'pixel_emulator-5554'
        groups = {
            'emulator-5554': _emu_group(_WORKER_A, 'pixel', ctime=_START - 1)
        }
        session = {key: ('emulator-5554', _WORKER_A)}
        daemon_a = self._daemon_with_log(
            'netsim I 09-25 22:37:06.000 netsimd.rs:757 - '
            'Wrote to INI file /tmp/netsim.ini\n')
        for now in (10.0, 12.0):
            self._tick(now, groups, session, procs=[daemon_a])
            self.assertAlmostEqual(
                self.tracker.emulator_records[key]['netsim_daemon_ready_s'],
                2.0)

        _touch(
            os.path.join(self.root, 'android-test', 'netsimd',
                         'netsim_stderr.log'),
            'netsim I 09-25 22:37:22.000 netsimd.rs:757 - '
            'Wrote to INI file /tmp/netsim.ini\n')
        daemon_b = _netsimd(_DAEMON_B, _START + 15, self.root)
        self._tick(20.0, groups, session, procs=[daemon_b])
        self.assertAlmostEqual(
            self.tracker.daemons[(_DAEMON_B,
                                  _START + 15)]['netsim_daemon_ready_s'], 2.0)
        self.assertAlmostEqual(
            self.tracker.emulator_records[key]['netsim_daemon_ready_s'], 2.0)

    def test_netsim_tracker_fallback_skips_exited_emulator_chips(self):
        # Once Phone exits, Glasses is the only live emulator: it takes the
        # chip nobody claims, but never Phone's.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        phone = _emu_group(_WORKER_A, 'phone', 'Phone')
        glasses = _emu_group(_WORKER_B, 'glasses', 'Glasses')
        session = {
            'phone_emulator-5554': ('emulator-5554', _WORKER_A),
            'glasses_emulator-5556': ('emulator-5556', _WORKER_B),
        }
        records = self.tracker.emulator_records

        self._tick(10.0, {
            'emulator-5554': phone,
            'emulator-5556': glasses
        }, session, procs)
        self.assertEqual(records['phone_emulator-5554']['chip_ids'], {'1'})
        self.assertEqual(records['glasses_emulator-5556']['chip_ids'], {'2'})
        glasses_first_ts = records['glasses_emulator-5556'][
            'first_transport_ts']
        self.assertIsNotNone(glasses_first_ts)

        self._tick(20.0, {'emulator-5556': glasses}, session, procs)
        # Phone missed one tick and may still be running, so Glasses is not
        # the only live emulator yet.
        phone_rec = records['phone_emulator-5554']
        self.assertEqual((phone_rec['state'], phone_rec['disconnect_count']),
                         ('CONNECTED', 0))
        self.assertEqual(records['glasses_emulator-5556']['chip_ids'], {'2'})

        self._tick(25.0, {'emulator-5556': glasses}, session, procs)
        self.assertEqual((phone_rec['state'], phone_rec['disconnect_count']),
                         ('DISCONNECTED', 1))
        glasses_rec = records['glasses_emulator-5556']
        self.assertEqual(glasses_rec['state'], 'CONNECTED')
        self.assertEqual(glasses_rec['chip_ids'], {'2', '3'})
        self.assertEqual(glasses_rec['first_transport_ts'], glasses_first_ts)

    def test_netsim_tracker_fallback_skips_exited_emulator_connections(self):
        # An emulator whose name is not known yet matches no device while
        # Phone runs. As the only live emulator it takes the unclaimed
        # connections and chips, and their device name, but none of Phone's.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        phone = _emu_group(_WORKER_A, 'phone', 'Phone')
        unnamed = _emu_group(_WORKER_B, None)
        session = {
            'phone_emulator-5554': ('emulator-5554', _WORKER_A),
            'emulator-5556': ('emulator-5556', _WORKER_B),
        }
        records = self.tracker.emulator_records

        self._tick(10.0, {
            'emulator-5554': phone,
            'emulator-5556': unnamed
        }, session, procs)
        rec = records['emulator-5556']
        self.assertEqual((rec['state'], rec['first_transport_ts']),
                         ('CONNECTING', None))

        # Phone is held for its first missed tick, so nothing moves yet.
        self._tick(20.0, {'emulator-5556': unnamed}, session, procs)
        self.assertEqual((rec['device_name'], rec['first_transport_ts']),
                         (None, None))

        self._tick(25.0, {'emulator-5556': unnamed}, session, procs)
        phone_first_ts = records['phone_emulator-5554']['first_transport_ts']
        self.assertEqual(rec['device_name'], 'Glasses')
        self.assertEqual(rec['chip_ids'], {'2', '3'})
        self.assertGreater(rec['first_transport_ts'], phone_first_ts)

    def test_netsim_tracker_host_scan_miss_while_adb_lists_keeps_state(self):
        # adb still lists the emulator, so a host-scan miss is a scan hiccup,
        # however long it lasts.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        key = 'phone_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(10.0,
                   {'emulator-5554': _emu_group(_WORKER_A, 'phone', 'Phone')},
                   session, procs)
        rec = self.tracker.emulator_records[key]
        self.assertEqual(rec['state'], 'CONNECTED')

        for now_mono in (20.0, 30.0, 40.0):
            self._tick(now_mono, {}, session, procs, adb=('emulator-5554',))
            self.assertEqual((rec['state'], rec['disconnect_count']),
                             ('CONNECTED', 0))

    def test_netsim_tracker_one_tick_miss_is_not_a_disconnect(self):
        # The emulator misses both the host scan and adb for one tick, then
        # comes back: it stays connected, with no disconnect and no lost time.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        phone = _emu_group(_WORKER_A, 'phone', 'Phone')
        key = 'phone_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(10.0, {'emulator-5554': phone}, session, procs)
        rec = self.tracker.emulator_records[key]

        self._tick(20.0, {}, session, procs)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))
        self.assertEqual(self.tracker.get_csv_fields()[f'netsim_state_{key}'],
                         'CONNECTED')

        self._tick(30.0, {'emulator-5554': phone}, session, procs)
        self.assertEqual((rec['state'], rec['disconnect_count'],
                          rec['connected_start_mono']), ('CONNECTED', 0, 10.0))

        # Seeing it again ended the miss run, so a new miss is held again.
        self._tick(40.0, {}, session, procs)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))

    def test_netsim_tracker_miss_run_restarts_when_adb_lists(self):
        # Only consecutive ticks missing from both the host scan and adb
        # count, and the exit is dated to the first of them.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        key = 'phone_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(10.0,
                   {'emulator-5554': _emu_group(_WORKER_A, 'phone', 'Phone')},
                   session, procs)
        rec = self.tracker.emulator_records[key]

        self._tick(20.0, {}, session, procs)
        self._tick(30.0, {}, session, procs, adb=('emulator-5554',))
        self._tick(40.0, {}, session, procs, adb=('emulator-5554',))
        self._tick(50.0, {}, session, procs)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))
        # While held, the summary counts connected time only up to the miss.
        summary = '\n'.join(self.tracker.format_summary_sections(55.0))
        self.assertIn('connected_time=40.0s', summary)

        self._tick(60.0, {}, session, procs)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('DISCONNECTED', 1))
        self.assertEqual(rec['connected_total_s'], 40.0)

    def test_netsim_tracker_stale_adb_offline_entry_does_not_hold(self):
        # adb can keep listing a killed emulator as 'offline'.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        key = 'phone_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        adb = {'emulator-5554': 'offline'}
        self._tick(10.0,
                   {'emulator-5554': _emu_group(_WORKER_A, 'phone', 'Phone')},
                   session, procs)
        rec = self.tracker.emulator_records[key]

        self._tick(20.0, {}, session, procs, adb=adb)
        self.assertEqual(rec['state'], 'CONNECTED')
        self._tick(30.0, {}, session, procs, adb=adb)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('DISCONNECTED', 1))
        self.assertEqual(rec['connected_total_s'], 10.0)

    def test_netsim_tracker_settled_emulator_stays_settled(self):
        # A daemon that later reports a startup timeout does not turn an
        # emulator that already disconnected into a launcher failure.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        key = 'phone_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(10.0,
                   {'emulator-5554': _emu_group(_WORKER_A, 'phone', 'Phone')},
                   session, procs)
        rec = self.tracker.emulator_records[key]
        self._tick(20.0, {}, session, procs)
        self._tick(30.0, {}, session, procs)
        self.assertEqual(rec['state'], 'DISCONNECTED')

        self.tracker.daemons[rec['daemon_key']]['exit_reason'] = (
            'startup_timeout')
        self._tick(40.0, {}, session, procs)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('DISCONNECTED', 1))

    def test_netsim_tracker_host_only_miss_run_resets_when_seen_again(self):
        # adb never lists this emulator (adb missing, or `adb devices` timing
        # out), so only the host scan ends its miss run.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        phone = {'emulator-5554': _emu_group(_WORKER_A, 'phone', 'Phone')}
        self._tick(10.0, phone, {}, procs, adb=())
        rec = self.tracker.emulator_records['phone_emulator-5554']
        self.assertEqual(rec['state'], 'CONNECTED')

        self._tick(20.0, {}, {}, procs, adb=())
        self._tick(30.0, phone, {}, procs, adb=())
        self._tick(40.0, {}, {}, procs, adb=())
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))
        self._tick(50.0, {}, {}, procs, adb=())
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('DISCONNECTED', 1))
        self.assertEqual(rec['connected_total_s'], 30.0)

    def test_netsim_tracker_adb_only_emulator_one_tick_adb_miss(self):
        # The host scan never sees this emulator, so adb alone decides, with
        # the same debounce: one failed `adb devices` is not a disconnect.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        key = 'pixel_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(10.0, {}, session, procs, adb=('emulator-5554',))
        rec = self.tracker.emulator_records[key]
        rec.update(state='CONNECTED',
                   first_transport_ts=_START + 10.0,
                   connected_start_mono=10.0)

        self._tick(20.0, {}, session, procs, adb=())
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))
        # adb listing it again ends the miss run, so the next miss is held.
        self._tick(30.0, {}, session, procs, adb=('emulator-5554',))
        self._tick(40.0, {}, session, procs, adb=())
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))
        self._tick(50.0, {}, session, procs, adb=())
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('DISCONNECTED', 1))
        self.assertEqual(rec['connected_total_s'], 30.0)

    def test_netsim_tracker_host_only_emulator_replaced_on_same_serial(self):
        # Another AVD takes the serial before adb lists either emulator, so
        # the old record settles at once instead of being held.
        self._tick(1.0, {'emulator-5554': _emu_group(_WORKER_A, 'pixel')}, {},
                   adb=())
        self._tick(2.0, {'emulator-5554': _emu_group(_WORKER_B, 'other')}, {},
                   adb=())
        records = self.tracker.emulator_records
        self.assertEqual(records['pixel_emulator-5554']['state'],
                         'FAILED(worker_exit_no_connect)')
        self.assertEqual(records['other_emulator-5554']['state'], 'CONNECTING')

    def test_qemu_next_supervisor_worker_transitions_preserve_session(self):
        procs = [self._daemon_with_log('')]
        log_path = os.path.join(self.root, 'android-test', 'netsimd',
                                'netsim_stderr.log')
        launcher_pid = 5_000_500
        key = 'phone_emulator-5554'

        # Startup: Tick 1 sees only supervisor launcher under bare serial.
        phone_launching = _emu_group(launcher_pid, None, ctime=_START - 1)
        phone_launching['pids'] = [launcher_pid]
        self._tick(5.0, {'emulator-5554': phone_launching},
                   {'emulator-5554': ('emulator-5554', launcher_pid)}, procs)
        self.assertIn('emulator-5554', self.tracker.emulator_records)

        # Tick 2 sees supervisor + worker under phone_emulator-5554.
        _touch(
            log_path,
            'netsim I 09-25 22:37:06.000 service.rs:585 - '
            'DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n',
        )
        phone_running = _emu_group(_WORKER_A, 'phone', 'Phone', ctime=_START)
        phone_running['pids'] = [launcher_pid, _WORKER_A]
        self._tick(10.0, {'emulator-5554': phone_running},
                   {key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertNotIn('emulator-5554', self.tracker.emulator_records)
        self.assertEqual(self.tracker.emulator_records[key]['state'],
                         'CONNECTED')
        self.assertEqual(self.tracker.emulator_records[key]['chip_ids'], {'1'})
        self.assertEqual(
            self.tracker.emulator_records[key]['chip_attach_s']['BT'],
            (1.0, False))

        # Shutdown: Worker exits first while supervisor launcher survives a tick
        # (and pid_<worker>.ini may already be gone, leaving avd_id=None).
        phone_shutting_down = _emu_group(launcher_pid, None, ctime=_START - 1)
        phone_shutting_down['pids'] = [launcher_pid]
        self._tick(20.0, {'emulator-5554': phone_shutting_down},
                   {key: ('emulator-5554', launcher_pid)}, procs)
        self._tick(22.0, {}, {key: ('emulator-5554', launcher_pid)}, procs)
        self._tick(24.0, {}, {key: ('emulator-5554', launcher_pid)}, procs)

        rec = self.tracker.emulator_records[key]
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('DISCONNECTED', 1))
        self.assertEqual(rec['chip_attach_s']['BT'], (1.0, False))

    def test_poll_sockets_fast_skips_settled_and_non_connecting_records(self):
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        procs = [self._daemon_with_log('')]
        self._tick(1.0, {}, {}, procs)
        self.tracker.emulator_records['old_emulator-5554'] = {
            'storage_key': 'old_emulator-5554',
            'serial': 'emulator-5554',
            'state': 'FAILED(worker_exit_no_connect)',
            'no_netsim': False,
            'worker_start_ts': float(_START),
            'first_transport_ts': None,
            'netsim_connect_s': None,
        }
        self.tracker.emulator_records['disabled_emulator-5554'] = {
            'storage_key': 'disabled_emulator-5554',
            'serial': 'emulator-5554',
            'state': 'DISABLED',
            'no_netsim': True,
            'worker_start_ts': float(_START),
            'first_transport_ts': None,
            'netsim_connect_s': None,
        }
        self.tracker.emulator_records['connected_emulator-5554'] = {
            'storage_key': 'connected_emulator-5554',
            'serial': 'emulator-5554',
            'state': 'CONNECTED',
            'no_netsim': False,
            'worker_start_ts': float(_START),
            'first_transport_ts': None,
            'netsim_connect_s': None,
        }
        self.tracker.emulator_records['live_emulator-5554'] = {
            'storage_key': 'live_emulator-5554',
            'serial': 'emulator-5554',
            'state': 'CONNECTING',
            'no_netsim': False,
            'worker_start_ts': float(_START + 2),
            'first_transport_ts': None,
            'netsim_connect_s': None,
        }
        conn = {'serial': 'emulator-5554', 'pid': _WORKER_B, 'peer_port': 59604}
        with patch.object(self.tracker,
                          '_collect_loopback_grpc_sockets',
                          return_value={43929: [conn]}):
            self.tracker.poll_sockets_fast(now_wall=float(_START + 5))
        self.assertIsNone(self.tracker.emulator_records['old_emulator-5554']
                          ['first_transport_ts'])
        self.assertIsNone(
            self.tracker.emulator_records['disabled_emulator-5554']
            ['first_transport_ts'])
        self.assertIsNone(
            self.tracker.emulator_records['connected_emulator-5554']
            ['first_transport_ts'])
        self.assertEqual(
            self.tracker.emulator_records['live_emulator-5554']
            ['first_transport_ts'], float(_START + 5))
        self.assertEqual(
            self.tracker.emulator_records['live_emulator-5554']
            ['netsim_connect_s'], 3.0)

    def test_fast_poll_stops_once_connecting_emulator_has_transport(self):
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        procs = [self._daemon_with_log('')]
        self._tick(1.0, {}, {}, procs)
        self.tracker.emulator_records['live_emulator-5554'] = {
            'storage_key': 'live_emulator-5554',
            'serial': 'emulator-5554',
            'state': 'CONNECTING',
            'no_netsim': False,
            'worker_start_ts': float(_START + 2),
            'first_transport_ts': None,
            'netsim_connect_s': None,
        }
        self.assertTrue(self.tracker.has_any_connecting())
        conn = {'serial': 'emulator-5554', 'pid': _WORKER_A, 'peer_port': 59604}
        with patch.object(self.tracker,
                          '_collect_loopback_grpc_sockets',
                          return_value={43929: [conn]}):
            self.tracker.poll_sockets_fast(now_wall=float(_START + 5))
        self.assertEqual(
            self.tracker.emulator_records['live_emulator-5554']
            ['first_transport_ts'], float(_START + 5))
        self.assertFalse(self.tracker.has_any_connecting())

        # /proc start time is read only on a new record or changed worker PID.
        group = _emu_group(_WORKER_B, 'pixel', 'Pixel', ctime=_START + 3)
        session = {'pixel_emulator-5556': ('emulator-5556', _WORKER_B)}
        with patch.object(self.erm,
                          'get_proc_precise_start_time',
                          return_value=float(_START + 3)) as precise_start:
            self._tick(10.0, {'emulator-5556': group}, session, procs)
            self._tick(15.0, {'emulator-5556': group}, session, procs)
        self.assertEqual(precise_start.call_count, 1)

    def test_netsim_tracker_uses_primary_ctime_for_worker_start_ts(self):
        # On non-Linux hosts where /proc/<pid>/stat is absent, get_process_stats
        # populates primary_ctime; worker_start_ts and netsim_connect_s must use
        # it rather than falling back to the current poll tick's wall time.
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        procs = [self._daemon_with_log('')]
        group = _emu_group(_WORKER_A, 'pixel', 'Pixel', ctime=_START + 2)
        group['grpc_endpoint'] = '127.0.0.1:43929'
        conn = {'serial': 'emulator-5554', 'pid': _WORKER_A, 'peer_port': 59604}
        with patch.object(self.erm,
                          'get_proc_precise_start_time',
                          side_effect=lambda pid, fallback_create_time=None:
                          fallback_create_time), \
                patch.object(self.tracker,
                             '_collect_loopback_grpc_sockets',
                             return_value={43929: [conn]}):
            self._tick(10.0, {'emulator-5554': group},
                       {'pixel_emulator-5554': ('emulator-5554', _WORKER_A)},
                       procs)
        rec = self.tracker.emulator_records['pixel_emulator-5554']
        self.assertEqual(rec['worker_start_ts'], float(_START + 2))
        self.assertEqual(rec['netsim_connect_s'], 8.0)

    def test_netsim_tracker_worker_exit_with_first_transport_settles_to_failed(
            self):
        # An emulator that records a transport timestamp while still CONNECTING
        # must transition to STALLED after 50s if still live without active
        # sockets or chips (UT-5), and settle to FAILED(worker_exit_no_connect)
        # in both joined and unjoined paths when the worker exits (UT-3, UT-4).
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        conn_only_log = ('netsim I 09-25 22:37:06.000 netsimd.rs:106 - '
                         'Handling new connection for "", device Pixel\n')
        procs = [self._daemon_with_log(conn_only_log)]
        group = _emu_group(_WORKER_A, 'pixel', 'Pixel')
        group['grpc_endpoint'] = '127.0.0.1:43929'
        key = 'pixel_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        self._tick(1.0, {'emulator-5554': group}, session, procs)
        rec = self.tracker.emulator_records[key]
        self.assertIsNotNone(rec['first_transport_ts'])
        self.assertEqual(rec['state'], 'CONNECTING')

        # UT-5: Live emulator with first_transport_ts set and no active sockets
        # or chips transitions to STALLED after 50s.
        self._tick(52.0, {'emulator-5554': group}, session, procs)
        self.assertEqual(rec['state'], 'STALLED')

        # Unjoined path: worker is gone for 2 ticks while first_transport_ts is
        # already populated -> FAILED(worker_exit_no_connect).
        self._tick(53.0, {}, session, procs)
        self.assertEqual(rec['state'], 'STALLED')
        self._tick(54.0, {}, session, procs)
        self.assertEqual(rec['state'], 'FAILED(worker_exit_no_connect)')

        # Joined path: an adb-listed emulator whose host worker exited while
        # joined to the single active daemon and holding first_transport_ts.
        self.tracker.emulator_records.clear()
        key2 = 'other_emulator-5556'
        session2 = {key2: ('emulator-5556', _WORKER_B)}
        self._tick(55.0, {}, session2, procs, adb=('emulator-5556',))
        rec2 = self.tracker.emulator_records[key2]
        self.assertIsNotNone(rec2['first_transport_ts'])
        self.assertEqual(rec2['state'], 'FAILED(worker_exit_no_connect)')

    def test_netsim_tracker_cli_socket_failure_retains_cache_until_clear(self):
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        procs = [self._daemon_with_log('')]
        group = _emu_group(_WORKER_A, 'pixel', 'Pixel')
        group['grpc_endpoint'] = '127.0.0.1:43929'
        key = 'pixel_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        lsof_row = f'p{_WORKER_A}\nn127.0.0.1:59604->127.0.0.1:43929\n'
        self.tracker.socket_os_family = 'darwin'

        def tick_with_cli(now_mono, cli_out, active_group=group, sess=session):
            self.tracker.socket_cli_runner = lambda _args: cli_out
            with patch.object(
                    self.erm,
                    '_psutil_loopback_rows',
                    side_effect=self.erm.psutil.AccessDenied(_WORKER_A)):
                self._tick(now_mono, {'emulator-5554': active_group}, sess,
                           procs)

        # Tick 1: lsof succeeds -> CONNECTED.
        tick_with_cli(10.0, lsof_row)
        rec = self.tracker.emulator_records[key]
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))

        # Tick 2: lsof times out / fails (None) -> retains cached socket for
        # live PID _WORKER_A, stays CONNECTED with 0 disconnects.
        tick_with_cli(20.0, None)
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('CONNECTED', 0))

        # Tick 3: lsof succeeds with empty output ('') -> socket truly closed,
        # transitions to DISCONNECTED.
        tick_with_cli(30.0, '')
        self.assertEqual(
            (rec['state'], rec['disconnect_count'], rec['connected_total_s']),
            ('DISCONNECTED', 1, 20.0))

        # Tick 4 & 5 (UT-7): Reconnect _WORKER_A at t=40, then replace with
        # _WORKER_B at t=50 while lsof times out (None) -> stale cached socket
        # for exited PID _WORKER_A is pruned rather than retained.
        tick_with_cli(40.0, lsof_row)
        self.assertEqual(rec['state'], 'CONNECTED')
        group_b = _emu_group(_WORKER_B, 'pixel', 'Pixel')
        group_b['grpc_endpoint'] = '127.0.0.1:43929'
        tick_with_cli(50.0,
                      None,
                      active_group=group_b,
                      sess={key: ('emulator-5554', _WORKER_B)})
        self.assertEqual(rec['state'], 'CONNECTING')

    def test_netsim_tracker_same_avd_restart_resets_state_and_merges_bare_serial(
            self):
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        log = ('netsim I 09-25 22:37:06.000 netsimd.rs:106 - '
               'Handling new connection for "", device Pixel\n'
               'netsim I 09-25 22:37:06.000 service.rs:585 - '
               'DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Pixel)\n')
        procs = [self._daemon_with_log(log)]
        key = 'pixel_emulator-5554'

        # Run 1 (PID _WORKER_A) connects at t=10 and exits by t=30.
        g1 = _emu_group(_WORKER_A, 'pixel', 'Pixel', ctime=_START + 2)
        g1['grpc_endpoint'] = '127.0.0.1:43929'
        self._tick(10.0, {'emulator-5554': g1},
                   {key: ('emulator-5554', _WORKER_A)}, procs)
        rec1 = self.tracker.emulator_records[key]
        self.assertEqual(rec1['state'], 'CONNECTED')
        self.assertIn('BT', rec1['chip_attach_s'])
        self._tick(20.0, {}, {key: ('emulator-5554', _WORKER_A)}, procs)
        self._tick(30.0, {}, {key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertEqual((rec1['state'], rec1['disconnect_count'],
                          rec1['connected_total_s']), ('DISCONNECTED', 1, 10.0))

        # Run 2 (PID _WORKER_B) of the same AVD starts on emulator-5554 at
        # t=60 (>=50s after Run 1). First tick has no AVD name yet (bare serial
        # key), and records a socket transport at t=61; second tick resolves AVD
        # 'pixel' and migrates the bare-serial record into 'pixel_emulator-5554'
        # while preserving Run 1's disconnect_count/connected_total_s and
        # ignoring Run 1's pre-restart log entries.
        g2_bare = _emu_group(_WORKER_B, None, None, ctime=_START + 60)
        g2_bare['grpc_endpoint'] = '127.0.0.1:43929'
        self._tick(60.0, {'emulator-5554': g2_bare}, {
            key: ('emulator-5554', _WORKER_A),
            'emulator-5554': ('emulator-5554', _WORKER_B),
        }, procs)
        self.assertIn('emulator-5554', self.tracker.emulator_records)
        self.tracker.emulator_records['emulator-5554']['first_transport_ts'] = (
            float(_START + 61))

        g2_named = _emu_group(_WORKER_B, 'pixel', 'Pixel', ctime=_START + 60)
        g2_named['grpc_endpoint'] = '127.0.0.1:43929'
        self._tick(62.0, {'emulator-5554': g2_named},
                   {key: ('emulator-5554', _WORKER_B)}, procs)
        self.assertNotIn('emulator-5554', self.tracker.emulator_records)
        rec2 = self.tracker.emulator_records[key]
        self.assertEqual(rec2['worker_pid'], _WORKER_B)
        self.assertEqual(rec2['worker_start_ts'], float(_START + 60))
        self.assertEqual(rec2['first_transport_ts'], float(_START + 61))
        self.assertEqual(rec2['netsim_connect_s'], 1.0)
        self.assertEqual(rec2['state'], 'CONNECTING')
        self.assertEqual(rec2['chip_attach_s'], {})
        self.assertEqual(rec2['disconnect_count'], 1)
        self.assertEqual(rec2['connected_total_s'], 10.0)

        # Direct same-key restart (PID _WORKER_B -> 5000600) at t=70 also resets
        # per-run state and first_seen_mono so it starts in CONNECTING rather
        # than immediately stalling.
        g3 = _emu_group(5000600, 'pixel', 'Pixel', ctime=_START + 70)
        g3['grpc_endpoint'] = '127.0.0.1:43929'
        self._tick(70.0, {'emulator-5554': g3},
                   {key: ('emulator-5554', 5000600)}, procs)
        self.assertEqual(rec2['worker_pid'], 5000600)
        self.assertEqual(rec2['state'], 'CONNECTING')
        self.assertIsNone(rec2['first_transport_ts'])
        self.assertEqual(rec2['disconnect_count'], 1)
        self.assertEqual(rec2['connected_total_s'], 10.0)

    def test_same_avd_restart_after_held_miss_counts_connected_time_to_the_miss(
            self):
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        phone_a = _emu_group(_WORKER_A, 'phone', 'Phone')
        phone_b = _emu_group(_WORKER_B, 'phone', 'Phone')
        key = 'phone_emulator-5554'
        self._tick(10.0, {'emulator-5554': phone_a},
                   {key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertEqual(self.tracker.emulator_records[key]['state'],
                         'CONNECTED')
        # Run 1 misses t=20 (held), then run 2 restarts on the key at t=30.
        self._tick(20.0, {}, {key: ('emulator-5554', _WORKER_A)}, procs)
        self._tick(30.0, {'emulator-5554': phone_b},
                   {key: ('emulator-5554', _WORKER_B)}, procs)
        rec = self.tracker.emulator_records[key]
        self.assertEqual(rec['worker_pid'], _WORKER_B)
        self.assertEqual(rec['disconnect_count'], 1)
        self.assertEqual(rec['connected_total_s'], 10.0)

        # Bare-serial restart after a held miss also ends Run 2 at the miss.
        self.assertEqual(rec['state'], 'CONNECTED')
        self._tick(40.0, {}, {key: ('emulator-5554', _WORKER_B)}, procs)
        pid_c = 5_000_500
        phone_c_bare = _emu_group(pid_c, None, None)
        self._tick(50.0, {'emulator-5554': phone_c_bare}, {
            key: ('emulator-5554', _WORKER_B),
            'emulator-5554': ('emulator-5554', pid_c),
        }, procs)
        phone_c_named = _emu_group(pid_c, 'phone', 'Phone')
        self._tick(55.0, {'emulator-5554': phone_c_named},
                   {key: ('emulator-5554', pid_c)}, procs)
        rec_c = self.tracker.emulator_records[key]
        self.assertEqual(rec_c['disconnect_count'], 2)
        self.assertEqual(rec_c['connected_total_s'], 20.0)

    def test_settled_emulator_does_not_leak_wifi_or_duplicate_pcaps(self):
        wifi_shared_log = (
            'netsim I 09-25 22:37:06.000 service.rs:585 - '
            'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n'
            'netsim I 09-25 22:37:07.000 ap_actor.rs:212 - '
            'ApActor: Associated 02:15:B2:00:00:00\n'
            'netsim I 09-25 22:37:08.000 service.rs:585 - '
            'DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Glasses)\n')
        procs = [self._daemon_with_log(wifi_shared_log)]
        phone = _emu_group(_WORKER_A, 'phone', 'Phone')
        glasses = _emu_group(_WORKER_B, 'glasses', 'Glasses')
        session = {
            'phone_emulator-5554': ('emulator-5554', _WORKER_A),
            'glasses_emulator-5556': ('emulator-5556', _WORKER_B),
        }
        records = self.tracker.emulator_records

        self._tick(10.0, {
            'emulator-5554': phone,
            'emulator-5556': glasses
        }, session, procs)
        self.assertIsNone(records['glasses_emulator-5556']['wifi_associated_s'])

        self._tick(20.0, {'emulator-5556': glasses}, session, procs)
        self._tick(25.0, {'emulator-5556': glasses}, session, procs)
        self.assertEqual(records['phone_emulator-5554']['state'],
                         'DISCONNECTED')
        self.assertIsNone(records['glasses_emulator-5556']['wifi_associated_s'])

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-test', 'netsimd')
            pcaps_dir = os.path.join(art_dir, 'pcaps')
            os.makedirs(pcaps_dir, exist_ok=True)
            with open(os.path.join(pcaps_dir, 'netsim-1-Phone-BLUETOOTH.pcap'),
                      'wb') as f:
                f.write(b'\xd4' * 64)
            _touch(os.path.join(art_dir, 'netsim_stderr.log'),
                   _SHARED_DAEMON_LOG)
            out_dir = os.path.join(tmpdir, 'out')
            tracker2 = self.erm.NetsimTracker(pcap_dir='',
                                              enable_log=True,
                                              out_dir=out_dir,
                                              os_family='linux')
            tracker2.custom_ini_dirs = [tmpdir]
            procs2 = [
                _netsimd(_DAEMON_A,
                         _START,
                         tmpdir,
                         uid=os.getuid() if hasattr(os, 'getuid') else None)
            ]
            session2 = {'phone_emulator-5554': ('emulator-5554', _WORKER_A)}
            with patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(tracker2.pcap_mgr, '_run_netsim_cli', return_value=''):
                self.tracker = tracker2
                self._tick(10.0, {'emulator-5554': phone}, session2, procs2)
                self.assertEqual(len(tracker2.pcap_mgr.copied_files), 1)

                self._tick(20.0, {}, session2, procs2)
                self._tick(30.0, {}, session2, procs2)
                self.assertEqual(
                    tracker2.emulator_records['phone_emulator-5554']['state'],
                    'DISCONNECTED')
                self.assertEqual(len(tracker2.pcap_mgr.copied_files), 1)
                self.assertFalse(
                    any('shared' in p for p in tracker2.pcap_mgr.copied_files))

    def test_settled_record_does_not_hide_live_dashboard_row(self):
        self._tick(10.0,
                   {'emulator-5554': _emu_group(_WORKER_B, 'other', 'Other')},
                   {'other_emulator-5554': ('emulator-5554', _WORKER_B)})
        self.tracker.emulator_records['pixel_emulator-5554'] = {
            'storage_key': 'pixel_emulator-5554',
            'serial': 'emulator-5554',
            'worker_pid': _WORKER_A,
            'state': 'DISCONNECTED',
            'disconnect_count': 1,
            'chip_attach_s': {},
            'unknown_chip_count': 0,
            'netsim_connect_s': None,
            'ambiguous': False,
        }
        snap = self.tracker.get_dashboard_snapshot(now_wall=float(_START +
                                                                  10.0))
        self.assertEqual(snap['emulator_rows'].get('emulator-5554'),
                         'netsim: CONNECTING (no-chips) | disc=0')

    def test_settled_pid_reuse_bystander_ready_disabled_and_dashboard_order(
            self):
        # 1) Settled record with group_pids must not hijack an unnamed new
        # emulator reusing a PID; same-AVD same-PID reuse with new ctime resets.
        procs = [self._daemon_with_log(_SHARED_DAEMON_LOG)]
        alpha_key = 'alpha_emulator-5554'
        reused_pid = 5_000_500
        alpha = _emu_group(_WORKER_A, 'alpha', 'Phone', ctime=_START)
        alpha['pids'] = [_WORKER_A, reused_pid]
        self._tick(10.0, {'emulator-5554': alpha},
                   {alpha_key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertEqual(self.tracker.emulator_records[alpha_key]['state'],
                         'CONNECTED')
        self._tick(20.0, {}, {alpha_key: ('emulator-5554', _WORKER_A)}, procs)
        self._tick(30.0, {}, {alpha_key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertEqual(self.tracker.emulator_records[alpha_key]['state'],
                         'DISCONNECTED')

        unnamed_new = _emu_group(reused_pid, None, None, ctime=_START + 40)
        self._tick(40.0, {'emulator-5554': unnamed_new},
                   {alpha_key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertEqual(self.tracker.emulator_records[alpha_key]['state'],
                         'DISCONNECTED')
        self.assertIn('emulator-5554', self.tracker.emulator_records)
        self.assertEqual(
            self.tracker.emulator_records['emulator-5554']['state'],
            'CONNECTING')

        self.tracker.emulator_records.pop('emulator-5554', None)
        alpha_reused = _emu_group(_WORKER_A,
                                  'alpha',
                                  'NewAlpha',
                                  ctime=_START + 60)
        self._tick(60.0, {'emulator-5554': alpha_reused},
                   {alpha_key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertEqual(self.tracker.emulator_records[alpha_key]['state'],
                         'CONNECTING')

        # 2) Bystander single-daemon fallback ready_s resets on real daemon join,
        # and 3) no_netsim clears daemon_key and leaves all_joined_emulators.
        art_dir = os.path.join(self.root, 'android-test', 'netsimd')
        _write_ini(os.path.join(self.root, 'netsim.ini'),
                   f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
        _touch(
            os.path.join(art_dir, 'netsim_stderr.log'),
            'netsim I 09-25 22:37:05.020 server.rs:100 - Wrote to INI file /tmp/netsim.ini\n',
        )
        bystander = _netsimd(_DAEMON_A, _START, self.root)
        p60_key = 'phone60_emulator-5560'
        p60 = _emu_group(_WORKER_B, 'phone60', 'Phone60', ctime=_START + 10)
        self.tracker = self.erm.NetsimTracker(pcap_dir=None,
                                              enable_log=True,
                                              out_dir=self.root,
                                              os_family='linux')
        self.tracker.custom_ini_dirs = [self.root]
        with patch.object(self.tracker, '_read_proc_locks', return_value={}):
            self._tick(10.0, {'emulator-5560': p60},
                       {p60_key: ('emulator-5560', _WORKER_B)}, [bystander])
            self.assertAlmostEqual(
                self.tracker.emulator_records[p60_key]['netsim_daemon_ready_s'],
                0.02,
                places=5)

            _write_ini(os.path.join(self.root, 'netsim_2.ini'),
                       f'pid={_DAEMON_B}\ngrpc.port=42747\n', _START + 11)
            _touch(
                os.path.join(art_dir, 'netsim_2_stderr.log'),
                'netsim I 09-25 22:37:15.420 server.rs:100 - Wrote to INI file /tmp/netsim_2.ini\n',
            )
            own_daemon = _netsimd(
                _DAEMON_B,
                _START + 10,
                self.root,
                cmdline=['/sdk/emulator/netsimd', '--instance', '2'])
            p60_ep = dict(p60, grpc_endpoint='127.0.0.1:42747')
            self._tick(12.0, {'emulator-5560': p60_ep},
                       {p60_key: ('emulator-5560', _WORKER_B)},
                       [bystander, own_daemon])
            self.assertAlmostEqual(
                self.tracker.emulator_records[p60_key]['netsim_daemon_ready_s'],
                0.42,
                places=5)

            p60_disabled = dict(p60_ep, no_netsim=True)
            self._tick(14.0, {'emulator-5560': p60_disabled},
                       {p60_key: ('emulator-5560', _WORKER_B)},
                       [bystander, own_daemon])
            self.assertEqual(self.tracker.emulator_records[p60_key]['state'],
                             'DISABLED')
            self.assertIsNone(
                self.tracker.emulator_records[p60_key]['daemon_key'])
            self.assertEqual(
                self.tracker.daemons[(_DAEMON_B,
                                      _START + 10)]['all_joined_emulators'], [])

        # 4) Settled dashboard row picks the record with largest first_seen_mono.
        self.tracker.emulator_records['alpha_emulator-5570'] = {
            'storage_key': 'alpha_emulator-5570',
            'serial': 'emulator-5570',
            'first_seen_mono': 10.0,
            'state': 'FAILED(worker_exit_no_connect)',
            'disconnect_count': 0,
            'chip_attach_s': {},
            'unknown_chip_count': 0,
            'netsim_connect_s': None,
            'ambiguous': False,
        }
        self.tracker.emulator_records['beta_emulator-5570'] = {
            'storage_key': 'beta_emulator-5570',
            'serial': 'emulator-5570',
            'first_seen_mono': 50.0,
            'state': 'DISCONNECTED',
            'disconnect_count': 1,
            'chip_attach_s': {},
            'unknown_chip_count': 0,
            'netsim_connect_s': None,
            'ambiguous': False,
        }
        snap = self.tracker.get_dashboard_snapshot(now_wall=float(_START +
                                                                  60.0))
        self.assertEqual(snap['emulator_rows'].get('emulator-5570'),
                         'netsim: DISCONNECTED (no-chips) | disc=1')

    def test_physical_adb_device_skipped_and_exited_daemon_state_released(self):
        wifi_log = ('netsim I 09-25 22:37:06.000 service.rs:585 - '
                    'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n'
                    'netsim I 09-25 22:37:07.000 ap_actor.rs:212 - '
                    'ApActor: Associated 02:15:B2:00:00:00\n')
        procs = [self._daemon_with_log(wifi_log)]
        phone = _emu_group(_WORKER_A, 'phone', 'Phone', ctime=_START)
        session = {
            'phone_emulator-5554': ('emulator-5554', _WORKER_A),
            'Pixel_8_3A151FDJH000XX': ('3A151FDJH000XX', None),
            'adb_tcp_192.168.1.50:5555': ('192.168.1.50:5555', None),
        }
        adb = {
            'emulator-5554': 'device',
            '3A151FDJH000XX': 'device',
            '192.168.1.50:5555': 'device',
        }
        d_key = (_DAEMON_A, float(_START))
        self.tracker.pcap_mgr.checked_emulators.add((d_key, 'Phone'))
        self.tracker.pcap_mgr.enabled_by_monitor[(d_key, 1)] = {
            'netsim_bin': 'netsim',
            'grpc_port': 43929,
            'device_name': 'Phone',
            'row': {
                'id': 1,
                'device_name': 'Phone',
                'chip_kind': 'WIFI'
            },
        }
        self._tick(10.0, {'emulator-5554': phone}, session, procs, adb=adb)

        self.assertEqual(list(self.tracker.emulator_records),
                         ['phone_emulator-5554'])
        self.assertEqual(
            self.tracker.emulator_records['phone_emulator-5554']
            ['wifi_associated_s'], 2.0)
        csv = self.tracker.get_csv_fields()
        self.assertNotIn('netsim_state_Pixel_8_3A151FDJH000XX', csv)

        self._tick(20.0, {'emulator-5554': phone}, session, [], adb=adb)
        d = self.tracker.daemons[d_key]
        self.assertTrue(d['exited'])
        self.assertEqual(
            (d['tailer'], d['stats_cache'], d['environ'], d['capture_rows'],
             d['all_joined_emulators']),
            (None, None, {}, [], []),
        )
        self.assertEqual(self.tracker.pcap_mgr.checked_emulators, set())
        self.assertEqual(self.tracker.pcap_mgr.enabled_by_monitor, {})
        self.assertEqual(self.tracker.pcap_mgr.last_harvest_by_daemon, {})

    def test_daemon_restart_preserves_exit_reason_and_ready_latency(self):
        ini_path = os.path.join(self.root, 'netsim.ini')
        art_dir = os.path.join(self.root, 'android-test', 'netsimd')
        os.makedirs(art_dir, exist_ok=True)
        log_path = os.path.join(art_dir, 'netsim_stderr.log')

        _write_ini(ini_path, f'pid={_DAEMON_A}\ngrpc.port=8554\n', _START + 1)
        _touch(
            log_path,
            'netsim I 09-25 22:37:06.000 server.rs:100 - Wrote to INI file /tmp/netsim.ini\n'
            'netsim I 09-25 22:37:08.000 server.rs:110 - Shutting down gracefully\n'
        )
        phone = _emu_group(_WORKER_A, 'phone', 'Phone', ctime=_START - 1)
        key = 'phone_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        daemon_a = _netsimd(_DAEMON_A, _START, self.root)
        daemon_b = _netsimd(_DAEMON_B, _START + 15, self.root)

        self._tick(10.0, {'emulator-5554': phone}, session, procs=[daemon_a])
        self.assertEqual(
            self.tracker.daemons[(_DAEMON_A, _START)]['netsim_daemon_ready_s'],
            1.0)
        self.assertEqual(
            self.tracker.emulator_records[key]['netsim_daemon_ready_s'], 2.0)

        # DAEMON_B spawns at _START + 15 before DAEMON_A finishes exiting and
        # before truncating DAEMON_A's log; by t=20 DAEMON_A has exited and
        # DAEMON_B has truncated netsim_stderr.log and written its own INI + ready line.
        self._tick(16.0, {'emulator-5554': phone},
                   session,
                   procs=[daemon_a, daemon_b])

        _write_ini(ini_path, f'pid={_DAEMON_B}\ngrpc.port=8555\n', _START + 17)
        _touch(
            log_path,
            'netsim I 09-25 22:37:22.000 server.rs:100 - Wrote to INI file /tmp/netsim.ini\n'
        )
        self._tick(20.0, {'emulator-5554': phone}, session, procs=[daemon_b])

        self.assertEqual(
            self.tracker.daemons[(_DAEMON_A, _START)]['exit_reason'],
            'sigterm_sigint')
        self.assertEqual(
            self.tracker.daemons[(_DAEMON_A, _START)]['netsim_daemon_ready_s'],
            1.0)
        self.assertEqual(
            self.tracker.daemons[(_DAEMON_B,
                                  _START + 15)]['netsim_daemon_ready_s'], 2.0)
        self.assertEqual(
            self.tracker.emulator_records[key]['netsim_daemon_ready_s'], 2.0)

    def test_daemon_restart_and_settled_records_update_connection_state(self):
        art_dir = os.path.join(self.root, 'android-test', 'netsimd')
        os.makedirs(art_dir, exist_ok=True)
        log_path = os.path.join(art_dir, 'netsim_stderr.log')
        _touch(log_path, _SHARED_DAEMON_LOG)

        phone = _emu_group(_WORKER_A, 'phone', 'Phone')
        key = 'phone_emulator-5554'
        session = {key: ('emulator-5554', _WORKER_A)}
        daemon_a = _netsimd(_DAEMON_A, _START, self.root)

        self._tick(10.0, {'emulator-5554': phone}, session, procs=[daemon_a])
        rec = self.tracker.emulator_records[key]
        self.assertEqual(rec['state'], 'CONNECTED')
        self.assertEqual(self.tracker.get_csv_fields()['netsim_devices'], 1)

        _touch(log_path, '')
        daemon_b = _netsimd(_DAEMON_B, _START + 15, self.root)
        self._tick(20.0, {'emulator-5554': phone}, session, procs=[daemon_b])
        self.assertEqual((rec['state'], rec['disconnect_count']),
                         ('DISCONNECTED', 1))
        self.assertEqual(rec['connected_total_s'], 10.0)
        self.assertEqual(rec['chip_attach_s'], {})
        self.assertEqual(self.tracker.get_csv_fields()['netsim_devices'], 0)

    def test_wifi_association_recorded_after_same_avd_worker_restart(self):
        art_dir = os.path.join(self.root, 'android-test', 'netsimd')
        os.makedirs(art_dir, exist_ok=True)
        log_path = os.path.join(art_dir, 'netsim_stderr.log')
        _touch(
            log_path, 'netsim I 09-25 22:37:06.000 service.rs:585 - '
            'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n'
            'netsim I 09-25 22:37:07.000 ap_actor.rs:212 - '
            'ApActor: Associated 02:15:B2:00:00:00\n')
        procs = [_netsimd(_DAEMON_A, _START, self.root)]
        key = 'phone_emulator-5554'
        phone_a = _emu_group(_WORKER_A, 'phone', 'Phone', ctime=_START)

        self._tick(10.0, {'emulator-5554': phone_a},
                   {key: ('emulator-5554', _WORKER_A)}, procs)
        self.assertEqual(
            self.tracker.emulator_records[key]['wifi_associated_s'], 2.0)

        _touch(
            log_path, 'netsim I 09-25 22:37:06.000 service.rs:585 - '
            'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n'
            'netsim I 09-25 22:37:07.000 ap_actor.rs:212 - '
            'ApActor: Associated 02:15:B2:00:00:00\n'
            'netsim I 09-25 22:37:26.000 service.rs:585 - '
            'DeviceActor: AddChip 2 (kind WIFI) to device 2 (Phone)\n'
            'netsim I 09-25 22:37:28.000 ap_actor.rs:212 - '
            'ApActor: Associated 02:15:B2:00:00:01\n')
        phone_b = _emu_group(_WORKER_B, 'phone', 'Phone', ctime=_START + 20)
        self._tick(25.0, {'emulator-5554': phone_b},
                   {key: ('emulator-5554', _WORKER_B)}, procs)
        self.assertEqual(
            self.tracker.emulator_records[key]['wifi_associated_s'], 3.0)

    def test_session_stats_keep_refining_approximate_chips(self):
        # netsim 1.0.24 logs kind-less AddChip lines; netsim_session_stats.json
        # supplies the kinds. Once WIFI is matched exactly, BT is still an
        # estimate and must be refined by a later stats file, while the exact
        # WIFI time is never replaced by an estimate.
        import json
        procs = [
            self._daemon_with_log(
                'netsim I 09-25 22:37:06.000 service.rs:579 - '
                'DeviceActor: AddChip Phone (EMULATOR, Phone) to device Phone\n'
                'netsim I 09-25 22:37:16.000 service.rs:579 - '
                'DeviceActor: AddChip Phone (EMULATOR, Phone) to device Phone\n'
            )
        ]
        stats_path = os.path.join(self.root, 'android-test', 'netsimd',
                                  'netsim_session_stats.json')

        def write_stats(mtime, wifi_s, bt_s):
            _write_ini(
                stats_path,
                json.dumps({
                    'device_stats': [{
                        'device_id': 1
                    }],
                    'radio_stats': [{
                        'device_id': 1,
                        'kind': 'WIFI',
                        'duration_secs': str(wifi_s)
                    }, {
                        'device_id': 1,
                        'kind': 'BLUETOOTH_LOW_ENERGY',
                        'duration_secs': str(bt_s)
                    }]
                }), mtime)

        key = 'phone_emulator-5554'
        phone = {'emulator-5554': _emu_group(_WORKER_A, 'phone', 'Phone')}
        session = {key: ('emulator-5554', _WORKER_A)}
        write_stats(_START + 30, 29, 5)
        self._tick(35.0, phone, session, procs)
        rec = self.tracker.emulator_records[key]
        self.assertEqual(rec['chip_attach_s'], {
            'WIFI': (1.0, False),
            'BT': (25.0, True)
        })

        write_stats(_START + 40, 30, 29)
        self._tick(45.0, phone, session, procs)
        self.assertEqual(rec['chip_attach_s'], {
            'WIFI': (1.0, False),
            'BT': (11.0, False)
        })

    def test_log_tailer_bounds_accumulated_event_lists(self):
        # Verifies NetsimLogTailer caps accumulated event/connection/chip lists
        # to _MAX_TAILER_EVENTS while retaining the most recent entries.
        import emulator_resource_monitor as erm
        tailer = erm.NetsimLogTailer(None)
        limit = erm._MAX_TAILER_EVENTS
        for i in range(limit + 25):
            tailer.feed_line(
                f'I0330 00:00:01.000Z src/lib.rs:1] Handling new connection for BLUETOOTH on device dev-{i}',
                now_utc=1000.0 + i,
            )
            tailer.feed_line(
                f'I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Create device dev-{i}',
                now_utc=1000.0 + i,
            )
            tailer.feed_line(
                f'I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: AddChip {i} (kind BLUETOOTH, name "bt-{i}") to device {i} (dev-{i})',
                now_utc=1000.0 + i,
            )
            tailer.feed_line(
                f'I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Removed chip {i} (kind BLUETOOTH) from device {i}',
                now_utc=1000.0 + i,
            )
            tailer.feed_line(
                f'I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Device dev-{i} is empty, auto-deleting',
                now_utc=1000.0 + i,
            )
            tailer.feed_line(
                'I0330 00:00:01.000Z src/lib.rs:1] Wrote to INI file /tmp/netsim.ini',
                now_utc=1000.0 + i,
            )

        self.assertEqual(len(tailer.connections), limit)
        self.assertEqual(tailer.connections[-1][2], f'dev-{limit + 24}')
        self.assertEqual(len(tailer.created_devices), limit)
        self.assertEqual(tailer.created_devices[-1][1], f'dev-{limit + 24}')
        self.assertEqual(len(tailer.chip_adds), limit)
        self.assertEqual(tailer.chip_adds[-1]['device_name'],
                         f'dev-{limit + 24}')
        self.assertEqual(len(tailer.chip_removals), limit)
        self.assertEqual(tailer.chip_removals[-1]['chip_id'], str(limit + 24))
        self.assertEqual(len(tailer.empty_devices), limit)
        self.assertEqual(tailer.empty_devices[-1][1], f'dev-{limit + 24}')
        self.assertEqual(len(tailer.events), limit)

    def test_snapshot_process_state_returns_isolated_copies(self):
        # Verifies _snapshot_process_state() returns detached copies under
        # _PROCESS_STATE_LOCK so concurrent callers cannot mutate live state.
        import emulator_resource_monitor as erm
        with erm._PROCESS_STATE_LOCK:
            erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {101, 102}}
            erm._LAST_EMU_GROUP_INFO = {'emulator-5554': {'avd_name': 'pixel'}}
            erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (101, 10.0)}
            erm._LAST_NETSIMD_PROCS = [{'pid': 900, 'create_time': 5.0}]
        daemons, pids, info, primary = erm._snapshot_process_state()
        pids['emulator-5554'].add(999)
        info['emulator-5554']['avd_name'] = 'mutated'
        primary['emulator-5554'] = (0, 0.0)
        daemons[0]['pid'] = 0

        daemons2, pids2, info2, primary2 = erm._snapshot_process_state()
        self.assertEqual(pids2['emulator-5554'], {101, 102})
        self.assertEqual(info2['emulator-5554']['avd_name'], 'pixel')
        self.assertEqual(primary2['emulator-5554'], (101, 10.0))
        self.assertEqual(daemons2[0]['pid'], 900)


class TestRound5Fixes(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)

    def test_pcap_manager_trusts_sticky_root_tmp_ini_and_rejects_unsafe_paths(
            self):
        import stat
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            out_dir = os.path.join(tmpdir, 'out')
            fake_tmp = os.path.join(tmpdir, 'simulated_slash_tmp')
            os.makedirs(fake_tmp, exist_ok=True)
            ini_path = os.path.join(fake_tmp, 'netsim.ini')
            with open(ini_path, 'w', encoding='utf-8') as f:
                f.write('grpc.port=50051\npid=9100\n')

            my_uid = os.getuid() if hasattr(os, 'getuid') else 1000
            real_stat = os.stat
            real_lstat = os.lstat

            def make_stat_result(mode, uid, base_st):
                vals = list(base_st)
                vals[0] = mode
                vals[4] = uid
                return os.stat_result(vals)

            canon_fake_tmp = os.path.realpath(fake_tmp)
            canon_ini_path = os.path.join(canon_fake_tmp, 'netsim.ini')
            canon_art_dir = os.path.realpath(art_dir)
            trusted_ancestors = set()
            for anc_root in (art_dir, canon_art_dir, os.path.dirname(fake_tmp),
                             os.path.dirname(canon_fake_tmp)):
                trusted_ancestors.update(erm._iter_path_prefixes(anc_root))
            base_dir_st = real_stat(fake_tmp)
            base_file_st = real_lstat(ini_path)
            dir_mode_uid = [stat.S_IFDIR | stat.S_ISVTX | 0o777, 0]
            file_mode_uid = [stat.S_IFREG | 0o600, my_uid]

            def stat_side_effect(p, *args, **kwargs):
                if p in (fake_tmp, canon_fake_tmp):
                    return make_stat_result(dir_mode_uid[0], dir_mode_uid[1],
                                            base_dir_st)
                if p in trusted_ancestors:
                    return make_stat_result(stat.S_IFDIR | 0o700, my_uid,
                                            base_dir_st)
                return real_stat(p, *args, **kwargs)

            def lstat_side_effect(p, *args, **kwargs):
                if p in (ini_path, canon_ini_path):
                    if file_mode_uid[0] is None:
                        raise FileNotFoundError(p)
                    return make_stat_result(file_mode_uid[0], file_mode_uid[1],
                                            base_file_st)
                if p in (fake_tmp, canon_fake_tmp):
                    return make_stat_result(dir_mode_uid[0], dir_mode_uid[1],
                                            base_dir_st)
                if p in trusted_ancestors:
                    return make_stat_result(stat.S_IFDIR | 0o700, my_uid,
                                            base_dir_st)
                return real_lstat(p, *args, **kwargs)

            cli_out = (
                'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                '1  | Pixel_8     | WIFI      | off   | 00:00:00  | 0       | 0\n'
            )
            joined = [{
                'serial': 'emulator-5554',
                'device_name': 'Pixel_8',
                'state': 'CONNECTED',
                'chip_ids': {'1'},
            }]

            def run_case():
                mgr = erm.NetsimPcapManager(pcap_dir='',
                                            max_mb=1,
                                            out_dir=out_dir)
                calls = []
                mgr._run_netsim_cli = lambda b, p, a, **kw: (calls.append(
                    list(a)) or (cli_out
                                 if a[:2] == ['capture', 'list'] else cli_out))
                daemon = {
                    'daemon_key': (9100, 1000.0),
                    'pid': 9100,
                    'start_wall': 1000.0,
                    'grpc_port': 50051,
                    'ini_path': ini_path,
                    'artifact_dir': art_dir,
                    'same_user': True,
                    'exited': False,
                }
                mgr.poll_and_manage_daemon(daemon, joined, now_mono=10.0)
                return calls

            with patch.object(erm, '_current_uid', return_value=my_uid), \
                    patch('os.stat', side_effect=stat_side_effect), \
                    patch('os.lstat', side_effect=lstat_side_effect):
                # Root-owned sticky /tmp with user-owned regular netsim.ini -> allowed.
                calls = run_case()
                self.assertIn(['capture', 'list'], calls)
                self.assertIn(
                    ['capture', 'patch', 'on', '1', 'Pixel_8', 'WIFI'], calls)

                # Foreign-owned netsim.ini in /tmp -> rejected.
                file_mode_uid[:] = [stat.S_IFREG | 0o600, my_uid + 1]
                self.assertEqual(run_case(), [])

                # Symlink netsim.ini in /tmp -> rejected.
                file_mode_uid[:] = [stat.S_IFLNK | 0o777, my_uid]
                self.assertEqual(run_case(), [])

                # Missing netsim.ini in world-writable sticky /tmp -> rejected.
                file_mode_uid[:] = [None, my_uid]
                self.assertEqual(run_case(), [])

                # Root-owned world-writable 0o777 WITHOUT sticky bit -> rejected.
                dir_mode_uid[:] = [stat.S_IFDIR | 0o777, 0]
                file_mode_uid[:] = [stat.S_IFREG | 0o600, my_uid]
                self.assertEqual(run_case(), [])

                # Root-owned non-world-writable 0o755 with owned netsim.ini -> allowed.
                dir_mode_uid[:] = [stat.S_IFDIR | 0o755, 0]
                self.assertIn(['capture', 'list'], run_case())

    def test_pre_adb_quickboot_does_not_trigger_bogus_boot_tracking(self):
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        tags_seen = []
        dev_seq = [
            {},
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        host_val = _host_tuple(
            {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
            qemu_stats={'emulator-5554': ('25.0%', '1800.0MB')})

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_dashboard(*args, **kwargs):
            for _, (_, tags, *_) in args[4].items():
                tags_seen.append(tags)

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 6.0
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=host_val), \
                patch('emulator_resource_monitor.get_guest_stats',
                      return_value=(_guest_model('Pixel 8'), False, '20.0%',
                                    '15.0%',
                                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)',
                                    '0.0 MB')), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=fake_dashboard), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emu = mock_save.call_args[0][0]['emulators']['Pixel_8_emulator-5554']
        self.assertTrue(emu['boot_completed'])
        self.assertFalse(emu['track_boot_stats'])
        self.assertNotIn('boot_finished_time', emu)
        self.assertEqual(emu['boot_log'], [])
        self.assertTrue(all('RECORDING BOOT STATS' not in t for t in tags_seen),
                        tags_seen)

    def test_offline_to_device_already_booted_completes_boot_without_log(self):
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        dev_seq = [
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        host_val = _host_tuple(
            {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
            qemu_stats={'emulator-5554': ('25.0%', '1800.0MB')})

        def fake_guest(serial, known_model, is_booting, status):
            if status != 'device':
                return (f'Unknown ({status})', is_booting, f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})')
            return (_guest_model('Pixel 8'), False, '20.0%', '15.0%',
                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 6.0
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=host_val), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emu = mock_save.call_args[0][0]['emulators']['Pixel_8_emulator-5554']
        self.assertTrue(emu['boot_completed'])
        self.assertFalse(emu['track_boot_stats'])
        self.assertNotIn('boot_finished_time', emu)
        self.assertEqual(emu['boot_log'], [])

    def test_initial_guest_timeout_preserves_cold_boot_tracking(self):
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        dev_seq = [
            {},
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            if fn is erm.get_guest_stats and tick[0] == 1:
                res = fn(*args, **kwargs)
                f.done.side_effect = lambda: tick[0] > 1
                f.result.return_value = res
                return f
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 6.0
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('concurrent.futures.wait',
                      side_effect=lambda fs, **kw: (set(), set())), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=_host_tuple()), \
                patch('emulator_resource_monitor.get_guest_stats',
                      return_value=(_guest_model('Pixel 8'), True, '55.0%',
                                    '45.0%',
                                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)',
                                    '0.0 MB')), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emu = mock_save.call_args[0][0]['emulators']['emulator-5554']
        self.assertFalse(emu['boot_completed'])
        self.assertTrue(emu['track_boot_stats'])
        self.assertGreaterEqual(len(emu['boot_log']), 1)

    def test_stale_adb_offline_does_not_poison_later_emulator_boot_window(self):
        import emulator_resource_monitor as erm

        tick = [0]
        times = [100.0, 200.0, 1200.0, 1206.0]
        dev_seq = [
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        host_seq = [
            _host_tuple(),
            _host_tuple(),
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={'emulator-5554': ('80.0%', '1500.0MB')}),
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={'emulator-5554': ('85.0%', '1600.0MB')}),
        ]

        def fake_guest(serial, known_model, is_booting, status):
            if status != 'device':
                return (f'Unknown ({status})', is_booting, f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})')
            return (_guest_model('Pixel 8'), True, '60.0%', '50.0%',
                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=lambda: host_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic',
                      side_effect=lambda: times[min(tick[0], 3)]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary_data = mock_save.call_args[0][0]
        emus = summary_data['emulators']
        self.assertEqual(list(emus.keys()), ['Pixel_8_emulator-5554'])
        emu = emus['Pixel_8_emulator-5554']
        self.assertTrue(emu['track_boot_stats'])
        self.assertGreaterEqual(len(emu['boot_log']), 1)
        self.assertIn(emu['boot_log'][0][0], ('+0s', '+6s'))
        self.assertNotIn('emu_cpu_tot_Pixel_8_emulator-5554',
                         summary_data['time_series'][0])

    def test_clean_exit_clears_group_pids_and_rejects_reused_helper_pid(self):
        import emulator_resource_monitor as erm

        tick = [0]
        dev_seq = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {},
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]

        def fake_host():
            idx = min(tick[0], 4)
            if idx in (0, 1):
                erm._LAST_EMU_GROUP_PIDS['emulator-5554'] = {1000, 1001}
                erm._LAST_EMU_GROUP_INFO['emulator-5554'] = {
                    'primary_pid': 1001,
                    'primary_ctime': 10.5,
                    'avd_id': 'Pixel_7',
                    'avd_name': 'Pixel_7',
                    'pids': {1000, 1001},
                    'member_start_times': {
                        1000: 10.0,
                        1001: 10.5
                    },
                }
                erm._LAST_PRIMARY_PROC_ID['emulator-5554'] = (1001, 10.5)
                return _host_tuple(
                    {'emulator-5554': 1001}, {'emulator-5554': 'Pixel_7'},
                    qemu_stats={'emulator-5554': ('20.0%', '1500.0MB')})
            if idx == 2:
                erm._LAST_EMU_GROUP_PIDS.clear()
                erm._LAST_EMU_GROUP_INFO.clear()
                erm._LAST_PRIMARY_PROC_ID.clear()
                return _host_tuple()
            erm._LAST_EMU_GROUP_PIDS['emulator-5554'] = {1000}
            erm._LAST_EMU_GROUP_INFO['emulator-5554'] = {
                'primary_pid': 1000,
                'primary_ctime': 50.0,
                'avd_id': None,
                'avd_name': None,
                'pids': {1000},
                'member_start_times': {
                    1000: 50.0
                },
            }
            erm._LAST_PRIMARY_PROC_ID['emulator-5554'] = (1000, 50.0)
            return _host_tuple(
                {'emulator-5554': 1000}, {},
                qemu_stats={'emulator-5554': ('300.0%', '2000.0MB')})

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            if tick[0] == 5:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 4)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host), \
                patch('emulator_resource_monitor.get_guest_stats',
                      return_value=(_guest_model('Pixel 7'), False, '15.0%',
                                    '10.0%',
                                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)',
                                    '0.0 MB')), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emus = mock_save.call_args[0][0]['emulators']
        self.assertEqual(sorted(emus.keys()),
                         ['Pixel_7_emulator-5554', 'emulator-5554'])
        self.assertTrue(emus['Pixel_7_emulator-5554'].get('retired'))
        self.assertEqual(emus['Pixel_7_emulator-5554']['qemu_cpu_max'], 20.0)
        self.assertEqual(emus['emulator-5554']['qemu_cpu_max'], 300.0)

    def test_bare_serial_unnamed_pid_replacement_does_not_merge_into_later_avd(
            self):
        host = [
            _host_tuple({'emulator-5554': 1000}, {},
                        qemu_stats={'emulator-5554': ('300.0%', '1000.0MB')}),
            _host_tuple({'emulator-5554': 1000}, {},
                        qemu_stats={'emulator-5554': ('300.0%', '1000.0MB')}),
            _host_tuple({'emulator-5554': 2000}, {},
                        qemu_stats={'emulator-5554': ('20.0%', '2000.0MB')}),
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={'emulator-5554': ('25.0%', '2100.0MB')}),
        ]
        summary_data, _ = _run_main_sync(
            host, ['Generic', 'Generic', 'Pixel 8', 'Pixel 8'])
        emus = summary_data['emulators']
        self.assertEqual(sorted(emus.keys()),
                         ['Pixel_8_emulator-5554', 'emulator-5554'])
        self.assertTrue(emus['emulator-5554'].get('retired'))
        self.assertEqual(emus['emulator-5554']['qemu_cpu_max'], 300.0)
        self.assertEqual(emus['Pixel_8_emulator-5554']['qemu_cpu_max'], 25.0)
        self.assertEqual(emus['Pixel_8_emulator-5554']['qemu_cpu_iterations'],
                         2)
        rows = list(summary_data['time_series'])
        self.assertEqual(rows[0]['qemu_cpu_emulator-5554'], 300.0)
        self.assertNotIn('qemu_cpu_Pixel_8_emulator-5554', rows[0])
        self.assertEqual(rows[1]['qemu_cpu_Pixel_8_emulator-5554'], 20.0)
        self.assertEqual(rows[2]['qemu_cpu_Pixel_8_emulator-5554'], 25.0)

    def test_qemu_next_fishtank_and_older_launcher_shutdown_shifts_preserve_netsim(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-test', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            log_path = os.path.join(art_dir, 'netsim_stderr.log')
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    'netsim I 09-25 22:37:06.000 service.rs:1 - '
                    'gRPC listening on 127.0.0.1:43929\n'
                    'netsim I 09-25 22:37:06.000 service.rs:2 - '
                    'DeviceActor: AddChip 0 (kind WIFI, name "wifi-0") to device 1 (Pixel)\n'
                )
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            _write_ini(ini_path, f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       _START + 1)

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            daemon_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'ANDROID_TMP': tmpdir,
                    'USER': 'test'
                },
                'ppid': 1,
                'uid': os.getuid() if hasattr(os, 'getuid') else None,
            }
            launcher_pid = 5_000_310
            worker_pid = 5_000_311
            fishtank_pid = 5_000_312
            key = 'pixel_emulator-5554'

            full_group = {
                'primary_pid': worker_pid,
                'primary_ctime': float(_START),
                'avd_id': 'pixel',
                'display_name': 'Pixel',
                'avd_name': 'pixel',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {launcher_pid, worker_pid, fishtank_pid},
                'member_start_times': {
                    launcher_pid: float(_START - 5),
                    worker_pid: float(_START),
                    fishtank_pid: float(_START + 5),
                },
            }
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={
                                     43929: [{
                                         'serial': 'emulator-5554',
                                         'peer_port': 51000
                                     }]
                                 }), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', [daemon_proc]), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS', {
                        'emulator-5554': {
                            launcher_pid, worker_pid, fishtank_pid
                        }
                    }), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO',
                                 {'emulator-5554': full_group}):
                tracker.update(
                    {
                        key: {
                            'name': 'Pixel',
                            'current_serial': 'emulator-5554',
                            'current_pid': worker_pid,
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 10),
                    now_mono=10.0)

            rec = tracker.emulator_records[key]
            self.assertEqual(rec['state'], 'CONNECTED')
            self.assertEqual(rec['chip_attach_s'], {'WIFI': (1.0, False)})

            # Worker exits and deletes pid.ini; fishtank (started +5s after
            # worker, avd_name=None) survives while daemon exits so the session
            # settles to DISCONNECTED cleanly.
            fishtank_only = {
                'primary_pid': fishtank_pid,
                'primary_ctime': float(_START + 5),
                'avd_id': None,
                'display_name': None,
                'avd_name': None,
                'grpc_endpoint': None,
                'no_netsim': False,
                'pids': {launcher_pid, fishtank_pid},
                'member_start_times': {
                    launcher_pid: float(_START - 5),
                    fishtank_pid: float(_START + 5),
                },
            }
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={}), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', []), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS', {
                        'emulator-5554': {launcher_pid, fishtank_pid}
                    }), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO',
                                 {'emulator-5554': fishtank_only}):
                tracker.update(
                    {
                        key: {
                            'name': 'Pixel',
                            'current_serial': 'emulator-5554',
                            'current_pid': fishtank_pid,
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'offline'},
                    now_wall=float(_START + 20),
                    now_mono=20.0)

            self.assertEqual(list(tracker.emulator_records.keys()), [key])
            self.assertEqual(rec['state'], 'DISCONNECTED')
            self.assertEqual(rec['disconnect_count'], 1)
            self.assertEqual(rec['chip_attach_s'], {'WIFI': (1.0, False)})

            # Record is now settled (was_settled=True) while launcher
            # (_START - 5) lingers for one more tick before exiting.
            launcher_only = {
                'primary_pid': launcher_pid,
                'primary_ctime': float(_START - 5),
                'avd_id': None,
                'display_name': None,
                'avd_name': None,
                'grpc_endpoint': None,
                'no_netsim': False,
                'pids': {launcher_pid},
                'member_start_times': {
                    launcher_pid: float(_START - 5)
                },
            }
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={}), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', []), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS',
                                 {'emulator-5554': {launcher_pid}}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO',
                                 {'emulator-5554': launcher_only}):
                tracker.update(
                    {
                        key: {
                            'name': 'Pixel',
                            'current_serial': 'emulator-5554',
                            'current_pid': launcher_pid,
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'offline'},
                    now_wall=float(_START + 22),
                    now_mono=22.0)

            self.assertEqual(list(tracker.emulator_records.keys()), [key])
            self.assertEqual(rec['state'], 'DISCONNECTED')
            self.assertEqual(rec['disconnect_count'], 1)
            self.assertEqual(rec['chip_attach_s'], {'WIFI': (1.0, False)})

    def test_physical_usb_and_unknown_prefixed_avd_refresh_summary_name(self):
        import emulator_resource_monitor as erm

        tick = [0]
        dev_seq = [
            {
                'HT4A12345678': 'unauthorized',
                'emulator-5554': 'device'
            },
            {
                'HT4A12345678': 'device',
                'emulator-5554': 'device'
            },
            {
                'HT4A12345678': 'device',
                'emulator-5554': 'device'
            },
            {
                'HT4A12345678': 'offline',
                'emulator-5554': 'offline'
            },
        ]
        host_val = _host_tuple(
            {'emulator-5554': 1000}, {'emulator-5554': 'Unknown_Custom_AVD'},
            qemu_stats={'emulator-5554': ('20.0%', '1500.0MB')})

        def fake_guest(serial, known_model, is_booting, status):
            if status != 'device':
                return (known_model if known_model and 'Android' in known_model
                        else f'Unknown ({status})', is_booting,
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})')
            if serial == 'HT4A12345678' and tick[0] == 1:
                return (
                    'Pixel 9 Pro | Android 16 (API 36) | Build: BP2A | Res: N/A',
                    True, '30.0%', '20.0%',
                    '40.0% (3.2GB/8.0GB, 0 errs, 10% data)', '0.0 MB')
            return (
                'Pixel 9 Pro | Android 16 (API 36) | Build: BP2A | Res: 1080x2400',
                False, '20.0%', '10.0%',
                '40.0% (3.2GB/8.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=host_val), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emus = mock_save.call_args[0][0]['emulators']
        self.assertIn('Res: 1080x2400', emus['HT4A12345678']['name'])
        self.assertNotIn('Unknown', emus['HT4A12345678']['name'])
        self.assertIn('Unknown_Custom_AVD | Pixel 9 Pro',
                      emus['Unknown_Custom_AVD_emulator-5554']['name'])

    def test_poll_daemon_log_preserves_matched_ini_path_on_exit(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-test', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            log_path = os.path.join(art_dir, 'netsim_stderr.log')
            unmounted_ini = os.path.join(tmpdir, 'missing_mount', 'netsim.ini')
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    f'netsim I 09-25 22:37:06.000 server.rs:1 - Wrote to INI file {unmounted_ini}\n'
                )
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            _write_ini(ini_path, f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       _START + 1)

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            daemon_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'ANDROID_TMP': tmpdir,
                    'USER': 'test'
                },
                'ppid': 1,
                'uid': os.getuid() if hasattr(os, 'getuid') else None,
            }
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={}), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', [daemon_proc]), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS', {}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO', {}):
                tracker.update({}, {},
                               now_wall=float(_START + 10),
                               now_mono=10.0)
            self.assertEqual(
                tracker.daemons[(_DAEMON_A, float(_START))]['ini_path'],
                ini_path)

            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={}), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', []), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS', {}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO', {}):
                tracker.update({}, {},
                               now_wall=float(_START + 20),
                               now_mono=20.0)

            d = tracker.daemons[(_DAEMON_A, float(_START))]
            self.assertEqual(d['ini_path'], ini_path)
            self.assertEqual(d['exit_reason'], 'killed_or_crashed')

    def test_log_tailer_detects_inplace_truncation_when_new_log_exceeds_offset(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, 'netsim_stderr.log')
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    'netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 43929\n'
                )
            old_tailer = erm.NetsimLogTailer(log_path)
            active_tailer = erm.NetsimLogTailer(log_path)
            self.assertTrue(
                old_tailer.poll(now_utc=float(_START + 5), allow_reset=True))
            self.assertTrue(
                active_tailer.poll(now_utc=float(_START + 5), allow_reset=True))
            self.assertEqual(old_tailer.grpc_port_logged, 43929)
            self.assertEqual(active_tailer.grpc_port_logged, 43929)
            ino_before = os.stat(log_path).st_ino

            # In-place O_TRUNC rewrite (same inode) whose new content is longer
            # than the previous offset.
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    'netsim I 09-25 22:38:00.250 netsimd.rs:456 - Netsim Version: 1.0.31, OS: linux, Arch: x86_64\n'
                    'netsim I 09-25 22:38:00.300 netsimd.rs:750 - gRPC port: 43930\n'
                )
            self.assertEqual(os.stat(log_path).st_ino, ino_before)

            self.assertTrue(
                old_tailer.poll(now_utc=float(_START + 60), allow_reset=False))
            self.assertTrue(old_tailer.frozen)
            self.assertEqual(old_tailer.grpc_port_logged, 43929)

            self.assertTrue(
                active_tailer.poll(now_utc=float(_START + 60),
                                   allow_reset=True))
            self.assertEqual(active_tailer.truncation_count, 1)
            self.assertEqual(active_tailer.version, '1.0.31')
            self.assertEqual(active_tailer.grpc_port_logged, 43930)

    def test_zombie_ini_writer_pid_is_marked_stale_ini(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            zombie_pid = 5_000_888
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            _write_ini(ini_path, f'pid={zombie_pid}\ngrpc.port=43929\n',
                       _START - 600)
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [tmpdir]

            real_pid_exists = erm.psutil.pid_exists
            real_process = erm.psutil.Process

            def fake_process(pid, *args, **kwargs):
                if pid == zombie_pid:
                    proc = MagicMock()
                    proc.status.return_value = erm.psutil.STATUS_ZOMBIE
                    return proc
                return real_process(pid, *args, **kwargs)

            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(erm.psutil,
                                 'pid_exists',
                                 side_effect=lambda p: p == zombie_pid or
                                 real_pid_exists(p)), \
                    patch.object(erm.psutil, 'Process', side_effect=fake_process), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', []), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS', {}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO', {}):
                tracker.update({}, {},
                               now_wall=float(_START + 10),
                               now_mono=10.0)

            self.assertIn(ini_path, tracker.stale_inis)

    def test_summary_omits_empty_charts_for_banner_when_device_has_no_charts(
            self):
        import emulator_resource_monitor as erm

        nan = float('nan')
        summary_data = {
            'start_time':
                1000.0,
            'start_time_monotonic':
                0.0,
            'iterations':
                2,
            'hardware_specs':
                'Test Host',
            'host_cpu_iterations':
                2,
            'host_cpu_sum':
                20.0,
            'host_cpu_max':
                10.0,
            'host_ram_iterations':
                2,
            'host_ram_sum':
                60.0,
            'host_ram_max':
                30.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'netsim_cpu_max':
                0.0,
            'netsim_ram_iterations':
                0,
            'netsim_ram_sum':
                0.0,
            'netsim_ram_max':
                0.0,
            'emulators': {
                'emulator-5554': {
                    'name': 'emulator-5554 | Unknown (offline)',
                    'cpu_iterations': 0,
                    'cpu_sum': 0.0,
                    'cpu_max': 0.0,
                    'ram_iterations': 0,
                    'ram_sum': 0.0,
                    'ram_max': 0.0,
                    'swap_iterations': 0,
                    'swap_sum': 0.0,
                    'swap_max': 0.0,
                    'qemu_cpu_iterations': 0,
                    'qemu_cpu_sum': 0.0,
                    'qemu_cpu_max': 0.0,
                    'qemu_ram_iterations': 0,
                    'qemu_ram_sum': 0.0,
                    'qemu_ram_max': 0.0,
                },
                'Pixel_8_emulator-5556': {
                    'name': 'Pixel_8 | Pixel 8',
                    'cpu_iterations': 2,
                    'cpu_sum': 50.0,
                    'cpu_max': 30.0,
                    'ram_iterations': 2,
                    'ram_sum': 80.0,
                    'ram_max': 40.0,
                    'swap_iterations': 0,
                    'swap_sum': 0.0,
                    'swap_max': 0.0,
                    'qemu_cpu_iterations': 0,
                    'qemu_cpu_sum': 0.0,
                    'qemu_cpu_max': 0.0,
                    'qemu_ram_iterations': 0,
                    'qemu_ram_sum': 0.0,
                    'qemu_ram_max': 0.0,
                },
            },
            'time_series': [
                {
                    'time': 2.0,
                    'host_cpu': 10.0,
                    'host_ram': 30.0,
                    'emu_cpu_tot_emulator-5554': nan,
                    'emu_cpu_tot_Pixel_8_emulator-5556': 20.0,
                },
                {
                    'time': 4.0,
                    'host_cpu': 10.0,
                    'host_ram': 30.0,
                    'emu_cpu_tot_emulator-5554': nan,
                    'emu_cpu_tot_Pixel_8_emulator-5556': 30.0,
                },
            ],
        }
        fake_fig = MagicMock()
        fake_fig.show.return_value = 'mock_chart_plot'
        with patch.dict(sys.modules,
                        {'plotille': MagicMock(Figure=lambda: fake_fig)}):
            lines = erm._build_summary_lines(summary_data, 1004.0,
                                             summary_data['time_series'])
        text = '\n'.join(lines)
        self.assertNotIn('CHARTS FOR: Unknown (offline)', text)
        self.assertIn('CHARTS FOR: Pixel_8 | Pixel 8', text)

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

    def test_install_py_handles_virtualenv_legacy_pip_and_psutil_failure(self):
        import contextlib
        import io
        import runpy
        import subprocess
        import tempfile

        install_path = os.path.join(os.path.dirname(__file__), 'install.py')

        # Inside a virtualenv: must omit --user and --break-system-packages.
        venv_cmds = []
        buf = io.StringIO()
        with patch.object(sys, 'prefix', '/tmp/fake_venv'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(subprocess,
                             'check_call',
                             side_effect=lambda cmd, **kw: venv_cmds.append(
                                 list(cmd))), \
                contextlib.redirect_stdout(buf):
            runpy.run_path(install_path, run_name='__main__')
        self.assertEqual(len(venv_cmds), 3)
        for cmd in venv_cmds:
            self.assertNotIn('--user', cmd)
            self.assertNotIn('--break-system-packages', cmd)
        self.assertIn('You can now run the monitor', buf.getvalue())

        # Outside a virtualenv on pip < 23.0.1: retries --user ONLY when pip
        # rejects --break-system-packages.
        legacy_run_cmds = []
        legacy_fallback_cmds = []

        def legacy_pip_run(cmd, **kw):
            legacy_run_cmds.append(list(cmd))
            return subprocess.CompletedProcess(
                cmd,
                2,
                stdout='',
                stderr='Usage: pip install [options]\n\n'
                'no such option: --break-system-packages\n',
            )

        buf2 = io.StringIO()
        err2 = io.StringIO()
        with patch.object(sys, 'prefix', '/usr'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(subprocess, 'run', side_effect=legacy_pip_run), \
                patch.object(
                    subprocess,
                    'check_call',
                    side_effect=lambda cmd, **kw: legacy_fallback_cmds.append(
                        list(cmd)),
                ), \
                contextlib.redirect_stdout(buf2), \
                contextlib.redirect_stderr(err2):
            runpy.run_path(install_path, run_name='__main__')
        self.assertEqual(len(legacy_run_cmds), 3)
        self.assertEqual(len(legacy_fallback_cmds), 3)
        for cmd in legacy_fallback_cmds:
            self.assertIn('--user', cmd)
            self.assertNotIn('--break-system-packages', cmd)
        self.assertEqual(err2.getvalue(), '')
        self.assertIn('Successfully installed: psutil, GPUtil, plotille',
                      buf2.getvalue())

        # Outside a virtualenv on modern pip >= 23.0.1: succeeds in one pass and
        # preserves pip stderr warnings without retrying --user.
        modern_run_cmds = []
        modern_fallback_cmds = []

        def modern_pip_ok(cmd, **kw):
            modern_run_cmds.append(list(cmd))
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='',
                stderr=f'WARNING: {cmd[-1]} script dir is not on PATH\n',
            )

        buf_ok = io.StringIO()
        err_ok = io.StringIO()
        with patch.object(sys, 'prefix', '/usr'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(subprocess, 'run', side_effect=modern_pip_ok), \
                patch.object(
                    subprocess,
                    'check_call',
                    side_effect=lambda cmd, **kw: modern_fallback_cmds.append(
                        list(cmd)),
                ), \
                contextlib.redirect_stdout(buf_ok), \
                contextlib.redirect_stderr(err_ok):
            runpy.run_path(install_path, run_name='__main__')
        self.assertEqual(len(modern_run_cmds), 3)
        self.assertEqual(modern_fallback_cmds, [])
        self.assertIn('WARNING: psutil script dir is not on PATH',
                      err_ok.getvalue())

        # ASCII-only sys.stderr stream: Unicode chars in pip stderr degrade
        # cleanly via _write_stderr without raising UnicodeEncodeError.
        def pip_ok_with_unicode_warning(cmd, **kw):
            self.assertEqual(list(cmd[:4]),
                             [sys.executable, '-m', 'pip', 'install'])
            return subprocess.CompletedProcess(cmd,
                                               0,
                                               stdout='',
                                               stderr='× warning ╰─>\n')

        raw_ascii = io.BytesIO()
        ascii_err = io.TextIOWrapper(raw_ascii,
                                     encoding='ascii',
                                     errors='strict')
        buf_ascii = io.StringIO()
        with patch.object(sys, 'prefix', '/usr'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(subprocess,
                             'run',
                             side_effect=pip_ok_with_unicode_warning), \
                contextlib.redirect_stdout(buf_ascii), \
                contextlib.redirect_stderr(ascii_err):
            runpy.run_path(install_path, run_name='__main__')
        ascii_err.flush()
        self.assertIn(b'? warning ??>\n', raw_ascii.getvalue())

        # Outside a virtualenv on PEP 668 host when network/compiler fails: must
        # NOT retry --user (avoiding externally-managed-environment noise), must
        # forward real pip stderr + Failed to install + remediation hint to
        # sys.stderr, and must exit 1 when psutil fails.
        net_run_cmds = []
        net_fallback_cmds = []

        def modern_pip_net_fail(cmd, **kw):
            net_run_cmds.append(list(cmd))
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout='',
                stderr=(
                    f'Command had --break-system-packages for {cmd[-1]}\n'
                    'gcc: error: unrecognized command-line option \'-Wbad\'\n'
                    f'ERROR: No matching distribution found for {cmd[-1]}\n'),
            )

        buf3 = io.StringIO()
        err3 = io.StringIO()
        with patch.object(sys, 'prefix', '/usr'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(subprocess,
                             'run',
                             side_effect=modern_pip_net_fail), \
                patch.object(
                    subprocess,
                    'check_call',
                    side_effect=lambda cmd, **kw: net_fallback_cmds.append(
                        list(cmd)),
                ), \
                contextlib.redirect_stdout(buf3), \
                contextlib.redirect_stderr(err3):
            with self.assertRaises(SystemExit) as cm:
                runpy.run_path(install_path, run_name='__main__')
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(net_run_cmds), 3)
        self.assertEqual(net_fallback_cmds, [])
        self.assertNotIn('Failed to install psutil\n', buf3.getvalue())
        self.assertNotIn('You can now run the monitor', buf3.getvalue())
        self.assertIn('ERROR: No matching distribution found for psutil',
                      err3.getvalue())
        self.assertIn('Failed to install psutil', err3.getvalue())
        self.assertIn('python3 -m venv', err3.getvalue())
        self.assertIn('python3-psutil', err3.getvalue())

        # Optional package failure when psutil succeeds: routes per-package
        # failure to stderr while still exiting 0.
        def psutil_only(cmd, **kw):
            if cmd[-1] != 'psutil':
                raise subprocess.CalledProcessError(1, cmd)

        buf4 = io.StringIO()
        err4 = io.StringIO()
        with patch.object(sys, 'prefix', '/tmp/fake_venv'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(subprocess,
                             'check_call',
                             side_effect=psutil_only), \
                contextlib.redirect_stdout(buf4), \
                contextlib.redirect_stderr(err4):
            runpy.run_path(install_path, run_name='__main__')
        self.assertIn('Successfully installed: psutil', buf4.getvalue())
        self.assertIn('You can now run the monitor', buf4.getvalue())
        self.assertIn('Failed to install GPUtil', err4.getvalue())
        self.assertIn('Failed to install plotille', err4.getvalue())

        # Unexpected exception at top level: routes error to sys.stderr and
        # exits 1.
        buf5 = io.StringIO()
        err5 = io.StringIO()
        with patch.object(sys, 'prefix', '/tmp/fake_venv'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(subprocess,
                             'check_call',
                             side_effect=OSError('pip binary missing')), \
                contextlib.redirect_stdout(buf5), \
                contextlib.redirect_stderr(err5):
            with self.assertRaises(SystemExit) as cm5:
                runpy.run_path(install_path, run_name='__main__')
        self.assertEqual(cm5.exception.code, 1)
        self.assertNotIn('An error occurred', buf5.getvalue())
        self.assertIn('An error occurred: pip binary missing', err5.getvalue())

        # End-to-end subprocess test (no subprocess mocks): verifies non-UTF-8
        # compiler bytes on pip stderr do not trigger UnicodeDecodeError and do
        # not trigger PEP 668 fallback.
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_pip = os.path.join(tmpdir, 'pip.py')
            log_file = os.path.join(tmpdir, 'invocations.txt')
            with open(fake_pip, 'w', encoding='utf-8') as f:
                f.write(
                    'import sys\n'
                    f'with open({log_file!r}, "a", encoding="utf-8") as lf:\n'
                    '    lf.write(" ".join(sys.argv[1:]) + "\\n")\n'
                    'sys.stderr.buffer.write(b"compiler error \\xff\\xfe\\n")\n'
                    'sys.exit(1)\n')
            env = dict(os.environ)
            env['PYTHONPATH'] = (tmpdir if not env.get('PYTHONPATH') else
                                 tmpdir + os.pathsep + env['PYTHONPATH'])
            e2e = subprocess.run(
                [
                    sys.executable,
                    '-c',
                    'import runpy, sys; sys.prefix = sys.base_prefix; '
                    f'runpy.run_path({install_path!r}, run_name="__main__")',
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(e2e.returncode, 1)
            self.assertIn('compiler error', e2e.stderr)
            self.assertIn('python3 -m venv', e2e.stderr)
            with open(log_file, 'r', encoding='utf-8') as lf:
                invocations = [ln.strip() for ln in lf if ln.strip()]
            self.assertEqual(len(invocations), 3)
            for inv in invocations:
                self.assertIn('--break-system-packages', inv)


class TestRound6Fixes(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)

    def test_cold_boot_survives_first_device_tick_adb_shell_failure(self):
        import emulator_resource_monitor as erm

        # Direct unit check: is_booting=None retries Res: N/A and stays None on
        # transient shell errors.
        with patch.object(
                erm,
                'run_cmd',
                side_effect=['adb: closed', 'adb: closed', '', '', '']):
            model, boot_flag, *_ = erm.get_guest_stats(
                'emulator-5554', 'Pixel 8 | Android 14 | Res: N/A', None,
                'device')
        self.assertIsNone(boot_flag)
        self.assertIn('Pixel 8', model)

        with patch.object(
                erm,
                'run_cmd',
                side_effect=[
                    'Pixel 8|!|14|!|34|!|UP1A|!|Physical size: 1080x2400|!|0',
                    'adb: closed', '', '', ''
                ]):
            model2, boot_flag2, *_ = erm.get_guest_stats(
                'emulator-5554', 'Pixel 8 | Android 14 | Res: N/A', None,
                'device')
        self.assertTrue(boot_flag2)
        self.assertIn('1080x2400', model2)

        tick = [0]
        now_t = [100.0]
        tags_seen = []
        dev_seq = [
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        host_val = _host_tuple(
            {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
            qemu_stats={'emulator-5554': ('40.0%', '1800.0MB')})

        def fake_run_cmd(cmd, **kwargs):
            cmd_str = ' '.join(cmd)
            if tick[0] == 1:
                return 'adb: closed'
            boot_c = '1' if tick[0] >= 4 else '0'
            if 'ro.product.model' in cmd_str:
                return f'Pixel 8|!|14|!|34|!|UP1A|!|Physical size: 1080x2400|!|{boot_c}'
            if '===ADB_MON_SPLIT===' in cmd_str:
                return (
                    'MemTotal: 4194304 kB\nMemAvailable: 2097152 kB\n'
                    '===ADB_MON_SPLIT===\n/dev/block/dm-5 6G 2G 4G 33% /data\n'
                    f'===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n{boot_c}\n'
                    '===ADB_MON_SPLIT===\n')
            if 'top' in cmd_str:
                return 'Tasks: 100 total\n400%cpu 120%user 0%nice 40%sys 240%idle\n'
            return ''

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_dashboard(*args, **kwargs):
            for _, (_, tags, *_) in args[4].items():
                tags_seen.append((tick[0], tags))

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 6.0
            if tick[0] == 5:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 4)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      return_value=host_val), \
                patch('emulator_resource_monitor.run_cmd',
                      side_effect=fake_run_cmd), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=fake_dashboard), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emu = mock_save.call_args[0][0]['emulators']['Pixel_8_emulator-5554']
        self.assertTrue(emu['boot_state_known'])
        self.assertTrue(emu['boot_completed'])
        self.assertTrue(emu['track_boot_stats'])
        self.assertIn('boot_finished_time', emu)
        self.assertGreaterEqual(len(emu['boot_log']), 2, emu['boot_log'])
        self.assertTrue(any('RECORDING BOOT STATS' in t for _, t in tags_seen),
                        tags_seen)

    def test_stale_offline_followed_by_delayed_avd_ini_skips_stale_rows(self):
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        dev_seq = [
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        host_seq = [
            _host_tuple({}, {}, qemu_stats={}),
            _host_tuple({}, {}, qemu_stats={}),
            _host_tuple({}, {}, qemu_stats={}),
            _host_tuple({'emulator-5554': 2000}, {},
                        qemu_stats={'emulator-5554': ('35.0%', '1900.0MB')}),
            _host_tuple({'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={'emulator-5554': ('30.0%', '1950.0MB')}),
        ]

        def fake_host():
            idx = min(tick[0], 4)
            if idx >= 3:
                avd = 'Pixel_8' if idx >= 4 else None
                erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                erm._LAST_EMU_GROUP_INFO = {
                    'emulator-5554': {
                        'primary_pid': 2000,
                        'primary_ctime': 115.0,
                        'avd_id': avd,
                        'display_name': 'Pixel 8' if avd else None,
                        'avd_name': avd,
                        'grpc_endpoint': None,
                        'no_netsim': True,
                        'pids': {2000},
                        'member_start_times': {
                            2000: 115.0
                        },
                    }
                }
                erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (2000, 115.0)}
            else:
                erm._LAST_EMU_GROUP_PIDS = {}
                erm._LAST_EMU_GROUP_INFO = {}
                erm._LAST_PRIMARY_PROC_ID = {}
            return host_seq[idx]

        def fake_guest(serial, known_model, is_booting, status):
            if status != 'device':
                return (f'Unknown ({status})', bool(is_booting),
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})')
            return (_guest_model('Pixel 8'), False, '20.0%', '15.0%',
                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 5.0
            if tick[0] == 5:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 4)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        ts = list(mock_save.call_args[0][0]['time_series'])
        self.assertEqual(len(ts), 4)
        # Ticks 1 and 2 were pre-launch stale adb offline rows: must NOT migrate
        # into Pixel_8_emulator-5554.
        for pre_row in ts[:2]:
            self.assertNotIn('netsim_state_Pixel_8_emulator-5554', pre_row)
            self.assertNotIn('emu_cpu_tot_Pixel_8_emulator-5554', pre_row)
        # Ticks 3 and 4 belong to PID 2000 and DO migrate into Pixel_8_emulator-5554.
        self.assertEqual(
            [r.get('netsim_state_Pixel_8_emulator-5554') for r in ts[2:]],
            ['DISABLED', 'DISABLED'])
        self.assertEqual(
            [r.get('qemu_cpu_Pixel_8_emulator-5554') for r in ts[2:]],
            [35.0, 30.0])
        self.assertEqual(ts[3].get('emu_cpu_tot_Pixel_8_emulator-5554'), 20.0)

    def test_online_badge_ignores_unauthorized_and_clears_on_offline_or_restart(
            self):
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        tags_by_tick = {}
        dev_seq = [
            {
                'emulator-5554': 'unauthorized'
            },
            {
                'emulator-5554': 'no permissions'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'unauthorized'
            },
        ]
        pid_seq = [1000, 1000, 1000, 1000, 1000, 2000]

        def fake_host():
            idx = min(tick[0], 5)
            pid = pid_seq[idx]
            erm._LAST_PRIMARY_PROC_ID = {
                'emulator-5554': (pid, 100.0 if pid == 1000 else 112.0)
            }
            return _host_tuple(
                {'emulator-5554': pid}, {'emulator-5554': 'Pixel_8'},
                qemu_stats={'emulator-5554': ('20.0%', '1500.0MB')})

        def fake_guest(serial, known_model, is_booting, status):
            if status != 'device':
                return (f'Unknown ({status})', bool(is_booting),
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})')
            return (_guest_model('Pixel 8'), False, '20.0%', '15.0%',
                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_dashboard(*args, **kwargs):
            for _, (_, tags, *_) in args[4].items():
                tags_by_tick[tick[0]] = tags

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 2.0
            if tick[0] == 6:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 5)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary'), \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=fake_dashboard), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        # Non-'device' states (unauthorized, no permissions, offline) must never
        # show [ONLINE]; real transitions to 'device' (ticks 2 and 4) must show
        # [ONLINE], and dropping offline/unauthorized within 10s clears it.
        self.assertNotIn('[ONLINE]', tags_by_tick[0])
        self.assertNotIn('[ONLINE]', tags_by_tick[1])
        self.assertIn('[ONLINE]', tags_by_tick[2])
        self.assertNotIn('[ONLINE]', tags_by_tick[3])
        self.assertIn('[ONLINE]', tags_by_tick[4])
        self.assertNotIn('[ONLINE]', tags_by_tick[5])

    def test_paging_rate_uses_vmstat_read_timestamp_across_slow_guest_ticks(
            self):
        import builtins
        import io
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        dev_seq = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'offline'
            },
        ]
        real_open = builtins.open

        def fake_open(path, mode='r', *args, **kwargs):
            if str(path) == '/proc/vmstat':
                # Constant 1000.0 KB/s paging-in rate, 500.0 KB/s paging-out.
                pgin = int(10000 + (now_t[0] - 100.0) * 1000.0)
                pgout = int(5000 + (now_t[0] - 100.0) * 500.0)
                txt = f'pgpgin {pgin}\npgpgout {pgout}\npswpin 0\npswpout 0\n'
                if 'b' in mode:
                    return io.BytesIO(txt.encode('ascii'))
                return io.StringIO(txt)
            return real_open(path, mode, *args, **kwargs)

        def fake_guest(serial, known_model, is_booting, status):
            if tick[0] == 1:
                # Slow guest adb query advances wall/monotonic clock by 1.8s
                # AFTER get_host_stats() already read /proc/vmstat at t=102.0.
                now_t[0] += 1.8
            if status != 'device':
                return (f'Unknown ({status})', bool(is_booting),
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})')
            return (_guest_model('Pixel 8'), False, '20.0%', '15.0%',
                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep(_):
            tick[0] += 1
            # Ticks start strictly at t=100.0, t=102.0, t=104.0.
            now_t[0] = 100.0 + tick[0] * 2.0
            if tick[0] == 3:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('platform.system', return_value='Linux'), \
                patch('builtins.open', side_effect=fake_open), \
                patch('emulator_resource_monitor.host_cpu_percent',
                      return_value=12.0), \
                patch('emulator_resource_monitor.get_process_stats',
                      return_value=({}, [], '0.0%', '0.0MB', {}, {})), \
                patch('emulator_resource_monitor.get_gpu_stats',
                      return_value=('N/A', 'N/A')), \
                patch('emulator_resource_monitor.get_emu_gpu_stats',
                      return_value={}), \
                patch('emulator_resource_monitor.read_disk_io',
                      return_value=None), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 2)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        ts = list(mock_save.call_args[0][0]['time_series'])
        self.assertEqual(len(ts), 2)
        for row in ts:
            self.assertAlmostEqual(row['host_paging_in'], 1000.0, delta=10.0)
            self.assertAlmostEqual(row['host_paging_out'], 500.0, delta=10.0)

    def test_lingering_qemu_next_companion_does_not_adopt_bystander_daemon(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            dir_a = os.path.join(tmpdir, 'a')
            dir_b = os.path.join(tmpdir, 'b')
            art_a = os.path.join(dir_a, 'android-test', 'netsimd')
            art_b = os.path.join(dir_b, 'android-test', 'netsimd')
            os.makedirs(art_a, exist_ok=True)
            os.makedirs(art_b, exist_ok=True)

            _write_ini(os.path.join(dir_a, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
            _write_ini(os.path.join(dir_b, 'netsim_2.ini'),
                       f'pid={_DAEMON_B}\ngrpc.port=43930\n', _START + 1)
            _touch(
                os.path.join(art_a, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:06.000 service.rs:585 - '
                'DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n')
            _touch(
                os.path.join(art_b, 'netsim_2_stderr.log'),
                'netsim I 09-25 22:37:06.000 service.rs:585 - '
                'DeviceActor: AddChip 2 (kind WIFI) to device 1 (Glasses)\n'
                'netsim I 09-25 22:37:07.000 wifi.rs:120 - '
                'ApActor: Associated 02:15:b2:00:00:01\n')

            daemon_a = _netsimd(_DAEMON_A, _START, dir_a)
            daemon_b = _netsimd(_DAEMON_B,
                                _START,
                                dir_b,
                                cmdline=['/sdk/emulator/netsimd', '-i', '2'])
            launcher_a, worker_a, fishtank_a = 5_000_310, 5_000_311, 5_000_312
            launcher_c, worker_c = 5_000_320, 5_000_321
            phone_key = 'phone_emulator-5554'
            glasses_key = 'glasses_emulator-5556'
            abort_key = 'abort_emulator-5558'

            phone_full = {
                'primary_pid': worker_a,
                'primary_ctime': float(_START),
                'avd_id': 'phone',
                'display_name': 'Phone',
                'avd_name': 'phone',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {launcher_a, worker_a, fishtank_a},
                'member_start_times': {
                    launcher_a: float(_START - 2),
                    worker_a: float(_START),
                    fishtank_a: float(_START + 1),
                },
            }
            glasses_full = {
                'primary_pid': _WORKER_B,
                'primary_ctime': float(_START),
                'avd_id': 'glasses',
                'display_name': 'Glasses',
                'avd_name': 'glasses',
                'grpc_endpoint': '127.0.0.1:43930',
                'no_netsim': False,
                'pids': {_WORKER_B},
                'member_start_times': {
                    _WORKER_B: float(_START)
                },
            }
            abort_full = {
                'primary_pid': worker_c,
                'primary_ctime': float(_START),
                'avd_id': 'abort',
                'display_name': 'Abort',
                'avd_name': 'abort',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {launcher_c, worker_c},
                'member_start_times': {
                    launcher_c: float(_START - 1),
                    worker_c: float(_START),
                },
            }

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir_a, dir_b]

            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={
                                     43929: [{
                                         'serial': 'emulator-5554',
                                         'peer_port': 51001
                                     }],
                                     43930: [{
                                         'serial': 'emulator-5556',
                                         'peer_port': 51002
                                     }],
                                 }), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS',
                                 [daemon_a, daemon_b]), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS', {
                        'emulator-5554': {launcher_a, worker_a, fishtank_a},
                        'emulator-5556': {_WORKER_B},
                        'emulator-5558': {launcher_c, worker_c},
                    }), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO', {
                        'emulator-5554': phone_full,
                        'emulator-5556': glasses_full,
                        'emulator-5558': abort_full,
                    }):
                tracker.update(
                    {
                        phone_key: {
                            'name': 'Phone',
                            'current_serial': 'emulator-5554',
                            'current_pid': worker_a,
                            'first_seen': 10.0,
                        },
                        glasses_key: {
                            'name': 'Glasses',
                            'current_serial': 'emulator-5556',
                            'current_pid': _WORKER_B,
                            'first_seen': 10.0,
                        },
                        abort_key: {
                            'name': 'Abort',
                            'current_serial': 'emulator-5558',
                            'current_pid': worker_c,
                            'first_seen': 10.0,
                        },
                    }, {
                        'emulator-5554': 'device',
                        'emulator-5556': 'device',
                    },
                    now_wall=float(_START + 10),
                    now_mono=10.0)

            phone_rec = tracker.emulator_records[phone_key]
            glasses_rec = tracker.emulator_records[glasses_key]
            abort_rec = tracker.emulator_records[abort_key]
            self.assertEqual(phone_rec['state'], 'CONNECTED')
            self.assertEqual(phone_rec['chip_attach_s'], {'BT': (1.0, False)})

            # Worker A, Worker C, and Daemon A all exit; launcher/fishtank
            # companions linger while bystander Daemon B remains active.
            os.remove(os.path.join(dir_a, 'netsim.ini'))
            phone_lingering = {
                'primary_pid': fishtank_a,
                'primary_ctime': float(_START + 1),
                'avd_id': None,
                'display_name': None,
                'avd_name': None,
                'grpc_endpoint': None,
                'no_netsim': False,
                'pids': {launcher_a, fishtank_a},
                'member_start_times': {
                    launcher_a: float(_START - 2),
                    fishtank_a: float(_START + 1),
                },
            }
            abort_lingering = {
                'primary_pid': launcher_c,
                'primary_ctime': float(_START - 1),
                'avd_id': None,
                'display_name': None,
                'avd_name': None,
                'grpc_endpoint': None,
                'no_netsim': False,
                'pids': {launcher_c},
                'member_start_times': {
                    launcher_c: float(_START - 1)
                },
            }
            for mono, wall in ((20.0, float(_START + 20)),
                               (22.0, float(_START + 22))):
                with patch.object(tracker,
                                  '_read_proc_locks',
                                  return_value={}), \
                        patch.object(tracker,
                                     '_collect_loopback_grpc_sockets',
                                     return_value={
                                         43930: [{
                                             'serial': 'emulator-5556',
                                             'peer_port': 51002
                                         }],
                                     }), \
                        patch.object(erm, '_LAST_NETSIMD_PROCS', [daemon_b]), \
                        patch.object(erm, '_LAST_EMU_GROUP_PIDS', {
                            'emulator-5554': {launcher_a, fishtank_a},
                            'emulator-5556': {_WORKER_B},
                            'emulator-5558': {launcher_c},
                        }), \
                        patch.object(erm, '_LAST_EMU_GROUP_INFO', {
                            'emulator-5554': phone_lingering,
                            'emulator-5556': glasses_full,
                            'emulator-5558': abort_lingering,
                        }):
                    tracker.update(
                        {
                            phone_key: {
                                'name': 'Phone',
                                'current_serial': 'emulator-5554',
                                'current_pid': fishtank_a,
                                'first_seen': 10.0,
                            },
                            glasses_key: {
                                'name': 'Glasses',
                                'current_serial': 'emulator-5556',
                                'current_pid': _WORKER_B,
                                'first_seen': 10.0,
                            },
                            abort_key: {
                                'name': 'Abort',
                                'current_serial': 'emulator-5558',
                                'current_pid': launcher_c,
                                'first_seen': 10.0,
                            },
                        }, {'emulator-5556': 'device'},
                        now_wall=wall,
                        now_mono=mono)

            self.assertEqual(phone_rec['state'], 'DISCONNECTED')
            self.assertEqual(phone_rec['disconnect_count'], 1)
            self.assertEqual(phone_rec['daemon_key'],
                             (_DAEMON_A, float(_START)))
            self.assertEqual(phone_rec['chip_attach_s'], {'BT': (1.0, False)})
            self.assertEqual(abort_rec['state'],
                             'FAILED(worker_exit_no_connect)')
            self.assertEqual(glasses_rec['wifi_associated_s'], 2.0)

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX uids')
    def test_foreign_daemon_unreadable_env_does_not_hijack_monitor_artifacts_or_pcap(
            self):
        import emulator_resource_monitor as erm

        my_uid = os.getuid()
        foreign_pid = _DAEMON_A - 50
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(os.environ, {'TMPDIR': tmpdir, 'USER': 'alice'},
                           clear=False):
            os.environ.pop('ANDROID_TMP', None)
            art_dir = os.path.join(tmpdir, 'android-alice', 'netsimd')
            pcaps_dir = os.path.join(art_dir, 'pcaps')
            os.makedirs(pcaps_dir, exist_ok=True)
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            _write_ini(ini_path, f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       _START + 1)
            _touch(
                os.path.join(art_dir, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.500 netsimd.rs:757 - Wrote to INI file {ini_path}\n'
                'netsim I 09-25 22:37:05.500 grpc.rs:100 - Grpc server listening on localhost:43929\n'
                'netsim I 09-25 22:37:06.000 service.rs:585 - '
                'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel)\n')
            with open(os.path.join(pcaps_dir, 'netsim-1-Pixel-WIFI.pcap'),
                      'wb') as f:
                f.write(b'\xd4' * 64)

            foreign_empty = {
                'pid': foreign_pid,
                'create_time': float(_START - 10),
                'ppid': 1,
                'cmdline': ['/sdk/emulator/netsimd'],
                'exe': '/sdk/emulator/netsimd',
                'environ': {},
                'uid': my_uid + 1,
            }
            foreign_spoofed = {
                'pid': foreign_pid + 1,
                'create_time': float(_START - 9),
                'ppid': 1,
                'cmdline': ['/sdk/emulator/netsimd'],
                'exe': '/sdk/emulator/netsimd',
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'alice'
                },
                'uid': my_uid + 1,
            }
            own_daemon = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'cmdline': ['/sdk/emulator/netsimd'],
                'exe': '/sdk/emulator/netsimd',
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'alice'
                },
                'uid': my_uid,
            }

            out_dir = os.path.join(tmpdir, 'out')
            tracker = erm.NetsimTracker(pcap_dir='',
                                        enable_log=True,
                                        out_dir=out_dir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            cli_calls = []

            def fake_cli(netsim_bin, grpc_port, args, **kw):
                cli_calls.append(list(args))
                return (
                    'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                    '1  | Pixel       | WIFI      | off   | 00:00:00  | 0       | 0\n'
                )

            tracker.pcap_mgr._run_netsim_cli = fake_cli
            emu_group = {
                'primary_pid': _WORKER_A,
                'primary_ctime': float(_START),
                'avd_id': 'pixel',
                'display_name': 'Pixel',
                'avd_name': 'pixel',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {_WORKER_A},
                'member_start_times': {
                    _WORKER_A: float(_START)
                },
            }
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={
                                     43929: [{
                                         'serial': 'emulator-5554',
                                         'peer_port': 51001
                                     }]
                                 }), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS',
                                 [foreign_empty, foreign_spoofed, own_daemon]), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS',
                                 {'emulator-5554': {_WORKER_A}}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO',
                                 {'emulator-5554': emu_group}):
                tracker.update(
                    {
                        'pixel_emulator-5554': {
                            'name': 'Pixel',
                            'current_serial': 'emulator-5554',
                            'current_pid': _WORKER_A,
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 10),
                    now_mono=10.0)

            self.assertEqual(
                tracker.daemons[(foreign_pid,
                                 float(_START - 10))]['artifact_dir'], '')
            self.assertEqual(
                tracker.daemons[(foreign_pid + 1,
                                 float(_START - 9))]['artifact_dir'], '')
            self.assertFalse(
                any('DUPLICATE_ARTIFACT_DIR' in ev
                    for _, ev in tracker.timeline), tracker.timeline)
            self.assertEqual(
                tracker.emulator_records['pixel_emulator-5554']['daemon_key'],
                (_DAEMON_A, float(_START)))
            self.assertIn(['capture', 'patch', 'on', '1', 'Pixel', 'WIFI'],
                          cli_calls)

            # Own daemon writes a second pcap and exits while foreign daemons stay
            # alive: exit harvest must still copy the second pcap.
            with open(os.path.join(pcaps_dir, 'netsim-2-Pixel-BT.pcap'),
                      'wb') as f:
                f.write(b'\xd4' * 96)
            os.remove(ini_path)
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={}), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS',
                                 [foreign_empty, foreign_spoofed]), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS', {}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO', {}):
                tracker.update({}, {},
                               now_wall=float(_START + 25),
                               now_mono=25.0)

            self.assertEqual(len(tracker.pcap_mgr.copied_files), 2)

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX mkfifo and symlinks')
    def test_regular_file_readers_reject_fifos_symlinks_and_oversized_files(
            self):
        import threading
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            fifo_ini = os.path.join(tmpdir, 'netsim_2.ini')
            os.mkfifo(fifo_ini)
            avd_run = os.path.join(tmpdir, 'avd', 'running')
            os.makedirs(avd_run, exist_ok=True)
            fifo_pid_ini = os.path.join(avd_run, f'pid_{_WORKER_A}.ini')
            os.mkfifo(fifo_pid_ini)
            fifo_log = os.path.join(tmpdir, 'fifo_stderr.log')
            os.mkfifo(fifo_log)
            fifo_stats = os.path.join(tmpdir, 'fifo_stats.json')
            os.mkfifo(fifo_stats)

            results = {}

            def probe_fifos():
                results['ini'] = erm.parse_netsim_ini(fifo_ini)
                w = MagicMock()
                w.environ.return_value = {'TMPDIR': tmpdir}
                with patch.object(erm, 'host_discovery_dirs', return_value=[]):
                    results['pid_ini'] = erm._read_emulator_discovery_ini(
                        _WORKER_A, w)
                results['log'] = erm.NetsimLogTailer(fifo_log).poll(
                    now_utc=1000.0)
                results['stats'] = erm._read_session_stats_json(fifo_stats)[0]

            t = threading.Thread(target=probe_fifos, daemon=True)
            t.start()
            t.join(timeout=0.5)
            was_alive = t.is_alive()
            if was_alive:
                for fp in (fifo_ini, fifo_pid_ini, fifo_log, fifo_stats):
                    try:
                        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
                        os.close(fd)
                    except OSError:
                        pass
                t.join(timeout=0.5)
            self.assertFalse(was_alive, 'reader blocked on writerless FIFO')
            self.assertEqual(results, {
                'ini': {},
                'pid_ini': {},
                'log': False,
                'stats': None,
            })

            # Symlink targets must not be followed by INI, log, or stats readers.
            real_target = os.path.join(tmpdir, 'secret_target.txt')
            _touch(
                real_target, 'grpc.port=42424\navd.id=Secret\n'
                'netsim I 09-25 22:37:05.000 grpc.rs:10 - '
                'Grpc server listening on localhost:42424\n')
            sym_ini = os.path.join(tmpdir, 'netsim.ini')
            os.symlink(real_target, sym_ini)
            sym_pid_ini = os.path.join(avd_run, f'pid_{_WORKER_B}.ini')
            os.symlink(real_target, sym_pid_ini)
            sym_log = os.path.join(tmpdir, 'sym_stderr.log')
            os.symlink(real_target, sym_log)
            real_json = os.path.join(tmpdir, 'target.json')
            _touch(real_json, '{"version": "1.0.25"}')
            sym_json = os.path.join(tmpdir, 'sym_stats.json')
            os.symlink(real_json, sym_json)

            self.assertEqual(erm.parse_netsim_ini(sym_ini), {})
            w2 = MagicMock()
            w2.environ.return_value = {'TMPDIR': tmpdir}
            with patch.object(erm, 'host_discovery_dirs', return_value=[]):
                self.assertEqual(
                    erm._read_emulator_discovery_ini(_WORKER_B, w2), {})
            self.assertFalse(erm.NetsimLogTailer(sym_log).poll(now_utc=1000.0))
            self.assertIsNone(erm._read_session_stats_json(sym_json)[0])

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [tmpdir]
            self.assertEqual(tracker._discover_ini_files([]), [])

            # Oversized (>4 MiB) session stats JSON is rejected cleanly.
            huge_json = os.path.join(tmpdir, 'huge_stats.json')
            with open(huge_json, 'wb') as f:
                f.write(b'{"pad":"' + (b'a' * (4 * 1024 * 1024 + 64)) + b'"}')
            parsed_huge, _, _, had_err = erm._read_session_stats_json(huge_json)
            self.assertIsNone(parsed_huge)
            self.assertTrue(had_err)

    def test_exited_no_netsim_disabled_record_does_not_outrank_newer_settled_row(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-test', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            _write_ini(os.path.join(tmpdir, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n', _START + 1)
            _touch(
                os.path.join(art_dir, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:06.000 service.rs:585 - '
                'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel)\n')
            daemon_a = _netsimd(_DAEMON_A, _START, tmpdir)
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]

            no_ns_group = {
                'primary_pid': _WORKER_A,
                'primary_ctime': float(_START),
                'avd_id': 'nonetsim',
                'display_name': 'NoNetsim',
                'avd_name': 'nonetsim',
                'grpc_endpoint': None,
                'no_netsim': True,
                'pids': {_WORKER_A},
                'member_start_times': {
                    _WORKER_A: float(_START)
                },
            }
            pixel_group = {
                'primary_pid': _WORKER_B,
                'primary_ctime': float(_START + 15),
                'avd_id': 'pixel',
                'display_name': 'Pixel',
                'avd_name': 'pixel',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {_WORKER_B},
                'member_start_times': {
                    _WORKER_B: float(_START + 15)
                },
            }

            # Tick 1: -no-netsim AVD runs on emulator-5554 at t=5.0.
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={}), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', []), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS',
                                 {'emulator-5554': {_WORKER_A}}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO',
                                 {'emulator-5554': no_ns_group}):
                tracker.update({}, {'emulator-5554': 'device'},
                               now_wall=float(_START + 5),
                               now_mono=5.0)

            # Tick 2: immediately replaced on emulator-5554 by netsim-enabled Pixel.
            with patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker,
                                 '_collect_loopback_grpc_sockets',
                                 return_value={
                                     43929: [{
                                         'serial': 'emulator-5554',
                                         'peer_port': 51001
                                     }]
                                 }), \
                    patch.object(erm, '_LAST_NETSIMD_PROCS', [daemon_a]), \
                    patch.object(erm, '_LAST_EMU_GROUP_PIDS',
                                 {'emulator-5554': {_WORKER_B}}), \
                    patch.object(erm, '_LAST_EMU_GROUP_INFO',
                                 {'emulator-5554': pixel_group}):
                tracker.update({}, {'emulator-5554': 'device'},
                               now_wall=float(_START + 20),
                               now_mono=20.0)

            # Ticks 3-4: Pixel exits and settles to DISCONNECTED.
            for mono, wall in ((30.0, float(_START + 30)),
                               (35.0, float(_START + 35))):
                with patch.object(tracker,
                                  '_read_proc_locks',
                                  return_value={}), \
                        patch.object(tracker,
                                     '_collect_loopback_grpc_sockets',
                                     return_value={}), \
                        patch.object(erm, '_LAST_NETSIMD_PROCS', []), \
                        patch.object(erm, '_LAST_EMU_GROUP_PIDS', {}), \
                        patch.object(erm, '_LAST_EMU_GROUP_INFO', {}):
                    tracker.update({}, {}, now_wall=wall, now_mono=mono)

            row = tracker.get_dashboard_snapshot(
                now_wall=float(_START + 35))['emulator_rows']['emulator-5554']
            self.assertIn('DISCONNECTED', row)
            self.assertNotIn('DISABLED', row)


class TestRound7Fixes(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)

    def test_first_pid_and_transient_host_error_preserve_guest_samples_and_state(
            self):
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        dev_seq = [{'emulator-5554': 'device'}] * 5

        def fake_host():
            idx = min(tick[0], 4)
            if idx <= 1:
                erm._LAST_EMU_GROUP_PIDS = {}
                erm._LAST_EMU_GROUP_INFO = {}
                erm._LAST_PRIMARY_PROC_ID = {}
                return _host_tuple({}, {}, qemu_stats={})
            if idx == 3:
                raise RuntimeError('transient psutil failure')
            erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
            erm._LAST_EMU_GROUP_INFO = {
                'emulator-5554': {
                    'primary_pid': 2000,
                    'primary_ctime': 100.0,
                    'avd_id': 'Pixel_8',
                    'display_name': 'Pixel 8',
                    'avd_name': 'Pixel_8',
                    'no_netsim': True,
                    'pids': {2000},
                    'member_start_times': {
                        2000: 100.0
                    },
                }
            }
            erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (2000, 100.0)}
            return _host_tuple(
                {'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                qemu_stats={'emulator-5554': ('40.0%', '1800.0MB')})

        def fake_guest(serial, known_model, is_booting, status):
            return (_guest_model('Pixel 8'), False, '25.0%', '20.0%',
                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            try:
                f.result.return_value = fn(*args, **kwargs)
            except Exception as exc:
                f.result.side_effect = exc
            return f

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 2.0
            if tick[0] == 5:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 4)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary = mock_save.call_args[0][0]
        ts = list(summary['time_series'])
        cpu_vals = [r.get('emu_cpu_tot_Pixel_8_emulator-5554') for r in ts]
        self.assertEqual(cpu_vals, [25.0, 25.0, 25.0, 25.0])
        emu = summary['emulators']['Pixel_8_emulator-5554']
        self.assertTrue(emu['boot_state_known'])
        self.assertTrue(emu['boot_completed'])
        self.assertAlmostEqual(emu['first_seen'], 100.0)

    def test_pending_host_scan_launch_and_preadb_replacement_evict_stale_model(
            self):
        import emulator_resource_monitor as erm

        # 1) Timed-out host scan in flight on launch ('offline') preserves boot
        # tracking once the host scan finishes on the next tick.
        tick = [0]
        now_t = [100.0]
        dev_seq = [
            {},
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        pending_host_future = MagicMock()
        pending_host_future.done.return_value = False

        def submit_side_effect(fn, *args, **kwargs):
            if fn is erm.get_host_stats:
                if tick[0] == 0:
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple({}, {}, qemu_stats={})
                    return f
                if tick[0] == 1:
                    pending_host_future.done.side_effect = [
                        False, False, True, True
                    ]

                    def finish_host():
                        erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                        erm._LAST_EMU_GROUP_INFO = {
                            'emulator-5554': {
                                'primary_pid': 2000,
                                'primary_ctime': 102.0,
                                'avd_id': 'Pixel_8',
                                'display_name': 'Pixel 8',
                                'avd_name': 'Pixel_8',
                                'no_netsim': True,
                                'pids': {2000},
                                'member_start_times': {
                                    2000: 102.0
                                },
                            }
                        }
                        erm._LAST_PRIMARY_PROC_ID = {
                            'emulator-5554': (2000, 102.0)
                        }
                        return _host_tuple(
                            {'emulator-5554': 2000},
                            {'emulator-5554': 'Pixel_8'},
                            qemu_stats={'emulator-5554': ('50.0%', '1900.0MB')})

                    pending_host_future.result.side_effect = finish_host
                    return pending_host_future
                f = MagicMock()
                f.done.return_value = True
                f.result.return_value = _host_tuple(
                    {'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                    qemu_stats={'emulator-5554': ('45.0%', '1900.0MB')})
                return f
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_guest(serial, known_model, is_booting, status):
            if status != 'device':
                return (f'Unknown ({status})', bool(is_booting),
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})')
            return (_guest_model('Pixel 8'), tick[0] == 2, '60.0%', '50.0%',
                    '45.0% (1.8GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 6.0
            if tick[0] == 4:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = submit_side_effect
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('concurrent.futures.wait', return_value=(set(), set())), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 3)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emu = mock_save.call_args[0][0]['emulators']['Pixel_8_emulator-5554']
        self.assertTrue(emu['track_boot_stats'])
        self.assertGreaterEqual(len(emu['boot_log']), 1)

        # 2) Pre-ADB replacement within the 5s disconnect_times window must not
        # inherit the exited emulator's cached model.
        tick2 = [0]
        now_t2 = [200.0]
        dev_seq2 = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {},
            {
                'emulator-5554': 'offline'
            },
        ]

        def fake_host2():
            idx = min(tick2[0], 3)
            pid, avd, ct = ((1000, 'Pixel_7', 190.0) if idx <= 1 else
                            (2000, 'Pixel_8', 204.0))
            erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {pid}}
            erm._LAST_EMU_GROUP_INFO = {
                'emulator-5554': {
                    'primary_pid': pid,
                    'primary_ctime': ct,
                    'avd_id': avd,
                    'display_name': avd.replace('_', ' '),
                    'avd_name': avd,
                    'no_netsim': True,
                    'pids': {pid},
                    'member_start_times': {
                        pid: ct
                    },
                }
            }
            erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (pid, ct)}
            return _host_tuple(
                {'emulator-5554': pid}, {'emulator-5554': avd},
                qemu_stats={'emulator-5554': ('30.0%', '1800.0MB')})

        def fake_guest2(serial, known_model, is_booting, status):
            if status != 'device':
                return (f'Unknown ({status})', bool(is_booting),
                        f'N/A ({status})', f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})')
            return (_guest_model('Pixel 7'), False, '20.0%', '15.0%',
                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit2(fn, *args, **kwargs):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_sleep2(_):
            tick2[0] += 1
            now_t2[0] += 2.0
            if tick2[0] == 4:
                raise KeyboardInterrupt

        executor2 = MagicMock()
        executor2.submit.side_effect = fake_submit2
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor2), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq2[min(tick2[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host2), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest2), \
                patch('emulator_resource_monitor.save_summary') as mock_save2, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('time.monotonic', side_effect=lambda: now_t2[0]), \
                patch('time.sleep', side_effect=fake_sleep2), \
                patch('sys.argv', ['emulator_resource_monitor.py', '--headless',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emus2 = mock_save2.call_args[0][0]['emulators']
        self.assertNotIn('Pixel 7', emus2['Pixel_8_emulator-5554']['name'])
        self.assertIn('Unknown', emus2['Pixel_8_emulator-5554']['name'])

    def test_stale_offline_placeholder_columns_purged_from_timeseries_and_csv(
            self):
        import csv
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            tick = [0]
            now_t = [100.0]
            dev_seq = [
                {
                    'emulator-5554': 'offline',
                    'emulator-5556': 'offline',
                },
                {
                    'emulator-5554': 'offline',
                    'emulator-5556': 'offline',
                },
                {
                    'emulator-5554': 'device',
                    'emulator-5556': 'offline',
                },
            ]

            def fake_host():
                if tick[0] < 2:
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    return _host_tuple({}, {}, qemu_stats={})
                erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                erm._LAST_EMU_GROUP_INFO = {
                    'emulator-5554': {
                        'primary_pid': 2000,
                        'primary_ctime': 104.0,
                        'avd_id': 'Pixel_8',
                        'display_name': 'Pixel 8',
                        'avd_name': 'Pixel_8',
                        'no_netsim': True,
                        'pids': {2000},
                        'member_start_times': {
                            2000: 104.0
                        },
                    }
                }
                erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (2000, 104.0)}
                return _host_tuple(
                    {'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                    qemu_stats={'emulator-5554': ('30.0%', '1900.0MB')})

            def fake_guest(serial, known_model, is_booting, status):
                if status != 'device':
                    return (f'Unknown ({status})', bool(is_booting),
                            f'N/A ({status})', f'N/A ({status})',
                            f'N/A ({status})', f'N/A ({status})')
                return (_guest_model('Pixel 8'), False, '20.0%', '15.0%',
                        '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

            def fake_submit(fn, *args, **kwargs):
                f = MagicMock()
                f.done.return_value = True
                f.result.return_value = fn(*args, **kwargs)
                return f

            def fake_sleep(_):
                tick[0] += 1
                now_t[0] += 2.0
                if tick[0] == 3:
                    raise KeyboardInterrupt

            executor = MagicMock()
            executor.submit.side_effect = fake_submit
            with patch('concurrent.futures.ThreadPoolExecutor',
                       return_value=executor), \
                    patch('emulator_resource_monitor.get_devices',
                          side_effect=lambda: dev_seq[min(tick[0], 2)]), \
                    patch('emulator_resource_monitor.get_host_stats',
                          side_effect=fake_host), \
                    patch('emulator_resource_monitor.get_guest_stats',
                          side_effect=fake_guest), \
                    patch('emulator_resource_monitor.print_dashboard'), \
                    patch('time.monotonic', side_effect=lambda: now_t[0]), \
                    patch('time.sleep', side_effect=fake_sleep), \
                    patch('sys.argv', [
                        'emulator_resource_monitor.py', '--headless',
                        '--no-summary', '--csv', '-o', tmpdir, '--no-netsim-log'
                    ]), \
                    patch('os._exit', return_value=None):
                erm.main()

            csv_files = [p for p in os.listdir(tmpdir) if p.endswith('.csv')]
            self.assertEqual(len(csv_files), 1)
            with open(os.path.join(tmpdir, csv_files[0]), 'r',
                      encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            header = list(rows[0].keys())
            self.assertNotIn('emu_cpu_tot_emulator-5554', header)
            self.assertNotIn('netsim_state_emulator-5554', header)
            self.assertIn('emu_cpu_tot_Pixel_8_emulator-5554', header)
            self.assertIn('emu_cpu_tot_emulator-5556', header)
            self.assertEqual(rows[-1]['emu_cpu_tot_Pixel_8_emulator-5554'],
                             '20.0')

    def test_netsim_foreign_daemon_isolation_and_ini_uid_matching(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_f = os.path.join(tmpdir, 'foreign_art')
            os.makedirs(art_f, exist_ok=True)
            ini_f = os.path.join(tmpdir, 'netsim_2.ini')
            _write_ini(ini_f, 'grpc.port=43930\npid=5000701\n', mtime=1001.0)
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [tmpdir]

            real_ini_uid = os.stat(ini_f).st_uid or 1001
            my_uid = real_ini_uid + 10
            foreign_daemon = {
                'pid': 5000701,
                'create_time': 1000.0,
                'ppid': 1,
                'uid': real_ini_uid,
                'same_user': False,
                'cmdline': ['netsimd', '--instance', '2'],
                'environ': {
                    'TMPDIR': art_f
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            emu_g = {
                'primary_pid': 5000801,
                'primary_ctime': 1002.0,
                'avd_id': 'Pixel_8',
                'display_name': 'Pixel 8',
                'avd_name': 'Pixel_8',
                'grpc_endpoint': None,
                'no_netsim': False,
                'pids': {5000801},
                'member_start_times': {
                    5000801: 1002.0
                },
            }
            summary_emus = {
                'Pixel_8_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5000801,
                    'name': 'Pixel_8 | Pixel 8',
                    'first_seen': 10.0,
                }
            }
            with patch.object(erm, '_current_uid', return_value=my_uid), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([foreign_daemon],
                                               {'emulator-5554': {5000801}},
                                               {'emulator-5554': emu_g}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(summary_emus, {'emulator-5554': 'device'},
                               now_wall=1005.0,
                               now_mono=15.0)
            self.assertIsNone(
                tracker.emulator_records['Pixel_8_emulator-5554']['daemon_key'])

            # Root monitor (uid 0) may still pair with the sole active daemon.
            with patch.object(erm, '_current_uid', return_value=0), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([foreign_daemon],
                                               {'emulator-5554': {5000801}},
                                               {'emulator-5554': emu_g}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(summary_emus, {'emulator-5554': 'device'},
                               now_wall=1006.0,
                               now_mono=16.0)
            self.assertEqual(
                tracker.emulator_records['Pixel_8_emulator-5554']['daemon_key'],
                (5000701, 1000.0))

            # Step-3 unowned INI matching enforces owned_by_self == same_user.
            tracker2 = erm.NetsimTracker(pcap_dir=None,
                                         enable_log=False,
                                         out_dir=tmpdir)
            tracker2.custom_ini_dirs = [tmpdir]
            k_foreign = (5000710, 1000.0)
            k_self = (5000711, 995.0)
            tracker2.daemons[k_foreign] = {
                'start_wall': 1000.0,
                'create_time': 1000.0,
                'same_user': False,
                'instance_num': 1,
                'ini_path': None,
            }
            tracker2.daemons[k_self] = {
                'start_wall': 995.0,
                'create_time': 995.0,
                'same_user': True,
                'instance_num': 1,
                'ini_path': None,
            }
            ini_self = {
                'path': '/tmp/self/netsim.ini',
                'data': {
                    'grpc.port': 43929
                },
                'holders': [],
                'pid': None,
                'port': 43929,
                'instance': 1,
                'mtime': 1001.0,
                'owned_by_self': True,
            }
            with patch.object(erm, '_current_uid', return_value=my_uid):
                matched = tracker2._match_inis_to_daemons([ini_self],
                                                          [k_foreign, k_self])
            self.assertEqual(matched, [k_self])

    def test_netsim_disabled_and_settled_companions_excluded_from_live_counts(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            log_path = os.path.join(art_dir, 'netsim_stderr.log')
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    'netsim I 09-25 22:37:06.000 service.rs:1 - '
                    'Wrote to INI file /tmp/netsim.ini\n'
                    'netsim I 09-25 22:37:06.500 service.rs:2 - '
                    'DeviceActor: AddChip 0 (kind WIFI, name "wifi-0") to device 1 (unnamed)\n'
                )
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            _write_ini(ini_path,
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            daemon_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_active = {
                'primary_pid': 5000901,
                'primary_ctime': float(_START + 1),
                'avd_id': 'Pixel_8',
                'display_name': None,
                'avd_name': 'Pixel_8',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {5000901},
                'member_start_times': {
                    5000901: float(_START + 1)
                },
            }
            g_disabled = {
                'primary_pid': 5000902,
                'primary_ctime': float(_START + 1),
                'avd_id': 'Pixel_8',
                'display_name': 'Pixel 8',
                'avd_name': 'Pixel_8',
                'grpc_endpoint': None,
                'no_netsim': True,
                'pids': {5000902},
                'member_start_times': {
                    5000902: float(_START + 1)
                },
            }
            g_settled_companion = {
                'primary_pid': 5000903,
                'primary_ctime': float(_START),
                'avd_id': 'Pixel_8',
                'display_name': 'Pixel 8',
                'avd_name': 'Pixel_8',
                'grpc_endpoint': None,
                'no_netsim': False,
                'pids': {5000903},
                'member_start_times': {
                    5000903: float(_START)
                },
            }
            # Pre-populate a settled record whose companion (5000903) lingers,
            # plus a no_netsim record with stale netsim_daemon_ready_s.
            tracker.emulator_records['Pixel_8_emulator-5558'] = {
                'storage_key': 'Pixel_8_emulator-5558',
                'serial': 'emulator-5558',
                'avd_id': 'Pixel_8',
                'device_name': 'Pixel 8',
                'worker_pid': 5000999,
                'worker_create_time': float(_START),
                'group_pids': {5000903, 5000999},
                'group_procs': {
                    5000903: float(_START),
                    5000999: float(_START)
                },
                'worker_start_ts': float(_START),
                'first_seen_mono': 5.0,
                'min_log_ts': float(_START),
                'no_netsim': False,
                'state': 'DISCONNECTED',
                'ambiguous': False,
                'daemon_key': None,
                'netsim_daemon_ready_s': 1.0,
                'first_transport_ts': float(_START + 1),
                'netsim_connect_s': 1.0,
                'chip_attach_s': {
                    'WIFI': (1.0, False)
                },
                'chip_ids': set(),
                'unknown_chip_count': 0,
                'wifi_associated_s': None,
                'disconnect_count': 1,
                'connected_start_mono': None,
                'connected_total_s': 3.0,
                'adb_online_since_mono': None,
                'host_seen': True,
                'missing_since_mono': None,
                'missed_ticks': 0,
            }
            tracker.emulator_records['Pixel_8_emulator-5556'] = {
                'storage_key': 'Pixel_8_emulator-5556',
                'serial': 'emulator-5556',
                'avd_id': 'Pixel_8',
                'device_name': 'Pixel 8',
                'worker_pid': 5000902,
                'worker_create_time': float(_START + 1),
                'group_pids': {5000902},
                'group_procs': {
                    5000902: float(_START + 1)
                },
                'worker_start_ts': float(_START + 1),
                'first_seen_mono': 5.0,
                'min_log_ts': float(_START + 1),
                'no_netsim': True,
                'state': 'CONNECTING',
                'ambiguous': False,
                'daemon_key': (_DAEMON_A, float(_START)),
                'netsim_daemon_ready_s': 2.5,
                'first_transport_ts': None,
                'netsim_connect_s': None,
                'chip_attach_s': {},
                'chip_ids': set(),
                'unknown_chip_count': 0,
                'wifi_associated_s': None,
                'disconnect_count': 0,
                'connected_start_mono': None,
                'connected_total_s': 0.0,
                'adb_online_since_mono': None,
                'host_seen': True,
                'missing_since_mono': None,
                'missed_ticks': 0,
            }
            summary_emus = {
                'Pixel_8_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5000901,
                    'name': 'Pixel 8 | Pixel 8',
                    'first_seen': 10.0,
                },
                'Pixel_8_emulator-5556': {
                    'current_serial': 'emulator-5556',
                    'current_pid': 5000902,
                    'name': 'Pixel 8 | Pixel 8',
                    'first_seen': 10.0,
                },
                'Pixel_8_emulator-5558': {
                    'current_serial': 'emulator-5558',
                    'current_pid': 5000903,
                    'name': 'Pixel 8 | Pixel 8',
                    'first_seen': 5.0,
                },
            }
            with patch.object(
                    erm,
                    '_snapshot_process_state',
                    return_value=([daemon_proc], {
                        'emulator-5554': {5000901},
                        'emulator-5556': {5000902},
                        'emulator-5558': {5000903},
                    }, {
                        'emulator-5554': g_active,
                        'emulator-5556': g_disabled,
                        'emulator-5558': g_settled_companion,
                    }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(summary_emus, {'emulator-5554': 'device'},
                               now_wall=float(_START + 2),
                               now_mono=12.0)

            rec_active = tracker.emulator_records['Pixel_8_emulator-5554']
            rec_disabled = tracker.emulator_records['Pixel_8_emulator-5556']
            self.assertFalse(rec_active['ambiguous'])
            self.assertIn('WIFI', rec_active['chip_attach_s'])
            self.assertEqual(rec_disabled['state'], 'DISABLED')
            self.assertIsNone(rec_disabled['netsim_daemon_ready_s'])

            # Same display_name ('Pixel 8') alongside DISABLED + settled clones
            # stays unambiguous, and disconnect_count > 0 preserves ready_s.
            g_active_named = dict(g_active, display_name='Pixel 8')
            with patch.object(
                    erm,
                    '_snapshot_process_state',
                    return_value=([daemon_proc], {
                        'emulator-5554': {5000901},
                        'emulator-5556': {5000902},
                        'emulator-5558': {5000903},
                    }, {
                        'emulator-5554': g_active_named,
                        'emulator-5556': g_disabled,
                        'emulator-5558': g_settled_companion,
                    }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(summary_emus, {'emulator-5554': 'device'},
                               now_wall=float(_START + 3),
                               now_mono=13.0)
            self.assertFalse(rec_active['ambiguous'])

    def test_netsim_symlink_lock_pcap_dir_clean_exit_ini_and_oversize_bounds(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) get_file_lock_key rejects symlinked .lock files (POSIX major/minor).
            real_lock = os.path.join(tmpdir, 'netsim_2.ini.lock')
            sym_lock = os.path.join(tmpdir, 'netsim.ini.lock')
            _touch(real_lock, '')
            if hasattr(os, 'major') and _try_symlink(real_lock, sym_lock):
                self.assertIsNotNone(erm.get_file_lock_key(real_lock))
                self.assertIsNone(erm.get_file_lock_key(sym_lock))

            # 2) NetsimPcapManager rejects empty and symlinked artifact_dir / pcaps.
            real_target = os.path.join(tmpdir, 'real_target')
            os.makedirs(os.path.join(real_target, 'pcaps'), exist_ok=True)
            _touch(os.path.join(real_target, 'netsim_artifacts_1.zip'), 'zip')
            with open(
                    os.path.join(real_target, 'pcaps', 'netsim-1-P-WIFI.pcap'),
                    'wb') as f:
                f.write(b'\xd4' * 64)
            sym_art = os.path.join(tmpdir, 'sym_art')
            has_sym_art = _try_symlink(real_target,
                                       sym_art,
                                       target_is_directory=True)
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(out_dir, exist_ok=True)
            mgr = erm.NetsimPcapManager(pcap_dir='', out_dir=out_dir)
            cli_calls = []
            mgr._run_netsim_cli = lambda *a, **kw: (
                cli_calls.append(1) or
                'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                '1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n'
            )
            joined = [{
                'serial': 'emulator-5554',
                'device_name': 'Phone',
                'state': 'CONNECTED',
                'chip_ids': {'1'},
            }]
            bad_arts = ('', sym_art) if has_sym_art else ('',)
            for bad_art in bad_arts:
                mgr.last_harvest_by_daemon.clear()
                mgr.poll_and_manage_daemon(
                    {
                        'daemon_key': (5001001, 1000.0),
                        'pid': 5001001,
                        'start_wall': 1000.0,
                        'grpc_port': 43929,
                        'artifact_dir': bad_art,
                        'same_user': True,
                        'exited': False,
                    },
                    joined,
                    now_mono=10.0)
            self.assertEqual(cli_calls, [])
            self.assertEqual(mgr.copied_files, {})

            sym_pcaps_art = os.path.join(tmpdir, 'real_art_sym_pcaps')
            os.makedirs(sym_pcaps_art, exist_ok=True)
            if _try_symlink(os.path.join(real_target, 'pcaps'),
                            os.path.join(sym_pcaps_art, 'pcaps'),
                            target_is_directory=True):
                d_sym_pcaps = {
                    'daemon_key': (5001002, 1000.0),
                    'pid': 5001002,
                    'start_wall': 1000.0,
                    'grpc_port': 43929,
                    'artifact_dir': sym_pcaps_art,
                    'same_user': True,
                    'exited': False,
                }
                mgr.harvest_daemon_files(d_sym_pcaps, joined)
                self.assertEqual(mgr.copied_files, {})
                self.assertEqual(d_sym_pcaps.get('pcap_live_files', 0), 0)

            # 3) Clean daemon exit deleting live_ini does not fall back to a
            # leftover tailer.ini_path_logged file.
            art_dir = os.path.join(tmpdir, 'clean_art')
            os.makedirs(art_dir, exist_ok=True)
            stale_logged_ini = os.path.join(tmpdir, 'old_logged_netsim.ini')
            _touch(stale_logged_ini, 'grpc.port=43999\n')
            live_ini = os.path.join(tmpdir, 'netsim.ini')
            _write_ini(live_ini,
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            log_path = os.path.join(art_dir, 'netsim_stderr.log')
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    f'netsim I 09-25 22:37:06.000 s.rs:1 - INI file path: {stale_logged_ini}\n'
                )
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            d_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch.object(erm,
                              'resolve_netsim_artifact_dir',
                              return_value=art_dir), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_proc], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update({}, {},
                               now_wall=float(_START + 2),
                               now_mono=10.0)
            os.remove(live_ini)
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update({}, {},
                               now_wall=float(_START + 4),
                               now_mono=12.0)
            self.assertEqual(
                tracker.daemons[(_DAEMON_A, float(_START))]['exit_reason'],
                'clean_unknown')

            # 4) Oversized INIs (>64 KiB) are rejected rather than truncated,
            # and >16 MiB newline-free log chunks advance tailer.offset.
            huge_ini_dir = os.path.join(tmpdir, 'huge_ini_dir')
            os.makedirs(huge_ini_dir, exist_ok=True)
            huge_ini = os.path.join(huge_ini_dir, 'netsim.ini')
            pad = '# ' + ('x' * (erm._MAX_INI_BYTES - 14)) + '\n'
            _touch(huge_ini, pad + 'grpc.port=43929\n')
            self.assertGreater(os.path.getsize(huge_ini), erm._MAX_INI_BYTES)
            self.assertEqual(erm.parse_netsim_ini(huge_ini), {})
            tracker.custom_ini_dirs = [huge_ini_dir]
            self.assertEqual(tracker._discover_ini_files([]), [])

            big_log = os.path.join(tmpdir, 'newline_free.log')
            with open(big_log, 'wb') as f:
                f.write(b'A' * (erm._MAX_LOG_POLL_BYTES + 128))
            tailer = erm.NetsimLogTailer(big_log)
            self.assertTrue(tailer.poll(now_utc=float(_START + 5)))
            self.assertEqual(tailer.offset, erm._MAX_LOG_POLL_BYTES)

    def test_netsim_daemon_exit_before_worker_does_not_hijack_bystander_daemon(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            dir_a = os.path.join(tmpdir, 'inst1')
            dir_b = os.path.join(tmpdir, 'inst2')
            art_a = os.path.join(dir_a, 'android-user', 'netsimd')
            art_b = os.path.join(dir_b, 'android-user', 'netsimd')
            os.makedirs(art_a, exist_ok=True)
            os.makedirs(art_b, exist_ok=True)
            _write_ini(os.path.join(dir_a, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            _write_ini(os.path.join(dir_b, 'netsim_2.ini'),
                       f'pid={_DAEMON_B}\ngrpc.port=43930\n',
                       mtime=float(_START + 1))
            _touch(
                os.path.join(art_a, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:06.000 s.rs:1 - '
                'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel 8)\n')
            _touch(
                os.path.join(art_b, 'netsim_2_stderr.log'),
                'netsim I 09-25 22:37:06.400 s.rs:1 - '
                'DeviceActor: AddChip 2 (kind WIFI) to device 1 (Pixel 8)\n'
                'netsim I 09-25 22:37:16.000 ap.rs:1 - '
                'ApActor: Associated 02:15:b2:00:00:00\n')

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir_a, dir_b]
            d_a = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_a,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_b = {
                'pid': _DAEMON_B,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd', '--instance', '2'],
                'environ': {
                    'TMPDIR': dir_b,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_a = {
                'primary_pid': 5001101,
                'primary_ctime': float(_START),
                'avd_id': 'Pixel_8_A',
                'display_name': 'Pixel 8',
                'avd_name': 'Pixel_8_A',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {5001101},
                'member_start_times': {
                    5001101: float(_START)
                },
            }
            g_b = {
                'primary_pid': 5001102,
                'primary_ctime': float(_START),
                'avd_id': 'Pixel_8_B',
                'display_name': 'Pixel 8',
                'avd_name': 'Pixel_8_B',
                'grpc_endpoint': '127.0.0.1:43930',
                'no_netsim': False,
                'pids': {5001102},
                'member_start_times': {
                    5001102: float(_START)
                },
            }
            summary_emus = {
                'Pixel_8_A_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5001101,
                    'name': 'Pixel 8',
                    'first_seen': 10.0,
                },
                'Pixel_8_B_emulator-5556': {
                    'current_serial': 'emulator-5556',
                    'current_pid': 5001102,
                    'name': 'Pixel 8',
                    'first_seen': 10.0,
                },
            }
            with patch.object(
                    erm,
                    '_snapshot_process_state',
                    return_value=([d_a, d_b], {
                        'emulator-5554': {5001101},
                        'emulator-5556': {5001102},
                    }, {
                        'emulator-5554': g_a,
                        'emulator-5556': g_b,
                    }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51001}],
                            43930: [{'serial': 'emulator-5556', 'peer_port': 51002}],
                        }):
                tracker.update(summary_emus, {
                    'emulator-5554': 'device',
                    'emulator-5556': 'device',
                },
                               now_wall=float(_START + 12),
                               now_mono=12.0)

            # DAEMON_A exits before Pixel_8_A's VM worker (Quickboot save), while
            # bystander DAEMON_B stays active.
            os.remove(os.path.join(dir_a, 'netsim.ini'))
            with patch.object(
                    erm,
                    '_snapshot_process_state',
                    return_value=([d_b], {
                        'emulator-5554': {5001101},
                        'emulator-5556': {5001102},
                    }, {
                        'emulator-5554': g_a,
                        'emulator-5556': g_b,
                    }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43930: [{'serial': 'emulator-5556', 'peer_port': 51002}],
                        }):
                tracker.update(summary_emus, {
                    'emulator-5554': 'offline',
                    'emulator-5556': 'device',
                },
                               now_wall=float(_START + 18),
                               now_mono=18.0)

            rec_a = tracker.emulator_records['Pixel_8_A_emulator-5554']
            rec_b = tracker.emulator_records['Pixel_8_B_emulator-5556']
            self.assertEqual(rec_a['daemon_key'], (_DAEMON_A, float(_START)))
            self.assertEqual(rec_a['state'], 'DISCONNECTED')
            self.assertEqual(rec_a['chip_attach_s'], {'WIFI': (1.0, False)})
            self.assertAlmostEqual(rec_b['wifi_associated_s'], 11.0)

    def test_netsim_prior_unnamed_bare_session_preserved_and_reused_pid_migrated(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            _write_ini(os.path.join(tmpdir, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            _touch(os.path.join(art_dir, 'netsim_stderr.log'), '')
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            d_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }

            # 1) Session 1: unnamed bare emulator-5554 connects for 20s and exits.
            g1 = _emu_group(5001201, None, None, ctime=_START)
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([d_proc], {'emulator-5554': {5001201}},
                                            {'emulator-5554': g1}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51001}]
                        }):
                tracker.update(
                    {
                        'emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5001201,
                            'name': 'emulator-5554',
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 10),
                    now_mono=10.0)
            for t_off in (30.0, 32.0):
                with patch.object(erm, '_snapshot_process_state',
                                  return_value=([d_proc], {}, {}, {})), \
                        patch.object(tracker, '_read_proc_locks',
                                     return_value={}), \
                        patch.object(tracker, '_collect_loopback_grpc_sockets',
                                     return_value={}):
                    tracker.update({}, {},
                                   now_wall=float(_START + t_off),
                                   now_mono=t_off)
            self.assertEqual(
                tracker.emulator_records['emulator-5554']['disconnect_count'],
                1)
            self.assertAlmostEqual(
                tracker.emulator_records['emulator-5554']['connected_total_s'],
                20.0)

            # Session 2: Pixel_8 starts with 1 bare-serial tick, then migrates.
            g2_bare = _emu_group(5001202, None, None, ctime=_START + 40)
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc], {'emulator-5554': {5001202}},
                                  {'emulator-5554': g2_bare}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(
                    {
                        'emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5001202,
                            'name': 'emulator-5554',
                            'first_seen': 40.0,
                        }
                    }, {},
                    now_wall=float(_START + 40),
                    now_mono=40.0)

            g2_named = _emu_group(5001202,
                                  'Pixel_8',
                                  'Pixel 8',
                                  ctime=_START + 40)
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc], {'emulator-5554': {5001202}},
                                  {'emulator-5554': g2_named}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(
                    {
                        'Pixel_8_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5001202,
                            'name': 'Pixel_8 | Pixel 8',
                            'first_seen': 40.0,
                        }
                    }, {},
                    now_wall=float(_START + 42),
                    now_mono=42.0)

            self.assertIn('emulator-5554', tracker.emulator_records)
            self.assertEqual(
                tracker.emulator_records['emulator-5554']['disconnect_count'],
                1)
            self.assertAlmostEqual(
                tracker.emulator_records['emulator-5554']['connected_total_s'],
                20.0)
            self.assertEqual(
                tracker.emulator_records['Pixel_8_emulator-5554']
                ['disconnect_count'], 0)
            self.assertAlmostEqual(
                tracker.emulator_records['Pixel_8_emulator-5554']
                ['connected_total_s'], 0.0)

            # 2) Reused-PID same-AVD bare-serial restart migrates cleanly.
            tracker2 = erm.NetsimTracker(pcap_dir=None,
                                         enable_log=True,
                                         out_dir=tmpdir,
                                         os_family='linux')
            tracker2.custom_ini_dirs = [tmpdir]
            p_pid = 5001210
            g_run1 = _emu_group(p_pid, 'Phone', 'Phone', ctime=_START)
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc], {'emulator-5554': {p_pid}},
                                  {'emulator-5554': g_run1}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker2,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51010}]
                        }):
                tracker2.update(
                    {
                        'Phone_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': p_pid,
                            'name': 'Phone',
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 10),
                    now_mono=10.0)
            for t_off in (20.0, 22.0):
                with patch.object(erm, '_snapshot_process_state',
                                  return_value=([d_proc], {}, {}, {})), \
                        patch.object(tracker2, '_read_proc_locks',
                                     return_value={}), \
                        patch.object(tracker2, '_collect_loopback_grpc_sockets',
                                     return_value={}):
                    tracker2.update({}, {},
                                    now_wall=float(_START + t_off),
                                    now_mono=t_off)

            g_run2_bare = _emu_group(p_pid, None, None, ctime=_START + 60)
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc], {'emulator-5554': {p_pid}},
                                  {'emulator-5554': g_run2_bare}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker2,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51011}]
                        }):
                tracker2.update(
                    {
                        'Phone_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': p_pid,
                            'name': 'Phone',
                            'first_seen': 10.0,
                        },
                        'emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': p_pid,
                            'name': 'emulator-5554',
                            'first_seen': 60.0,
                        },
                    }, {},
                    now_wall=float(_START + 61),
                    now_mono=61.0)
            g_run2_named = _emu_group(p_pid,
                                      'Phone',
                                      'Phone',
                                      ctime=_START + 60)
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc], {'emulator-5554': {p_pid}},
                                  {'emulator-5554': g_run2_named}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(tracker2, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker2.update(
                    {
                        'Phone_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': p_pid,
                            'name': 'Phone',
                            'first_seen': 60.0,
                        },
                    }, {},
                    now_wall=float(_START + 63),
                    now_mono=63.0)
            self.assertEqual(list(tracker2.emulator_records.keys()),
                             ['Phone_emulator-5554'])
            self.assertEqual(
                tracker2.emulator_records['Phone_emulator-5554']
                ['first_transport_ts'], float(_START + 61))
            self.assertEqual(
                tracker2.emulator_records['Phone_emulator-5554']['min_log_ts'],
                float(_START + 60))

    def test_netsim_pcap_prechip_recheck_and_duplicate_name_chip_disambiguation(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Pre-chip CONNECTING poll does not block immediate auto-enable
            # when WIFI and staggered BT attach within 10s.
            art_dir = os.path.join(tmpdir, 'art')
            pcaps_dir = os.path.join(art_dir, 'pcaps')
            os.makedirs(pcaps_dir, exist_ok=True)
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(out_dir, exist_ok=True)
            mgr = erm.NetsimPcapManager(pcap_dir='', out_dir=out_dir)
            rows_state = []
            patch_calls = []

            def fake_cli(_bin, _port, args, timeout=5.0, **_):
                if args[:2] == ['capture', 'list']:
                    hdr = 'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                    body = ''.join(
                        f"{r['id']} | {r['device_name']} | {r['raw_chip_kind']} | {r['state']} | 00:00:00 | 0 | 0\n"
                        for r in rows_state)
                    return hdr + body
                if args[:2] == ['capture', 'patch']:
                    patch_calls.append(list(args))
                    for m in erm.simulate_netsim_capture_filter(
                            rows_state, args[3:]):
                        m['state'] = args[2]
                    return 'OK'
                return ''

            mgr._run_netsim_cli = fake_cli
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            _touch(ini_path, 'grpc.port=43929\npid=5001300\n')
            daemon_info = {
                'daemon_key': (5001300, 1000.0),
                'pid': 5001300,
                'start_wall': 1000.0,
                'grpc_port': 43929,
                'ini_path': ini_path,
                'artifact_dir': art_dir,
                'same_user': True,
                'exited': False,
            }
            emu = {
                'serial': 'emulator-5554',
                'device_name': 'Phone',
                'state': 'CONNECTING',
                'chip_attach_s': {},
                'chip_ids': set(),
                'unknown_chip_count': 0,
            }
            mgr.poll_and_manage_daemon(daemon_info, [emu], now_mono=10.0)
            self.assertEqual(patch_calls, [])

            # WIFI attaches at t=12.0 (< 20.0s interval): must auto-enable now.
            rows_state.append({
                'id': 1,
                'device_name': 'Phone',
                'chip_kind': 'WIFI',
                'raw_chip_kind': 'WIFI',
                'state': 'off',
            })
            emu['state'] = 'CONNECTED'
            emu['chip_attach_s'] = {'WIFI': (1.0, False)}
            emu['chip_ids'] = {'1'}
            mgr.poll_and_manage_daemon(daemon_info, [emu], now_mono=12.0)
            self.assertEqual(rows_state[0]['state'], 'on')

            # Staggered BT attaches at t=14.0 (< 22.0s): must auto-enable now.
            rows_state.append({
                'id': 2,
                'device_name': 'Phone',
                'chip_kind': 'BT',
                'raw_chip_kind': 'BLUETOOTH_LOW_ENERGY',
                'state': 'off',
            })
            emu['chip_attach_s']['BT'] = (3.0, False)
            emu['chip_ids'].add('2')
            mgr.poll_and_manage_daemon(daemon_info, [emu], now_mono=14.0)
            self.assertEqual([r['state'] for r in rows_state], ['on', 'on'])

            # 2) Duplicate chip_id claims across distinct serials route to shared/,
            # and AddChip events with peer_port disambiguate duplicate display names.
            with open(os.path.join(pcaps_dir, 'netsim-99-Pixel_8-WIFI.pcap'),
                      'wb') as f:
                f.write(b'\xd4' * 64)
            mgr.harvest_daemon_files(daemon_info, [
                {
                    'serial': 'emulator-5554',
                    'device_name': 'Pixel 8',
                    'state': 'CONNECTED',
                    'chip_ids': {'99'},
                },
                {
                    'serial': 'emulator-5556',
                    'device_name': 'Pixel 8',
                    'state': 'CONNECTED',
                    'chip_ids': {'99'},
                },
            ])
            self.assertTrue(
                any(os.sep + 'shared' + os.sep in p for p in mgr.copied_files),
                mgr.copied_files)

            art2 = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art2, exist_ok=True)
            _write_ini(os.path.join(tmpdir, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            _touch(
                os.path.join(art2, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:06.100 s.rs:1 - '
                'DeviceActor: AddChipByGuid for device ipv4:127.0.0.1:51001\n'
                'netsim I 09-25 22:37:06.100 s.rs:2 - '
                'DeviceActor: AddChip 10 (kind WIFI) to device 1 (Pixel 8)\n')
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            d_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g54 = _emu_group(5001310, 'Pixel_8_1', 'Pixel 8', ctime=_START)
            g56 = _emu_group(5001311, 'Pixel_8_2', 'Pixel 8', ctime=_START)
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc], {
                        'emulator-5554': {5001310},
                        'emulator-5556': {5001311},
                    }, {
                        'emulator-5554': g54,
                        'emulator-5556': g56,
                    }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [
                                {'serial': 'emulator-5554', 'peer_port': 51001},
                                {'serial': 'emulator-5556', 'peer_port': 51002},
                            ]
                        }):
                tracker.update(
                    {
                        'Pixel_8_1_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5001310,
                            'name': 'Pixel 8',
                            'first_seen': 10.0,
                        },
                        'Pixel_8_2_emulator-5556': {
                            'current_serial': 'emulator-5556',
                            'current_pid': 5001311,
                            'name': 'Pixel 8',
                            'first_seen': 10.0,
                        },
                    }, {
                        'emulator-5554': 'device',
                        'emulator-5556': 'device',
                    },
                    now_wall=float(_START + 5),
                    now_mono=10.0)
            self.assertEqual(
                tracker.emulator_records['Pixel_8_1_emulator-5554']['chip_ids'],
                {'10'})
            self.assertFalse(tracker.emulator_records['Pixel_8_1_emulator-5554']
                             ['ambiguous'])
            self.assertEqual(
                tracker.emulator_records['Pixel_8_2_emulator-5556']['chip_ids'],
                set())

    def test_recycled_pid_precise_start_guard_and_zero_grpc_port_rejected(self):
        import emulator_resource_monitor as erm

        if os.path.exists('/proc/uptime'):
            stale_ctime = 1_700_000_000.0
            self.assertEqual(
                erm.get_proc_precise_start_time(
                    os.getpid(), fallback_create_time=stale_ctime), stale_ctime)

        with tempfile.TemporaryDirectory() as tmpdir:
            zero_ini = os.path.join(tmpdir, 'netsim.ini')
            _touch(zero_ini, 'grpc.port=0\npid=5001400\n')
            obs = erm._read_ini_observation(zero_ini, {})
            self.assertIsNone(obs['port'])

            tailer = erm.NetsimLogTailer(os.path.join(tmpdir, 'missing.log'))
            tailer.feed_line(
                'netsim I 09-25 22:37:06.000 s.rs:1 - gRPC port: 0',
                now_utc=float(_START + 1))
            self.assertIsNone(tailer.grpc_port_logged)


class TestRound8Fixes(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)

    def test_evicted_emulator_not_overwritten_on_pending_host_or_remote_reconnect(
            self):
        import emulator_resource_monitor as erm

        tick = [0]
        now_t = [100.0]
        dev_seq = [
            {
                'emulator-5554': 'device',
                'HT4A1234': 'device'
            },
            {
                'emulator-5554': 'device',
                'HT4A1234': 'device'
            },
            {},
            {},
            {
                'emulator-5554': 'device',
                'HT4A1234': 'device'
            },
            {
                'emulator-5554': 'device',
                'HT4A1234': 'device'
            },
        ]
        pending_host = MagicMock()
        dash_guest_snapshots = []

        def submit_side_effect(fn, *args, **kwargs):
            if fn is erm.get_host_stats:
                idx = tick[0]
                if idx <= 1:
                    erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {1000}}
                    erm._LAST_EMU_GROUP_INFO = {
                        'emulator-5554': {
                            'primary_pid': 1000,
                            'primary_ctime': 90.0,
                            'avd_id': 'Pixel_7',
                            'display_name': 'Pixel 7',
                            'avd_name': 'Pixel_7',
                            'no_netsim': True,
                            'pids': {1000},
                            'member_start_times': {
                                1000: 90.0
                            },
                        }
                    }
                    erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (1000, 90.0)}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple(
                        {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_7'},
                        qemu_stats={'emulator-5554': ('30.0%', '1500.0MB')})
                    return f
                if idx in (2, 3):
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple({}, {}, qemu_stats={})
                    return f
                if idx == 4:
                    pending_host.done.side_effect = [False, False, True, True]

                    def finish_p8():
                        erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                        erm._LAST_EMU_GROUP_INFO = {
                            'emulator-5554': {
                                'primary_pid': 2000,
                                'primary_ctime': 114.0,
                                'avd_id': 'Pixel_8',
                                'display_name': 'Pixel 8',
                                'avd_name': 'Pixel_8',
                                'no_netsim': True,
                                'pids': {2000},
                                'member_start_times': {
                                    2000: 114.0
                                },
                            }
                        }
                        erm._LAST_PRIMARY_PROC_ID = {
                            'emulator-5554': (2000, 114.0)
                        }
                        return _host_tuple(
                            {'emulator-5554': 2000},
                            {'emulator-5554': 'Pixel_8'},
                            qemu_stats={'emulator-5554': ('50.0%', '1900.0MB')})

                    pending_host.result.side_effect = finish_p8
                    return pending_host
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_guest(serial, known_model, is_booting, status):
            if serial == 'HT4A1234':
                return (_guest_model('Pixel 9 Pro'), False, '15.0%', '15.0%',
                        '35.0% (2.8GB/8.0GB, 0 errs, 10% data)', '0.0 MB')
            if tick[0] <= 1:
                return (_guest_model('Pixel 7'), False, '20.0%', '20.0%',
                        '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')
            return (_guest_model('Pixel 8'), False, '80.0%', '80.0%',
                    '50.0% (2.0GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def capture_dash(*args, **kwargs):
            dash_guest_snapshots.append(dict(args[4]))

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += (8.0 if tick[0] == 3 else 2.0)
            if tick[0] == 6:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = submit_side_effect
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('concurrent.futures.wait', return_value=(set(), set())), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 5)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=capture_dash), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        # [DISCONNECTED] card must still display after >5s eviction (tick 3)
        self.assertIn('[DISCONNECTED]',
                      dash_guest_snapshots[3]['emulator-5554'][1])

        summary = mock_save.call_args[0][0]
        emus = summary['emulators']
        self.assertNotIn('Pixel 8', emus['Pixel_7_emulator-5554']['name'])
        self.assertEqual(emus['Pixel_7_emulator-5554']['cpu_iterations'], 1)
        self.assertAlmostEqual(emus['Pixel_7_emulator-5554']['cpu_max'], 20.0)
        self.assertEqual(emus['Pixel_8_emulator-5554']['cpu_iterations'], 2)
        self.assertAlmostEqual(emus['Pixel_8_emulator-5554']['cpu_max'], 80.0)
        self.assertEqual(emus['HT4A1234']['cpu_iterations'], 3)

        # Host exit during <=5s ADB absence clears serial_pids and records
        # host_exited_serials so a recycled PID without start_time evicts.
        d_names = {'emulator-5554': 'Pixel 7'}
        disc_t = {}
        s_pids = {'emulator-5554': 1000}
        s_grp = {'emulator-5554': {1000}}
        seen_off, h_exited = set(), set()
        self.assertEqual(
            erm.update_device_cache(d_names,
                                    disc_t,
                                    s_pids,
                                    set(), {},
                                    102.0,
                                    serial_group_pids=s_grp,
                                    host_fresh=True,
                                    seen_offline_without_host=seen_off,
                                    host_exited_serials=h_exited), set())
        self.assertEqual(s_pids, {})
        self.assertEqual(h_exited, {'emulator-5554'})
        self.assertEqual(
            erm.update_device_cache(d_names,
                                    disc_t,
                                    s_pids,
                                    set(), {'emulator-5554': 1000},
                                    103.0,
                                    serial_group_pids=s_grp,
                                    host_fresh=True,
                                    seen_offline_without_host=seen_off,
                                    host_exited_serials=h_exited),
            {'emulator-5554'})
        self.assertEqual(d_names, {})

    def test_netsim_pcap_duplicate_device_name_throttle_and_chip_attach_recheck(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'art')
            os.makedirs(os.path.join(art_dir, 'pcaps'), exist_ok=True)
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(out_dir, exist_ok=True)

            # 1) Two same-named clones with differing chip counts must still
            # respect the 10s CLI throttle once both are checked.
            mgr = erm.NetsimPcapManager(pcap_dir='', out_dir=out_dir)
            list_calls = [0]

            def fake_cli(_bin, _port, args, timeout=5.0, **_):
                if args[:2] == ['capture', 'list']:
                    list_calls[0] += 1
                    return (
                        'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                        '1  | Pixel 8     | WIFI      | on    | 00:00:00  | 0       | 0\n'
                        '2  | Pixel 8     | BLUETOOTH_LOW_ENERGY | on | 00:00:00 | 0 | 0\n'
                        '3  | Pixel 8     | WIFI      | on    | 00:00:00  | 0       | 0\n'
                    )
                return 'OK'

            mgr._run_netsim_cli = fake_cli
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            _touch(ini_path, 'grpc.port=43929\npid=5002000\n')
            daemon_info = {
                'daemon_key': (5002000, 1000.0),
                'pid': 5002000,
                'start_wall': 1000.0,
                'grpc_port': 43929,
                'ini_path': ini_path,
                'artifact_dir': art_dir,
                'same_user': True,
                'exited': False,
            }
            emu_a = {
                'serial': 'emulator-5554',
                'device_name': 'Pixel 8',
                'state': 'CONNECTED',
                'chip_attach_s': {
                    'WIFI': (1.0, False),
                    'BT': (1.5, False)
                },
                'chip_ids': {'1', '2'},
                'unknown_chip_count': 0,
            }
            emu_b = {
                'serial': 'emulator-5556',
                'device_name': 'Pixel 8',
                'state': 'CONNECTED',
                'chip_attach_s': {
                    'WIFI': (1.2, False)
                },
                'chip_ids': {'3'},
                'unknown_chip_count': 0,
            }
            for t in (10.0, 12.0, 14.0, 16.0, 18.0):
                mgr.poll_and_manage_daemon(daemon_info, [emu_a, emu_b],
                                           now_mono=t)
            self.assertEqual(list_calls[0], 1)

            # Same-count chip_id replacement ('3' -> '4') triggers immediate recheck.
            emu_b['chip_ids'] = {'4'}
            mgr.poll_and_manage_daemon(daemon_info, [emu_a, emu_b],
                                       now_mono=19.0)
            self.assertEqual(list_calls[0], 2)

            # 2) First duplicate-name clone attaching a chip within 10s must
            # immediately trigger capture list + auto-enable.
            mgr2 = erm.NetsimPcapManager(pcap_dir='', out_dir=out_dir)
            rows_state = [{
                'id': 1,
                'device_name': 'Pixel 8',
                'chip_kind': 'WIFI',
                'raw_chip_kind': 'WIFI',
                'state': 'on',
            }]

            def fake_cli2(_bin, _port, args, timeout=5.0, **_):
                if args[:2] == ['capture', 'list']:
                    hdr = 'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                    body = ''.join(
                        f"{r['id']} | {r['device_name']} | {r['raw_chip_kind']} | {r['state']} | 00:00:00 | 0 | 0\n"
                        for r in rows_state)
                    return hdr + body
                if args[:2] == ['capture', 'patch']:
                    for m in erm.simulate_netsim_capture_filter(
                            rows_state, args[3:]):
                        m['state'] = args[2]
                    return 'OK'
                return ''

            mgr2._run_netsim_cli = fake_cli2
            e1 = {
                'serial': 'emulator-5554',
                'device_name': 'Pixel 8',
                'state': 'CONNECTING',
                'chip_attach_s': {},
                'chip_ids': set(),
                'unknown_chip_count': 0,
            }
            e2 = {
                'serial': 'emulator-5556',
                'device_name': 'Pixel 8',
                'state': 'CONNECTED',
                'chip_attach_s': {
                    'WIFI': (1.0, False)
                },
                'chip_ids': {'1'},
                'unknown_chip_count': 0,
            }
            mgr2.poll_and_manage_daemon(daemon_info, [e1, e2], now_mono=10.0)
            e1['state'] = 'CONNECTED'
            e1['chip_attach_s'] = {'WIFI': (2.0, False)}
            e1['chip_ids'] = {'2'}
            rows_state.append({
                'id': 2,
                'device_name': 'Pixel 8',
                'chip_kind': 'WIFI',
                'raw_chip_kind': 'WIFI',
                'state': 'off',
            })
            mgr2.poll_and_manage_daemon(daemon_info, [e1, e2], now_mono=12.0)
            self.assertEqual(rows_state[1]['state'], 'on')

    def test_netsim_duplicate_name_other_peer_ports_connections_and_bystander_isolation(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Unconnected duplicate-name clone must not claim sibling's
            # peer_port AddChip or connection timestamp.
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            _write_ini(os.path.join(tmpdir, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            _touch(
                os.path.join(art_dir, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:06.000 s.rs:1 - Handling new connection for WIFI on device Pixel 8\n'
                'netsim I 09-25 22:37:06.100 s.rs:2 - DeviceActor: AddChipByGuid for device ipv4:127.0.0.1:51001\n'
                'netsim I 09-25 22:37:06.100 s.rs:3 - DeviceActor: AddChip 10 (kind WIFI) to device 1 (Pixel 8)\n'
            )
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            d_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g54 = _emu_group(5003101, 'Pixel_8_1', 'Pixel 8', ctime=_START)
            g56 = _emu_group(5003102, 'Pixel_8_2', 'Pixel 8', ctime=_START)
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc], {
                        'emulator-5554': {5003101},
                        'emulator-5556': {5003102},
                    }, {
                        'emulator-5554': g54,
                        'emulator-5556': g56,
                    }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51001}]
                        }):
                tracker.update(
                    {
                        'Pixel_8_1_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5003101,
                            'name': 'Pixel_8_1 | Pixel 8',
                            'first_seen': 10.0,
                        },
                        'Pixel_8_2_emulator-5556': {
                            'current_serial': 'emulator-5556',
                            'current_pid': 5003102,
                            'name': 'Pixel_8_2 | Pixel 8',
                            'first_seen': 10.0,
                        },
                    }, {
                        'emulator-5554': 'device',
                        'emulator-5556': 'offline'
                    },
                    now_wall=float(_START + 5),
                    now_mono=10.0)
            rec54 = tracker.emulator_records['Pixel_8_1_emulator-5554']
            rec56 = tracker.emulator_records['Pixel_8_2_emulator-5556']
            self.assertEqual(rec54['state'], 'CONNECTED')
            self.assertEqual(rec54['chip_ids'], {'10'})
            self.assertEqual(rec56['state'], 'CONNECTING')
            self.assertEqual(rec56['chip_ids'], set())
            self.assertIsNone(rec56['first_transport_ts'])

            # 2) Cross-artifact_dir bystander daemon must not be hijacked when
            # an emulator without grpc_endpoint sees its own daemon exit first.
            dir_a = os.path.join(tmpdir, 'harness_a')
            dir_b = os.path.join(tmpdir, 'harness_b')
            art_a = os.path.join(dir_a, 'android-user', 'netsimd')
            art_b = os.path.join(dir_b, 'android-user', 'netsimd')
            os.makedirs(art_a, exist_ok=True)
            os.makedirs(art_b, exist_ok=True)
            _write_ini(os.path.join(dir_a, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            _write_ini(os.path.join(dir_b, 'netsim.ini'),
                       f'pid={_DAEMON_B}\ngrpc.port=43930\n',
                       mtime=float(_START + 3))
            _touch(
                os.path.join(art_a, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:06.000 s.rs:1 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (PhoneA)\n'
            )
            _touch(
                os.path.join(art_b, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:10.000 s.rs:1 - ApActor: Associated 02:00:00:00:00:01\n'
            )
            tracker2 = erm.NetsimTracker(pcap_dir=None,
                                         enable_log=True,
                                         out_dir=tmpdir,
                                         os_family='linux')
            tracker2.custom_ini_dirs = [dir_a, dir_b]
            da_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_a,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            db_proc = {
                'pid': _DAEMON_B,
                'create_time': float(_START + 2),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_b,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_a = _emu_group(5005101, 'PhoneA', 'PhoneA', ctime=_START)
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([da_proc, db_proc],
                                            {'emulator-5554': {5005101}},
                                            {'emulator-5554': g_a}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker2,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51001}],
                            43930: [],
                        }):
                tracker2.update(
                    {
                        'PhoneA_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5005101,
                            'name': 'PhoneA',
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 5),
                    now_mono=10.0)
            os.remove(os.path.join(dir_a, 'netsim.ini'))
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([db_proc],
                                            {'emulator-5554': {5005101}},
                                            {'emulator-5554': g_a}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(tracker2, '_collect_loopback_grpc_sockets',
                                 return_value={43930: []}):
                tracker2.update(
                    {
                        'PhoneA_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5005101,
                            'name': 'PhoneA',
                            'first_seen': 10.0,
                        }
                    }, {'emulator-5554': 'offline'},
                    now_wall=float(_START + 12),
                    now_mono=17.0)
            rec_a = tracker2.emulator_records['PhoneA_emulator-5554']
            self.assertEqual(rec_a['daemon_key'], (_DAEMON_A, float(_START)))
            self.assertEqual(rec_a['state'], 'DISCONNECTED')
            self.assertEqual(rec_a['chip_attach_s'], {'WIFI': (1.0, False)})

            self.assertEqual(
                erm.NetsimTracker._effective_daemon_instance({
                    'ini_path': '/tmp/netsim_2.ini',
                    'instance_num': None,
                }), 2)
            bare_abort = {
                'storage_key': 'emulator-5554',
                'serial': 'emulator-5554',
                'worker_pid': 5005201,
                'state': 'CONNECTING',
                'chip_attach_s': {},
                'chip_ids': set(),
                'disconnect_count': 0,
                'connected_total_s': 0.0,
            }
            erm.NetsimTracker._reset_for_new_worker(bare_abort,
                                                    5005202,
                                                    float(_START + 20),
                                                    False,
                                                    20.0,
                                                    worker_ctime=float(_START +
                                                                       20),
                                                    prev_exit='startup_timeout')
            self.assertEqual(bare_abort['_prior_bare_snapshot'][0]['state'],
                             'FAILED(launcher_abort)')

    def test_netsim_symlink_artifact_dir_tailer_oversize_lines_and_port_zero_bounds(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) NetsimTracker rejects symlinked artifact_dir for logs and stats.
            secret_dir = os.path.join(tmpdir, 'secret_dir')
            os.makedirs(secret_dir, exist_ok=True)
            _touch(os.path.join(secret_dir, 'netsim_stderr.log'),
                   'netsim I 09-25 22:37:06.000 s.rs:1 - gRPC port: 44444\n')
            stats_f = os.path.join(secret_dir, 'netsim_session_stats.json')
            _touch(stats_f, '{"version": "9.9.9-secret"}')
            os.utime(stats_f, (float(_START + 2), float(_START + 2)))
            evil_tmp = os.path.join(tmpdir, 'evil_tmp')
            os.makedirs(os.path.join(evil_tmp, 'android-evil'), exist_ok=True)
            if _try_symlink(secret_dir,
                            os.path.join(evil_tmp, 'android-evil', 'netsimd'),
                            target_is_directory=True):
                tracker = erm.NetsimTracker(pcap_dir=None,
                                            enable_log=True,
                                            out_dir=tmpdir,
                                            os_family='linux')
                tracker.custom_ini_dirs = [tmpdir]
                d_sym = {
                    'pid': 5004001,
                    'create_time': float(_START),
                    'ppid': 1,
                    'same_user': True,
                    'cmdline': ['netsimd'],
                    'environ': {
                        'TMPDIR': evil_tmp,
                        'USER': 'evil'
                    },
                    'exe': '/opt/sdk/emulator/netsimd',
                }
                with patch.object(erm, '_snapshot_process_state',
                                  return_value=([d_sym], {}, {}, {})), \
                        patch.object(tracker, '_read_proc_locks', return_value={}), \
                        patch.object(tracker, '_collect_loopback_grpc_sockets',
                                     return_value={}):
                    tracker.update({}, {},
                                   now_wall=float(_START + 5),
                                   now_mono=10.0)
                d_rec = tracker.daemons[(5004001, float(_START))]
                self.assertEqual(d_rec['artifact_dir'], '')
                self.assertIsNone(d_rec['grpc_port'])
                self.assertIsNone(d_rec['version'])

            # 2) NetsimLogTailer skips tail of >16 MiB newline-free lines AND
            # preserves normal lines straddling the next 16 MiB boundary.
            cap = erm._MAX_LOG_POLL_BYTES
            log_p = os.path.join(tmpdir, 'oversize_straddle.log')
            with open(log_p, 'wb') as f:
                f.write(b'X' * (cap + (cap - 40)))
                f.write(b'gRPC port: 59999\n')
                f.write(
                    b'netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n'
                )
            tailer = erm.NetsimLogTailer(log_p)
            self.assertTrue(tailer.poll(now_utc=float(_START + 1)))
            self.assertIsNone(tailer.grpc_port_logged)
            self.assertTrue(tailer.poll(now_utc=float(_START + 2)))
            self.assertIsNone(tailer.grpc_port_logged)
            self.assertTrue(tailer.poll(now_utc=float(_START + 3)))
            self.assertEqual(tailer.grpc_port_logged, 43929)

            # 3) >64 KiB single line ignored, and port 0 rejected in AddChipByGuid
            # and _loopback_endpoint_port.
            tailer.feed_line('netsim I 09-25 22:37:05.300 s.rs:1 - ' +
                             ('Z' * 70000) + ' gRPC port: 55555',
                             now_utc=float(_START + 4))
            self.assertEqual(tailer.grpc_port_logged, 43929)
            tailer.feed_line(
                'netsim I 09-25 22:37:05.400 s.rs:1 - '
                'DeviceActor: AddChipByGuid 1 for device 127.0.0.1:0',
                now_utc=float(_START + 5))
            self.assertIsNone(tailer.pending_guid_port)
            self.assertIsNone(erm._loopback_endpoint_port('127.0.0.1:0'))

    def test_netsim_settled_bare_preserved_on_qemu_next_shutdown_and_device_name_reset(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            _write_ini(os.path.join(tmpdir, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            log_path = os.path.join(art_dir, 'netsim_stderr.log')
            _touch(
                log_path, 'netsim I 09-25 22:37:06.000 s.rs:1 - '
                'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Wear_Bare)\n')
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            d_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }

            # Session 1: unnamed bare emulator-5554 learns 'Wear_Bare' and settles.
            g1 = _emu_group(5006001, None, None, ctime=_START)
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([d_proc], {'emulator-5554': {5006001}},
                                            {'emulator-5554': g1}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51001}]
                        }):
                tracker.update({}, {},
                               now_wall=float(_START + 10),
                               now_mono=10.0)
            for t_off in (30.0, 32.0):
                with patch.object(erm, '_snapshot_process_state',
                                  return_value=([d_proc], {}, {}, {})), \
                        patch.object(tracker, '_read_proc_locks',
                                     return_value={}), \
                        patch.object(tracker, '_collect_loopback_grpc_sockets',
                                     return_value={}):
                    tracker.update({}, {},
                                   now_wall=float(_START + t_off),
                                   now_mono=t_off)
            self.assertEqual(tracker.emulator_records['emulator-5554']['state'],
                             'DISCONNECTED')
            self.assertEqual(
                tracker.emulator_records['emulator-5554']['device_name'],
                'Wear_Bare')

            # Session 2: named QEMU-next AVD 'Phone_emulator-5554' (launcher + worker)
            # runs, then worker exits and launcher lingers with avd_name=None.
            launcher_pid = 5006010
            worker_pid = 5006011
            g2_full = {
                'primary_pid': worker_pid,
                'primary_ctime': float(_START + 40),
                'avd_id': 'Phone',
                'display_name': 'Phone',
                'avd_name': 'Phone',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {launcher_pid, worker_pid},
                'member_start_times': {
                    launcher_pid: float(_START + 39),
                    worker_pid: float(_START + 40),
                },
            }
            with patch.object(
                    erm, '_snapshot_process_state',
                    return_value=([d_proc],
                                  {'emulator-5554': {launcher_pid, worker_pid}},
                                  {'emulator-5554': g2_full}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51002}]
                        }):
                tracker.update({}, {'emulator-5554': 'device'},
                               now_wall=float(_START + 45),
                               now_mono=45.0)
            g2_shutdown = {
                'primary_pid': launcher_pid,
                'primary_ctime': float(_START + 39),
                'avd_id': None,
                'display_name': None,
                'avd_name': None,
                'grpc_endpoint': None,
                'no_netsim': False,
                'pids': {launcher_pid},
                'member_start_times': {
                    launcher_pid: float(_START + 39)
                },
            }
            for t_off in (50.0, 52.0):
                with patch.object(erm, '_snapshot_process_state',
                                  return_value=([d_proc],
                                                {'emulator-5554': {launcher_pid}},
                                                {'emulator-5554': g2_shutdown},
                                                {})), \
                        patch.object(tracker, '_read_proc_locks',
                                     return_value={}), \
                        patch.object(tracker, '_collect_loopback_grpc_sockets',
                                     return_value={}):
                    tracker.update(
                        {
                            'emulator-5554': {
                                'current_serial': 'emulator-5554',
                                'current_pid': launcher_pid,
                                'name': 'emulator-5554',
                                'first_seen': 45.0,
                            }
                        }, {'emulator-5554': 'offline'},
                        now_wall=float(_START + t_off),
                        now_mono=t_off)
            self.assertEqual(tracker.emulator_records['emulator-5554']['state'],
                             'DISCONNECTED')
            self.assertEqual(
                tracker.emulator_records['emulator-5554']['device_name'],
                'Wear_Bare')
            self.assertEqual(
                tracker.emulator_records['Phone_emulator-5554']['state'],
                'DISCONNECTED')

            # Session 3: new worker on bare key followed by classic -avd Pixel_8
            # (display_name=None) must NOT inherit Session 1's 'Wear_Bare'.
            g3_bare = _emu_group(5006020, None, None, ctime=_START + 70)
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([d_proc], {'emulator-5554': {5006020}},
                                            {'emulator-5554': g3_bare}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update({}, {},
                               now_wall=float(_START + 70),
                               now_mono=70.0)
            g3_named = _emu_group(5006020, 'Pixel_8', None, ctime=_START + 70)
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([d_proc], {'emulator-5554': {5006020}},
                                            {'emulator-5554': g3_named}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update({}, {},
                               now_wall=float(_START + 72),
                               now_mono=72.0)
            self.assertEqual(
                tracker.emulator_records['Pixel_8_emulator-5554']
                ['device_name'], 'Pixel_8')
            self.assertEqual(
                tracker.emulator_records['emulator-5554']['device_name'],
                'Wear_Bare')


class TestRound9Fixes(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)
        ncli = patch(
            'emulator_resource_monitor.NetsimPcapManager._run_netsim_cli',
            return_value=None)
        ncli.start()
        self.addCleanup(ncli.stop)
        scli = patch('emulator_resource_monitor._run_cli_with_timeout',
                     return_value=None)
        scli.start()
        self.addCleanup(scli.stop)

    def test_host_exited_within_5s_and_offline_to_device_reconnect_isolate_session(
            self):
        import emulator_resource_monitor as erm

        # 1) Host exit + <=5s ADB disconnect -> reconnect while host scan is
        # pending (and tick deadline is exhausted by slow host scan).
        tick = [0]
        now_t = [100.0]
        dev_seq = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {},
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        pending_host = MagicMock()
        guest_calls = []

        def submit_se(fn, *args, **kwargs):
            if fn is erm.get_host_stats:
                idx = tick[0]
                if idx <= 1:
                    erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {1000}}
                    erm._LAST_EMU_GROUP_INFO = {
                        'emulator-5554': {
                            'primary_pid': 1000,
                            'primary_ctime': 90.0,
                            'avd_id': 'Pixel_6',
                            'display_name': 'Pixel 6',
                            'avd_name': 'Pixel_6',
                            'no_netsim': True,
                            'pids': {1000},
                            'member_start_times': {
                                1000: 90.0
                            },
                        }
                    }
                    erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (1000, 90.0)}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple(
                        {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_6'},
                        qemu_stats={'emulator-5554': ('25.0%', '1500.0MB')})
                    return f
                if idx == 2:
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple({}, {}, qemu_stats={})
                    return f
                if idx == 3:
                    pending_host.done.side_effect = [False, False, True, True]

                    def finish_p8():
                        erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                        erm._LAST_EMU_GROUP_INFO = {
                            'emulator-5554': {
                                'primary_pid': 2000,
                                'primary_ctime': 106.0,
                                'avd_id': 'Pixel_8',
                                'display_name': 'Pixel 8',
                                'avd_name': 'Pixel_8',
                                'no_netsim': True,
                                'pids': {2000},
                                'member_start_times': {
                                    2000: 106.0
                                },
                            }
                        }
                        erm._LAST_PRIMARY_PROC_ID = {
                            'emulator-5554': (2000, 106.0)
                        }
                        return _host_tuple(
                            {'emulator-5554': 2000},
                            {'emulator-5554': 'Pixel_8'},
                            qemu_stats={'emulator-5554': ('60.0%', '2000.0MB')})

                    pending_host.result.side_effect = finish_p8
                    return pending_host
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_wait(fs, timeout=None):
            if tick[0] == 3 and pending_host in fs:
                now_t[0] += 2.0
            return (set(), set())

        def retry_aware_guest(serial, known_model, is_booting, status):
            guest_calls.append((tick[0], known_model, is_booting))
            need_query = (not known_model or
                          'android' not in known_model.lower() or
                          'unknown model' in known_model.lower() or
                          'error:' in known_model.lower() or
                          'adb:' in known_model.lower() or
                          'offline' in known_model.lower() or
                          ('Res: N/A' in known_model and
                           (is_booting is None or is_booting)))
            if tick[0] <= 1:
                live_m = _guest_model('Pixel 6')
                return (live_m if need_query else known_model, False, '20.0%',
                        '20.0%', '40.0% (1.6GB/4.0GB, 0 errs, 10% data)',
                        '0.0 MB')
            live_m = _guest_model('Pixel 8')
            return (live_m if need_query else known_model, True, '80.0%',
                    '80.0%', '50.0% (2.0GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 2.0
            if tick[0] == 5:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = submit_se
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('concurrent.futures.wait', side_effect=fake_wait), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 4)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=retry_aware_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary = mock_save.call_args[0][0]
        emus = summary['emulators']
        ts = list(summary['time_series'])
        self.assertEqual([c for c in guest_calls if c[0] == 3], [(3, '', None)])
        self.assertEqual(emus['Pixel_6_emulator-5554']['cpu_iterations'], 1)
        self.assertAlmostEqual(emus['Pixel_6_emulator-5554']['cpu_max'], 20.0)
        self.assertTrue(math.isnan(ts[2]['emu_cpu_tot_Pixel_6_emulator-5554']))
        self.assertEqual(emus['Pixel_8_emulator-5554']['cpu_iterations'], 2)
        self.assertAlmostEqual(emus['Pixel_8_emulator-5554']['cpu_max'], 80.0)
        self.assertEqual(ts[2]['emu_cpu_tot_Pixel_8_emulator-5554'], 80.0)
        self.assertIn('Pixel 8', emus['Pixel_8_emulator-5554']['name'])
        self.assertNotIn('Pixel 6', emus['Pixel_8_emulator-5554']['name'])
        self.assertEqual(emus['Pixel_8_emulator-5554']['boot_log'][0][0], '+0s')

        # 2) Continuous ADB listing ('device' -> 'offline' on host exit ->
        # 'device' for a forwarded/remote target with new_pid=None) isolates
        # the new session while preserving the lingering 'offline' tick on Pixel_6.
        tick2 = [0]
        now_t2 = [300.0]
        dev_seq2 = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
        ]

        def fake_host2():
            if tick2[0] <= 1:
                erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {1000}}
                erm._LAST_EMU_GROUP_INFO = {
                    'emulator-5554': {
                        'primary_pid': 1000,
                        'primary_ctime': 290.0,
                        'avd_id': 'Pixel_6',
                        'display_name': 'Pixel 6',
                        'avd_name': 'Pixel_6',
                        'no_netsim': True,
                        'pids': {1000},
                        'member_start_times': {
                            1000: 290.0
                        },
                    }
                }
                erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (1000, 290.0)}
                return _host_tuple(
                    {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_6'},
                    qemu_stats={'emulator-5554': ('25.0%', '1500.0MB')})
            erm._LAST_EMU_GROUP_PIDS = {}
            erm._LAST_EMU_GROUP_INFO = {}
            erm._LAST_PRIMARY_PROC_ID = {}
            return _host_tuple({}, {}, qemu_stats={})

        def retry_aware_guest2(serial, known_model, is_booting, status):
            if status != 'device':
                return (known_model if known_model else f'Unknown ({status})',
                        bool(is_booting), f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})')
            need_query = (not known_model or
                          'android' not in known_model.lower() or
                          'offline' in known_model.lower())
            live_m = (_guest_model('Pixel 6')
                      if tick2[0] <= 1 else _guest_model('Pixel 9'))
            cpu_s = '20.0%' if tick2[0] <= 1 else '70.0%'
            return (live_m if need_query else known_model, False, cpu_s, cpu_s,
                    '45.0% (1.8GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_submit2(fn, *a, **kw):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*a, **kw)
            return f

        def fake_sleep2(_):
            tick2[0] += 1
            now_t2[0] += 2.0
            if tick2[0] == 4:
                raise KeyboardInterrupt

        exec2 = MagicMock()
        exec2.submit.side_effect = fake_submit2
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=exec2), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq2[min(tick2[0], 3)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host2), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=retry_aware_guest2), \
                patch('emulator_resource_monitor.save_summary') as mock_save2, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t2[0]), \
                patch('time.sleep', side_effect=fake_sleep2), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emus2 = mock_save2.call_args[0][0]['emulators']
        self.assertEqual(emus2['Pixel_6_emulator-5554']['cpu_iterations'], 1)
        self.assertAlmostEqual(emus2['Pixel_6_emulator-5554']['cpu_max'], 20.0)
        self.assertEqual(emus2['emulator-5554']['cpu_iterations'], 1)
        self.assertAlmostEqual(emus2['emulator-5554']['cpu_max'], 70.0)
        self.assertIn('Pixel 9', emus2['emulator-5554']['name'])

    def test_preadb_replacement_preserves_disconnected_card_and_suppresses_live_port_rows(
            self):
        import io
        import emulator_resource_monitor as erm

        # 1) Pre-ADB named replacement keeps outgoing [DISCONNECTED] card visible.
        tick = [0]
        now_t = [200.0]
        dev_seq = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {},
        ]
        dash_snapshots = []

        def fake_host():
            idx = min(tick[0], 2)
            pid, avd, ct = ((1000, 'Pixel_6', 190.0) if idx <= 1 else
                            (2000, 'Pixel_8', 202.0))
            erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {pid}}
            erm._LAST_EMU_GROUP_INFO = {
                'emulator-5554': {
                    'primary_pid': pid,
                    'primary_ctime': ct,
                    'avd_id': avd,
                    'display_name': avd.replace('_', ' '),
                    'avd_name': avd,
                    'no_netsim': True,
                    'pids': {pid},
                    'member_start_times': {
                        pid: ct
                    },
                }
            }
            erm._LAST_PRIMARY_PROC_ID = {'emulator-5554': (pid, ct)}
            return _host_tuple(
                {'emulator-5554': pid}, {'emulator-5554': avd},
                qemu_stats={'emulator-5554': ('30.0%', '1500.0MB')})

        def fake_submit(fn, *a, **kw):
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*a, **kw)
            return f

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 1.0
            if tick[0] == 3:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = fake_submit
        with patch('concurrent.futures.ThreadPoolExecutor',
                   return_value=executor), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 2)]), \
                patch('emulator_resource_monitor.get_host_stats',
                      side_effect=fake_host), \
                patch('emulator_resource_monitor.get_guest_stats',
                      return_value=(_guest_model('Pixel 6'), False, '20.0%',
                                    '20.0%',
                                    '40.0% (1.6GB/4.0GB, 0 errs, 10% data)',
                                    '0.0 MB')), \
                patch('emulator_resource_monitor.save_summary'), \
                patch('emulator_resource_monitor.print_dashboard',
                      side_effect=lambda *a, **kw: dash_snapshots.append(dict(a[4]))), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        self.assertIn('emulator-5554', dash_snapshots[2])
        self.assertIn('[DISCONNECTED]', dash_snapshots[2]['emulator-5554'][1])
        self.assertIn('Pixel 6', dash_snapshots[2]['emulator-5554'][0])

        # 2) print_dashboard suppresses live Host QEMU / Netsim Status rows on
        # [DISCONNECTED] cards when a replacement occupies the port, while still
        # rendering settled Netsim Status when no host QEMU occupies the port.
        disc_guests = {
            'emulator-5554': (
                'Pixel_6 | Pixel 6',
                f' {erm.Colors.RED}[DISCONNECTED]{erm.Colors.RESET}',
                'N/A',
                'N/A',
                'N/A',
                'N/A',
            )
        }
        ns_snap = {
            'daemon_rows': [],
            'daemon_status_row': 'N/A',
            'emulator_rows': {
                'emulator-5554': 'netsim: CONNECTING (no-chips) | disc=0'
            },
        }
        buf_with_qemu = io.StringIO()
        with patch('sys.stdout', buf_with_qemu):
            erm.print_dashboard('Linux',
                                '10.0%',
                                {'emulator-5554': ('45.0%', '1800.0MB')},
                                'N/A',
                                disc_guests,
                                '30.0%',
                                '0.0%',
                                'N/A',
                                1,
                                100,
                                False, [],
                                'N/A',
                                'N/A',
                                'N/A', {'emulator-5554': '12.0%'},
                                is_once=True,
                                netsim_snapshot=ns_snap)
        out_with_qemu = buf_with_qemu.getvalue()
        self.assertNotIn('Host QEMU CPU', out_with_qemu)
        self.assertNotIn('Host QEMU RAM', out_with_qemu)
        self.assertNotIn('Host QEMU GPU Load', out_with_qemu)
        self.assertNotIn('Netsim Status', out_with_qemu)

        buf_no_qemu = io.StringIO()
        with patch('sys.stdout', buf_no_qemu):
            erm.print_dashboard('Linux',
                                '10.0%', {},
                                'N/A',
                                disc_guests,
                                '30.0%',
                                '0.0%',
                                'N/A',
                                1,
                                100,
                                False, [],
                                'N/A',
                                'N/A',
                                'N/A', {},
                                is_once=True,
                                netsim_snapshot=ns_snap)
        self.assertIn('Netsim Status', buf_no_qemu.getvalue())

    def test_netsim_exited_unattached_bystander_clears_ready_s_while_real_restart_keeps_it(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Exited unattached bystander (--instance 1, dir_a) clears its
            # ready_s when Phone60 switches to its real daemon (--instance 2).
            dir_a = os.path.join(tmpdir, 'a')
            dir_b = os.path.join(tmpdir, 'b')
            art_a = os.path.join(dir_a, 'android-user', 'netsimd')
            art_b = os.path.join(dir_b, 'android-user', 'netsimd')
            os.makedirs(art_a, exist_ok=True)
            os.makedirs(art_b, exist_ok=True)
            _write_ini(os.path.join(dir_a, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START) + 0.3)
            _touch(
                os.path.join(art_a, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.300 s.rs:1 - Wrote to INI file {dir_a}/netsim.ini\n'
            )
            _write_ini(os.path.join(dir_b, 'netsim_2.ini'),
                       f'pid={_DAEMON_B}\ngrpc.port=43930\n',
                       mtime=float(_START) + 2.5)
            _touch(
                os.path.join(art_b, 'netsim_2_stderr.log'),
                f'netsim I 09-25 22:37:07.500 s.rs:1 - Wrote to INI file {dir_b}/netsim_2.ini\n'
                'netsim I 09-25 22:37:08.000 s.rs:2 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone60)\n'
            )
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir_a, dir_b]
            d_a = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_a,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_b = {
                'pid': _DAEMON_B,
                'create_time': float(_START + 1),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd', '--instance', '2'],
                'environ': {
                    'TMPDIR': dir_b,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g60 = _emu_group(5007001, 'Phone60', 'Phone60', ctime=_START)
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_a], {'emulator-5560': {5007001}},
                                               {'emulator-5560': g60}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(
                    {
                        'Phone60_emulator-5560': {
                            'current_serial': 'emulator-5560',
                            'current_pid': 5007001,
                            'name': 'Phone60',
                            'first_seen': 1.0,
                        }
                    }, {},
                    now_wall=float(_START + 1),
                    now_mono=1.0)
            os.remove(os.path.join(dir_a, 'netsim.ini'))
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_b], {'emulator-5560': {5007001}},
                                               {'emulator-5560': g60}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43930: [{'serial': 'emulator-5560', 'peer_port': 51001}]
                        }):
                tracker.update(
                    {
                        'Phone60_emulator-5560': {
                            'current_serial': 'emulator-5560',
                            'current_pid': 5007001,
                            'name': 'Phone60',
                            'first_seen': 1.0,
                        }
                    }, {'emulator-5560': 'device'},
                    now_wall=float(_START + 4),
                    now_mono=4.0)
            self.assertAlmostEqual(
                tracker.emulator_records['Phone60_emulator-5560']
                ['netsim_daemon_ready_s'],
                2.5,
                places=5)

            # 2) True replacement daemon restart (same instance & artifact_dir)
            # preserves the original first ready_s latency.
            dir_r = os.path.join(tmpdir, 'restart')
            art_r = os.path.join(dir_r, 'android-user', 'netsimd')
            os.makedirs(art_r, exist_ok=True)
            _write_ini(os.path.join(dir_r, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START) + 0.4)
            _touch(
                os.path.join(art_r, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.400 s.rs:1 - Wrote to INI file {dir_r}/netsim.ini\n'
            )
            tracker_r = erm.NetsimTracker(pcap_dir=None,
                                          enable_log=True,
                                          out_dir=tmpdir,
                                          os_family='linux')
            tracker_r.custom_ini_dirs = [dir_r]
            dr_1 = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_r,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            dr_2 = dict(dr_1, pid=_DAEMON_B, create_time=float(_START + 2))
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([dr_1], {'emulator-5560': {5007001}},
                                               {'emulator-5560': g60}, {})), \
                    patch.object(tracker_r, '_read_proc_locks', return_value={}), \
                    patch.object(tracker_r, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker_r.update(
                    {
                        'Phone60_emulator-5560': {
                            'current_serial': 'emulator-5560',
                            'current_pid': 5007001,
                            'name': 'Phone60',
                            'first_seen': 1.0,
                        }
                    }, {},
                    now_wall=float(_START + 1),
                    now_mono=1.0)
            _write_ini(os.path.join(dir_r, 'netsim.ini'),
                       f'pid={_DAEMON_B}\ngrpc.port=43929\n',
                       mtime=float(_START) + 3.0)
            _touch(
                os.path.join(art_r, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:08.000 s.rs:1 - Wrote to INI file {dir_r}/netsim.ini\n'
            )
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([dr_2], {'emulator-5560': {5007001}},
                                               {'emulator-5560': g60}, {})), \
                    patch.object(tracker_r, '_read_proc_locks', return_value={}), \
                    patch.object(tracker_r, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker_r.update(
                    {
                        'Phone60_emulator-5560': {
                            'current_serial': 'emulator-5560',
                            'current_pid': 5007001,
                            'name': 'Phone60',
                            'first_seen': 1.0,
                        }
                    }, {},
                    now_wall=float(_START + 4),
                    now_mono=4.0)
            self.assertAlmostEqual(
                tracker_r.emulator_records['Phone60_emulator-5560']
                ['netsim_daemon_ready_s'],
                0.4,
                places=5)

    def test_netsim_post_registration_symlink_foreign_art_dir_and_zero_guid_port_reset(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Post-registration symlinked artifact_dir is cleared on re-poll.
            secret_dir = os.path.join(tmpdir, 'secret_dir')
            os.makedirs(secret_dir, exist_ok=True)
            _touch(os.path.join(secret_dir, 'netsim_stderr.log'),
                   'netsim I 09-25 22:37:06.000 s.rs:1 - gRPC port: 44444\n')
            stats_f = os.path.join(secret_dir, 'netsim_session_stats.json')
            _touch(stats_f, '{"version": "9.9.9-secret"}')
            os.utime(stats_f, (float(_START + 2), float(_START + 2)))
            late_tmp = os.path.join(tmpdir, 'late_sym_tmp')
            os.makedirs(os.path.join(late_tmp, 'android-user'), exist_ok=True)
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            d_late = {
                'pid': 5008001,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': late_tmp,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([d_late], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update({}, {}, now_wall=float(_START + 1), now_mono=1.0)
            if _try_symlink(secret_dir,
                            os.path.join(late_tmp, 'android-user', 'netsimd'),
                            target_is_directory=True):
                with patch.object(erm, '_snapshot_process_state',
                                  return_value=([d_late], {}, {}, {})), \
                        patch.object(tracker, '_read_proc_locks', return_value={}), \
                        patch.object(tracker, '_collect_loopback_grpc_sockets',
                                     return_value={}):
                    tracker.update({}, {},
                                   now_wall=float(_START + 3),
                                   now_mono=3.0)
                d_rec = tracker.daemons[(5008001, float(_START))]
                self.assertEqual(d_rec['artifact_dir'], '')
                self.assertIsNone(d_rec['tailer'])
                self.assertIsNone(d_rec['grpc_port'])
                self.assertIsNone(d_rec['version'])

            # 2) Foreign (!same_user) daemon registered before monitor user's
            # artifact_dir exists drops artifact_dir once created, avoiding
            # port/version theft, DUPLICATE_ARTIFACT_DIR, and exit harvest block.
            own_tmp = os.path.join(tmpdir, 'own_tmp')
            own_art = os.path.join(own_tmp, 'android-user', 'netsimd')
            pcap_out = os.path.join(tmpdir, 'pcap_out')
            tracker2 = erm.NetsimTracker(pcap_dir=pcap_out,
                                         enable_log=True,
                                         out_dir=tmpdir,
                                         os_family='linux')
            tracker2.custom_ini_dirs = [own_tmp]
            my_uid = os.stat(tmpdir).st_uid or (erm._current_uid() or 1000)
            d_foreign = {
                'pid': 5008010,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': False,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': own_tmp,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_own = {
                'pid': _DAEMON_A,
                'create_time': float(_START + 1),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': own_tmp,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch.object(erm, '_current_uid', return_value=my_uid), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_foreign], {}, {}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(tracker2, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker2.update({}, {},
                                now_wall=float(_START + 1),
                                now_mono=1.0)

            os.makedirs(own_art, exist_ok=True)
            _write_ini(os.path.join(own_tmp, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 2))
            _touch(
                os.path.join(own_art, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:07.000 s.rs:1 - gRPC port: 43929\n'
                'netsim I 09-25 22:37:07.100 s.rs:2 - '
                'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n')
            _touch(os.path.join(own_art, 'netsim_artifacts_own.zip'), 'zipdata')
            own_stats = os.path.join(own_art, 'netsim_session_stats.json')
            _touch(own_stats, '{"version": "1.0.31"}')
            os.utime(own_stats, (float(_START + 3), float(_START + 3)))
            g_phone = dict(_emu_group(5008020,
                                      'Phone',
                                      'Phone',
                                      ctime=_START + 1),
                           grpc_endpoint='127.0.0.1:43929')

            with patch.object(erm, '_current_uid', return_value=my_uid), \
                    patch.object(
                        erm,
                        '_is_trusted_owned_real_dir',
                        side_effect=lambda p, **_kw: p in (
                            own_art, os.path.join(own_art, 'pcaps'))), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_foreign, d_own],
                                               {'emulator-5554': {5008020}},
                                               {'emulator-5554': g_phone}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker2,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51001}]
                        }):
                tracker2.update(
                    {
                        'Phone_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5008020,
                            'name': 'Phone',
                            'first_seen': 2.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 4),
                    now_mono=4.0)

            os.remove(os.path.join(own_tmp, 'netsim.ini'))
            with patch.object(erm, '_current_uid', return_value=my_uid), \
                    patch.object(
                        erm,
                        '_is_trusted_owned_real_dir',
                        side_effect=lambda p, **_kw: p in (
                            own_art, os.path.join(own_art, 'pcaps'))), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_foreign],
                                               {'emulator-5554': {5008020}},
                                               {'emulator-5554': g_phone}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}), \
                    patch.object(tracker2, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker2.update(
                    {
                        'Phone_emulator-5554': {
                            'current_serial': 'emulator-5554',
                            'current_pid': 5008020,
                            'name': 'Phone',
                            'first_seen': 2.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 6),
                    now_mono=6.0)

            self.assertEqual(
                tracker2.daemons[(5008010, float(_START))]['artifact_dir'], '')
            self.assertIsNone(tracker2.daemons[(5008010,
                                                float(_START))]['grpc_port'])
            self.assertFalse(
                any('DUPLICATE_ARTIFACT_DIR' in ev
                    for _, ev in tracker2.timeline))
            self.assertEqual(
                tracker2.daemons[(_DAEMON_A, float(_START + 1))]['version'],
                '1.0.31')
            self.assertEqual(len(tracker2.pcap_mgr.rescued_zips), 1)
            self.assertEqual(
                tracker2.emulator_records['Phone_emulator-5554']['state'],
                'DISCONNECTED')

            # 3) AddChipByGuid with port 0 or >65535 clears stale pending_guid_port.
            tailer = erm.NetsimLogTailer(os.path.join(tmpdir, 'guid.log'))
            tailer.feed_line(
                'netsim I 09-25 22:37:05.000 s.rs:1 - '
                'DeviceActor: AddChipByGuid 1 for device 127.0.0.1:50001',
                now_utc=float(_START))
            self.assertIsNotNone(tailer.pending_guid_port)
            tailer.feed_line(
                'netsim I 09-25 22:37:05.100 s.rs:2 - '
                'DeviceActor: AddChipByGuid 2 for device 127.0.0.1:0',
                now_utc=float(_START) + 0.1)
            self.assertIsNone(tailer.pending_guid_port)
            tailer.feed_line(
                'netsim I 09-25 22:37:05.100 s.rs:3 - '
                'DeviceActor: AddChip 2 (kind WIFI) to device 2 (Phone2)',
                now_utc=float(_START) + 0.1)
            self.assertIsNone(tailer.chip_adds[-1]['peer_port'])


class TestRound10Fixes(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)
        ncli = patch(
            'emulator_resource_monitor.NetsimPcapManager._run_netsim_cli',
            return_value=None)
        ncli.start()
        self.addCleanup(ncli.stop)
        scli = patch('emulator_resource_monitor._run_cli_with_timeout',
                     return_value=None)
        scli.start()
        self.addCleanup(scli.stop)

    def test_au10_py_01_bare_replacement_and_hostless_offline_pending_host_scan_isolation(
            self):
        import emulator_resource_monitor as erm

        # 1) Unnamed bare-serial session (pid=1000, 20% CPU) exits, then named
        # Pixel_8 (pid=2000, 85% CPU) connects on ADB while get_host_stats is
        # pending and resolves on the following tick.
        tick = [0]
        now_t = [100.0]
        now_w = [float(_START)]
        dev_seq = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {},
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        pending_host = MagicMock()

        def submit_se(fn, *args, **kwargs):
            if fn is erm.get_host_stats:
                idx = tick[0]
                if idx <= 1:
                    erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {1000}}
                    erm._LAST_EMU_GROUP_INFO = {
                        'emulator-5554': {
                            'primary_pid': 1000,
                            'primary_ctime': float(_START - 10),
                            'avd_id': None,
                            'display_name': None,
                            'avd_name': None,
                            'no_netsim': False,
                            'pids': {1000},
                            'member_start_times': {
                                1000: float(_START - 10)
                            },
                        }
                    }
                    erm._LAST_PRIMARY_PROC_ID = {
                        'emulator-5554': (1000, float(_START - 10))
                    }
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple(
                        {'emulator-5554': 1000}, {},
                        qemu_stats={
                            'emulator-5554':
                                ('25.0%', '1500.0MB (4 thr, 10 cs)')
                        })
                    return f
                if idx == 2:
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple({}, {}, qemu_stats={})
                    return f
                if idx == 3:
                    pending_host.done.side_effect = [False, False, True, True]

                    def finish_p8():
                        erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                        erm._LAST_EMU_GROUP_INFO = {
                            'emulator-5554': {
                                'primary_pid': 2000,
                                'primary_ctime': float(_START + 6),
                                'avd_id': 'Pixel_8',
                                'display_name': 'Pixel 8',
                                'avd_name': 'Pixel_8',
                                'no_netsim': False,
                                'pids': {2000},
                                'member_start_times': {
                                    2000: float(_START + 6)
                                },
                            }
                        }
                        erm._LAST_PRIMARY_PROC_ID = {
                            'emulator-5554': (2000, float(_START + 6))
                        }
                        return _host_tuple({'emulator-5554': 2000},
                                           {'emulator-5554': 'Pixel_8'},
                                           qemu_stats={
                                               'emulator-5554':
                                                   ('60.0%',
                                                    '2000.0MB (4 thr, 10 cs)')
                                           })

                    pending_host.result.side_effect = finish_p8
                    return pending_host
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_wait(fs, timeout=None):
            if tick[0] == 3 and pending_host in fs:
                now_t[0] += 2.0
                now_w[0] += 2.0
            return (set(), set())

        def fake_guest(_serial, known_model, is_booting, status):
            if status != 'device':
                return (known_model if known_model else f'Unknown ({status})',
                        bool(is_booting), f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})')
            if tick[0] <= 1:
                return (_guest_model('OldEmu'), False, '20.0%', '20.0%',
                        '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')
            return (_guest_model('Pixel 8'), True, '85.0%', '85.0%',
                    '50.0% (2.0GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_sleep(_):
            tick[0] += 1
            now_t[0] += 2.0
            now_w[0] += 2.0
            if tick[0] == 5:
                raise KeyboardInterrupt

        executor = MagicMock()
        executor.submit.side_effect = submit_se
        with patch.object(
                erm,
                'get_proc_precise_start_time',
                side_effect=lambda _p, fallback_create_time=None: fallback_create_time
        ), \
                patch('concurrent.futures.ThreadPoolExecutor',
                      return_value=executor), \
                patch('concurrent.futures.wait', side_effect=fake_wait), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq[min(tick[0], 4)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest), \
                patch('emulator_resource_monitor.save_summary') as mock_save, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t[0]), \
                patch('time.time', side_effect=lambda: now_w[0]), \
                patch('time.sleep', side_effect=fake_sleep), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary = mock_save.call_args[0][0]
        emus = summary['emulators']
        ts = list(summary['time_series'])
        self.assertIn('OldEmu', emus['emulator-5554']['name'])
        self.assertNotIn('Pixel 8', emus['emulator-5554']['name'])
        self.assertEqual(emus['emulator-5554']['cpu_iterations'], 1)
        self.assertAlmostEqual(emus['emulator-5554']['cpu_max'], 20.0)
        self.assertEqual(emus['Pixel_8_emulator-5554']['cpu_iterations'], 2)
        self.assertAlmostEqual(emus['Pixel_8_emulator-5554']['cpu_max'], 85.0)
        self.assertEqual(ts[2]['emu_cpu_tot_Pixel_8_emulator-5554'], 85.0)
        self.assertNotIn('emu_cpu_tot_emulator-5554', ts[2])
        self.assertEqual(emus['Pixel_8_emulator-5554']['boot_log'][0][0], '+0s')

        # 2) Stale hostless 'offline' entry (seen_offline_without_host) ->
        # 'device' during a pending host scan must purge stale offline columns,
        # pass clean known_model="", and keep netsim in CONNECTING (not FAILED).
        tick2 = [0]
        now_t2 = [300.0]
        now_w2 = [float(_START + 100)]
        dev_seq2 = [
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        pending_host2 = MagicMock()
        guest_calls2 = []
        d_standalone = {
            'pid': _DAEMON_A,
            'create_time': float(_START + 90),
            'ppid': 1,
            'same_user': True,
            'cmdline': ['netsimd'],
            'environ': {
                'TMPDIR': '/nonexistent',
                'USER': 'user'
            },
            'exe': '/opt/sdk/emulator/netsimd',
        }

        def submit_se2(fn, *args, **kwargs):
            if fn is erm.get_host_stats:
                idx = tick2[0]
                if idx <= 1:
                    erm._LAST_NETSIMD_PROCS = [d_standalone]
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple({}, {}, qemu_stats={})
                    return f
                if idx == 2:
                    pending_host2.done.side_effect = [False, False, True, True]

                    def finish_p8_2():
                        erm._LAST_NETSIMD_PROCS = [d_standalone]
                        erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                        erm._LAST_EMU_GROUP_INFO = {
                            'emulator-5554': {
                                'primary_pid': 2000,
                                'primary_ctime': float(_START + 104),
                                'avd_id': 'Pixel_8',
                                'display_name': 'Pixel 8',
                                'avd_name': 'Pixel_8',
                                'no_netsim': False,
                                'pids': {2000},
                                'member_start_times': {
                                    2000: float(_START + 104)
                                },
                            }
                        }
                        erm._LAST_PRIMARY_PROC_ID = {
                            'emulator-5554': (2000, float(_START + 104))
                        }
                        return _host_tuple({'emulator-5554': 2000},
                                           {'emulator-5554': 'Pixel_8'},
                                           qemu_stats={
                                               'emulator-5554':
                                                   ('60.0%',
                                                    '2000.0MB (4 thr, 10 cs)')
                                           })

                    pending_host2.result.side_effect = finish_p8_2
                    return pending_host2
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_guest2(_serial, known_model, is_booting, status):
            guest_calls2.append((tick2[0], known_model, is_booting))
            if status != 'device':
                return (known_model if known_model else f'Unknown ({status})',
                        bool(is_booting), f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})')
            return (_guest_model('Pixel 8'), True, '85.0%', '85.0%',
                    '50.0% (2.0GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_sleep2(_):
            tick2[0] += 1
            now_t2[0] += 2.0
            now_w2[0] += 2.0
            if tick2[0] == 4:
                raise KeyboardInterrupt

        exec2 = MagicMock()
        exec2.submit.side_effect = submit_se2
        with patch.object(
                erm,
                'get_proc_precise_start_time',
                side_effect=lambda _p, fallback_create_time=None: fallback_create_time
        ), \
                patch('concurrent.futures.ThreadPoolExecutor',
                      return_value=exec2), \
                patch('concurrent.futures.wait',
                      side_effect=lambda _fs, **_kw: (set(), set())), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq2[min(tick2[0], 3)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest2), \
                patch('emulator_resource_monitor.save_summary') as mock_save2, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t2[0]), \
                patch('time.time', side_effect=lambda: now_w2[0]), \
                patch('time.sleep', side_effect=fake_sleep2), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary2 = mock_save2.call_args[0][0]
        ts2 = list(summary2['time_series'])
        self.assertEqual([c for c in guest_calls2 if c[0] == 2],
                         [(2, '', None)])
        self.assertNotIn('emu_cpu_tot_Pixel_8_emulator-5554', ts2[0])
        self.assertNotIn('netsim_state_Pixel_8_emulator-5554', ts2[0])
        self.assertEqual(ts2[1]['netsim_state_Pixel_8_emulator-5554'],
                         'CONNECTING')
        self.assertEqual(ts2[2]['netsim_state_Pixel_8_emulator-5554'],
                         'CONNECTING')

        # Direct <=5s disconnect -> reconnect check on seen_offline_without_host
        d_names = {'emulator-5554': 'Unknown (offline)'}
        disc_t, s_pids = {}, {}
        seen_off = {'emulator-5554'}
        h_exited = set()
        self.assertEqual(
            erm.update_device_cache(d_names,
                                    disc_t,
                                    s_pids,
                                    set(), {},
                                    100.0,
                                    host_fresh=True,
                                    devices={},
                                    seen_offline_without_host=seen_off,
                                    host_exited_serials=h_exited), set())
        self.assertEqual(
            erm.update_device_cache(d_names,
                                    disc_t,
                                    s_pids, {'emulator-5554'}, {},
                                    102.0,
                                    host_fresh=False,
                                    devices={'emulator-5554': 'device'},
                                    seen_offline_without_host=seen_off,
                                    host_exited_serials=h_exited),
            {'emulator-5554'})
        self.assertEqual(d_names, {})
        self.assertEqual(seen_off, set())

    def test_au10_py_02_psutil_zero_port_rejected_and_disallowed_art_dir_marks_log_unavail(
            self):
        import shutil
        import emulator_resource_monitor as erm
        from collections import namedtuple

        FakeConn = namedtuple('FakeConn', ['status', 'laddr', 'raddr'])
        fake_proc = MagicMock()
        fake_proc.net_connections.return_value = [
            FakeConn('ESTABLISHED', ('127.0.0.1', 0), ('127.0.0.1', 43929)),
            FakeConn('ESTABLISHED', ('127.0.0.1', 51001), ('127.0.0.1', 0)),
            FakeConn('ESTABLISHED', ('127.0.0.1', 51002), ('127.0.0.1', 43929)),
        ]
        with patch('psutil.Process', return_value=fake_proc):
            rows = erm._psutil_loopback_rows(4001)
        self.assertEqual(rows, [(4001, 51002, 43929)])

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            _touch(
                os.path.join(art_dir, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 43929\n'
            )
            secret_dir = os.path.join(tmpdir, 'secret')
            os.makedirs(secret_dir, exist_ok=True)

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            d_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_proc], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}):
                tracker.update({}, {}, now_wall=float(_START + 1), now_mono=1.0)
                self.assertTrue(
                    tracker.daemons[(_DAEMON_A,
                                     float(_START))]['log_available'])

                shutil.rmtree(art_dir)
                if _try_symlink(secret_dir, art_dir, target_is_directory=True):
                    tracker.update({}, {},
                                   now_wall=float(_START + 2),
                                   now_mono=2.0)
                    d_rec = tracker.daemons[(_DAEMON_A, float(_START))]
                    self.assertFalse(d_rec['log_available'])
                    snap = tracker.get_dashboard_snapshot(
                        now_wall=float(_START + 2))
                    self.assertIn('log=unavail', snap['daemon_rows'][0][1])
                    unavail_evs = [
                        ev for _, ev in tracker.timeline
                        if ev == f'LOG_UNAVAILABLE(pid={_DAEMON_A})'
                    ]
                    self.assertEqual(len(unavail_evs), 1)

    def test_au10_netsim_01_part2_unattached_provisional_daemon_key_cleared_on_conflict_or_multi_daemon(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Provisional single-daemon fallback cleared once port conflict
            # resolves, freeing the real attached emulator's L2 stats & wifi.
            dir_a = os.path.join(tmpdir, 'a')
            art_a = os.path.join(dir_a, 'android-user', 'netsimd')
            os.makedirs(art_a, exist_ok=True)
            _touch(
                os.path.join(art_a, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.300 s.rs:1 - Wrote to INI file {dir_a}/netsim.ini\n'
                'netsim I 09-25 22:37:06.000 s.rs:2 - '
                'DeviceActor: AddChip wlan0 (1, 1) to device PhoneA\n'
                'netsim I 09-25 22:37:06.500 s.rs:3 - '
                'ApActor: Associated 02:15:b2:00:00:00\n')
            stats_path = os.path.join(art_a, 'netsim_session_stats.json')
            _touch(
                stats_path, '{"device_stats": [{"device_id": 1}], '
                '"radio_stats": [{"device_id": 1, "kind": "WIFI", "duration_secs": 2}]}'
            )
            os.utime(stats_path, (float(_START + 3), float(_START + 3)))

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir_a]
            d_a = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_a,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_real = _emu_group(5010001, 'PhoneA', 'PhoneA', ctime=_START)
            g_conf_t1 = _emu_group(5010002, 'OtherB', 'OtherB', ctime=_START)
            emus = {
                'PhoneA_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5010001,
                    'name': 'PhoneA',
                    'first_seen': 1.0,
                },
                'OtherB_emulator-5556': {
                    'current_serial': 'emulator-5556',
                    'current_pid': 5010002,
                    'name': 'OtherB',
                    'first_seen': 1.0,
                },
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_a], {
                                     'emulator-5554': {5010001},
                                     'emulator-5556': {5010002},
                                 }, {
                                     'emulator-5554': g_real,
                                     'emulator-5556': g_conf_t1,
                                 }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(emus, {
                    'emulator-5554': 'device',
                    'emulator-5556': 'device',
                },
                               now_wall=float(_START + 1),
                               now_mono=1.0)

            self.assertEqual(
                tracker.emulator_records['OtherB_emulator-5556']['daemon_key'],
                (_DAEMON_A, float(_START)))

            # Tick 2: INI resolves port 43929, OtherB resolves conflicting 49999.
            _write_ini(os.path.join(dir_a, 'netsim.ini'),
                       f'pid={_DAEMON_A}\ngrpc.port=43929\n',
                       mtime=float(_START + 1))
            g_real_t2 = dict(g_real, grpc_endpoint='127.0.0.1:43929')
            g_conf_t2 = dict(g_conf_t1, grpc_endpoint='127.0.0.1:49999')
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_a], {
                                     'emulator-5554': {5010001},
                                     'emulator-5556': {5010002},
                                 }, {
                                     'emulator-5554': g_real_t2,
                                     'emulator-5556': g_conf_t2,
                                 }, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(
                        tracker,
                        '_collect_loopback_grpc_sockets',
                        return_value={
                            43929: [{'serial': 'emulator-5554', 'peer_port': 51010}]
                        }):
                tracker.update(emus, {
                    'emulator-5554': 'device',
                    'emulator-5556': 'device',
                },
                               now_wall=float(_START + 3),
                               now_mono=3.0)

            rec_other = tracker.emulator_records['OtherB_emulator-5556']
            rec_real = tracker.emulator_records['PhoneA_emulator-5554']
            self.assertIsNone(rec_other['daemon_key'])
            self.assertIsNone(rec_other['netsim_daemon_ready_s'])
            self.assertEqual(rec_other['state'], 'CONNECTING')
            self.assertIn('WIFI', rec_real['chip_attach_s'])
            self.assertIsNotNone(rec_real['wifi_associated_s'])

            # 2) Compatible single daemon exiting with startup_timeout still
            # marks its unattached emulator FAILED(launcher_abort).
            dir_c = os.path.join(tmpdir, 'c')
            art_c = os.path.join(dir_c, 'android-user', 'netsimd')
            os.makedirs(art_c, exist_ok=True)
            _touch(
                os.path.join(art_c, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:06.000 s.rs:1 - Startup timeout reached\n'
            )
            tracker_c = erm.NetsimTracker(pcap_dir=None,
                                          enable_log=True,
                                          out_dir=tmpdir,
                                          os_family='linux')
            tracker_c.custom_ini_dirs = [dir_c]
            d_c = {
                'pid': _DAEMON_B,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_c,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_c = _emu_group(5010003, 'PhoneC', 'PhoneC', ctime=_START)
            emus_c = {
                'PhoneC_emulator-5558': {
                    'current_serial': 'emulator-5558',
                    'current_pid': 5010003,
                    'name': 'PhoneC',
                    'first_seen': 1.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_c], {'emulator-5558': {5010003}},
                                               {'emulator-5558': g_c}, {})), \
                    patch.object(tracker_c, '_read_proc_locks', return_value={}), \
                    patch.object(tracker_c, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker_c.update(emus_c, {'emulator-5558': 'device'},
                                 now_wall=float(_START + 1),
                                 now_mono=1.0)
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([], {'emulator-5558': {5010003}},
                                               {'emulator-5558': g_c}, {})), \
                    patch.object(tracker_c, '_read_proc_locks', return_value={}), \
                    patch.object(tracker_c, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker_c.update(emus_c, {'emulator-5558': 'device'},
                                 now_wall=float(_START + 3),
                                 now_mono=3.0)
            self.assertEqual(
                tracker_c.emulator_records['PhoneC_emulator-5558']['state'],
                'FAILED(launcher_abort)')

            # 3) Second active daemon spawning while old_d is still alive clears
            # unattached provisional daemon_key and netsim_daemon_ready_s.
            dir_m1 = os.path.join(tmpdir, 'm1')
            dir_m2 = os.path.join(tmpdir, 'm2')
            art_m1 = os.path.join(dir_m1, 'android-user', 'netsimd')
            art_m2 = os.path.join(dir_m2, 'android-user', 'netsimd')
            os.makedirs(art_m1, exist_ok=True)
            os.makedirs(art_m2, exist_ok=True)
            _touch(
                os.path.join(art_m1, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.300 s.rs:1 - Wrote to INI file {dir_m1}/netsim.ini\n'
            )
            tracker_m = erm.NetsimTracker(pcap_dir=None,
                                          enable_log=True,
                                          out_dir=tmpdir,
                                          os_family='linux')
            tracker_m.custom_ini_dirs = [dir_m1, dir_m2]
            dm_1 = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_m1,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            dm_2 = {
                'pid': _DAEMON_B,
                'create_time': float(_START + 2),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd', '--instance', '2'],
                'environ': {
                    'TMPDIR': dir_m2,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_m = _emu_group(5010004, 'PhoneM', 'PhoneM', ctime=_START)
            emus_m = {
                'PhoneM_emulator-5560': {
                    'current_serial': 'emulator-5560',
                    'current_pid': 5010004,
                    'name': 'PhoneM',
                    'first_seen': 1.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([dm_1], {'emulator-5560': {5010004}},
                                               {'emulator-5560': g_m}, {})), \
                    patch.object(tracker_m, '_read_proc_locks', return_value={}), \
                    patch.object(tracker_m, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker_m.update(emus_m, {'emulator-5560': 'device'},
                                 now_wall=float(_START + 1),
                                 now_mono=1.0)
            self.assertIsNotNone(
                tracker_m.emulator_records['PhoneM_emulator-5560']
                ['netsim_daemon_ready_s'])
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([dm_1, dm_2],
                                               {'emulator-5560': {5010004}},
                                               {'emulator-5560': g_m}, {})), \
                    patch.object(tracker_m, '_read_proc_locks', return_value={}), \
                    patch.object(tracker_m, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker_m.update(emus_m, {'emulator-5560': 'device'},
                                 now_wall=float(_START + 3),
                                 now_mono=3.0)
            self.assertIsNone(tracker_m.emulator_records['PhoneM_emulator-5560']
                              ['daemon_key'])
            self.assertIsNone(tracker_m.emulator_records['PhoneM_emulator-5560']
                              ['netsim_daemon_ready_s'])

    def test_au10_netsim_02_zero_offset_tailer_does_not_steal_replacement_server_truncated_log(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Lingering 0-offset daemon must not steal replacement server's
            # in-place O_TRUNC log, port, version, or endpoint match.
            dir_s = os.path.join(tmpdir, 'shared')
            art_s = os.path.join(dir_s, 'android-user', 'netsimd')
            os.makedirs(art_s, exist_ok=True)
            log_s = os.path.join(art_s, 'netsim_stderr.log')
            _touch(log_s, '')

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir_s]
            d_old = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_s,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_new = {
                'pid': _DAEMON_B,
                'create_time': float(_START + 2),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_s,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_old], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}):
                tracker.update({}, {}, now_wall=float(_START + 1), now_mono=1.0)

            self.assertEqual(
                tracker.daemons[(_DAEMON_A, float(_START))]['tailer'].offset, 0)

            with open(log_s, 'w', encoding='utf-8') as f:
                f.write(
                    'netsim I 09-25 22:37:07.100 s.rs:1 - Netsim Version: 1.0.31\n'
                    'netsim I 09-25 22:37:07.200 s.rs:2 - gRPC port: 43930\n'
                    f'netsim I 09-25 22:37:07.300 s.rs:3 - Wrote to INI file {dir_s}/netsim.ini\n'
                )
            g_emu = dict(_emu_group(5010010, 'Phone', 'Phone',
                                    ctime=_START + 2),
                         grpc_endpoint='127.0.0.1:43930')
            emus = {
                'Phone_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5010010,
                    'name': 'Phone',
                    'first_seen': 3.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_old, d_new],
                                               {'emulator-5554': {5010010}},
                                               {'emulator-5554': g_emu}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(emus, {'emulator-5554': 'device'},
                               now_wall=float(_START + 3),
                               now_mono=3.0)

            rec_old = tracker.daemons[(_DAEMON_A, float(_START))]
            rec_new = tracker.daemons[(_DAEMON_B, float(_START + 2))]
            self.assertIsNone(rec_old['grpc_port'])
            self.assertIsNone(rec_old['version'])
            self.assertIsNone(rec_old['ready_ts'])
            self.assertEqual(rec_new['grpc_port'], 43930)
            self.assertEqual(rec_new['version'], '1.0.31')
            self.assertEqual(
                tracker.emulator_records['Phone_emulator-5554']['daemon_key'],
                (_DAEMON_B, float(_START + 2)))

            # Tick 3: d_old exits while d_new remains active; exited 0-offset
            # d_old must still not ingest d_new's log.
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_new],
                                               {'emulator-5554': {5010010}},
                                               {'emulator-5554': g_emu}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(emus, {'emulator-5554': 'device'},
                               now_wall=float(_START + 4),
                               now_mono=4.0)
            self.assertIsNone(rec_old['grpc_port'])
            self.assertIsNone(rec_old['version'])
            self.assertFalse(
                any(f'READY(pid={_DAEMON_A},' in ev
                    for _, ev in tracker.timeline))

            # 2) Newer client-mode sharer alongside 0-offset active server must
            # not block the active server from polling its own log.
            dir_cm = os.path.join(tmpdir, 'cm')
            art_cm = os.path.join(dir_cm, 'android-user', 'netsimd')
            os.makedirs(art_cm, exist_ok=True)
            _touch(
                os.path.join(art_cm, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:05.100 s.rs:1 - gRPC port: 43931\n'
                f'netsim I 09-25 22:37:05.200 s.rs:2 - Wrote to INI file {dir_cm}/netsim.ini\n'
                'netsim W 09-25 22:37:07.100 s.rs:3 - Lock held by another process\n'
            )
            tracker_cm = erm.NetsimTracker(pcap_dir=None,
                                           enable_log=True,
                                           out_dir=tmpdir,
                                           os_family='linux')
            tracker_cm.custom_ini_dirs = [dir_cm]
            d_srv = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_cm,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_cli = {
                'pid': _DAEMON_B,
                'create_time': float(_START + 2),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_cm,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_srv, d_cli], {}, {}, {})), \
                    patch.object(tracker_cm, '_read_proc_locks', return_value={}):
                tracker_cm.update({}, {},
                                  now_wall=float(_START + 3),
                                  now_mono=3.0)
            self.assertTrue(
                tracker_cm.daemons[(_DAEMON_B,
                                    float(_START +
                                          2))].get('client_mode_recorded'))
            self.assertEqual(
                tracker_cm.daemons[(_DAEMON_A, float(_START))]['grpc_port'],
                43931)


class TestRound11Fixes(unittest.TestCase):

    def setUp(self):
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)
        ncli = patch(
            'emulator_resource_monitor.NetsimPcapManager._run_netsim_cli',
            return_value=None)
        ncli.start()
        self.addCleanup(ncli.stop)
        scli = patch('emulator_resource_monitor._run_cli_with_timeout',
                     return_value=None)
        scli.start()
        self.addCleanup(scli.stop)

    def test_au11_py_01_and_04_subsecond_client_mode_spawn_and_active_client_isolation(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Sub-second client-mode spawn alongside 0-offset server:
            # client-mode records CLIENT_MODE_SPAWN without stealing server
            # port/version/READY or starving the 0-offset server tailer, and
            # active client_mode_recorded neither breaks single-daemon fallback
            # nor triggers ORPHANED after >=30s.
            dir1 = os.path.join(tmpdir, 'd1')
            art1 = os.path.join(dir1, 'android-user', 'netsimd')
            os.makedirs(art1, exist_ok=True)
            ini1 = os.path.join(dir1, 'netsim.ini')
            _touch(ini1, f'grpc.port=43931\npid={_DAEMON_A}\n')
            _touch(
                os.path.join(art1, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:05.100 s.rs:1 - Netsim Version: 1.0.31\n'
                'netsim I 09-25 22:37:05.200 s.rs:2 - gRPC port: 43931\n'
                f'netsim I 09-25 22:37:05.250 s.rs:3 - Wrote to INI file {ini1}\n'
                'netsim I 09-25 22:37:05.350 d.rs:4 - '
                'DeviceActor: AddChip 0 (kind WIFI) to device 1 (Pixel 8)\n'
                'netsim W 09-25 22:37:05.600 s.rs:5 - Lock held by another process\n'
            )
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir1]
            d_srv = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir1,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_cli = {
                'pid': _DAEMON_B,
                'create_time': float(_START) + 0.5,
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir1,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_phone = _emu_group(5010100, 'Phone', 'Pixel 8', ctime=_START)
            g_phone.pop('grpc_endpoint', None)
            emus = {
                'Phone_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5010100,
                    'name': 'Phone | Pixel 8',
                    'first_seen': 1.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_srv, d_cli],
                                               {'emulator-5554': {5010100}},
                                               {'emulator-5554': g_phone}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(emus, {'emulator-5554': 'device'},
                               now_wall=float(_START + 2),
                               now_mono=2.0)
                tracker.update(emus, {'emulator-5554': 'device'},
                               now_wall=float(_START + 38),
                               now_mono=38.0)

            srv_rec = tracker.daemons[(_DAEMON_A, float(_START))]
            cli_rec = tracker.daemons[(_DAEMON_B, float(_START) + 0.5)]
            self.assertTrue(cli_rec.get('client_mode_recorded'))
            self.assertIsNone(cli_rec['grpc_port'])
            self.assertIsNone(cli_rec['ready_ts'])
            self.assertFalse(cli_rec.get('orphaned'))
            self.assertFalse(srv_rec.get('client_mode_recorded'))
            self.assertTrue(srv_rec['log_available'])
            self.assertEqual(srv_rec['version'], '1.0.31')
            self.assertIsNotNone(srv_rec['ready_ts'])
            self.assertTrue(
                any(ev == f'CLIENT_MODE_SPAWN(pid={_DAEMON_B})'
                    for _, ev in tracker.timeline))
            self.assertFalse(
                any(
                    ev.startswith(f'READY(pid={_DAEMON_B},')
                    for _, ev in tracker.timeline))
            emu_rec = tracker.emulator_records['Phone_emulator-5554']
            self.assertEqual(emu_rec['state'], 'CONNECTED')
            self.assertEqual(emu_rec['daemon_key'], (_DAEMON_A, float(_START)))
            self.assertEqual(emu_rec['disconnect_count'], 0)

            # 2) Pid-less INI fallback pairs with server DAEMON_A, not active
            # client_mode_recorded DAEMON_B.
            dir2 = os.path.join(tmpdir, 'd2')
            art2 = os.path.join(dir2, 'android-user', 'netsimd')
            os.makedirs(art2, exist_ok=True)
            ini2 = os.path.join(dir2, 'netsim.ini')
            _touch(ini2, 'grpc.port=43932\n')
            os.utime(ini2, (float(_START + 1), float(_START + 1)))
            _touch(
                os.path.join(art2, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:05.100 s.rs:1 - Netsim Version: 1.0.31\n'
                'netsim W 09-25 22:37:06.100 s.rs:2 - Lock held by another process\n'
            )
            tracker2 = erm.NetsimTracker(pcap_dir=None,
                                         enable_log=True,
                                         out_dir=tmpdir,
                                         os_family='linux')
            tracker2.custom_ini_dirs = [dir2]
            d_srv2 = dict(d_srv, environ={'TMPDIR': dir2, 'USER': 'user'})
            d_cli2 = dict(d_cli,
                          create_time=float(_START + 1),
                          environ={
                              'TMPDIR': dir2,
                              'USER': 'user'
                          })
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_srv2, d_cli2], {}, {}, {})), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}):
                tracker2.update({}, {},
                                now_wall=float(_START + 2),
                                now_mono=2.0)
            self.assertEqual(
                tracker2.daemons[(_DAEMON_A, float(_START))]['grpc_port'],
                43932)
            self.assertIsNone(
                tracker2.daemons[(_DAEMON_B, float(_START + 1))]['grpc_port'])

    def test_au11_py_02_exited_endpoint_match_preserved_and_bystander_provisional_cleared(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Confirmed grpc_endpoint match preserves daemon_key and
            # netsim_daemon_ready_s when the emulator exits before chip attach
            # while a second daemon (--instance 2) is also running.
            dir_a = os.path.join(tmpdir, 'a')
            dir_b = os.path.join(tmpdir, 'b')
            art_a = os.path.join(dir_a, 'android-user', 'netsimd')
            art_b = os.path.join(dir_b, 'android-user', 'netsimd')
            os.makedirs(art_a, exist_ok=True)
            os.makedirs(art_b, exist_ok=True)
            ini_a = os.path.join(dir_a, 'netsim.ini')
            ini_b = os.path.join(dir_b, 'netsim_2.ini')
            _touch(ini_a, f'grpc.port=43929\npid={_DAEMON_A}\n')
            _touch(ini_b, f'grpc.port=43930\npid={_DAEMON_B}\n')
            _touch(
                os.path.join(art_a, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.450 s.rs:1 - Wrote to INI file {ini_a}\n'
            )
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir_a, dir_b]
            da = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_a,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            db = {
                'pid': _DAEMON_B,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd', '--instance', '2'],
                'environ': {
                    'TMPDIR': dir_b,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_a = dict(_emu_group(5010200, 'PhoneA', 'PhoneA', ctime=_START),
                       grpc_endpoint='127.0.0.1:43929')
            emus_a = {
                'PhoneA_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5010200,
                    'name': 'PhoneA',
                    'first_seen': 1.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([da, db], {
                                      'emulator-5554': {5010200}
                                  }, {
                                      'emulator-5554': g_a
                                  }, {})):
                    tracker.update(emus_a, {'emulator-5554': 'device'},
                                   now_wall=float(_START + 1),
                                   now_mono=1.0)
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([da, db], {}, {}, {})):
                    tracker.update({}, {},
                                   now_wall=float(_START + 2),
                                   now_mono=2.0)
                    tracker.update({}, {},
                                   now_wall=float(_START + 3),
                                   now_mono=3.0)

            rec_a = tracker.emulator_records['PhoneA_emulator-5554']
            self.assertEqual(rec_a['state'], 'FAILED(worker_exit_no_connect)')
            self.assertEqual(rec_a['daemon_key'], (_DAEMON_A, float(_START)))
            self.assertAlmostEqual(rec_a['netsim_daemon_ready_s'],
                                   0.45,
                                   places=2)

            # 2) Pre-existing bystander daemon provisionally joined on Tick 1
            # clears daemon_key and netsim_daemon_ready_s when a second same-user
            # daemon spawns on Tick 2 sharing the same artifact_dir/instance.
            dir_sh = os.path.join(tmpdir, 'sh')
            art_sh = os.path.join(dir_sh, 'android-user', 'netsimd')
            os.makedirs(art_sh, exist_ok=True)
            _touch(
                os.path.join(art_sh, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.200 s.rs:1 - Wrote to INI file {dir_sh}/netsim.ini\n'
            )
            tracker_b = erm.NetsimTracker(pcap_dir=None,
                                          enable_log=True,
                                          out_dir=tmpdir,
                                          os_family='linux')
            tracker_b.custom_ini_dirs = [dir_sh]
            d_bys = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_sh,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_own = {
                'pid': _DAEMON_B,
                'create_time': float(_START + 12),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_sh,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_b = _emu_group(5010201, 'PhoneB', 'PhoneB', ctime=_START + 10)
            emus_b = {
                'PhoneB_emulator-5556': {
                    'current_serial': 'emulator-5556',
                    'current_pid': 5010201,
                    'name': 'PhoneB',
                    'first_seen': 10.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(tracker_b, '_read_proc_locks', return_value={}), \
                    patch.object(tracker_b, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([d_bys], {
                                      'emulator-5556': {5010201}
                                  }, {
                                      'emulator-5556': g_b
                                  }, {})):
                    tracker_b.update(emus_b, {'emulator-5556': 'device'},
                                     now_wall=float(_START + 11),
                                     now_mono=11.0)
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([d_bys, d_own], {
                                      'emulator-5556': {5010201}
                                  }, {
                                      'emulator-5556': g_b
                                  }, {})):
                    tracker_b.update(emus_b, {'emulator-5556': 'device'},
                                     now_wall=float(_START + 13),
                                     now_mono=13.0)
            rec_b = tracker_b.emulator_records['PhoneB_emulator-5556']
            self.assertIsNone(rec_b['daemon_key'])
            self.assertIsNone(rec_b['netsim_daemon_ready_s'])

    def test_au11_py_03_cold_boot_offline_pending_scan_and_retired_bare_adb_reset(
            self):
        import emulator_resource_monitor as erm

        # 1) Cold-boot 'offline' during pending host scan must stay CONNECTING
        # (never settling a phantom FAILED bare record before AVD resolves).
        tick1 = [0]
        now_t1 = [100.0]
        now_w1 = [float(_START)]
        dev_seq1 = [
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'offline'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        pending_host1 = MagicMock()

        def submit_se1(fn, *args, **kwargs):
            if fn is erm.get_host_stats:
                idx = tick1[0]
                if idx == 0:
                    erm._LAST_NETSIMD_PROCS = []
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    pending_host1.done.side_effect = [False, False, True, True]

                    def finish_host1():
                        erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                        erm._LAST_EMU_GROUP_INFO = {
                            'emulator-5554': {
                                'primary_pid': 2000,
                                'primary_ctime': float(_START),
                                'avd_id': None,
                                'display_name': None,
                                'avd_name': None,
                                'no_netsim': False,
                                'pids': {2000},
                                'member_start_times': {
                                    2000: float(_START)
                                },
                            }
                        }
                        erm._LAST_PRIMARY_PROC_ID = {
                            'emulator-5554': (2000, float(_START))
                        }
                        return _host_tuple({'emulator-5554': 2000}, {},
                                           qemu_stats={
                                               'emulator-5554':
                                                   ('45.0%',
                                                    '1800.0MB (4 thr, 10 cs)')
                                           })

                    pending_host1.result.side_effect = finish_host1
                    return pending_host1
                if idx >= 2:
                    erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                    erm._LAST_EMU_GROUP_INFO = {
                        'emulator-5554': {
                            'primary_pid': 2000,
                            'primary_ctime': float(_START),
                            'avd_id': 'Pixel_8',
                            'display_name': 'Pixel 8',
                            'avd_name': 'Pixel_8',
                            'no_netsim': False,
                            'pids': {2000},
                            'member_start_times': {
                                2000: float(_START)
                            },
                        }
                    }
                    erm._LAST_PRIMARY_PROC_ID = {
                        'emulator-5554': (2000, float(_START))
                    }
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple(
                        {'emulator-5554': 2000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={
                            'emulator-5554':
                                ('60.0%', '2000.0MB (4 thr, 10 cs)')
                        })
                    return f
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_guest1(_serial, known_model, is_booting, status):
            if status != 'device':
                return (known_model if known_model else f'Unknown ({status})',
                        bool(is_booting), f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})')
            return (_guest_model('Pixel 8'), True, '80.0%', '80.0%',
                    '50.0% (2.0GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_sleep1(_):
            tick1[0] += 1
            now_t1[0] += 2.0
            now_w1[0] += 2.0
            if tick1[0] == 4:
                raise KeyboardInterrupt

        exec1 = MagicMock()
        exec1.submit.side_effect = submit_se1
        with patch.object(
                erm,
                'get_proc_precise_start_time',
                side_effect=lambda _p, fallback_create_time=None: fallback_create_time
        ), \
                patch('concurrent.futures.ThreadPoolExecutor',
                      return_value=exec1), \
                patch('concurrent.futures.wait',
                      side_effect=lambda _fs, **_kw: (set(), set())), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq1[min(tick1[0], 3)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest1), \
                patch('emulator_resource_monitor.save_summary') as mock_save1, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t1[0]), \
                patch('time.time', side_effect=lambda: now_w1[0]), \
                patch('time.sleep', side_effect=fake_sleep1), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        summary1 = mock_save1.call_args[0][0]
        ts1 = list(summary1['time_series'])
        self.assertEqual(ts1[0]['netsim_state_Pixel_8_emulator-5554'],
                         'CONNECTING')
        self.assertNotIn('netsim_state_emulator-5554', ts1[2])

        # 2) Retired bare entry ('emulator-5554') reactivated in main() ADB loop
        # while new_pid is None (pending host scan) must retire in-place and
        # migrate cleanly once the new AVD resolves.
        tick2 = [0]
        now_t2 = [500.0]
        now_w2 = [float(_START + 200)]
        dev_seq2 = [
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
            {},
            {
                'emulator-5554': 'device'
            },
            {
                'emulator-5554': 'device'
            },
        ]
        pending_host2 = MagicMock()

        def submit_se2(fn, *args, **kwargs):
            if fn is erm.get_host_stats:
                idx = tick2[0]
                if idx <= 1:
                    erm._LAST_NETSIMD_PROCS = []
                    erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {900}}
                    erm._LAST_EMU_GROUP_INFO = {
                        'emulator-5554': {
                            'primary_pid': 900,
                            'primary_ctime': float(_START + 190),
                            'avd_id': None,
                            'display_name': None,
                            'avd_name': None,
                            'no_netsim': False,
                            'pids': {900},
                            'member_start_times': {
                                900: float(_START + 190)
                            },
                        }
                    }
                    erm._LAST_PRIMARY_PROC_ID = {
                        'emulator-5554': (900, float(_START + 190))
                    }
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple(
                        {'emulator-5554': 900}, {},
                        qemu_stats={
                            'emulator-5554':
                                ('15.0%', '1200.0MB (4 thr, 10 cs)')
                        })
                    return f
                if idx == 2:
                    erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {1000}}
                    erm._LAST_EMU_GROUP_INFO = {
                        'emulator-5554': {
                            'primary_pid': 1000,
                            'primary_ctime': float(_START + 204),
                            'avd_id': 'Pixel_8',
                            'display_name': 'Pixel 8',
                            'avd_name': 'Pixel_8',
                            'no_netsim': False,
                            'pids': {1000},
                            'member_start_times': {
                                1000: float(_START + 204)
                            },
                        }
                    }
                    erm._LAST_PRIMARY_PROC_ID = {
                        'emulator-5554': (1000, float(_START + 204))
                    }
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple(
                        {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
                        qemu_stats={
                            'emulator-5554':
                                ('30.0%', '1600.0MB (4 thr, 10 cs)')
                        })
                    return f
                if idx == 3:
                    erm._LAST_EMU_GROUP_PIDS = {}
                    erm._LAST_EMU_GROUP_INFO = {}
                    erm._LAST_PRIMARY_PROC_ID = {}
                    f = MagicMock()
                    f.done.return_value = True
                    f.result.return_value = _host_tuple({}, {}, qemu_stats={})
                    return f
                if idx == 4:
                    pending_host2.done.side_effect = [False, False, True, True]

                    def finish_mp():
                        erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': {2000}}
                        erm._LAST_EMU_GROUP_INFO = {
                            'emulator-5554': {
                                'primary_pid': 2000,
                                'primary_ctime': float(_START + 208),
                                'avd_id': 'Medium_Phone',
                                'display_name': 'Medium Phone',
                                'avd_name': 'Medium_Phone',
                                'no_netsim': False,
                                'pids': {2000},
                                'member_start_times': {
                                    2000: float(_START + 208)
                                },
                            }
                        }
                        erm._LAST_PRIMARY_PROC_ID = {
                            'emulator-5554': (2000, float(_START + 208))
                        }
                        return _host_tuple({'emulator-5554': 2000},
                                           {'emulator-5554': 'Medium_Phone'},
                                           qemu_stats={
                                               'emulator-5554':
                                                   ('70.0%',
                                                    '2200.0MB (4 thr, 10 cs)')
                                           })

                    pending_host2.result.side_effect = finish_mp
                    return pending_host2
            f = MagicMock()
            f.done.return_value = True
            f.result.return_value = fn(*args, **kwargs)
            return f

        def fake_guest2(_serial, known_model, is_booting, status):
            if status != 'device':
                return (known_model if known_model else f'Unknown ({status})',
                        bool(is_booting), f'N/A ({status})', f'N/A ({status})',
                        f'N/A ({status})', f'N/A ({status})')
            if tick2[0] <= 1:
                return (_guest_model('BareEmu'), False, '15.0%', '15.0%',
                        '40.0% (1.6GB/4.0GB, 0 errs, 10% data)', '0.0 MB')
            if tick2[0] == 2:
                return (_guest_model('Pixel 8'), False, '30.0%', '30.0%',
                        '45.0% (1.8GB/4.0GB, 0 errs, 10% data)', '0.0 MB')
            return (_guest_model('Medium Phone'), True, '85.0%', '85.0%',
                    '50.0% (2.0GB/4.0GB, 0 errs, 10% data)', '0.0 MB')

        def fake_wait2(fs, timeout=None):
            if tick2[0] == 4 and pending_host2 in fs:
                now_t2[0] += 2.0
                now_w2[0] += 2.0
            return (set(), set())

        def fake_sleep2(_):
            tick2[0] += 1
            now_t2[0] += 2.0
            now_w2[0] += 2.0
            if tick2[0] == 6:
                raise KeyboardInterrupt

        exec2 = MagicMock()
        exec2.submit.side_effect = submit_se2
        with patch.object(
                erm,
                'get_proc_precise_start_time',
                side_effect=lambda _p, fallback_create_time=None: fallback_create_time
        ), \
                patch('concurrent.futures.ThreadPoolExecutor',
                      return_value=exec2), \
                patch('concurrent.futures.wait', side_effect=fake_wait2), \
                patch('emulator_resource_monitor.get_devices',
                      side_effect=lambda: dev_seq2[min(tick2[0], 5)]), \
                patch('emulator_resource_monitor.get_guest_stats',
                      side_effect=fake_guest2), \
                patch('emulator_resource_monitor.save_summary') as mock_save2, \
                patch('emulator_resource_monitor.print_dashboard'), \
                patch('emulator_resource_monitor.setup_alt_screen'), \
                patch('emulator_resource_monitor.restore_screen'), \
                patch('time.monotonic', side_effect=lambda: now_t2[0]), \
                patch('time.time', side_effect=lambda: now_w2[0]), \
                patch('time.sleep', side_effect=fake_sleep2), \
                patch('sys.argv', ['emulator_resource_monitor.py',
                                   '--no-netsim-log']), \
                patch('os._exit', return_value=None):
            erm.main()

        emus2 = mock_save2.call_args[0][0]['emulators']
        self.assertIn('BareEmu', emus2['emulator-5554']['name'])
        self.assertEqual(emus2['emulator-5554']['cpu_iterations'], 1)
        self.assertAlmostEqual(emus2['emulator-5554']['cpu_max'], 15.0)
        self.assertEqual(emus2['Pixel_8_emulator-5554']['cpu_iterations'], 1)
        self.assertAlmostEqual(emus2['Pixel_8_emulator-5554']['cpu_max'], 30.0)
        self.assertEqual(emus2['Medium_Phone_emulator-5554']['cpu_iterations'],
                         2)
        self.assertAlmostEqual(emus2['Medium_Phone_emulator-5554']['cpu_max'],
                               85.0)

    def test_qa_review_client_mode_exit_pcap_csv_and_exited_disabled_bare_isolation(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) Client-mode daemon must not block primary server exit harvest,
            # double-harvest pcaps/zips, or report as netsim_up/netsim_pid in CSV.
            dir_p = os.path.join(tmpdir, 'p')
            art_p = os.path.join(dir_p, 'android-user', 'netsimd')
            pcap_src = os.path.join(art_p, 'pcaps')
            os.makedirs(pcap_src, exist_ok=True)
            ini_p = os.path.join(dir_p, 'netsim.ini')
            _touch(ini_p, f'grpc.port=43940\npid={_DAEMON_B}\n')
            _touch(
                os.path.join(art_p, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:05.100 s.rs:1 - Netsim Version: 1.0.31\n'
                'netsim I 09-25 22:37:05.200 s.rs:2 - gRPC port: 43940\n'
                f'netsim I 09-25 22:37:05.250 s.rs:3 - Wrote to INI file {ini_p}\n'
                'netsim I 09-25 22:37:05.350 d.rs:4 - '
                'DeviceActor: AddChip 0 (kind WIFI) to device 1 (Pixel 8)\n'
                'netsim W 09-25 22:37:05.600 s.rs:5 - Lock held by another process\n'
            )
            _touch(os.path.join(pcap_src, 'netsim-0-Pixel_8-WIFI.pcap'),
                   'pcap-bytes')
            pcap_out = os.path.join(tmpdir, 'out_pcaps')
            tracker = erm.NetsimTracker(pcap_dir=pcap_out,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [dir_p]
            # Client-mode PID (_DAEMON_A < _DAEMON_B) precedes server in dict order.
            d_cli = {
                'pid': _DAEMON_A,
                'create_time': float(_START) + 0.5,
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_p,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            d_srv = {
                'pid': _DAEMON_B,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': dir_p,
                    'USER': 'user'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_phone = dict(_emu_group(5010300, 'Phone', 'Pixel 8',
                                      ctime=_START),
                           grpc_endpoint='127.0.0.1:43940')
            emus = {
                'Phone_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5010300,
                    'name': 'Phone | Pixel 8',
                    'first_seen': 1.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([d_cli, d_srv], {
                                      'emulator-5554': {5010300}
                                  }, {
                                      'emulator-5554': g_phone
                                  }, {})):
                    tracker.update(emus, {'emulator-5554': 'device'},
                                   now_wall=float(_START + 2),
                                   now_mono=2.0)
                csv1 = tracker.get_csv_fields()
                self.assertEqual(csv1['netsim_up'], 1)
                self.assertEqual(csv1['netsim_pid'], _DAEMON_B)

                # Primary server exits while client-mode lingers for 1 tick:
                # server must still harvest its pcap, and CSV reports netsim_up=0.
                os.remove(ini_p)
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([d_cli], {}, {}, {})):
                    tracker.update({}, {},
                                   now_wall=float(_START + 4),
                                   now_mono=4.0)
                csv2 = tracker.get_csv_fields()
                self.assertEqual(csv2['netsim_up'], 0)
                self.assertEqual(csv2['netsim_pid'], '')
                tracker.cleanup()

            harvested = list(tracker.pcap_mgr.copied_files.keys())
            self.assertEqual(len(harvested), 1)
            self.assertIn(f'{_DAEMON_B}-', harvested[0])

            # 2) Exited -no-netsim bare record must stay preserved under its
            # bare serial when a later named AVD connects with pending host scan.
            tracker2 = erm.NetsimTracker(pcap_dir=None,
                                         enable_log=False,
                                         out_dir=tmpdir)
            tracker2.custom_ini_dirs = [tmpdir]
            g_dis = dict(_emu_group(5010400, None, None, ctime=_START),
                         no_netsim=True)
            emus_bare = {
                'emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 5010400,
                    'name': 'emulator-5554',
                    'first_seen': 1.0,
                }
            }
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(tracker2, '_read_proc_locks', return_value={}):
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([], {
                                      'emulator-5554': {5010400}
                                  }, {
                                      'emulator-5554': g_dis
                                  }, {})):
                    tracker2.update(emus_bare, {'emulator-5554': 'device'},
                                    now_wall=float(_START + 1),
                                    now_mono=1.0)
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([], {}, {}, {})):
                    tracker2.update(emus_bare, {},
                                    now_wall=float(_START + 3),
                                    now_mono=3.0)
                    tracker2.update(emus_bare, {},
                                    now_wall=float(_START + 5),
                                    now_mono=5.0)
                self.assertTrue(
                    tracker2._is_exited_disabled(
                        tracker2.emulator_records['emulator-5554']))

                emus_next = {
                    'Pixel_8_emulator-5554': {
                        'current_serial': 'emulator-5554',
                        'current_pid': None,
                        'name': 'Pixel_8 | Pixel 8',
                        'first_seen': 7.0,
                        '_pending_host_scan': True,
                    }
                }
                with patch.object(erm,
                                  '_snapshot_process_state',
                                  return_value=([], {}, {}, {})):
                    tracker2.update(emus_next, {'emulator-5554': 'device'},
                                    now_wall=float(_START + 7),
                                    now_mono=7.0)
            self.assertEqual(
                tracker2.emulator_records['emulator-5554']['state'], 'DISABLED')
            self.assertEqual(
                tracker2.emulator_records['Pixel_8_emulator-5554']['state'],
                'CONNECTING')

    def test_copy_regular_file_unbounded_size_and_resolve_migrate_helper(self):
        import emulator_resource_monitor as erm

        # 1) _copy_regular_file delegates to _open_owned_regular_file with no max_bytes cap.
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, 'large.pcap')
            dst = os.path.join(tmpdir, 'copied.pcap')
            payload = b'\xd4\xc3\xb2\xa1' + (b'P' * (erm._MAX_INI_BYTES + 8192))
            with open(src, 'wb') as f:
                f.write(payload)
            st = erm._copy_regular_file(src, dst)
            self.assertIsNotNone(st)
            self.assertEqual(st.st_size, len(payload))
            with open(dst, 'rb') as f:
                self.assertEqual(f.read(), payload)

            # 2) _reconcile_emulator_session_entry migrates bare serial ->
            # avd_serial, isolates reused PIDs, and enforces keyword-only args.
            summary = {'iterations': 1, 'emulators': {}, 'time_series': []}
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [tmpdir]
            bare_keys = set()
            hg1 = erm._HostGroupIdentity(pid=3100,
                                         proc_id=(3100, 100.0),
                                         group_pids={3100},
                                         group_procs={3100: 100.0})
            with self.assertRaises(TypeError):
                erm._reconcile_emulator_session_entry(summary, tracker)
            r1 = erm._reconcile_emulator_session_entry(
                summary_data=summary,
                netsim_tracker=tracker,
                bare_key_serials=bare_keys,
                serial='emulator-5554',
                avd_name=None,
                model_part='Unknown Device',
                host_group=hg1,
                adb_seen=False)
            self.assertEqual(
                (r1.storage_key, r1.created, r1.restarted, r1.effective_avd),
                ('emulator-5554', True, False, None))
            self.assertIn('emulator-5554', bare_keys)
            r1.entry['qemu_cpu_iterations'] = 1
            r1.entry['qemu_cpu_sum'] = 33.0
            r1.entry['qemu_cpu_max'] = 33.0

            r2 = erm._reconcile_emulator_session_entry(
                summary_data=summary,
                netsim_tracker=tracker,
                bare_key_serials=bare_keys,
                serial='emulator-5554',
                avd_name='Pixel_8',
                model_part='Unknown Device',
                host_group=hg1,
                adb_seen=False)
            self.assertEqual(
                (r2.storage_key, r2.created, r2.restarted, r2.effective_avd),
                ('Pixel_8_emulator-5554', False, False, 'Pixel_8'))
            self.assertNotIn('emulator-5554', bare_keys)
            self.assertEqual(
                summary['emulators']['Pixel_8_emulator-5554']['qemu_cpu_max'],
                33.0)

            # Reused PID 3100 with new start_time 200.0 retires Pixel_8_emulator-5554.
            hg_reused = erm._HostGroupIdentity(pid=3100,
                                               proc_id=(3100, 200.0),
                                               group_pids={3100},
                                               group_procs={3100: 200.0})
            r3 = erm._reconcile_emulator_session_entry(
                summary_data=summary,
                netsim_tracker=tracker,
                bare_key_serials=bare_keys,
                serial='emulator-5554',
                avd_name=None,
                model_part='Unknown Device',
                host_group=hg_reused,
                adb_seen=False)
            self.assertEqual((r3.storage_key, r3.created, r3.effective_avd),
                             ('emulator-5554', True, None))
            self.assertTrue(
                summary['emulators']['Pixel_8_emulator-5554'].get('retired'))

    def test_save_summary_succeeds_without_optional_plotille(self):
        import emulator_resource_monitor as erm

        summary = {
            'iterations':
                2,
            'start_time':
                1000.0,
            'start_time_monotonic':
                1000.0,
            'host_cpu_iterations':
                2,
            'host_cpu_sum':
                60.0,
            'host_cpu_max':
                40.0,
            'host_ram_iterations':
                2,
            'host_ram_sum':
                100.0,
            'host_ram_max':
                50.0,
            'netsim_cpu_iterations':
                0,
            'netsim_cpu_sum':
                0.0,
            'emulators': {},
            'time_series': [
                {
                    'time': 1000.0,
                    'host_cpu': 20.0,
                    'host_ram': 50.0
                },
                {
                    'time': 1005.0,
                    'host_cpu': 40.0,
                    'host_ram': 50.0
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(sys.modules, {'plotille': None}), \
                patch('sys.stdout', new_callable=io.StringIO):
            erm.save_summary(summary,
                             1010.0,
                             save_csv=True,
                             save_txt=True,
                             out_dir=tmpdir,
                             is_autosave=True)
            with open(os.path.join(tmpdir,
                                   'device_monitor_summary_autosave.txt'),
                      encoding='utf-8') as f:
                txt = f.read()
        self.assertIn('Average CPU Usage: 30.0%', txt)
        self.assertNotIn('HOST CPU HISTORY CHART:', txt)

    def test_non_ascii_utf8_paths_and_avd_names(self):
        import csv
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as base_tmp:
            utf8_out = os.path.join(base_tmp, 'out_täst_中文_🚀')
            utf8_tmp = os.path.join(base_tmp, 'tmp_täst_中文_🚀')
            avd_name = 'José_täst_中文_🚀'
            emu_key = f'{avd_name}_emulator-5554'
            user_name = 'José'

            # 1) Artifact, discovery, and pid_<pid>.ini paths with non-ASCII UTF-8.
            art_dir = erm.resolve_netsim_artifact_dir(
                {
                    'TMPDIR': utf8_tmp,
                    'USER': user_name
                }, target_os='linux')
            self.assertEqual(
                art_dir,
                os.path.join(utf8_tmp, f'android-{user_name}', 'netsimd'))
            pcaps_dir = os.path.join(art_dir, 'pcaps')
            os.makedirs(pcaps_dir, exist_ok=True)
            avd_run = os.path.join(utf8_tmp, 'avd', 'running')
            os.makedirs(avd_run, exist_ok=True)
            _touch(
                os.path.join(avd_run, 'pid_4242.ini'),
                f'port.serial=5554\navd.id={avd_name}\navd.name={avd_name}\n')
            fake_proc = MagicMock()
            fake_proc.environ.return_value = {
                'ANDROID_EMULATOR_DISCOVERY_DIR': utf8_tmp
            }
            with patch.object(erm, 'host_discovery_dirs', return_value=[]):
                ini_data = erm._read_emulator_discovery_ini(4242, fake_proc)
            self.assertEqual(ini_data.get('avd.id'), avd_name)

            # 2) NetsimTracker log + PCAP harvesting with non-ASCII device name & out_dir.
            safe_dev = erm.sanitize_netsim_filename(avd_name)
            self.assertEqual(safe_dev, 'José_täst_中文__')
            _touch(
                os.path.join(art_dir, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:05.200 s.rs:1 - gRPC port: 43929\n'
                f'netsim I 09-25 22:37:05.400 d.rs:2 - DeviceActor: AddChip 1 (kind WIFI) to device 1 ({avd_name})\n'
            )
            with open(os.path.join(pcaps_dir, f'netsim-1-{safe_dev}-WIFI.pcap'),
                      'wb') as f:
                f.write(b'\xd4\xc3\xb2\xa1' + b'\x00' * 20)
            tracker = erm.NetsimTracker(pcap_dir='',
                                        enable_log=True,
                                        out_dir=utf8_out,
                                        os_family='linux')
            tracker.custom_ini_dirs = [utf8_tmp]
            d_proc = {
                'pid': _DAEMON_A,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': utf8_tmp,
                    'USER': user_name,
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            g_utf8 = dict(_emu_group(4242, avd_name, avd_name, ctime=_START),
                          grpc_endpoint='127.0.0.1:43929')
            with patch.object(
                    erm,
                    'get_proc_precise_start_time',
                    side_effect=lambda _p, fallback_create_time=None: fallback_create_time
            ), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_proc],
                                               {'emulator-5554': {4242}},
                                               {'emulator-5554': g_utf8}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update(
                    {
                        emu_key: {
                            'current_serial': 'emulator-5554',
                            'current_pid': 4242,
                            'name': f'{avd_name} | Pixel 8',
                            'first_seen': 1.0,
                        }
                    }, {'emulator-5554': 'device'},
                    now_wall=float(_START + 2),
                    now_mono=2.0)
            self.assertEqual(len(tracker.pcap_mgr.copied_files), 1)

            # 3) print_dashboard truncation & save_summary TXT + CSV in UTF-8 out_dir.
            dash_buf = io.StringIO()
            with patch('sys.stdout', dash_buf):
                erm.print_dashboard(
                    os_name='Linux',
                    host_total='12.0%',
                    qemu_stats={'emulator-5554': ('25.0%', '1800.0MB')},
                    netsim_cpu='1.0%',
                    guest_results={
                        'emulator-5554':
                            (f'{avd_name} | Pixel 8_täst_中文_🚀_long_model_label',
                             ' [BOOTING]', '30.0%', '15.0%',
                             '45.0% (1.8GB/4.0GB, 0 errs, 12% data)', '0.0 MB')
                    },
                    host_ram='40.0%',
                    host_swap='0.0%',
                    paging_str='In: 0 / Out: 0 (KB/s)',
                    iter_count=1,
                    term_width=76,
                    force_clear=False,
                    unmapped_qemu=[],
                    netsim_ram='10.0MB',
                    gpu_load='N/A',
                    gpu_ram='N/A',
                    emu_gpu_stats={},
                    hw_specs=f'Host_täst_中文_🚀_{"X" * 90}',
                    netsim_snapshot=tracker.get_dashboard_snapshot(
                        now_wall=float(_START + 2)))
            self.assertIn('José', dash_buf.getvalue())

            summary = {
                'iterations':
                    2,
                'start_time':
                    1000.0,
                'start_time_monotonic':
                    1000.0,
                'host_cpu_iterations':
                    2,
                'host_cpu_sum':
                    30.0,
                'host_cpu_max':
                    20.0,
                'host_ram_iterations':
                    2,
                'host_ram_sum':
                    80.0,
                'host_ram_max':
                    40.0,
                'netsim_cpu_iterations':
                    0,
                'netsim_cpu_sum':
                    0.0,
                'netsim_summary_lines':
                    tracker.format_summary_sections(now_mono=2.0),
                'emulators': {
                    emu_key: {
                        'name': f'{avd_name} | Pixel 8',
                        'cpu_iterations': 2,
                        'cpu_sum': 30.0,
                        'cpu_max': 20.0,
                        'ram_iterations': 2,
                        'ram_sum': 90.0,
                        'ram_max': 45.0,
                    }
                },
                'time_series': [
                    {
                        'time': 1000.0,
                        'host_cpu': 10.0,
                        'host_ram': 40.0,
                        f'emu_cpu_tot_{emu_key}': 10.0,
                    },
                    {
                        'time': 1005.0,
                        'host_cpu': 20.0,
                        'host_ram': 40.0,
                        f'emu_cpu_tot_{emu_key}': 20.0,
                    },
                ],
            }
            fake_fig = MagicMock()
            fake_fig.show.return_value = 'mock_chart_plot'
            with patch.dict(sys.modules,
                            {'plotille': MagicMock(Figure=lambda: fake_fig)}), \
                    patch('sys.stdout', new_callable=io.StringIO):
                erm.save_summary(summary,
                                 1010.0,
                                 save_csv=True,
                                 save_txt=True,
                                 out_dir=utf8_out)
            txt_files = [
                f for f in os.listdir(utf8_out) if
                f.startswith('device_monitor_summary_') and f.endswith('.txt')
            ]
            csv_files = [
                f for f in os.listdir(utf8_out) if
                f.startswith('device_monitor_summary_') and f.endswith('.csv')
            ]
            self.assertEqual(len(txt_files), 1)
            self.assertEqual(len(csv_files), 1)
            with open(os.path.join(utf8_out, txt_files[0]),
                      encoding='utf-8') as f:
                txt_content = f.read()
            self.assertIn(f'GUEST SYSTEM ({avd_name} | Pixel 8):', txt_content)
            with open(os.path.join(utf8_out, csv_files[0]),
                      encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[-1][f'emu_cpu_tot_{emu_key}'], '20.0')

    def test_non_ascii_utf8_paths_tmpdir_and_avd_names(self):
        import emulator_resource_monitor as erm

        self.assertEqual(erm.parse_cmdline(['emulator', '-avd', 'täst_中文_🚀']),
                         (None, 'täst_中文_🚀'))
        self.assertEqual(erm.parse_cmdline(['emulator', '@José']),
                         (None, 'José'))

        with tempfile.TemporaryDirectory() as tmpdir:
            utf8_tmpdir = os.path.join(tmpdir, 'tmp_täst_中文_🚀')
            running_dir = os.path.join(utf8_tmpdir, 'avd', 'running')
            os.makedirs(running_dir, exist_ok=True)
            ini_file = os.path.join(running_dir, 'pid_424242.ini')
            with open(ini_file, 'w', encoding='utf-8') as f:
                f.write('port.serial=5554\navd.id=täst_中文_🚀\navd.name=José\n')

            fake_proc = MagicMock()
            fake_proc.environ.return_value = {'TMPDIR': utf8_tmpdir}
            disc = erm._read_emulator_discovery_ini(424242, proc_obj=fake_proc)
            self.assertEqual(disc.get('avd.id'), 'täst_中文_🚀')
            self.assertEqual(disc.get('avd.name'), 'José')

            utf8_out_dir = os.path.join(tmpdir, 'täst_中文_🚀', 'José')
            summary_data = {
                'start_time':
                    1700000000.0,
                'start_time_monotonic':
                    100.0,
                'iterations':
                    2,
                'host_cpu_sum':
                    60.0,
                'host_cpu_max':
                    35.0,
                'host_cpu_iterations':
                    2,
                'host_ram_sum':
                    100.0,
                'host_ram_max':
                    50.0,
                'host_ram_iterations':
                    2,
                'netsim_cpu_sum':
                    0.0,
                'netsim_cpu_iterations':
                    0,
                'emulators': {
                    'emulator-5554': {
                        'name': 'emulator-5554',
                        'cpu_sum': 10.0,
                        'cpu_max': 10.0,
                        'cpu_iterations': 1,
                        'ram_sum': 55.0,
                        'ram_max': 55.0,
                        'ram_iterations': 1,
                    },
                    'José_emulator-5556': {
                        'name': 'José | Watch',
                        'cpu_sum': 10.0,
                        'cpu_max': 10.0,
                        'cpu_iterations': 1,
                        'ram_sum': 45.0,
                        'ram_max': 45.0,
                        'ram_iterations': 1,
                    },
                },
                'time_series': [
                    {
                        'time': 101.0,
                        'host_cpu': 25.0,
                        'emu_cpu_tot_emulator-5554': 40.0,
                        'emu_cpu_adj_emulator-5554': 10.0,
                        'emu_ram_emulator-5554': 55.0,
                    },
                    {
                        'time': 102.0,
                        'host_cpu': 35.0,
                        'emu_cpu_tot_täst_中文_🚀_emulator-5554': 60.0,
                        'emu_cpu_adj_täst_中文_🚀_emulator-5554': 15.0,
                        'emu_ram_täst_中文_🚀_emulator-5554': 65.0,
                        'emu_cpu_tot_José_emulator-5556': 20.0,
                        'emu_cpu_adj_José_emulator-5556': 10.0,
                        'emu_ram_José_emulator-5556': 45.0,
                    },
                ],
            }
            erm._migrate_summary_keys(summary_data, 'emulator-5554',
                                      'täst_中文_🚀_emulator-5554')
            summary_data['emulators']['täst_中文_🚀_emulator-5554'][
                'name'] = 'täst_中文_🚀 | Pixel 9'

            with patch.object(erm, 'run_cmd', return_value=''):
                erm.save_summary(summary_data,
                                 1700000002.0,
                                 save_csv=True,
                                 save_txt=True,
                                 out_dir=utf8_out_dir,
                                 is_autosave=True)

            with open(os.path.join(utf8_out_dir,
                                   'device_monitor_summary_autosave.txt'),
                      encoding='utf-8') as f:
                txt = f.read()
            with open(os.path.join(utf8_out_dir,
                                   'device_monitor_summary_autosave.csv'),
                      encoding='utf-8') as f:
                csv_text = f.read()

            self.assertIn('GUEST SYSTEM (täst_中文_🚀 | Pixel 9):', txt)
            self.assertIn('GUEST SYSTEM (José | Watch):', txt)
            self.assertNotIn('GUEST SYSTEM (emulator-5554', txt)
            header = csv_text.splitlines()[0]
            self.assertIn('emu_cpu_tot_täst_中文_🚀_emulator-5554', header)
            self.assertIn('emu_cpu_tot_José_emulator-5556', header)
            self.assertNotIn('emu_cpu_tot_emulator-5554', header)

            buf = io.StringIO()
            guest_results = {
                'emulator-5554': (
                    'täst_中文_🚀_José | Pixel 9 Pro | Android 16 (API 36) | Build: BGQ1.260312.001 | Res: 1080x2400',
                    ' [RECORDING BOOT STATS]',
                    '60.0%',
                    '15.0% (over 4 cores)',
                    '65.0% (2.6GB/4.0GB, 0 errs, 25% data)',
                    '0.0 MB',
                )
            }
            with patch('sys.stdout', buf):
                erm.print_dashboard(
                    'Linux',
                    '25.0%',
                    {},
                    'N/A',
                    guest_results,
                    '50.0% (8.0GB/16.0GB)',
                    '0.0% (0.0GB/4.0GB)',
                    'N/A',
                    1,
                    76,
                    True,
                    [],
                    'N/A',
                    'N/A',
                    'N/A',
                    {},
                    is_once=True,
                    hw_specs=
                    'Linux | täst_中文_🚀 | José — 64-Core Processor | 128.0 GB RAM',
                )
            dash = buf.getvalue()
            self.assertIn('täst_中文_🚀', dash)
            self.assertIn('…', dash)


class TestTrackingHardening(unittest.TestCase):
    """Trust checks, parsing of text from other processes, session bookkeeping."""

    def test_pcap_cli_skips_missing_ini_dir_and_pins_netsim_instance_env(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'art')
            pcaps_dir = os.path.join(art_dir, 'pcaps')
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(pcaps_dir, exist_ok=True)
            with open(os.path.join(pcaps_dir, 'netsim-1-Phone-WIFI.pcap'),
                      'wb') as f:
                f.write(b'\xd4\xc3\xb2\xa1' + b'\x00' * 16)

            mgr = erm.NetsimPcapManager(pcap_dir='', max_mb=1, out_dir=out_dir)
            runs = []

            def fake_run(cmd, **kwargs):
                runs.append((cmd, kwargs))
                return MagicMock(stdout='OK', stderr='')

            with patch.dict(os.environ, {'NETSIM_INSTANCE': '99'}), \
                    patch.object(erm.subprocess, 'run', side_effect=fake_run):
                self.assertIsNone(
                    mgr._run_netsim_cli('netsim',
                                        43929, ['capture', 'list'],
                                        ini_dir=None,
                                        instance_num=2))
                self.assertEqual(runs, [])

                ini_dir = os.path.join(tmpdir, 'ini_dir')
                os.makedirs(ini_dir, exist_ok=True)
                self.assertEqual(
                    mgr._run_netsim_cli('netsim',
                                        43929, ['capture', 'list'],
                                        ini_dir=ini_dir,
                                        instance_num=None), 'OK')
                self.assertEqual(runs[-1][1]['env']['NETSIM_INSTANCE'], '1')
                self.assertEqual(runs[-1][1]['env']['TMPDIR'], ini_dir)

                self.assertEqual(
                    mgr._run_netsim_cli('netsim',
                                        43929, ['capture', 'list'],
                                        ini_dir=ini_dir,
                                        instance_num=2), 'OK')
                self.assertEqual(runs[-1][1]['env']['NETSIM_INSTANCE'], '2')

            cli_calls = [0]

            def count_cli(*_args, **_kwargs):
                cli_calls[0] += 1
                return None

            mgr._run_netsim_cli = count_cli
            daemon_no_ini = {
                'daemon_key': (9100, 1000.0),
                'pid': 9100,
                'start_wall': 1000.0,
                'grpc_port': 43929,
                'ini_path': None,
                'artifact_dir': art_dir,
                'same_user': True,
                'exited': False,
            }
            joined = [{
                'serial': 'emulator-5554',
                'device_name': 'Phone',
                'state': 'CONNECTED',
                'chip_ids': {'1'},
            }]
            mgr.poll_and_manage_daemon(daemon_no_ini, joined, now_mono=10.0)
            self.assertEqual(cli_calls[0], 0)
            self.assertEqual(mgr.skipped_notes, [])
            self.assertEqual(mgr.cli_error_counts, {})
            self.assertTrue(bool(mgr.copied_files))

    def test_read_emulator_discovery_ini_enforces_real_dir_and_uid(self):
        import emulator_resource_monitor as erm

        my_uid = erm._current_uid()
        foreign_uid = (my_uid or 1000) + 1
        pid = 5_009_321
        with tempfile.TemporaryDirectory() as tmpdir:
            shared_tmp = os.path.join(tmpdir, 'shared_tmp')
            running = os.path.join(shared_tmp, 'avd', 'running')
            os.makedirs(running, exist_ok=True)
            _touch(
                os.path.join(running, f'pid_{pid}.ini'),
                'port.serial=5554\navd.id=Pixel_8\nnetsim.endpoint=localhost:43929\n'
            )
            w = MagicMock()
            w.environ.return_value = {
                'ANDROID_EMULATOR_DISCOVERY_DIR': shared_tmp
            }
            with patch.object(erm, 'host_discovery_dirs', return_value=[]):
                self.assertEqual(
                    erm._read_emulator_discovery_ini(pid, w).get('avd.id'),
                    'Pixel_8')
                if os.name == 'posix' and hasattr(os, 'getuid'):
                    with patch.object(erm,
                                      '_current_uid',
                                      return_value=foreign_uid):
                        self.assertEqual(
                            erm._read_emulator_discovery_ini(pid, w), {})
                    with patch.object(erm, '_current_uid', return_value=0):
                        self.assertEqual(
                            erm._read_emulator_discovery_ini(pid,
                                                             w).get('avd.id'),
                            'Pixel_8')

            sym_base = os.path.join(tmpdir, 'sym_base')
            os.makedirs(sym_base, exist_ok=True)
            if _try_symlink(os.path.join(shared_tmp, 'avd'),
                            os.path.join(sym_base, 'avd'),
                            target_is_directory=True):
                w_sym = MagicMock()
                w_sym.environ.return_value = {
                    'ANDROID_EMULATOR_DISCOVERY_DIR': sym_base
                }
                with patch.object(erm, 'host_discovery_dirs', return_value=[]):
                    self.assertEqual(
                        erm._read_emulator_discovery_ini(pid, w_sym), {})
                    if os.name == 'posix' and hasattr(os, 'getuid'):
                        with patch.object(erm, '_current_uid', return_value=0):
                            self.assertEqual(
                                erm._read_emulator_discovery_ini(pid, w_sym),
                                {})

    def test_read_emulator_discovery_ini_drops_only_token_keys(self):
        import emulator_resource_monitor as erm

        pid = 5_009_322
        with tempfile.TemporaryDirectory() as tmpdir:
            running = os.path.join(tmpdir, 'avd', 'running')
            os.makedirs(running)
            _touch(
                os.path.join(running, f'pid_{pid}.ini'),
                '# written by the emulator = yes\n'
                'avd.name=Token_Phone\n'
                'avd.id = token_phone \n'
                'avd.dir=/home/u/.android/avd/token_phone.avd\n'
                'grpc.token=secret\n'
                'GRPC.Token.Path=/tmp/secret\n'
                'port.serial=5554\n')
            w = MagicMock()
            w.environ.return_value = {'ANDROID_EMULATOR_DISCOVERY_DIR': tmpdir}
            with patch.object(erm, 'host_discovery_dirs', return_value=[]):
                data = erm._read_emulator_discovery_ini(pid, w)
        # As in Rust parse_netsim_ini_str(): the word in a value is no secret.
        self.assertEqual(
            data, {
                'avd.name': 'Token_Phone',
                'avd.id': 'token_phone',
                'avd.dir': '/home/u/.android/avd/token_phone.avd',
                'port.serial': '5554',
            })

    def test_numbers_in_external_text_are_read_as_rust_does_and_never_raise(
            self):
        import emulator_resource_monitor as erm

        too_long = '9' * 5000

        def serial_port(value, known=None):
            return erm._merge_discovery_ini({'port.serial': value}, known, None,
                                            None, None)[0]

        self.assertEqual(serial_port('5554'), 5554)
        self.assertEqual(serial_port('+5554'), 5554)
        # A port the command line gave wins.
        self.assertEqual(serial_port('5556', known=5554), 5554)
        for bad in ('0', '65536', '\u00b2', '5554x', '', too_long):
            with self.subTest(port_serial=bad[:8]):
                self.assertIsNone(serial_port(bad))
        self.assertIsNone(
            erm._merge_discovery_ini({}, None, None, None, None)[0])

        # Emulator command line: a port is a u16, and a bad one costs the
        # other details nothing.
        details = erm._parse_cmdline_details
        self.assertEqual(
            details(['emulator', '-avd', 'P8', '-port', '5554'])[:2],
            (5554, 'P8'))
        self.assertEqual(details(['emulator', '-fishtank=5556'])[0], 5556)
        self.assertEqual(
            details(['qemu', '-device', 'x,host_port=5555'])[0], 5554)
        for bad in ('\u00b2', '65536', too_long):
            with self.subTest(cmdline_port=bad[:8]):
                self.assertEqual(
                    details(['emulator', '-avd', 'P8', '-port', bad])[:2],
                    (None, 'P8'))
                self.assertEqual(
                    details(['emulator', '-avd', 'P8', f'-ports={bad}'])[:2],
                    (None, 'P8'))
                self.assertEqual(
                    details([
                        'qemu', '-avd', 'P8', '-device',
                        f'x,serial_number={bad},host_port={bad}'
                    ])[:2], (None, 'P8'))

        # Capture rows: a u32 id, and u64 counters that default to 0.
        rows, err = erm.parse_netsim_capture_list(
            'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
            '1  | A | WIFI | off | 00:00:00 | 7 | 9\n'
            '+2 | B | WIFI | off | 00:00:00 | x | \u00b2\n'
            '\u00b2 | C | WIFI | off | 00:00:00 | 0 | 0\n'
            f'{too_long} | D | WIFI | off | 00:00:00 | 0 | 0\n'
            '4294967296 | E | WIFI | off | 00:00:00 | 0 | 0\n'
            f'3 | F | WIFI | off | 00:00:00 | {too_long} | 18446744073709551616\n'
        )
        self.assertIsNone(err)
        self.assertEqual(
            [(r['id'], r['device_name'], r['records'], r['size_bytes'])
             for r in rows], [(1, 'A', 7, 9), (2, 'B', 0, 0), (3, 'F', 0, 0)])

        # Session stats: a duration and a device id are read as Rust reads
        # them, and a value of the wrong JSON type counts as absent.
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = os.path.join(tmpdir, 'netsim_session_stats.json')

            def resolved(text):
                _write_ini(stats, text, 2000.0)
                return erm.resolve_session_stats_chips(stats, [1990.0], 1, None,
                                                       0.0)[1]

            i64_max = 2**63 - 1
            durations = [
                ('"7"', 7),
                ('7', 7),
                ('"7.9"', 7),
                ('-7.9', -7),
                ('"+.7e1"', 7),
                ('"7E0"', 7),
                # Not a number to Rust's f64 parser: no '_', no surrounding
                # space, and only ASCII digits and letters (U+0131 is a
                # dotless i).
                ('"1_0"', 0),
                ('" 7 "', 0),
                ('"\\u0667"', 0),
                ('"\\u0131nf"', 0),
                ('"x"', 0),
                ('""', 0),
                # Not a string or a number.
                ('true', 0),
                ('null', 0),
                ('[7]', 0),
                # Rust's `as i64`: NaN is 0, anything else is clamped.
                ('"nan"', 0),
                ('"inf"', i64_max),
                ('"-Infinity"', -i64_max - 1),
                ('"1e400"', i64_max),
                ('18446744073709551615', i64_max),
                # Rust's JSON parser rejects this file; Python's reads infinity.
                ('1e999', i64_max),
            ]
            for dur, secs in durations:
                with self.subTest(duration_secs=dur):
                    self.assertEqual(
                        resolved(
                            '{"device_stats": [{"device_id": 1}], '
                            '"radio_stats": [{"device_id": 1, "kind": "WIFI", '
                            f'"duration_secs": {dur}}}]}}'),
                        {'WIFI': (2000.0 - secs, True)})
            # A radio is skipped when it names a device other than the first
            # one. Ids match as JSON values: 1, 1.0, true and "1" all differ.
            ids = [
                ('{"device_id": 1}', '"device_id": 1, ', True),
                ('{"device_id": 1}', '"device_id": null, ', True),
                ('{"device_id": 1}', '', True),
                ('{}', '"device_id": 2, ', True),
                ('{"device_id": null}', '"device_id": null, ', True),
                ('{"device_id": 1}', '"device_id": 2, ', False),
                ('{"device_id": 1}', '"device_id": 1.0, ', False),
                ('{"device_id": 1}', '"device_id": true, ', False),
                ('{"device_id": 1}', '"device_id": "1", ', False),
                ('{"device_id": null}', '"device_id": 1, ', False),
            ]
            for device, radio_id, counted in ids:
                with self.subTest(device=device, radio_id=radio_id):
                    self.assertEqual(
                        resolved(f'{{"device_stats": [{device}], '
                                 f'"radio_stats": [{{{radio_id}"kind": "WIFI", '
                                 '"duration_secs": "5"}]}'),
                        {'WIFI': (1995.0, True)} if counted else None)
            for odd in (
                    '{"device_stats": 5, "radio_stats": 5}',
                    '{"device_stats": [5], "radio_stats": 5}',
                    '{"device_stats": {"a": 1}, "radio_stats": []}',
                    '{"device_stats": [{}], "radio_stats": '
                    '[{"kind": 5}, {"kind": ["WIFI"]}, 7]}',
                    # Nested too deeply for the JSON decoder.
                    '[' * 200000 + ']' * 200000):
                with self.subTest(stats=odd[:40]):
                    self.assertIsNone(resolved(odd))

    def test_a_nul_byte_in_a_logged_path_is_refused_not_raised(self):
        import emulator_resource_monitor as erm

        tailer = erm.NetsimLogTailer('')
        tailer.feed_line(
            'netsim I 09-25 22:37:05.100 x.rs:1 - INI file path: /tmp/a\x00b.ini',
            now_utc=float(_START))
        tailer.feed_line(
            'netsim I 09-25 22:37:05.200 x.rs:2 - netsim artifacts path: '
            '"/tmp/a\x00b"',
            now_utc=float(_START))
        self.assertEqual(tailer.ini_path_logged, '/tmp/a\x00b.ini')
        self.assertEqual(tailer.artifact_dir_logged, '/tmp/a\x00b')
        # os.lstat() raises ValueError, not OSError, for such a path.
        self.assertFalse(
            erm._can_adopt_logged_ini_path(tailer.ini_path_logged, True))
        self.assertFalse(erm._is_real_dir(tailer.artifact_dir_logged))
        self.assertFalse(erm._has_trusted_ancestors(tailer.artifact_dir_logged))

    def test_ini_pid_beyond_the_os_pid_range_is_stale_not_an_error(self):
        import emulator_resource_monitor as erm

        # An INI may name any pid up to u32::MAX, as in Rust, while
        # psutil.pid_exists() raises OverflowError above INT_MAX.
        for pid in (2**31, 2**32 - 1):
            self.assertFalse(erm._is_live_non_zombie_pid(pid), pid)
        with tempfile.TemporaryDirectory() as tmpdir:
            ini = os.path.join(tmpdir, 'netsim_7.ini')
            _write_ini(ini, 'grpc.port=41935\npid=4294967295\n', float(_START))
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                for tick in (1.0, 2.0):
                    tracker.update({}, {},
                                   now_wall=_START + tick,
                                   now_mono=tick)
            self.assertIn(ini, tracker.stale_inis)

    def test_a_failing_netsim_update_does_not_end_the_session(self):
        import emulator_resource_monitor as erm

        def failing_update(_tracker, *_args, **_kwargs):
            raise OverflowError('signed integer is greater than maximum')

        host = [
            _host_tuple(
                {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
                qemu_stats={'emulator-5554': ('20.0%', '1000.0MB (50 cs/s)')})
        ] * 4
        with patch.object(erm.NetsimTracker, 'update', failing_update):
            summary_data, _ = _run_main_sync(host, ['Pixel 8'] * 4)
        # Tick 0 primes the baselines; every later tick was still sampled.
        emu = summary_data['emulators']['Pixel_8_emulator-5554']
        self.assertEqual(emu['cpu_iterations'], 3)
        self.assertEqual(emu['qemu_cpu_iterations'], 3)
        self.assertEqual(summary_data['host_cpu_iterations'], 3)
        self.assertEqual(len(summary_data['time_series']), 3)
        # The failure is reported once, by type and place: the message could
        # quote another process's file.
        errors = [
            line for line in summary_data['netsim_summary_lines']
            if 'TRACKING_ERROR' in line
        ]
        self.assertEqual(len(errors), 1, errors)
        self.assertRegex(
            errors[0],
            r'TRACKING_ERROR\(OverflowError in failing_update:\d+\)$')

    def test_a_failing_fast_socket_poll_does_not_end_the_session(self):
        import emulator_resource_monitor as erm

        def failing_poll(_tracker, now_wall=None):
            raise OSError('socket table unavailable')

        # While an emulator is connecting, the wait between two ticks polls
        # the sockets every 0.25 s. Only time.sleep() advances this clock, and
        # the run stops at the end of the third tick's wait.
        ticks = 3
        clock = [100.0]

        def sleep_on_clock(seconds):
            clock[0] += seconds
            if clock[0] >= 100.0 + ticks * 2.0:
                raise KeyboardInterrupt

        host = [
            _host_tuple(
                {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
                qemu_stats={'emulator-5554': ('20.0%', '1000.0MB (50 cs/s)')})
        ]
        with patch.object(erm.NetsimTracker, 'update',
                          lambda *_args, **_kwargs: None), \
                patch.object(erm.NetsimTracker, 'has_any_connecting',
                             lambda _tracker: True), \
                patch.object(erm.NetsimTracker, 'poll_sockets_fast',
                             failing_poll), \
                patch('time.monotonic', side_effect=lambda: clock[0]):
            summary_data, _ = _run_main_sync(host, ['Pixel 8'],
                                             argv=['--interval', '2'],
                                             sleep=sleep_on_clock)
        # Tick 0 primes the baselines; the later ticks were still sampled.
        emu = summary_data['emulators']['Pixel_8_emulator-5554']
        self.assertEqual(emu['cpu_iterations'], ticks - 1)
        self.assertEqual(len(summary_data['time_series']), ticks - 1)
        errors = [
            line for line in summary_data['netsim_summary_lines']
            if 'TRACKING_ERROR' in line
        ]
        self.assertEqual(len(errors), 1, errors)
        self.assertRegex(errors[0],
                         r'TRACKING_ERROR\(OSError in failing_poll:\d+\)$')

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX ownership')
    def test_ini_observation_describes_the_file_that_was_read(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            ini = os.path.join(tmpdir, 'netsim.ini')
            _write_ini(ini, f'grpc.port=43929\npid={_DAEMON_A}\n', 1000.0)
            lst = os.lstat(ini)
            foreign_uid = os.getuid() + 1
            real_fstat = os.fstat

            # The open file is another user's and newer than what lstat saw,
            # as when the path is replaced between the two calls.
            def fstat_of_replaced_ini(fd):
                st = real_fstat(fd)
                if (st.st_dev, st.st_ino) != (lst.st_dev, lst.st_ino):
                    return st
                vals = list(st)
                vals[4], vals[8] = foreign_uid, 2000
                return os.stat_result(vals)

            with patch('os.fstat', side_effect=fstat_of_replaced_ini):
                obs = erm._read_ini_observation(ini, {})
            self.assertEqual((obs['pid'], obs['port']), (_DAEMON_A, 43929))
            self.assertEqual(obs['file_uid'], foreign_uid)
            self.assertIs(obs['owned_by_self'], False)
            self.assertEqual(obs['mtime'], 2000.0)

            # A file that cannot be opened gives no keys; lstat still says
            # whose it is and how old.
            real_open = os.open

            def open_denied(path, flags, *args, **kwargs):
                if path == ini:
                    raise PermissionError(13, 'Permission denied', path)
                return real_open(path, flags, *args, **kwargs)

            with patch('os.open', side_effect=open_denied):
                obs = erm._read_ini_observation(ini, {})
            self.assertEqual(obs['data'], {})
            self.assertEqual((obs['pid'], obs['port']), (None, None))
            self.assertEqual(obs['file_uid'], os.getuid())
            self.assertIs(obs['owned_by_self'], True)
            self.assertEqual(obs['mtime'], 1000.0)

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX permissions')
    def test_invoke_cli_vets_an_adopted_sibling_before_every_run(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            sdk = os.path.join(tmpdir, 'sdk')
            os.makedirs(sdk)
            os.chmod(sdk, 0o755)
            cli = os.path.join(sdk, 'netsim')
            with open(cli, 'wb') as f:
                f.write(b'#!/bin/sh\n')
            os.chmod(cli, 0o755)
            mgr = erm.NetsimPcapManager(pcap_dir='',
                                        max_mb=1,
                                        out_dir=os.path.join(tmpdir, 'out'))
            runs = []
            mgr._run_netsim_cli = lambda b, _p, _a, **_kw: (runs.append(b) or
                                                            'ran')
            args = ['capture', 'list']
            self.assertEqual(mgr._invoke_cli(cli, 43929, args), 'ran')
            # Once others can write to its directory, the sibling is no
            # longer run.
            os.chmod(sdk, 0o777)
            self.assertIsNone(mgr._invoke_cli(cli, 43929, args))
            os.chmod(sdk, 0o755)
            self.assertEqual(mgr._invoke_cli(cli, 43929, args), 'ran')
            os.remove(cli)
            self.assertIsNone(mgr._invoke_cli(cli, 43929, args))
            self.assertEqual(runs, [cli, cli])
            # A bare name is found through the monitor's own PATH and is not
            # vetted.
            self.assertEqual(mgr._invoke_cli('netsim', 43929, args), 'ran')
            self.assertEqual(runs, [cli, cli, 'netsim'])

    def test_match_inis_uid_compatibility_and_trusted_ini_path_symlinks(self):
        import emulator_resource_monitor as erm

        my_uid = erm._current_uid()
        foreign_uid = (my_uid or 1000) + 1
        with tempfile.TemporaryDirectory() as tmpdir:
            spoof_ini = os.path.join(tmpdir, 'spoof_netsim.ini')
            own_ini = os.path.join(tmpdir, 'own_netsim.ini')
            _touch(spoof_ini, 'grpc.port=49999\n')
            _touch(own_ini, 'grpc.port=43929\n')

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [tmpdir]
            k_self = (5_000_100, 1000.0)
            tracker.daemons[k_self] = {
                'start_wall': 1000.0,
                'create_time': 1000.0,
                'same_user': True,
                'instance_num': 1,
                'ini_path': None,
            }
            obs_spoof = {
                'path': spoof_ini,
                'data': {},
                'holders': [],
                'pid': 5_000_100,
                'port': 49999,
                'instance': 1,
                'mtime': 1001.0,
                'owned_by_self': False,
            }
            obs_own = {
                'path': own_ini,
                'data': {},
                'holders': [],
                'pid': 5_000_100,
                'port': 43929,
                'instance': 1,
                'mtime': 1001.0,
                'owned_by_self': True,
            }
            with patch.object(erm, '_current_uid', return_value=1000):
                self.assertEqual(
                    tracker._match_inis_to_daemons([obs_spoof, obs_own],
                                                   [k_self]), [None, k_self])
                tracker.daemons[k_self]['ini_path'] = spoof_ini
                obs_logged_foreign = dict(obs_spoof, pid=None)
                self.assertEqual(
                    tracker._match_inis_to_daemons([obs_logged_foreign],
                                                   [k_self]), [None])
            with patch.object(erm, '_current_uid', return_value=0):
                self.assertEqual(
                    tracker._match_inis_to_daemons([obs_logged_foreign],
                                                   [k_self]), [k_self])

            if os.name != 'posix' or not hasattr(os, 'getuid'):
                self.skipTest('the rest of this test needs POSIX ownership')

            real_dir = os.path.join(tmpdir, 'real_dir')
            os.makedirs(real_dir, exist_ok=True)
            os.chmod(real_dir, 0o1777)
            real_ini = os.path.join(real_dir, 'netsim.ini')
            _touch(real_ini, 'grpc.port=43929\n')
            # Missing INI in a 0o1777 sticky world-writable directory must be
            # rejected even when owned by the monitor user or root (`sudo` /tmp).
            self.assertFalse(
                erm._is_trusted_ini_path(os.path.join(real_dir, 'missing.ini')))
            self.assertFalse(
                erm._is_trusted_ini_path(os.path.join(real_dir, 'missing.ini'),
                                         root_uid=my_uid))
            with patch.object(erm, '_current_uid', return_value=0):
                self.assertFalse(
                    erm._is_trusted_ini_path(
                        '/tmp/netsim_erm_missing_sudo.ini'))
            sym_dir = os.path.join(tmpdir, 'sym_dir')
            if _try_symlink(real_dir, sym_dir, target_is_directory=True):
                via_sym = os.path.join(sym_dir, 'netsim.ini')

                if my_uid is not None:
                    self.assertTrue(erm._is_trusted_ini_path(via_sym))
                    with patch.object(erm,
                                      '_current_uid',
                                      return_value=foreign_uid):
                        self.assertFalse(erm._is_trusted_ini_path(via_sym))
                        self.assertFalse(
                            erm._is_trusted_ini_path(via_sym, root_uid=my_uid))
                        self.assertFalse(
                            erm._can_adopt_logged_ini_path(via_sym,
                                                           False,
                                                           expected_uid=None))
                        self.assertFalse(
                            erm._can_adopt_logged_ini_path(os.path.join(
                                real_dir, '..', 'real_dir', 'netsim.ini'),
                                                           False,
                                                           expected_uid=None))
                    self.assertTrue(
                        erm._is_trusted_ini_path(via_sym, root_uid=my_uid))

    def test_same_user_art_dir_log_and_stats_uid_ownership(self):
        import emulator_resource_monitor as erm

        my_uid = erm._current_uid()
        foreign_uid = (my_uid or 1000) + 1
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_art = os.path.join(tmpdir, 'android-user', 'netsimd')
            self.assertFalse(
                erm.NetsimTracker._is_disallowed_daemon_art_dir(
                    pending_art, True))

            os.makedirs(os.path.join(tmpdir, 'android-user'), exist_ok=True)
            if os.name == 'posix' and hasattr(os, 'getuid'):
                with patch.object(erm, '_current_uid',
                                  return_value=foreign_uid):
                    self.assertTrue(
                        erm.NetsimTracker._is_disallowed_daemon_art_dir(
                            pending_art, True))

            os.makedirs(pending_art, exist_ok=True)
            if os.name == 'posix' and hasattr(os, 'getuid'):
                with patch.object(erm, '_current_uid',
                                  return_value=foreign_uid):
                    self.assertTrue(
                        erm.NetsimTracker._is_disallowed_daemon_art_dir(
                            pending_art, True))
                with patch.object(erm, '_current_uid', return_value=0):
                    self.assertFalse(
                        erm.NetsimTracker._is_disallowed_daemon_art_dir(
                            pending_art, True))

            log_path = os.path.join(pending_art, 'netsim_stderr.log')
            _touch(
                log_path,
                'netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 43929\n'
            )
            tailer = erm.NetsimLogTailer(log_path, require_self_owner=True)
            if os.name == 'posix' and hasattr(os, 'getuid'):
                with patch.object(erm, '_current_uid',
                                  return_value=foreign_uid):
                    self.assertFalse(tailer.poll(now_utc=100.0))
                    self.assertIsNone(tailer.grpc_port_logged)
            self.assertTrue(tailer.poll(now_utc=100.0))
            self.assertEqual(tailer.grpc_port_logged, 43929)

            stats_path = os.path.join(pending_art, 'netsim_session_stats.json')
            _touch(
                stats_path,
                '{"version":"1.0.31","device_stats":[{"device_id":1}],'
                '"radio_stats":[{"device_id":1,"kind":"WIFI","duration_secs":"5"}]}'
            )
            cnt, res, cache = erm.resolve_session_stats_chips(
                stats_path, [],
                joined_emulator_count=1,
                cached_state=None,
                not_before=0.0,
                require_self_owner=True)
            self.assertEqual(cnt, 1)
            self.assertIsNotNone(res)
            if os.name != 'posix' or not hasattr(os, 'getuid'):
                self.skipTest('the rest of this test needs POSIX ownership')
            with patch.object(erm, '_current_uid', return_value=foreign_uid):
                cnt_f, res_f, _ = erm.resolve_session_stats_chips(
                    stats_path, [],
                    joined_emulator_count=1,
                    cached_state=cache,
                    not_before=0.0,
                    require_self_owner=True)
            self.assertEqual((cnt_f, res_f), (0, None))

            # Logged `artifact_dir` inside `_poll_daemon_log` must also reject
            # foreign-owned directories when `same_user` is True.
            own_tmp = os.path.join(tmpdir, 'own_tmp')
            own_art = os.path.join(own_tmp, 'android-self', 'netsimd')
            os.makedirs(own_art, exist_ok=True)
            foreign_art = os.path.join(tmpdir, 'foreign_art')
            os.makedirs(foreign_art, exist_ok=True)
            _touch(
                os.path.join(own_art, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.100 s.rs:1 - Artifacts: "{foreign_art}"\n'
            )
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [own_tmp]
            orig_lstat = os.lstat

            def lstat_foreign_art(p, *args, **kwargs):
                st = orig_lstat(p, *args, **kwargs)
                if p == foreign_art:
                    vals = list(st)
                    vals[4] = foreign_uid
                    return os.stat_result(vals)
                return st

            d_proc = {
                'pid': 5_008_901,
                'create_time': 1000.0,
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': own_tmp,
                    'USER': 'self'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch('os.lstat', side_effect=lstat_foreign_art), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_proc], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update({}, {}, now_wall=1005.0, now_mono=5.0)
            self.assertEqual(
                tracker.daemons[(5_008_901, 1000.0)]['artifact_dir'], own_art)

    def test_netsim_tracker_windows_socket_os_family_discovery_and_artifacts(
            self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            local_app = os.path.join(tmpdir, 'LocalAppData')
            win_disc = os.path.join(local_app, 'Temp')
            win_temp = os.path.join(tmpdir, 'WinTemp')
            win_art = os.path.join(win_temp, 'netsimd')
            os.makedirs(win_disc, exist_ok=True)
            os.makedirs(win_art, exist_ok=True)

            ini_path = os.path.join(win_disc, 'netsim.ini')
            _write_ini(ini_path,
                       'grpc.port=43929\npid=5009901\n',
                       mtime=float(_START + 1))
            _touch(
                os.path.join(win_art, 'netsim_stderr.log'),
                'netsim I 09-25 22:37:05.100 s.rs:1 - Netsim Version: 1.0.31\n'
                'netsim I 09-25 22:37:05.200 s.rs:2 - gRPC port: 43929\n'
                f'netsim I 09-25 22:37:05.250 s.rs:3 - Wrote to INI file {ini_path}\n'
                'netsim I 09-25 22:37:05.400 s.rs:4 - '
                'DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n')

            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir)
            tracker.socket_os_family = 'win32'
            tracker.socket_cli_runner = lambda cmd, **_kw: (
                '  TCP    127.0.0.1:51001    127.0.0.1:43929    ESTABLISHED    4001\r\n'
                if cmd[0] == 'netstat' else '')
            d_win = {
                'pid': 5_009_901,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': [r'C:\Android\Sdk\emulator\netsimd.exe'],
                'environ': {
                    'LOCALAPPDATA': local_app,
                    'TEMP': win_temp,
                },
                'exe': r'C:\Android\Sdk\emulator\netsimd.exe',
            }
            emu_g = {
                'primary_pid': 4001,
                'primary_ctime': float(_START),
                'avd_id': 'Phone',
                'display_name': 'Phone',
                'avd_name': 'Phone',
                'grpc_endpoint': '127.0.0.1:43929',
                'no_netsim': False,
                'pids': {4001},
                'member_start_times': {
                    4001: float(_START)
                },
            }
            summary_emus = {
                'Phone_emulator-5554': {
                    'current_serial': 'emulator-5554',
                    'current_pid': 4001,
                    'name': 'Phone',
                    'first_seen': 1.0,
                }
            }
            with patch.object(erm, 'host_discovery_dirs', return_value=[]):
                self.assertIn(ini_path,
                              tracker._discover_ini_files([d_win['environ']]))
            tracker.custom_ini_dirs = [win_disc]
            with patch.object(erm, '_snapshot_process_state',
                              return_value=([d_win],
                                            {'emulator-5554': {4001}},
                                            {'emulator-5554': emu_g}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}):
                tracker.update(summary_emus, {'emulator-5554': 'device'},
                               now_wall=float(_START + 5),
                               now_mono=5.0)
            d_rec = tracker.daemons[(5_009_901, float(_START))]
            self.assertEqual(d_rec['artifact_dir'], win_art)
            self.assertEqual(d_rec['netsim_cli_bin'], 'netsim.exe')
            self.assertEqual(d_rec['grpc_port'], 43929)
            self.assertEqual(d_rec['version'], '1.0.31')
            self.assertEqual(
                tracker.emulator_records['Phone_emulator-5554']['state'],
                'CONNECTED')

    def test_current_uid_is_the_real_uid_not_the_effective_uid(self):
        import emulator_resource_monitor as erm

        with patch('os.getuid', return_value=1000, create=True), \
                patch('os.geteuid', return_value=0, create=True):
            self.assertEqual(erm._current_uid(), 1000)

        with patch('os.getuid', return_value=0, create=True), \
                patch('os.geteuid', return_value=501, create=True):
            self.assertEqual(erm._current_uid(), 0)
            seen_cmds = []

            def fake_subproc_run(cmd, *_a, **_kw):
                seen_cmds.append(list(cmd))
                return MagicMock(stdout='ok')

            with patch.object(erm, 'run_cmd', _REAL_RUN_CMD), \
                    patch('subprocess.run', side_effect=fake_subproc_run), \
                    patch.object(erm, 'ADB_PATH', '/sdk/adb'), \
                    patch.object(erm, 'sudo_user', 'alice'):
                erm.run_cmd(['adb', 'devices'])
            self.assertEqual(seen_cmds, [['/sdk/adb', 'devices']])

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX ownership')
    def test_has_trusted_ancestors_rejects_third_party_intermediate_real_dirs(
            self):
        import stat
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            real_uid = os.stat(tmpdir).st_uid or 1000
            attacker_uid = real_uid + 77
            attacker_sub = os.path.join(tmpdir, 'attacker_sub')
            owned_dir = os.path.join(attacker_sub, 'owned_dir')
            owned_netsimd = os.path.join(owned_dir, 'netsimd')
            avd_running = os.path.join(owned_dir, 'avd', 'running')
            os.makedirs(owned_netsimd, exist_ok=True)
            os.makedirs(avd_running, exist_ok=True)
            ini_in_owned = os.path.join(owned_dir, 'netsim.ini')
            _touch(ini_in_owned, 'grpc.port=43929\n')
            _touch(os.path.join(avd_running, 'pid_7777.ini'),
                   'port.serial=5554\navd.id=Pixel_9\n')

            user_group_dir = os.path.join(tmpdir, 'primarygroup_775')
            os.makedirs(user_group_dir, exist_ok=True)
            user_world_dir = os.path.join(tmpdir, 'world_777')
            os.makedirs(user_world_dir, exist_ok=True)
            root_group_dir = os.path.join(tmpdir, 'root_775')
            os.makedirs(root_group_dir, exist_ok=True)

            orig_lstat = os.lstat
            orig_stat = os.stat

            def make_st(base_st, mode=None, uid=None):
                vals = list(base_st)
                if mode is not None:
                    vals[0] = mode
                if uid is not None:
                    vals[4] = uid
                return os.stat_result(vals)

            canon_attacker = os.path.realpath(attacker_sub)
            canon_user_group = os.path.realpath(user_group_dir)
            canon_user_world = os.path.realpath(user_world_dir)
            canon_root_group = os.path.realpath(root_group_dir)

            def fake_lstat(p, *args, **kwargs):
                st = orig_lstat(p, *args, **kwargs)
                if p in (attacker_sub, canon_attacker):
                    return make_st(st, stat.S_IFDIR | 0o755, attacker_uid)
                if p in (user_group_dir, canon_user_group):
                    return make_st(st, stat.S_IFDIR | 0o775, real_uid)
                if p in (user_world_dir, canon_user_world):
                    return make_st(st, stat.S_IFDIR | 0o777, real_uid)
                if p in (root_group_dir, canon_root_group):
                    return make_st(st, stat.S_IFDIR | 0o775, 0)
                return st

            def fake_stat(p, *args, **kwargs):
                st = orig_stat(p, *args, **kwargs)
                if p in (attacker_sub, canon_attacker):
                    return make_st(st, stat.S_IFDIR | 0o755, attacker_uid)
                if p in (user_group_dir, canon_user_group):
                    return make_st(st, stat.S_IFDIR | 0o775, real_uid)
                if p in (user_world_dir, canon_user_world):
                    return make_st(st, stat.S_IFDIR | 0o777, real_uid)
                if p in (root_group_dir, canon_root_group):
                    return make_st(st, stat.S_IFDIR | 0o775, 0)
                return st

            w_proc = MagicMock()
            w_proc.environ.return_value = {
                'ANDROID_EMULATOR_DISCOVERY_DIR': owned_dir
            }
            w_proc.uids.return_value = MagicMock(real=real_uid)

            with patch.object(erm, '_current_uid', return_value=real_uid), \
                    patch('os.lstat', side_effect=fake_lstat), \
                    patch('os.stat', side_effect=fake_stat), \
                    patch.object(erm, 'host_discovery_dirs', return_value=[]):
                self.assertFalse(
                    erm._has_trusted_ancestors(owned_dir,
                                               expected_uid=real_uid))
                self.assertFalse(
                    erm._is_trusted_owned_real_dir(owned_dir,
                                                   expected_uid=real_uid))
                self.assertFalse(erm._is_trusted_ini_path(ini_in_owned))
                self.assertEqual(erm._read_emulator_discovery_ini(7777, w_proc),
                                 {})
                self.assertTrue(
                    erm.NetsimTracker._is_disallowed_daemon_art_dir(
                        owned_netsimd, True, expected_uid=real_uid))
                self.assertTrue(
                    erm.NetsimTracker._is_disallowed_daemon_art_dir(
                        os.path.join(attacker_sub, 'android-u', 'netsimd'),
                        True,
                        expected_uid=real_uid))
                # gLinux primarygroup 0o775 owned dir is trusted; 0o777 non-sticky
                # user dir and 0o775 non-sticky root dir are rejected.
                self.assertTrue(
                    erm._is_trusted_owned_real_dir(user_group_dir,
                                                   expected_uid=real_uid))
                self.assertFalse(
                    erm._is_trusted_owned_real_dir(user_world_dir,
                                                   expected_uid=real_uid))
                self.assertFalse(
                    erm._has_trusted_ancestors(root_group_dir,
                                               expected_uid=real_uid))

                owned_sym = os.path.join(tmpdir, 'owned_sym_to_attacker')
                if _try_symlink(owned_dir, owned_sym, target_is_directory=True):
                    self.assertFalse(
                        erm._has_trusted_ancestors(owned_sym,
                                                   expected_uid=real_uid))
                    self.assertFalse(
                        erm._is_trusted_ini_path(
                            os.path.join(owned_sym, 'netsim.ini')))

                untrusted_parent = user_world_dir
                root_sym = os.path.join(untrusted_parent, 'root_sym')
                if _try_symlink(user_group_dir,
                                root_sym,
                                target_is_directory=True):
                    self.assertFalse(
                        erm._has_trusted_ancestors(root_sym,
                                                   expected_uid=real_uid,
                                                   root_uid=real_uid))
                    self.assertFalse(
                        erm._has_trusted_ancestors(os.path.join(root_sym, '..'),
                                                   expected_uid=real_uid))
                    self.assertTrue(
                        erm.NetsimTracker._is_disallowed_daemon_art_dir(
                            os.path.join(root_sym, '..', 'netsimd'),
                            True,
                            expected_uid=real_uid))

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX ownership')
    def test_target_process_expected_uid_under_sudo_and_pcap_art_dir(self):
        import stat
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            dev_uid = os.stat(tmpdir).st_uid or 1000
            attacker_uid = dev_uid + 88

            dev_base = os.path.join(tmpdir, 'dev_base')
            dev_running = os.path.join(dev_base, 'avd', 'running')
            os.makedirs(dev_running, exist_ok=True)
            dev_pid_ini = os.path.join(dev_running, 'pid_8100.ini')
            _touch(dev_pid_ini, 'port.serial=5554\navd.id=DevAvd\n')

            spoof_base = os.path.join(tmpdir, 'spoof_base')
            spoof_running = os.path.join(spoof_base, 'avd', 'running')
            os.makedirs(spoof_running, exist_ok=True)
            spoof_pid_ini = os.path.join(spoof_running, 'pid_8100.ini')
            _touch(spoof_pid_ini, 'port.serial=5554\navd.id=SpoofAvd\n')

            dev_art = os.path.join(tmpdir, 'android-dev', 'netsimd')
            os.makedirs(dev_art, exist_ok=True)
            dev_log = os.path.join(dev_art, 'netsim_stderr.log')
            _touch(
                dev_log,
                'netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 43929\n'
            )
            dev_stats = os.path.join(dev_art, 'netsim_session_stats.json')
            _touch(
                dev_stats,
                '{"version":"1.0.31","device_stats":[{"device_id":1}],'
                '"radio_stats":[{"device_id":1,"kind":"WIFI","duration_secs":"4"}]}'
            )

            orig_lstat = os.lstat
            orig_fstat = os.fstat
            spoof_inodes = {
                orig_lstat(spoof_pid_ini).st_ino,
            }
            attacker_mode = [False]

            def sudo_lstat(p, *args, **kwargs):
                st = orig_lstat(p, *args, **kwargs)
                if p == spoof_pid_ini or (attacker_mode[0] and
                                          p in (dev_log, dev_stats)):
                    vals = list(st)
                    vals[4] = attacker_uid
                    return os.stat_result(vals)
                return st

            def sudo_fstat(fd):
                st = orig_fstat(fd)
                if st.st_ino in spoof_inodes:
                    vals = list(st)
                    vals[4] = attacker_uid
                    return os.stat_result(vals)
                return st

            w_dev = MagicMock()
            w_dev.uids.return_value = MagicMock(real=dev_uid)
            w_dev.environ.return_value = {
                'ANDROID_EMULATOR_DISCOVERY_DIR': dev_base
            }
            w_spoof = MagicMock()
            w_spoof.uids.return_value = MagicMock(real=dev_uid)
            w_spoof.environ.return_value = {
                'ANDROID_EMULATOR_DISCOVERY_DIR': spoof_base
            }

            with patch.object(erm, '_current_uid', return_value=0), \
                    patch('os.lstat', side_effect=sudo_lstat), \
                    patch('os.fstat', side_effect=sudo_fstat), \
                    patch.object(erm, 'host_discovery_dirs', return_value=[]):
                self.assertEqual(
                    erm._read_emulator_discovery_ini(8100, w_dev).get('avd.id'),
                    'DevAvd')
                self.assertEqual(
                    erm._read_emulator_discovery_ini(8100, w_spoof), {})

                pending_dev_art = os.path.join(tmpdir, 'android-pending',
                                               'netsimd')
                self.assertFalse(
                    erm.NetsimTracker._is_disallowed_daemon_art_dir(
                        pending_dev_art, False, expected_uid=dev_uid))
                self.assertFalse(
                    erm.NetsimTracker._is_disallowed_daemon_art_dir(
                        dev_art, False, expected_uid=dev_uid))

                tailer = erm.NetsimLogTailer(dev_log,
                                             require_self_owner=False,
                                             expected_uid=dev_uid)
                self.assertTrue(tailer.poll(now_utc=100.0))
                self.assertEqual(tailer.grpc_port_logged, 43929)

                cnt, res, cached = erm.resolve_session_stats_chips(
                    dev_stats, [],
                    joined_emulator_count=1,
                    cached_state=None,
                    not_before=0.0,
                    expected_uid=dev_uid)
                self.assertEqual(cnt, 1)
                self.assertIsNotNone(res)

                # Third-party UID spoofing under sudo is rejected even before
                # mtime-cache fast path or frozen tailer fast path.
                spoof_inodes.update(
                    {orig_lstat(dev_log).st_ino,
                     orig_lstat(dev_stats).st_ino})
                attacker_mode[0] = True
                tailer.frozen = True
                self.assertFalse(tailer.poll(now_utc=101.0))
                cnt_bad, res_bad, _ = erm.resolve_session_stats_chips(
                    dev_stats, [],
                    joined_emulator_count=1,
                    cached_state=cached,
                    not_before=0.0,
                    expected_uid=dev_uid)
                self.assertEqual((cnt_bad, res_bad), (0, None))

                # _match_inis_to_daemons enforces file_uid == expected_uid even
                # under sudo (_current_uid() == 0).
                sudo_tracker = erm.NetsimTracker(pcap_dir=None,
                                                 enable_log=False,
                                                 out_dir=tmpdir)
                sudo_tracker.custom_ini_dirs = [tmpdir]
                k_dev = (8200, 1000.0)
                sudo_tracker.daemons[k_dev] = {
                    'uid': dev_uid,
                    'start_wall': 1000.0,
                    'create_time': 1000.0,
                    'same_user': False,
                    'instance_num': 1,
                    'ini_path': None,
                }
                obs_sudo_spoof = {
                    'path': os.path.join(tmpdir, 'spoof_netsim.ini'),
                    'data': {},
                    'holders': [],
                    'pid': 8200,
                    'port': 49999,
                    'instance': 1,
                    'mtime': 1001.0,
                    'file_uid': attacker_uid,
                    'owned_by_self': False,
                }
                obs_sudo_dev = dict(obs_sudo_spoof,
                                    path=os.path.join(tmpdir, 'dev_netsim.ini'),
                                    port=43929,
                                    file_uid=dev_uid)
                self.assertEqual(
                    sudo_tracker._match_inis_to_daemons(
                        [obs_sudo_spoof, obs_sudo_dev], [k_dev]), [None, k_dev])

            # NetsimPcapManager art_dir_trusted rejects owned leaf art_dir under
            # an attacker-owned intermediate directory.
            attacker_parent = os.path.join(tmpdir, 'attacker_parent')
            pcap_art = os.path.join(attacker_parent, 'android-u', 'netsimd')
            os.makedirs(os.path.join(pcap_art, 'pcaps'), exist_ok=True)
            _touch(os.path.join(pcap_art, 'pcaps', 'netsim-1-P-WIFI.pcap'),
                   'pcap')
            ini_ok = os.path.join(tmpdir, 'netsim.ini')
            _touch(ini_ok, 'grpc.port=43929\npid=9100\n')
            mgr = erm.NetsimPcapManager(pcap_dir='',
                                        max_mb=1,
                                        out_dir=os.path.join(tmpdir, 'out'))
            cli_invocations = []
            mgr._run_netsim_cli = lambda *a, **kw: cli_invocations.append(a
                                                                         ) or ''
            canon_att_parent = os.path.realpath(attacker_parent)

            def lstat_att_parent(p, *args, **kwargs):
                st = orig_lstat(p, *args, **kwargs)
                if p in (attacker_parent, canon_att_parent):
                    vals = list(st)
                    vals[0] = stat.S_IFDIR | 0o755
                    vals[4] = attacker_uid
                    return os.stat_result(vals)
                return st

            with patch.object(erm, '_current_uid', return_value=dev_uid), \
                    patch('os.lstat', side_effect=lstat_att_parent):
                mgr.poll_and_manage_daemon(
                    {
                        'daemon_key': (9100, 1000.0),
                        'pid': 9100,
                        'uid': dev_uid,
                        'start_wall': 1000.0,
                        'grpc_port': 43929,
                        'ini_path': ini_ok,
                        'artifact_dir': pcap_art,
                        'same_user': True,
                        'exited': False,
                    }, [{
                        'serial': 'emulator-5554',
                        'device_name': 'P',
                        'state': 'CONNECTED',
                        'chip_ids': {'1'},
                    }],
                    now_mono=10.0)
            self.assertEqual(cli_invocations, [])
            self.assertEqual(mgr.copied_files, {})

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX modes and symlinks')
    def test_trusted_dir_resolution_checks_every_directory_it_enters(self):
        import emulator_resource_monitor as erm

        my_uid = os.getuid()
        with tempfile.TemporaryDirectory() as root:
            safe = os.path.join(root, 'safe')
            open_dir = os.path.join(root, 'open')
            leaf = os.path.join(open_dir, 'leaf')
            os.makedirs(safe)
            os.makedirs(leaf)
            _touch(os.path.join(safe, 'netsim.ini'), 'grpc.port=43929\n')
            # Both `in/out` and `via` end in `safe`, but get there through
            # `open/leaf`.
            os.symlink(os.path.join('..', '..', 'safe'),
                       os.path.join(leaf, 'out'))
            os.symlink(os.path.join('open', 'leaf'), os.path.join(root, 'in'))
            os.symlink(os.path.join('open', 'leaf', 'out'),
                       os.path.join(root, 'via'))
            os.symlink('loop', os.path.join(root, 'loop'))
            for d in (safe, leaf):
                os.chmod(d, 0o755)
            through_open = (os.path.join(root, 'in',
                                         'out'), os.path.join(root, 'via'))

            # World-writable without the sticky bit: anyone may replace `leaf`.
            os.chmod(open_dir, 0o777)
            for path in through_open:
                with self.subTest(path=path, open_mode='777'):
                    self.assertFalse(
                        erm._has_trusted_ancestors(path, expected_uid=my_uid))
                    self.assertFalse(
                        erm._is_trusted_ini_path(
                            os.path.join(path, 'netsim.ini')))
                    # With root_uid=my_uid the links count as root's and are
                    # followed, which leads into `open` again.
                    self.assertFalse(
                        erm._has_trusted_ancestors_unowned(path,
                                                           root_uid=my_uid))

            # With the sticky bit nobody else can replace `leaf`.
            os.chmod(open_dir, 0o1777)
            for path in through_open:
                with self.subTest(path=path, open_mode='1777'):
                    self.assertEqual(
                        erm._trusted_canonical_dir(path, expected_uid=my_uid),
                        os.path.realpath(safe))
                    self.assertTrue(
                        erm._is_trusted_ini_path(
                            os.path.join(path, 'netsim.ini')))
                    self.assertTrue(
                        erm._has_trusted_ancestors_unowned(path,
                                                           root_uid=my_uid))
                    # Without a known owner only root's links are followed.
                    if my_uid != 0:
                        self.assertFalse(
                            erm._has_trusted_ancestors_unowned(path))

            # A link loop ends the resolution instead of recursing forever.
            loop = os.path.join(root, 'loop')
            self.assertFalse(
                erm._has_trusted_ancestors(loop, expected_uid=my_uid))
            self.assertFalse(
                erm._has_trusted_ancestors_unowned(loop, root_uid=my_uid))
            self.assertFalse(
                erm._can_adopt_logged_ini_path(os.path.join(loop, 'netsim.ini'),
                                               False))

            # `..` is refused in the path itself, and a file is no directory.
            self.assertFalse(
                erm._has_trusted_ancestors(os.path.join(safe, '..', 'safe'),
                                           expected_uid=my_uid))
            self.assertFalse(
                erm._has_trusted_ancestors(os.path.join(safe, 'netsim.ini'),
                                           expected_uid=my_uid))

            # The directory read from may not be world-writable itself, sticky
            # or not; as an ancestor a sticky one is fine.
            self.assertFalse(
                erm._is_trusted_owned_real_dir(open_dir, expected_uid=my_uid))
            self.assertTrue(
                erm._is_trusted_owned_real_dir(leaf, expected_uid=my_uid))

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX uids')
    def test_daemon_with_unreadable_uid_is_not_the_monitors_own(self):
        import emulator_resource_monitor as erm

        def tracked(d_proc, monitor_uid, readable_env=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                env = {'TMPDIR': tmpdir, 'USER': 'u'} if readable_env else {}
                tracker = erm.NetsimTracker(pcap_dir=None,
                                            enable_log=True,
                                            out_dir=tmpdir,
                                            os_family='linux')
                tracker.custom_ini_dirs = [tmpdir]
                with patch.object(erm, '_current_uid', return_value=monitor_uid), \
                        patch.object(erm, '_snapshot_process_state',
                                     return_value=([dict(d_proc, environ=env)],
                                                   {}, {}, {})), \
                        patch.object(tracker, '_read_proc_locks', return_value={}), \
                        patch.object(tracker, '_collect_loopback_grpc_sockets',
                                     return_value={}):
                    tracker.update({}, {},
                                   now_wall=float(_START + 2),
                                   now_mono=2.0)
                return tracker.daemons[(_DAEMON_A, float(_START))]

        # psutil reports no uid for a process that is already gone, and its
        # environment cannot be read either.
        d_proc = {
            'pid': _DAEMON_A,
            'uid': None,
            'create_time': float(_START),
            'ppid': 1,
            'cmdline': ['netsimd'],
            'exe': '/opt/sdk/emulator/netsimd',
        }
        d = tracked(d_proc, monitor_uid=1000, readable_env=False)
        self.assertFalse(d['same_user'])
        # So it must not fall back to the monitor's own artifact dir and log.
        self.assertEqual(d['artifact_dir'], '')
        self.assertIsNone(d['tailer'])
        self.assertEqual(d['netsim_cli_bin'], 'netsim')

        # Windows has no uids: there every daemon is the monitor's own.
        self.assertTrue(tracked(d_proc, monitor_uid=None)['same_user'])
        # A known uid decides, whoever the monitor is.
        self.assertTrue(
            tracked(dict(d_proc, uid=1000), monitor_uid=1000)['same_user'])
        self.assertFalse(
            tracked(dict(d_proc, uid=1001), monitor_uid=1000)['same_user'])

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX modes')
    def test_world_writable_art_dir_is_disallowed_even_when_sticky(self):
        import emulator_resource_monitor as erm

        my_uid = os.getuid()

        def disallowed(path):
            return erm.NetsimTracker._is_disallowed_daemon_art_dir(
                path, True, expected_uid=my_uid)

        with tempfile.TemporaryDirectory() as tmpdir:
            parent = os.path.join(tmpdir, 'android-u')
            art = os.path.join(parent, 'netsimd')
            not_yet_created = os.path.join(parent, 'pending')
            os.makedirs(art)
            for mode in (0o1777, 0o1757, 0o777):
                with self.subTest(mode=oct(mode)):
                    os.chmod(parent, 0o755)
                    os.chmod(art, mode)
                    self.assertTrue(disallowed(art))
                    # The android-<user> parent counts too, whether or not
                    # the dir below it exists yet.
                    os.chmod(art, 0o755)
                    os.chmod(parent, mode)
                    self.assertTrue(disallowed(art))
                    self.assertTrue(disallowed(not_yet_created))
            os.chmod(parent, 0o755)
            self.assertFalse(disallowed(art))
            self.assertFalse(disallowed(not_yet_created))

    def test_netsim_instance_number_follows_netsim_get_instance(self):
        import emulator_resource_monitor as erm

        num = erm._extract_netsim_instance_num
        self.assertIsNone(num(['netsimd'], {}))
        self.assertIsNone(num(None, None))
        self.assertEqual(num(['netsimd', '-i', '3'], {}), 3)
        self.assertEqual(num(['netsimd', '-i', '+3'], {}), 3)
        self.assertEqual(num(['netsimd', '--instance', '4'], None), 4)
        self.assertEqual(num(['netsimd', '--instance=5'], {}), 5)
        # The environment wins over the flag.
        self.assertEqual(num(['netsimd', '-i', '3'], {'NETSIM_INSTANCE': '2'}),
                         2)
        self.assertEqual(num(['netsimd', '-i'], {'NETSIM_INSTANCE': '65535'}),
                         65535)
        # netsim skips a source that holds 0 or no u16. A run of digits too
        # long for int() is no number either, and must not raise.
        for bad in ('0', '65536', '-1', ' 2', 'x', '', '1_0', '\uff12',
                    '9' * 5000):
            with self.subTest(bad=bad[:8]):
                self.assertEqual(
                    num(['netsimd', '-i', '3'], {'NETSIM_INSTANCE': bad}), 3)
                self.assertIsNone(num(['netsimd', '-i', bad], {}))
                self.assertIsNone(num(['netsimd', f'--instance={bad}'], {}))

    def test_logged_ini_trust_gate_default_instance_1_and_exit_cleanup(self):
        import stat
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1) _run_netsim_cli pins NETSIM_INSTANCE='1' for both None and 1
            # even when NETSIM_INSTANCE=7 is exported in os.environ.
            ini_dir = os.path.join(tmpdir, 'cli_ini_dir')
            os.makedirs(ini_dir, exist_ok=True)
            ini_file = os.path.join(ini_dir, 'netsim.ini')
            _touch(ini_file, 'grpc.port=43929\npid=9100\n')
            mgr = erm.NetsimPcapManager(pcap_dir='',
                                        max_mb=1,
                                        out_dir=os.path.join(tmpdir, 'out'))
            seen_envs = []

            def capture_subproc(cmd, **kwargs):
                seen_envs.append((list(cmd), dict(kwargs.get('env') or {})))
                return MagicMock(stdout='OK', stderr='')

            with patch.dict(os.environ, {'NETSIM_INSTANCE': '7'}), \
                    patch.object(erm.subprocess, 'run',
                                 side_effect=capture_subproc):
                mgr._run_netsim_cli('netsim',
                                    43929, ['capture', 'list'],
                                    ini_dir=ini_dir,
                                    instance_num=None)
                mgr._run_netsim_cli('netsim',
                                    43929, ['capture', 'list'],
                                    ini_dir=ini_dir,
                                    instance_num=1)
            self.assertEqual(len(seen_envs), 2)
            self.assertEqual(seen_envs[0][1]['NETSIM_INSTANCE'], '1')
            self.assertEqual(seen_envs[1][1]['NETSIM_INSTANCE'], '1')

            if os.name != 'posix' or not hasattr(os, 'getuid'):
                self.skipTest('the rest of this test needs POSIX ownership')

            my_uid = os.stat(tmpdir).st_uid or 1000
            foreign_uid = my_uid + 55

            # 2) _poll_daemon_log rejects symlink and foreign ini_path_logged.
            art_dir = os.path.join(tmpdir, 'android-u', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            real_ini = os.path.join(tmpdir, 'real_target.ini')
            _touch(real_ini, f'pid={_DAEMON_B}\ngrpc.port=49999\n')
            foreign_ini = os.path.join(tmpdir, 'foreign_netsim.ini')
            _touch(foreign_ini, f'pid={_DAEMON_B}\ngrpc.port=49999\n')
            sym_ini = os.path.join(tmpdir, 'sym_netsim.ini')
            has_sym = _try_symlink(real_ini, sym_ini)
            poison_target = sym_ini if has_sym else foreign_ini
            _touch(
                os.path.join(art_dir, 'netsim_stderr.log'),
                f'netsim I 09-25 22:37:05.100 s.rs:1 - INI file path: {poison_target}\n'
                f'netsim I 09-25 22:37:05.200 s.rs:2 - INI file path: {foreign_ini}\n'
            )
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=True,
                                        out_dir=tmpdir,
                                        os_family='linux')
            tracker.custom_ini_dirs = [tmpdir]
            orig_lstat = os.lstat

            def lstat_foreign_ini(p, *args, **kwargs):
                st = orig_lstat(p, *args, **kwargs)
                if p == foreign_ini:
                    vals = list(st)
                    vals[4] = foreign_uid
                    return os.stat_result(vals)
                return st

            d_proc = {
                'pid': _DAEMON_A,
                'uid': my_uid,
                'create_time': float(_START),
                'ppid': 1,
                'same_user': True,
                'cmdline': ['netsimd'],
                'environ': {
                    'TMPDIR': tmpdir,
                    'USER': 'u'
                },
                'exe': '/opt/sdk/emulator/netsimd',
            }
            with patch.object(erm, '_current_uid', return_value=my_uid), \
                    patch('os.lstat', side_effect=lstat_foreign_ini), \
                    patch.object(erm, '_snapshot_process_state',
                                 return_value=([d_proc], {}, {}, {})), \
                    patch.object(tracker, '_read_proc_locks', return_value={}), \
                    patch.object(tracker, '_collect_loopback_grpc_sockets',
                                 return_value={}):
                tracker.update({}, {}, now_wall=float(_START + 2), now_mono=2.0)
            self.assertIsNone(tracker.daemons[(_DAEMON_A,
                                               float(_START))]['ini_path'])

            # 3) restore_captures_on_exit re-verifies _is_trusted_ini_path.
            list_out = (
                'ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n'
                '1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n'
            )
            cli_calls = []
            mgr._run_netsim_cli = lambda _b, _p, a, **_kw: (cli_calls.append(
                list(a)) or list_out)
            d_info = {
                'daemon_key': (9100, 1000.0),
                'pid': 9100,
                'uid': my_uid,
                'start_wall': 1000.0,
                'grpc_port': 43929,
                'ini_path': ini_file,
                'artifact_dir': art_dir,
                'same_user': True,
                'exited': False,
            }
            with patch.object(erm, '_current_uid', return_value=my_uid):
                mgr.poll_and_manage_daemon(d_info, [{
                    'serial': 'emulator-5554',
                    'device_name': 'Phone',
                    'state': 'CONNECTED',
                    'chip_ids': {'1'},
                }],
                                           now_mono=10.0)
                self.assertEqual(
                    mgr.enabled_by_monitor[(d_info['daemon_key'],
                                            1)]['ini_path'], ini_file)
                cli_calls.clear()

                # Simulate INI unlinked in sticky /tmp before exit (both normal
                # user and sudo root _current_uid() == 0 must skip CLI cleanup).
                canon_ini_dir = os.path.realpath(ini_dir)
                canon_ini_file = os.path.join(canon_ini_dir, 'netsim.ini')

                def lstat_untrusted_exit(p, *args, **kwargs):
                    st = orig_lstat(p, *args, **kwargs)
                    if p in (ini_dir, canon_ini_dir):
                        vals = list(st)
                        vals[0] = stat.S_IFDIR | stat.S_ISVTX | 0o777
                        vals[4] = 0
                        return os.stat_result(vals)
                    if p in (ini_file, canon_ini_file):
                        raise FileNotFoundError(p)
                    return st

                orig_stat = os.stat

                def stat_sticky_exit(p, *args, **kwargs):
                    st = orig_stat(p, *args, **kwargs)
                    if p in (ini_dir, canon_ini_dir):
                        vals = list(st)
                        vals[0] = stat.S_IFDIR | stat.S_ISVTX | 0o777
                        vals[4] = 0
                        return os.stat_result(vals)
                    return st

                with patch('os.lstat', side_effect=lstat_untrusted_exit), \
                        patch('os.stat', side_effect=stat_sticky_exit):
                    mgr.restore_captures_on_exit({d_info['daemon_key']: d_info})
                    with patch.object(erm, '_current_uid', return_value=0):
                        mgr.restore_captures_on_exit(
                            {d_info['daemon_key']: d_info})
                self.assertEqual(cli_calls, [])
                # The summary names the capture the monitor could not turn off.
                left_on = (
                    "Left capture on for 'Phone' (chip 1): netsim INI is "
                    "missing or no longer trusted")
                self.assertEqual(mgr.skipped_notes, [left_on])

                # Nor is it turned off when the INI stops being trusted while
                # `capture list` runs.
                mgr.skipped_notes.clear()
                with patch.object(erm,
                                  '_is_trusted_ini_path',
                                  side_effect=[True, False]):
                    mgr.restore_captures_on_exit({d_info['daemon_key']: d_info})
                self.assertEqual(cli_calls, [['capture', 'list']])
                self.assertEqual(mgr.skipped_notes, [left_on])
                cli_calls.clear()

                # Once INI is valid again, restore_captures_on_exit turns it off.
                mgr.restore_captures_on_exit({d_info['daemon_key']: d_info})
                self.assertIn(['capture', 'list'], cli_calls)
                self.assertIn(['capture', 'patch', 'off', '1', 'Phone', 'WIFI'],
                              cli_calls)

    def test_reconcile_emulator_session_entry_dead_pid_and_retired(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)
            tracker.custom_ini_dirs = [tmpdir]
            bare_keys = set()
            summary = {'iterations': 3, 'emulators': {}, 'time_series': []}

            # Pre-existing named entry from a dead prior PID that is marked
            # retired (so _storage_key_for_serial returns None), then re-launched
            # on the host-only loop with a new PID.
            dead_group = erm._HostGroupIdentity.from_scan(
                serial='emulator-5554',
                pid=4100,
                primary_proc_ids={'emulator-5554': (4100, 50.0)},
                emu_group_pids={'emulator-5554': {4100}},
                emu_group_info={})
            r_old = erm._reconcile_emulator_session_entry(
                summary_data=summary,
                netsim_tracker=tracker,
                bare_key_serials=bare_keys,
                serial='emulator-5554',
                avd_name='Pixel_8',
                model_part='Pixel 8',
                host_group=dead_group,
                adb_seen=True,
                known_boot=True,
                is_booting=False)
            r_old.entry['retired'] = True
            r_old.entry['host_exited'] = True
            r_old.entry['boot_completed'] = True

            live_group = erm._HostGroupIdentity.from_scan(
                serial='emulator-5554',
                pid=4200,
                primary_proc_ids={'emulator-5554': (4200, 90.0)},
                emu_group_pids={'emulator-5554': {4200, 4201}},
                emu_group_info={
                    'emulator-5554': {
                        'member_start_times': {
                            4200: 90.0,
                            4201: 90.0
                        }
                    }
                })
            r_new = erm._reconcile_emulator_session_entry(
                summary_data=summary,
                netsim_tracker=tracker,
                bare_key_serials=bare_keys,
                serial='emulator-5554',
                avd_name='Pixel_8',
                model_part='Unknown Device',
                host_group=live_group,
                adb_seen=False)
            self.assertEqual(r_new.storage_key, 'Pixel_8_emulator-5554')
            self.assertFalse(r_new.created)
            self.assertTrue(r_new.restarted)
            self.assertFalse(r_new.entry.get('retired', False))
            self.assertFalse(r_new.entry['boot_completed'])
            self.assertFalse(r_new.entry['adb_seen'])
            self.assertEqual(r_new.entry['current_pid'], 4200)
            self.assertEqual(r_new.entry['group_pids'], {4200, 4201})

    def test_reconcile_emulator_session_entry_transitions(self):
        import emulator_resource_monitor as erm

        serial = 'emulator-5554'
        key = 'Pixel_8_emulator-5554'

        def group(pid, ctime=None):
            return erm._HostGroupIdentity.from_scan(
                serial=serial,
                pid=pid,
                primary_proc_ids={serial: (pid, ctime)} if pid else {},
                emu_group_pids={serial: {pid}} if pid else {},
                emu_group_info={})

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = erm.NetsimTracker(pcap_dir=None,
                                        enable_log=False,
                                        out_dir=tmpdir)

            def reconcile(summary, **kwargs):
                args = dict(summary_data=summary,
                            netsim_tracker=tracker,
                            bare_key_serials=set(),
                            serial=serial,
                            avd_name='Pixel_8',
                            model_part='Pixel 8',
                            adb_seen=True)
                args.update(kwargs)
                return erm._reconcile_emulator_session_entry(**args)

            # The first adb sighting creates the entry.
            summary = {'iterations': 3, 'emulators': {}, 'time_series': []}
            first = reconcile(summary,
                              host_group=group(100, 1.0),
                              known_boot=True,
                              is_booting=False)
            self.assertEqual(
                (first.storage_key, first.created, first.restarted),
                (key, True, False))
            entry = first.entry
            self.assertEqual(entry['name'], 'Pixel_8 | Pixel 8')
            self.assertTrue(entry['boot_completed'])

            # Another process on the serial is a new emulator: the entry is
            # reset, and the previous emulator's model is not shown for it.
            second = reconcile(summary,
                               host_group=group(200, 2.0),
                               model_part='Unknown Device',
                               track_boot_stats_on_reset=True)
            self.assertIs(second.entry, entry)
            self.assertEqual((second.created, second.restarted), (False, True))
            self.assertEqual(entry['name'], 'Pixel_8 | Unknown Device')
            self.assertEqual(entry['current_pid'], 200)
            self.assertFalse(entry['boot_completed'])
            self.assertTrue(entry['track_boot_stats'])
            # Without a restart an unknown model does not replace a known one.
            reconcile(summary, host_group=group(200, 2.0), model_part='Pixel 9')
            self.assertEqual(entry['name'], 'Pixel_8 | Pixel 9')
            third = reconcile(summary,
                              host_group=group(200, 2.0),
                              model_part='Unknown Device')
            self.assertFalse(third.restarted)
            self.assertEqual(entry['name'], 'Pixel_8 | Pixel 9')

            # No process in a stale host scan says nothing. In a fresh one it
            # means the emulator's process is gone.
            entry['_pending_host_scan'] = True
            reconcile(summary, host_group=group(None), host_fresh=False)
            self.assertTrue(entry['_pending_host_scan'])
            self.assertFalse(entry['host_exited'])
            self.assertEqual(entry['group_pids'], {200})
            reconcile(summary, host_group=group(None), host_fresh=True)
            self.assertNotIn('_pending_host_scan', entry)
            self.assertTrue(entry['host_exited'])
            self.assertEqual((entry['group_pids'], entry['group_procs']),
                             (set(), {}))

            # An evicted entry that adb lists again before the host scan has
            # caught up is reset and waits for that scan.
            entry['evicted'] = True
            fourth = reconcile(summary,
                               host_group=group(None),
                               has_live_target=False,
                               host_fresh=False,
                               pending_host_scan=True)
            self.assertTrue(fourth.restarted)
            self.assertTrue(entry['_pending_host_scan'])
            self.assertIsNone(entry['first_seen'])
            self.assertIsNone(entry['current_pid'])
            self.assertFalse(entry['host_exited'])
            self.assertNotIn('evicted', entry)

            # An emulator only the host scan has found was never seen by adb.
            summary = {'iterations': 0, 'emulators': {}, 'time_series': []}
            host_only = reconcile(summary,
                                  host_group=group(300, 3.0),
                                  adb_seen=False,
                                  model_part='Unknown Device')
            self.assertTrue(host_only.created)
            self.assertEqual(host_only.entry['last_seen'], 0)
            self.assertFalse(host_only.entry['adb_seen'])
            self.assertIsNotNone(host_only.entry['first_seen'])

            # A live process replaces the placeholder that adb left for an
            # offline device under the bare serial, columns included.
            summary = {
                'iterations': 4,
                'emulators': {},
                'time_series': [{
                    'emu_cpu_tot_emulator-5554': 1.0
                }],
            }
            bare = set()
            placeholder = reconcile(summary,
                                    bare_key_serials=bare,
                                    avd_name=None,
                                    host_group=group(None),
                                    has_live_target=False)
            self.assertEqual(placeholder.storage_key, serial)
            self.assertIsNone(placeholder.entry['first_seen'])
            self.assertEqual(bare, {serial})
            live = reconcile(summary,
                             bare_key_serials=bare,
                             host_group=group(400, 4.0),
                             adb_seen=False,
                             model_part='Unknown Device')
            self.assertTrue(live.created)
            self.assertEqual(list(summary['emulators']), [key])
            self.assertEqual(summary['time_series'], [{}])
            self.assertEqual(bare, set())

            # An entry created without a live target gets its start time once
            # a process shows up; that alone is not a restart.
            summary = {'iterations': 0, 'emulators': {}, 'time_series': []}
            offline = reconcile(summary,
                                host_group=group(None),
                                has_live_target=False)
            self.assertIsNone(offline.entry['first_seen'])
            started = reconcile(summary,
                                host_group=group(500, 5.0),
                                adb_seen=False)
            self.assertIs(started.entry, offline.entry)
            self.assertFalse(started.restarted)
            self.assertIsNotNone(started.entry['first_seen'])
            self.assertEqual(started.entry['current_pid'], 500)
            self.assertTrue(started.entry['adb_seen'])

            # A retired entry whose key is used again is reset even when the
            # process looks the same (same pid, start time unknown).
            summary = {'iterations': 0, 'emulators': {}, 'time_series': []}
            old = reconcile(summary, host_group=group(600)).entry
            old['retired'] = True
            old['first_seen'] = -1.0
            reused = reconcile(summary, host_group=group(600))
            self.assertIs(reused.entry, old)
            self.assertEqual((reused.created, reused.restarted), (False, True))
            self.assertNotIn('retired', old)
            self.assertGreaterEqual(old['first_seen'], 0.0)

    @unittest.skipUnless(os.name == 'posix', 'uses POSIX ownership')
    def test_copy_regular_file_refuses_a_file_of_another_user(self):
        import emulator_resource_monitor as erm

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, 'netsim-1-Phone-WIFI.pcap')
            dst = os.path.join(tmpdir, 'copy.pcap')
            _touch(src, 'abc')
            self.assertIsNone(
                erm._copy_regular_file(src, dst, expected_uid=os.getuid() + 1))
            self.assertFalse(os.path.exists(dst))
            st = erm._copy_regular_file(src, dst, expected_uid=os.getuid())
            self.assertEqual(st.st_size, 3)
            self.assertTrue(os.path.exists(dst))

    def test_emulator_found_only_by_the_host_scan_replaces_its_offline_placeholder(
            self):
        import emulator_resource_monitor as erm

        # Tick 0: adb lists the serial as offline and no process is found, so
        # it gets a placeholder. From tick 1 on adb no longer lists it, but
        # the host scan finds its process.
        no_emulator = _host_tuple()
        running = _host_tuple(
            {'emulator-5554': 1000}, {'emulator-5554': 'Pixel_8'},
            qemu_stats={'emulator-5554': ('20.0%', '1000.0MB (50 cs/s)')})
        with patch.multiple(erm,
                            _LAST_EMU_GROUP_PIDS={},
                            _LAST_EMU_GROUP_INFO={},
                            _LAST_PRIMARY_PROC_ID={}), \
                patch.object(erm.NetsimTracker, 'update',
                             lambda *_args, **_kwargs: None):
            summary_data, _ = _run_main_sync([no_emulator, running, running],
                                             ['Pixel 8'] * 3,
                                             devices=[{
                                                 'emulator-5554': 'offline'
                                             }, {}, {}],
                                             offline_guest_ticks=(0,))
        self.assertEqual(list(summary_data['emulators']),
                         ['Pixel_8_emulator-5554'])
        emu = summary_data['emulators']['Pixel_8_emulator-5554']
        self.assertIsNotNone(emu['first_seen'])
        self.assertFalse(emu['adb_seen'])
        self.assertEqual(emu['qemu_cpu_iterations'], 2)

    def test_resolve_netsim_cli_bin_os_family_parameter(self):
        import emulator_resource_monitor as erm

        self.assertEqual(
            erm.resolve_netsim_cli_bin('', True, os_family='windows'),
            'netsim.exe')
        self.assertEqual(
            erm.resolve_netsim_cli_bin(r'C:\Sdk\emulator\netsimd.exe',
                                       False,
                                       os_family='win32'), 'netsim.exe')
        self.assertEqual(
            erm.resolve_netsim_cli_bin('', True, os_family='linux'), 'netsim')
        self.assertEqual(
            erm.resolve_netsim_cli_bin('', True, os_family='darwin'), 'netsim')

    def test_install_py_locale_safe_option_rejection_and_full_branch_coverage(
            self):
        import contextlib
        import subprocess
        import install as inst

        # 1) _in_virtualenv branches: prefix != base_prefix, real_prefix, system.
        with patch.object(sys, 'prefix', '/venv'), \
                patch.object(sys, 'base_prefix', '/usr'):
            self.assertTrue(inst._in_virtualenv())
        with patch.object(sys, 'prefix', '/usr'), \
                patch.object(sys, 'base_prefix', '/usr'), \
                patch.object(sys, 'real_prefix', '/usr', create=True):
            self.assertTrue(inst._in_virtualenv())
        with patch.object(sys, 'prefix', '/usr'), \
                patch.object(sys, 'base_prefix', '/usr'):
            if not hasattr(sys, 'real_prefix'):
                self.assertFalse(inst._in_virtualenv())

        # 2) _rejected_break_system_packages: empty, localized rc=2, unrelated
        # rc=2, English marker rc=1, and PEP 668 / compiler rc=1.
        self.assertFalse(inst._rejected_break_system_packages('', None))
        self.assertFalse(inst._rejected_break_system_packages(None, 2))
        self.assertTrue(
            inst._rejected_break_system_packages(
                'Aufruf: pip install [Optionen]\n'
                'Fehler: Keine solche Option: --break-system-packages\n', 2))
        self.assertTrue(
            inst._rejected_break_system_packages(
                'エラー: そのようなオプションはありません: --break-system-packages\n', 2))
        self.assertFalse(
            inst._rejected_break_system_packages('no such option: --bad-flag\n',
                                                 2))
        self.assertTrue(
            inst._rejected_break_system_packages(
                'ERROR: no such option: --break-system-packages\n', 1))
        self.assertFalse(
            inst._rejected_break_system_packages(
                'pip install --user --break-system-packages psutil\n'
                'ERROR: Could not find a version that satisfies psutil\n', 1))

        # 3) _write_stderr: empty no-op, UTF-8 write, and encoding=None fallback.
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            inst._write_stderr('')
            inst._write_stderr(None)
            self.assertEqual(err_buf.getvalue(), '')
            inst._write_stderr('plain err\n')
            self.assertEqual(err_buf.getvalue(), 'plain err\n')

        class _StrictNoEncStderr:

            def __init__(self):
                self.encoding = None
                self.writes = []

            def write(self, s):
                if any(ord(ch) > 127 for ch in s):
                    raise UnicodeEncodeError('ascii', s, 0, 1, 'non-ascii')
                self.writes.append(s)

        fake_err = _StrictNoEncStderr()
        with patch.object(sys, 'stderr', fake_err):
            inst._write_stderr('warn ×\n')
        self.assertEqual(fake_err.writes, ['warn ?\n'])

        # 4) _pip_install passes LC_MESSAGES=C and retries localized rc=2 while
        # keeping rc=1 non-retried.
        seen_envs = []
        retried = []

        def localized_rc2(cmd, **kw):
            seen_envs.append(dict(kw.get('env') or {}))
            return subprocess.CompletedProcess(
                cmd,
                2,
                stdout='',
                stderr='aucune option telle que : --break-system-packages\n')

        with patch.dict(os.environ, {'LC_MESSAGES': 'fr_FR.UTF-8'}), \
                patch.object(inst.subprocess, 'run', side_effect=localized_rc2), \
                patch.object(inst.subprocess,
                             'check_call',
                             side_effect=lambda c, **_k: retried.append(list(c))):
            inst._pip_install('psutil', in_venv=False)
        self.assertEqual(seen_envs[0].get('LC_MESSAGES'), 'C')
        self.assertEqual(len(retried), 1)
        self.assertNotIn('--break-system-packages', retried[0])

        # 5) main() generic Exception branch.
        out_exc = io.StringIO()
        err_exc = io.StringIO()
        with patch.object(inst, '_in_virtualenv',
                          side_effect=RuntimeError('unexpected boom')), \
                contextlib.redirect_stdout(out_exc), \
                contextlib.redirect_stderr(err_exc):
            with self.assertRaises(SystemExit) as cm_exc:
                inst.main()
        self.assertEqual(cm_exc.exception.code, 1)
        self.assertIn('An error occurred: unexpected boom', err_exc.getvalue())


if __name__ == '__main__':

    unittest.main()
