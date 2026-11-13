"""Tests for the dashboard, charts, session summary and CSV export."""

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

from emulator_resource_monitor import RE_ANSI, get_val
import test_support
from test_support import _DAEMON_A, _START, _emu_group, _touch


class TestDashboardChartsAndSummary(test_support.MonitorTestCase):

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

    def test_get_val_fallback(self):
        self.assertEqual(get_val("N/A"), 0.0)
        self.assertEqual(get_val("Error: restricted permissions"), 0.0)

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


class TestSummaryPercentilesAutosaveAndCsv(
        test_support.IsolatedDiscoveryMonitorTestCase):

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


class TestSummaryEmptyChartsBanner(test_support.MockedCmdTestCase):

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


class TestSummaryOptionalPlotilleAndUtf8Paths(
        test_support.IsolatedNetsimCliTestCase):

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


if __name__ == '__main__':
    unittest.main()
