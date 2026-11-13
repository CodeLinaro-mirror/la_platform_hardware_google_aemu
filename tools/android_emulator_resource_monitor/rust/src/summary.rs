use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::File;
use std::sync::LazyLock;
use std::time::{Duration, SystemTime};

use chrono::{DateTime, Local};
use csv::Writer;
use regex::Regex;
use sysinfo::System;

const SUMMARY_DIVIDER_WIDTH: usize = 60;
const CHART_BANNER_WIDTH: usize = 80;
const DEFAULT_CHART_WIDTH: usize = 60;
const DEFAULT_CHART_HEIGHT: usize = 10;
const P95_PERCENTILE: usize = 95;
const PERCENT_SCALE: usize = 100;
const ADB_VERSION_TIMEOUT: Duration = Duration::from_secs(2);

const PLACEHOLDER_SUFFIX_MARKERS: &[&str] =
    &["Unsupported", "Calculating", "No ADB", "Error", "Timeout", "Hang"];
const PAGING_MARKERS: &[&str] = &["pages/s", "Out:", "KB/s"];

const GUEST_TIME_SERIES_METRICS: &[&str] = &[
    "emu_cpu_tot",
    "emu_cpu_adj",
    "emu_ram",
    "emu_swap",
    "emu_disk_pct",
    "emu_errs",
    "qemu_cpu",
    "qemu_ram",
    "qemu_cs",
    "qemu_gpu",
];

const HOST_AND_NETSIM_CHARTS: &[(&str, &str)] = &[
    ("host_cpu", "HOST CPU HISTORY CHART"),
    ("host_ram", "HOST RAM HISTORY CHART"),
    ("host_swap", "HOST SWAP HISTORY CHART"),
    ("host_disk_read_mbs", "HOST DISK READ HISTORY CHART"),
    ("host_disk_write_mbs", "HOST DISK WRITE HISTORY CHART"),
    ("netsim_cpu", "NETSIM CPU HISTORY CHART"),
    ("netsim_ram", "NETSIM RAM HISTORY CHART"),
    ("host_thermal_c", "HOST THERMAL HISTORY CHART"),
    ("host_gpu", "HOST GPU LOAD HISTORY CHART"),
    ("host_gpu_mem", "HOST GPU MEMORY HISTORY CHART"),
];

const CSV_HOST_COLUMNS: &[&str] = &[
    "time",
    "host_cpu",
    "host_ram",
    "netsim_cpu",
    "netsim_ram",
    "host_gpu",
    "host_gpu_mem",
    "host_swap",
    "host_thermal_c",
    "host_disk_read_mbs",
    "host_disk_write_mbs",
    "host_paging_in",
    "host_paging_out",
    "host_battery",
    "host_unmapped_qemu_cpu",
    "host_unmapped_qemu_ram",
];

const CSV_NETSIM_DAEMON_COLUMNS: &[&str] =
    &["netsim_up", "netsim_pid", "netsim_restarts", "netsim_conns", "netsim_devices"];

const CSV_PER_EMULATOR_PREFIXES: &[&str] = &[
    "netsim_state",
    "emu_cpu_tot",
    "emu_cpu_adj",
    "emu_ram",
    "emu_swap",
    "emu_errs",
    "emu_disk_pct",
    "qemu_cpu",
    "qemu_ram",
    "qemu_cs",
    "qemu_gpu",
];

const GUEST_CHART_SPECS: &[(&str, &str)] = &[
    ("emu_cpu_tot", "GUEST CPU HISTORY"),
    ("emu_ram", "GUEST RAM HISTORY"),
    ("emu_swap", "GUEST SWAP MEMORY HISTORY"),
    ("qemu_cpu", "HOST QEMU CPU HISTORY"),
    ("qemu_ram", "HOST QEMU RAM HISTORY"),
    ("emu_disk_pct", "GUEST DATA PARTITION FULLNESS"),
    ("emu_errs", "GUEST LOGCAT ERRORS"),
    ("qemu_cs", "QEMU CONTEXT SWITCHES"),
    ("qemu_gpu", "HOST QEMU GPU LOAD HISTORY"),
];

fn is_ts_placeholder(s: &str) -> bool {
    s.trim_start().starts_with("N/A")
        || PLACEHOLDER_SUFFIX_MARKERS.iter().any(|marker| s.contains(marker))
}

