use super::*;
use std::collections::HashMap;

#[test]
fn test_get_p95() {
    let mut ts = Vec::new();
    for i in 1..=100 {
        let mut entry = HashMap::new();
        entry.insert("val".to_string(), i as f32);
        ts.push(entry);
    }
    let p95 = compute_series_stats(&ts, "val").p95;
    assert_eq!(p95, 95.0);
}

#[test]
fn test_get_avg() {
    let mut ts = Vec::new();
    for i in 1..=10 {
        let mut entry = HashMap::new();
        entry.insert("val".to_string(), i as f32);
        ts.push(entry);
    }
    let avg = compute_series_stats(&ts, "val").avg;
    assert_eq!(avg, 5.5);
}

#[test]
fn test_get_max() {
    let mut ts = Vec::new();
    for i in 1..=10 {
        let mut entry = HashMap::new();
        entry.insert("val".to_string(), i as f32);
        ts.push(entry);
    }
    let max_val = compute_series_stats(&ts, "val").peak;
    assert_eq!(max_val, 10.0);
}

#[test]
fn test_export_csv() {
    let mut ts = Vec::new();
    let mut entry1 = HashMap::new();
    entry1.insert("time".to_string(), 1.0);
    entry1.insert("host_cpu".to_string(), 10.0);
    ts.push(entry1);

    let mut entry2 = HashMap::new();
    entry2.insert("time".to_string(), 2.0);
    entry2.insert("host_cpu".to_string(), 20.0);
    ts.push(entry2);

    let summary = SessionSummary {
        start_time: std::time::SystemTime::now(),
        iterations: 2,
        host_cpu_max: 20.0,
        host_ram_max: 0.0,
        time_series: ts,
        time_series_str: Vec::new(),
        boot_logs: HashMap::new(),
        device_names: HashMap::new(),
        netsim_summary_lines: Vec::new(),
    };

    let dir = tempfile::tempdir().unwrap();
    let file_path = dir.path().join("test_output.csv");
    let filename = file_path.to_str().unwrap();
    let res = export_csv(&summary, filename);
    assert!(res.is_ok());

    let content = std::fs::read_to_string(filename).unwrap();
    assert!(content.contains("time,host_cpu"));
    let start_chrono: DateTime<Local> = summary.start_time.into();
    let expected_ts1 =
        (start_chrono + chrono::Duration::seconds(1)).format("%Y-%m-%d %H:%M:%S").to_string();
    let expected_ts2 =
        (start_chrono + chrono::Duration::seconds(2)).format("%Y-%m-%d %H:%M:%S").to_string();

    assert!(content.contains(&format!("{},10", expected_ts1)));
    assert!(content.contains(&format!("{},20", expected_ts2)));
}

#[test]
fn test_get_val_percentages() {
    assert_eq!(get_val("50%", false), 50.0);
    assert_eq!(get_val("-12.5%", false), -12.5);
}

#[test]
fn test_get_val_paging() {
    assert_eq!(get_val("In: 10 / Out: 20 (pages/s)", false), 20.0);
    assert_eq!(get_val("In: 50 / Out: 10 (KB/s)", false), 50.0);
}

#[test]
fn test_get_val_fallback() {
    assert_eq!(get_val("Random text 123.45", false), 123.45);
    assert!(get_val("Error occurred", true).is_nan());
}

#[test]
fn test_get_val_allows_secondary_na_metadata_while_rejecting_leading_na_and_suffixes() {
    assert_eq!(get_val("25% (N/A used/N/A avail)", true), 25.0);
    assert!((get_val("15.2% (0.6GB/3.8GB, 12 errs, N/A data)", true) - 15.2).abs() < 1e-4);
    assert!(get_val("N/A", true).is_nan());
    assert!(get_val("  N/A (offline)", true).is_nan());
    assert!(get_val("N/A% (Plugged)", true).is_nan());
    assert!(get_val("15.2% (Timeout)", true).is_nan());
    assert!(get_val("15.2% (Error)", true).is_nan());
    assert!(get_val("Calculating...", true).is_nan());
}
#[test]
fn test_migrate_keys() {
    let mut summary = SessionSummary::new();
    let mut entry = HashMap::new();
    entry.insert("emu_cpu_tot_emulator-5554".to_string(), 10.0);
    entry.insert("emu_ram_emulator-5554".to_string(), 20.0);
    entry.insert("qemu_cpu_emulator-5554".to_string(), 30.0);
    summary.time_series.push(entry);
    summary
        .boot_logs
        .insert("emulator-5554".to_string(), vec![("+0s".to_string(), "CPU: 10%".to_string())]);

    summary.migrate_keys("emulator-5554", "AI_Glasses_emulator-5554");

    assert!(summary.time_series[0].contains_key("emu_cpu_tot_AI_Glasses_emulator-5554"));
    assert!(summary.time_series[0].contains_key("emu_ram_AI_Glasses_emulator-5554"));
    assert!(summary.time_series[0].contains_key("qemu_cpu_AI_Glasses_emulator-5554"));
    assert!(!summary.time_series[0].contains_key("emu_cpu_tot_emulator-5554"));
    assert!(!summary.time_series[0].contains_key("emu_ram_emulator-5554"));
    assert!(!summary.time_series[0].contains_key("qemu_cpu_emulator-5554"));
    assert_eq!(summary.time_series[0]["emu_cpu_tot_AI_Glasses_emulator-5554"], 10.0);
    assert_eq!(summary.time_series[0]["emu_ram_AI_Glasses_emulator-5554"], 20.0);
    assert_eq!(summary.time_series[0]["qemu_cpu_AI_Glasses_emulator-5554"], 30.0);
    assert!(summary.boot_logs.contains_key("AI_Glasses_emulator-5554"));
    assert!(!summary.boot_logs.contains_key("emulator-5554"));
}

#[test]
fn test_save_summary() {
    let mut ts = Vec::new();
    let mut entry1 = HashMap::new();
    entry1.insert("time".to_string(), 1.0);
    entry1.insert("host_cpu".to_string(), 10.0);
    ts.push(entry1);

    let summary = SessionSummary {
        start_time: std::time::SystemTime::now(),
        iterations: 1,
        host_cpu_max: 10.0,
        host_ram_max: 0.0,
        time_series: ts,
        time_series_str: Vec::new(),
        boot_logs: HashMap::new(),
        device_names: HashMap::new(),
        netsim_summary_lines: Vec::new(),
    };

    let dir = tempfile::tempdir().unwrap();
    let out_dir = dir.path().to_str().unwrap();

    save_summary(&summary, out_dir, true, true, "Mock Specs", 4, false);

    let mut csv_found = false;
    let mut txt_found = false;
    for entry in std::fs::read_dir(out_dir).unwrap() {
        let entry = entry.unwrap();
        let path = entry.path();
        if path.is_file() {
            if path.extension().and_then(|s| s.to_str()) == Some("csv") {
                csv_found = true;
            }
            if path.extension().and_then(|s| s.to_str()) == Some("txt") {
                txt_found = true;
            }
        }
    }
    assert!(csv_found);
    assert!(txt_found);
}

#[test]
fn test_save_summary_thermal_na_and_netsim_peaks() {
    let mut summary = SessionSummary::new();
    summary.iterations = 2;
    summary.host_cpu_max = 20.0;
    summary.time_series = vec![
        HashMap::from([
            ("time".to_string(), 1.0),
            ("host_cpu".to_string(), 10.0),
            ("netsim_cpu".to_string(), 5.0),
            ("netsim_ram".to_string(), 30.0),
        ]),
        HashMap::from([
            ("time".to_string(), 2.0),
            ("host_cpu".to_string(), 20.0),
            ("netsim_cpu".to_string(), 15.0),
            ("netsim_ram".to_string(), 40.0),
        ]),
    ];
    let dir = tempfile::tempdir().unwrap();
    save_summary(&summary, dir.path().to_str().unwrap(), false, true, "Mock Specs", 4, false);
    let txt_path = std::fs::read_dir(dir.path())
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| p.extension().is_some_and(|e| e == "txt"))
        .unwrap();
    let txt = std::fs::read_to_string(txt_path).unwrap();

    // No thermal sensor: N/A instead of 0.0°C.
    assert!(txt.contains("  Average Thermal:   N/A\n"), "{txt}");
    assert!(txt.contains("  Peak Thermal:      N/A\n"));
    assert!(txt.contains("  P95 Thermal:       N/A\n"));
    // NETSIM lists peaks like the Python monitor.
    assert!(txt.contains("  Peak CPU Usage:    15.0%\n"));
    assert!(txt.contains("  Peak RAM Usage:    40.0MB\n"));
}

#[test]
fn test_export_csv_column_order_is_deterministic() {
    let mut summary = SessionSummary::new();
    summary.time_series = vec![HashMap::from([
        ("qemu_cpu_A_emulator-5554".to_string(), 1.0),
        ("netsim_cpu".to_string(), 2.0),
        ("host_ram".to_string(), 3.0),
        ("time".to_string(), 0.0),
        ("host_cpu".to_string(), 4.0),
    ])];
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("out.csv");
    export_csv(&summary, path.to_str().unwrap()).unwrap();
    let header = std::fs::read_to_string(&path).unwrap().lines().next().unwrap().to_string();
    assert_eq!(header, "time,host_cpu,host_ram,netsim_cpu,qemu_cpu_A_emulator-5554");
}

