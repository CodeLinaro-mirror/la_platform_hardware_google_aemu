mod chart;
mod guest;
mod host;
mod netsim;
mod provider;
mod summary;
mod ui;

use clap::Parser;
use futures::FutureExt;
use provider::{GuestProvider, GuestStats, HostProvider};
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::task::JoinHandle;
use tokio::time::Instant;
use ui::GuestUiStats;

const MIN_INTERVAL_SECS: f64 = 1.0;
const MAX_INTERVAL_SECS: f64 = 10.0;
const DEFAULT_INTERVAL_SECS: f64 = 2.0;
const MIN_SLEEP_SECS: f64 = 0.1;
const MAX_TIME_SERIES_LEN: usize = 43_200;
const DISCONNECT_EVICT_TIMEOUT_SECS: u64 = 5;
const ONLINE_FLASH_SECS: u64 = 10;
const POST_BOOT_TRACK_SECS: u64 = 300;
const MAX_BOOT_LOG_WINDOW_SECS: u64 = 900;
const BOOT_LOG_PACING_SECS: u64 = 5;
const DISCONNECTED_DISPLAY_WINDOW_SECS: u64 = 120;
const FAST_POLL_STEP_SECS: f64 = 0.25;
const MIN_FAST_POLL_STEP_SECS: f64 = 0.01;
const DEFAULT_TERM_WIDTH: u16 = 80;

#[derive(Parser, Debug, Clone)]
#[command(author, version, about = "Cross-platform Android Emulator & Device Resource Monitor", long_about = None)]
pub struct Args {
    #[arg(short = '1', long)]
    pub once: bool,

    #[arg(long)]
    pub no_summary: bool,

    #[arg(long)]
    pub csv: bool,

    #[arg(short, long, default_value = ".")]
    pub out_dir: PathBuf,

    #[arg(short, long, default_value_t = 2.0)]
    pub interval: f64,

    #[arg(long)]
    pub headless: bool,

    #[arg(long, default_value_t = 5)]
    pub auto_save_interval: u32,

    #[arg(long, num_args = 0..=1, default_missing_value = "")]
    pub netsim_pcap: Option<String>,

    #[arg(long, default_value_t = 500)]
    pub netsim_pcap_max_mb: u64,

    #[arg(long)]
    pub no_netsim_log: bool,
}

/// Awaits `task` until `deadline` (`None`: no limit); `None` if it is still running then.
async fn join_by<T>(
    task: &mut JoinHandle<T>,
    deadline: Option<Instant>,
) -> Option<Result<T, tokio::task::JoinError>> {
    match deadline {
        Some(deadline) => tokio::time::timeout_at(deadline, task).await.ok(),
        None => Some(task.await),
    }
}

fn sanitize_interval(interval: f64) -> f64 {
    if interval.is_finite() {
        interval.clamp(MIN_INTERVAL_SECS, MAX_INTERVAL_SECS)
    } else {
        DEFAULT_INTERVAL_SECS
    }
}

fn push_capped<T>(series: &mut Vec<T>, item: T, max_len: usize) {
    if max_len == 0 {
        series.clear();
        return;
    }
    if series.len() >= max_len {
        let excess = series.len() + 1 - max_len;
        series.drain(..excess);
    }
    series.push(item);
}

fn should_use_alternate_screen_with_tty(args: &Args, stdout_is_tty: bool) -> bool {
    !args.once && !args.headless && stdout_is_tty
}

fn should_use_alternate_screen(args: &Args) -> bool {
    use std::io::IsTerminal;
    should_use_alternate_screen_with_tty(args, std::io::stdout().is_terminal())
}

pub fn calculate_sleep_duration(interval: f64, loop_elapsed: f64) -> f64 {
    let safe_interval = if interval.is_finite() { interval } else { DEFAULT_INTERVAL_SECS };
    let safe_elapsed = if loop_elapsed.is_finite() { loop_elapsed.max(0.0) } else { 0.0 };
    (safe_interval - safe_elapsed).max(MIN_SLEEP_SECS)
}

/// Per-serial tracking state maintained across monitor ticks until cache eviction.
#[derive(Debug, Clone, Default)]
pub struct DeviceState {
    pub cached_model: Option<String>,
    pub disconnect_since: Option<Instant>,
    pub online_indicator_until: Option<Instant>,
    pub last_status: Option<String>,
    pub boot_completed: bool,
    pub boot_finished_time: Option<Instant>,
    pub is_booting: Option<bool>,
    pub last_seen_time: Option<Instant>,
    pub last_guest_res: Option<GuestStats>,
    /// Whether `last_guest_res` came from a query that finished during the current tick.
    pub guest_res_fresh: bool,
    pub first_seen_time: Option<Instant>,
    pub last_boot_log_time: Option<Instant>,
    pub last_known_pid: Option<u32>,
    pub group_procs: BTreeMap<u32, u64>,
    pub host_process_exited: bool,
    pub seen_offline_without_host: bool,
    pub last_avd_name: Option<String>,
    pub current_storage_key: Option<String>,
    pub evicted: bool,
}

fn build_group_procs(pid: u32, g_info: Option<&netsim::EmuGroupNetsimInfo>) -> BTreeMap<u32, u64> {
    let mut procs = BTreeMap::new();
    if let Some(g) = g_info {
        for (&member_pid, &st) in &g.member_start_times {
            procs.insert(member_pid, st);
        }
        for &member_pid in &g.pids {
            procs.entry(member_pid).or_insert(0);
        }
        let primary_st = procs.get(&pid).copied().unwrap_or(0).max(if g.primary_pid == pid {
            g.primary_start_time
        } else {
            0
        });
        procs.insert(pid, primary_st);
    } else {
        procs.insert(pid, 0);
    }
    procs
}

fn group_procs_match(known: &BTreeMap<u32, u64>, curr: &BTreeMap<u32, u64>) -> bool {
    curr.iter().any(|(pid, &st)| {
        known.get(pid).is_some_and(|&prev_st| prev_st == 0 || st == 0 || prev_st == st)
    })
}

fn merge_group_procs(dst: &mut BTreeMap<u32, u64>, src: &BTreeMap<u32, u64>) {
    for (&pid, &st) in src {
        if st > 0 {
            dst.insert(pid, st);
        } else {
            dst.entry(pid).or_insert(0);
        }
    }
}

impl DeviceState {
    /// Clears the guest result and the boot and storage-key tracking when a new host QEMU PID
    /// replaces an old one on the same serial.
    pub fn reset_for_new_pid(&mut self) {
        self.cached_model = None;
        self.disconnect_since = None;
        self.online_indicator_until = None;
        self.last_status = None;
        self.last_guest_res = None;
        self.boot_completed = false;
        self.boot_finished_time = None;
        self.is_booting = None;
        self.first_seen_time = None;
        self.last_boot_log_time = None;
        self.host_process_exited = false;
        self.seen_offline_without_host = false;
        self.last_avd_name = None;
        self.current_storage_key = None;
        self.group_procs.clear();
    }

