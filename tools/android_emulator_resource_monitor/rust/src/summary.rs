use std::collections::HashMap;
use std::time::SystemTime;
use std::fs::File;
// Deleted unused import
use csv::Writer;
use sysinfo::System;
use chrono::{DateTime, Local};
use regex::Regex;

pub fn get_val(s: &str, for_ts: bool) -> f32 {
    if for_ts && (s.contains("N/A") || s.contains("Unsupported") || s.contains("Calculating") || s.contains("No ADB") || s.contains("Error") || s.contains("Timeout") || s.contains("Hang")) {
        return f32::NAN;
    }

    let re_pct = Regex::new(r"(-?\d+\.?\d*)%").unwrap();
    let re_float = Regex::new(r"(-?\d+\.?\d*)").unwrap();

    if let Some(caps) = re_pct.captures(s) {
        return caps[1].parse::<f32>().unwrap_or(0.0);
    }

    if s.contains("pages/s") || s.contains("Out:") || s.contains("KB/s") {
        let mut max_val: f32 = 0.0;
        for caps in re_float.captures_iter(s) {
            if let Ok(v) = caps[1].parse::<f32>() {
                max_val = max_val.max(v);
            }
        }
        return max_val;
    }

    if let Some(caps) = re_float.captures(s) {
        return caps[1].parse::<f32>().unwrap_or(0.0);
    }

    if for_ts { f32::NAN } else { 0.0 }
}

pub struct SessionSummary {
    pub start_time: SystemTime,
    pub iterations: u64,
    pub host_cpu_sum: f32,
    pub host_cpu_max: f32,
    pub host_ram_sum: f32,
    pub host_ram_max: f32,
    pub time_series: Vec<HashMap<String, f32>>,
    pub boot_logs: HashMap<String, Vec<(String, String)>>,
    pub device_names: HashMap<String, String>,
}

impl SessionSummary {
    pub fn new() -> Self {
        Self {
            start_time: SystemTime::now(),
            iterations: 0,
            host_cpu_sum: 0.0,
            host_cpu_max: 0.0,
            host_ram_sum: 0.0,
            host_ram_max: 0.0,
            time_series: Vec::new(),
            boot_logs: HashMap::new(),
            device_names: HashMap::new(),
        }
    }

    pub fn migrate_keys(&mut self, old_key: &str, new_key: &str) {
        let metrics = ["emu_cpu_tot", "emu_cpu_adj", "emu_ram", "emu_swap", "emu_disk_pct", "emu_errs", "qemu_cpu", "qemu_ram", "qemu_cs", "qemu_gpu"];
        for entry in &mut self.time_series {
            for metric in &metrics {
                let old_full_key = format!("{}_{}", metric, old_key);
                let new_full_key = format!("{}_{}", metric, new_key);
                if let Some(val) = entry.remove(&old_full_key) {
                    entry.insert(new_full_key, val);
                }
            }
        }
    }
}

pub fn get_p95(time_series: &[HashMap<String, f32>], key: &str) -> f32 {
    let mut vals: Vec<f32> = time_series.iter()
        .filter_map(|entry| entry.get(key).cloned())
        .filter(|v| !v.is_nan())
        .collect();

    if vals.is_empty() {
        return 0.0;
    }

    vals.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let idx = (vals.len() * 95 / 100).min(vals.len() - 1);
    vals[idx]
}

pub fn get_avg(time_series: &[HashMap<String, f32>], key: &str) -> f32 {
    let vals: Vec<f32> = time_series.iter()
        .filter_map(|entry| entry.get(key).cloned())
        .filter(|v| !v.is_nan())
        .collect();

    if vals.is_empty() {
        return 0.0;
    }

    vals.iter().sum::<f32>() / vals.len() as f32
}

pub fn get_max(time_series: &[HashMap<String, f32>], key: &str) -> f32 {
    let vals: Vec<f32> = time_series.iter()
        .filter_map(|entry| entry.get(key).cloned())
        .filter(|v| !v.is_nan())
        .collect();

    if vals.is_empty() {
        return 0.0;
    }

    vals.iter().copied().reduce(f32::max).unwrap_or(0.0)
}

