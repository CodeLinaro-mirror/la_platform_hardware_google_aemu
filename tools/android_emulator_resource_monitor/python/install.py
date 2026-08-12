#!/usr/bin/env python3
import sys
import subprocess

print("Installing dependencies for Android Emulator & Device Resource Monitor...")

try:
    dependencies = ["psutil", "GPUtil", "plotille"]

    installed = []
    failed = []

    for dep in dependencies:
        try:
            print(f"Installing: {dep}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", "--index-url", "https://pypi.org/simple", dep])
            installed.append(dep)
        except subprocess.CalledProcessError:
            print(f"Failed to install {dep}")
            failed.append(dep)

    print("\nInstallation Summary:")
    print(f"Successfully installed: {', '.join(installed) if installed else 'None'}")
    print(f"Failed to install: {', '.join(failed) if failed else 'None'}")
    print("You can now run the monitor using: python3 emulator_resource_monitor.py")
except Exception as e:
    print(f"\nAn error occurred: {e}")
    sys.exit(1)