    fn effective_avd_name(&self, serial: &str, host_avd: Option<&str>) -> Option<String> {
        host_avd.map(str::to_string).or_else(|| self.last_avd_name.clone()).or_else(|| {
            let key = self.current_storage_key.as_deref()?;
            let suffix = format!("_{serial}");
            (key != serial).then(|| key.strip_suffix(&suffix)).flatten().map(str::to_string)
        })
    }

    /// Whether `curr_procs` (`pid -> start_time`) belongs to the same emulator session
    /// currently tracked by `self`.
    pub fn matches_emu_session(&self, curr_procs: &BTreeMap<u32, u64>) -> bool {
        self.last_known_pid.is_some()
            && !self.group_procs.is_empty()
            && group_procs_match(&self.group_procs, curr_procs)
    }

    /// Whether this serial has already tracked a prior emulator session (live PID,
    /// exited host process, or pre-host offline state).
    pub fn had_prior_emulator(&self) -> bool {
        self.last_known_pid.is_some() || self.host_process_exited || self.seen_offline_without_host
    }

    fn record_emu_group(&mut self, pid: u32, curr_procs: &BTreeMap<u32, u64>) {
        self.host_process_exited = false;
        self.seen_offline_without_host = false;
        self.last_known_pid = Some(pid);
        merge_group_procs(&mut self.group_procs, curr_procs);
    }

    fn clear_host_process(&mut self) {
        if self.last_known_pid.is_some() {
            self.host_process_exited = true;
        }
        self.last_known_pid = None;
        self.group_procs.clear();
    }

    /// Whether boot-phase metrics should be recorded at `now` given the current `adb` status.
    pub fn should_track_boot(&self, is_booting: bool, status: &str, now: Instant) -> bool {
        if self
            .first_seen_time
            .is_some_and(|t| now.saturating_duration_since(t).as_secs() >= MAX_BOOT_LOG_WINDOW_SECS)
        {
            return false;
        }
        if is_booting || (status == "offline" && !self.boot_completed) {
            true
        } else if self.boot_completed {
            self.boot_finished_time
                .map(|finished| {
                    now.saturating_duration_since(finished).as_secs() < POST_BOOT_TRACK_SECS
                })
                .unwrap_or(false)
        } else {
            false
        }
    }
}

fn initial_host_stats() -> crate::host::HostStats {
    crate::host::HostStats {
        cpu_usage: 0.0,
        ram_used_gb: 0.0,
        ram_total_gb: 0.0,
        ram_percent: 0.0,
        swap_used_gb: 0.0,
        swap_total_gb: 0.0,
        swap_percent: 0.0,
        disk_read_mbs: 0.0,
        disk_write_mbs: 0.0,
        emulators: vec![],
        netsim_cpu: 0.0,
        netsim_ram_mb: 0.0,
        netsim_count: 0,
        netsim_daemons: vec![],
        emu_groups: HashMap::new(),
        gpu_percent: None,
        gpu_memory_mb: None,
        thermal_temp: None,
        paging_str: "Calculating...".to_string(),
        paging_in: 0.0,
        paging_out: 0.0,
        battery_str: "Calculating...".to_string(),
        battery_percent: None,
    }
}

pub(crate) fn parse_serial_port(serial: &str) -> Option<u16> {
    serial.strip_prefix("emulator-").and_then(|s| s.parse::<u16>().ok())
}

pub(crate) fn serial_matches_emu_port(serial: &str, emu_port: Option<u16>) -> bool {
    match (parse_serial_port(serial), emu_port) {
        (Some(serial_port), Some(port)) => serial_port == port,
        _ => false,
    }
}

/// Rounds a time-series value to one decimal, as the Python monitor stores it.
fn round_1dp(v: f32) -> f32 {
    (v * 10.0).round() / 10.0
}

/// Guest metrics recorded per emulator; see `Monitor::process_guest_results`.
const GUEST_METRIC_PREFIXES: [&str; 6] =
    ["emu_cpu_tot", "emu_cpu_adj", "emu_ram", "emu_swap", "emu_errs", "emu_disk_pct"];

/// Records an emulator process's QEMU metrics under `storage_key` (NaN when the host sample
/// is not this tick's), plus NaN for each guest metric this tick has no result for, so every
/// emulator gets the full column set in the CSV.
fn record_qemu_metrics(
    ts_entry: &mut HashMap<String, f32>,
    storage_key: &str,
    emu: &crate::host::EmulatorProcess,
    fresh: bool,
) {
    let fresh_or_nan = |v: f32| if fresh { v } else { f32::NAN };
    for (prefix, val) in [
        ("qemu_cpu", round_1dp(emu.cpu_usage)),
        ("qemu_ram", round_1dp(emu.ram_mb as f32)),
        ("qemu_cs", emu.context_switches as f32),
        ("qemu_gpu", round_1dp(emu.gpu_load.unwrap_or(0.0))),
    ] {
        ts_entry.insert(format!("{prefix}_{storage_key}"), fresh_or_nan(val));
    }
    record_missing_guest_metrics(ts_entry, storage_key);
}

/// Records NaN for each guest metric of `storage_key` this tick has no result for.
fn record_missing_guest_metrics(ts_entry: &mut HashMap<String, f32>, storage_key: &str) {
    for prefix in GUEST_METRIC_PREFIXES {
        ts_entry.entry(format!("{prefix}_{storage_key}")).or_insert(f32::NAN);
    }
}

/// Identity and slice offsets of the emulator session that recorded metrics under a bare serial.
#[derive(Debug, Clone, Default)]
struct BareKeyOwner {
    group_procs: BTreeMap<u32, u64>,
    offline_without_host: bool,
    start_ts_idx: usize,
    start_iters: u64,
    start_boot_log_idx: usize,
    prior_device_name: Option<String>,
}

impl BareKeyOwner {
    fn new(
        ts_len: usize,
        iters: u64,
        boot_log_len: usize,
        prior_device_name: Option<String>,
    ) -> Self {
        Self {
            group_procs: BTreeMap::new(),
            offline_without_host: false,
            start_ts_idx: ts_len,
            start_iters: iters,
            start_boot_log_idx: boot_log_len,
            prior_device_name,
        }
    }

    fn matches(&self, curr_procs: &BTreeMap<u32, u64>) -> bool {
        if self.offline_without_host && !curr_procs.is_empty() {
            return false;
        }
        if self.group_procs.is_empty() || curr_procs.is_empty() {
            return true;
        }
        group_procs_match(&self.group_procs, curr_procs)
    }

    fn belongs_to_live_procs(&self, live_procs: &BTreeMap<u32, u64>) -> bool {
        !self.offline_without_host
            && !self.group_procs.is_empty()
            && !live_procs.is_empty()
            && group_procs_match(&self.group_procs, live_procs)
    }

    fn effective_start_ts_idx(&self, curr_ts_len: usize, curr_iters: u64) -> usize {
        let added =
            usize::try_from(curr_iters.saturating_sub(self.start_iters)).unwrap_or(usize::MAX);
        let expected_len = self.start_ts_idx.saturating_add(added);
        let drained = expected_len.saturating_sub(curr_ts_len);
        self.start_ts_idx.saturating_sub(drained)
    }

