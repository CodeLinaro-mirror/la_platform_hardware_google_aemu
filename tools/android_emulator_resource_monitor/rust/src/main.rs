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
mod test_support {
    /// Runs `launch` until it stops failing with `ETXTBSY`.
    ///
    /// A child that another test thread forks while a freshly written executable is still
    /// open for writing keeps that handle until it calls `exec`; launching the file before
    /// then fails with `ETXTBSY`.
    pub(crate) fn retry_while_text_busy<T>(
        mut launch: impl FnMut() -> std::io::Result<T>,
    ) -> std::io::Result<T> {
        for _ in 0..1000 {
            match launch() {
                Err(e) if e.kind() == std::io::ErrorKind::ExecutableFileBusy => {
                    std::thread::sleep(std::time::Duration::from_millis(5));
                }
                res => return res,
            }
        }
        launch()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::host::{EmulatorProcess, HostStats};
    use std::collections::HashMap;
    #[test]
    fn test_calculate_sleep_duration() {
        assert_eq!(calculate_sleep_duration(2.0, 0.5), 1.5);
        assert_eq!(calculate_sleep_duration(2.0, 2.5), 0.1);
        assert_eq!(calculate_sleep_duration(2.0, 5.0), 0.1);
    }

    struct MockHostProvider;
    impl provider::HostProvider for MockHostProvider {
        fn get_cpu_cores(&self) -> usize {
            4
        }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            HostStats {
                cpu_usage: 10.0,
                ram_used_gb: 1.0,
                ram_total_gb: 4.0,
                ram_percent: 25.0,
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
                paging_str: "N/A".to_string(),
                paging_in: 0.0,
                paging_out: 0.0,
                battery_str: "N/A".to_string(),
                battery_percent: None,
            }
        }
    }