#[test]
fn test_save_summary_deduplicates_emulators() {
    let mut ts = Vec::new();
    let mut entry1 = HashMap::new();
    entry1.insert("time".to_string(), 1.0);
    entry1.insert("emu_cpu_tot_Pixel_6_emulator-5554".to_string(), 5.0);
    ts.push(entry1);

    let mut device_names = HashMap::new();
    device_names.insert("Pixel_6_emulator-5554".to_string(), "Pixel_6".to_string());

    let summary = SessionSummary {
        start_time: std::time::SystemTime::now(),
        iterations: 1,
        host_cpu_max: 10.0,
        host_ram_max: 0.0,
        time_series: ts,
        time_series_str: Vec::new(),
        boot_logs: HashMap::new(),
        device_names,
        netsim_summary_lines: Vec::new(),
    };

    let dir = tempfile::tempdir().unwrap();
    let out_dir = dir.path().to_str().unwrap();

    save_summary(&summary, out_dir, false, true, "Mock Specs", 4, true);

    let txt_path = dir.path().join("device_monitor_summary_autosave.txt");
    let content = std::fs::read_to_string(txt_path).unwrap();

    let guest_mentions: Vec<&str> =
        content.lines().filter(|l| l.contains("GUEST SYSTEM (")).collect();
    assert_eq!(guest_mentions.len(), 1);
    assert!(guest_mentions[0].contains("Pixel_6"));
}

fn build_representative_summary() -> SessionSummary {
    let start_time =
        std::time::SystemTime::UNIX_EPOCH + std::time::Duration::from_secs(1_700_000_000);
    let mut device_names = HashMap::new();
    // Raw serial should be deduplicated in favor of the AVD-prefixed key.
    device_names.insert("emulator-5554".to_string(), "emulator-5554".to_string());
    device_names.insert("Pixel_6_emulator-5554".to_string(), "Pixel_6 | Pixel 6".to_string());
    device_names.insert("Watch_emulator-5556".to_string(), "Watch | Pixel Watch".to_string());

    let mut boot_logs = HashMap::new();
    boot_logs.insert(
        "Pixel_6_emulator-5554".to_string(),
        vec![
            ("+0s".to_string(), "CPU: 15.0% | RAM: 40.0%".to_string()),
            ("+2s".to_string(), "CPU: 45.0% | RAM: 55.0%".to_string()),
        ],
    );
    boot_logs.insert(
        "Watch_emulator-5556".to_string(),
        vec![
            ("+0s".to_string(), "CPU: 20.0% | RAM: 35.0%".to_string()),
            ("+3s".to_string(), "CPU: 30.0% | RAM: 42.0%".to_string()),
        ],
    );

    let mut time_series = Vec::new();
    let mut time_series_str = Vec::new();
    for i in 1..=25 {
        let t = i as f32;
        let mut map = HashMap::new();
        map.insert("time".to_string(), t);
        map.insert("host_cpu".to_string(), 10.0 + t * 2.0);
        map.insert("host_ram".to_string(), 40.0 + t * 1.5);
        map.insert("host_swap".to_string(), 100.0 + t * 4.0);
        map.insert("host_disk_read_mbs".to_string(), 5.0 + t * 0.8);
        map.insert("host_disk_write_mbs".to_string(), 2.0 + t * 0.5);
        map.insert("host_thermal_c".to_string(), 45.0 + t * 0.5);
        map.insert("host_gpu".to_string(), 8.0 + t * 1.2);
        map.insert("host_gpu_mem".to_string(), 256.0 + t * 16.0);
        map.insert("host_battery".to_string(), if i == 1 { f32::NAN } else { 100.0 - t * 0.5 });
        map.insert("netsim_cpu".to_string(), 1.5 + t * 0.4);
        map.insert("netsim_ram".to_string(), 35.0 + t * 0.8);

        map.insert("emu_cpu_tot_Pixel_6_emulator-5554".to_string(), 20.0 + t * 2.5);
        map.insert("emu_cpu_adj_Pixel_6_emulator-5554".to_string(), 10.0 + t * 1.25);
        map.insert("emu_ram_Pixel_6_emulator-5554".to_string(), 50.0 + t * 1.1);
        map.insert("emu_swap_Pixel_6_emulator-5554".to_string(), 5.0 + t * 0.6);
        map.insert("emu_disk_pct_Pixel_6_emulator-5554".to_string(), 42.0 + t * 0.2);
        map.insert("emu_errs_Pixel_6_emulator-5554".to_string(), (i % 4) as f32);
        map.insert("qemu_cpu_Pixel_6_emulator-5554".to_string(), 80.0 + t * 5.0);
        map.insert("qemu_ram_Pixel_6_emulator-5554".to_string(), 3000.0 + t * 20.0);
        map.insert("qemu_cs_Pixel_6_emulator-5554".to_string(), 1200.0 + t * 60.0);
        map.insert("qemu_gpu_Pixel_6_emulator-5554".to_string(), 10.0 + t * 0.9);

        map.insert("emu_cpu_tot_Watch_emulator-5556".to_string(), 12.0 + t * 1.4);
        map.insert("emu_cpu_adj_Watch_emulator-5556".to_string(), 6.0 + t * 0.7);
        map.insert("emu_ram_Watch_emulator-5556".to_string(), 44.0 + t * 0.8);
        map.insert("emu_swap_Watch_emulator-5556".to_string(), t * 0.3);
        map.insert("emu_disk_pct_Watch_emulator-5556".to_string(), 28.0 + t * 0.15);
        map.insert("emu_errs_Watch_emulator-5556".to_string(), (i % 2) as f32);
        map.insert("qemu_cpu_Watch_emulator-5556".to_string(), 50.0 + t * 3.0);
        map.insert("qemu_ram_Watch_emulator-5556".to_string(), 1600.0 + t * 12.0);
        map.insert("qemu_cs_Watch_emulator-5556".to_string(), 700.0 + t * 30.0);
        map.insert("qemu_gpu_Watch_emulator-5556".to_string(), 4.0 + t * 0.5);
        time_series.push(map);

        let mut smap = std::collections::BTreeMap::new();
        smap.insert(
            "netsim_state_Pixel_6_emulator-5554".to_string(),
            if i % 2 == 0 { "BLE:TX" } else { "BLE:ON" }.to_string(),
        );
        smap.insert(
            "netsim_state_Watch_emulator-5556".to_string(),
            if i % 3 == 0 { "BLE:RX" } else { "BLE:IDLE" }.to_string(),
        );
        time_series_str.push(smap);
    }

    SessionSummary {
        start_time,
        iterations: 25,
        host_cpu_max: 60.0,
        host_ram_max: 77.5,
        time_series,
        time_series_str,
        boot_logs,
        device_names,
        netsim_summary_lines: vec![
            "  Active Devices:    2".to_string(),
            "  BLE Captures:      1".to_string(),
            "".to_string(),
        ],
    }
}

fn normalize_summary_text(raw: &str) -> String {
    let mut lines: Vec<String> = Vec::new();
    let raw_lines: Vec<&str> = raw.lines().collect();
    let mut i = 0;
    while i < raw_lines.len() {
        let line = raw_lines[i];
        if line.starts_with("Start Time:  ") {
            lines.push("Start Time:  <START_TIME>".to_string());
            i += 1;
        } else if line.starts_with("End Time:    ") {
            lines.push("End Time:    <END_TIME>".to_string());
            i += 1;
        } else if line.starts_with("Duration:    ") {
            assert!(line.ends_with(" seconds"), "unexpected duration line: {line}");
            lines.push("Duration:    <DURATION>".to_string());
            i += 1;
        } else if line.starts_with("Host OS:      ") {
            lines.push("Host OS:      <HOST_OS>".to_string());
            i += 1;
        } else if line.starts_with("Host Virtual: ") {
            lines.push("Host Virtual: <HOST_VIRTUAL>".to_string());
            i += 1;
        } else if line.starts_with("ADB Version:  ") {
            lines.push("ADB Version:  <ADB_VERSION>".to_string());
            i += 1;
        } else {
            lines.push(line.to_string());
            i += 1;
        }
    }
    lines.join("\n")
}

const EXPECTED_SUMMARY_TXT: &str = r##"Android Emulator & Device Resource Monitor Session Summary
============================================================
Start Time:  <START_TIME>
End Time:    <END_TIME>
Duration:    <DURATION>
Total Loops: 25
Host OS:      <HOST_OS>
Host Specs:   Mock CPU 8-Core
Host Cores:   8
Host Virtual: <HOST_VIRTUAL>
ADB Version:  <ADB_VERSION>

HOST SYSTEM:
  Average CPU Usage: 36.0%
  Peak CPU Usage:    60.0%
  P95 CPU Usage:     58.0%
  Average RAM Usage: 59.5%
  Peak RAM Usage:    77.5%
  P95 RAM Usage:     76.0%
  Average Thermal:   51.5°C
  Peak Thermal:      57.5°C
  P95 Thermal:       57.0°C
  Avg Disk Read:     15.4 MB/s
  Peak Disk Read:    25.0 MB/s
  P95 Disk Read:     24.2 MB/s
  Avg Disk Write:    8.5 MB/s
  Peak Disk Write:   14.5 MB/s
  P95 Disk Write:    14.0 MB/s

NETSIM:
  Average CPU Usage: 6.7%
  Peak CPU Usage:    11.5%
  P95 CPU Usage:     11.1%
  Average RAM Usage: 45.4MB
  Peak RAM Usage:    55.0MB
  P95 RAM Usage:     54.2MB

  Active Devices:    2
  BLE Captures:      1

GUEST SYSTEM (Pixel_6 | Pixel 6):
  Average Total CPU:    52.5%
  Peak Total CPU:       82.5%
  P95 Total CPU:        80.0%
  Average Adjusted CPU: 26.2%
  Peak Adjusted CPU:    41.2%
  P95 Adjusted CPU:     40.0%
  Average RAM Usage:    64.3%
  Peak RAM Usage:       77.5%
  P95 RAM Usage:        76.4%
  Average Swap Usage:   12.8MB
  Peak Swap Usage:      20.0MB
  P95 Swap Usage:       19.4MB
  Host QEMU CPU (Avg): 145.0%
  Host QEMU CPU (Max): 205.0%
  Host QEMU CPU (P95): 200.0%
  Host QEMU RAM (Avg): 3260.0MB
  Host QEMU RAM (Max): 3500.0MB
  Host QEMU RAM (P95): 3480.0MB
  Boot Progression Log:
    +0s | CPU: 15.0% | RAM: 40.0%
    +2s | CPU: 45.0% | RAM: 55.0%