    fn record(
        &mut self,
        curr_procs: &BTreeMap<u32, u64>,
        offline_without_host: bool,
        ts_len: usize,
        iters: u64,
        boot_log_len: usize,
        curr_device_name: Option<String>,
    ) {
        let replaced = !curr_procs.is_empty()
            && (self.offline_without_host
                || (!self.group_procs.is_empty()
                    && !group_procs_match(&self.group_procs, curr_procs)));
        if replaced {
            self.group_procs.clear();
            self.offline_without_host = false;
            self.start_ts_idx = ts_len;
            self.start_iters = iters;
            self.start_boot_log_idx = boot_log_len;
            self.prior_device_name = curr_device_name;
        } else if curr_procs.is_empty() && offline_without_host && self.group_procs.is_empty() {
            self.offline_without_host = true;
        } else if !curr_procs.is_empty() || !offline_without_host {
            self.offline_without_host = false;
        }
        merge_group_procs(&mut self.group_procs, curr_procs);
    }
}

/// Drops `bare_key_serials[serial]` unless it already tracks a distinct live replacement session.
fn remove_stale_bare_key_owner(
    bare_key_serials: &mut HashMap<String, BareKeyOwner>,
    serial: &str,
    replacement_procs: Option<&BTreeMap<u32, u64>>,
) {
    let owned_by_replacement = match (bare_key_serials.get(serial), replacement_procs) {
        (Some(owner), Some(procs)) => owner.belongs_to_live_procs(procs),
        _ => false,
    };
    if !owned_by_replacement {
        bare_key_serials.remove(serial);
    }
}

/// Moves the history recorded under the bare `serial` to `storage_key` once its AVD is known,
/// provided the bare-serial data came from the same emulator session.
fn note_or_migrate_bare_key(
    summary: &mut summary::SessionSummary,
    bare_key_serials: &mut HashMap<String, BareKeyOwner>,
    serial: &str,
    storage_key: &str,
    curr_procs: &BTreeMap<u32, u64>,
    offline_without_host: bool,
) {
    if storage_key == serial {
        let ts_len = summary.time_series.len();
        let iters = summary.iterations;
        let boot_log_len = summary.boot_logs.get(serial).map_or(0, Vec::len);
        let curr_name = summary.device_names.get(serial).cloned();
        bare_key_serials
            .entry(serial.to_string())
            .or_insert_with(|| BareKeyOwner::new(ts_len, iters, boot_log_len, curr_name.clone()))
            .record(curr_procs, offline_without_host, ts_len, iters, boot_log_len, curr_name);
    } else if let Some(owner) = bare_key_serials.remove(serial) {
        if owner.matches(curr_procs) {
            let start_ts_idx =
                owner.effective_start_ts_idx(summary.time_series.len(), summary.iterations);
            summary.migrate_keys_from(serial, storage_key, start_ts_idx, owner.start_boot_log_idx);
            if summary.device_names.contains_key(serial) {
                if let Some(prev_name) = owner.prior_device_name {
                    summary.device_names.insert(serial.to_string(), prev_name);
                }
            }
        }
    }
}

fn resolve_and_migrate_storage_key(
    summary: &mut summary::SessionSummary,
    state: &mut DeviceState,
    serial: &str,
    avd_name_str: &str,
    model: &str,
    bare_key_serials: &mut HashMap<String, BareKeyOwner>,
) -> String {
    let mut storage_key = if avd_name_str != serial {
        format!("{avd_name_str}_{serial}")
    } else {
        serial.to_string()
    };

    if let Some(old_key) = state.current_storage_key.clone() {
        if storage_key == serial && old_key != serial {
            storage_key = old_key;
        }
    }
    note_or_migrate_bare_key(
        summary,
        bare_key_serials,
        serial,
        &storage_key,
        &state.group_procs,
        state.seen_offline_without_host,
    );
    state.current_storage_key = Some(storage_key.clone());

    // Without a host process (e.g. while an exiting emulator is still
    // listed by adb) the AVD name is unknown; keep the name already shown.
    let avd_known = avd_name_str != serial;
    let model_known = !model.is_empty() && !model.contains("Unknown");
    let is_placeholder_name = summary.device_names.get(&storage_key).is_none_or(|existing| {
        existing == &storage_key
            || existing == avd_name_str
            || existing == serial
            || existing
                .split_once(" | ")
                .is_some_and(|(_, m)| m.is_empty() || m.contains("Unknown"))
    });
    if is_placeholder_name || (model_known && (avd_known || storage_key == serial)) {
        let display_name = if model.is_empty() {
            avd_name_str.to_string()
        } else {
            format!("{avd_name_str} | {model}")
        };
        summary.device_names.insert(storage_key.clone(), display_name);
    }
    storage_key
}

fn record_guest_ts_metrics(
    ts_entry: &mut HashMap<String, f32>,
    storage_key: &str,
    result: &GuestStats,
) {
    let cpu_tot = crate::summary::get_val(&result.cpu_total, true);
    let cpu_adj = crate::summary::get_val(&result.cpu_adjusted, true);
    let ram_pct = crate::summary::get_val(&result.ram, true);
    let swap_val =
        result.swap.as_deref().map(|s| crate::summary::get_val(s, true)).unwrap_or(f32::NAN);

    ts_entry.insert(format!("emu_cpu_tot_{storage_key}"), round_1dp(cpu_tot));
    ts_entry.insert(format!("emu_cpu_adj_{storage_key}"), round_1dp(cpu_adj));
    ts_entry.insert(format!("emu_ram_{storage_key}"), round_1dp(ram_pct));
    ts_entry.insert(format!("emu_swap_{storage_key}"), round_1dp(swap_val));
    ts_entry.insert(
        format!("emu_disk_pct_{storage_key}"),
        result.data_pct.map(round_1dp).unwrap_or(f32::NAN),
    );
    let errs_val = if ram_pct.is_nan() { f32::NAN } else { result.err_count as f32 };
    ts_entry.insert(format!("emu_errs_{storage_key}"), errs_val);
}