/// Parses a numeric metric value from a formatted display string.
///
/// When `for_ts` is true, status placeholders (such as leading `"N/A"` or `"Timeout"`)
/// and unparseable strings yield `f32::NAN` so time-series aggregators skip them.
pub fn get_val(s: &str, for_ts: bool) -> f32 {
    if for_ts && is_ts_placeholder(s) {
        return f32::NAN;
    }

    static RE_PCT: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(-?\d+\.?\d*)%").expect("valid percentage regex"));
    static RE_FLOAT: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(-?\d+\.?\d*)").expect("valid float regex"));

    if let Some(caps) = RE_PCT.captures(s) {
        return caps[1].parse::<f32>().unwrap_or(0.0);
    }

    if PAGING_MARKERS.iter().any(|marker| s.contains(marker)) {
        return RE_FLOAT
            .captures_iter(s)
            .filter_map(|caps| caps[1].parse::<f32>().ok())
            .fold(0.0, f32::max);
    }

    if let Some(caps) = RE_FLOAT.captures(s) {
        return caps[1].parse::<f32>().unwrap_or(0.0);
    }

    if for_ts { f32::NAN } else { 0.0 }
}

pub struct SessionSummary {
    pub start_time: SystemTime,
    pub iterations: u64,
    pub host_cpu_max: f32,
    pub host_ram_max: f32,
    pub time_series: Vec<HashMap<String, f32>>,
    pub time_series_str: Vec<BTreeMap<String, String>>,
    pub boot_logs: HashMap<String, Vec<(String, String)>>,
    pub device_names: HashMap<String, String>,
    pub netsim_summary_lines: Vec<String>,
}

impl Default for SessionSummary {
    fn default() -> Self {
        Self::new()
    }
}

impl SessionSummary {
    pub fn new() -> Self {
        Self {
            start_time: SystemTime::now(),
            iterations: 0,
            host_cpu_max: 0.0,
            host_ram_max: 0.0,
            time_series: Vec::new(),
            time_series_str: Vec::new(),
            boot_logs: HashMap::new(),
            device_names: HashMap::new(),
            netsim_summary_lines: Vec::new(),
        }
    }

    pub fn migrate_keys(&mut self, old_key: &str, new_key: &str) {
        self.migrate_keys_from(old_key, new_key, 0, 0);
    }

    pub fn migrate_keys_from(
        &mut self,
        old_key: &str,
        new_key: &str,
        start_ts_idx: usize,
        start_boot_log_idx: usize,
    ) {
        let column_pairs: Vec<(String, String)> = GUEST_TIME_SERIES_METRICS
            .iter()
            .map(|metric| (format!("{metric}_{old_key}"), format!("{metric}_{new_key}")))
            .collect();
        let ts_start = start_ts_idx.min(self.time_series.len());
        for entry in &mut self.time_series[ts_start..] {
            for (old_full_key, new_full_key) in &column_pairs {
                if let Some(val) = entry.remove(old_full_key) {
                    if entry.get(new_full_key).is_none_or(|v| v.is_nan()) {
                        entry.insert(new_full_key.clone(), val);
                    }
                }
            }
        }
        let old_ns_key = format!("netsim_state_{old_key}");
        let new_ns_key = format!("netsim_state_{new_key}");
        let str_start = start_ts_idx.min(self.time_series_str.len());
        for entry in &mut self.time_series_str[str_start..] {
            if let Some(val) = entry.remove(&old_ns_key) {
                if entry.get(&new_ns_key).is_none_or(String::is_empty) {
                    entry.insert(new_ns_key.clone(), val);
                }
            }
        }
        if let Some(mut logs) = self.boot_logs.remove(old_key) {
            let split_idx = start_boot_log_idx.min(logs.len());
            let migrated = logs.split_off(split_idx);
            if !logs.is_empty() {
                self.boot_logs.insert(old_key.to_string(), logs);
            }
            if !migrated.is_empty() {
                self.boot_logs.entry(new_key.to_string()).or_default().extend(migrated);
            }
        }
        if !has_unmigrated_entries(self, old_key) {
            if let Some(name) = self.device_names.remove(old_key) {
                let keep_for_new = (start_ts_idx == 0 && start_boot_log_idx == 0)
                    || (!name.contains("Unknown") && name != old_key);
                if keep_for_new
                    && self
                        .device_names
                        .get(new_key)
                        .is_none_or(|existing| existing.is_empty() || existing == old_key)
                {
                    self.device_names.insert(new_key.to_string(), name);
                }
            }
        }
    }
}