GUEST SYSTEM (Watch | Pixel Watch):
  Average Total CPU:    30.2%
  Peak Total CPU:       47.0%
  P95 Total CPU:        45.6%
  Average Adjusted CPU: 15.1%
  Peak Adjusted CPU:    23.5%
  P95 Adjusted CPU:     22.8%
  Average RAM Usage:    54.4%
  Peak RAM Usage:       64.0%
  P95 RAM Usage:        63.2%
  Average Swap Usage:   3.9MB
  Peak Swap Usage:      7.5MB
  P95 Swap Usage:       7.2MB
  Host QEMU CPU (Avg): 89.0%
  Host QEMU CPU (Max): 125.0%
  Host QEMU CPU (P95): 122.0%
  Host QEMU RAM (Avg): 1756.0MB
  Host QEMU RAM (Max): 1900.0MB
  Host QEMU RAM (P95): 1888.0MB
  Boot Progression Log:
    +0s | CPU: 20.0% | RAM: 35.0%
    +3s | CPU: 30.0% | RAM: 42.0%

HOST CPU HISTORY CHART:
(Usage (%)) ^
     60.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     55.2000008 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     50.4000015 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     45.5999985 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     40.7999992 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     36.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     31.1999989 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     26.3999977 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     21.5999985 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     16.7999992 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST RAM HISTORY CHART:
(Usage (%)) ^
     77.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     73.9000015 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     70.3000031 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     66.6999969 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     63.0999985 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     59.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     55.9000015 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     52.3000031 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     48.7000008 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     45.1000023 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST SWAP HISTORY CHART:
(Usage (%)) ^
    200.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
    190.3999939 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
    180.8000031 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    171.1999969 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    161.6000061 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    152.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    142.3999939 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    132.7999878 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    123.1999969 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    113.5999985 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST DISK READ HISTORY CHART:
(Read (MB/s)) ^
     25.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     23.0799999 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     21.1599998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     19.2399998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     17.3199997 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     15.3999996 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     13.4799995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     11.5599995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      9.6399994 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      7.7199993 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST DISK WRITE HISTORY CHART:
(Write (MB/s)) ^
     14.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     13.3000002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     12.1000004 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     10.8999996 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      9.6999998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      8.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      7.2999997 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      6.0999994 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      4.8999996 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      3.6999998 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

NETSIM CPU HISTORY CHART:
(Usage (%)) ^
     11.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     10.5400000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
      9.5799999 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      8.6199999 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      7.6599998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      6.6999998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      5.7399998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      4.7799997 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      3.8199997 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      2.8599997 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

NETSIM RAM HISTORY CHART:
(MB) ^
     55.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     53.0800018 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     51.1599998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     49.2399979 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     47.3199997 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     45.4000015 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     43.4799995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     41.5599976 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     39.6399994 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     37.7200012 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST THERMAL HISTORY CHART:
(Temp (°C)) ^
     57.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     56.2999992 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     55.0999985 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     53.9000015 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     52.7000008 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     51.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     50.2999992 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     49.0999985 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     47.9000015 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     46.7000008 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST GPU LOAD HISTORY CHART:
(Usage (%)) ^
     38.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     35.1199989 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     32.2400017 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     29.3600006 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     26.4799995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     23.6000004 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     20.7200012 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     17.8400002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     14.9600010 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     12.0800018 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST GPU MEMORY HISTORY CHART:
(Usage/MB) ^
    656.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
    617.5999756 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
    579.2000122 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    540.7999878 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    502.3999939 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    464.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    425.5999756 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    387.1999817 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    348.7999878 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    310.3999939 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25



================================================================================
CHARTS FOR: Pixel_6 | Pixel 6
================================================================================


GUEST CPU HISTORY (Pixel_6_emulator-5554):
(Usage (%)) ^
     82.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     76.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     70.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     64.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     58.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     52.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     46.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     40.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     34.5000000 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     28.5000000 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST RAM HISTORY (Pixel_6_emulator-5554):
(Usage (%)) ^
     77.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     74.8600006 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     72.2200012 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     69.5800018 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     66.9400024 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     64.3000031 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     61.6599998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     59.0199966 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     56.3799973 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     53.7399979 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST SWAP MEMORY HISTORY (Pixel_6_emulator-5554):
(MB) ^
     20.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     18.5599995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     17.1200008 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     15.6800003 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     14.2399998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     12.8000002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     11.3600006 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      9.9200001 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      8.4800005 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      7.0400009 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST QEMU CPU HISTORY (Pixel_6_emulator-5554):
(Usage (%)) ^
    205.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
    193.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
    181.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    169.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    157.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    145.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    133.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    121.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    109.0000000 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     97.0000000 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST QEMU RAM HISTORY (Pixel_6_emulator-5554):
(MB) ^
   3500.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
   3452.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
   3404.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   3356.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   3308.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   3260.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   3212.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   3164.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   3116.0000000 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   3068.0000000 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST DATA PARTITION FULLNESS (Pixel_6_emulator-5554):
(Usage (%)) ^
     47.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     46.5200005 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     46.0400009 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     45.5600014 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     45.0800018 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     44.5999985 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     44.1199989 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠔⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     43.6399994 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     43.1599998 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     42.6800003 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST LOGCAT ERRORS (Pixel_6_emulator-5554):
(Errors) ^
      3.0000000 | ⡇⠀⠀⠀⠀⣼⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⠀⠀⠀⠀⠀
      2.7000000 | ⡇⠀⠀⠀⡸⠀⡇⠀⠀⠀⠀⠀⠀⠀⡸⠀⡇⠀⠀⠀⠀⠀⠀⠀⡸⠀⡇⠀⠀⠀⠀⠀⠀⠀⡸⠀⡇⠀⠀⠀⠀⠀⠀⠀⡸⠀⡇⠀⠀⠀⠀⠀⠀⠀⡸⠀⡇⠀⠀⠀⠀
      2.4000001 | ⡇⠀⠀⢠⠃⠀⡇⠀⠀⠀⠀⠀⠀⢠⠃⠀⡇⠀⠀⠀⠀⠀⠀⢠⠃⠀⡇⠀⠀⠀⠀⠀⠀⢠⠃⠀⡇⠀⠀⠀⠀⠀⠀⢠⠃⠀⡇⠀⠀⠀⠀⠀⠀⢠⠃⠀⡇⠀⠀⠀⠀
      2.0999999 | ⡇⠀⢀⠇⠀⠀⢸⠀⠀⠀⠀⠀⢀⠇⠀⠀⢸⠀⠀⠀⠀⠀⢀⠇⠀⠀⢸⠀⠀⠀⠀⠀⢀⠇⠀⠀⢸⠀⠀⠀⠀⠀⢀⠇⠀⠀⢸⠀⠀⠀⠀⠀⢀⠇⠀⠀⢸⠀⠀⠀⠀
      1.8000000 | ⡇⠀⡜⠀⠀⠀⢸⠀⠀⠀⠀⠀⡎⠀⠀⠀⢸⠀⠀⠀⠀⠀⡎⠀⠀⠀⢸⠀⠀⠀⠀⠀⡎⠀⠀⠀⢸⠀⠀⠀⠀⠀⡎⠀⠀⠀⢸⠀⠀⠀⠀⠀⡎⠀⠀⠀⢸⠀⠀⠀⠀
      1.5000000 | ⡇⢠⠃⠀⠀⠀⠀⡇⠀⠀⠀⡸⠀⠀⠀⠀⠀⡇⠀⠀⠀⡸⠀⠀⠀⠀⠀⡇⠀⠀⠀⡸⠀⠀⠀⠀⠀⡇⠀⠀⠀⡸⠀⠀⠀⠀⠀⡇⠀⠀⠀⡸⠀⠀⠀⠀⠀⡇⠀⠀⠀
      1.1999999 | ⡇⠎⠀⠀⠀⠀⠀⡇⠀⠀⢰⠁⠀⠀⠀⠀⠀⡇⠀⠀⢰⠁⠀⠀⠀⠀⠀⡇⠀⠀⢰⠁⠀⠀⠀⠀⠀⡇⠀⠀⢰⠁⠀⠀⠀⠀⠀⡇⠀⠀⢰⠁⠀⠀⠀⠀⠀⡇⠀⠀⢠
      0.8999999 | ⡇⠀⠀⠀⠀⠀⠀⢸⠀⢠⠃⠀⠀⠀⠀⠀⠀⢸⠀⢠⠃⠀⠀⠀⠀⠀⠀⢸⠀⢠⠃⠀⠀⠀⠀⠀⠀⢸⠀⢠⠃⠀⠀⠀⠀⠀⠀⢸⠀⢠⠃⠀⠀⠀⠀⠀⠀⢸⠀⢠⠃
      0.5999999 | ⡇⠀⠀⠀⠀⠀⠀⢸⠀⡎⠀⠀⠀⠀⠀⠀⠀⢸⠀⡎⠀⠀⠀⠀⠀⠀⠀⢸⠀⡎⠀⠀⠀⠀⠀⠀⠀⢸⠀⡎⠀⠀⠀⠀⠀⠀⠀⢸⠀⡎⠀⠀⠀⠀⠀⠀⠀⢸⠀⡎⠀
      0.3000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⡟⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

QEMU CONTEXT SWITCHES (Pixel_6_emulator-5554):
(Switches) ^
   2700.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
   2556.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
   2412.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   2268.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   2124.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1980.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1836.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1692.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1548.0000000 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1404.0000000 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST QEMU GPU LOAD HISTORY (Pixel_6_emulator-5554):