pub fn export_csv(summary: &SessionSummary, filename: &str) -> Result<(), Box<dyn std::error::Error>> {
    let mut all_keys: Vec<String> = Vec::new();
    for entry in &summary.time_series {
        for key in entry.keys() {
            if !all_keys.contains(key) {
                all_keys.push(key.clone());
            }
        }
    }

    if let Some(pos) = all_keys.iter().position(|k| k == "time") {
        all_keys.remove(pos);
        all_keys.insert(0, "time".to_string());
    }

    let file = File::create(filename)?;
    let mut wtr = Writer::from_writer(file);

    wtr.write_record(&all_keys)?;

    for entry in &summary.time_series {
        let mut row = Vec::new();
        for key in &all_keys {
            let val = if key == "time" {
                let elapsed = entry.get(key).cloned().unwrap_or(0.0);
                let system_time = summary.start_time + std::time::Duration::from_secs_f64(elapsed as f64);
                let datetime: chrono::DateTime<chrono::Local> = system_time.into();
                datetime.format("%Y-%m-%d %H:%M:%S").to_string()
            } else {
                entry.get(key).map(|v| v.to_string()).unwrap_or_default()
            };
            row.push(val);
        }
        wtr.write_record(&row)?;
    }

    wtr.flush()?;
    Ok(())
}



pub fn get_chart_str(summary: &SessionSummary, key: &str, title: &str) -> Option<String> {
    let points: Vec<(f32, f32)> = summary.time_series.iter()
        .filter_map(|entry| {
            let t = entry.get("time").cloned()?;
            let v = entry.get(key).cloned()?;
            Some((t, v))
        })
        .collect();

    if points.len() >= 2 {
        Some(crate::chart::render_chart(&points, 60, 10, title))
    } else {
        None
    }
}

