"""Tests for the main loop: device sessions, boot tracking and shutdown."""

import io
import math
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Prepend tests/ (for test_support) and python/ (for the monitor) to sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emulator_resource_monitor import RE_ANSI
import test_support
from test_support import _DAEMON_A, _START, _guest_model


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


def _host_tuple(qemu_pids=None, qemu_avds=None, qemu_stats=None):
    return ("10%", qemu_stats or
            {}, "2%", "30%", "0%", 0, 0, [], "10MB", qemu_pids or
            {}, "0%", "0%", {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", qemu_avds or
            {})


class TestSessionMainLoopAndBootTracking(test_support.MonitorTestCase):

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
            ("20%", {}, "4%", "40%", "15%", 0, 0, [], "20MB", {}, "0%", "0%",
             {}, "N/A", "0MB/s", 0.0, 0.0, "N/A", {})
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


class TestDeviceCacheAndBootLifecycle(test_support.MockedCmdTestCase):

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


class TestSessionAsyncHarvestAndKeyMigration(
        test_support.IsolatedDiscoveryMonitorTestCase):

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


class TestSessionReconnectAndBareSerialIsolation(
        test_support.IsolatedNetsimCliTestCase):

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


class TestSessionEntryReconciliationAndResilience(unittest.TestCase):

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


if __name__ == '__main__':
    unittest.main()