/// Summary statistics (average, peak, 95th percentile, and sample count) for a metric series.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct SeriesStats {
    pub avg: f32,
    pub peak: f32,
    pub p95: f32,
    pub count: usize,
}

/// Computes average, maximum (peak), P95, and finite sample count for `key` across `time_series`.
pub fn compute_series_stats(time_series: &[HashMap<String, f32>], key: &str) -> SeriesStats {
    let mut vals: Vec<f32> = time_series
        .iter()
        .filter_map(|entry| entry.get(key).copied())
        .filter(|v| v.is_finite())
        .collect();

    if vals.is_empty() {
        return SeriesStats::default();
    }

    let avg = vals.iter().sum::<f32>() / vals.len() as f32;
    vals.sort_by(f32::total_cmp);
    let len = vals.len();
    let p95_idx = (P95_PERCENTILE * len).div_ceil(PERCENT_SCALE) - 1;

    SeriesStats { avg, peak: vals[len - 1], p95: vals[p95_idx], count: len }
}

fn per_emulator_key(key: &str) -> Option<(usize, &str)> {
    for (idx, &prefix) in CSV_PER_EMULATOR_PREFIXES.iter().enumerate() {
        if let Some(rest) = key.strip_prefix(prefix) {
            if let Some(emu_key) = rest.strip_prefix('_') {
                return Some((idx, emu_key));
            }
        }
    }
    None
}

fn csv_column_sort_key(key: &str) -> (u8, &str, usize, &str) {
    if let Some(idx) = CSV_HOST_COLUMNS.iter().position(|&c| c == key) {
        return (0, "", idx, key);
    }
    if key.starts_with("host_") {
        return (1, "", 0, key);
    }
    if let Some(idx) = CSV_NETSIM_DAEMON_COLUMNS.iter().position(|&c| c == key) {
        return (2, "", idx, key);
    }
    if let Some((idx, emu_key)) = per_emulator_key(key) {
        return (4, emu_key, idx, key);
    }
    if key.starts_with("netsim_") {
        return (3, "", 0, key);
    }
    (5, "", 0, key)
}

fn system_time_to_local_datetime(st: SystemTime) -> Option<DateTime<Local>> {
    let (secs, nsecs) = match st.duration_since(SystemTime::UNIX_EPOCH) {
        Ok(dur) => (i64::try_from(dur.as_secs()).ok()?, dur.subsec_nanos()),
        Err(err) => {
            let dur = err.duration();
            let whole = i64::try_from(dur.as_secs()).ok()?;
            if dur.subsec_nanos() == 0 {
                (whole.checked_neg()?, 0)
            } else {
                (whole.checked_neg()?.checked_sub(1)?, 1_000_000_000 - dur.subsec_nanos())
            }
        }
    };
    let utc = DateTime::from_timestamp(secs, nsecs)?;
    let headroom = chrono::TimeDelta::days(2);
    utc.naive_utc().checked_add_signed(headroom)?;
    utc.naive_utc().checked_sub_signed(headroom)?;
    Some(utc.with_timezone(&Local))
}

fn format_csv_timestamp(start_time: SystemTime, elapsed: f32) -> String {
    let sample_time = Duration::try_from_secs_f64(elapsed as f64)
        .ok()
        .and_then(|delta| start_time.checked_add(delta));
    let datetime = sample_time
        .and_then(system_time_to_local_datetime)
        .or_else(|| system_time_to_local_datetime(start_time))
        .unwrap_or_else(|| DateTime::UNIX_EPOCH.with_timezone(&Local));
    datetime.format("%Y-%m-%d %H:%M:%S").to_string()
}

#[cfg(test)]
pub fn export_csv(
    summary: &SessionSummary,
    filename: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let file_path = expand_tilde_with(filename, resolve_home_dir().as_deref());
    export_csv_to_path(summary, &file_path)
}

