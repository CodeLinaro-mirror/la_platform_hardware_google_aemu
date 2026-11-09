use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::PathBuf;

use super::NetsimLogTailer;

/// Identifies a `netsimd` daemon instance by `(pid, start_time)`.
pub type DaemonKey = (u32, u64);

/// Identifies a chip capture enabled by the monitor by `(daemon_key, chip_id)`.
pub type EnabledChipKey = (DaemonKey, u32);

/// Maps normalized chip kind to `(attach_latency_s, is_approximate)`.
pub type ResolvedChipMap = BTreeMap<String, (f64, bool)>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureRow {
    pub id: u32,
    pub device_name: String,
    pub chip_kind: String,
    pub raw_chip_kind: String,
    pub state: String,
    pub timestamp: String,
    pub records: u64,
    pub size_bytes: u64,
}

#[derive(Debug, Clone)]
pub struct ChipAddEvent {
    pub ts: f64,
    pub chip_id: Option<String>,
    pub kind: String,
    pub device_name: String,
    pub peer_port: Option<u16>,
}

#[derive(Debug, Clone, Default)]
pub struct NetsimDaemonProc {
    pub pid: u32,
    pub parent: Option<u32>,
    pub parent_name: Option<String>,
    pub start_time: u64,
    pub exe: Option<PathBuf>,
    pub cmd: Vec<String>,
    pub environ: HashMap<String, String>,
    pub cpu: f32,
    pub ram_mb: f64,
    /// Real UID of the daemon process on Unix (`None` on Windows or synthetic fixtures).
    pub uid: Option<u32>,
    /// The daemon runs as the monitor's user (see `host::is_same_user`).
    /// Only such daemons may nominate a sibling `netsim` CLI or have their
    /// artifact directory harvested.
    pub same_user: bool,
}

#[derive(Debug, Clone, Default)]
pub struct EmuGroupNetsimInfo {
    pub serial: String,
    pub port: u16,
    pub primary_pid: u32,
    pub primary_start_time: u64,
    pub pids: Vec<u32>,
    pub member_start_times: BTreeMap<u32, u64>,
    pub avd_id: Option<String>,
    pub display_name: Option<String>,
    pub grpc_endpoint: Option<String>,
    pub no_netsim: bool,
}

#[derive(Debug, Clone, Default)]
pub struct NetsimDashboardSnapshot {
    pub daemon_rows: Vec<(String, String)>,
    pub daemon_status_row: Option<String>,
    pub emulator_rows: HashMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct StatsCache {
    pub mtime: f64,
    pub parsed: Value,
}

pub fn normalize_chip_kind(kind_str: &str) -> String {
    let k = kind_str.trim().to_uppercase();
    if k.is_empty() {
        return "?".to_string();
    }
    match k.as_str() {
        "BLUETOOTH_LOW_ENERGY" | "BLUETOOTH_CLASSIC" | "BLUETOOTH" | "BLE" => "BT".to_string(),
        _ => k,
    }
}

#[derive(Debug, Clone)]
pub struct DaemonRecord {
    pub daemon_key: (u32, u64),
    pub pid: u32,
    pub create_time: u64,
    pub start_wall: f64,
    pub spawner: String,
    pub instance_num: Option<u32>,
    pub artifact_dir: PathBuf,
    pub log_path: PathBuf,
    pub tailer: Option<NetsimLogTailer>,
    pub netsim_cli_bin: String,
    /// Copied from `NetsimDaemonProc::same_user`.
    pub same_user: bool,
    /// Expected Unix owner UID for this daemon's artifacts, logs, stats, and INIs.
    pub expected_uid: Option<u32>,
    pub version: Option<String>,
    pub ini_path: Option<PathBuf>,
    pub grpc_port: Option<u16>,
    pub ready_ts: Option<f64>,
    pub ready_recorded: bool,
    pub netsim_daemon_ready_s: Option<f64>,
    pub frontend_disabled: bool,
    pub pcap_flag: bool,
    pub log_available: bool,
    pub log_unavail_recorded: bool,
    pub client_mode_recorded: bool,
    pub exited: bool,
    pub exit_reason_hint: Option<String>,
    pub exit_reason: Option<String>,
    pub orphaned: bool,
    pub zero_devices_since_mono: Option<f64>,
    pub stats_cache: Option<StatsCache>,
    pub pcap_live_bytes: u64,
    pub pcap_live_files: usize,
    pub active_socket_conns: usize,
    pub joined_emulator_count: usize,
}

#[derive(Debug, Clone, Default)]
pub struct EmulatorNetsimRecord {
    pub storage_key: String,
    pub serial: String,
    pub avd_id: Option<String>,
    pub device_name: Option<String>,
    pub worker_pid: u32,
    pub worker_create_time: u64,
    pub group_pids: Vec<u32>,
    pub group_procs: BTreeMap<u32, u64>,
    pub worker_start_ts: f64,
    pub first_seen_mono: f64,
    pub no_netsim: bool,
    pub state: String,
    pub ambiguous: bool,
    pub daemon_key: Option<(u32, u64)>,
    pub netsim_daemon_ready_s: Option<f64>,
    pub first_transport_ts: Option<f64>,
    pub netsim_connect_s: Option<f64>,
    pub chip_attach_s: BTreeMap<String, (f64, bool)>,
    pub chip_ids: BTreeSet<String>,
    pub unknown_chip_count: usize,
    pub wifi_associated_s: Option<f64>,
    pub disconnect_count: u32,
    pub connected_start_mono: Option<f64>,
    pub connected_total_s: f64,
    pub adb_online_since_mono: Option<f64>,
    /// First tick of the current run of host-scan misses.
    pub missing_since_mono: Option<f64>,
    /// Ticks of that run on which adb did not list the serial either.
    pub missed_ticks: u32,
    /// Earliest daemon log timestamp accepted after a same-AVD worker restart.
    pub min_log_ts: Option<f64>,
    /// Settled snapshot of an earlier unnamed bare-serial run on this port,
    /// restored if a replacement worker on the bare key later resolves an AVD name.
    pub prior_bare_snapshot: Option<Box<EmulatorNetsimRecord>>,
}

impl EmulatorNetsimRecord {
    pub fn is_settled(&self) -> bool {
        self.state == "DISCONNECTED" || self.state.starts_with("FAILED")
    }

