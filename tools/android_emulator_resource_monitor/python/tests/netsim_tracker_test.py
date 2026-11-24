"""Tests for NetsimTracker: daemon matching and emulator sessions."""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Prepend tests/ (for test_support) and python/ (for the monitor) to sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_support
from test_support import (
    _DAEMON_A,
    _DAEMON_B,
    _START,
    _WORKER_A,
    _WORKER_B,
    _emu_group,
    _netsimd,
    _touch,
    _try_symlink,
    _write_ini,
)

_DEAD_PID = 5_000_999

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


class TestNetsimTrackerStandaloneLifecycle(test_support.NetsimGlobalsTestCase):

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


class TestNetsimDaemonDiscoveryAndMatching(test_support.NetsimTempDirTestCase):

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


class TestNetsimEmulatorSessionTracking(
        test_support.NetsimSessionTrackerTestCase):

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


class TestNetsimBystanderAndCompanionIsolation(test_support.MockedCmdTestCase):

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


class TestNetsimClientModeAndProvisionalIsolation(
        test_support.IsolatedNetsimCliTestCase):

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


class TestNetsimWindowsTrackerAndLoggedIniGate(unittest.TestCase):

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


if __name__ == '__main__':
    unittest.main()