(Usage (%)) ^
     32.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     30.3400002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     28.1800003 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     26.0200005 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     23.8600006 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     21.7000008 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     19.5399990 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠔⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     17.3799992 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     15.2199993 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     13.0599995 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25



================================================================================
CHARTS FOR: Watch | Pixel Watch
================================================================================


GUEST CPU HISTORY (Watch_emulator-5556):
(Usage (%)) ^
     47.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     43.6399994 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     40.2799988 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     36.9199982 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     33.5600014 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     30.2000008 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     26.8400002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     23.4800014 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     20.1200008 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     16.7600002 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST RAM HISTORY (Watch_emulator-5556):
(Usage (%)) ^
     64.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     62.0800018 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     60.1599998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     58.2399979 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     56.3199997 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     54.4000015 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     52.4799995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     50.5599976 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     48.6399994 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     46.7200012 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST SWAP MEMORY HISTORY (Watch_emulator-5556):
(MB) ^
      7.5000005 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
      6.7800007 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
      6.0600004 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      5.3400002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      4.6200004 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      3.9000003 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      3.1800003 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      2.4600005 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      1.7400002 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      1.0200000 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST QEMU CPU HISTORY (Watch_emulator-5556):
(Usage (%)) ^
    125.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
    117.8000031 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
    110.5999985 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    103.4000015 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     96.1999969 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     89.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     81.8000031 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     74.6000061 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     67.4000015 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     60.2000046 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST QEMU RAM HISTORY (Watch_emulator-5556):
(MB) ^
   1900.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
   1871.1999512 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
   1842.4000244 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1813.5999756 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1784.8000488 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1756.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1727.1999512 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1698.4000244 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1669.5999756 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1640.8000488 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST DATA PARTITION FULLNESS (Watch_emulator-5556):
(Usage (%)) ^
     31.7500000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     31.3899994 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     31.0300007 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     30.6700001 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     30.3099995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     29.9500008 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     29.5900002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     29.2299995 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     28.8699989 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     28.5100002 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

GUEST LOGCAT ERRORS (Watch_emulator-5556):
(Errors) ^
      1.0000000 | ⡇⡇⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸⠀⠀⠀⠀⢸
      0.9000000 | ⡇⢱⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇
      0.8000000 | ⡇⢸⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇⡇⠀⠀⠀⡇
      0.7000000 | ⡇⠸⡀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀
      0.6000000 | ⡇⠀⡇⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀⢸⠀⠀⢸⠀
      0.5000000 | ⡇⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀
      0.4000000 | ⡇⠀⢱⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀⠀⡇⠀⡇⠀
      0.3000000 | ⡇⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀
      0.2000000 | ⡇⠀⠸⣸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀⠀⢸⢸⠀⠀
      0.1000000 | ⡇⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀⠀⠀⡇⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

QEMU CONTEXT SWITCHES (Watch_emulator-5556):
(Switches) ^
   1450.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
   1378.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
   1306.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1234.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1162.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1090.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   1018.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    946.0000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    874.0000000 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
    802.0000000 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25

HOST QEMU GPU LOAD HISTORY (Watch_emulator-5556):
(Usage (%)) ^
     16.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠒⠉
     15.3000002 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡠⠔⠒⠊⠉⠁⠀⠀⠀⠀
     14.1000004 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     12.8999996 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠤⠒⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     11.6999998 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡠⠤⠔⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
     10.5000000 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠤⠤⠒⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      9.2999992 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠤⠤⠒⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      8.0999994 | ⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡠⠤⠔⠊⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      6.8999996 | ⡇⠀⠀⠀⠀⠀⠀⣀⣀⠤⠒⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
      5.6999998 | ⡇⣀⠤⠤⠒⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
----------------|-|---------|---------|---------|---------|---------|---------|-> (Time (s))
                | 1         5         9         13        17        21        25"##;

const EXPECTED_SUMMARY_CSV: &str = r##"time,host_cpu,host_ram,netsim_cpu,netsim_ram,host_gpu,host_gpu_mem,host_swap,host_thermal_c,host_disk_read_mbs,host_disk_write_mbs,host_battery,netsim_state_Pixel_6_emulator-5554,emu_cpu_tot_Pixel_6_emulator-5554,emu_cpu_adj_Pixel_6_emulator-5554,emu_ram_Pixel_6_emulator-5554,emu_swap_Pixel_6_emulator-5554,emu_errs_Pixel_6_emulator-5554,emu_disk_pct_Pixel_6_emulator-5554,qemu_cpu_Pixel_6_emulator-5554,qemu_ram_Pixel_6_emulator-5554,qemu_cs_Pixel_6_emulator-5554,qemu_gpu_Pixel_6_emulator-5554,netsim_state_Watch_emulator-5556,emu_cpu_tot_Watch_emulator-5556,emu_cpu_adj_Watch_emulator-5556,emu_ram_Watch_emulator-5556,emu_swap_Watch_emulator-5556,emu_errs_Watch_emulator-5556,emu_disk_pct_Watch_emulator-5556,qemu_cpu_Watch_emulator-5556,qemu_ram_Watch_emulator-5556,qemu_cs_Watch_emulator-5556,qemu_gpu_Watch_emulator-5556
2023-11-14 22:13:21,12.0,41.5,1.9,35.8,9.2,272.0,104.0,45.5,5.8,2.5,,BLE:ON,22.5,11.2,51.1,5.6,1.0,42.2,85.0,3020.0,1260.0,10.9,BLE:IDLE,13.4,6.7,44.8,0.3,1.0,28.1,53.0,1612.0,730.0,4.5
2023-11-14 22:13:22,14.0,43.0,2.3,36.6,10.4,288.0,108.0,46.0,6.6,3.0,99.0,BLE:TX,25.0,12.5,52.2,6.2,2.0,42.4,90.0,3040.0,1320.0,11.8,BLE:IDLE,14.8,7.4,45.6,0.6,0.0,28.3,56.0,1624.0,760.0,5.0
2023-11-14 22:13:23,16.0,44.5,2.7,37.4,11.6,304.0,112.0,46.5,7.4,3.5,98.5,BLE:ON,27.5,13.8,53.3,6.8,3.0,42.6,95.0,3060.0,1380.0,12.7,BLE:RX,16.2,8.1,46.4,0.9,1.0,28.5,59.0,1636.0,790.0,5.5
2023-11-14 22:13:24,18.0,46.0,3.1,38.2,12.8,320.0,116.0,47.0,8.2,4.0,98.0,BLE:TX,30.0,15.0,54.4,7.4,0.0,42.8,100.0,3080.0,1440.0,13.6,BLE:IDLE,17.6,8.8,47.2,1.2,0.0,28.6,62.0,1648.0,820.0,6.0
2023-11-14 22:13:25,20.0,47.5,3.5,39.0,14.0,336.0,120.0,47.5,9.0,4.5,97.5,BLE:ON,32.5,16.2,55.5,8.0,1.0,43.0,105.0,3100.0,1500.0,14.5,BLE:IDLE,19.0,9.5,48.0,1.5,1.0,28.8,65.0,1660.0,850.0,6.5
2023-11-14 22:13:26,22.0,49.0,3.9,39.8,15.2,352.0,124.0,48.0,9.8,5.0,97.0,BLE:TX,35.0,17.5,56.6,8.6,2.0,43.2,110.0,3120.0,1560.0,15.4,BLE:RX,20.4,10.2,48.8,1.8,0.0,28.9,68.0,1672.0,880.0,7.0
2023-11-14 22:13:27,24.0,50.5,4.3,40.6,16.4,368.0,128.0,48.5,10.6,5.5,96.5,BLE:ON,37.5,18.8,57.7,9.2,3.0,43.4,115.0,3140.0,1620.0,16.3,BLE:IDLE,21.8,10.9,49.6,2.1,1.0,29.0,71.0,1684.0,910.0,7.5
2023-11-14 22:13:28,26.0,52.0,4.7,41.4,17.6,384.0,132.0,49.0,11.4,6.0,96.0,BLE:TX,40.0,20.0,58.8,9.8,0.0,43.6,120.0,3160.0,1680.0,17.2,BLE:IDLE,23.2,11.6,50.4,2.4,0.0,29.2,74.0,1696.0,940.0,8.0
2023-11-14 22:13:29,28.0,53.5,5.1,42.2,18.8,400.0,136.0,49.5,12.2,6.5,95.5,BLE:ON,42.5,21.2,59.9,10.4,1.0,43.8,125.0,3180.0,1740.0,18.1,BLE:RX,24.6,12.3,51.2,2.7,1.0,29.4,77.0,1708.0,970.0,8.5
2023-11-14 22:13:30,30.0,55.0,5.5,43.0,20.0,416.0,140.0,50.0,13.0,7.0,95.0,BLE:TX,45.0,22.5,61.0,11.0,2.0,44.0,130.0,3200.0,1800.0,19.0,BLE:IDLE,26.0,13.0,52.0,3.0,0.0,29.5,80.0,1720.0,1000.0,9.0
2023-11-14 22:13:31,32.0,56.5,5.9,43.8,21.2,432.0,144.0,50.5,13.8,7.5,94.5,BLE:ON,47.5,23.8,62.1,11.6,3.0,44.2,135.0,3220.0,1860.0,19.9,BLE:IDLE,27.4,13.7,52.8,3.3,1.0,29.6,83.0,1732.0,1030.0,9.5
2023-11-14 22:13:32,34.0,58.0,6.3,44.6,22.4,448.0,148.0,51.0,14.6,8.0,94.0,BLE:TX,50.0,25.0,63.2,12.2,0.0,44.4,140.0,3240.0,1920.0,20.8,BLE:RX,28.8,14.4,53.6,3.6,0.0,29.8,86.0,1744.0,1060.0,10.0
2023-11-14 22:13:33,36.0,59.5,6.7,45.4,23.6,464.0,152.0,51.5,15.4,8.5,93.5,BLE:ON,52.5,26.2,64.3,12.8,1.0,44.6,145.0,3260.0,1980.0,21.7,BLE:IDLE,30.2,15.1,54.4,3.9,1.0,30.0,89.0,1756.0,1090.0,10.5
2023-11-14 22:13:34,38.0,61.0,7.1,46.2,24.8,480.0,156.0,52.0,16.2,9.0,93.0,BLE:TX,55.0,27.5,65.4,13.4,2.0,44.8,150.0,3280.0,2040.0,22.6,BLE:IDLE,31.6,15.8,55.2,4.2,0.0,30.1,92.0,1768.0,1120.0,11.0
2023-11-14 22:13:35,40.0,62.5,7.5,47.0,26.0,496.0,160.0,52.5,17.0,9.5,92.5,BLE:ON,57.5,28.8,66.5,14.0,3.0,45.0,155.0,3300.0,2100.0,23.5,BLE:RX,33.0,16.5,56.0,4.5,1.0,30.2,95.0,1780.0,1150.0,11.5
2023-11-14 22:13:36,42.0,64.0,7.9,47.8,27.2,512.0,164.0,53.0,17.8,10.0,92.0,BLE:TX,60.0,30.0,67.6,14.6,0.0,45.2,160.0,3320.0,2160.0,24.4,BLE:IDLE,34.4,17.2,56.8,4.8,0.0,30.4,98.0,1792.0,1180.0,12.0
2023-11-14 22:13:37,44.0,65.5,8.3,48.6,28.4,528.0,168.0,53.5,18.6,10.5,91.5,BLE:ON,62.5,31.2,68.7,15.2,1.0,45.4,165.0,3340.0,2220.0,25.3,BLE:IDLE,35.8,17.9,57.6,5.1,1.0,30.5,101.0,1804.0,1210.0,12.5
2023-11-14 22:13:38,46.0,67.0,8.7,49.4,29.6,544.0,172.0,54.0,19.4,11.0,91.0,BLE:TX,65.0,32.5,69.8,15.8,2.0,45.6,170.0,3360.0,2280.0,26.2,BLE:RX,37.2,18.6,58.4,5.4,0.0,30.7,104.0,1816.0,1240.0,13.0
2023-11-14 22:13:39,48.0,68.5,9.1,50.2,30.8,560.0,176.0,54.5,20.2,11.5,90.5,BLE:ON,67.5,33.8,70.9,16.4,3.0,45.8,175.0,3380.0,2340.0,27.1,BLE:IDLE,38.6,19.3,59.2,5.7,1.0,30.9,107.0,1828.0,1270.0,13.5
2023-11-14 22:13:40,50.0,70.0,9.5,51.0,32.0,576.0,180.0,55.0,21.0,12.0,90.0,BLE:TX,70.0,35.0,72.0,17.0,0.0,46.0,180.0,3400.0,2400.0,28.0,BLE:IDLE,40.0,20.0,60.0,6.0,0.0,31.0,110.0,1840.0,1300.0,14.0
2023-11-14 22:13:41,52.0,71.5,9.9,51.8,33.2,592.0,184.0,55.5,21.8,12.5,89.5,BLE:ON,72.5,36.2,73.1,17.6,1.0,46.2,185.0,3420.0,2460.0,28.9,BLE:RX,41.4,20.7,60.8,6.3,1.0,31.1,113.0,1852.0,1330.0,14.5
2023-11-14 22:13:42,54.0,73.0,10.3,52.6,34.4,608.0,188.0,56.0,22.6,13.0,89.0,BLE:TX,75.0,37.5,74.2,18.2,2.0,46.4,190.0,3440.0,2520.0,29.8,BLE:IDLE,42.8,21.4,61.6,6.6,0.0,31.3,116.0,1864.0,1360.0,15.0
2023-11-14 22:13:43,56.0,74.5,10.7,53.4,35.6,624.0,192.0,56.5,23.4,13.5,88.5,BLE:ON,77.5,38.8,75.3,18.8,3.0,46.6,195.0,3460.0,2580.0,30.7,BLE:IDLE,44.2,22.1,62.4,6.9,1.0,31.5,119.0,1876.0,1390.0,15.5
2023-11-14 22:13:44,58.0,76.0,11.1,54.2,36.8,640.0,196.0,57.0,24.2,14.0,88.0,BLE:TX,80.0,40.0,76.4,19.4,0.0,46.8,200.0,3480.0,2640.0,31.6,BLE:RX,45.6,22.8,63.2,7.2,0.0,31.6,122.0,1888.0,1420.0,16.0
2023-11-14 22:13:45,60.0,77.5,11.5,55.0,38.0,656.0,200.0,57.5,25.0,14.5,87.5,BLE:ON,82.5,41.2,77.5,20.0,1.0,47.0,205.0,3500.0,2700.0,32.5,BLE:IDLE,47.0,23.5,64.0,7.5,1.0,31.8,125.0,1900.0,1450.0,16.5
"##;