fn record_boot_log_if_due(
    summary: &mut summary::SessionSummary,
    state: &mut DeviceState,
    storage_key: &str,
    result: &GuestStats,
    was_booting: bool,
    status: &str,
) -> bool {
    let is_booting = result.is_booting;
    let boot_observed = status == "device" && result.boot_state_known;
    if boot_observed {
        if was_booting && !is_booting {
            state.boot_completed = true;
            state.boot_finished_time = Some(Instant::now());
        } else if !is_booting {
            state.boot_completed = true;
        }
    }

    let current_time = Instant::now();
    let should_track = state.should_track_boot(is_booting, status, current_time);
    if should_track {
        if status != "device" && state.last_known_pid.is_none() {
            return true;
        }
        let first_seen = *state.first_seen_time.get_or_insert(current_time);
        let elapsed_boot = current_time.saturating_duration_since(first_seen).as_secs();
        let pacing_ok = match state.last_boot_log_time {
            Some(t) => current_time.saturating_duration_since(t).as_secs() >= BOOT_LOG_PACING_SECS,
            None => true,
        };
        let has_valid_metrics = !crate::summary::get_val(&result.cpu_adjusted, true).is_nan()
            && !crate::summary::get_val(&result.ram, true).is_nan();

        if elapsed_boot < MAX_BOOT_LOG_WINDOW_SECS && pacing_ok && has_valid_metrics {
            let delta_str = format!("+{elapsed_boot}s");
            let stats_str = if let Some(ram_open) = result.ram.strip_suffix(')') {
                format!(
                    "CPU: {} | RAM: {ram_open}, {} errs, {} data, {} swap)",
                    result.cpu_adjusted,
                    result.err_count,
                    result.data_space.as_deref().unwrap_or("N/A"),
                    result.swap.as_deref().unwrap_or("N/A")
                )
            } else {
                format!(
                    "CPU: {} | RAM: {} ({} errs, {} data, {} swap)",
                    result.cpu_adjusted,
                    result.ram,
                    result.err_count,
                    result.data_space.as_deref().unwrap_or("N/A"),
                    result.swap.as_deref().unwrap_or("N/A")
                )
            };

            summary
                .boot_logs
                .entry(storage_key.to_string())
                .or_default()
                .push((delta_str, stats_str));
            state.last_boot_log_time = Some(current_time);
        }
        return elapsed_boot < MAX_BOOT_LOG_WINDOW_SECS;
    }
    false
}

/// Records the host metrics, or NaN for each when `fresh` is false (the sample is an earlier
/// tick's, already recorded).
fn insert_host_ts_metrics(
    ts_entry: &mut HashMap<String, f32>,
    stats: &crate::host::HostStats,
    fresh: bool,
) {
    let (netsim_cpu, netsim_ram) = if stats.netsim_count > 0 {
        (round_1dp(stats.netsim_cpu), round_1dp(stats.netsim_ram_mb as f32))
    } else {
        (f32::NAN, f32::NAN)
    };
    let (unmapped_cpu, unmapped_ram) = stats
        .emulators
        .iter()
        .filter(|e| e.port.is_none())
        .fold((0.0_f32, 0.0_f32), |(cpu, ram), e| (cpu + e.cpu_usage, ram + e.ram_mb as f32));

    for (key, val) in [
        ("host_cpu", round_1dp(stats.cpu_usage)),
        ("host_ram", round_1dp(stats.ram_percent)),
        ("netsim_cpu", netsim_cpu),
        ("netsim_ram", netsim_ram),
        ("host_gpu", stats.gpu_percent.map(round_1dp).unwrap_or(f32::NAN)),
        ("host_gpu_mem", stats.gpu_memory_mb.map(round_1dp).unwrap_or(f32::NAN)),
        ("host_swap", round_1dp(stats.swap_percent)),
        (
            "host_thermal_c",
            stats.thermal_temp.filter(|&t| t > 0.0).map(round_1dp).unwrap_or(f32::NAN),
        ),
        ("host_disk_read_mbs", round_1dp(stats.disk_read_mbs as f32)),
        ("host_disk_write_mbs", round_1dp(stats.disk_write_mbs as f32)),
        ("host_paging_in", round_1dp(stats.paging_in as f32)),
        ("host_paging_out", round_1dp(stats.paging_out as f32)),
        ("host_battery", stats.battery_percent.map(round_1dp).unwrap_or(f32::NAN)),
        ("host_unmapped_qemu_cpu", round_1dp(unmapped_cpu)),
        ("host_unmapped_qemu_ram", round_1dp(unmapped_ram)),
    ] {
        ts_entry.insert(key.to_string(), if fresh { val } else { f32::NAN });
    }
}

struct Monitor<H, G> {
    host_prov: Arc<Mutex<H>>,
    guest_prov: Arc<G>,
    args: Args,
    hardware_specs: String,
    interval: f64,
    cpu_cores: usize,
    summary: summary::SessionSummary,
    stats: crate::host::HostStats,
    /// Whether `stats` came from a host sample that finished during the current tick.
    host_sample_fresh: bool,
    devices: HashMap<String, String>,
    device_states: HashMap<String, DeviceState>,
    /// Serials whose metrics were recorded under the bare serial before their AVD was known.
    bare_key_serials: HashMap<String, BareKeyOwner>,
    prev_stats: HashMap<String, f32>,
    prev_term_width: u16,
    active_host_task: Option<JoinHandle<crate::host::HostStats>>,
    active_devices_task: Option<JoinHandle<HashMap<String, String>>>,
    active_guest_tasks: HashMap<String, JoinHandle<GuestStats>>,
    last_host_task_start: Instant,
    loop_start_time: Instant,
    last_auto_save: Instant,
    netsim_tracker: Arc<Mutex<netsim::NetsimTracker>>,
}

impl<H: HostProvider + 'static, G: GuestProvider + 'static> Monitor<H, G> {
    fn new(host_prov: H, guest_prov: G, args: Args, hardware_specs: String) -> Self {
        let interval = sanitize_interval(args.interval);
        let cpu_cores = host_prov.get_cpu_cores();
        let netsim_tracker = Arc::new(Mutex::new(netsim::NetsimTracker::new(
            args.netsim_pcap.as_deref(),
            args.netsim_pcap_max_mb,
            !args.no_netsim_log,
            &args.out_dir,
        )));
        let now = Instant::now();
        Self {
            host_prov: Arc::new(Mutex::new(host_prov)),
            guest_prov: Arc::new(guest_prov),
            args,
            hardware_specs,
            interval,
            cpu_cores,
            summary: summary::SessionSummary::new(),
            stats: initial_host_stats(),
            host_sample_fresh: false,
            devices: HashMap::new(),
            device_states: HashMap::new(),
            bare_key_serials: HashMap::new(),
            prev_stats: HashMap::new(),
            prev_term_width: 0,
            active_host_task: None,
            active_devices_task: None,
            active_guest_tasks: HashMap::new(),
            last_host_task_start: now,
            loop_start_time: now,
            last_auto_save: now,
            netsim_tracker,
        }
    }

    /// Starts a host sample unless one from an earlier tick is still running.
    fn spawn_host_task(&mut self) {
        if self.active_host_task.is_some() {
            return;
        }
        let host_clone = self.host_prov.clone();
        let elapsed = self.last_host_task_start.elapsed().as_secs_f64();
        self.last_host_task_start = Instant::now();
        // Host sampling reads /proc and runs CLIs, so keep it off the async workers.
        self.active_host_task = Some(tokio::task::spawn_blocking(move || {
            host_clone
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .get_host_stats(elapsed)
        }));
    }

