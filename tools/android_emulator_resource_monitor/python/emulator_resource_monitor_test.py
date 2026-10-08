import unittest
from unittest.mock import patch, MagicMock, mock_open
import io
import os
import socket
import sys
import tempfile
import time
from collections import namedtuple

# Append current path to sys.path to import our script
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from emulator_resource_monitor import get_val, get_devices, RE_ANSI, RE_FLOAT, RE_PCT, parse_cmdline


class TestMonitor(unittest.TestCase):

    def setUp(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()

    def tearDown(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()

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

    def test_regex_ansi_strip(self):
        ansi_str = "\033[91mHello\033[0m"
        stripped = RE_ANSI.sub("", ansi_str)
        self.assertEqual(stripped, "Hello")

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
                    break
        self.assertTrue(called_with_filter)

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

    def test_get_guest_stats_offline_returns_booting(self):
        from emulator_resource_monitor import get_guest_stats
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", "Android 12", False, "offline")
        self.assertTrue(is_booting)
        self.assertEqual(total_cpu, "N/A (offline)")

    @patch('emulator_resource_monitor.run_cmd')
    def test_get_guest_stats_busy_device_retains_booting(self, mock_run_cmd):
        mock_run_cmd.return_value = ""  # Simulate failure to run shell commands
        from emulator_resource_monitor import get_guest_stats
        model, is_booting, total_cpu, adj_cpu, ram, swap = get_guest_stats(
            "emulator-5554", "Android 12", True, "device")
        self.assertTrue(is_booting)  # Should retain True

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

    @patch('psutil.disk_io_counters')
    @patch('psutil.sensors_temperatures')
    @patch('psutil.virtual_memory')
    @patch('psutil.swap_memory')
    @patch('psutil.cpu_percent')
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
        with patch('os.path.exists',
                   return_value=True), patch('builtins.open',
                                             MagicMock()) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = "hypervisor flags"
            try:
                save_summary(mock_summary, 1005.0, save_csv=False, out_dir='.')
            except Exception as e:
                self.fail(f"VM Test threw unexpected Exception: {e}")

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
            if len(sleep_calls) >= 3:
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
            if len(sleep_calls) >= 3:
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
            if len(sleep_calls) >= 2:
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
            if len(sleep_calls) >= 2:
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
            if len(sleep_calls) >= 3:
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

    @patch('plotille.Figure')
    def test_graphs_generation(self, mock_figure_class):
        from emulator_resource_monitor import save_summary

        mock_figure = MagicMock()
        mock_figure.show.return_value = "mocked graph content"
        mock_figure_class.return_value = mock_figure

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

        mock_file = MagicMock()
        written_lines = []

        def mock_write(s):
            written_lines.extend(s.splitlines())

        mock_file.write.side_effect = mock_write

        with patch('builtins.open',
                   return_value=MagicMock(__enter__=MagicMock(
                       return_value=mock_file))):
            with patch('emulator_resource_monitor.run_cmd',
                       return_value="Android Debug Bridge version 1.0.41"):
                save_summary(mock_summary,
                             1010.0,
                             save_csv=False,
                             save_txt=True,
                             out_dir='.')

        # 10 host charts + 7 emulator charts = 17 charts total!
        self.assertEqual(mock_figure.plot.call_count, 17)

    @patch('plotille.Figure')
    def test_graphs_generation_limits(self, mock_figure_class):
        from emulator_resource_monitor import save_summary

        mock_figure = MagicMock()
        mock_figure_class.return_value = mock_figure

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

        with patch('builtins.open', MagicMock()):
            with patch('emulator_resource_monitor.run_cmd', return_value=""):
                save_summary(mock_summary,
                             1010.0,
                             save_csv=False,
                             save_txt=True,
                             out_dir='.')

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

        with patch('platform.system', return_value='Linux'):
            with patch('platform.processor', return_value='Mock Processor'):
                with patch('os.path.exists', return_value=True):

                    def mock_open(path, mode='r'):
                        if path == '/etc/os-release':
                            return MagicMock(__enter__=MagicMock(
                                return_value=['PRETTY_NAME="Mock OS"\n']))
                        elif path == '/proc/cpuinfo':
                            return MagicMock(__enter__=MagicMock(
                                return_value=['model name : Mock CPU\n']))
                        return MagicMock(__enter__=MagicMock(return_value=[]))

                    with patch('builtins.open', side_effect=mock_open):
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
        from emulator_resource_monitor import get_gpu_stats

        with patch('platform.system', return_value='Linux'):
            with patch('emulator_resource_monitor.HAS_GPUTIL', False):
                with patch('emulator_resource_monitor.run_cmd',
                           return_value=""):
                    with patch('os.path.exists', return_value=True):
                        with patch('builtins.open',
                                   return_value=MagicMock(__enter__=MagicMock(
                                       return_value=MagicMock(read=MagicMock(
                                           return_value="45\n"))))):
                            gpu_load, gpu_mem = get_gpu_stats()

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

        with patch('shutil.which', return_value=None):
            with patch('os.environ.get',
                       side_effect=lambda k, d=None: '/path/to/sdk'
                       if k == 'ANDROID_HOME' else None):
                with patch('os.path.exists', return_value=True):
                    res = find_adb()

        self.assertEqual(res, '/path/to/sdk/platform-tools/adb')

    def test_handle_sigterm(self):
        from emulator_resource_monitor import handle_sigterm
        with self.assertRaises(KeyboardInterrupt):
            handle_sigterm(None, None)

    def test_main_trends(self):
        from emulator_resource_monitor import main

        mock_host_stats = [
            ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", {}, "0%", "0%", {},
             "N/A", "0MB/s", 0.0, 0.0, "N/A", {}),
            ("20%", {}, "4%", "40%", "0%", 0, 0, [], "20MB", {}, "0%", "0%", {},
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
            if len(sleep_calls) >= 2:
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
    @patch('psutil.cpu_percent')
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

        mock_file = mock_open(read_data="pgpgin 1000\npgpgout 2000\n")

        from emulator_resource_monitor import get_host_stats

        with patch('builtins.open', mock_file):
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

    @patch('plotille.Figure')
    def test_graphs_generation_flat_data(self, mock_figure_class):
        from emulator_resource_monitor import save_summary

        mock_figure = MagicMock()
        mock_figure_class.return_value = mock_figure

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

        mock_file = MagicMock()
        written_lines = []

        def mock_write(s):
            written_lines.extend(s.splitlines())

        mock_file.write.side_effect = mock_write

        mock_figure.plot.side_effect = Exception("Plotille crash")

        with patch('builtins.open',
                   return_value=MagicMock(__enter__=MagicMock(
                       return_value=mock_file))):
            save_summary(mock_summary,
                         1010.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir='.')

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
            if 'top' in cmd:
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
            if len(sleep_calls) >= 3:
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
            if len(sleep_calls) >= 3:
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

        mock_devices.return_value = {"emulator-5554": "device"}
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
            else:
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
            if iteration >= 2:
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

        mock_devices.return_value = {"emulator-5554": "device"}
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

            if iteration >= 4:
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
            if iteration >= 3:
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

        with patch('builtins.open', MagicMock()):
            try:
                save_summary(mock_summary,
                             1005.0,
                             save_csv=False,
                             save_txt=True,
                             out_dir='.')
            except ValueError as e:
                self.fail(f"save_summary crashed with ValueError: {e}")

    def test_guest_cpu_cores_rounding(self):
        from emulator_resource_monitor import get_guest_stats

        def mock_run_cmd(cmd, **kwargs):
            if 'top' in cmd:
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

        with tempfile.TemporaryDirectory() as tmpdir:
            save_summary(mock_summary,
                         1010.0,
                         save_csv=False,
                         save_txt=True,
                         out_dir=tmpdir)

            txt_files = [f for f in os.listdir(tmpdir) if f.endswith('.txt')]
            self.assertEqual(len(txt_files), 1)

            with open(os.path.join(tmpdir, txt_files[0]), 'r') as f:
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
            if len(sleep_calls) >= 2:
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


def _host_tuple(qemu_pids=None, qemu_avds=None):
    return ("10%", {}, "2%", "30%", "0%", 0, 0, [], "10MB", qemu_pids or
            {}, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", qemu_avds or
            {})


class TestCorrectnessFixes(unittest.TestCase):

    def setUp(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()
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

    def tearDown(self):
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()

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
            if len(sleep_calls) >= 3:
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
            self.assertIsNotNone(tailer.wifi_associated_ts)
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

            def fake_cli(netsim_bin, grpc_port, args, timeout=5.0):
                cli_calls.append(list(args))
                if args[:2] == ['capture', 'list']:
                    return cli_out
                return "OK"

            mgr._run_netsim_cli = fake_cli
            daemon_info = {
                'daemon_key': (9000, 1000.0),
                'pid':
                    9000,
                'start_wall':
                    1000.0,
                'grpc_port':
                    43929,
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
        os.symlink(secret, os.path.join(pcaps_dir, 'netsim-2-Evil-BT.pcap'))
        os.symlink(secret, os.path.join(art_dir, 'netsim_artifacts_2.zip'))
        mgr = NetsimPcapManager(pcap_dir='', max_mb=1, out_dir=out_dir)
        daemon_info = {
            'daemon_key': (9001, 1000.0),
            'pid': 9001,
            'start_wall': 1000.0,
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
            # The live counters still describe what is on disk.
            self.assertEqual(daemon_info['pcap_live_files'], 2)

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
            # A symlink named netsim is judged by its target, which is fine
            # when the target is trusted too.
            os.remove(cli)
            real = os.path.join(sdk, 'netsim-real')
            with open(real, 'wb') as f:
                f.write(b'#!/bin/sh\n')
            os.chmod(real, 0o755)
            os.symlink(real, cli)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True), cli)
            # Missing sibling falls back to PATH.
            os.remove(cli)
            os.remove(real)
            self.assertEqual(erm.resolve_netsim_cli_bin(daemon_exe, True),
                             'netsim')

    def test_netsim_tracker_standalone_and_emulator_lifecycle(self):
        import emulator_resource_monitor as erm
        with tempfile.TemporaryDirectory() as tmpdir:
            art_dir = os.path.join(tmpdir, 'android-user', 'netsimd')
            os.makedirs(art_dir, exist_ok=True)
            log_path = os.path.join(art_dir, 'netsim_stderr.log')
            ini_path = os.path.join(tmpdir, 'netsim.ini')
            with open(ini_path, 'w', encoding='utf-8') as f:
                f.write("grpc.port=43929\npid=9500\n")
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(
                    'netsim I 09-25 22:37:05.683 netsimd.rs:757 - Wrote to INI file '
                    + ini_path + '\n')

            erm._LAST_NETSIMD_PROCS = [{
                'pid': 9500,
                'create_time': 1000.0,
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
                                        out_dir=tmpdir)
            with patch.object(tracker,
                              '_discover_ini_files',
                              return_value=[ini_path]), \
                    patch.object(tracker,
                                 '_read_proc_locks',
                                 return_value={}):
                tracker.update({}, {}, now_wall=1005.0, now_mono=10.0)
                # After 30s with 0 devices, daemon transitions to ORPHANED
                tracker.update({}, {}, now_wall=1040.0, now_mono=45.0)
                d = tracker.daemons[(9500, 1000.0)]
                self.assertEqual(d['spawner'], 'STANDALONE')
                self.assertTrue(d['orphaned'])

                # Now attach an emulator and verify CONNECTED state + CSV/summary
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(
                        'netsim I 09-25 22:37:10.000 service.rs:585 - '
                        'DeviceActor: AddChip 1 (kind WIFI) to device 1 (ERM QN Phone 37)\n'
                    )
                erm._LAST_EMU_GROUP_PIDS = {'emulator-5554': [9600]}
                erm._LAST_EMU_GROUP_INFO = {
                    'emulator-5554': {
                        'primary_pid': 9600,
                        'primary_create_time': 1006.0,
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
                        'current_pid': 9600,
                        'first_seen': 12.0,
                    }
                }
                tracker.update(emulators, {'emulator-5554': 'device'},
                               now_wall=1050.0,
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
                                         out_dir=self.root)
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
                                              out_dir=self.root)
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
        group_pids = {serial: [g['primary_pid']] for serial, g in groups.items()}
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
        self._tick(10.0, {'emulator-5554': _emu_group(os.getpid(), 'pixel')},
                   {})
        rec = self.tracker.emulator_records['pixel_emulator-5554']
        self.assertLessEqual(rec['worker_start_ts'], now)
        self.assertGreater(rec['worker_start_ts'], now - 3600)

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

        # Direct same-key restart (PID _WORKER_B -> 4003) at t=70 also resets
        # per-run state and first_seen_mono so it starts in CONNECTING rather
        # than immediately stalling.
        g3 = _emu_group(4003, 'pixel', 'Pixel', ctime=_START + 70)
        g3['grpc_endpoint'] = '127.0.0.1:43929'
        self._tick(70.0, {'emulator-5554': g3}, {key: ('emulator-5554', 4003)},
                   procs)
        self.assertEqual(rec2['worker_pid'], 4003)
        self.assertEqual(rec2['state'], 'CONNECTING')
        self.assertIsNone(rec2['first_transport_ts'])
        self.assertEqual(rec2['disconnect_count'], 1)
        self.assertEqual(rec2['connected_total_s'], 10.0)

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


if __name__ == '__main__':

    unittest.main()