#[test]
fn test_save_summary_and_export_csv_pin_current_output() {
    let summary = build_representative_summary();
    let dir = tempfile::tempdir().unwrap();
    let out_dir = dir.path().to_str().unwrap();

    save_summary(&summary, out_dir, true, true, "Mock CPU 8-Core", 8, true);

    let txt =
        std::fs::read_to_string(dir.path().join("device_monitor_summary_autosave.txt")).unwrap();
    let csv =
        std::fs::read_to_string(dir.path().join("device_monitor_summary_autosave.csv")).unwrap();

    let start_local: DateTime<Local> = summary.start_time.into();
    let expected_csv: String = EXPECTED_SUMMARY_CSV
        .lines()
        .enumerate()
        .map(|(idx, line)| {
            if idx == 0 {
                line.to_string()
            } else {
                let (_, rest) = line.split_once(',').unwrap();
                let ts = (start_local + chrono::Duration::seconds(idx as i64))
                    .format("%Y-%m-%d %H:%M:%S");
                format!("{ts},{rest}")
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";

    // Chart rows end in padding spaces; compare without them so the fixture
    // carries no trailing whitespace.
    let trimmed =
        normalize_summary_text(&txt).lines().map(str::trim_end).collect::<Vec<_>>().join("\n");
    assert_eq!(trimmed, EXPECTED_SUMMARY_TXT);
    assert_eq!(csv, expected_csv);

    let direct_csv_path = dir.path().join("direct.csv");
    export_csv(&summary, direct_csv_path.to_str().unwrap()).unwrap();
    let direct_csv = std::fs::read_to_string(direct_csv_path).unwrap();
    assert_eq!(direct_csv, expected_csv);
}

#[test]
fn test_compute_series_stats_handles_nan_and_empty() {
    let empty: Vec<HashMap<String, f32>> = Vec::new();
    assert_eq!(compute_series_stats(&empty, "missing"), SeriesStats::default());

    let mut ts = Vec::new();
    for i in 1..=20 {
        ts.push(HashMap::from([("v".to_string(), i as f32)]));
    }
    ts.push(HashMap::from([("v".to_string(), f32::NAN)]));

    let stats = compute_series_stats(&ts, "v");
    assert_eq!(stats.avg, 10.5);
    assert_eq!(stats.peak, 20.0);
    assert_eq!(stats.p95, 19.0);
}

#[test]
fn test_section_builders() {
    let summary = build_representative_summary();
    let emulators = deduplicated_emulators(&summary);
    assert_eq!(
        emulators,
        vec!["Pixel_6_emulator-5554".to_string(), "Watch_emulator-5556".to_string()]
    );

    let host_lines = build_host_section(&summary);
    assert_eq!(host_lines[0], "HOST SYSTEM:");
    assert!(host_lines.iter().any(|l| l == "  Peak CPU Usage:    60.0%"));

    let netsim_lines = build_netsim_section(&summary);
    assert_eq!(netsim_lines[0], "NETSIM:");
    assert!(netsim_lines.iter().any(|l| l == "  Active Devices:    2"));

    let guest_lines = build_guests_section(&summary, &emulators);
    assert!(guest_lines.iter().any(|l| l == "GUEST SYSTEM (Pixel_6 | Pixel 6):"));
    assert!(guest_lines.iter().any(|l| l == "GUEST SYSTEM (Watch | Pixel Watch):"));
    assert!(guest_lines.iter().any(|l| l == "  Boot Progression Log:"));
    assert!(guest_lines.iter().any(|l| l == "    +0s | CPU: 15.0% | RAM: 40.0%"));
    assert!(guest_lines.iter().any(|l| l == "    +3s | CPU: 30.0% | RAM: 42.0%"));

    let chart_lines = build_charts_section(&summary, &emulators);
    assert!(chart_lines.iter().any(|l| l.starts_with("HOST CPU HISTORY CHART:")));
    assert!(chart_lines.iter().any(|l| l == "CHARTS FOR: Pixel_6 | Pixel 6"));
    assert!(chart_lines.iter().any(|l| l == "CHARTS FOR: Watch | Pixel Watch"));
}

#[test]
fn test_get_chart_str_skips_nan_samples() {
    let mut summary = SessionSummary::new();
    summary.time_series = vec![
        HashMap::from([("time".to_string(), 1.0), ("netsim_cpu".to_string(), f32::NAN)]),
        HashMap::from([("time".to_string(), 2.0), ("netsim_cpu".to_string(), f32::NAN)]),
    ];
    assert!(get_chart_str(&summary, "netsim_cpu", "NETSIM CPU HISTORY CHART").is_none());

    summary
        .time_series
        .push(HashMap::from([("time".to_string(), 3.0), ("netsim_cpu".to_string(), 10.0)]));
    assert!(get_chart_str(&summary, "netsim_cpu", "NETSIM CPU HISTORY CHART").is_none());

    summary
        .time_series
        .push(HashMap::from([("time".to_string(), 4.0), ("netsim_cpu".to_string(), 20.0)]));
    let chart = get_chart_str(&summary, "netsim_cpu", "NETSIM CPU HISTORY CHART").unwrap();
    assert!(!chart.contains("NaN"));
}

#[test]
fn test_save_summary_and_export_csv_create_missing_directories() {
    let mut summary = SessionSummary::new();
    summary.iterations = 1;
    summary.time_series =
        vec![HashMap::from([("time".to_string(), 1.0), ("host_cpu".to_string(), 10.0)])];
    let dir = tempfile::tempdir().unwrap();
    let nested = dir.path().join("nested").join("summary_out");
    save_summary(&summary, nested.to_str().unwrap(), true, true, "Mock Specs", 4, true);
    assert!(nested.join("device_monitor_summary_autosave.txt").exists());
    assert!(nested.join("device_monitor_summary_autosave.csv").exists());

    let direct_csv = dir.path().join("other_nested").join("out.csv");
    export_csv(&summary, direct_csv.to_str().unwrap()).unwrap();
    assert!(direct_csv.exists());
}

#[test]
fn test_migrate_keys_preserves_existing_values_and_export_csv_handles_invalid_time() {
    let mut summary = SessionSummary::new();
    summary.time_series.push(HashMap::from([
        ("time".to_string(), -1.0),
        ("emu_cpu_tot_Pixel_6_emulator-5554".to_string(), 55.0),
        ("emu_cpu_tot_emulator-5554".to_string(), 0.0),
    ]));
    summary.device_names.insert("emulator-5554".to_string(), "emulator-5554".to_string());

    summary.migrate_keys("emulator-5554", "Pixel_6_emulator-5554");
    assert_eq!(summary.time_series[0]["emu_cpu_tot_Pixel_6_emulator-5554"], 55.0);
    assert!(!summary.device_names.contains_key("emulator-5554"));
    assert_eq!(
        summary.device_names.get("Pixel_6_emulator-5554").map(String::as_str),
        Some("emulator-5554")
    );

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("out.csv");
    assert!(export_csv(&summary, path.to_str().unwrap()).is_ok());
}

#[test]
fn test_parse_adb_version_output_rejects_failed_or_missing_command() {
    assert_eq!(parse_adb_version_output(None), "N/A");
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        let failed = std::process::Output {
            status: std::process::ExitStatus::from_raw(256),
            stdout: b"adb: error\n".to_vec(),
            stderr: Vec::new(),
        };
        assert_eq!(parse_adb_version_output(Some(failed)), "N/A");
    }
}

#[test]
fn test_guest_section_nests_boot_progression_log() {
    let summary = build_representative_summary();
    let emulators = deduplicated_emulators(&summary);
    let guest_lines = build_guests_section(&summary, &emulators);
    let joined = guest_lines.join("\n");
    assert!(joined.contains(
        "  Host QEMU RAM (P95): 3480.0MB\n  Boot Progression Log:\n    +0s | CPU: 15.0% | RAM: 40.0%\n    +2s | CPU: 45.0% | RAM: 55.0%\n"
    ));
    assert!(joined.contains(
        "  Host QEMU RAM (P95): 1888.0MB\n  Boot Progression Log:\n    +0s | CPU: 20.0% | RAM: 35.0%\n    +3s | CPU: 30.0% | RAM: 42.0%\n"
    ));
}

#[test]
fn test_migrate_keys_replaces_nan_and_empty_placeholders() {
    let mut summary = SessionSummary::new();
    summary.time_series.push(HashMap::from([
        ("time".to_string(), 1.0),
        ("emu_cpu_tot_Pixel_7_emulator-5554".to_string(), f32::NAN),
        ("emu_cpu_tot_emulator-5554".to_string(), 25.0),
        ("qemu_cpu_Pixel_7_emulator-5554".to_string(), 80.0),
        ("qemu_cpu_emulator-5554".to_string(), 10.0),
    ]));
    summary.time_series_str.push(BTreeMap::from([
        ("netsim_state_Pixel_7_emulator-5554".to_string(), String::new()),
        ("netsim_state_emulator-5554".to_string(), "BLE:ON".to_string()),
    ]));
    summary.device_names.insert("emulator-5554".to_string(), "emulator-5554 | Pixel 7".to_string());
    summary.device_names.insert("Pixel_7_emulator-5554".to_string(), "emulator-5554".to_string());

    summary.migrate_keys("emulator-5554", "Pixel_7_emulator-5554");
    assert_eq!(summary.time_series[0]["emu_cpu_tot_Pixel_7_emulator-5554"], 25.0);
    assert_eq!(summary.time_series[0]["qemu_cpu_Pixel_7_emulator-5554"], 80.0);
    assert_eq!(
        summary.time_series_str[0].get("netsim_state_Pixel_7_emulator-5554").map(String::as_str),
        Some("BLE:ON")
    );
    assert_eq!(
        summary.device_names.get("Pixel_7_emulator-5554").map(String::as_str),
        Some("emulator-5554 | Pixel 7")
    );
}

#[test]
fn test_summary_and_csv_edge_cases() {
    let mut summary = SessionSummary::new();
    summary.device_names.insert("adb_1234".to_string(), "adb_1234".to_string());
    summary.device_names.insert("Pixel_6_adb_1234".to_string(), "Pixel_6 | Pixel 6".to_string());
    assert_eq!(deduplicated_emulators(&summary), vec!["Pixel_6_adb_1234".to_string()]);

    // Only true bare-serial keys are dropped: AVD `7` keeps its own entry
    // even though `Pixel_7_emulator-5554` ends with `_7_emulator-5554`.
    summary.device_names.insert("7_emulator-5554".to_string(), "7 | Phone".to_string());
    summary
        .device_names
        .insert("Pixel_7_emulator-5554".to_string(), "Pixel_7 | Pixel 7".to_string());
    summary
        .device_names
        .insert("emulator-5554".to_string(), "emulator-5554 | sdk_phone".to_string());
    assert_eq!(
        deduplicated_emulators(&summary),
        vec![
            "7_emulator-5554".to_string(),
            "Pixel_6_adb_1234".to_string(),
            "Pixel_7_emulator-5554".to_string()
        ]
    );

    // Zero-iteration empty session skips writing summary files.
    let empty_dir = tempfile::tempdir().unwrap();
    save_summary(
        &SessionSummary::new(),
        empty_dir.path().to_str().unwrap(),
        true,
        true,
        "Mock Specs",
        4,
        false,
    );
    assert_eq!(std::fs::read_dir(empty_dir.path()).unwrap().count(), 0);

    // Overflowing elapsed time (both SystemTime overflow at 1e19 and chrono
    // NaiveDate overflow at 1e15) does not panic in export_csv.
    summary
        .time_series
        .push(HashMap::from([("time".to_string(), 1e15_f32), ("host_cpu".to_string(), 10.0)]));
    summary
        .time_series
        .push(HashMap::from([("time".to_string(), 1e19_f32), ("host_cpu".to_string(), 10.0)]));
    let csv_path = empty_dir.path().join("overflow.csv");
    assert!(export_csv(&summary, csv_path.to_str().unwrap()).is_ok());
}

#[test]
fn test_unmigrated_bare_serial_kept_and_boot_log_not_stolen_by_earlier_avd() {
    let mut summary = SessionSummary::new();
    summary
        .device_names
        .insert("Pixel_6_emulator-5554".to_string(), "Pixel_6 | sdk_gphone64".to_string());
    summary
        .device_names
        .insert("emulator-5554".to_string(), "emulator-5554 | Pixel Watch".to_string());

    summary.time_series.push(HashMap::from([
        ("time".to_string(), 1.0),
        ("emu_cpu_tot_Pixel_6_emulator-5554".to_string(), 20.0),
    ]));
    summary.time_series.push(HashMap::from([
        ("time".to_string(), 2.0),
        ("emu_cpu_tot_emulator-5554".to_string(), 75.0),
    ]));
    summary.boot_logs.insert(
        "emulator-5554".to_string(),
        vec![("+0s".to_string(), "CPU: 75.0% | RAM: 30.0%".to_string())],
    );

    let emulators = deduplicated_emulators(&summary);
    assert_eq!(emulators, vec!["Pixel_6_emulator-5554".to_string(), "emulator-5554".to_string()]);

    let guest_lines = build_guests_section(&summary, &emulators).join("\n");
    let (pixel_block, watch_block) =
        guest_lines.split_once("GUEST SYSTEM (emulator-5554 | Pixel Watch):").unwrap();
    assert!(
        !pixel_block.contains("Boot Progression Log:"),
        "Earlier AVD without boot log must not inherit replacement bare-serial boot log:\n{pixel_block}"
    );
    assert!(watch_block.contains("Boot Progression Log:"));
    assert!(watch_block.contains("+0s | CPU: 75.0% | RAM: 30.0%"));
}

#[test]
fn test_summary_omits_absent_qemu_netsim_zero_charts_and_empty_banners() {
    let mut summary = SessionSummary::new();
    summary.iterations = 2;
    summary.device_names.insert("1A2B3C4D".to_string(), "1A2B3C4D | Pixel 8".to_string());
    summary
        .device_names
        .insert("Pixel_7_emulator-5554".to_string(), "Pixel_7 | Pixel 7".to_string());
    summary.device_names.insert("Offline_emulator-5556".to_string(), "Offline | Phone".to_string());

    summary.time_series = vec![
        HashMap::from([
            ("time".to_string(), 1.0),
            ("host_cpu".to_string(), 12.0),
            ("host_disk_read_mbs".to_string(), 0.0),
            ("host_disk_write_mbs".to_string(), 0.0),
            ("emu_cpu_tot_1A2B3C4D".to_string(), 30.0),
            ("emu_cpu_tot_Pixel_7_emulator-5554".to_string(), 40.0),
            ("qemu_cpu_Pixel_7_emulator-5554".to_string(), 90.0),
            ("qemu_ram_Pixel_7_emulator-5554".to_string(), 2048.0),
            ("qemu_gpu_Pixel_7_emulator-5554".to_string(), 0.0),
            ("emu_cpu_tot_Offline_emulator-5556".to_string(), f32::NAN),
        ]),
        HashMap::from([
            ("time".to_string(), 2.0),
            ("host_cpu".to_string(), 18.0),
            ("host_disk_read_mbs".to_string(), 0.0),
            ("host_disk_write_mbs".to_string(), 0.0),
            ("emu_cpu_tot_1A2B3C4D".to_string(), 35.0),
            ("emu_cpu_tot_Pixel_7_emulator-5554".to_string(), 50.0),
            ("qemu_cpu_Pixel_7_emulator-5554".to_string(), 110.0),
            ("qemu_ram_Pixel_7_emulator-5554".to_string(), 2048.0),
            ("qemu_gpu_Pixel_7_emulator-5554".to_string(), 0.0),
            ("emu_cpu_tot_Offline_emulator-5556".to_string(), f32::NAN),
        ]),
    ];

    assert!(build_netsim_section(&summary).is_empty());
    summary.netsim_summary_lines = vec!["Netsim daemon lifecycle:".to_string()];
    let netsim_only_lifecycle = build_netsim_section(&summary).join("\n");
    assert!(netsim_only_lifecycle.starts_with("NETSIM:\nNetsim daemon lifecycle:"));
    assert!(!netsim_only_lifecycle.contains("Average CPU Usage:"));

    let emulators = deduplicated_emulators(&summary);
    let guest_txt = build_guests_section(&summary, &emulators).join("\n");
    let (usb_block, rest) = guest_txt.split_once("GUEST SYSTEM (Offline | Phone):").unwrap();
    assert!(
        !usb_block.contains("Host QEMU CPU"),
        "Physical USB device without QEMU samples must omit Host QEMU lines:\n{usb_block}"
    );
    assert!(rest.contains("Host QEMU CPU (Avg): 100.0%"));

    let charts_txt = build_charts_section(&summary, &emulators).join("\n");
    assert!(charts_txt.contains("HOST DISK READ HISTORY CHART:"));
    assert!(charts_txt.contains("HOST DISK WRITE HISTORY CHART:"));
    assert!(!charts_txt.contains("HOST QEMU GPU LOAD HISTORY"));
    assert!(charts_txt.contains("CHARTS FOR: 1A2B3C4D | Pixel 8"));
    assert!(charts_txt.contains("CHARTS FOR: Pixel_7 | Pixel 7"));
    assert!(!charts_txt.contains("CHARTS FOR: Offline | Phone"));
}

#[test]
fn test_format_host_os_info_deduplicates_version_and_handles_missing() {
    assert_eq!(
        format_host_os_info(
            Some("Debian GNU/Linux rodete"),
            Some("rodete"),
            Some("7.1.6-1rodete1-amd64")
        ),
        "Debian GNU/Linux rodete (kernel 7.1.6-1rodete1-amd64)"
    );
    assert_eq!(
        format_host_os_info(Some("Ubuntu"), Some("24.04"), Some("6.8.0")),
        "Ubuntu 24.04 (kernel 6.8.0)"
    );
    assert_eq!(format_host_os_info(Some("Linux"), None, Some("6.6.0")), "Linux (kernel 6.6.0)");
    assert_eq!(format_host_os_info(Some("Darwin"), Some("15.2"), None), "Darwin 15.2");
    assert_eq!(format_host_os_info(None, None, None), "Unknown OS");
}

#[test]
fn test_save_summary_expands_tilde_out_dir_via_pathbuf() {
    let fake_home = tempfile::tempdir().unwrap();
    assert_eq!(
        expand_tilde_with("~/erm_logs/sub", Some(fake_home.path())),
        fake_home.path().join("erm_logs/sub")
    );
    assert_eq!(expand_tilde_with("~", Some(fake_home.path())), fake_home.path());
    assert_eq!(
        expand_tilde_with("~\\erm_logs", Some(fake_home.path())),
        fake_home.path().join("erm_logs")
    );

    let mut summary = SessionSummary::new();
    summary.iterations = 1;
    summary.time_series =
        vec![HashMap::from([("time".to_string(), 1.0), ("host_cpu".to_string(), 10.0)])];
    let expanded = expand_tilde_with("~/erm_logs", Some(fake_home.path()));
    save_summary_to_dir(&summary, &expanded, true, true, "Mock Specs", 4, SaveKind::Autosave);
    assert!(fake_home.path().join("erm_logs/device_monitor_summary_autosave.txt").exists());
    assert!(fake_home.path().join("erm_logs/device_monitor_summary_autosave.csv").exists());
}

#[test]
fn test_build_host_section_uses_max_of_summary_fields_and_series_peaks() {
    let mut summary = SessionSummary::new();
    summary.host_cpu_max = 88.0; // e.g. from an evicted >24h tick
    summary.host_ram_max = 0.0; // unset while time_series has 55.0
    summary.time_series = vec![HashMap::from([
        ("time".to_string(), 1.0),
        ("host_cpu".to_string(), 40.0),
        ("host_ram".to_string(), 55.0),
    ])];
    let host_txt = build_host_section(&summary).join("\n");
    assert!(host_txt.contains("  Peak CPU Usage:    88.0%"), "{host_txt}");
    assert!(host_txt.contains("  Peak RAM Usage:    55.0%"), "{host_txt}");
}

#[test]
fn test_export_csv_handles_chrono_out_of_range_timestamps() {
    let dir = tempfile::tempdir().unwrap();
    let mut summary = SessionSummary::new();
    summary.start_time = SystemTime::UNIX_EPOCH + Duration::from_secs(1_700_000_000);
    for elapsed in [0.0_f32, -5.0, 1e13, 1e15, 1e19, f32::NAN, f32::INFINITY] {
        summary
            .time_series
            .push(HashMap::from([("time".to_string(), elapsed), ("host_cpu".to_string(), 12.5)]));
    }
    let csv_path = dir.path().join("chrono_bounds.csv");
    assert!(export_csv(&summary, csv_path.to_str().unwrap()).is_ok());
    let text = std::fs::read_to_string(&csv_path).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines.len(), 8);
    let expected_fallback = lines[1].split(',').next().unwrap();
    for row in &lines[2..] {
        assert_eq!(row.split(',').next().unwrap(), expected_fallback);
    }

    // Even an out-of-range start_time falls back cleanly to UNIX_EPOCH without panicking.
    summary.start_time = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000_000_000_000);
    let extreme_start_csv = dir.path().join("extreme_start.csv");
    assert!(export_csv(&summary, extreme_start_csv.to_str().unwrap()).is_ok());
}

