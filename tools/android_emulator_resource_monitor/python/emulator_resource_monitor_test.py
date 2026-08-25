import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import tempfile

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

        # Primary compute PID is worker (1001) due to highest CPU
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
        self.assertIn("Pixel_7", summary_data['emulators'])

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


if __name__ == '__main__':

    unittest.main()