fn export_csv_to_path(
    summary: &SessionSummary,
    file_path: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut all_keys: Vec<String> = Vec::new();
    let mut seen = HashSet::new();
    for entry in &summary.time_series {
        for key in entry.keys() {
            if seen.insert(key.clone()) {
                all_keys.push(key.clone());
            }
        }
    }
    for entry in &summary.time_series_str {
        for key in entry.keys() {
            if seen.insert(key.clone()) {
                all_keys.push(key.clone());
            }
        }
    }

    let kept_emulators: HashSet<String> = deduplicated_emulators(summary).into_iter().collect();
    let csv_emu_keys: Vec<String> =
        all_keys.iter().filter_map(|k| per_emulator_key(k).map(|(_, ek)| ek.to_string())).collect();
    all_keys.retain(|col| {
        let Some((_, emu_key)) = per_emulator_key(col) else {
            return true;
        };
        if summary.device_names.contains_key(emu_key) {
            return kept_emulators.contains(emu_key);
        }
        if emu_key.contains('_') || has_unmigrated_entries(summary, emu_key) {
            return true;
        }
        let suffix = format!("_{emu_key}");
        !summary.device_names.keys().any(|k| k.ends_with(&suffix))
            && !csv_emu_keys.iter().any(|k| k.ends_with(&suffix))
    });

    all_keys.sort_by(|a, b| csv_column_sort_key(a).cmp(&csv_column_sort_key(b)));

    if let Some(parent) = file_path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let file = File::create(file_path)?;
    let mut wtr = Writer::from_writer(file);

    wtr.write_record(&all_keys)?;

    for (idx, entry) in summary.time_series.iter().enumerate() {
        let str_entry = summary.time_series_str.get(idx);
        let mut row = Vec::with_capacity(all_keys.len());
        for key in &all_keys {
            let val = if key == "time" {
                let elapsed = entry.get(key).copied().unwrap_or(0.0);
                format_csv_timestamp(summary.start_time, elapsed)
            } else if let Some(&v) = entry.get(key) {
                if v.is_finite() { format!("{v:.1}") } else { String::new() }
            } else if let Some(sv) = str_entry.and_then(|m| m.get(key)) {
                sv.clone()
            } else {
                String::new()
            };
            row.push(val);
        }
        wtr.write_record(&row)?;
    }

    wtr.flush()?;
    Ok(())
}

pub fn get_chart_str(summary: &SessionSummary, key: &str, title: &str) -> Option<String> {
    let points: Vec<(f32, f32)> = summary
        .time_series
        .iter()
        .filter_map(|entry| {
            let t = *entry.get("time")?;
            let v = *entry.get(key)?;
            (t.is_finite() && v.is_finite()).then_some((t, v))
        })
        .collect();

    if points.len() >= 2 {
        Some(crate::chart::render_chart(&points, DEFAULT_CHART_WIDTH, DEFAULT_CHART_HEIGHT, title))
    } else {
        None
    }
}

fn has_unmigrated_entries(summary: &SessionSummary, key: &str) -> bool {
    if summary.boot_logs.get(key).is_some_and(|l| !l.is_empty()) {
        return true;
    }
    let ns_key = format!("netsim_state_{key}");
    if summary.time_series_str.iter().any(|entry| entry.get(&ns_key).is_some_and(|v| !v.is_empty()))
    {
        return true;
    }
    let metric_keys: Vec<String> =
        GUEST_TIME_SERIES_METRICS.iter().map(|metric| format!("{metric}_{key}")).collect();
    summary
        .time_series
        .iter()
        .any(|entry| metric_keys.iter().any(|k| entry.get(k).is_some_and(|v| v.is_finite())))
}

/// Returns sorted device keys, dropping bare-serial keys only when an
/// AVD-qualified `<avd>_<serial>` key exists and the bare key has no
/// unmigrated data of its own.
fn deduplicated_emulators(summary: &SessionSummary) -> Vec<String> {
    let is_bare_serial = |key: &str| {
        summary.device_names.get(key).is_some_and(|name| {
            name.strip_prefix(key).is_some_and(|rest| rest.is_empty() || rest.starts_with(" | "))
        })
    };
    let raw_keys: Vec<&String> = summary.device_names.keys().collect();
    let mut emulators: Vec<String> = raw_keys
        .iter()
        .filter(|key| {
            !is_bare_serial(key)
                || has_unmigrated_entries(summary, key)
                || !raw_keys.iter().any(|k| k.ends_with(&format!("_{key}")))
        })
        .map(|&k| k.clone())
        .collect();
    emulators.sort();
    emulators
}