pub fn save_summary(summary: &SessionSummary, out_dir: &str, save_csv: bool, save_txt: bool, hardware_specs: &str, cpu_cores: usize, is_autosave: bool) {
    let end_time = SystemTime::now();
    let duration = end_time.duration_since(summary.start_time).unwrap_or_default().as_secs_f32();

    let start_chrono: DateTime<Local> = summary.start_time.into();
    let end_chrono: DateTime<Local> = end_time.into();

    let os_info = format!("{} {} (kernel {})",
        System::name().unwrap_or_default(),
        System::os_version().unwrap_or_default(),
        System::kernel_version().unwrap_or_default()
    );

    let adb_ver = match std::process::Command::new("adb").arg("version").output() {
        Ok(output) => String::from_utf8_lossy(&output.stdout).lines().next().unwrap_or("N/A").to_string(),
        Err(_) => "N/A".to_string(),
    };

    let is_vm = std::fs::read_to_string("/proc/cpuinfo")
        .map(|c| c.contains("hypervisor"))
        .unwrap_or(false);

    let mut lines = Vec::new();
    lines.push("Android Emulator & Device Resource Monitor Session Summary".to_string());
    lines.push("=".repeat(60));
    lines.push(format!("Start Time:  {}", start_chrono.format("%Y-%m-%d %H:%M:%S")));
    lines.push(format!("End Time:    {}", end_chrono.format("%Y-%m-%d %H:%M:%S")));
    lines.push(format!("Duration:    {:.1} seconds", duration));
    lines.push(format!("Total Loops: {}", summary.iterations));
    lines.push(format!("Host OS:      {}", os_info));
    lines.push(format!("Host Specs:   {}", hardware_specs));
    lines.push(format!("Host Cores:   {}", cpu_cores));
    lines.push(format!("Host Virtual: {}", is_vm));
    lines.push(format!("ADB Version:  {}", adb_ver));
    lines.push("".to_string());

    lines.push("HOST SYSTEM:".to_string());
    lines.push(format!("  Average CPU Usage: {:.1}%", get_avg(&summary.time_series, "host_cpu")));
    lines.push(format!("  Peak CPU Usage:    {:.1}%", summary.host_cpu_max));
    lines.push(format!("  P95 CPU Usage:     {:.1}%", get_p95(&summary.time_series, "host_cpu")));
    lines.push(format!("  Average RAM Usage: {:.1}%", get_avg(&summary.time_series, "host_ram")));
    lines.push(format!("  Peak RAM Usage:    {:.1}%", summary.host_ram_max));
    lines.push(format!("  P95 RAM Usage:     {:.1}%", get_p95(&summary.time_series, "host_ram")));

    let avg_t = get_avg(&summary.time_series, "host_thermal");
    let peak_t = get_max(&summary.time_series, "host_thermal");
    let p95_t = get_p95(&summary.time_series, "host_thermal");
    lines.push(format!("  Average Thermal:   {:.1}°C", avg_t));
    lines.push(format!("  Peak Thermal:      {:.1}°C", peak_t));
    lines.push(format!("  P95 Thermal:       {:.1}°C", p95_t));

    let avg_r = get_avg(&summary.time_series, "host_disk_read");
    let peak_r = get_max(&summary.time_series, "host_disk_read");
    let p95_r = get_p95(&summary.time_series, "host_disk_read");
    lines.push(format!("  Avg Disk Read:     {:.1} MB/s", avg_r));
    lines.push(format!("  Peak Disk Read:    {:.1} MB/s", peak_r));
    lines.push(format!("  P95 Disk Read:     {:.1} MB/s", p95_r));

    let avg_w = get_avg(&summary.time_series, "host_disk_write");
    let peak_w = get_max(&summary.time_series, "host_disk_write");
    let p95_w = get_p95(&summary.time_series, "host_disk_write");
    lines.push(format!("  Avg Disk Write:    {:.1} MB/s", avg_w));
    lines.push(format!("  Peak Disk Write:   {:.1} MB/s", peak_w));
    lines.push(format!("  P95 Disk Write:    {:.1} MB/s", p95_w));
    lines.push("".to_string());

    let avg_netsim_cpu = get_avg(&summary.time_series, "netsim_cpu");
    let p95_netsim_cpu = get_p95(&summary.time_series, "netsim_cpu");
    lines.push("NETSIM:".to_string());
    lines.push(format!("  Average CPU Usage: {:.1}%", avg_netsim_cpu));
    lines.push(format!("  P95 CPU Usage:     {:.1}%", p95_netsim_cpu));

    let avg_netsim_ram = get_avg(&summary.time_series, "netsim_ram");
    let p95_netsim_ram = get_p95(&summary.time_series, "netsim_ram");
    lines.push(format!("  Average RAM Usage: {:.1} MB", avg_netsim_ram));
    lines.push(format!("  P95 RAM Usage:     {:.1} MB", p95_netsim_ram));
    lines.push("".to_string());

    let raw_keys: Vec<String> = summary.device_names.keys().cloned().collect();
    let mut emulators = Vec::new();
    for key in &raw_keys {
        if !key.contains('_') {
            let better_key_exists = raw_keys.iter().any(|k| k.ends_with(&format!("_{}", key)));
            if better_key_exists { continue; }
        }
        emulators.push(key.clone());
    }
    emulators.sort();

    for serial in &emulators {
        let name = summary.device_names.get(serial).cloned().unwrap_or_else(|| serial.clone());
        lines.push(format!("GUEST SYSTEM ({}):", name));

        let avg_tot = get_avg(&summary.time_series, &format!("emu_cpu_tot_{}", serial));
        let peak_tot = get_max(&summary.time_series, &format!("emu_cpu_tot_{}", serial));
        let p95_tot = get_p95(&summary.time_series, &format!("emu_cpu_tot_{}", serial));
        lines.push(format!("  Average Total CPU:    {:.1}%", avg_tot));
        lines.push(format!("  Peak Total CPU:       {:.1}%", peak_tot));
        lines.push(format!("  P95 Total CPU:        {:.1}%", p95_tot));

        let avg_adj = get_avg(&summary.time_series, &format!("emu_cpu_adj_{}", serial));
        let peak_adj = get_max(&summary.time_series, &format!("emu_cpu_adj_{}", serial));
        let p95_adj = get_p95(&summary.time_series, &format!("emu_cpu_adj_{}", serial));
        lines.push(format!("  Average Adjusted CPU: {:.1}%", avg_adj));
        lines.push(format!("  Peak Adjusted CPU:    {:.1}%", peak_adj));
        lines.push(format!("  P95 Adjusted CPU:     {:.1}%", p95_adj));

        let avg_ram = get_avg(&summary.time_series, &format!("emu_ram_{}", serial));
        let peak_ram = get_max(&summary.time_series, &format!("emu_ram_{}", serial));
        let p95_ram = get_p95(&summary.time_series, &format!("emu_ram_{}", serial));
        lines.push(format!("  Average RAM Usage:    {:.1}%", avg_ram));
        lines.push(format!("  Peak RAM Usage:       {:.1}%", peak_ram));
        lines.push(format!("  P95 RAM Usage:        {:.1}%", p95_ram));

        let avg_swap = get_avg(&summary.time_series, &format!("emu_swap_{}", serial));
        let peak_swap = get_max(&summary.time_series, &format!("emu_swap_{}", serial));
        let p95_swap = get_p95(&summary.time_series, &format!("emu_swap_{}", serial));
        lines.push(format!("  Average Swap Usage:   {:.1}MB", avg_swap));
        lines.push(format!("  Peak Swap Usage:      {:.1}MB", peak_swap));
        lines.push(format!("  P95 Swap Usage:       {:.1}MB", p95_swap));

        let avg_qemu_cpu = get_avg(&summary.time_series, &format!("qemu_cpu_{}", serial));
        let peak_qemu_cpu = get_max(&summary.time_series, &format!("qemu_cpu_{}", serial));
        let p95_qemu_cpu = get_p95(&summary.time_series, &format!("qemu_cpu_{}", serial));
        lines.push(format!("  Host QEMU CPU (Avg): {:.1}%", avg_qemu_cpu));
        lines.push(format!("  Host QEMU CPU (Max): {:.1}%", peak_qemu_cpu));
        lines.push(format!("  Host QEMU CPU (P95): {:.1}%", p95_qemu_cpu));

        let avg_qemu_ram = get_avg(&summary.time_series, &format!("qemu_ram_{}", serial));
        let peak_qemu_ram = get_max(&summary.time_series, &format!("qemu_ram_{}", serial));
        let p95_qemu_ram = get_p95(&summary.time_series, &format!("qemu_ram_{}", serial));
        lines.push(format!("  Host QEMU RAM (Avg): {:.1}MB", avg_qemu_ram));
        lines.push(format!("  Host QEMU RAM (Max): {:.1}MB", peak_qemu_ram));
        lines.push(format!("  Host QEMU RAM (P95): {:.1}MB", p95_qemu_ram));
        lines.push("".to_string());
    }

    for (serial, log) in &summary.boot_logs {
        lines.push(format!("GUEST SYSTEM ({}) Boot Progression Log:", serial));
        for (delta, stats) in log {
            lines.push(format!("  {} | {}", delta, stats));
        }
        lines.push("".to_string());
    }

    // Charts
    let mut chart_lines = Vec::new();
    if let Some(c) = get_chart_str(summary, "host_cpu", "HOST CPU HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "host_ram", "HOST RAM HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "host_swap", "HOST SWAP HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "host_disk_read", "HOST DISK READ HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "host_disk_write", "HOST DISK WRITE HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "netsim_cpu", "NETSIM CPU HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "netsim_ram", "NETSIM RAM HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "host_thermal", "HOST THERMAL HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "host_gpu", "HOST GPU LOAD HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }
    if let Some(c) = get_chart_str(summary, "host_gpu_mem", "HOST GPU MEMORY HISTORY CHART") { chart_lines.push(c); chart_lines.push("".to_string()); }

    for serial in &emulators {
        chart_lines.push("\n".to_string());
        chart_lines.push("=".repeat(80));
        let name = summary.device_names.get(serial).cloned().unwrap_or_else(|| serial.clone());
        chart_lines.push(format!("CHARTS FOR: {}", name));
        chart_lines.push("=".repeat(80));
        chart_lines.push("\n".to_string());

        if let Some(c) = get_chart_str(summary, &format!("emu_cpu_tot_{}", serial), &format!("GUEST CPU HISTORY ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("emu_ram_{}", serial), &format!("GUEST RAM HISTORY ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("emu_swap_{}", serial), &format!("GUEST SWAP MEMORY HISTORY ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("qemu_cpu_{}", serial), &format!("HOST QEMU CPU HISTORY ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("qemu_ram_{}", serial), &format!("HOST QEMU RAM HISTORY ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("emu_disk_pct_{}", serial), &format!("GUEST DATA PARTITION FULLNESS ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("emu_errs_{}", serial), &format!("GUEST LOGCAT ERRORS ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("qemu_cs_{}", serial), &format!("QEMU CONTEXT SWITCHES ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
        if let Some(c) = get_chart_str(summary, &format!("qemu_gpu_{}", serial), &format!("HOST QEMU GPU LOAD HISTORY ({})", serial)) { chart_lines.push(c); chart_lines.push("".to_string()); }
    }

    // Append charts to lines for file saving
    for c in &chart_lines {
        lines.push(c.clone());
    }

    if !is_autosave {
        // Print summary to console exactly like Python
        println!("\n{}", "=".repeat(60));
        println!("SESSION SUMMARY (Console Output)");
        println!("{}", "=".repeat(60));
        println!("{}", lines.join("\n"));
        println!("{}\n", "=".repeat(60));
    }

    let file_ts = Local::now().format("%Y%m%d_%H%M%S").to_string();
    if save_txt {
        let txt_filename = if is_autosave {
            format!("{}/device_monitor_summary_autosave.txt", out_dir)
        } else {
            format!("{}/device_monitor_summary_{}.txt", out_dir, file_ts)
        };

        use std::io::Write;
        match File::create(&txt_filename) {
            Ok(mut file) => {
                if let Err(e) = file.write_all(lines.join("\n").as_bytes()) {
                    println!("Failed to write summary file: {}", e);
                } else if !is_autosave {
                    println!("Session summary saved to {}", txt_filename);
                }
            }
            Err(e) => {
                println!("Failed to create summary file: {}", e);
            }
        }
    }

    if save_csv && !summary.time_series.is_empty() {
        let filename = if is_autosave {
            format!("{}/device_monitor_summary_autosave.csv", out_dir)
        } else {
            format!("{}/device_monitor_summary_{}.csv", out_dir, file_ts)
        };
        if let Err(e) = export_csv(summary, &filename) {
            println!("Failed to export CSV: {}", e);
        } else {
            println!("CSV exported to {}", filename);
        }
    }

    if !is_autosave {
        let autosave_txt = format!("{}/device_monitor_summary_autosave.txt", out_dir);
        let autosave_csv = format!("{}/device_monitor_summary_autosave.csv", out_dir);
        let _ = std::fs::remove_file(autosave_txt);
        let _ = std::fs::remove_file(autosave_csv);
    }
}

#[cfg(test)]
mod tests {
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
        let p95 = get_p95(&ts, "val");
        assert_eq!(p95, 96.0);
    }

    #[test]
    fn test_get_avg() {
        let mut ts = Vec::new();
        for i in 1..=10 {
            let mut entry = HashMap::new();
            entry.insert("val".to_string(), i as f32);
            ts.push(entry);
        }
        let avg = get_avg(&ts, "val");
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
        let max_val = get_max(&ts, "val");
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
            host_cpu_sum: 30.0,
            host_cpu_max: 20.0,
            host_ram_sum: 0.0,
            host_ram_max: 0.0,
            time_series: ts,
            boot_logs: HashMap::new(),
            device_names: HashMap::new(),
        };

        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_output.csv");
        let filename = file_path.to_str().unwrap();
        let res = export_csv(&summary, filename);
        assert!(res.is_ok());

        let content = std::fs::read_to_string(filename).unwrap();
        assert!(content.contains("time,host_cpu"));
        let start_chrono: DateTime<Local> = summary.start_time.into();
        let expected_ts1 = (start_chrono + chrono::Duration::seconds(1)).format("%Y-%m-%d %H:%M:%S").to_string();
        let expected_ts2 = (start_chrono + chrono::Duration::seconds(2)).format("%Y-%m-%d %H:%M:%S").to_string();

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
    fn test_migrate_keys() {
        let mut summary = SessionSummary::new();
        let mut entry = HashMap::new();
        entry.insert("emu_cpu_tot_emulator-5554".to_string(), 10.0);
        entry.insert("emu_ram_emulator-5554".to_string(), 20.0);
        entry.insert("qemu_cpu_emulator-5554".to_string(), 30.0);
        summary.time_series.push(entry);

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
            host_cpu_sum: 10.0,
            host_cpu_max: 10.0,
            host_ram_sum: 0.0,
            host_ram_max: 0.0,
            time_series: ts,
            boot_logs: HashMap::new(),
            device_names: HashMap::new(),
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
            host_cpu_sum: 10.0,
            host_cpu_max: 10.0,
            host_ram_sum: 0.0,
            host_ram_max: 0.0,
            time_series: ts,
            boot_logs: HashMap::new(),
            device_names,
        };

        let dir = tempfile::tempdir().unwrap();
        let out_dir = dir.path().to_str().unwrap();

        save_summary(&summary, out_dir, false, true, "Mock Specs", 4, true);

        let txt_path = dir.path().join("device_monitor_summary_autosave.txt");
        let content = std::fs::read_to_string(txt_path).unwrap();

        let guest_mentions: Vec<&str> = content.lines().filter(|l| l.contains("GUEST SYSTEM (")).collect();
        assert_eq!(guest_mentions.len(), 1);
        assert!(guest_mentions[0].contains("Pixel_6"));
    }
}