    /// Waits for the running host sample until `deadline` (`None`: no limit) and keeps it if it
    /// finished; otherwise a later tick collects it.
    async fn collect_host_sample(&mut self, deadline: Option<Instant>) {
        self.host_sample_fresh = false;
        let Some(task) = self.active_host_task.as_mut() else {
            return;
        };
        if let Some(result) = join_by(task, deadline).await {
            self.active_host_task = None;
            if let Ok(stats) = result {
                self.stats = stats;
                self.host_sample_fresh = true;
            }
        }
    }

    /// Serial -> PID of the host emulator process serving each serial adb lists.
    fn qemu_pids(&self) -> HashMap<String, u32> {
        let mut qemu_pids = HashMap::new();
        for emu in &self.stats.emulators {
            for serial in self.devices.keys() {
                if serial_matches_emu_port(serial, emu.port) {
                    qemu_pids.insert(serial.clone(), emu.pid);
                    break;
                }
            }
        }
        qemu_pids
    }

    /// Queries `adb devices` (unless a previous query is still in flight) and waits for the
    /// result until `deadline` (`None`: no limit).
    async fn poll_devices(&mut self, deadline: Option<Instant>) {
        if self.active_devices_task.is_none() {
            let guest_clone = self.guest_prov.clone();
            self.active_devices_task =
                Some(tokio::spawn(async move { guest_clone.get_devices().await }));
        }
        let Some(task) = self.active_devices_task.as_mut() else {
            return;
        };
        if let Some(result) = join_by(task, deadline).await {
            self.active_devices_task = None;
            if let Ok(devices) = result {
                self.devices = devices;
            }
        }
    }

    #[cfg(test)]
    fn cached_model(&self, serial: &str) -> Option<&str> {
        self.device_states
            .get(serial)
            .filter(|s| !s.evicted)
            .and_then(|s| s.cached_model.as_deref())
    }

    /// Updates the per-serial caches: eviction of long-gone serials and the "online" flash.
    fn update_device_caches(&mut self) {
        let now = Instant::now();
        let mut evicted = Vec::new();
        for (serial, state) in &mut self.device_states {
            if state.evicted {
                continue;
            }
            if !self.devices.contains_key(serial) {
                state.online_indicator_until = None;
                state.last_status = Some("offline".to_string());
                let curr_host_procs = self
                    .stats
                    .emulators
                    .iter()
                    .find(|e| serial_matches_emu_port(serial, e.port))
                    .map(|e| build_group_procs(e.pid, self.stats.emu_groups.get(serial)));
                let replaced_by_new_host = curr_host_procs.as_ref().is_some_and(|procs| {
                    state.had_prior_emulator() && !state.matches_emu_session(procs)
                });
                if curr_host_procs.is_none() && self.host_sample_fresh {
                    state.clear_host_process();
                }
                let disc_since = *state.disconnect_since.get_or_insert(now);
                if replaced_by_new_host
                    || state.host_process_exited
                    || state.seen_offline_without_host
                    || now.saturating_duration_since(disc_since).as_secs()
                        > DISCONNECT_EVICT_TIMEOUT_SECS
                {
                    let replacement_procs =
                        curr_host_procs.filter(|procs| !state.matches_emu_session(procs));
                    state.evicted = true;
                    state.disconnect_since = None;
                    state.clear_host_process();
                    evicted.push((serial.clone(), replacement_procs));
                }
            } else {
                state.disconnect_since = None;
            }
        }
        // A new emulator can reuse an evicted serial; mark it evicted so its
        // tracking starts fresh on reconnect while keeping its [DISCONNECTED]
        // card visible until DISCONNECTED_DISPLAY_WINDOW_SECS expires.
        for (serial, replacement_procs) in &evicted {
            remove_stale_bare_key_owner(
                &mut self.bare_key_serials,
                serial,
                replacement_procs.as_ref(),
            );
            self.prev_stats.remove(serial);
            self.prev_stats.remove(&format!("{serial}_real"));
        }

        for (serial, status) in &self.devices {
            if self.device_states.get(serial).is_some_and(|s| s.evicted) {
                self.device_states.remove(serial);
            }
            let state = self.device_states.entry(serial.clone()).or_default();
            state.disconnect_since = None;
            let old_status = state.last_status.as_deref().unwrap_or("offline");
            if status != "device" {
                state.online_indicator_until = None;
            } else if old_status != "device" {
                state.online_indicator_until = Some(now + Duration::from_secs(ONLINE_FLASH_SECS));
            }
            state.last_status = Some(status.clone());
        }
    }

    fn spawn_guest_task_for(&mut self, serial: &str, status: &str, is_booting: bool) {
        let known_model = self
            .device_states
            .get(serial)
            .filter(|s| !s.evicted)
            .and_then(|s| s.cached_model.clone())
            .unwrap_or_default();
        let serial_clone = serial.to_string();
        let status_clone = status.to_string();
        let guest_prov_ref = self.guest_prov.clone();
        self.active_guest_tasks.insert(
            serial.to_string(),
            tokio::spawn(async move {
                guest_prov_ref
                    .get_guest_stats(&serial_clone, &known_model, is_booting, &status_clone)
                    .await
            }),
        );
    }

    /// Resets per-serial device state, bare-key ownership, UI trend baselines, and any
    /// in-flight guest query when a replacement emulator session takes over `serial`.
    fn reset_replaced_session(
        &mut self,
        serial: &str,
        status: &str,
        curr_procs: Option<&BTreeMap<u32, u64>>,
    ) {
        let state = self.device_states.entry(serial.to_string()).or_default();
        state.reset_for_new_pid();
        state.last_status = Some(status.to_string());
        if status == "device" {
            state.online_indicator_until =
                Some(Instant::now() + Duration::from_secs(ONLINE_FLASH_SECS));
        }
        remove_stale_bare_key_owner(&mut self.bare_key_serials, serial, curr_procs);
        self.prev_stats.remove(serial);
        self.prev_stats.remove(&format!("{serial}_real"));
        if let Some(task) = self.active_guest_tasks.remove(serial) {
            task.abort();
        }
    }

    /// Starts a guest query for every listed serial that has none in flight, cancelling queries
    /// for serials adb no longer lists so host and guest sampling run concurrently.
    fn spawn_guest_tasks(&mut self, deadline: Option<Instant>) {
        let devices = &self.devices;
        self.active_guest_tasks.retain(|serial, task| {
            let listed = devices.contains_key(serial);
            if !listed {
                task.abort();
            }
            listed
        });
        let listed: Vec<(String, String)> =
            self.devices.iter().map(|(s, st)| (s.clone(), st.clone())).collect();
        for (serial, status) in &listed {
            let reset_needed = status == "device"
                && self.device_states.get(serial).is_some_and(|s| {
                    !s.evicted && (s.host_process_exited || s.seen_offline_without_host)
                });
            if reset_needed {
                let curr_host_procs = self
                    .stats
                    .emulators
                    .iter()
                    .find(|e| serial_matches_emu_port(serial, e.port))
                    .map(|e| build_group_procs(e.pid, self.stats.emu_groups.get(serial)));
                self.reset_replaced_session(serial, status, curr_host_procs.as_ref());
            }
        }
        if deadline.is_some_and(|d| Instant::now() >= d) {
            return;
        }
        for (serial, status) in listed {
            if self.active_guest_tasks.contains_key(&serial) {
                continue;
            }
            let current_is_booting = self
                .device_states
                .get(&serial)
                .filter(|s| !s.evicted)
                .and_then(|s| s.is_booting)
                .unwrap_or(true);
            self.spawn_guest_task_for(&serial, &status, current_is_booting);
        }
    }