#[test]
fn test_stale_offline_nan_bare_serial_omitted_from_summary_and_csv() {
    let mut summary = SessionSummary::new();
    summary.iterations = 2;
    summary
        .device_names
        .insert("emulator-5554".to_string(), "emulator-5554 | Unknown (offline)".to_string());
    summary
        .device_names
        .insert("Pixel_8_emulator-5554".to_string(), "Pixel_8 | Pixel 8".to_string());
    summary
        .device_names
        .insert("emulator-5556".to_string(), "emulator-5556 | Unknown (offline)".to_string());

    summary.time_series = vec![
        HashMap::from([
            ("time".to_string(), 0.0),
            ("host_cpu".to_string(), 10.0),
            ("emu_cpu_tot_emulator-5554".to_string(), f32::NAN),
            ("emu_ram_emulator-5554".to_string(), f32::NAN),
            ("emu_cpu_tot_emulator-5556".to_string(), f32::NAN),
        ]),
        HashMap::from([
            ("time".to_string(), 2.0),
            ("host_cpu".to_string(), 12.0),
            ("emu_cpu_tot_Pixel_8_emulator-5554".to_string(), 45.0),
            ("qemu_cpu_Pixel_8_emulator-5554".to_string(), 90.0),
            ("emu_cpu_tot_emulator-5556".to_string(), f32::NAN),
        ]),
    ];
    summary.time_series_str = vec![
        BTreeMap::from([("netsim_state_emulator-5554".to_string(), String::new())]),
        BTreeMap::from([("netsim_state_Pixel_8_emulator-5554".to_string(), "CONNECTED".into())]),
    ];

    let emulators = deduplicated_emulators(&summary);
    assert_eq!(
        emulators,
        vec!["Pixel_8_emulator-5554".to_string(), "emulator-5556".to_string()],
        "All-NaN stale offline bare-serial placeholder must drop when <avd>_<serial> exists"
    );
    let guest_txt = build_guests_section(&summary, &emulators).join("\n");
    assert!(!guest_txt.contains("emulator-5554 | Unknown (offline)"));
    assert!(guest_txt.contains("GUEST SYSTEM (Pixel_8 | Pixel 8):"));
    assert!(guest_txt.contains("GUEST SYSTEM (emulator-5556 | Unknown (offline)):"));

    let dir = tempfile::tempdir().unwrap();
    let csv_path = dir.path().join("stale_offline.csv");
    export_csv(&summary, csv_path.to_str().unwrap()).unwrap();
    let header = std::fs::read_to_string(&csv_path).unwrap().lines().next().unwrap().to_string();
    assert!(
        !header.contains("emu_cpu_tot_emulator-5554")
            && !header.contains("netsim_state_emulator-5554"),
        "Dropped stale offline bare-serial columns leaked into CSV header: {header}"
    );
    assert!(header.contains("emu_cpu_tot_Pixel_8_emulator-5554"));
    assert!(header.contains("emu_cpu_tot_emulator-5556"));
}