fn parse_adb_version_output(output: Option<std::process::Output>) -> String {
    output
        .filter(|out| out.status.success())
        .and_then(|out| {
            String::from_utf8_lossy(&out.stdout)
                .lines()
                .next()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(ToOwned::to_owned)
        })
        .unwrap_or_else(|| "N/A".to_string())
}

fn cached_adb_version() -> &'static str {
    static ADB_VERSION: LazyLock<String> = LazyLock::new(|| {
        parse_adb_version_output(crate::netsim::run_with_timeout(
            crate::guest::find_adb(),
            &["version"],
            ADB_VERSION_TIMEOUT,
        ))
    });
    ADB_VERSION.as_str()
}

fn format_host_os_info(
    name: Option<&str>,
    os_version: Option<&str>,
    kernel_version: Option<&str>,
) -> String {
    let name = name.map(str::trim).filter(|s| !s.is_empty());
    let ver = os_version.map(str::trim).filter(|s| !s.is_empty());
    let kernel = kernel_version.map(str::trim).filter(|s| !s.is_empty());

    let base = match (name, ver) {
        (Some(n), Some(v)) => {
            let n_low = n.to_lowercase();
            let v_low = v.to_lowercase();
            let already_present = n_low == v_low
                || n_low.ends_with(&format!(" {v_low}"))
                || n_low.split(|c: char| !c.is_alphanumeric() && c != '.').any(|tok| tok == v_low);
            if already_present { n.to_string() } else { format!("{n} {v}") }
        }
        (Some(n), None) => n.to_string(),
        (None, Some(v)) => v.to_string(),
        (None, None) => "Unknown OS".to_string(),
    };

    match kernel {
        Some(k) => format!("{base} (kernel {k})"),
        None => base,
    }
}

fn build_header_section(
    summary: &SessionSummary,
    end_time: SystemTime,
    hardware_specs: &str,
    cpu_cores: usize,
) -> Vec<String> {
    let duration = end_time.duration_since(summary.start_time).unwrap_or_default().as_secs_f32();
    let to_local = |st: SystemTime| {
        system_time_to_local_datetime(st)
            .unwrap_or_else(|| DateTime::UNIX_EPOCH.with_timezone(&Local))
    };
    let start_chrono = to_local(summary.start_time);
    let end_chrono = to_local(end_time);

    let os_info = format_host_os_info(
        System::name().as_deref(),
        System::os_version().as_deref(),
        System::kernel_version().as_deref(),
    );

    let adb_ver = cached_adb_version();

    let is_vm =
        std::fs::read_to_string("/proc/cpuinfo").map(|c| c.contains("hypervisor")).unwrap_or(false);

    vec![
        "Android Emulator & Device Resource Monitor Session Summary".to_string(),
        "=".repeat(SUMMARY_DIVIDER_WIDTH),
        format!("Start Time:  {}", start_chrono.format("%Y-%m-%d %H:%M:%S")),
        format!("End Time:    {}", end_chrono.format("%Y-%m-%d %H:%M:%S")),
        format!("Duration:    {duration:.1} seconds"),
        format!("Total Loops: {}", summary.iterations),
        format!("Host OS:      {os_info}"),
        format!("Host Specs:   {hardware_specs}"),
        format!("Host Cores:   {cpu_cores}"),
        format!("Host Virtual: {is_vm}"),
        format!("ADB Version:  {adb_ver}"),
        String::new(),
    ]
}