    /// Reconciles connected serials against the latest host emulator PIDs. When a new emulator
    /// process replaces an earlier one on the same serial, its speculative/in-flight guest query
    /// and cached state are dropped and re-spawned if time remains before `deadline`.
    fn reconcile_guest_tasks(&mut self, deadline: Option<Instant>) {
        let host_scan_pending = !self.host_sample_fresh && self.active_host_task.is_some();
        let qemu_pids = self.qemu_pids();
        let listed: Vec<(String, String)> =
            self.devices.iter().map(|(s, st)| (s.clone(), st.clone())).collect();
        for (serial, status) in listed {
            let curr_procs = qemu_pids
                .get(&serial)
                .map(|&pid| (pid, build_group_procs(pid, self.stats.emu_groups.get(&serial))));
            let Some((pid, procs)) = curr_procs else {
                let state = self.device_states.entry(serial).or_default();
                if !host_scan_pending {
                    if !state.had_prior_emulator() && status != "device" {
                        state.seen_offline_without_host = true;
                    }
                    state.clear_host_process();
                }
                continue;
            };
            let replaced = {
                let state = self.device_states.entry(serial.clone()).or_default();
                !state.matches_emu_session(&procs) && state.had_prior_emulator()
            };
            if replaced {
                self.reset_replaced_session(&serial, &status, Some(&procs));
            }
            self.device_states.entry(serial.clone()).or_default().record_emu_group(pid, &procs);
            if replaced && deadline.is_none_or(|d| Instant::now() < d) {
                self.spawn_guest_task_for(&serial, &status, true);
            }
        }
    }

    /// Waits for the running guest queries until `deadline` (`None`: no limit) and keeps the
    /// results of those that finished; the others are collected by a later tick.
    async fn collect_guest_samples(&mut self, deadline: Option<Instant>) {
        for state in self.device_states.values_mut() {
            state.guest_res_fresh = false;
        }
        let serials: Vec<String> = self.active_guest_tasks.keys().cloned().collect();
        for serial in serials {
            let Some(task) = self.active_guest_tasks.get_mut(&serial) else {
                continue;
            };
            let Some(result) = join_by(task, deadline).await else {
                continue;
            };
            self.active_guest_tasks.remove(&serial);
            if let Ok(stats) = result {
                let state = self.device_states.entry(serial).or_default();
                state.last_guest_res = Some(stats);
                state.guest_res_fresh = true;
            }
        }
    }

    fn avd_name_for_serial(&self, serial: &str) -> String {
        for emu in &self.stats.emulators {
            if serial_matches_emu_port(serial, emu.port) {
                if let Some(ref avd) = emu.avd_name {
                    return avd.clone();
                }
                break;
            }
        }
        serial.to_string()
    }

    fn collect_disconnected_guest_cards(
        &mut self,
        guest_results: &mut HashMap<String, GuestUiStats>,
    ) {
        let check_time = Instant::now();
        let mut expired_evicted = Vec::new();
        for (serial, state) in &self.device_states {
            if self.devices.contains_key(serial) {
                continue;
            }
            let Some(name) = state
                .cached_model
                .as_ref()
                .or_else(|| state.last_guest_res.as_ref().map(|r| &r.model))
                .filter(|m| !m.is_empty() && !m.contains("Unknown"))
            else {
                if state.evicted {
                    expired_evicted.push(serial.clone());
                }
                continue;
            };
            let should_show = state.last_seen_time.is_some_and(|t| {
                check_time.saturating_duration_since(t).as_secs() < DISCONNECTED_DISPLAY_WINDOW_SECS
            });

            if should_show {
                let avd_name = state.effective_avd_name(serial, None);
                guest_results
                    .insert(serial.clone(), GuestUiStats::disconnected(name.clone(), avd_name));
            } else if state.evicted {
                expired_evicted.push(serial.clone());
            }
        }
        for serial in expired_evicted {
            self.device_states.remove(&serial);
        }
    }

    fn process_guest_results(
        &mut self,
        ts_entry: &mut HashMap<String, f32>,
    ) -> HashMap<String, GuestUiStats> {
        let mut guest_results = HashMap::new();
        let connected_serials: Vec<String> = self.devices.keys().cloned().collect();

        for serial in &connected_serials {
            let avd_name_str = self.avd_name_for_serial(serial);
            let host_avd = (avd_name_str != *serial).then_some(avd_name_str.as_str());
            let status = self.devices.get(serial).map(String::as_str).unwrap_or("offline");

            let Some(state) = self.device_states.get_mut(serial) else {
                continue;
            };
            if let Some(avd) = host_avd {
                state.last_avd_name = Some(avd.to_string());
            }
            let effective_avd = state.effective_avd_name(serial, host_avd);

            let Some(mut result) = state.last_guest_res.clone() else {
                state.last_seen_time = Some(Instant::now());
                let known_model = state.cached_model.clone().unwrap_or_default();
                let avd_for_key = effective_avd.as_deref().unwrap_or(serial);
                let storage_key = resolve_and_migrate_storage_key(
                    &mut self.summary,
                    state,
                    serial,
                    avd_for_key,
                    &known_model,
                    &mut self.bare_key_serials,
                );
                record_missing_guest_metrics(ts_entry, &storage_key);
                let should_track = state.should_track_boot(
                    state.is_booting.unwrap_or(false),
                    status,
                    Instant::now(),
                );
                guest_results.insert(
                    serial.clone(),
                    GuestUiStats::calculating(known_model, effective_avd, should_track),
                );
                continue;
            };

            // A result from an earlier tick was already recorded: it is only shown again.
            let fresh = state.guest_res_fresh;
            let model = result.model.clone();
            let was_booting =
                state.current_storage_key.is_some() && state.is_booting.unwrap_or(false);
            if fresh {
                let boot_observed = status == "device" && result.boot_state_known;
                let is_booting = if state.boot_completed && !boot_observed {
                    false
                } else if boot_observed {
                    result.is_booting
                } else {
                    state.is_booting.unwrap_or(false)
                };
                result.is_booting = is_booting;
                if boot_observed || state.boot_completed {
                    state.is_booting = Some(is_booting);
                }
                state.last_guest_res = Some(result.clone());
                if !model.is_empty() && !model.contains("Unknown") {
                    state.cached_model = Some(model.clone());
                }
            }
            state.last_seen_time = Some(Instant::now());

            let avd_for_key = effective_avd.as_deref().unwrap_or(serial);
            let storage_key = resolve_and_migrate_storage_key(
                &mut self.summary,
                state,
                serial,
                avd_for_key,
                &model,
                &mut self.bare_key_serials,
            );
            let should_track = if fresh {
                record_guest_ts_metrics(ts_entry, &storage_key, &result);
                record_boot_log_if_due(
                    &mut self.summary,
                    state,
                    &storage_key,
                    &result,
                    was_booting,
                    status,
                )
            } else {
                record_missing_guest_metrics(ts_entry, &storage_key);
                state.should_track_boot(state.is_booting.unwrap_or(false), status, Instant::now())
            };
            let mut ui_stats =
                GuestUiStats::from_guest_stats(&result, should_track).with_avd_name(effective_avd);
            if !fresh {
                ui_stats.mark_timed_out();
            }
            guest_results.insert(serial.clone(), ui_stats);
        }

        self.collect_disconnected_guest_cards(&mut guest_results);
        guest_results
    }

