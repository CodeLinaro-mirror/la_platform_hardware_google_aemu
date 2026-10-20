# Android Emulator & Device Resource Monitor (Rust Implementation)

This directory contains the Rust implementation of the Android Emulator & Device Resource Monitor.

A lightweight, cross-platform terminal tool to monitor resource consumption (CPU and RAM) on your host machine and all connected Android Emulators and physical devices.

## Features

*   **Real-Time Dashboard**: Live-updating terminal interface with color-coded bars.
*   **Host & Guest Monitoring**: Tracks CPU and RAM for your computer and all connected Android devices simultaneously.
*   **Per-Device QEMU Tracking**: Automatically maps host QEMU emulator processes to their corresponding guest devices for precise resource attribution.
*   **Host GPU Monitoring**: Tracks GPU load and memory usage (graceful fallback if not available).
*   **Network Simulator Tracking**: Monitors CPU and RAM usage for Netsim, including process counts.
*   **Terminal Plotting**: Displays live sparklines in the dashboard and full ASCII charts in the summary file.
*   **CSV Data Export**: Supports exporting time-series data to CSV for easy analysis.
*   **Auto-Discovery**: Automatically detects when devices are connected or disconnected.
*   **Guest Swap Memory Tracking**: Tracks guest swap memory usage extracted directly from devices via ADB.
*   **Periodic Auto-Saving**: Automatically checkpoints time-series metrics to disk to prevent data loss on long 24+ hour runs.
*   **Boot Logging**: Actively logs CPU and RAM usage for the first 5 minutes of a device's connection to help analyze boot performance.
*   **Session Summary**: Generates a detailed text report on exit, including averages, peaks, and boot logs.

## Prerequisites

*   **Rust Toolchain** (cargo, rustc)
*   **ADB (Android Debug Bridge)** installed and available in your system's PATH.

## How to Build

Build the project using Cargo from this directory:

```bash
cargo build --release
```

The binary will be generated in `target/release/`.

## How to Run

You can run it directly via Cargo from this directory:

```bash
cargo run --release -- [options]
```

Or run the compiled binary directly:

```bash
./target/release/rust [options]
```

### Options

*   `-1` or `--once`: Prints the metrics once and exits immediately. Useful for automated scripts or CI environments.
*   `--no-summary`: Disables saving the session summary text file on exit.
*   `--csv`: Exports time-series data to a CSV file on exit.
*   `-o <dir>` or `--out-dir <dir>`: Directory to save session summaries and CSV files (default: current directory).
*   `-i <seconds>` or `--interval <seconds>`: Adjustable refresh rate (default: 2.0s).
*   `--headless`: Suppresses terminal UI updates completely.
*   `--auto-save-interval <minutes>`: Sets the auto-save frequency in minutes (default: 5, use 0 to disable).

## Running Tests

Run the test suite using Cargo from this directory:

```bash
cargo test
```