    /// Whether the session has finished (settled or an exited `-no-netsim` run).
    pub fn is_inactive(&self) -> bool {
        self.is_settled()
            || (self.no_netsim && self.missed_ticks >= super::SETTLE_AFTER_MISSED_TICKS)
    }

    /// Returns a settled snapshot of `self` before a new worker replaces it on
    /// a bare-serial key (`storage_key == serial`).
    pub fn settled_bare_snapshot(&self, prev_exit: Option<&str>, now_mono: f64) -> Self {
        let mut snap = self.clone();
        snap.prior_bare_snapshot = None;
        snap.adb_online_since_mono = None;
        let end = snap.missing_since_mono.unwrap_or(now_mono);
        if snap.no_netsim {
            snap.missed_ticks = super::SETTLE_AFTER_MISSED_TICKS;
        } else if !snap.is_settled() {
            if snap.state == "CONNECTED" || snap.state == "CONNECTED_NO_CHIPS" {
                snap.state = "DISCONNECTED".to_string();
                snap.disconnect_count += 1;
                if let Some(st) = snap.connected_start_mono.take() {
                    snap.connected_total_s += (end - st).max(0.0);
                }
            } else if prev_exit == Some("startup_timeout") {
                snap.state = "FAILED(launcher_abort)".to_string();
            } else {
                snap.state = "FAILED(worker_exit_no_connect)".to_string();
            }
        }
        snap
    }

    /// Carries cumulative connection totals from an earlier run on the same
    /// key into a newly migrated bare-serial record and sets `min_log_ts` so
    /// pre-restart daemon log entries are ignored.
    pub fn merge_prior_run(&mut self, mut prev: EmulatorNetsimRecord, now_mono: f64) {
        if prev.state == "CONNECTED" || prev.state == "CONNECTED_NO_CHIPS" {
            prev.disconnect_count += 1;
            // A held run stopped counting at its first missed tick.
            let end = prev.missing_since_mono.unwrap_or(now_mono);
            if let Some(st) = prev.connected_start_mono.take() {
                prev.connected_total_s += (end - st).max(0.0);
            }
        }
        self.disconnect_count += prev.disconnect_count;
        self.connected_total_s += prev.connected_total_s;
        self.min_log_ts = Some(self.worker_start_ts);
    }

    /// Resets per-run connection and chip state when a new worker PID replaces
    /// a previous worker on the same session key, preserving cumulative
    /// `disconnect_count` and `connected_total_s`.
    pub fn reset_for_new_worker(
        &mut self,
        worker_pid: u32,
        worker_create_time: u64,
        worker_start_ts: f64,
        _first_seen_mono: f64,
        no_netsim: bool,
        now_mono: f64,
    ) {
        if self.state == "CONNECTED" || self.state == "CONNECTED_NO_CHIPS" {
            self.disconnect_count += 1;
            let end = self.missing_since_mono.unwrap_or(now_mono);
            if let Some(st) = self.connected_start_mono.take() {
                self.connected_total_s += (end - st).max(0.0);
            }
        }
        self.avd_id = None;
        self.device_name = None;
        self.worker_pid = worker_pid;
        self.worker_create_time = worker_create_time;
        self.group_pids.clear();
        self.group_procs.clear();
        self.worker_start_ts = worker_start_ts;
        self.first_seen_mono = now_mono;
        self.no_netsim = no_netsim;
        self.state = "CONNECTING".to_string();
        self.ambiguous = false;
        self.daemon_key = None;
        self.netsim_daemon_ready_s = None;
        self.first_transport_ts = None;
        self.netsim_connect_s = None;
        self.chip_attach_s.clear();
        self.chip_ids.clear();
        self.unknown_chip_count = 0;
        self.wifi_associated_s = None;
        self.connected_start_mono = None;
        self.adb_online_since_mono = None;
        self.missing_since_mono = None;
        self.missed_ticks = 0;
        self.min_log_ts = Some(worker_start_ts);
        self.prior_bare_snapshot = None;
    }
}