    fn collect_summary_emulators(
        &mut self,
        elapsed_total: f64,
        ts_entry: &mut HashMap<String, f32>,
    ) -> Vec<(String, String, u32, f64)> {
        let mut summary_emulators = Vec::new();
        for emu in &self.stats.emulators {
            let Some(port) = emu.port else {
                continue;
            };
            let serial = format!("emulator-{port}");
            let curr_procs = build_group_procs(emu.pid, self.stats.emu_groups.get(&serial));
            let active_state = if let Some(s) = self
                .device_states
                .get_mut(&serial)
                .filter(|s| !s.evicted && s.matches_emu_session(&curr_procs))
            {
                if let Some(ref avd) = emu.avd_name {
                    s.last_avd_name = Some(avd.clone());
                }
                Some(&*s)
            } else {
                None
            };
            let storage_key = if let Some(ref avd) = emu.avd_name {
                format!("{avd}_{serial}")
            } else {
                active_state
                    .and_then(|s| s.current_storage_key.clone())
                    .unwrap_or_else(|| serial.clone())
            };
            note_or_migrate_bare_key(
                &mut self.summary,
                &mut self.bare_key_serials,
                &serial,
                &storage_key,
                &curr_procs,
                false,
            );
            let fallback_avd = (storage_key != serial)
                .then(|| storage_key.strip_suffix(&format!("_{serial}")))
                .flatten();
            let resolved_avd = emu.avd_name.as_deref().or(fallback_avd);
            match (self.summary.device_names.get(&storage_key).cloned(), resolved_avd) {
                (None, Some(avd)) => {
                    self.summary.device_names.insert(storage_key.clone(), avd.to_string());
                }
                (None, None) => {
                    self.summary.device_names.insert(storage_key.clone(), serial.clone());
                }
                (Some(existing), Some(avd)) if existing == serial => {
                    self.summary.device_names.insert(storage_key.clone(), avd.to_string());
                }
                (Some(existing), Some(avd)) => {
                    if let Some(model_part) = existing.strip_prefix(&format!("{serial} | ")) {
                        self.summary
                            .device_names
                            .insert(storage_key.clone(), format!("{avd} | {model_part}"));
                    }
                }
                (Some(_), None) => {}
            }
            record_qemu_metrics(ts_entry, &storage_key, emu, self.host_sample_fresh);
            let fs_mono = active_state
                .and_then(|s| s.first_seen_time)
                .map(|t| t.saturating_duration_since(self.loop_start_time).as_secs_f64())
                .unwrap_or(elapsed_total);
            summary_emulators.push((storage_key, serial, emu.pid, fs_mono));
        }
        summary_emulators
    }

    async fn record_netsim(
        &mut self,
        elapsed_total: f64,
        ts_entry: &mut HashMap<String, f32>,
    ) -> (netsim::NetsimDashboardSnapshot, bool) {
        let summary_emulators = self.collect_summary_emulators(elapsed_total, ts_entry);
        let now_wall = netsim::now_wall_secs();
        let now_mono = Instant::now().saturating_duration_since(self.loop_start_time).as_secs_f64();
        let ns_tracker_clone = self.netsim_tracker.clone();
        let ns_daemons = self.stats.netsim_daemons.clone();
        let ns_emu_groups = self.stats.emu_groups.clone();
        let ns_devices = self.devices.clone();
        let (ns_str_fields, ns_summary_lines, ns_snap, has_connecting) =
            tokio::task::spawn_blocking(move || {
                let mut tracker = ns_tracker_clone.lock().unwrap_or_else(|e| e.into_inner());
                tracker.update(
                    &ns_daemons,
                    &ns_emu_groups,
                    &summary_emulators,
                    &ns_devices,
                    now_wall,
                    now_mono,
                );
                (
                    tracker.get_csv_fields(),
                    tracker.format_summary_sections(now_mono),
                    tracker.get_dashboard_snapshot(now_wall),
                    tracker.has_any_connecting(),
                )
            })
            .await
            .unwrap_or_else(|_| (BTreeMap::new(), Vec::new(), Default::default(), false));
        push_capped(&mut self.summary.time_series_str, ns_str_fields, MAX_TIME_SERIES_LEN);
        self.summary.netsim_summary_lines = ns_summary_lines;
        (ns_snap, has_connecting)
    }

    fn maybe_auto_save(&mut self) {
        if self.args.auto_save_interval > 0
            && self.last_auto_save.elapsed().as_secs() >= (self.args.auto_save_interval as u64 * 60)
        {
            crate::summary::save_summary(
                &self.summary,
                &self.args.out_dir.to_string_lossy(),
                self.args.csv,
                !self.args.no_summary,
                &self.hardware_specs,
                self.cpu_cores,
                true,
            );
            self.last_auto_save = Instant::now();
        }
    }

    fn record_tick(&mut self, mut ts_entry: HashMap<String, f32>) {
        self.summary.iterations += 1;
        if self.host_sample_fresh {
            self.summary.host_cpu_max = self.summary.host_cpu_max.max(self.stats.cpu_usage);
            self.summary.host_ram_max = self.summary.host_ram_max.max(self.stats.ram_percent);
        }

        insert_host_ts_metrics(&mut ts_entry, &self.stats, self.host_sample_fresh);
        push_capped(&mut self.summary.time_series, ts_entry, MAX_TIME_SERIES_LEN);
        self.maybe_auto_save();
    }

    fn render(
        &mut self,
        guest_results: &HashMap<String, GuestUiStats>,
        ns_snap: &netsim::NetsimDashboardSnapshot,
    ) {
        if !self.args.headless && (!self.args.once || self.summary.iterations > 1) {
            let term_width =
                crossterm::terminal::size().map(|(w, _)| w).unwrap_or(DEFAULT_TERM_WIDTH);
            let force_clear = self.summary.iterations == 1 || term_width != self.prev_term_width;
            self.prev_term_width = term_width;
            let online_indicator_until: HashMap<String, Instant> = self
                .device_states
                .iter()
                .filter_map(|(k, v)| v.online_indicator_until.map(|t| (k.clone(), t)))
                .collect();
            ui::print_dashboard(ui::DashboardInput {
                host_stats: &self.stats,
                guest_results,
                prev_stats: &mut self.prev_stats,
                iter_count: self.summary.iterations,
                term_width,
                is_once: self.args.once,
                hardware_specs: &self.hardware_specs,
                force_clear,
                online_indicator_until: &online_indicator_until,
                netsim_snapshot: Some(ns_snap),
                host_timed_out: !self.host_sample_fresh,
            });
        }
    }