fn build_host_section(summary: &SessionSummary) -> Vec<String> {
    let ts = &summary.time_series;
    let cpu = compute_series_stats(ts, "host_cpu");
    let ram = compute_series_stats(ts, "host_ram");
    let thermal_stats = compute_series_stats(ts, "host_thermal_c");
    let disk_read = compute_series_stats(ts, "host_disk_read_mbs");
    let disk_write = compute_series_stats(ts, "host_disk_write_mbs");

    // Hosts without a thermal sensor only ever record 0.0.
    let fmt_thermal = |v: f32| {
        if thermal_stats.peak > 0.0 { format!("{v:.1}°C") } else { "N/A".to_string() }
    };

    vec![
        "HOST SYSTEM:".to_string(),
        format!("  Average CPU Usage: {:.1}%", cpu.avg),
        format!("  Peak CPU Usage:    {:.1}%", summary.host_cpu_max.max(cpu.peak)),
        format!("  P95 CPU Usage:     {:.1}%", cpu.p95),
        format!("  Average RAM Usage: {:.1}%", ram.avg),
        format!("  Peak RAM Usage:    {:.1}%", summary.host_ram_max.max(ram.peak)),
        format!("  P95 RAM Usage:     {:.1}%", ram.p95),
        format!("  Average Thermal:   {}", fmt_thermal(thermal_stats.avg)),
        format!("  Peak Thermal:      {}", fmt_thermal(thermal_stats.peak)),
        format!("  P95 Thermal:       {}", fmt_thermal(thermal_stats.p95)),
        format!("  Avg Disk Read:     {:.1} MB/s", disk_read.avg),
        format!("  Peak Disk Read:    {:.1} MB/s", disk_read.peak),
        format!("  P95 Disk Read:     {:.1} MB/s", disk_read.p95),
        format!("  Avg Disk Write:    {:.1} MB/s", disk_write.avg),
        format!("  Peak Disk Write:   {:.1} MB/s", disk_write.peak),
        format!("  P95 Disk Write:    {:.1} MB/s", disk_write.p95),
        String::new(),
    ]
}

fn build_netsim_section(summary: &SessionSummary) -> Vec<String> {
    let ts = &summary.time_series;
    let cpu = compute_series_stats(ts, "netsim_cpu");
    let ram = compute_series_stats(ts, "netsim_ram");

    let mut lines = Vec::new();
    if cpu.count > 0 {
        lines.extend([
            "NETSIM:".to_string(),
            format!("  Average CPU Usage: {:.1}%", cpu.avg),
            format!("  Peak CPU Usage:    {:.1}%", cpu.peak),
            format!("  P95 CPU Usage:     {:.1}%", cpu.p95),
            format!("  Average RAM Usage: {:.1}MB", ram.avg),
            format!("  Peak RAM Usage:    {:.1}MB", ram.peak),
            format!("  P95 RAM Usage:     {:.1}MB", ram.p95),
            String::new(),
        ]);
    } else if !summary.netsim_summary_lines.is_empty() {
        lines.push("NETSIM:".to_string());
    }
    lines.extend(summary.netsim_summary_lines.iter().cloned());
    lines
}

fn build_guests_section(summary: &SessionSummary, emulators: &[String]) -> Vec<String> {
    let ts = &summary.time_series;
    let mut lines = Vec::new();

    for serial in emulators {
        let name = summary.device_names.get(serial).cloned().unwrap_or_else(|| serial.clone());
        let cpu_tot = compute_series_stats(ts, &format!("emu_cpu_tot_{serial}"));
        let cpu_adj = compute_series_stats(ts, &format!("emu_cpu_adj_{serial}"));
        let ram = compute_series_stats(ts, &format!("emu_ram_{serial}"));
        let swap = compute_series_stats(ts, &format!("emu_swap_{serial}"));
        let qemu_cpu = compute_series_stats(ts, &format!("qemu_cpu_{serial}"));
        let qemu_ram = compute_series_stats(ts, &format!("qemu_ram_{serial}"));

        lines.push(format!("GUEST SYSTEM ({name}):"));
        lines.push(format!("  Average Total CPU:    {:.1}%", cpu_tot.avg));
        lines.push(format!("  Peak Total CPU:       {:.1}%", cpu_tot.peak));
        lines.push(format!("  P95 Total CPU:        {:.1}%", cpu_tot.p95));
        lines.push(format!("  Average Adjusted CPU: {:.1}%", cpu_adj.avg));
        lines.push(format!("  Peak Adjusted CPU:    {:.1}%", cpu_adj.peak));
        lines.push(format!("  P95 Adjusted CPU:     {:.1}%", cpu_adj.p95));
        lines.push(format!("  Average RAM Usage:    {:.1}%", ram.avg));
        lines.push(format!("  Peak RAM Usage:       {:.1}%", ram.peak));
        lines.push(format!("  P95 RAM Usage:        {:.1}%", ram.p95));
        lines.push(format!("  Average Swap Usage:   {:.1}MB", swap.avg));
        lines.push(format!("  Peak Swap Usage:      {:.1}MB", swap.peak));
        lines.push(format!("  P95 Swap Usage:       {:.1}MB", swap.p95));
        if qemu_cpu.count > 0 {
            lines.push(format!("  Host QEMU CPU (Avg): {:.1}%", qemu_cpu.avg));
            lines.push(format!("  Host QEMU CPU (Max): {:.1}%", qemu_cpu.peak));
            lines.push(format!("  Host QEMU CPU (P95): {:.1}%", qemu_cpu.p95));
            lines.push(format!("  Host QEMU RAM (Avg): {:.1}MB", qemu_ram.avg));
            lines.push(format!("  Host QEMU RAM (Max): {:.1}MB", qemu_ram.peak));
            lines.push(format!("  Host QEMU RAM (P95): {:.1}MB", qemu_ram.p95));
        }
        if let Some(log) = summary.boot_logs.get(serial).filter(|l| !l.is_empty()) {
            lines.push("  Boot Progression Log:".to_string());
            for (delta, stats) in log {
                lines.push(format!("    {delta} | {stats}"));
            }
        }
        lines.push(String::new());
    }

    lines
}

