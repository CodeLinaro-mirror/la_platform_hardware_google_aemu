"""Tests for cmdline parsing, emulator discovery/grouping and host metrics."""

from collections import namedtuple
import io
import math
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Prepend tests/ (for test_support) and python/ (for the monitor) to sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emulator_resource_monitor import parse_cmdline
import test_support
from test_support import _guest_model, _touch, _try_symlink, _write_ini

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


class TestHostProcessesAndMetrics(test_support.MonitorTestCase):

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


class TestHostProcessGroupsGpuAndPaging(
        test_support.IsolatedDiscoveryMonitorTestCase):

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

    def test_get_process_stats_requests_ppid_in_process_iter(self):
        import emulator_resource_monitor as erm
        requested_attrs = []

        def fake_iter(attrs=None):
            requested_attrs.extend(attrs or ())
            return []

        with patch('psutil.process_iter', side_effect=fake_iter):
            erm.get_process_stats()
        self.assertIn('ppid', requested_attrs)


class TestHostCrossPlatformProcesses(test_support.NetsimTempDirTestCase):

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


class TestHostProcessStateSnapshot(test_support.NetsimSessionTrackerTestCase):

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


class TestHostPagingRateTimestamps(test_support.MockedCmdTestCase):

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


class TestHostDiscoveryIniAndExternalNumbers(unittest.TestCase):

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


if __name__ == '__main__':
    unittest.main()
