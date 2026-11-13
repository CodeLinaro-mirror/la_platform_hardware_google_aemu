"""Tests for NetsimPcapManager: captures, harvesting and exit restore."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Prepend tests/ (for test_support) and python/ (for the monitor) to sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_support
from test_support import (
    _DAEMON_A,
    _START,
    _emu_group,
    _touch,
    _try_symlink,
    _write_ini,
)


class NetsimPcapHarvestTestCase(test_support.NetsimGlobalsTestCase):
    """Adds an artifact directory fixture for the pcap harvest tests."""

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


class TestNetsimPcapCaptureAndHarvest(NetsimPcapHarvestTestCase):

    def test_sanitize_netsim_filename_replaces_invalid_chars(self):
        # netsimd maps every character other than alphanumerics, '-' and '_'
        # to '_' (capture-actor/src/service.rs), as the Rust monitor does.
        from emulator_resource_monitor import sanitize_netsim_filename
        self.assertEqual(sanitize_netsim_filename('Pixel 8 Pro!'),
                         'Pixel_8_Pro_')

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


class TestNetsimPcapDisambiguationAndTmpIni(test_support.MockedCmdTestCase):

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


class TestNetsimPcapCliVettingAndInstanceEnv(unittest.TestCase):

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


if __name__ == '__main__':
    unittest.main()