fn should_render_chart(summary: &SessionSummary, key: &str) -> bool {
    if key == "host_disk_read_mbs" || key == "host_disk_write_mbs" {
        return true;
    }
    summary
        .time_series
        .iter()
        .filter_map(|entry| entry.get(key).copied())
        .any(|v| v.is_finite() && v > 0.0)
}

fn build_charts_section(summary: &SessionSummary, emulators: &[String]) -> Vec<String> {
    let mut chart_lines = Vec::new();

    for &(key, title) in HOST_AND_NETSIM_CHARTS {
        if !should_render_chart(summary, key) {
            continue;
        }
        if let Some(chart) = get_chart_str(summary, key, title) {
            chart_lines.push(chart);
            chart_lines.push(String::new());
        }
    }

    for serial in emulators {
        let mut guest_charts = Vec::new();
        for &(metric_prefix, title_prefix) in GUEST_CHART_SPECS {
            let key = format!("{metric_prefix}_{serial}");
            if !should_render_chart(summary, &key) {
                continue;
            }
            let title = format!("{title_prefix} ({serial})");
            if let Some(chart) = get_chart_str(summary, &key, &title) {
                guest_charts.push(chart);
                guest_charts.push(String::new());
            }
        }

        if !guest_charts.is_empty() {
            let name = summary.device_names.get(serial).cloned().unwrap_or_else(|| serial.clone());
            chart_lines.push("\n".to_string());
            chart_lines.push("=".repeat(CHART_BANNER_WIDTH));
            chart_lines.push(format!("CHARTS FOR: {name}"));
            chart_lines.push("=".repeat(CHART_BANNER_WIDTH));
            chart_lines.push("\n".to_string());
            chart_lines.extend(guest_charts);
        }
    }

    chart_lines
}

pub(crate) fn resolve_home_dir() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME")
        .filter(|v| !v.is_empty())
        .or_else(|| std::env::var_os("USERPROFILE").filter(|v| !v.is_empty()))
        .map(std::path::PathBuf::from)
}

pub(crate) fn expand_tilde_with(
    raw_path: &str,
    home: Option<&std::path::Path>,
) -> std::path::PathBuf {
    if raw_path.is_empty() {
        return std::path::PathBuf::from(".");
    }
    if raw_path == "~" {
        if let Some(h) = home {
            return h.to_path_buf();
        }
    } else if let Some(rest) = raw_path.strip_prefix("~/").or_else(|| raw_path.strip_prefix("~\\"))
    {
        if let Some(h) = home {
            return if rest.is_empty() { h.to_path_buf() } else { h.join(rest) };
        }
    }
    std::path::PathBuf::from(raw_path)
}

