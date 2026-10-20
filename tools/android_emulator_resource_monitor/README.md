# Android Emulator & Device Resource Monitor

A lightweight, cross-platform terminal tool to monitor resource consumption (CPU and RAM) on your host machine and all connected Android Emulators and physical devices.

## Implementations

This project is available in two implementations:

*   **[Python Implementation](python/README.md)**: The original implementation.
*   **[Rust Implementation](rust/README.md)**: A high-performance port in Rust.

Please refer to the respective README files in the `python/` and `rust/` directories for specific instructions on installation, building, and running.

## Key Features (Common to both)

*   **Real-Time Dashboard**: Live-updating terminal interface with color-coded bars.
*   **Host & Guest Monitoring**: Tracks CPU and RAM for your computer and all connected Android devices simultaneously.
*   **Per-Device QEMU Tracking**: Automatically maps host QEMU emulator processes to their corresponding guest devices for precise resource attribution.
*   **Host GPU Monitoring**: Tracks GPU load and memory usage (where supported).
*   **Network Simulator Tracking**: Monitors CPU and RAM usage for Netsim.
*   **Guest Swap Memory Tracking**: Tracks guest swap memory usage extracted directly from devices via ADB.
*   **CSV Data Export**: Supports exporting time-series data to CSV for easy analysis.
*   **Auto-Discovery**: Automatically detects when devices are connected or disconnected.
*   **Periodic Auto-Saving**: Automatically checkpoints time-series metrics to disk to prevent data loss on long 24+ hour runs.
*   **Session Summary**: Generates a detailed report on exit.
