# Android Emulator & Device Resource Monitor (Python Implementation)

This directory contains the Python implementation of the Android Emulator & Device Resource Monitor.

A lightweight, cross-platform terminal tool to monitor resource consumption (CPU and RAM) on your host machine and all connected Android Emulators and physical devices.

## Features

*   **Real-Time Dashboard**: Live-updating terminal interface with color-coded bars.
*   **Host & Guest Monitoring**: Tracks CPU and RAM for your computer and all connected Android devices simultaneously.
*   **Per-Device QEMU Tracking**: Automatically maps host QEMU emulator processes to their corresponding guest devices for precise resource attribution.
*   **Host GPU Monitoring**: Tracks GPU load and memory usage (graceful fallback if not available).
*   **Network Simulator Tracking**: Monitors CPU and RAM usage for Netsim, including process counts.
*   **Terminal Plotting**: Displays live sparklines in the dashboard and full ASCII charts in the summary file.
*   **CSV Data Export**: Supports exporting time-series data to CSV for easy analysis in Google Sheets.
*   **Auto-Discovery**: Automatically detects when devices are connected or disconnected.
*   **Guest Swap Memory Tracking**: Tracks guest swap memory usage extracted directly from devices via ADB.
*   **Periodic Auto-Saving**: Automatically checkpoints time-series metrics to disk to prevent data loss on long 24+ hour runs.
*   **Boot Logging**: Actively logs CPU and RAM usage for the first 5 minutes of a device's connection to help analyze boot performance.
*   **Session Summary**: Generates a detailed text report on exit, including averages, peaks, **P95 percentiles**, and boot logs.

## Prerequisites

*   **Python 3.x**
*   **ADB (Android Debug Bridge)** installed and available in your system's PATH.
*   **Root/Sudo Privileges** (Optional): Required for monitoring Intel GPUs on Linux and any GPU on macOS due to OS-level security restrictions. NVIDIA and AMD on Linux, and all GPUs on Windows work without root.

## Installation

This tool requires `psutil`, `GPUtil`, and `plotille`. Run the provided install script from this directory:

```bash
python3 install.py
```

This will install the required dependencies for your user using `pip`.

## How to Run

Open your terminal/command prompt in this directory and run:

```bash
python3 emulator_resource_monitor.py
```

### Options

*   `--once` or `-1`: Prints the metrics once and exits immediately. Useful for automated scripts or CI environments.
*   `--no-summary`: Disables saving the session summary text file on exit.
*   `--csv`: Exports time-series data to a CSV file on exit.
*   `-o <dir>` or `--out-dir <dir>`: Directory to save session summaries and CSV files (default: current directory).
*   `-i <seconds>` or `--interval <seconds>`: Adjustable refresh rate clamped from 1.0s to 10.0s (default: 2.0s).
*   `--headless`: Suppresses terminal UI updates completely (High performance for background operations).
*   `--auto-save-interval <minutes>`: Sets the auto-save frequency in minutes (default: 5, use 0 to disable).


## Understanding the Dashboard

The dashboard is designed to be intuitive even for non-technical users:

### Colors
*   **Green**: Normal usage.
*   **Yellow**: Moderate usage, worth keeping an eye on.
*   **Red**: High usage.
*   **Red + Bold + [!!]**: Extremely high usage (above 90%), action may be needed.

### Key Metrics

#### Host System
*   **Complete System CPU**: Total CPU usage of your computer.
*   **Complete System RAM**: Total memory usage of your computer.
*   **Backup Memory (Swap) Usage**: Usage of disk space as extra memory.
*   **Memory Swapping Activity**: Rate of data moving between RAM and disk.
*   **Host GPU Load**: GPU usage percentage (if available). *Note: Intel GPUs on Linux and all GPUs on macOS require running the script with `sudo`.*
*   **Host GPU Memory**: GPU memory usage (if available).
*   **Host Thermal Temperature**: Core temperature in Celsius fetched from hardware sensors.
*   **Host Disk I/O Rate**: System wide Read/Write bandwidth in Megabytes per second (MB/s).
*   **Laptop Battery Status**: Power percentage and DC Discharging / Plugged status fetched on laptops.
*   **Network Simulator (netsim)**: CPU and RAM used by the network simulator, with process counts.
*   **Host CPU History**: Live sparkline showing CPU usage over time.

#### Guest System (Devices)
*   **Total Device CPU Usage**: Combined CPU usage across all cores of the device.
*   **Adjusted CPU Load**: Average load per CPU core.
*   **Guest RAM Usage**: Memory usage inside the Android device with absolute error logs frequency and `/data` partition disk fullness triggers.
*   **Host QEMU CPU/RAM**: Resource usage of the specific emulator process on the host, containing threads allocation (thr) and voluntary context switches (cs).
*   **Guest Swap Memory**: Tracks guest swap memory usage extracted directly from devices via ADB.

### Indicators
*   `[ONLINE]`: Appears in green for 10 seconds when a device is newly detected or reconnects.
*   `[RECORDING BOOT STATS]`: Appears in yellow when the tool is actively recording boot-time statistics for the device.
*   `[DISCONNECTED]`: Appears in red when a device disconnects. Devices will linger in the list for up to 2 minutes after disconnect (or longer if they were booting) to prevent them from disappearing immediately.

## Session Summary

When you stop the script (by pressing `Ctrl+C`), it will automatically save a summary file in the same directory with a name like `device_monitor_summary_YYYYMMDD_HHMMSS.txt`.

This file contains:
*   Session duration and total loops.
*   Host OS, CPU cores, and ADB version.
*   Average, Peak, and **P95 Percentile** CPU/RAM usage for Host, Guests, Netsim, and QEMU.
*   Full ASCII charts for Host (CPU, RAM, Swap, GPU), Netsim, and Emulator (Guest and Host QEMU) history.
*   Boot progression logs for relevant devices.

If you used the `--csv` flag, it will also generate a file like `device_monitor_data_YYYYMMDD_HHMMSS.csv` containing full time-series data.

## Running Tests

The suite includes robust unit tests in `emulator_resource_monitor_test.py` to safeguard string parsing, regular expressions, and process iterations.

Run the test suite locally using Python's standard unittest framework from this directory:

```bash
python3 emulator_resource_monitor_test.py
```

All components including ADB mocking side-effects run smoothly in independent sandbox scopes safely.
