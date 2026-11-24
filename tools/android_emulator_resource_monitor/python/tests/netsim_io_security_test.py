"""Tests for netsim discovery, file trust, sockets, log and stats parsing."""

from collections import namedtuple
import os
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Prepend tests/ (for test_support) and python/ (for the monitor) to sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emulator_resource_monitor import run_cmd as _REAL_RUN_CMD
import test_support
from test_support import (
    _DAEMON_A,
    _DAEMON_B,
    _START,
    _WORKER_A,
    _WORKER_B,
    _touch,
    _try_symlink,
    _write_ini,
)

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


def _fail_cli(args):
    raise AssertionError(f'unexpected tool run: {args}')


class TestNetsimParsersAndFileTrust(test_support.NetsimGlobalsTestCase):

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


class TestNetsimCrossPlatformDiscoveryAndSockets(
        test_support.NetsimTempDirTestCase):

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


class TestNetsimLogTailerEventBounds(test_support.NetsimSessionTrackerTestCase):

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


class TestNetsimFileBoundsAndSymlinkGuards(test_support.MockedCmdTestCase):

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


class TestNetsimZeroPortAndRegularFileCopy(
        test_support.IsolatedNetsimCliTestCase):

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


class TestNetsimTrustAndOwnershipRules(unittest.TestCase):

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


if __name__ == '__main__':
    unittest.main()