    /// Runs one sampling tick: refreshes the host, device and guest data, records it and
    /// redraws the dashboard. Returns whether a netsim connection is in progress.
    async fn tick(&mut self, tick_start: Instant) -> bool {
        let elapsed_total =
            tick_start.saturating_duration_since(self.loop_start_time).as_secs_f64();
        // Samples are waited for until the next tick is due, as in the Python monitor. The
        // first tick and `--once` have nothing earlier to show, so they wait for everything.
        let deadline = (self.summary.iterations > 0 && !self.args.once)
            .then(|| tick_start + Duration::from_secs_f64(self.interval));

        self.spawn_host_task();
        self.poll_devices(deadline).await;
        self.spawn_guest_tasks(deadline);
        self.collect_host_sample(deadline).await;
        self.update_device_caches();
        self.reconcile_guest_tasks(deadline);
        self.collect_guest_samples(deadline).await;

        let mut ts_entry = HashMap::new();
        ts_entry.insert("time".to_string(), elapsed_total as f32);

        let guest_results = self.process_guest_results(&mut ts_entry);
        let (ns_snap, has_connecting) = self.record_netsim(elapsed_total, &mut ts_entry).await;
        self.record_tick(ts_entry);
        self.render(&guest_results, &ns_snap);
        has_connecting
    }

    async fn sleep(&self, tick_start: Instant, has_connecting: bool) {
        let sleep_time =
            calculate_sleep_duration(self.interval, tick_start.elapsed().as_secs_f64());
        if has_connecting && sleep_time > FAST_POLL_STEP_SECS {
            let deadline = Instant::now() + Duration::from_secs_f64(sleep_time);
            let shared_emu_groups = Arc::new(self.stats.emu_groups.clone());
            while Instant::now() < deadline {
                let step = deadline
                    .saturating_duration_since(Instant::now())
                    .as_secs_f64()
                    .clamp(MIN_FAST_POLL_STEP_SECS, FAST_POLL_STEP_SECS);
                tokio::time::sleep(Duration::from_secs_f64(step)).await;
                let ns_tracker_clone = self.netsim_tracker.clone();
                let ns_emu_groups = Arc::clone(&shared_emu_groups);
                let still_connecting = tokio::task::spawn_blocking(move || {
                    let mut tracker = ns_tracker_clone.lock().unwrap_or_else(|e| e.into_inner());
                    if tracker.has_any_connecting() {
                        tracker.poll_sockets_fast(&ns_emu_groups, netsim::now_wall_secs());
                        tracker.has_any_connecting()
                    } else {
                        false
                    }
                })
                .await
                .unwrap_or(false);
                if !still_connecting {
                    let rem = deadline.saturating_duration_since(Instant::now());
                    if !rem.is_zero() {
                        tokio::time::sleep(rem).await;
                    }
                    break;
                }
            }
        } else {
            tokio::time::sleep(Duration::from_secs_f64(sleep_time)).await;
        }
    }

    async fn finish(
        mut self,
        loop_outcome: Result<(), Box<dyn std::any::Any + Send>>,
    ) -> summary::SessionSummary {
        if let Some(task) = self.active_devices_task.take() {
            task.abort();
        }
        for (_, task) in self.active_guest_tasks.drain() {
            task.abort();
        }

        if should_use_alternate_screen(&self.args) {
            let _ = crossterm::execute!(
                std::io::stdout(),
                crossterm::terminal::LeaveAlternateScreen,
                crossterm::cursor::Show
            );
        }

        let end_mono = Instant::now().saturating_duration_since(self.loop_start_time).as_secs_f64();
        let ns_tracker_clone = self.netsim_tracker.clone();
        if let Ok(lines) = tokio::task::spawn_blocking(move || {
            let mut tracker = ns_tracker_clone.lock().unwrap_or_else(|e| e.into_inner());
            tracker.cleanup();
            tracker.format_summary_sections(end_mono)
        })
        .await
        {
            self.summary.netsim_summary_lines = lines;
        }

        summary::save_summary(
            &self.summary,
            &self.args.out_dir.to_string_lossy(),
            self.args.csv,
            !self.args.no_summary,
            &self.hardware_specs,
            self.cpu_cores,
            false,
        );
        if let Err(panic) = loop_outcome {
            std::panic::resume_unwind(panic);
        }
        self.summary
    }
}

async fn wait_for_shutdown_signal() {
    #[cfg(unix)]
    {
        if let Ok(mut sigterm) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            tokio::select! {
                _ = tokio::signal::ctrl_c() => {}
                _ = sigterm.recv() => {}
            }
        } else {
            let _ = tokio::signal::ctrl_c().await;
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}

pub async fn run<H: HostProvider + 'static, G: GuestProvider + 'static>(
    host_prov: H,
    guest_prov: G,
    args: Args,
    hardware_specs: String,
) -> summary::SessionSummary {
    if should_use_alternate_screen(&args) {
        let _ = crossterm::execute!(
            std::io::stdout(),
            crossterm::terminal::EnterAlternateScreen,
            crossterm::cursor::Hide
        );
    }

    let mut monitor = Monitor::new(host_prov, guest_prov, args, hardware_specs);

    // A panic in the loop is caught so the summary and netsim cleanup below
    // still run; it is re-raised at the end.
    let loop_outcome = tokio::select! {
        outcome = std::panic::AssertUnwindSafe(async {
            loop {
                let tick_start = Instant::now();
                let has_connecting = monitor.tick(tick_start).await;
                if monitor.args.once && monitor.summary.iterations >= 2 {
                    break;
                }

                monitor.sleep(tick_start, has_connecting).await;
            }
        })
        .catch_unwind() => outcome,
        _ = wait_for_shutdown_signal() => {
            if !monitor.args.headless && monitor.args.once {
                println!("\nCtrl+C received, exiting...");
            }
            Ok(())
        }
    };

    monitor.finish(loop_outcome).await
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let args_clone = args.clone();
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        if should_use_alternate_screen(&args_clone) {
            let _ = crossterm::execute!(
                std::io::stdout(),
                crossterm::terminal::LeaveAlternateScreen,
                crossterm::cursor::Show
            );
        }
        default_hook(panic_info);
    }));

    let host_prov = host::RealHostProvider::default();

    let guest_prov = guest::RealGuestProvider;

    let hardware_specs = host::get_static_hardware_info();
    run(host_prov, guest_prov, args, hardware_specs).await;
}

#[cfg(all(test, unix))]
#[path = "../tests/test_support.rs"]
mod test_support;

#[cfg(test)]
#[path = "../tests/main_test.rs"]
mod tests;