    struct MockGuestProvider;
    impl provider::GuestProvider for MockGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> GuestStats {
            GuestStats {
                model: "Mock Model".to_string(),
                is_booting: false,
                boot_state_known: true,
                cpu_total: "50%".to_string(),
                cpu_adjusted: "12.5% (over 4 cores)".to_string(),
                ram: "50%".to_string(),
                data_space: Some("20%".to_string()),
                swap: Some("0.0 MB".to_string()),
                err_count: 0,
                data_pct: Some(20.0),
            }
        }
    }

    #[tokio::test]
    async fn test_main_run_mocked() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };

        // If this finishes without hanging, dependency injection is working properly!
        let summary =
            run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
        assert_eq!(summary.iterations, 2);
        assert_eq!(summary.time_series[0].get("host_cpu"), Some(&10.0));
        assert_eq!(summary.time_series[0].get("emu_cpu_tot_emulator-5554"), Some(&50.0));
    }

    #[tokio::test]
    async fn test_main_headless_flag() {
        let dir = tempfile::tempdir().unwrap();
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: dir.path().to_path_buf(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        let summary =
            run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
        assert_eq!(summary.iterations, 2);
        assert_eq!(summary.time_series.len(), 2);
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
    }

    #[tokio::test]
    async fn test_main_no_summary_flag() {
        let dir = tempfile::tempdir().unwrap();
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: dir.path().to_path_buf(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        let summary =
            run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
        assert_eq!(summary.iterations, 2);
        assert!(
            std::fs::read_dir(dir.path()).unwrap().filter_map(Result::ok).all(|e| e
                .path()
                .extension()
                .and_then(|s| s.to_str())
                != Some("txt"))
        );
    }

    struct MockBootingGuestProvider;
    impl provider::GuestProvider for MockBootingGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> GuestStats {
            GuestStats {
                model: "Mock Model".to_string(),
                is_booting: true,
                boot_state_known: true,
                cpu_total: "50%".to_string(),
                cpu_adjusted: "12.5% (over 4 cores)".to_string(),
                ram: "50%".to_string(),
                data_space: Some("20%".to_string()),
                swap: Some("0.0 MB".to_string()),
                err_count: 0,
                data_pct: Some(20.0),
            }
        }
    }

    #[tokio::test]
    async fn test_main_loop_booting_device() {
        let dir = tempfile::tempdir().unwrap();
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: dir.path().to_path_buf(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        let summary =
            run(MockHostProvider, MockBootingGuestProvider, args, "Mock Specs".to_string()).await;
        assert_eq!(summary.iterations, 2);
        assert!(!summary.boot_logs["emulator-5554"].is_empty());
    }

    struct MockOfflineGuestProvider;
    impl provider::GuestProvider for MockOfflineGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "offline".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> GuestStats {
            GuestStats {
                model: "Mock Model".to_string(),
                is_booting: true,
                boot_state_known: false,
                cpu_total: "N/A".to_string(),
                cpu_adjusted: "N/A".to_string(),
                ram: "N/A".to_string(),
                data_space: None,
                swap: None,
                err_count: 0,
                data_pct: None,
            }
        }
    }

    #[tokio::test]
    async fn test_main_loop_offline_device() {
        let dir = tempfile::tempdir().unwrap();
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: dir.path().to_path_buf(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        let summary =
            run(MockHostProvider, MockOfflineGuestProvider, args, "Mock Specs".to_string()).await;
        assert_eq!(summary.iterations, 2);
        assert!(summary.time_series[0]["emu_cpu_tot_emulator-5554"].is_nan());
    }

    #[tokio::test]
    async fn test_main_csv_flag() {
        let dir = tempfile::tempdir().unwrap();
        let args = Args {
            once: true,
            no_summary: true,
            csv: true,
            out_dir: dir.path().to_path_buf(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
        let files: Vec<PathBuf> = std::fs::read_dir(dir.path())
            .unwrap()
            .filter_map(Result::ok)
            .map(|e| e.path())
            .collect();
        let csv_files: Vec<&PathBuf> = files
            .iter()
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("csv"))
            .collect();
        assert_eq!(csv_files.len(), 1, "expected exactly 1 CSV file, got {files:?}");
        assert!(
            files.iter().all(|p| p.extension().and_then(|s| s.to_str()) != Some("txt")),
            "unexpected .txt summary file with --no-summary: {files:?}"
        );
        let csv_text = std::fs::read_to_string(csv_files[0]).unwrap();
        assert!(csv_text.contains("host_cpu"));
        assert!(csv_text.contains("emu_cpu_tot_emulator-5554"));
    }

    #[tokio::test]
    async fn test_main_out_dir_flag() {
        let dir = tempfile::tempdir().unwrap();
        let out_dir = dir.path().join("out_rust");
        std::fs::create_dir_all(&out_dir).unwrap();
        let args = Args {
            once: true,
            no_summary: false,
            csv: false,
            out_dir: out_dir.clone(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
        let txt_files: Vec<PathBuf> = std::fs::read_dir(&out_dir)
            .unwrap()
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("txt"))
            .collect();
        assert_eq!(txt_files.len(), 1, "expected 1 summary .txt in out_dir, got {txt_files:?}");
        let txt = std::fs::read_to_string(&txt_files[0]).unwrap();
        assert!(txt.contains("Mock Model"));
    }

    #[tokio::test]
    async fn test_main_loop_serial_migration() {
        let dir = tempfile::tempdir().unwrap();
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: dir.path().to_path_buf(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };

        struct DelayedAvdHostProvider {
            calls: usize,
        }
        impl provider::HostProvider for DelayedAvdHostProvider {
            fn get_cpu_cores(&self) -> usize {
                4
            }
            fn get_host_stats(&mut self, _: f64) -> HostStats {
                self.calls += 1;
                let emulators = if self.calls >= 2 {
                    vec![EmulatorProcess {
                        pid: 4242,
                        port: Some(5556),
                        avd_name: Some("Pixel_7".to_string()),
                        cpu_usage: 25.0,
                        ram_mb: 1024.0,
                        threads: 8,
                        context_switches: 10,
                        gpu_load: None,
                    }]
                } else {
                    vec![]
                };
                HostStats { emulators, ..MockHostProvider.get_host_stats(0.0) }
            }
        }

        struct MigratingGuestProvider;
        impl provider::GuestProvider for MigratingGuestProvider {
            async fn get_devices(&self) -> std::collections::HashMap<String, String> {
                HashMap::from([("emulator-5556".to_string(), "device".to_string())])
            }
            async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> GuestStats {
                GuestStats {
                    model: "Mock Model".to_string(),
                    is_booting: false,
                    boot_state_known: true,
                    cpu_total: "50%".to_string(),
                    cpu_adjusted: "12.5% (over 4 cores)".to_string(),
                    ram: "50%".to_string(),
                    data_space: Some("20%".to_string()),
                    swap: Some("0.0 MB".to_string()),
                    err_count: 0,
                    data_pct: Some(20.0),
                }
            }
        }

        let summary = run(
            DelayedAvdHostProvider { calls: 0 },
            MigratingGuestProvider,
            args,
            "Mock Specs".to_string(),
        )
        .await;
        assert_eq!(summary.iterations, 2);
        assert_eq!(summary.time_series[0].get("emu_cpu_tot_Pixel_7_emulator-5556"), Some(&50.0));
        assert_eq!(summary.time_series[1].get("emu_cpu_tot_Pixel_7_emulator-5556"), Some(&50.0));
        assert!(!summary.time_series[0].contains_key("emu_cpu_tot_emulator-5556"));
    }

    struct MockPhysicalDeviceGuestProvider;
    impl provider::GuestProvider for MockPhysicalDeviceGuestProvider {
        async fn get_devices(&self) -> std::collections::HashMap<String, String> {
            let mut map = std::collections::HashMap::new();
            map.insert("HT4A12345678".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> GuestStats {
            GuestStats {
                model: "Pixel 6".to_string(),
                is_booting: false,
                boot_state_known: true,
                cpu_total: "10%".to_string(),
                cpu_adjusted: "2.5% (over 4 cores)".to_string(),
                ram: "20%".to_string(),
                data_space: Some("15%".to_string()),
                swap: Some("0.0 MB".to_string()),
                err_count: 0,
                data_pct: Some(15.0),
            }
        }
    }

    #[tokio::test]
    async fn test_main_loop_physical_device() {
        let dir = tempfile::tempdir().unwrap();
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: dir.path().to_path_buf(),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        let summary =
            run(MockHostProvider, MockPhysicalDeviceGuestProvider, args, "Mock Specs".to_string())
                .await;
        assert_eq!(summary.iterations, 2);
        assert_eq!(summary.time_series[0].get("emu_cpu_tot_HT4A12345678"), Some(&10.0));
    }

    fn seeded_cache_monitor() -> Monitor<ScriptedHostProvider, ScriptedGuestProvider> {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Model A", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.stats.emulators = vec![scripted_emulator(100, 5554)];
        monitor.devices.insert(serial.to_string(), "device".to_string());
        let mut state = DeviceState {
            cached_model: Some("Model A".to_string()),
            last_status: Some("device".to_string()),
            ..Default::default()
        };
        state.record_emu_group(100, &BTreeMap::from([(100, 1000)]));
        monitor.device_states.insert(serial.to_string(), state);
        monitor
    }

    #[tokio::test]
    async fn test_update_device_cache_normal_operation() {
        let mut monitor = seeded_cache_monitor();
        monitor.update_device_caches();
        monitor.reconcile_guest_tasks(None);

        assert_eq!(monitor.cached_model("emulator-5554"), Some("Model A"));
        assert_eq!(monitor.device_states["emulator-5554"].last_known_pid, Some(100));
        assert!(monitor.device_states["emulator-5554"].disconnect_since.is_none());
        assert!(!monitor.device_states["emulator-5554"].evicted);
    }

    #[tokio::test]
    async fn test_update_device_cache_pid_changed() {
        let mut monitor = seeded_cache_monitor();
        monitor.stats.emulators = vec![scripted_emulator(101, 5554)];

        monitor.update_device_caches();
        monitor.reconcile_guest_tasks(None);
        for (_, task) in monitor.active_guest_tasks.drain() {
            task.abort();
        }

        assert_eq!(monitor.cached_model("emulator-5554"), None);
        assert_eq!(monitor.device_states["emulator-5554"].last_known_pid, Some(101));
    }

    #[tokio::test]
    async fn test_update_device_cache_disconnect_starts_timer() {
        let mut monitor = seeded_cache_monitor();
        monitor.devices.clear();
        monitor.stats.emulators.clear();

        monitor.update_device_caches();

        assert_eq!(monitor.cached_model("emulator-5554"), Some("Model A"));
        assert!(monitor.device_states["emulator-5554"].disconnect_since.is_some());
        assert!(!monitor.device_states["emulator-5554"].evicted);
    }

    #[tokio::test]
    async fn test_update_device_cache_disconnect_timeout_expires() {
        let mut monitor = seeded_cache_monitor();
        monitor.devices.clear();
        monitor.stats.emulators.clear();
        monitor.device_states.get_mut("emulator-5554").unwrap().disconnect_since =
            Some(Instant::now() - Duration::from_secs(6));

        monitor.update_device_caches();

        let state = &monitor.device_states["emulator-5554"];
        assert!(state.evicted);
        assert_eq!(monitor.cached_model("emulator-5554"), None);
        assert!(state.disconnect_since.is_none());
        assert!(state.last_known_pid.is_none());
    }

    #[tokio::test]
    async fn test_update_device_cache_forgets_pid_without_host_process() {
        let mut monitor = seeded_cache_monitor();
        monitor.stats.emulators.clear();

        monitor.update_device_caches();
        monitor.reconcile_guest_tasks(None);

        let state = &monitor.device_states["emulator-5554"];
        assert!(!state.evicted);
        assert!(state.last_known_pid.is_none());
        assert!(state.host_process_exited);
        assert_eq!(monitor.cached_model("emulator-5554"), Some("Model A"));
    }

    #[tokio::test]
    async fn test_update_device_cache_disconnect_waiting() {
        let mut monitor = seeded_cache_monitor();
        monitor.devices.clear();
        monitor.stats.emulators.clear();
        let past = Instant::now() - Duration::from_secs(4);
        monitor.device_states.get_mut("emulator-5554").unwrap().disconnect_since = Some(past);

        monitor.update_device_caches();

        assert_eq!(monitor.cached_model("emulator-5554"), Some("Model A"));
        assert_eq!(monitor.device_states["emulator-5554"].disconnect_since, Some(past));
        assert!(!monitor.device_states["emulator-5554"].evicted);
    }

    #[tokio::test]
    async fn test_update_device_cache_reconnect_before_timeout() {
        let mut monitor = seeded_cache_monitor();
        let past = Instant::now() - Duration::from_secs(4);
        monitor.device_states.get_mut("emulator-5554").unwrap().disconnect_since = Some(past);

        monitor.update_device_caches();
        monitor.reconcile_guest_tasks(None);

        assert!(monitor.device_states["emulator-5554"].disconnect_since.is_none());
        assert_eq!(monitor.cached_model("emulator-5554"), Some("Model A"));
    }

    struct MockCollisionHostProvider;
    impl provider::HostProvider for MockCollisionHostProvider {
        fn get_cpu_cores(&self) -> usize {
            4
        }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            HostStats {
                cpu_usage: 10.0,
                ram_used_gb: 1.0,
                ram_total_gb: 4.0,
                ram_percent: 25.0,
                swap_used_gb: 0.0,
                swap_total_gb: 0.0,
                swap_percent: 0.0,
                disk_read_mbs: 0.0,
                disk_write_mbs: 0.0,
                emulators: vec![
                    EmulatorProcess {
                        pid: 1234,
                        port: Some(5554),
                        avd_name: Some("Pixel_6".to_string()),
                        cpu_usage: 10.0,
                        ram_mb: 100.0,
                        threads: 4,
                        context_switches: 100,
                        gpu_load: None,
                    },
                    EmulatorProcess {
                        pid: 5678,
                        port: Some(5556),
                        avd_name: Some("Pixel_6".to_string()),
                        cpu_usage: 20.0,
                        ram_mb: 200.0,
                        threads: 4,
                        context_switches: 100,
                        gpu_load: None,
                    },
                ],
                netsim_cpu: 0.0,
                netsim_ram_mb: 0.0,
                netsim_count: 0,
                netsim_daemons: vec![],
                emu_groups: HashMap::new(),
                gpu_percent: None,
                gpu_memory_mb: None,
                thermal_temp: None,
                paging_str: "N/A".to_string(),
                paging_in: 0.0,
                paging_out: 0.0,
                battery_str: "N/A".to_string(),
                battery_percent: None,
            }
        }
    }

    struct MockCollisionGuestProvider;
    impl provider::GuestProvider for MockCollisionGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map.insert("emulator-5556".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, serial: &str, _: &str, _: bool, _: &str) -> GuestStats {
            if serial == "emulator-5554" {
                GuestStats {
                    model: "Pixel_6".to_string(),
                    is_booting: false,
                    boot_state_known: true,
                    cpu_total: "50%".to_string(),
                    cpu_adjusted: "25%".to_string(),
                    ram: "50%".to_string(),
                    data_space: None,
                    swap: Some("0.0 MB".to_string()),
                    err_count: 0,
                    data_pct: None,
                }
            } else {
                GuestStats {
                    model: "Pixel_6".to_string(),
                    is_booting: false,
                    boot_state_known: true,
                    cpu_total: "60%".to_string(),
                    cpu_adjusted: "30%".to_string(),
                    ram: "60%".to_string(),
                    data_space: None,
                    swap: Some("0.0 MB".to_string()),
                    err_count: 0,
                    data_pct: None,
                }
            }
        }
    }

    #[tokio::test]
    async fn test_avd_key_collision() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };

        let summary = run(
            MockCollisionHostProvider,
            MockCollisionGuestProvider,
            args,
            "Mock Specs".to_string(),
        )
        .await;
        assert!(summary.device_names.contains_key("Pixel_6_emulator-5554"));
        assert!(summary.device_names.contains_key("Pixel_6_emulator-5556"));
        assert_eq!(summary.time_series[0].get("emu_cpu_tot_Pixel_6_emulator-5554"), Some(&50.0));
        assert_eq!(summary.time_series[0].get("emu_cpu_tot_Pixel_6_emulator-5556"), Some(&60.0));
    }

    struct MockDynamicHostProvider {
        iteration: std::sync::Mutex<usize>,
    }

    impl provider::HostProvider for MockDynamicHostProvider {
        fn get_cpu_cores(&self) -> usize {
            4
        }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            let mut iter = self.iteration.lock().unwrap();
            *iter += 1;

            let pid = if *iter == 1 { 1234 } else { 5678 };

            HostStats {
                cpu_usage: 10.0,
                ram_used_gb: 1.0,
                ram_total_gb: 4.0,
                ram_percent: 25.0,
                swap_used_gb: 0.0,
                swap_total_gb: 0.0,
                swap_percent: 0.0,
                disk_read_mbs: 0.0,
                disk_write_mbs: 0.0,
                emulators: vec![EmulatorProcess {
                    pid,
                    port: Some(5554),
                    avd_name: Some("Pixel_6".to_string()),
                    cpu_usage: 10.0,
                    ram_mb: 100.0,
                    threads: 4,
                    context_switches: 100,
                    gpu_load: None,
                }],
                netsim_cpu: 0.0,
                netsim_ram_mb: 0.0,
                netsim_count: 0,
                netsim_daemons: vec![],
                emu_groups: HashMap::new(),
                gpu_percent: None,
                gpu_memory_mb: None,
                thermal_temp: None,
                paging_str: "N/A".to_string(),
                paging_in: 0.0,
                paging_out: 0.0,
                battery_str: "N/A".to_string(),
                battery_percent: None,
            }
        }
    }

    struct MockDynamicGuestProvider {
        iteration: std::sync::Mutex<usize>,
    }

    impl provider::GuestProvider for MockDynamicGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> GuestStats {
            let mut iter = self.iteration.lock().unwrap();
            *iter += 1;

            if *iter == 1 {
                GuestStats {
                    model: "Pixel_6".to_string(),
                    is_booting: false,
                    boot_state_known: true,
                    cpu_total: "50%".to_string(),
                    cpu_adjusted: "25%".to_string(),
                    ram: "50%".to_string(),
                    data_space: None,
                    swap: Some("0.0 MB".to_string()),
                    err_count: 0,
                    data_pct: None,
                }
            } else {
                GuestStats {
                    model: "Pixel_6".to_string(),
                    is_booting: true,
                    boot_state_known: true,
                    cpu_total: "60%".to_string(),
                    cpu_adjusted: "30%".to_string(),
                    ram: "60%".to_string(),
                    data_space: None,
                    swap: Some("0.0 MB".to_string()),
                    err_count: 0,
                    data_pct: None,
                }
            }
        }
    }

    #[tokio::test]
    async fn test_boot_tracking_restart_detected() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };

        let host_prov = MockDynamicHostProvider { iteration: std::sync::Mutex::new(0) };
        let guest_prov = MockDynamicGuestProvider { iteration: std::sync::Mutex::new(0) };

        let summary = run(host_prov, guest_prov, args, "Mock Specs".to_string()).await;
        assert!(
            summary.boot_logs.get("Pixel_6_emulator-5554").map(|v| !v.is_empty()).unwrap_or(false)
        );
    }

    #[test]
    fn test_device_state_reset_and_boot_tracking_window() {
        let now = tokio::time::Instant::now();
        let mut state = DeviceState {
            boot_completed: true,
            boot_finished_time: Some(now - std::time::Duration::from_secs(100)),
            is_booting: Some(false),
            first_seen_time: Some(now),
            last_boot_log_time: Some(now),
            current_storage_key: Some("Pixel_6_emulator-5554".to_string()),
            last_guest_res: Some(scripted_guest_stats("Pixel 6", false)),
            ..Default::default()
        };

        assert!(state.should_track_boot(false, "device", now));
        state.boot_finished_time = Some(now - std::time::Duration::from_secs(301));
        assert!(!state.should_track_boot(false, "device", now));
        assert!(state.should_track_boot(true, "device", now));
        assert!(!state.should_track_boot(false, "offline", now));

        state.reset_for_new_pid();
        assert!(!state.boot_completed);
        assert!(state.should_track_boot(false, "offline", now));
        assert!(!state.should_track_boot(false, "device", now));
        assert!(state.boot_finished_time.is_none());
        assert_eq!(state.is_booting, None);
        assert!(state.first_seen_time.is_none());
        assert!(state.last_boot_log_time.is_none());
        assert!(state.current_storage_key.is_none());
        assert!(state.last_guest_res.is_none());
    }

    /// Host provider whose emulator list and sample duration a test changes between ticks;
    /// `cpu_usage` counts the samples taken.
    #[derive(Clone, Default)]
    struct ScriptedHostProvider {
        emulators: Arc<Mutex<Vec<EmulatorProcess>>>,
        emu_groups: Arc<Mutex<HashMap<String, netsim::EmuGroupNetsimInfo>>>,
        samples: Arc<std::sync::atomic::AtomicU32>,
        sample_time: Arc<Mutex<Duration>>,
    }

    impl provider::HostProvider for ScriptedHostProvider {
        fn get_cpu_cores(&self) -> usize {
            4
        }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            let sample = self.samples.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
            std::thread::sleep(*self.sample_time.lock().unwrap());
            HostStats {
                cpu_usage: sample as f32,
                emulators: self.emulators.lock().unwrap().clone(),
                emu_groups: self.emu_groups.lock().unwrap().clone(),
                ..initial_host_stats()
            }
        }
    }

    /// Guest provider whose `adb devices` list, per-serial results and query duration a test
    /// changes between ticks.
    #[derive(Clone, Default)]
    struct ScriptedGuestProvider {
        devices: Arc<Mutex<HashMap<String, String>>>,
        results: Arc<Mutex<HashMap<String, GuestStats>>>,
        query_time: Arc<Mutex<Duration>>,
    }

    impl provider::GuestProvider for ScriptedGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            self.devices.lock().unwrap().clone()
        }
        async fn get_guest_stats(&self, serial: &str, _: &str, _: bool, _: &str) -> GuestStats {
            let result = self.results.lock().unwrap()[serial].clone();
            let query_time = *self.query_time.lock().unwrap();
            tokio::time::sleep(query_time).await;
            result
        }
    }

    fn scripted_emulator(pid: u32, port: u16) -> EmulatorProcess {
        EmulatorProcess {
            pid,
            port: Some(port),
            avd_name: Some("Pixel_6".to_string()),
            cpu_usage: 100.0,
            ram_mb: 2048.0,
            threads: 40,
            context_switches: 0,
            gpu_load: None,
        }
    }

    fn scripted_guest_stats(model: &str, is_booting: bool) -> GuestStats {
        GuestStats {
            model: model.to_string(),
            is_booting,
            boot_state_known: true,
            cpu_total: "50%".to_string(),
            cpu_adjusted: "12.5%".to_string(),
            ram: "40%".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            data_pct: None,
        }
    }

    /// Arguments for a headless, continuous monitor that writes no files.
    fn scripted_args() -> Args {
        Args {
            once: false,
            no_summary: true,
            csv: false,
            out_dir: PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 0,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        }
    }

    fn scripted_monitor(
        host: &ScriptedHostProvider,
        guest: &ScriptedGuestProvider,
    ) -> Monitor<ScriptedHostProvider, ScriptedGuestProvider> {
        Monitor::new(host.clone(), guest.clone(), scripted_args(), "Specs".to_string())
    }

    /// Waits until every query the monitor has in flight has finished, so the next tick
    /// sees all of their results.
    async fn settle(monitor: &Monitor<ScriptedHostProvider, ScriptedGuestProvider>) {
        while monitor.active_host_task.as_ref().is_some_and(|t| !t.is_finished())
            || monitor.active_devices_task.as_ref().is_some_and(|t| !t.is_finished())
            || monitor.active_guest_tasks.values().any(|t| !t.is_finished())
        {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    }

    #[tokio::test]
    async fn test_restarted_emulator_does_not_inherit_old_guest_result() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Old", false));
        let mut monitor = scripted_monitor(&host, &guest);

        // A booted emulator is seen, then killed: adb and the host stop listing it.
        for _ in 0..2 {
            monitor.tick(Instant::now()).await;
            settle(&monitor).await;
        }
        host.emulators.lock().unwrap().clear();
        guest.devices.lock().unwrap().clear();
        for _ in 0..2 {
            monitor.tick(Instant::now()).await;
            settle(&monitor).await;
        }

        // A new emulator boots on the same serial within the eviction timeout. Once the
        // monitor sees its PID, nothing from the old emulator may carry over.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(200, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest.results.lock().unwrap().insert(serial.to_string(), scripted_guest_stats("New", true));
        for _ in 0..4 {
            monitor.tick(Instant::now()).await;
            settle(&monitor).await;
            let state = &monitor.device_states[serial];
            if state.last_known_pid == Some(200) {
                assert!(!state.boot_completed, "the new emulator is still booting");
                assert!(
                    monitor.cached_model(serial).is_none_or(|name| name != "Old"),
                    "{:?}",
                    monitor.cached_model(serial)
                );
            }
        }
        assert_eq!(monitor.device_states[serial].last_known_pid, Some(200));
        assert_eq!(monitor.cached_model(serial), Some("New"));
        assert_eq!(monitor.device_states[serial].is_booting, Some(true));
    }

    #[tokio::test]
    async fn test_new_emulator_process_cancels_the_old_guest_query() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Old", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        // The next query of the old emulator hangs past the tick.
        *guest.query_time.lock().unwrap() = Duration::from_secs(60);
        monitor.tick(Instant::now()).await;
        assert!(monitor.active_guest_tasks.contains_key(serial));

        // A new emulator process takes over the serial; its query must not wait for the old one.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(200, 5554)];
        *guest.query_time.lock().unwrap() = Duration::ZERO;
        guest.results.lock().unwrap().insert(serial.to_string(), scripted_guest_stats("New", true));
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.cached_model(serial), Some("New"));
        assert!(!monitor.device_states[serial].boot_completed);
    }

    #[tokio::test]
    async fn test_each_tick_records_its_own_samples() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        let mut monitor = scripted_monitor(&host, &guest);

        for cpu in ["10%", "20%", "30%"] {
            let stats =
                GuestStats { cpu_total: cpu.to_string(), ..scripted_guest_stats("Pixel 6", false) };
            guest.results.lock().unwrap().insert(serial.to_string(), stats);
            monitor.tick(Instant::now()).await;
        }

        let rows = &monitor.summary.time_series;
        let host_cpu: Vec<f32> = rows.iter().map(|row| row["host_cpu"]).collect();
        let guest_cpu: Vec<f32> =
            rows.iter().map(|row| row["emu_cpu_tot_Pixel_6_emulator-5554"]).collect();
        assert_eq!(host_cpu, [1.0, 2.0, 3.0]);
        assert_eq!(guest_cpu, [10.0, 20.0, 30.0]);
    }

    /// Host provider whose sample waits up to 2 s for the `adb devices` query to start. It
    /// reports 1.0 % CPU if the two ran at the same time and 0.0 % if they ran one by one.
    #[derive(Clone, Default)]
    struct OverlapHostProvider {
        devices_started: Arc<std::sync::atomic::AtomicBool>,
    }

    impl provider::HostProvider for OverlapHostProvider {
        fn get_cpu_cores(&self) -> usize {
            4
        }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            let started = || self.devices_started.load(std::sync::atomic::Ordering::SeqCst);
            let give_up = std::time::Instant::now() + Duration::from_secs(2);
            while !started() && std::time::Instant::now() < give_up {
                std::thread::sleep(Duration::from_millis(5));
            }
            HostStats { cpu_usage: if started() { 1.0 } else { 0.0 }, ..initial_host_stats() }
        }
    }

    #[derive(Clone, Default)]
    struct OverlapGuestProvider {
        devices_started: Arc<std::sync::atomic::AtomicBool>,
    }

    impl provider::GuestProvider for OverlapGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            self.devices_started.store(true, std::sync::atomic::Ordering::SeqCst);
            HashMap::new()
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> GuestStats {
            unreachable!("no devices are listed")
        }
    }

    #[tokio::test]
    async fn test_first_tick_queries_host_and_devices_together() {
        let host = OverlapHostProvider::default();
        let guest = OverlapGuestProvider { devices_started: host.devices_started.clone() };
        let mut monitor = Monitor::new(host, guest, scripted_args(), "Specs".to_string());
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.summary.time_series[0]["host_cpu"], 1.0);
    }

    #[tokio::test]
    async fn test_physical_device_does_not_match_unmapped_emulator() {
        let serial = "HT4A12345678";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            pid: 9999,
            port: None,
            avd_name: Some("Unmapped_AVD".to_string()),
            cpu_usage: 77.0,
            ram_mb: 2048.0,
            threads: 8,
            context_switches: 500,
            gpu_load: Some(33.0),
        }];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        assert_eq!(
            monitor.summary.device_names.get(serial).map(String::as_str),
            Some("HT4A12345678 | Pixel 6")
        );
        assert!(!monitor.summary.device_names.contains_key("Unmapped_AVD_HT4A12345678"));
        assert!(!monitor.summary.time_series[0].contains_key("qemu_cpu_HT4A12345678"));
        assert!(!monitor.summary.time_series[0].contains_key("qemu_cpu_Unmapped_AVD_HT4A12345678"));

        let mut prev_stats = HashMap::new();
        let online = HashMap::new();
        let mut guest_ui = HashMap::new();
        guest_ui.insert(
            serial.to_string(),
            GuestUiStats::from_guest_stats(&scripted_guest_stats("Pixel 6", false), false),
        );
        let dashboard = ui::build_dashboard(ui::DashboardInput {
            host_stats: &monitor.stats,
            guest_results: &guest_ui,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 100,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        assert!(dashboard.contains("GUEST SYSTEM (HT4A12345678 | Pixel 6)"));
        assert!(!dashboard.contains("GUEST SYSTEM (Unmapped_AVD"));
        assert!(!dashboard.contains("Host QEMU GPU Load"));
    }

    #[tokio::test]
    async fn test_unavailable_guest_stats_record_nan_in_time_series() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 0,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        let summary =
            run(MockHostProvider, MockOfflineGuestProvider, args, "Specs".to_string()).await;
        let row = &summary.time_series[0];
        assert!(
            row["emu_cpu_tot_emulator-5554"].is_nan(),
            "emu_cpu_tot should be NaN when offline, got {}",
            row["emu_cpu_tot_emulator-5554"]
        );
        assert!(
            row["emu_cpu_adj_emulator-5554"].is_nan(),
            "emu_cpu_adj should be NaN when offline, got {}",
            row["emu_cpu_adj_emulator-5554"]
        );
        assert!(
            row["emu_ram_emulator-5554"].is_nan(),
            "emu_ram should be NaN when offline, got {}",
            row["emu_ram_emulator-5554"]
        );
        assert!(
            row["emu_disk_pct_emulator-5554"].is_nan(),
            "emu_disk_pct should be NaN when data_pct is None, got {}",
            row["emu_disk_pct_emulator-5554"]
        );
    }

    #[tokio::test]
    async fn test_sleep_does_not_overshoot_short_remainder_when_connecting() {
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        let monitor = scripted_monitor(&host, &guest);

        // Simulate a tick that already used its full 1.0s budget so only MIN_SLEEP_SECS (0.1s) remains.
        let tick_start = Instant::now() - Duration::from_secs(2);
        let before = Instant::now();
        monitor.sleep(tick_start, true).await;
        let elapsed = before.elapsed();
        assert!(
            elapsed < Duration::from_millis(250),
            "sleep with 0.1s remaining overshot to {elapsed:?} when has_connecting=true"
        );
    }

    #[tokio::test]
    async fn test_disconnected_device_shown_past_cache_eviction_and_reset_on_reconnect() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        // Device disconnects; after >5s (cache eviction) but <120s (display window),
        // it must still appear as [DISCONNECTED] in the UI.
        monitor.devices.clear();
        monitor.device_states.get_mut(serial).unwrap().disconnect_since =
            Some(Instant::now() - Duration::from_secs(6));
        monitor.update_device_caches();
        assert_eq!(monitor.cached_model(serial), None);

        let mut ts = HashMap::new();
        let guest_ui = monitor.process_guest_results(&mut ts);
        let ui_entry = guest_ui.get(serial).expect(
            "disconnected device within 120s display window should still be shown after 5s cache eviction",
        );
        assert!(ui_entry.is_disconnected);
        assert_eq!(ui_entry.model, "Pixel 6");

        // When a new device later connects on the same serial, its state starts fresh.
        monitor.devices.insert(serial.to_string(), "device".to_string());
        monitor.update_device_caches();
        assert!(!monitor.device_states[serial].boot_completed);
        assert!(monitor.device_states[serial].last_guest_res.is_none());
    }

    #[tokio::test]
    async fn test_csv_schema_and_values_match_python_parity() {
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![
            scripted_emulator(100, 5554),
            EmulatorProcess {
                pid: 101,
                port: Some(5556),
                avd_name: Some("Watch".to_string()),
                cpu_usage: 50.0,
                ram_mb: 1024.0,
                threads: 20,
                context_switches: 12,
                gpu_load: None,
            },
            EmulatorProcess {
                pid: 999,
                port: None,
                avd_name: None,
                cpu_usage: 5.24,
                ram_mb: 128.44,
                threads: 4,
                context_switches: 3,
                gpu_load: None,
            },
        ];
        guest.devices.lock().unwrap().insert("emulator-5554".to_string(), "device".to_string());
        guest.devices.lock().unwrap().insert("emulator-5556".to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert("emulator-5554".to_string(), scripted_guest_stats("Pixel 6", false));
        guest
            .results
            .lock()
            .unwrap()
            .insert("emulator-5556".to_string(), scripted_guest_stats("Pixel Watch", false));

        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        let dir = tempfile::tempdir().unwrap();
        let csv_path = dir.path().join("out.csv");
        summary::export_csv(&monitor.summary, csv_path.to_str().unwrap()).unwrap();
        let csv_text = std::fs::read_to_string(&csv_path).unwrap();
        let mut lines = csv_text.lines();
        let header = lines.next().unwrap();
        let row = lines.next().unwrap();

        let expected_header = [
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
            "netsim_up",
            "netsim_pid",
            "netsim_restarts",
            "netsim_conns",
            "netsim_devices",
            "netsim_state_Pixel_6_emulator-5554",
            "emu_cpu_tot_Pixel_6_emulator-5554",
            "emu_cpu_adj_Pixel_6_emulator-5554",
            "emu_ram_Pixel_6_emulator-5554",
            "emu_swap_Pixel_6_emulator-5554",
            "emu_errs_Pixel_6_emulator-5554",
            "emu_disk_pct_Pixel_6_emulator-5554",
            "qemu_cpu_Pixel_6_emulator-5554",
            "qemu_ram_Pixel_6_emulator-5554",
            "qemu_cs_Pixel_6_emulator-5554",
            "qemu_gpu_Pixel_6_emulator-5554",
            "netsim_state_Watch_emulator-5556",
            "emu_cpu_tot_Watch_emulator-5556",
            "emu_cpu_adj_Watch_emulator-5556",
            "emu_ram_Watch_emulator-5556",
            "emu_swap_Watch_emulator-5556",
            "emu_errs_Watch_emulator-5556",
            "emu_disk_pct_Watch_emulator-5556",
            "qemu_cpu_Watch_emulator-5556",
            "qemu_ram_Watch_emulator-5556",
            "qemu_cs_Watch_emulator-5556",
            "qemu_gpu_Watch_emulator-5556",
        ]
        .join(",");
        assert_eq!(header, expected_header);

        let cols: Vec<&str> = row.split(',').collect();
        assert_eq!(cols.len(), 43);
        // host_cpu = 1.0, host_gpu = "", host_unmapped_qemu_cpu = 5.2, host_unmapped_qemu_ram = 128.4
        assert_eq!(cols[1], "1.0");
        assert_eq!(cols[5], "");
        assert_eq!(cols[8], "");
        assert_eq!(cols[14], "5.2");
        assert_eq!(cols[15], "128.4");
        // qemu_gpu defaults to 0.0 when QEMU is running without per-process GPU telemetry
        assert_eq!(cols[31], "0.0");
        assert_eq!(cols[42], "0.0");
    }

    #[tokio::test]
    async fn test_already_booted_and_offline_devices_do_not_record_bogus_boot_logs() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        assert!(
            monitor.summary.boot_logs.get("Pixel_6_emulator-5554").is_none_or(|v| v.is_empty()),
            "Already-booted emulator should not record Boot Progression Log: {:?}",
            monitor.summary.boot_logs
        );
        assert!(
            monitor.device_states[serial].boot_finished_time.is_none(),
            "Already-booted emulator should not start a post-boot tracking window"
        );

        // A booting emulator that is still offline (N/A metrics) must not push N/A rows to boot_logs.
        let offline_serial = "emulator-5556";
        guest.devices.lock().unwrap().insert(offline_serial.to_string(), "offline".to_string());
        guest.results.lock().unwrap().insert(
            offline_serial.to_string(),
            GuestStats::unavailable("Pixel 7", true, "offline"),
        );
        monitor.devices.insert(offline_serial.to_string(), "offline".to_string());
        monitor.tick(Instant::now()).await;
        assert!(
            monitor.summary.boot_logs.get(offline_serial).is_none_or(|v| v.is_empty()),
            "Offline placeholder stats should not be appended to boot_logs: {:?}",
            monitor.summary.boot_logs
        );
    }

    #[tokio::test]
    async fn test_restarted_emulator_after_offline_tick_invalidates_cached_model() {
        let mut monitor = seeded_cache_monitor();

        // Emulator A (PID 100) exits while adb still lists emulator-5554 as offline.
        monitor.stats.emulators.clear();
        monitor.devices.insert("emulator-5554".to_string(), "offline".to_string());
        monitor.update_device_caches();
        monitor.reconcile_guest_tasks(None);
        assert!(monitor.device_states["emulator-5554"].last_known_pid.is_none());
        assert!(monitor.device_states["emulator-5554"].host_process_exited);
        assert_eq!(monitor.cached_model("emulator-5554"), Some("Model A"));

        // Emulator B starts on emulator-5554 (reusing PID 100 or with PID 200).
        monitor.stats.emulators = vec![scripted_emulator(100, 5554)];
        monitor.devices.insert("emulator-5554".to_string(), "device".to_string());
        monitor.update_device_caches();
        monitor.reconcile_guest_tasks(None);
        for (_, task) in monitor.active_guest_tasks.drain() {
            task.abort();
        }
        assert_eq!(
            monitor.cached_model("emulator-5554"),
            None,
            "Stale model from exited PID 100 leaked to restarted emulator"
        );
        assert_eq!(monitor.device_states["emulator-5554"].last_known_pid, Some(100));
    }

    #[tokio::test]
    async fn test_disconnected_device_expires_after_display_window_even_if_offline_or_booting() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        // Emulator is killed: adb briefly reports "offline", preserving the booted state.
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("Pixel 6", false, "offline"));
        monitor.devices.insert(serial.to_string(), "offline".to_string());
        monitor.tick(Instant::now()).await;

        // Emulator then disappears from adb; after >120s (DISCONNECTED_DISPLAY_WINDOW_SECS),
        // it must expire from the dashboard and be removed from device_states.
        monitor.devices.clear();
        monitor.device_states.get_mut(serial).unwrap().disconnect_since =
            Some(Instant::now() - Duration::from_secs(600));
        monitor.update_device_caches();
        monitor.device_states.get_mut(serial).unwrap().last_seen_time =
            Some(Instant::now() - Duration::from_secs(600));

        let mut ts = HashMap::new();
        let guest_ui = monitor.process_guest_results(&mut ts);
        assert!(
            !guest_ui.contains_key(serial),
            "Disconnected device still shown after 600s (> 120s window): {guest_ui:?}"
        );
        assert!(
            !monitor.device_states.contains_key(serial),
            "Evicted device_state should be removed once display window expires"
        );
    }

    #[tokio::test]
    async fn test_storage_key_migrates_bare_serial_and_preserves_distinct_avd() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        // Tick 1: host process on port 5554 has no avd_name yet and is not in adb devices yet.
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            pid: 100,
            port: Some(5554),
            avd_name: None,
            cpu_usage: 45.0,
            ram_mb: 1024.0,
            threads: 10,
            context_switches: 5,
            gpu_load: None,
        }];
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert!(monitor.summary.time_series[0].contains_key("qemu_cpu_emulator-5554"));

        // Tick 2: avd_name is resolved to Pixel_6 and adb lists emulator-5554.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        monitor.devices.insert(serial.to_string(), "device".to_string());
        monitor.tick(Instant::now()).await;

        assert!(
            !monitor.summary.time_series[0].contains_key("qemu_cpu_emulator-5554"),
            "Bare key qemu_cpu_emulator-5554 was not migrated to Pixel_6_emulator-5554: {:?}",
            monitor.summary.time_series[0]
        );
        assert_eq!(
            monitor.summary.time_series[0].get("qemu_cpu_Pixel_6_emulator-5554"),
            Some(&45.0)
        );

        // If a distinct AVD key (Pixel_8_emulator-5554) is resolved on the same serial,
        // Pixel_6_emulator-5554 must not be merged into Pixel_8_emulator-5554 or removed.
        let state = monitor.device_states.get_mut(serial).unwrap();
        let new_key = resolve_and_migrate_storage_key(
            &mut monitor.summary,
            state,
            serial,
            "Pixel_8",
            "Pixel 8",
            &mut monitor.bare_key_serials,
        );
        assert_eq!(new_key, "Pixel_8_emulator-5554");
        assert!(
            monitor.summary.device_names.contains_key("Pixel_6_emulator-5554"),
            "Distinct AVD Pixel_6_emulator-5554 must be preserved in summary.device_names"
        );
        assert_eq!(
            monitor.summary.time_series[0].get("qemu_cpu_Pixel_6_emulator-5554"),
            Some(&45.0)
        );
    }

    #[tokio::test]
    async fn test_poll_devices_updates_on_every_tick() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        let mut monitor = scripted_monitor(&host, &guest);

        // Tick 1: no devices connected.
        monitor.tick(Instant::now()).await;
        assert!(monitor.devices.is_empty());

        // Before Tick 2: emulator-5554 connects.
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        monitor.tick(Instant::now()).await;

        assert!(
            monitor.devices.contains_key(serial),
            "Tick 2 should observe newly connected device without a dead tick"
        );
    }

    #[tokio::test]
    async fn test_finish_aborts_in_flight_guest_and_device_tasks() {
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        let mut monitor = scripted_monitor(&host, &guest);
        let dev_handle = tokio::spawn(async {
            tokio::time::sleep(Duration::from_secs(60)).await;
            HashMap::new()
        });
        let guest_handle = tokio::spawn(async {
            tokio::time::sleep(Duration::from_secs(60)).await;
            scripted_guest_stats("Pixel 6", false)
        });
        let dev_abort = dev_handle.abort_handle();
        let guest_abort = guest_handle.abort_handle();
        monitor.active_devices_task = Some(dev_handle);
        monitor.active_guest_tasks.insert("emulator-5554".to_string(), guest_handle);

        monitor.finish(Ok(())).await;
        tokio::task::yield_now().await;

        assert!(dev_abort.is_finished(), "active_devices_task should be aborted on finish()");
        assert!(guest_abort.is_finished(), "active_guest_tasks should be aborted on finish()");
    }

    #[tokio::test]
    async fn test_timed_out_samples_are_not_recorded_again() {
        let serial = "emulator-5554";
        let key = "Pixel_6_emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        let guest_result = |cpu: &str| GuestStats {
            cpu_total: cpu.to_string(),
            ..scripted_guest_stats("Pixel 6", false)
        };
        guest.results.lock().unwrap().insert(serial.to_string(), guest_result("10%"));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        // Both the host sample and the guest query outlast the 1 s tick, so tick 2 gets
        // neither result; both arrive during tick 3.
        *host.sample_time.lock().unwrap() = Duration::from_millis(1500);
        *guest.query_time.lock().unwrap() = Duration::from_millis(1500);
        guest.results.lock().unwrap().insert(serial.to_string(), guest_result("30%"));
        monitor.tick(Instant::now()).await;
        *host.sample_time.lock().unwrap() = Duration::ZERO;
        *guest.query_time.lock().unwrap() = Duration::ZERO;
        let mut ts = HashMap::new();
        let ui = monitor.process_guest_results(&mut ts);
        assert!(
            ui[serial].cpu_adjusted.ends_with(" (Timeout)"),
            "a timed-out guest shows its last CPU value marked (Timeout): {:?}",
            ui[serial].cpu_adjusted
        );
        monitor.tick(Instant::now()).await;

        let rows = &monitor.summary.time_series;
        let host_cpu: Vec<f32> = rows.iter().map(|row| row["host_cpu"]).collect();
        let qemu_cpu: Vec<f32> = rows.iter().map(|row| row[&format!("qemu_cpu_{key}")]).collect();
        let guest_cpu: Vec<f32> =
            rows.iter().map(|row| row[&format!("emu_cpu_tot_{key}")]).collect();
        assert_eq!(host_cpu[0], 1.0);
        assert!(host_cpu[1].is_nan(), "stale host sample recorded again: {host_cpu:?}");
        assert_eq!(host_cpu[2], 2.0);
        assert!(qemu_cpu[1].is_nan(), "stale QEMU sample recorded again: {qemu_cpu:?}");
        assert_eq!(guest_cpu[0], 10.0);
        assert!(guest_cpu[1].is_nan(), "stale guest sample recorded again: {guest_cpu:?}");
        assert_eq!(guest_cpu[2], 30.0);
        assert_eq!(monitor.summary.host_cpu_max, 2.0);
    }

    #[tokio::test]
    async fn test_bare_serial_migration_runs_once() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        // Tick 1: the emulator's AVD is not known yet, so its metrics go under the bare serial.
        *host.emulators.lock().unwrap() =
            vec![EmulatorProcess { avd_name: None, ..scripted_emulator(100, 5554) }];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert!(monitor.summary.time_series[0].contains_key("emu_cpu_tot_emulator-5554"));

        // Tick 2: the AVD is known and the bare-serial history moves to the AVD key.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        monitor.tick(Instant::now()).await;
        let row0 = &monitor.summary.time_series[0];
        assert!(!row0.contains_key("emu_cpu_tot_emulator-5554"), "{row0:?}");
        assert_eq!(row0.get("emu_cpu_tot_Pixel_6_emulator-5554"), Some(&50.0));

        // Later ticks have nothing left under the bare serial and must not walk the
        // history again: a bare column planted now stays where it is.
        monitor.summary.time_series[0].insert("qemu_cpu_emulator-5554".to_string(), 99.0);
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.time_series[0].get("qemu_cpu_emulator-5554"),
            Some(&99.0),
            "the bare-serial migration ran again on a later tick"
        );
    }

    #[tokio::test]
    async fn test_host_and_guest_queries_run_concurrently_within_tick() {
        let serial = "emulator-5554";
        let key = "Pixel_6_emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        // Tick 2 (1.0s interval): 600ms host + 600ms guest fit within 1.0s when run concurrently.
        *host.sample_time.lock().unwrap() = Duration::from_millis(600);
        *guest.query_time.lock().unwrap() = Duration::from_millis(600);
        guest.results.lock().unwrap().insert(
            serial.to_string(),
            GuestStats { cpu_total: "77%".to_string(), ..scripted_guest_stats("Pixel 6", false) },
        );
        monitor.tick(Instant::now()).await;

        // Tick 3: even when the host sample itself exceeds the 1.0s deadline, a fast guest query
        // must still finish and be recorded on its own tick.
        *host.sample_time.lock().unwrap() = Duration::from_millis(1400);
        *guest.query_time.lock().unwrap() = Duration::from_millis(50);
        guest.results.lock().unwrap().insert(
            serial.to_string(),
            GuestStats { cpu_total: "88%".to_string(), ..scripted_guest_stats("Pixel 6", false) },
        );
        monitor.tick(Instant::now()).await;

        let col = format!("emu_cpu_tot_{key}");
        assert_eq!(monitor.summary.time_series[1][&col], 77.0);
        assert_eq!(monitor.summary.time_series[2][&col], 88.0);
        assert!(monitor.summary.time_series[2]["host_cpu"].is_nan());
    }

    #[tokio::test]
    async fn test_qemu_next_group_pid_shifts_preserve_session_and_pid_reuse_resets() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        let group_info =
            |primary_pid: u32, start_time: u64, pids: Vec<u32>| netsim::EmuGroupNetsimInfo {
                serial: serial.to_string(),
                port: 5554,
                primary_pid,
                primary_start_time: start_time,
                pids,
                avd_id: Some("Pixel_6".to_string()),
                display_name: Some("Pixel 6".to_string()),
                ..Default::default()
            };

        // Tick 1: Launcher PID 100 starts first while booting.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(100, 1000, vec![100]));
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", true));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        let initial_first_seen = monitor.device_states[serial].first_seen_time;
        assert!(initial_first_seen.is_some());

        // Tick 2: Worker PID 101 spawns inside the same QEMU-next group and finishes boot.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(101, 5554)];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(101, 1001, vec![100, 101]));
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.cached_model(serial), Some("Pixel 6"));
        assert_eq!(monitor.device_states[serial].first_seen_time, initial_first_seen);
        assert!(monitor.device_states[serial].boot_completed);
        monitor.prev_stats.insert(format!("{serial}_real"), 12.5);

        // Tick 3: Worker 101 exits first during shutdown; launcher 100 survives while adb reports offline.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(100, 1000, vec![100]));
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("", false, "offline"));
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.cached_model(serial), Some("Pixel 6"));
        assert!(monitor.device_states[serial].boot_completed);

        // Tick 4: Launcher 100 finishes exiting and adb drops the serial -> [DISCONNECTED] card stays visible.
        host.emulators.lock().unwrap().clear();
        host.emu_groups.lock().unwrap().clear();
        guest.devices.lock().unwrap().clear();
        monitor.tick(Instant::now()).await;
        let mut ts = HashMap::new();
        let ui = monitor.process_guest_results(&mut ts);
        assert!(
            ui.get(serial).is_some_and(|c| c.is_disconnected && c.model == "Pixel 6"),
            "QEMU-next worker->launcher shutdown shift wiped [DISCONNECTED] card: {ui:?}"
        );

        // Tick 5: A new emulator reuses PID 100 with a newer start_time -> state and prev_stats reset.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(100, 2000, vec![100]));
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 8", true));
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.cached_model(serial), Some("Pixel 8"));
        assert!(!monitor.device_states[serial].boot_completed);
        assert!(!monitor.prev_stats.contains_key(&format!("{serial}_real")));
    }

    #[tokio::test]
    async fn test_booted_device_offline_and_truncated_batch_preserve_boot_state() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert!(monitor.device_states[serial].boot_completed);

        // RealGuestProvider must preserve is_booting=false when a booted device goes offline.
        let offline_stats =
            guest::RealGuestProvider.get_guest_stats(serial, "Pixel 6", false, "offline").await;
        assert!(!offline_stats.is_booting, "offline stats reset is_booting to true");

        // An already-booted device going offline must not flash [RECORDING BOOT STATS].
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest.results.lock().unwrap().insert(serial.to_string(), offline_stats);
        monitor.tick(Instant::now()).await;
        let mut ts = HashMap::new();
        let ui = monitor.process_guest_results(&mut ts);
        assert!(
            !ui[serial].recording_boot_stats,
            "booted device going offline showed [RECORDING BOOT STATS]: {:?}",
            ui[serial]
        );
        assert!(monitor.device_states[serial].first_seen_time.is_none());

        // A booting device past the 900s boot window stops showing [RECORDING BOOT STATS].
        let boot_serial = "emulator-5556";
        guest.devices.lock().unwrap().insert(boot_serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(boot_serial.to_string(), scripted_guest_stats("Pixel 7", true));
        monitor.tick(Instant::now()).await;
        monitor.device_states.get_mut(boot_serial).unwrap().first_seen_time =
            Some(Instant::now() - Duration::from_secs(MAX_BOOT_LOG_WINDOW_SECS + 5));
        monitor.tick(Instant::now()).await;
        let ui_after_window = monitor.process_guest_results(&mut ts);
        assert!(!ui_after_window[boot_serial].recording_boot_stats);
    }

    #[tokio::test]
    async fn test_modelless_disconnect_evicts_and_host_only_or_replaced_emus_stay_isolated() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();

        // Part 1: Offline device without cached model disconnects; after >5s eviction and
        // >120s display window, device_states and serial_pids must be cleaned up.
        *host.emulators.lock().unwrap() =
            vec![EmulatorProcess { avd_name: None, ..scripted_emulator(100, 5554) }];
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("", true, "offline"));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.summary.time_series[0].get("qemu_cpu_emulator-5554"), Some(&100.0));

        host.emulators.lock().unwrap().clear();
        guest.devices.lock().unwrap().clear();
        monitor.tick(Instant::now()).await;
        if let Some(st) = monitor.device_states.get_mut(serial) {
            st.disconnect_since = Some(Instant::now() - Duration::from_secs(600));
            st.last_seen_time = Some(Instant::now() - Duration::from_secs(600));
        }
        monitor.tick(Instant::now()).await;
        assert!(
            !monitor.device_states.contains_key(serial),
            "model-less disconnected serial leaked in device_states: {:?}",
            monitor.device_states
        );

        // Part 2: Unrelated replacement emulator PID 200 (Pixel_8) on the same port must NOT
        // absorb PID 100's bare-serial history into Pixel_8_emulator-5554, and host-only
        // emulators must appear in summary.device_names.
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            pid: 200,
            port: Some(5554),
            avd_name: Some("Pixel_8".to_string()),
            cpu_usage: 55.0,
            ram_mb: 1024.0,
            threads: 10,
            context_switches: 0,
            gpu_load: None,
        }];
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.time_series[0].get("qemu_cpu_emulator-5554"),
            Some(&100.0),
            "earlier PID 100 bare-serial metrics were wrongly migrated into replacement PID 200"
        );
        assert!(!monitor.summary.time_series[0].contains_key("qemu_cpu_Pixel_8_emulator-5554"));
        assert_eq!(
            monitor.summary.device_names.get("Pixel_8_emulator-5554").map(String::as_str),
            Some("Pixel_8"),
            "host-only emulator missing from summary.device_names: {:?}",
            monitor.summary.device_names
        );
    }

    #[tokio::test]
    async fn test_dashboard_pending_guest_avd_retention_thermal_zero_and_non_leading_na() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();

        // Tick 1: host has Pixel_6 (PID 100, port 5554), adb has not listed it yet.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.interval = 0.2;
        monitor.tick(Instant::now()).await;

        // Tick 2: emulator-5554 appears in adb, but its first guest query takes 1500ms (> 200ms).
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest.query_time.lock().unwrap() = Duration::from_millis(1500);
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", true));
        monitor.tick(Instant::now()).await;
        let mut ts = HashMap::new();
        let pending_ui = monitor.process_guest_results(&mut ts);
        let mut prev_stats = HashMap::new();
        let online = HashMap::new();
        let pending_dash = ui::build_dashboard(ui::DashboardInput {
            host_stats: &monitor.stats,
            guest_results: &pending_ui,
            prev_stats: &mut prev_stats,
            iter_count: 2,
            term_width: 110,
            is_once: false,
            hardware_specs: "Specs",
            force_clear: false,
            online_indicator_until: &online,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        assert!(
            !pending_dash.contains("No devices connected"),
            "connected emulator with in-flight initial query hid guest card:\n{pending_dash}"
        );
        assert!(pending_dash.contains("GUEST SYSTEM (Pixel_6)"), "{pending_dash}");
        assert!(pending_dash.contains("Calculating..."), "{pending_dash}");
        assert!(pending_dash.contains("Host QEMU CPU"), "{pending_dash}");
        for task in monitor.active_guest_tasks.values() {
            task.abort();
        }
        monitor.active_guest_tasks.clear();

        // Tick 3: guest query finishes quickly -> model resolved to "Pixel 6".
        *guest.query_time.lock().unwrap() = Duration::ZERO;
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        monitor.tick(Instant::now()).await;

        // Tick 4: QEMU process exits while adb still reports offline -> header retains Pixel_6.
        host.emulators.lock().unwrap().clear();
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("Pixel 6", false, "offline"));
        monitor.tick(Instant::now()).await;
        let offline_ui = monitor.process_guest_results(&mut ts);
        let offline_dash = ui::build_dashboard(ui::DashboardInput {
            host_stats: &monitor.stats,
            guest_results: &offline_ui,
            prev_stats: &mut prev_stats,
            iter_count: 4,
            term_width: 110,
            is_once: false,
            hardware_specs: "Specs",
            force_clear: false,
            online_indicator_until: &online,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        assert!(
            offline_dash.contains("GUEST SYSTEM (Pixel_6 | Pixel 6)"),
            "offline card after QEMU exit lost AVD name:\n{offline_dash}"
        );

        // Tick 5: adb drops emulator-5554 while a new emulator (Pixel_9) starts on port 5554.
        // The [DISCONNECTED] card must keep Pixel_6 and NOT attach Pixel_9's live QEMU stats.
        guest.devices.lock().unwrap().clear();
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            pid: 999,
            port: Some(5554),
            avd_name: Some("Pixel_9".to_string()),
            cpu_usage: 88.0,
            ram_mb: 4096.0,
            threads: 20,
            context_switches: 0,
            gpu_load: Some(45.0),
        }];
        monitor.tick(Instant::now()).await;
        let disc_ui = monitor.process_guest_results(&mut ts);
        monitor.stats.thermal_temp = Some(0.0);
        let disc_dash = ui::build_dashboard(ui::DashboardInput {
            host_stats: &monitor.stats,
            guest_results: &disc_ui,
            prev_stats: &mut prev_stats,
            iter_count: 5,
            term_width: 110,
            is_once: false,
            hardware_specs: "Specs",
            force_clear: false,
            online_indicator_until: &online,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        assert!(
            disc_dash.contains("GUEST SYSTEM (Pixel_6 | Pixel 6)"),
            "disconnected card lost AVD name or collided with new emulator:\n{disc_dash}"
        );
        assert!(!disc_dash.contains("Pixel_9"), "{disc_dash}");
        assert!(!disc_dash.contains("88.0%"), "{disc_dash}");
        let temp_line = disc_dash.lines().find(|l| l.contains("Host Thermal Temperature")).unwrap();
        assert!(!temp_line.contains("0.0°C") && temp_line.contains("N/A"), "{temp_line}");

        // Non-leading "N/A" inside parentheses is NOT a placeholder, while leading "N/A" is.
        let mut disk_guest = GuestUiStats::from_guest_stats(
            &GuestStats {
                data_space: Some("25% (N/A used/N/A avail)".to_string()),
                ..scripted_guest_stats("Pixel 6", false)
            },
            false,
        );
        disk_guest.cpu_adjusted = "N/A (offline)".to_string();
        let disk_dash = ui::build_dashboard(ui::DashboardInput {
            host_stats: &initial_host_stats(),
            guest_results: &HashMap::from([(serial.to_string(), disk_guest)]),
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 110,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        let disk_line = disk_dash.lines().find(|l| l.contains("Guest Disk Space")).unwrap();
        assert!(
            disk_line.contains("\x1b[92m[█████"),
            "25% (N/A used/N/A avail) should render a green 25% bar, got: {disk_line:?}"
        );
        let adj_line = disk_dash.lines().find(|l| l.contains("Adjusted CPU Load")).unwrap();
        assert!(adj_line.contains("\x1b[96m"), "N/A (offline) must remain cyan: {adj_line:?}");

        // Boot log formatting without parenthetical RAM must not emit an unmatched ')'.
        let mut boot_summary = summary::SessionSummary::new();
        let mut boot_state = DeviceState::default();
        let mut bare_ram_stats = scripted_guest_stats("Pixel 6", true);
        bare_ram_stats.ram = "50.0%".to_string();
        assert!(record_boot_log_if_due(
            &mut boot_summary,
            &mut boot_state,
            "Pixel_6_emulator-5554",
            &bare_ram_stats,
            true,
            "device",
        ));
        let (_, log_str) = &boot_summary.boot_logs["Pixel_6_emulator-5554"][0];
        assert_eq!(
            log_str.chars().filter(|&c| c == '(').count(),
            log_str.chars().filter(|&c| c == ')').count(),
            "unbalanced parentheses in boot log: {log_str}"
        );
    }

    #[tokio::test]
    async fn test_non_finite_interval_time_series_cap_and_sudo_adb_resolution() {
        for bad_interval in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let args = Args {
                once: false,
                no_summary: true,
                csv: false,
                out_dir: PathBuf::from("."),
                interval: bad_interval,
                headless: true,
                auto_save_interval: 0,
                netsim_pcap: None,
                netsim_pcap_max_mb: 500,
                no_netsim_log: true,
            };
            let mut monitor = Monitor::new(
                ScriptedHostProvider::default(),
                ScriptedGuestProvider::default(),
                args,
                "Specs".to_string(),
            );
            let t0 = Instant::now();
            monitor.tick(t0).await;
            monitor.tick(t0 + Duration::from_millis(10)).await;
            assert_eq!(
                monitor.interval, 2.0,
                "non-finite interval was not sanitized: {bad_interval}"
            );
            if let Some(earlier) = monitor.loop_start_time.checked_sub(Duration::from_secs(1)) {
                monitor.tick(earlier).await;
            }
        }

        // Non-TTY stdout must never enter/leave the alternate screen.
        let interactive_args = Args {
            once: false,
            no_summary: true,
            csv: false,
            out_dir: PathBuf::from("."),
            interval: 2.0,
            headless: false,
            auto_save_interval: 0,
            netsim_pcap: None,
            netsim_pcap_max_mb: 500,
            no_netsim_log: true,
        };
        assert!(!should_use_alternate_screen_with_tty(&interactive_args, false));
        assert!(should_use_alternate_screen_with_tty(&interactive_args, true));
        let mut headless_args = interactive_args.clone();
        headless_args.headless = true;
        assert!(!should_use_alternate_screen_with_tty(&headless_args, true));

        // Time-series buffers must stay bounded at MAX_TIME_SERIES_LEN (43,200) in sync.
        let mut cap_monitor = Monitor::new(
            ScriptedHostProvider::default(),
            ScriptedGuestProvider::default(),
            headless_args,
            "Specs".to_string(),
        );
        for i in 0..MAX_TIME_SERIES_LEN {
            cap_monitor.summary.time_series.push(HashMap::from([("time".to_string(), i as f32)]));
            cap_monitor
                .summary
                .time_series_str
                .push(BTreeMap::from([("netsim_daemon_state".to_string(), format!("row_{i}"))]));
        }
        cap_monitor.tick(Instant::now()).await;
        assert_eq!(cap_monitor.summary.time_series.len(), MAX_TIME_SERIES_LEN);
        assert_eq!(cap_monitor.summary.time_series_str.len(), MAX_TIME_SERIES_LEN);
        assert_eq!(cap_monitor.summary.time_series[0].get("time"), Some(&1.0));
        assert_eq!(
            cap_monitor.summary.time_series_str[0].get("netsim_daemon_state").map(String::as_str),
            Some("row_1")
        );

        // Sudo user validation, passwd home resolution, SDK fallback, and sudo -n -u prefix.
        assert_eq!(guest::validate_sudo_user(Some("alice_dev-1.test")), Some("alice_dev-1.test"));
        for bad_user in [None, Some(""), Some("root"), Some("-u"), Some("alice;id"), Some("a/b")] {
            assert_eq!(guest::validate_sudo_user(bad_user), None, "accepted {bad_user:?}");
        }
        let passwd = "# comment\nroot:x:0:0:root:/root:/bin/bash\nalice:x:1000:1000:Alice:/home/alice:/bin/zsh\n";
        assert_eq!(guest::parse_passwd_home(passwd, "alice").as_deref(), Some("/home/alice"));
        assert_eq!(guest::parse_passwd_home(passwd, "missing"), None);
        let sudo_adb = guest::resolve_adb_path_with_sudo(
            None,
            None,
            Some("/root"),
            Some("/home/alice"),
            &|p| p == "/home/alice/Android/Sdk/platform-tools/adb",
        );
        assert_eq!(sudo_adb.as_deref(), Some("/home/alice/Android/Sdk/platform-tools/adb"));
        assert_eq!(
            guest::adb_invocation("/home/alice/Android/Sdk/platform-tools/adb", Some("alice")),
            (
                "sudo",
                vec![
                    "-n".to_string(),
                    "-u".to_string(),
                    "alice".to_string(),
                    "/home/alice/Android/Sdk/platform-tools/adb".to_string(),
                ]
            )
        );
        assert_eq!(guest::adb_invocation("/usr/bin/adb", Some("-bad")), ("/usr/bin/adb", vec![]));
    }

    #[tokio::test]
    async fn test_quickboot_replacement_and_pending_booted_device_skip_bogus_boot_window() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();

        // Tick 1: Emulator A (Pixel_6, PID 100) runs and is booted.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.interval = 0.2;
        monitor.tick(Instant::now()).await;

        // Tick 2: Quickboot replacement Emulator B (Pixel_8, PID 200, already booted) takes over
        // port 5554 while a booted USB phone also connects; initial guest queries exceed 200ms.
        let usb_serial = "HT4A12345678";
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            ..scripted_emulator(200, 5554)
        }];
        guest.devices.lock().unwrap().insert(usb_serial.to_string(), "device".to_string());
        *guest.query_time.lock().unwrap() = Duration::from_millis(500);
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 8", false));
        guest
            .results
            .lock()
            .unwrap()
            .insert(usb_serial.to_string(), scripted_guest_stats("Pixel 9 Pro", false));
        monitor.tick(Instant::now()).await;
        let mut ts = HashMap::new();
        let pending_ui = monitor.process_guest_results(&mut ts);
        assert!(
            !pending_ui[serial].recording_boot_stats
                && !pending_ui[usb_serial].recording_boot_stats,
            "Pending initial query on online devices flashed [RECORDING BOOT STATS]: {pending_ui:?}"
        );
        for task in monitor.active_guest_tasks.values() {
            task.abort();
        }
        monitor.active_guest_tasks.clear();

        // Tick 3: Initial guest queries finish with is_booting = false -> no bogus 300s window.
        *guest.query_time.lock().unwrap() = Duration::ZERO;
        monitor.tick(Instant::now()).await;

        assert!(
            monitor.device_states[serial].boot_finished_time.is_none(),
            "Quickboot replacement falsely started 300s post-boot window: {:?}",
            monitor.device_states[serial]
        );
        assert!(
            monitor.summary.boot_logs.get("Pixel_8_emulator-5554").is_none_or(|v| v.is_empty()),
            "Quickboot replacement recorded bogus Boot Progression Log: {:?}",
            monitor.summary.boot_logs
        );

        // Tick 4-5: An already-booted phone transitioning unauthorized -> device also skips boot window.
        let unauth_serial = "USB987654321";
        guest.devices.lock().unwrap().insert(unauth_serial.to_string(), "unauthorized".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(unauth_serial.to_string(), GuestStats::unavailable("", true, "unauthorized"));
        monitor.tick(Instant::now()).await;
        guest.devices.lock().unwrap().insert(unauth_serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(unauth_serial.to_string(), scripted_guest_stats("Pixel 9", false));
        monitor.tick(Instant::now()).await;
        assert!(monitor.device_states[unauth_serial].boot_finished_time.is_none());
        assert!(monitor.summary.boot_logs.get(unauth_serial).is_none_or(|v| v.is_empty()));
    }

    #[tokio::test]
    async fn test_qemu_next_fishtank_shutdown_shift_preserves_session_and_rejects_recycled_member()
    {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        let group_info_with_members =
            |primary_pid: u32, start_time: u64, members: &[(u32, u64)]| {
                netsim::EmuGroupNetsimInfo {
                    serial: serial.to_string(),
                    port: 5554,
                    primary_pid,
                    primary_start_time: start_time,
                    pids: members.iter().map(|&(p, _)| p).collect(),
                    member_start_times: members.iter().copied().collect(),
                    avd_id: Some("Pixel_6".to_string()),
                    display_name: Some("Pixel 6".to_string()),
                    ..Default::default()
                }
            };

        // Tick 1: QEMU-next running with launcher=100 (st=1000), worker=101 (st=1001, primary),
        // fishtank GUI=102 (st=1002, spawned 1s after worker).
        *host.emulators.lock().unwrap() = vec![scripted_emulator(101, 5554)];
        host.emu_groups.lock().unwrap().insert(
            serial.to_string(),
            group_info_with_members(101, 1001, &[(100, 1000), (101, 1001), (102, 1002)]),
        );
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.cached_model(serial), Some("Pixel 6"));

        // Tick 2: Worker 101 exits first; fishtank 102 (st=1002 > 1001, no -avd flag) survives
        // as sole primary while adb reports offline.
        *host.emulators.lock().unwrap() =
            vec![EmulatorProcess { avd_name: None, ..scripted_emulator(102, 5554) }];
        host.emu_groups.lock().unwrap().insert(
            serial.to_string(),
            group_info_with_members(102, 1002, &[(100, 1000), (102, 1002)]),
        );
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("", false, "offline"));
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.cached_model(serial), Some("Pixel 6"));
        assert!(monitor.device_states[serial].boot_completed);
        assert!(
            !monitor.summary.device_names.contains_key(serial),
            "Surviving fishtank created spurious bare-serial summary entry: {:?}",
            monitor.summary.device_names
        );

        // Tick 3: Launcher 100 and fishtank 102 exit and adb drops serial -> [DISCONNECTED] stays.
        host.emulators.lock().unwrap().clear();
        host.emu_groups.lock().unwrap().clear();
        guest.devices.lock().unwrap().clear();
        monitor.tick(Instant::now()).await;
        let mut ts = HashMap::new();
        let ui = monitor.process_guest_results(&mut ts);
        assert!(
            ui.get(serial).is_some_and(|c| c.is_disconnected && c.model == "Pixel 6"),
            "QEMU-next worker->fishtank shutdown shift lost [DISCONNECTED] card: {ui:?}"
        );

        // Tick 4: A new QEMU-next session reuses PID 102 as launcher (st=2000 != 1002) -> resets.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(301, 5554)];
        host.emu_groups.lock().unwrap().insert(
            serial.to_string(),
            group_info_with_members(301, 2001, &[(102, 2000), (301, 2001)]),
        );
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 8", true));
        monitor.tick(Instant::now()).await;
        assert_eq!(monitor.cached_model(serial), Some("Pixel 8"));
        assert!(!monitor.device_states[serial].boot_completed);
    }

    #[tokio::test]
    async fn test_physical_and_bare_serial_summary_names_refresh_past_unknown_and_res_na() {
        let usb_serial = "HT4A12345678";
        let emu_serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();

        // Tick 1: USB phone starts as unauthorized ("Unknown (unauthorized)"), while an AVD
        // emulator resolves its real model.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(usb_serial.to_string(), "unauthorized".to_string());
        guest.devices.lock().unwrap().insert(emu_serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(usb_serial.to_string(), GuestStats::unavailable("", true, "unauthorized"));
        guest.results.lock().unwrap().insert(
            emu_serial.to_string(),
            scripted_guest_stats(
                "Pixel 6 | Android 15 (API 35) | Build: AP4A | Res: 1080x2400",
                false,
            ),
        );
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        // Tick 2: USB phone is authorized during early boot ("Res: N/A"), while emulator receives
        // an "Unknown (offline)" placeholder that must NOT overwrite its resolved model.
        guest.devices.lock().unwrap().insert(usb_serial.to_string(), "device".to_string());
        guest.devices.lock().unwrap().insert(emu_serial.to_string(), "offline".to_string());
        guest.results.lock().unwrap().insert(
            usb_serial.to_string(),
            scripted_guest_stats(
                "Pixel 9 Pro | Android 16 (API 36) | Build: BP2A | Res: N/A",
                true,
            ),
        );
        guest
            .results
            .lock()
            .unwrap()
            .insert(emu_serial.to_string(), GuestStats::unavailable("", false, "offline"));
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.device_names.get(usb_serial).map(String::as_str),
            Some("HT4A12345678 | Pixel 9 Pro | Android 16 (API 36) | Build: BP2A | Res: N/A")
        );
        assert_eq!(
            monitor.summary.device_names.get("Pixel_6_emulator-5554").map(String::as_str),
            Some("Pixel_6 | Pixel 6 | Android 15 (API 35) | Build: AP4A | Res: 1080x2400")
        );

        // Tick 3: WindowManager comes up on USB phone -> "Res: 1280x2856" replaces "Res: N/A".
        guest.results.lock().unwrap().insert(
            usb_serial.to_string(),
            scripted_guest_stats(
                "Pixel 9 Pro | Android 16 (API 36) | Build: BP2A | Res: 1280x2856",
                false,
            ),
        );
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.device_names.get(usb_serial).map(String::as_str),
            Some("HT4A12345678 | Pixel 9 Pro | Android 16 (API 36) | Build: BP2A | Res: 1280x2856")
        );
    }

    #[tokio::test]
    async fn test_stale_adb_offline_entry_does_not_poison_boot_window_or_bare_keys() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();

        // Tick 1: Stale killed emulator entry sits in `adb devices` as offline with no host QEMU.
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("", true, "offline"));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert!(
            monitor.device_states[serial].first_seen_time.is_none(),
            "Stale offline entry without host QEMU prematurely locked first_seen_time"
        );

        // Tick 2: User launches cold-booting Pixel_8 (PID 200) on port 5554.
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            ..scripted_emulator(200, 5554)
        }];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 8", true));
        monitor.tick(Instant::now()).await;

        let logs = monitor.summary.boot_logs.get("Pixel_8_emulator-5554");
        assert!(
            logs.is_some_and(|v| !v.is_empty() && v[0].0 == "+0s"),
            "Cold-booting emulator after stale offline entry failed to record +0s boot log: {logs:?}"
        );
        assert!(
            !monitor.summary.time_series[0].contains_key("emu_cpu_tot_Pixel_8_emulator-5554"),
            "Stale pre-launch offline row was wrongly migrated into Pixel_8_emulator-5554: {:?}",
            monitor.summary.time_series[0]
        );
    }

    #[tokio::test]
    async fn test_transient_initial_shell_failure_and_truncated_batch_do_not_corrupt_boot_tracking()
    {
        // Verify guest::build_online_guest_stats only sets boot_state_known when sys.boot_completed
        // was actually parsed from MODEL_INFO_SCRIPT or section 3 of BATCH_STATS_SCRIPT.
        let failed = guest::build_online_guest_stats("", true, None, None);
        assert!(!failed.boot_state_known);
        let truncated = guest::build_online_guest_stats(
            "Pixel 8 | Android 15 (API 35) | Build: B | Res: 1080x2400",
            true,
            None,
            Some("MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n"),
        );
        assert!(!truncated.boot_state_known);
        let model_only = guest::build_online_guest_stats(
            "",
            true,
            Some("Pixel 8|!|15|!|35|!|BP1A|!|Physical size: 1080x2400|!|1"),
            None,
        );
        assert!(model_only.boot_state_known);
        assert!(!model_only.is_booting);

        #[derive(Default)]
        struct TransientFailGuestProvider {
            calls: Mutex<usize>,
        }
        impl provider::GuestProvider for TransientFailGuestProvider {
            async fn get_devices(&self) -> HashMap<String, String> {
                HashMap::from([("emulator-5554".to_string(), "device".to_string())])
            }
            async fn get_guest_stats(
                &self,
                _serial: &str,
                known_model: &str,
                is_booting: bool,
                _status: &str,
            ) -> GuestStats {
                let mut calls = self.calls.lock().unwrap();
                *calls += 1;
                match *calls {
                    1 => guest::build_online_guest_stats(known_model, is_booting, None, None),
                    2 => guest::build_online_guest_stats(
                        known_model,
                        is_booting,
                        None,
                        Some("MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n"),
                    ),
                    _ => scripted_guest_stats("Pixel 8", false),
                }
            }
        }

        let host = ScriptedHostProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        let mut monitor = Monitor::new(
            host.clone(),
            TransientFailGuestProvider::default(),
            scripted_args(),
            "Specs".to_string(),
        );
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.device_states["emulator-5554"].is_booting, None,
            "Failed initial adb shell must leave state.is_booting unset"
        );
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.device_states["emulator-5554"].is_booting, None,
            "Truncated batch without sys.boot_completed must leave state.is_booting unset"
        );
        monitor.tick(Instant::now()).await;
        assert!(
            monitor.device_states["emulator-5554"].boot_finished_time.is_none(),
            "Already-booted emulator started bogus 300s post-boot window after initial shell failure: {:?}",
            monitor.device_states["emulator-5554"]
        );
        assert!(
            monitor.summary.boot_logs.get("Pixel_6_emulator-5554").is_none_or(|v| v.is_empty())
        );

        // Conversely, a genuinely cold-booting emulator whose first shell fails must NOT latch
        // boot_completed = true early; once sys.boot_completed reads 0 -> 1, it tracks post-boot.
        let guest = ScriptedGuestProvider::default();
        guest.devices.lock().unwrap().insert("emulator-5554".to_string(), "device".to_string());
        guest.results.lock().unwrap().insert(
            "emulator-5554".to_string(),
            guest::build_online_guest_stats("", true, None, None),
        );
        let mut cold_mon = scripted_monitor(&host, &guest);
        cold_mon.tick(Instant::now()).await;
        assert!(!cold_mon.device_states["emulator-5554"].boot_completed);
        guest
            .results
            .lock()
            .unwrap()
            .insert("emulator-5554".to_string(), scripted_guest_stats("Pixel 6", true));
        cold_mon.tick(Instant::now()).await;
        assert_eq!(cold_mon.device_states["emulator-5554"].is_booting, Some(true));
        guest
            .results
            .lock()
            .unwrap()
            .insert("emulator-5554".to_string(), scripted_guest_stats("Pixel 6", false));
        cold_mon.tick(Instant::now()).await;
        assert!(cold_mon.device_states["emulator-5554"].boot_finished_time.is_some());
    }

    #[tokio::test]
    async fn test_bare_serial_prior_session_isolated_when_later_avd_has_initial_bare_tick() {
        let serial = "emulator-5554";
        let group_info = |pid: u32, st: u64, members: &[(u32, u64)]| netsim::EmuGroupNetsimInfo {
            serial: serial.to_string(),
            port: 5554,
            primary_pid: pid,
            primary_start_time: st,
            pids: members.iter().map(|&(p, _)| p).collect(),
            member_start_times: members.iter().copied().collect(),
            ..Default::default()
        };

        // Part 1: Host-only unnamed Emulator A (PID 100) exits without ever appearing in adb;
        // later QEMU-next Emulator B starts with 1 bare-serial launcher tick before worker Pixel_8.
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: None,
            cpu_usage: 95.0,
            ..scripted_emulator(100, 5554)
        }];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(100, 1000, &[(100, 1000)]));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        host.emulators.lock().unwrap().clear();
        host.emu_groups.lock().unwrap().clear();
        monitor.tick(Instant::now()).await;

        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: None,
            cpu_usage: 15.0,
            ..scripted_emulator(200, 5554)
        }];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(200, 2000, &[(200, 2000)]));
        monitor.tick(Instant::now()).await;

        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            cpu_usage: 40.0,
            ..scripted_emulator(201, 5554)
        }];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(201, 2001, &[(200, 2000), (201, 2001)]));
        monitor.tick(Instant::now()).await;

        assert_eq!(
            monitor.summary.time_series[0].get("qemu_cpu_emulator-5554"),
            Some(&95.0),
            "Host-only Emulator A's bare-serial row was stolen by Pixel_8"
        );
        assert!(!monitor.summary.time_series[0].contains_key("qemu_cpu_Pixel_8_emulator-5554"));
        assert_eq!(
            monitor.summary.time_series[2].get("qemu_cpu_Pixel_8_emulator-5554"),
            Some(&15.0),
            "Emulator B's own pre-AVD launcher tick should migrate to Pixel_8_emulator-5554"
        );
        assert!(monitor.summary.device_names.contains_key(serial));

        // Part 2: Evicted unnamed ADB session with guest stats & boot log must keep its rows
        // and boot log when a later named AVD starts with 1 bare-serial tick.
        let host2 = ScriptedHostProvider::default();
        let guest2 = ScriptedGuestProvider::default();
        *host2.emulators.lock().unwrap() =
            vec![EmulatorProcess { avd_name: None, ..scripted_emulator(300, 5554) }];
        guest2.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest2
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Custom AOSP", true));
        let mut mon2 = scripted_monitor(&host2, &guest2);
        mon2.tick(Instant::now()).await;
        assert_eq!(mon2.summary.boot_logs[serial].len(), 1);

        host2.emulators.lock().unwrap().clear();
        guest2.devices.lock().unwrap().clear();
        mon2.devices.clear();
        mon2.device_states.get_mut(serial).unwrap().disconnect_since =
            Some(Instant::now() - Duration::from_secs(10));
        mon2.tick(Instant::now()).await;

        // Emulator B starts with 1 bare-serial tick (already booted), then resolves AVD Pixel_9.
        *host2.emulators.lock().unwrap() =
            vec![EmulatorProcess { avd_name: None, ..scripted_emulator(400, 5554) }];
        guest2.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest2.results.lock().unwrap().insert(
            serial.to_string(),
            GuestStats { cpu_total: "22%".to_string(), ..scripted_guest_stats("Pixel 9", false) },
        );
        mon2.tick(Instant::now()).await;
        *host2.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_9".to_string()),
            ..scripted_emulator(400, 5554)
        }];
        mon2.tick(Instant::now()).await;

        assert_eq!(mon2.summary.time_series[0].get("emu_cpu_tot_emulator-5554"), Some(&50.0));
        assert_eq!(
            mon2.summary.time_series[2].get("emu_cpu_tot_Pixel_9_emulator-5554"),
            Some(&22.0)
        );
        assert_eq!(mon2.summary.boot_logs[serial].len(), 1);
        assert!(mon2.summary.boot_logs.get("Pixel_9_emulator-5554").is_none_or(|v| v.is_empty()));
        assert_eq!(
            mon2.summary.device_names.get(serial).map(String::as_str),
            Some("emulator-5554 | Custom AOSP")
        );
    }

    #[tokio::test]
    async fn test_timed_out_host_scan_does_not_mark_connecting_emulator_offline_without_host() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.interval = 0.2;

        // Tick 1: Idle baseline.
        monitor.tick(Instant::now()).await;

        // Tick 2: Cold-booting Pixel_6 launches; adb sees `offline`, while the host scan takes
        // 500ms (> 200ms deadline) so `collect_host_sample` times out with `active_host_task` live.
        *host.sample_time.lock().unwrap() = Duration::from_millis(500);
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("", true, "offline"));
        monitor.tick(Instant::now()).await;
        assert!(
            !monitor.device_states[serial].seen_offline_without_host,
            "In-flight timed-out host scan falsely set seen_offline_without_host"
        );

        // Tick 3: In-flight host scan finishes with Pixel_6 (PID 100), and adb reaches `device`.
        *host.sample_time.lock().unwrap() = Duration::ZERO;
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", true));
        settle(&monitor).await;
        monitor.tick(Instant::now()).await;

        assert!(
            !monitor.summary.time_series[1].contains_key("emu_cpu_tot_emulator-5554"),
            "Tick 2 bare-serial row should migrate to Pixel_6_emulator-5554 once host scan completes: {:?}",
            monitor.summary.time_series[1]
        );
    }

    #[tokio::test]
    async fn test_online_badge_flashes_after_unauthorized_and_clears_when_non_device_or_pid_reset()
    {
        let usb = "HT4A12345678";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        guest.devices.lock().unwrap().insert(usb.to_string(), "unauthorized".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(usb.to_string(), GuestStats::unavailable("", true, "unauthorized"));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert!(monitor.device_states[usb].online_indicator_until.is_none());

        // Transition unauthorized -> device flashes [ONLINE].
        guest.devices.lock().unwrap().insert(usb.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(usb.to_string(), scripted_guest_stats("Pixel 9", false));
        monitor.tick(Instant::now()).await;
        assert!(
            monitor.device_states[usb].online_indicator_until.is_some(),
            "unauthorized -> device transition should trigger [ONLINE] indicator"
        );

        // Dropping back to offline within the 10s window immediately clears [ONLINE].
        guest.devices.lock().unwrap().insert(usb.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(usb.to_string(), GuestStats::unavailable("Pixel 9", false, "offline"));
        monitor.tick(Instant::now()).await;
        assert!(monitor.device_states[usb].online_indicator_until.is_none());

        let mut ts = HashMap::new();
        let guest_ui = monitor.process_guest_results(&mut ts);
        let online_map: HashMap<String, Instant> = monitor
            .device_states
            .iter()
            .filter_map(|(k, v)| v.online_indicator_until.map(|t| (k.clone(), t)))
            .collect();
        let dash = ui::build_dashboard(ui::DashboardInput {
            host_stats: &monitor.stats,
            guest_results: &guest_ui,
            prev_stats: &mut HashMap::new(),
            iter_count: 3,
            term_width: 110,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_map,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        assert!(
            !dash.contains("[ONLINE]"),
            "Offline card must not display green [ONLINE] badge:\n{dash}"
        );

        // PID reset also clears online_indicator_until and last_status.
        let mut st = DeviceState {
            online_indicator_until: Some(Instant::now() + Duration::from_secs(10)),
            last_status: Some("device".to_string()),
            ..Default::default()
        };
        st.reset_for_new_pid();
        assert!(st.online_indicator_until.is_none());
        assert!(st.last_status.is_none());
    }

    #[tokio::test]
    async fn test_online_badge_flashes_on_pid_replacement_and_short_disconnect_reconnect() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();

        // 1) Stale `adb offline` entry followed by Quickboot emulator launching as `device`.
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("", true, "offline"));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;
        assert!(monitor.device_states[serial].seen_offline_without_host);

        *host.emulators.lock().unwrap() = vec![scripted_emulator(200, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 8", false));
        monitor.tick(Instant::now()).await;
        assert!(
            monitor.device_states[serial].online_indicator_until.is_some(),
            "Quickboot launch after stale offline entry should flash [ONLINE]"
        );

        // 2) Running booted emulator (PID 200) replaced directly by Quickboot emulator (PID 300).
        monitor.device_states.get_mut(serial).unwrap().online_indicator_until = None;
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_9".to_string()),
            ..scripted_emulator(300, 5554)
        }];
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 9", false));
        monitor.tick(Instant::now()).await;
        assert!(
            monitor.device_states[serial].online_indicator_until.is_some(),
            "Online -> online PID replacement should flash [ONLINE]"
        );

        // Replacement starting in `offline` must keep `online_indicator_until = None` until `device`.
        *host.emulators.lock().unwrap() = vec![scripted_emulator(400, 5554)];
        guest.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), GuestStats::unavailable("", true, "offline"));
        monitor.tick(Instant::now()).await;
        assert!(monitor.device_states[serial].online_indicator_until.is_none());

        // 3) Physical USB device disconnects (<= 5s, [DISCONNECTED]) and reconnects as `device`.
        let usb = "HT4A12345678";
        guest.devices.lock().unwrap().insert(usb.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(usb.to_string(), scripted_guest_stats("Pixel 9 Pro", false));
        monitor.tick(Instant::now()).await;
        monitor.device_states.get_mut(usb).unwrap().online_indicator_until = None;

        guest.devices.lock().unwrap().remove(usb);
        monitor.tick(Instant::now()).await;
        assert!(monitor.device_states[usb].online_indicator_until.is_none());

        guest.devices.lock().unwrap().insert(usb.to_string(), "device".to_string());
        monitor.tick(Instant::now()).await;
        assert!(
            monitor.device_states[usb].online_indicator_until.is_some(),
            "[DISCONNECTED] -> device reconnect within 5s should flash [ONLINE]"
        );
    }

    #[tokio::test]
    async fn test_eviction_and_pid_reset_preserve_new_live_emulator_bare_key_owner() {
        let serial = "emulator-5554";
        let group_info = |pid: u32, st: u64, members: &[(u32, u64)]| netsim::EmuGroupNetsimInfo {
            serial: serial.to_string(),
            port: 5554,
            primary_pid: pid,
            primary_start_time: st,
            pids: members.iter().map(|&(p, _)| p).collect(),
            member_start_times: members.iter().copied().collect(),
            ..Default::default()
        };

        // Part 1: > 5s disconnect eviction of Emulator A while pre-ADB Emulator B owns bare key.
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(100, 1000, &[(100, 1000)]));
        guest.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        host.emulators.lock().unwrap().clear();
        host.emu_groups.lock().unwrap().clear();
        guest.devices.lock().unwrap().clear();
        monitor.tick(Instant::now()).await;

        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: None,
            cpu_usage: 35.0,
            ..scripted_emulator(200, 5554)
        }];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(200, 2000, &[(200, 2000)]));
        monitor.tick(Instant::now()).await;

        monitor.device_states.get_mut(serial).unwrap().disconnect_since =
            Some(Instant::now() - Duration::from_secs(DISCONNECT_EVICT_TIMEOUT_SECS + 1));
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            cpu_usage: 60.0,
            ..scripted_emulator(201, 5554)
        }];
        host.emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(201, 2001, &[(200, 2000), (201, 2001)]));
        monitor.tick(Instant::now()).await;

        assert_eq!(
            monitor.summary.time_series[2].get("qemu_cpu_Pixel_8_emulator-5554"),
            Some(&35.0),
            "Emulator B's pre-AVD launcher tick was orphaned under bare serial on eviction: {:?}",
            monitor.summary.time_series[2]
        );
        assert!(!monitor.summary.device_names.contains_key(serial));

        // Part 2: <= 5s ADB reconnect of Emulator B after a pre-ADB bare-serial launcher tick.
        let host2 = ScriptedHostProvider::default();
        let guest2 = ScriptedGuestProvider::default();
        *host2.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host2
            .emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(100, 1000, &[(100, 1000)]));
        guest2.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest2
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 6", false));
        let mut mon2 = scripted_monitor(&host2, &guest2);
        mon2.tick(Instant::now()).await;

        host2.emulators.lock().unwrap().clear();
        host2.emu_groups.lock().unwrap().clear();
        guest2.devices.lock().unwrap().clear();
        mon2.tick(Instant::now()).await;

        *host2.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: None,
            cpu_usage: 42.0,
            ..scripted_emulator(200, 5554)
        }];
        host2
            .emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(200, 2000, &[(200, 2000)]));
        mon2.tick(Instant::now()).await;

        *host2.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            cpu_usage: 65.0,
            ..scripted_emulator(201, 5554)
        }];
        host2
            .emu_groups
            .lock()
            .unwrap()
            .insert(serial.to_string(), group_info(201, 2001, &[(200, 2000), (201, 2001)]));
        guest2.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        guest2
            .results
            .lock()
            .unwrap()
            .insert(serial.to_string(), scripted_guest_stats("Pixel 8", false));
        mon2.tick(Instant::now()).await;

        assert_eq!(
            mon2.summary.time_series[2].get("qemu_cpu_Pixel_8_emulator-5554"),
            Some(&42.0),
            "Emulator B's pre-AVD launcher tick was orphaned on <= 5s PID reset: {:?}",
            mon2.summary.time_series[2]
        );
    }

    #[tokio::test]
    async fn test_pre_adb_replacement_within_5s_evicts_dead_session_state() {
        #[derive(Clone, Default)]
        struct RetryAwareGuestProvider {
            devices: Arc<Mutex<HashMap<String, String>>>,
            live_model: Arc<Mutex<String>>,
            live_booting: Arc<Mutex<bool>>,
        }
        impl provider::GuestProvider for RetryAwareGuestProvider {
            async fn get_devices(&self) -> HashMap<String, String> {
                self.devices.lock().unwrap().clone()
            }
            async fn get_guest_stats(
                &self,
                _serial: &str,
                known_model: &str,
                is_booting: bool,
                status: &str,
            ) -> GuestStats {
                if status != "device" {
                    return GuestStats::unavailable(known_model, is_booting, status);
                }
                let model = if guest::should_retry_model(known_model, is_booting) {
                    self.live_model.lock().unwrap().clone()
                } else {
                    known_model.to_string()
                };
                GuestStats {
                    model,
                    is_booting: *self.live_booting.lock().unwrap(),
                    boot_state_known: true,
                    cpu_total: "40%".to_string(),
                    cpu_adjusted: "10.0% (over 4 cores)".to_string(),
                    ram: "45.0% (1.8GB/4.0GB)".to_string(),
                    data_space: Some("20% (2G used/8G avail)".to_string()),
                    swap: Some("0.0 MB".to_string()),
                    err_count: 0,
                    data_pct: Some(20.0),
                }
            }
        }

        let serial = "emulator-5554";
        let group_info = |pid: u32, st: u64| netsim::EmuGroupNetsimInfo {
            serial: serial.to_string(),
            port: 5554,
            primary_pid: pid,
            primary_start_time: st,
            pids: vec![pid],
            member_start_times: BTreeMap::from([(pid, st)]),
            ..Default::default()
        };

        // Pre-ADB replacement within 5s followed by a slow host scan on ADB connect must keep
        // Emulator A's [DISCONNECTED] card while offline and record Emulator B's initial sample.
        let host = ScriptedHostProvider::default();
        let guest_prov = RetryAwareGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host.emu_groups.lock().unwrap().insert(serial.to_string(), group_info(100, 1000));
        guest_prov.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest_prov.live_model.lock().unwrap() =
            "Pixel 6 | Android 15 (API 35) | Build: AP4A | Res: 1080x2400".to_string();
        *guest_prov.live_booting.lock().unwrap() = false;

        let mut monitor =
            Monitor::new(host.clone(), guest_prov.clone(), scripted_args(), "Specs".to_string());
        monitor.interval = 0.2;
        monitor.tick(Instant::now()).await;

        guest_prov.devices.lock().unwrap().clear();
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            ..scripted_emulator(200, 5554)
        }];
        host.emu_groups.lock().unwrap().insert(serial.to_string(), group_info(200, 2000));
        monitor.tick(Instant::now()).await;
        let mut disc_ts = HashMap::new();
        let disc_cards = monitor.process_guest_results(&mut disc_ts);
        assert!(
            disc_cards.get(serial).is_some_and(|c| {
                c.is_disconnected
                    && c.avd_name.as_deref() == Some("Pixel_6")
                    && c.model.contains("Pixel 6")
            }),
            "Evicted pre-ADB replacement lost prior [DISCONNECTED] card: {disc_cards:?}"
        );

        guest_prov.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest_prov.live_model.lock().unwrap() =
            "Pixel 8 | Android 16 (API 36) | Build: BP2A | Res: 1080x2400".to_string();
        *guest_prov.live_booting.lock().unwrap() = true;
        *host.sample_time.lock().unwrap() = Duration::from_millis(350);
        monitor.tick(Instant::now()).await;
        if let Some(task) = monitor.active_host_task.take() {
            let _ = task.await;
        }

        assert_eq!(
            monitor.summary.time_series[2].get("emu_cpu_tot_Pixel_8_emulator-5554"),
            Some(&40.0),
            "Pre-ADB replacement within 5s dropped Tick 3 guest sample when host scan was slow: {:?}",
            monitor.summary.time_series[2]
        );
        assert_eq!(
            monitor.summary.device_names.get("Pixel_8_emulator-5554").map(String::as_str),
            Some("Pixel_8 | Pixel 8 | Android 16 (API 36) | Build: BP2A | Res: 1080x2400")
        );
        assert!(
            monitor
                .summary
                .boot_logs
                .get("Pixel_8_emulator-5554")
                .is_some_and(|logs| !logs.is_empty() && logs[0].0 == "+0s"),
            "Pre-ADB replacement within 5s missed +0s boot log: {:?}",
            monitor.summary.boot_logs
        );

        // Crashed pre-ADB replacement leaving `offline` in ADB must not reclaim dead Pixel_6,
        // and fresh host exit during <= 5s ADB absence must record host_process_exited.
        let host_b = ScriptedHostProvider::default();
        let guest_b = RetryAwareGuestProvider::default();
        *host_b.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host_b.emu_groups.lock().unwrap().insert(serial.to_string(), group_info(100, 1000));
        guest_b.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest_b.live_model.lock().unwrap() =
            "Pixel 6 | Android 15 (API 35) | Build: AP4A | Res: 1080x2400".to_string();
        *guest_b.live_booting.lock().unwrap() = true;
        let mut mon_b =
            Monitor::new(host_b.clone(), guest_b.clone(), scripted_args(), "Specs".to_string());
        mon_b.tick(Instant::now()).await;

        guest_b.devices.lock().unwrap().clear();
        host_b.emulators.lock().unwrap().clear();
        host_b.emu_groups.lock().unwrap().clear();
        mon_b.tick(Instant::now()).await;
        assert!(
            mon_b.device_states[serial].host_process_exited,
            "Fresh host exit during <= 5s ADB absence must mark host_process_exited"
        );

        *host_b.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            ..scripted_emulator(200, 5554)
        }];
        host_b.emu_groups.lock().unwrap().insert(serial.to_string(), group_info(200, 2000));
        mon_b.tick(Instant::now()).await;

        host_b.emulators.lock().unwrap().clear();
        host_b.emu_groups.lock().unwrap().clear();
        guest_b.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        mon_b.tick(Instant::now()).await;

        let mut ts = HashMap::new();
        let ui_cards = mon_b.process_guest_results(&mut ts);
        assert_eq!(
            ui_cards[serial].avd_name, None,
            "Crashed replacement's stale offline entry was misattributed to dead Pixel_6: {:?}",
            ui_cards[serial]
        );
        assert!(
            !ui_cards[serial].model.contains("Pixel 6"),
            "Crashed replacement's stale offline entry inherited Pixel 6 model: {:?}",
            ui_cards[serial]
        );
    }

    #[tokio::test]
    async fn test_host_exit_reconnect_and_offline_to_device_isolate_dead_session_and_keep_disconnected_card()
     {
        #[derive(Clone, Default)]
        struct RetryAwareGuestProvider {
            devices: Arc<Mutex<HashMap<String, String>>>,
            live_model: Arc<Mutex<String>>,
            live_booting: Arc<Mutex<bool>>,
            live_cpu: Arc<Mutex<String>>,
        }
        impl provider::GuestProvider for RetryAwareGuestProvider {
            async fn get_devices(&self) -> HashMap<String, String> {
                self.devices.lock().unwrap().clone()
            }
            async fn get_guest_stats(
                &self,
                _serial: &str,
                known_model: &str,
                is_booting: bool,
                status: &str,
            ) -> GuestStats {
                if status != "device" {
                    return GuestStats::unavailable("", is_booting, status);
                }
                let model = if guest::should_retry_model(known_model, is_booting) {
                    self.live_model.lock().unwrap().clone()
                } else {
                    known_model.to_string()
                };
                GuestStats {
                    model,
                    is_booting: *self.live_booting.lock().unwrap(),
                    boot_state_known: true,
                    cpu_total: self.live_cpu.lock().unwrap().clone(),
                    cpu_adjusted: "10.0% (over 4 cores)".to_string(),
                    ram: "45.0% (1.8GB/4.0GB)".to_string(),
                    data_space: Some("20% (2G used/8G avail)".to_string()),
                    swap: Some("0.0 MB".to_string()),
                    err_count: 0,
                    data_pct: Some(20.0),
                }
            }
        }

        let serial = "emulator-5554";
        let group_info = |pid: u32, st: u64| netsim::EmuGroupNetsimInfo {
            serial: serial.to_string(),
            port: 5554,
            primary_pid: pid,
            primary_start_time: st,
            pids: vec![pid],
            member_start_times: BTreeMap::from([(pid, st)]),
            ..Default::default()
        };

        // Host exit + ADB removal followed within <= 5s by ADB reconnect during an in-flight
        // host scan must keep Pixel_6's [DISCONNECTED] card after an Unknown(offline) tick
        // while isolating Pixel_8's initial sample from dead Pixel_6.
        let host = ScriptedHostProvider::default();
        let guest_prov = RetryAwareGuestProvider::default();
        *host.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host.emu_groups.lock().unwrap().insert(serial.to_string(), group_info(100, 1000));
        guest_prov.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest_prov.live_model.lock().unwrap() =
            "Pixel 6 | Android 15 (API 35) | Build: AP4A | Res: 1080x2400".to_string();
        *guest_prov.live_booting.lock().unwrap() = false;
        *guest_prov.live_cpu.lock().unwrap() = "20%".to_string();

        let mut monitor =
            Monitor::new(host.clone(), guest_prov.clone(), scripted_args(), "Specs".to_string());
        monitor.interval = 0.2;
        monitor.tick(Instant::now()).await;

        host.emulators.lock().unwrap().clear();
        host.emu_groups.lock().unwrap().clear();
        guest_prov.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        monitor.tick(Instant::now()).await;

        guest_prov.devices.lock().unwrap().clear();
        monitor.tick(Instant::now()).await;
        let mut disc_ts = HashMap::new();
        let disc_cards = monitor.process_guest_results(&mut disc_ts);
        assert!(
            disc_cards.get(serial).is_some_and(|c| {
                c.is_disconnected
                    && c.avd_name.as_deref() == Some("Pixel_6")
                    && c.model.contains("Pixel 6")
            }),
            "Evicted session after Unknown(offline) tick lost [DISCONNECTED] card: {disc_cards:?}"
        );

        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            ..scripted_emulator(200, 5554)
        }];
        host.emu_groups.lock().unwrap().insert(serial.to_string(), group_info(200, 2000));
        *host.sample_time.lock().unwrap() = Duration::from_millis(350);
        guest_prov.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest_prov.live_model.lock().unwrap() =
            "Pixel 8 | Android 16 (API 36) | Build: BP2A | Res: 1080x2400".to_string();
        *guest_prov.live_booting.lock().unwrap() = true;
        *guest_prov.live_cpu.lock().unwrap() = "85%".to_string();
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.time_series[3].get("emu_cpu_tot_Pixel_6_emulator-5554"),
            None,
            "Dead Pixel_6 absorbed replacement Pixel_8's 85% CPU sample on slow host scan: {:?}",
            monitor.summary.time_series[3]
        );

        *host.sample_time.lock().unwrap() = Duration::ZERO;
        while monitor.active_host_task.as_ref().is_some_and(|t| !t.is_finished()) {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.time_series[3].get("emu_cpu_tot_Pixel_8_emulator-5554"),
            Some(&85.0),
            "Pixel_8's initial sample during slow host scan failed to migrate: {:?}",
            monitor.summary.time_series[3]
        );
        assert_eq!(
            monitor.summary.device_names.get("Pixel_8_emulator-5554").map(String::as_str),
            Some("Pixel_8 | Pixel 8 | Android 16 (API 36) | Build: BP2A | Res: 1080x2400")
        );

        // 'device' -> 'offline' (host exited) -> 'device' for a forwarded/remote target
        // (no local host PID) must also isolate the new session and flash [ONLINE].
        let host_b = ScriptedHostProvider::default();
        let guest_b = RetryAwareGuestProvider::default();
        *host_b.emulators.lock().unwrap() = vec![scripted_emulator(100, 5554)];
        host_b.emu_groups.lock().unwrap().insert(serial.to_string(), group_info(100, 1000));
        guest_b.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest_b.live_model.lock().unwrap() =
            "Pixel 6 | Android 15 (API 35) | Build: AP4A | Res: 1080x2400".to_string();
        *guest_b.live_booting.lock().unwrap() = false;
        *guest_b.live_cpu.lock().unwrap() = "20%".to_string();

        let mut mon_b =
            Monitor::new(host_b.clone(), guest_b.clone(), scripted_args(), "Specs".to_string());
        mon_b.tick(Instant::now()).await;
        mon_b.device_states.get_mut(serial).unwrap().online_indicator_until = None;

        host_b.emulators.lock().unwrap().clear();
        host_b.emu_groups.lock().unwrap().clear();
        guest_b.devices.lock().unwrap().insert(serial.to_string(), "offline".to_string());
        mon_b.tick(Instant::now()).await;

        guest_b.devices.lock().unwrap().insert(serial.to_string(), "device".to_string());
        *guest_b.live_model.lock().unwrap() =
            "Remote Pixel 9 | Android 16 (API 36) | Build: BP2A | Res: 1080x2400".to_string();
        *guest_b.live_cpu.lock().unwrap() = "65%".to_string();
        mon_b.tick(Instant::now()).await;

        assert!(mon_b.device_states[serial].online_indicator_until.is_some());
        assert_eq!(
            mon_b.summary.time_series[2].get("emu_cpu_tot_Pixel_6_emulator-5554"),
            None,
            "Dead Pixel_6 absorbed forwarded session after 'device'->'offline'->'device': {:?}",
            mon_b.summary.time_series[2]
        );
        assert_eq!(mon_b.summary.time_series[2].get("emu_cpu_tot_emulator-5554"), Some(&65.0));
        assert_eq!(
            mon_b.summary.device_names.get(serial).map(String::as_str),
            Some(
                "emulator-5554 | Remote Pixel 9 | Android 16 (API 36) | Build: BP2A | Res: 1080x2400"
            )
        );
    }

    #[tokio::test]
    async fn test_host_only_bare_to_avd_migration_updates_summary_device_name() {
        let serial = "emulator-5554";
        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();

        // QEMU-next launcher starts unnamed on Tick 1 (`start_ts_idx == 0`), then worker
        // resolves `avd_name = Some("Pixel_8")` on Tick 2 before ADB lists the serial.
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: None,
            cpu_usage: 25.0,
            ..scripted_emulator(100, 5554)
        }];
        host.emu_groups.lock().unwrap().insert(
            serial.to_string(),
            netsim::EmuGroupNetsimInfo {
                serial: serial.to_string(),
                port: 5554,
                primary_pid: 100,
                primary_start_time: 1000,
                pids: vec![100],
                member_start_times: BTreeMap::from([(100, 1000)]),
                ..Default::default()
            },
        );
        let mut monitor = scripted_monitor(&host, &guest);
        monitor.tick(Instant::now()).await;

        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Pixel_8".to_string()),
            cpu_usage: 60.0,
            ..scripted_emulator(101, 5554)
        }];
        host.emu_groups.lock().unwrap().insert(
            serial.to_string(),
            netsim::EmuGroupNetsimInfo {
                serial: serial.to_string(),
                port: 5554,
                primary_pid: 101,
                primary_start_time: 1001,
                pids: vec![100, 100 + 1],
                member_start_times: BTreeMap::from([(100, 1000), (101, 1001)]),
                avd_id: Some("Pixel_8".to_string()),
                ..Default::default()
            },
        );
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.device_names.get("Pixel_8_emulator-5554").map(String::as_str),
            Some("Pixel_8"),
            "Host-only bare->AVD migration left device_names stuck on bare serial: {:?}",
            monitor.summary.device_names
        );

        // Bare-serial session with resolved ADB model that disconnects from ADB before its
        // AVD name resolves must rewrite its `<serial> | <model>` prefix to `<avd> | <model>`.
        let serial2 = "emulator-5556";
        *host.emulators.lock().unwrap() =
            vec![EmulatorProcess { avd_name: None, ..scripted_emulator(300, 5556) }];
        host.emu_groups.lock().unwrap().insert(
            serial2.to_string(),
            netsim::EmuGroupNetsimInfo {
                serial: serial2.to_string(),
                port: 5556,
                primary_pid: 300,
                primary_start_time: 3000,
                pids: vec![300],
                member_start_times: BTreeMap::from([(300, 3000)]),
                ..Default::default()
            },
        );
        guest.devices.lock().unwrap().insert(serial2.to_string(), "device".to_string());
        guest
            .results
            .lock()
            .unwrap()
            .insert(serial2.to_string(), scripted_guest_stats("sdk_gphone64", false));
        monitor.tick(Instant::now()).await;

        guest.devices.lock().unwrap().clear();
        *host.emulators.lock().unwrap() = vec![EmulatorProcess {
            avd_name: Some("Medium_Phone".to_string()),
            ..scripted_emulator(300, 5556)
        }];
        monitor.tick(Instant::now()).await;
        assert_eq!(
            monitor.summary.device_names.get("Medium_Phone_emulator-5556").map(String::as_str),
            Some("Medium_Phone | sdk_gphone64")
        );
    }

    #[tokio::test]
    async fn test_had_prior_emulator_and_reset_replaced_session_helper() {
        let mut s = DeviceState::default();
        assert!(!s.had_prior_emulator());
        s.seen_offline_without_host = true;
        assert!(s.had_prior_emulator());
        s.seen_offline_without_host = false;
        s.host_process_exited = true;
        assert!(s.had_prior_emulator());
        s.host_process_exited = false;
        s.last_known_pid = Some(100);
        assert!(s.had_prior_emulator());

        let host = ScriptedHostProvider::default();
        let guest = ScriptedGuestProvider::default();
        let mut monitor = scripted_monitor(&host, &guest);
        let serial = "emulator-5554";
        let prior = DeviceState {
            last_known_pid: Some(100),
            cached_model: Some("Pixel 6".to_string()),
            ..Default::default()
        };
        monitor.device_states.insert(serial.to_string(), prior);
        monitor.prev_stats.insert(serial.to_string(), 42.0);
        monitor.prev_stats.insert(format!("{serial}_real"), 10.5);
        let (stale_alive, stale_gone) = tokio::sync::oneshot::channel::<()>();
        monitor.active_guest_tasks.insert(
            serial.to_string(),
            tokio::spawn(async move {
                let _alive = stale_alive;
                tokio::time::sleep(Duration::from_secs(60)).await;
                scripted_guest_stats("Stale", false)
            }),
        );

        let new_procs = BTreeMap::from([(200, 2000)]);
        monitor.reset_replaced_session(serial, "device", Some(&new_procs));
        let reset_state = &monitor.device_states[serial];
        assert!(reset_state.cached_model.is_none());
        assert_eq!(reset_state.last_status.as_deref(), Some("device"));
        assert!(reset_state.online_indicator_until.is_some());
        assert!(!monitor.prev_stats.contains_key(serial));
        assert!(!monitor.prev_stats.contains_key(&format!("{serial}_real")));
        assert!(!monitor.active_guest_tasks.contains_key(serial));
        // The stale query is cancelled, not left running: aborting it drops its future.
        let stale = tokio::time::timeout(Duration::from_secs(30), stale_gone).await;
        assert!(stale.is_ok_and(|alive| alive.is_err()), "stale guest query was not aborted");

        // Non-"device" status resets without arming the online flash indicator.
        monitor.reset_replaced_session(serial, "offline", Some(&new_procs));
        assert_eq!(monitor.device_states[serial].last_status.as_deref(), Some("offline"));
        assert!(monitor.device_states[serial].online_indicator_until.is_none());
    }
}
