#!/usr/bin/env python3
import os
import subprocess
import sys

_UNSUPPORTED_OPTION_MARKERS = (
    "no such option",
    "unrecognized arguments",
    "unrecognized option",
    "unknown option",
    "unexpected argument",
    "not recognized",
)


def _in_virtualenv():
    return (sys.prefix != getattr(sys, "base_prefix", sys.prefix) or
            hasattr(sys, "real_prefix"))


def _rejected_break_system_packages(stderr_text, returncode=None):
    text = stderr_text or ""
    if returncode == 2 and "--break-system-packages" in text.lower():
        return True
    for line in text.splitlines():
        lower = line.lower()
        if "--break-system-packages" in lower and any(
                marker in lower for marker in _UNSUPPORTED_OPTION_MARKERS):
            return True
    return False


def _write_stderr(text):
    if not text:
        return
    try:
        sys.stderr.write(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stderr, "encoding", None) or "ascii"
        safe = text.encode(enc, errors="replace").decode(enc, errors="replace")
        sys.stderr.write(safe)


def _pip_install(dep, in_venv):
    base_cmd = [
        sys.executable, "-m", "pip", "install", "--index-url",
        "https://pypi.org/simple"
    ]
    if in_venv:
        subprocess.check_call(base_cmd + [dep])
        return

    cmd = base_cmd + ["--user", "--break-system-packages", dep]
    res = subprocess.run(
        cmd,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env={
            **os.environ, "LC_MESSAGES": "C"
        },
        check=False,
    )
    if res.returncode == 0:
        _write_stderr(res.stderr)
        return
    if _rejected_break_system_packages(res.stderr, res.returncode):
        subprocess.check_call(base_cmd + ["--user", dep])
        return
    _write_stderr(res.stderr)
    raise subprocess.CalledProcessError(res.returncode, cmd, stderr=res.stderr)


def main():
    print(
        "Installing dependencies for Android Emulator & Device Resource Monitor..."
    )

    try:
        dependencies = ["psutil", "GPUtil", "plotille"]
        in_venv = _in_virtualenv()

        installed = []
        failed = []

        for dep in dependencies:
            try:
                print(f"Installing: {dep}")
                _pip_install(dep, in_venv)
                installed.append(dep)
            except subprocess.CalledProcessError:
                print(f"Failed to install {dep}", file=sys.stderr)
                failed.append(dep)

        print("\nInstallation Summary:")
        print(
            f"Successfully installed: {', '.join(installed) if installed else 'None'}"
        )
        print(f"Failed to install: {', '.join(failed) if failed else 'None'}")
        if "psutil" in failed:
            print(
                "Error: Required dependency 'psutil' failed to install. "
                "Install inside a virtual environment "
                "(e.g. 'python3 -m venv .venv' and '.venv/bin/python install.py') "
                "or install your OS package (e.g. 'python3-psutil').",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            "You can now run the monitor using: python3 emulator_resource_monitor.py"
        )
    except SystemExit:
        raise
    except Exception as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