#[test]
fn test_compute_series_stats_skips_infinities_and_header_handles_extreme_times() {
    let series = vec![
        HashMap::from([("host_cpu".to_string(), 20.0)]),
        HashMap::from([("host_cpu".to_string(), f32::INFINITY)]),
        HashMap::from([("host_cpu".to_string(), f32::NEG_INFINITY)]),
        HashMap::from([("host_cpu".to_string(), 40.0)]),
    ];
    let stats = compute_series_stats(&series, "host_cpu");
    assert_eq!(stats.count, 2);
    assert_eq!(stats.avg, 30.0);
    assert_eq!(stats.peak, 40.0);

    let mut summary = SessionSummary::new();
    summary.start_time = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000_000_000_000);
    let end_time = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000_000_000_010);
    let header = build_header_section(&summary, end_time, "Mock Specs", 4);
    assert!(header.iter().any(|l| l.starts_with("Start Time:  ")));
    assert!(header.iter().any(|l| l.starts_with("End Time:    ")));
}

#[test]
fn test_save_summary_error_includes_path_and_remediation_and_preserves_autosave() {
    let dir = tempfile::tempdir().unwrap();
    let summary = build_representative_summary();
    let save = |base: &std::path::Path, kind| {
        save_summary_to_dir(&summary, base, true, true, "Mock Specs", 4, kind);
    };

    save(dir.path(), SaveKind::Autosave);
    let autosave_txt = dir.path().join("device_monitor_summary_autosave.txt");
    let autosave_csv = dir.path().join("device_monitor_summary_autosave.csv");
    assert!(autosave_txt.is_file());
    assert!(autosave_csv.is_file());

    let blocker_file = dir.path().join("blocked_out_dir");
    std::fs::write(&blocker_file, b"not a directory").unwrap();
    let io_err = std::fs::write(blocker_file.join("out.txt"), b"x").unwrap_err();
    let target_txt = blocker_file.join("device_monitor_summary_autosave.txt");
    let formatted = format_save_error("summary file", &target_txt, &io_err);
    assert!(formatted.contains(&target_txt.display().to_string()), "{formatted}");
    assert!(formatted.contains("--out-dir permissions"), "{formatted}");
    assert!(formatted.contains("open file locks"), "{formatted}");
    assert!(formatted.contains("disk space"), "{formatted}");

    // Must report to stderr without panicking when out_dir is blocked by a regular file.
    save(&blocker_file, SaveKind::Autosave);
    assert!(blocker_file.is_file());

    let final_save = SaveKind::Final { stamp: "20260102_030405" };

    // A read-only output directory (non-root Unix) fails the final save without a panic
    // and leaves the autosave pair in place.
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if std::fs::metadata(dir.path()).map(|m| m.uid()).unwrap_or(0) != 0 {
            std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o555)).unwrap();
            save(dir.path(), final_save);
            std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
            assert!(autosave_txt.is_file(), "autosave.txt must survive failed final write");
            assert!(autosave_csv.is_file(), "autosave.csv must survive failed final write");
        }
    }

    // A final save that cannot write its files keeps the autosave pair. Directories occupy
    // the final names, so the output directory stays writable and a cleanup that ignored
    // the failed writes would delete the pair.
    let final_txt = dir.path().join(final_save.file_name("txt"));
    let final_csv = dir.path().join(final_save.file_name("csv"));
    std::fs::create_dir(&final_txt).unwrap();
    std::fs::create_dir(&final_csv).unwrap();
    save(dir.path(), final_save);
    assert!(autosave_txt.is_file(), "autosave.txt must survive a failed final write");
    assert!(autosave_csv.is_file(), "autosave.csv must survive a failed final write");

    // Each autosave file goes once the final file of its own kind is written.
    std::fs::remove_dir(&final_csv).unwrap();
    save(dir.path(), final_save);
    assert!(final_csv.is_file());
    assert!(autosave_txt.is_file());
    assert!(!autosave_csv.exists());
    std::fs::remove_dir(&final_txt).unwrap();
    save(dir.path(), final_save);
    assert!(final_txt.is_file());
    assert!(!autosave_txt.exists());
}

