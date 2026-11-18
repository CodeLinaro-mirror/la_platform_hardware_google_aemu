"""Tests for install.py: virtualenv detection, pip fallbacks and errors."""

import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Prepend tests/ (for test_support) and python/ (for the monitor) to sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_support


class TestInstallScriptExecution(test_support.MockedCmdTestCase):

    def test_install_py_handles_virtualenv_legacy_pip_and_psutil_failure(self):
        import contextlib
        import io
        import runpy
        import subprocess
        import tempfile

        install_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'install.py')

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


class TestInstallHelpersAndLocaleSafety(unittest.TestCase):

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
