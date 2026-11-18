"""Shared helpers, constants and TestCase fixtures used across *_test.py suites."""

import copy
import os
import tempfile
import unittest
from unittest.mock import patch


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


def _guest_model(name):
    return f"{name} | Android 14 (API 34) | Build: UP1A | Res: 1080x2400"


# PIDs above Linux's PID_MAX_LIMIT (2^22), as in rust/tests/netsim/tracker_test.rs:
# no /proc lookup or liveness probe reaches a real process, so a daemon's
# start_wall equals its create_time.
_DAEMON_A = 5_000_100
_DAEMON_B = 5_000_200
_WORKER_A = 5_000_300
_WORKER_B = 5_000_400
_START = 1_790_375_825


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


class MockedCmdTestCase(unittest.TestCase):
    """Makes run_cmd return '' and hides GPUtil."""

    def setUp(self):
        super().setUp()
        rc = patch('emulator_resource_monitor.run_cmd', return_value='')
        rc.start()
        self.addCleanup(rc.stop)
        gp = patch('emulator_resource_monitor.HAS_GPUTIL', False)
        gp.start()
        self.addCleanup(gp.stop)


class MonitorTestCase(MockedCmdTestCase):
    """Clears host-scan caches and GPU latches, stubs run_cmd and hides GPUtil."""

    @staticmethod
    def _reset_monitor_caches():
        import emulator_resource_monitor
        emulator_resource_monitor._PID_PORT_CACHE.clear()
        emulator_resource_monitor._PROCESS_CACHE.clear()
        emulator_resource_monitor._NVIDIA_SMI_FAILED = False
        emulator_resource_monitor._NVIDIA_PMON_FAILED = False
        emulator_resource_monitor._LINUX_INTEL_GPU_FAILED = False

    def setUp(self):
        super().setUp()
        self._reset_monitor_caches()
        self.addCleanup(self._reset_monitor_caches)


class IsolatedDiscoveryMonitorTestCase(MonitorTestCase):
    """MonitorTestCase with discovery env vars pointed at an empty temp dir."""

    def setUp(self):
        super().setUp()
        discovery = tempfile.TemporaryDirectory()
        self.addCleanup(discovery.cleanup)
        env = patch.dict(
            os.environ, {
                'ANDROID_EMULATOR_DISCOVERY_DIR': discovery.name,
                'XDG_RUNTIME_DIR': discovery.name
            })
        env.start()
        self.addCleanup(env.stop)


class IsolatedNetsimCliTestCase(MockedCmdTestCase):
    """The MockedCmdTestCase setup plus no netsim CLI and no socket tool.

    NetsimPcapManager._run_netsim_cli and _run_cli_with_timeout return None.
    """

    def setUp(self):
        super().setUp()
        ncli = patch(
            'emulator_resource_monitor.NetsimPcapManager._run_netsim_cli',
            return_value=None)
        ncli.start()
        self.addCleanup(ncli.stop)
        scli = patch('emulator_resource_monitor._run_cli_with_timeout',
                     return_value=None)
        scli.start()
        self.addCleanup(scli.stop)


class NetsimGlobalsTestCase(unittest.TestCase):
    """Restores the three per-tick netsim/group globals after each test."""

    def setUp(self):
        super().setUp()
        import emulator_resource_monitor as erm
        for name in ('_LAST_NETSIMD_PROCS', '_LAST_EMU_GROUP_PIDS',
                     '_LAST_EMU_GROUP_INFO'):
            patcher = patch.object(erm, name, copy.deepcopy(getattr(erm, name)))
            patcher.start()
            self.addCleanup(patcher.stop)


class NetsimTempDirTestCase(unittest.TestCase):
    """Provides a temp root (`self.root`), `self.erm` and `_tracker_for(dirs)` helpers."""

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


class NetsimSessionTrackerTestCase(unittest.TestCase):
    """Provides a temp root (`self.root`) and `self.tracker` with log reading enabled."""

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