#[test]
fn test_non_ascii_utf8_paths_tmpdir_and_avd_names() {
    use std::ffi::OsString;

    let dir = tempfile::tempdir().unwrap();
    let utf8_out_dir = dir.path().join("täst_中文_🚀").join("José");
    let expanded = expand_tilde_with("~/täst_中文_🚀/José", Some(dir.path()));
    assert_eq!(expanded, utf8_out_dir);

    // Cmdline AVD extraction with multi-byte UTF-8 and internal underscores.
    assert_eq!(
        crate::host::parse_cmdline(&[OsString::from("-avd"), OsString::from("täst_中文_🚀")]),
        (None, Some("täst_中文_🚀".to_string()))
    );
    assert_eq!(
        crate::host::parse_cmdline(&[OsString::from("@José")]),
        (None, Some("José".to_string()))
    );

    // TMPDIR discovery INI parsing under non-ASCII UTF-8 directory and AVD names.
    let utf8_tmpdir = dir.path().join("tmp_täst_中文_🚀");
    let running_dir = utf8_tmpdir.join("avd").join("running");
    std::fs::create_dir_all(&running_dir).unwrap();
    std::fs::write(
        running_dir.join("pid_424242.ini"),
        "port.serial=5554\navd.id=täst_中文_🚀\navd.name=José\n",
    )
    .unwrap();
    let env = HashMap::from([(
        "TMPDIR".to_string(),
        utf8_tmpdir.to_str().expect("valid UTF-8 temp path").to_string(),
    )]);
    let ini = crate::netsim::discover_emulator_pid_ini(424242, &env, None);
    assert_eq!(ini.get("avd.id").map(String::as_str), Some("täst_中文_🚀"));
    assert_eq!(ini.get("avd.name").map(String::as_str), Some("José"));

    // Summary & CSV export with bare->UTF-8 AVD key migration and multi-byte paths.
    let mut summary = SessionSummary::new();
    summary.iterations = 2;
    summary.host_cpu_max = 35.0;
    summary.host_ram_max = 50.0;
    summary.device_names.insert("emulator-5554".to_string(), "emulator-5554".to_string());
    summary.time_series.push(HashMap::from([
        ("time".to_string(), 1.0),
        ("host_cpu".to_string(), 25.0),
        ("emu_cpu_tot_emulator-5554".to_string(), 40.0),
        ("emu_cpu_adj_emulator-5554".to_string(), 10.0),
        ("emu_ram_emulator-5554".to_string(), 55.0),
    ]));
    summary.migrate_keys_from("emulator-5554", "täst_中文_🚀_emulator-5554", 0, 0);
    summary
        .device_names
        .insert("täst_中文_🚀_emulator-5554".to_string(), "täst_中文_🚀 | Pixel 9".to_string());
    summary.device_names.insert("José_emulator-5556".to_string(), "José | Watch".to_string());
    summary.time_series.push(HashMap::from([
        ("time".to_string(), 2.0),
        ("host_cpu".to_string(), 35.0),
        ("emu_cpu_tot_täst_中文_🚀_emulator-5554".to_string(), 60.0),
        ("emu_cpu_adj_täst_中文_🚀_emulator-5554".to_string(), 15.0),
        ("emu_ram_täst_中文_🚀_emulator-5554".to_string(), 65.0),
        ("emu_cpu_tot_José_emulator-5556".to_string(), 20.0),
        ("emu_cpu_adj_José_emulator-5556".to_string(), 10.0),
        ("emu_ram_José_emulator-5556".to_string(), 45.0),
    ]));

    save_summary(&summary, utf8_out_dir.to_str().unwrap(), true, true, "Mock UTF-8 Specs", 4, true);
    let txt =
        std::fs::read_to_string(utf8_out_dir.join("device_monitor_summary_autosave.txt")).unwrap();
    let csv =
        std::fs::read_to_string(utf8_out_dir.join("device_monitor_summary_autosave.csv")).unwrap();
    assert!(txt.contains("GUEST SYSTEM (täst_中文_🚀 | Pixel 9):"), "{txt}");
    assert!(txt.contains("GUEST SYSTEM (José | Watch):"), "{txt}");
    assert!(!txt.contains("GUEST SYSTEM (emulator-5554"), "{txt}");
    let header = csv.lines().next().unwrap();
    assert!(header.contains("emu_cpu_tot_täst_中文_🚀_emulator-5554"), "{header}");
    assert!(header.contains("emu_cpu_tot_José_emulator-5556"), "{header}");
    assert!(!header.contains("emu_cpu_tot_emulator-5554"), "{header}");

    // Narrow TUI dashboard truncation across multi-byte UTF-8 code points must never panic.
    let mut guest_results = HashMap::new();
    guest_results.insert(
        "emulator-5554".to_string(),
        crate::ui::GuestUiStats {
            model: "Pixel 9 Pro | Android 16 (API 36) | Build: BGQ1.260312.001 | Res: 1080x2400"
                .to_string(),
            avd_name: Some("täst_中文_🚀_José".to_string()),
            cpu_total: "60%".to_string(),
            cpu_adjusted: "15.0% (over 4 cores)".to_string(),
            ram: "65.0% (2.6GB/4.0GB)".to_string(),
            data_space: Some("25% (2.5G used/7.5G avail)".to_string()),
            swap: Some("0.0 MB".to_string()),
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: true,
        },
    );
    let dash = crate::ui::build_dashboard(crate::ui::DashboardInput {
        host_stats: &crate::initial_host_stats(),
        guest_results: &guest_results,
        prev_stats: &mut HashMap::new(),
        iter_count: 1,
        term_width: 76,
        is_once: true,
        hardware_specs: "Linux | täst_中文_🚀 | José — 64-Core Processor | 128.0 GB RAM",
        force_clear: true,
        online_indicator_until: &HashMap::new(),
        netsim_snapshot: None,
        host_timed_out: false,
    });
    assert!(dash.contains("täst_中文_🚀"), "{dash}");
    assert!(dash.contains('…'), "{dash}");
}