pub fn save_summary(
    summary: &SessionSummary,
    out_dir: &str,
    save_csv: bool,
    save_txt: bool,
    hardware_specs: &str,
    cpu_cores: usize,
    is_autosave: bool,
) {
    let base_dir = expand_tilde_with(out_dir, resolve_home_dir().as_deref());
    let stamp = (!is_autosave).then(|| Local::now().format("%Y%m%d_%H%M%S").to_string());
    let kind = stamp.as_deref().map_or(SaveKind::Autosave, |stamp| SaveKind::Final { stamp });
    save_summary_to_dir(summary, &base_dir, save_csv, save_txt, hardware_specs, cpu_cores, kind);
}

/// Which pair of files a save writes.
#[derive(Clone, Copy)]
enum SaveKind<'a> {
    /// The periodic `..._autosave.{txt,csv}` pair, overwritten in place.
    Autosave,
    /// The session's final `..._<stamp>.{txt,csv}` pair, which replaces the autosave pair.
    Final { stamp: &'a str },
}

impl SaveKind<'_> {
    fn file_name(self, ext: &str) -> String {
        match self {
            Self::Autosave => format!("device_monitor_summary_autosave.{ext}"),
            Self::Final { stamp } => format!("device_monitor_summary_{stamp}.{ext}"),
        }
    }
}

const SAVE_REMEDIATION_HINT: &str =
    "Check --out-dir permissions, open file locks, and available disk space.";

fn format_save_error(kind: &str, path: &std::path::Path, err: &dyn std::fmt::Display) -> String {
    format!("Failed to write {kind} '{}': {err}. {SAVE_REMEDIATION_HINT}", path.display())
}

fn save_summary_to_dir(
    summary: &SessionSummary,
    base_dir: &std::path::Path,
    save_csv: bool,
    save_txt: bool,
    hardware_specs: &str,
    cpu_cores: usize,
    kind: SaveKind<'_>,
) {
    if summary.iterations == 0 && summary.time_series.is_empty() {
        return;
    }

    if save_txt || (save_csv && !summary.time_series.is_empty()) {
        let _ = std::fs::create_dir_all(base_dir);
    }

    let is_autosave = matches!(kind, SaveKind::Autosave);
    let mut txt_ok = !save_txt;
    if save_txt || !is_autosave {
        let end_time = SystemTime::now();
        let emulators = deduplicated_emulators(summary);

        let mut lines = build_header_section(summary, end_time, hardware_specs, cpu_cores);
        lines.extend(build_host_section(summary));
        lines.extend(build_netsim_section(summary));
        lines.extend(build_guests_section(summary, &emulators));
        lines.extend(build_charts_section(summary, &emulators));

        if !is_autosave {
            let divider = "=".repeat(SUMMARY_DIVIDER_WIDTH);
            println!("\n{divider}");
            println!("SESSION SUMMARY (Console Output)");
            println!("{divider}");
            println!("{}", lines.join("\n"));
            println!("{divider}\n");
        }

        if save_txt {
            let txt_path = base_dir.join(kind.file_name("txt"));

            if let Err(e) = std::fs::write(&txt_path, lines.join("\n")) {
                eprintln!("{}", format_save_error("summary file", &txt_path, &e));
            } else {
                txt_ok = true;
                if !is_autosave {
                    println!("Session summary saved to {}", txt_path.display());
                }
            }
        }
    }

    let mut csv_ok = !save_csv || summary.time_series.is_empty();
    if save_csv && !summary.time_series.is_empty() {
        let csv_path = base_dir.join(kind.file_name("csv"));
        if let Err(e) = export_csv_to_path(summary, &csv_path) {
            eprintln!("{}", format_save_error("CSV file", &csv_path, &e));
        } else {
            csv_ok = true;
            if !is_autosave {
                println!("CSV exported to {}", csv_path.display());
            }
        }
    }

    // An autosave file is only superseded by a final file of the same kind that was written.
    if !is_autosave {
        if txt_ok {
            let _ = std::fs::remove_file(base_dir.join(SaveKind::Autosave.file_name("txt")));
        }
        if csv_ok {
            let _ = std::fs::remove_file(base_dir.join(SaveKind::Autosave.file_name("csv")));
        }
    }
}

#[cfg(test)]
#[path = "../tests/summary_test.rs"]
mod tests;
