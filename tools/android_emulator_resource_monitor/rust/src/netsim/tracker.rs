use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use super::*;

/// Clock slack when comparing an INI's mtime with its writer's start time.
const INI_MTIME_SLACK_S: f64 = 2.0;

/// 1s slack for `/proc` stat `starttime` tick rounding vs. millisecond log timestamps.
pub(super) const LOG_FLOOR_SLACK_S: f64 = 1.0;

/// Ticks an emulator must be missing from both the host scan and adb before
/// it counts as exited, so a one-tick miss is not reported as a disconnect.
pub(super) const SETTLE_AFTER_MISSED_TICKS: u32 = 2;

/// Seconds of continuous adb online status without attached chips before an
/// emulator is classified as `CONNECTED_NO_CHIPS`.
const NO_CHIPS_ADB_ONLINE_THRESHOLD_S: f64 = 120.0;

/// Seconds a live emulator may stay in `CONNECTING` without transport or chips
/// before transitioning to `STALLED`.
const CONNECT_STALL_TIMEOUT_S: f64 = 50.0;

/// Seconds a running daemon with zero joined or created devices waits before
/// being marked `ORPHANED`.
const DAEMON_ORPHAN_TIMEOUT_S: f64 = 30.0;

/// Resolved session key and host-scan identity for an emulator active on a tick.
struct LiveEmulatorEntry {
    storage_key: String,
    serial: String,
    fallback_pid: u32,
    first_seen_mono: f64,
}

/// Per-tick context shared across live emulator updates.
struct LiveEmulatorTickCtx<'a> {
    emu_groups: &'a HashMap<String, EmuGroupNetsimInfo>,
    port_to_conns: &'a HashMap<u16, Vec<LoopbackGrpcConn>>,
    display_name_counts: HashMap<String, usize>,
    other_session_names: HashSet<String>,
    single_live: bool,
    now_wall: f64,
    now_mono: f64,
}

/// Whether `a` and `b` name the same file (canonical paths when both resolve).
fn same_file(a: &Path, b: &Path) -> bool {
    a == b
        || matches!(
            (std::fs::canonicalize(a), std::fs::canonicalize(b)),
            (Ok(x), Ok(y)) if x == y
        )
}

/// Whether a non-zombie process with this PID exists, on any host.
fn pid_exists(pid: u32) -> bool {
    use sysinfo::{Pid, ProcessRefreshKind, ProcessStatus, ProcessesToUpdate, System};
    let pid = Pid::from_u32(pid);
    let mut sys = System::new();
    sys.refresh_processes_specifics(
        ProcessesToUpdate::Some(&[pid]),
        true,
        ProcessRefreshKind::nothing(),
    );
    sys.process(pid)
        .is_some_and(|p| !matches!(p.status(), ProcessStatus::Zombie | ProcessStatus::Dead))
}

/// A discovered netsim INI plus the evidence used to pair it with a daemon.
struct IniObservation {
    path: PathBuf,
    data: HashMap<String, String>,
    /// Holders of `<ini>.lock`, then of `<ini>.init.lock` (Linux `/proc/locks`).
    holders: Vec<u32>,
    pid: Option<u32>,
    port: Option<u16>,
    instance: Option<u32>,
    mtime: Option<f64>,
    file_uid: Option<u32>,
    owned_by_self: Option<bool>,
}

impl IniObservation {
    fn read(path: PathBuf, lock_holders: &HashMap<String, u32>) -> Self {
        let (data, meta) = read_netsim_ini(&path);
        // An INI that cannot be opened contributes no keys, but its owner and mtime still
        // count as evidence.
        let meta = meta
            .or_else(|| std::fs::symlink_metadata(&path).ok().filter(|m| m.file_type().is_file()));
        let holders = if lock_holders.is_empty() {
            Vec::new()
        } else {
            [".lock", ".init.lock"]
                .into_iter()
                .filter_map(|suffix| {
                    let mut lock_path = path.clone().into_os_string();
                    lock_path.push(suffix);
                    get_file_lock_key(Path::new(&lock_path))
                        .and_then(|k| lock_holders.get(&k).copied())
                })
                .collect()
        };
        let pid = data.get("pid").and_then(|s| s.parse::<u32>().ok()).filter(|&p| p > 0);
        let port = data.get("grpc.port").and_then(|s| s.parse::<u16>().ok()).filter(|&p| p > 0);
        let mtime = meta
            .as_ref()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64());
        #[cfg(unix)]
        let file_uid = meta.as_ref().map(|m| {
            use std::os::unix::fs::MetadataExt;
            m.uid()
        });
        #[cfg(not(unix))]
        let file_uid: Option<u32> = None;
        let owned_by_self = file_uid.and_then(|fuid| super::owner_uid().map(|uid| fuid == uid));
        let instance = extract_ini_instance_num(&path);
        Self { path, data, holders, pid, port, instance, mtime, file_uid, owned_by_self }
    }
}

pub struct NetsimTracker {
    pub enable_log: bool,
    pub pcap_mgr: NetsimPcapManager,
    pub daemons: BTreeMap<(u32, u64), DaemonRecord>,
    pub stale_inis: BTreeMap<PathBuf, HashMap<String, String>>,
    pub timeline: Vec<(f64, String)>,
    pub emulator_records: BTreeMap<String, EmulatorNetsimRecord>,
    pub restart_count: u32,
    pub last_active_by_ini: HashMap<PathBuf, (u32, u64)>,
    pub custom_ini_dirs: Vec<PathBuf>,
    pub custom_proc_locks: Option<String>,
    /// Liveness probe for the `pid=` writer of an unmatched INI; tests
    /// replace it.
    pub pid_alive: fn(u32) -> bool,
    pub host_os: HostOs,
    pub socket_cli_runner: fn(&str, &[&str]) -> Option<String>,
    pub last_socket_conns: HashMap<u16, Vec<LoopbackGrpcConn>>,
    provisional_daemons: HashMap<String, DaemonKey>,
    cleaned_up: bool,
}

impl Drop for NetsimTracker {
    /// Restores capture state and harvests files when the run ends without an
    /// explicit `cleanup()`, e.g. when a panic unwinds out of the main loop.
    fn drop(&mut self) {
        self.cleanup();
    }
}

impl NetsimTracker {
    pub fn new(pcap_dir: Option<&str>, pcap_max_mb: u64, enable_log: bool, out_dir: &Path) -> Self {
        Self {
            enable_log,
            pcap_mgr: NetsimPcapManager::new(pcap_dir, pcap_max_mb, out_dir),
            daemons: BTreeMap::new(),
            stale_inis: BTreeMap::new(),
            timeline: Vec::new(),
            emulator_records: BTreeMap::new(),
            restart_count: 0,
            last_active_by_ini: HashMap::new(),
            custom_ini_dirs: Vec::new(),
            custom_proc_locks: None,
            pid_alive: pid_exists,
            host_os: HostOs::current(),
            socket_cli_runner: default_socket_cli_runner,
            last_socket_conns: HashMap::new(),
            provisional_daemons: HashMap::new(),
            cleaned_up: false,
        }
    }

    fn group_pid_ctime(g_info: Option<&EmuGroupNetsimInfo>, pid: u32) -> u64 {
        g_info
            .and_then(|g| {
                g.member_start_times.get(&pid).copied().filter(|&t| t > 0).or_else(|| {
                    (g.primary_pid == pid && g.primary_start_time > 0)
                        .then_some(g.primary_start_time)
                })
            })
            .unwrap_or(0)
    }

    fn group_contains_worker(
        g_info: Option<&EmuGroupNetsimInfo>,
        rec: &EmulatorNetsimRecord,
    ) -> bool {
        rec.worker_pid > 0
            && g_info.is_some_and(|g| {
                (g.primary_pid == rec.worker_pid || g.pids.contains(&rec.worker_pid)) && {
                    let ct = Self::group_pid_ctime(Some(g), rec.worker_pid);
                    rec.worker_create_time == 0 || ct == 0 || rec.worker_create_time == ct
                }
            })
    }

    fn record_has_member(rec: &EmulatorNetsimRecord, pid: u32, pid_ctime: u64) -> bool {
        let Some(&member_ct) = rec.group_procs.get(&pid).filter(|_| pid > 0) else {
            return false;
        };
        if member_ct > 0 && pid_ctime > 0 {
            member_ct == pid_ctime
        } else if pid_ctime > 0 && rec.worker_create_time > 0 {
            pid_ctime <= rec.worker_create_time.saturating_add(LOG_FLOOR_SLACK_S as u64)
        } else {
            !rec.is_settled()
        }
    }

    fn collect_sockets(
        &mut self,
        target_ports: &HashSet<u16>,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        daemon_pids: &HashSet<u32>,
        use_cache_on_err: bool,
    ) -> Option<HashMap<u16, Vec<LoopbackGrpcConn>>> {
        if target_ports.is_empty() {
            self.last_socket_conns.clear();
            return Some(HashMap::new());
        }
        let pid_to_serial = emulator_pid_to_serial(emu_groups, daemon_pids);
        if pid_to_serial.is_empty() {
            let empty = empty_port_map(target_ports);
            self.last_socket_conns = empty.clone();
            return Some(empty);
        }
        let raw = match self.host_os {
            HostOs::Linux => {
                Some(collect_loopback_grpc_sockets_from_proc(target_ports, emu_groups, daemon_pids))
            }
            os => collect_loopback_grpc_sockets_via_cli(
                os,
                target_ports,
                emu_groups,
                daemon_pids,
                &self.socket_cli_runner,
            ),
        };
        if let Some(conns) = raw {
            self.last_socket_conns = conns.clone();
            return Some(conns);
        }
        if !use_cache_on_err {
            return None;
        }
        // Retain cached sockets only for still-live PIDs across transient lsof/netstat
        // timeouts; full eviction requires an explicit empty Some(...) CLI result.
        let mut cached = empty_port_map(target_ports);
        for &p in target_ports {
            if let Some(prev) = self.last_socket_conns.get(&p) {
                let live: Vec<LoopbackGrpcConn> = prev
                    .iter()
                    .filter(|c| pid_to_serial.get(&c.pid) == Some(&c.serial))
                    .cloned()
                    .collect();
                cached.insert(p, live);
            }
        }
        Some(cached)
    }

    fn record_event(&mut self, ts: f64, text: String) {
        if self.timeline.last().map(|(_, s)| s.as_str()) != Some(text.as_str()) {
            push_bounded(&mut self.timeline, (ts, text));
        }
    }

    fn read_proc_locks(&self) -> HashMap<String, u32> {
        if let Some(ref txt) = self.custom_proc_locks {
            return parse_proc_locks(txt);
        }
        if let Ok(txt) = std::fs::read_to_string("/proc/locks") {
            return parse_proc_locks(&txt);
        }
        HashMap::new()
    }

    /// netsim INIs in the monitor's and each daemon's discovery dirs, one path
    /// per file (macOS `/tmp` and `$TMPDIR` are symlinks, so one INI can be
    /// reachable twice). `custom_ini_dirs`, when set, replaces the search.
    fn discover_ini_files(&self, daemon_procs: &[NetsimDaemonProc]) -> Vec<PathBuf> {
        let os = self.host_os;
        let mut dirs = Vec::new();
        if self.custom_ini_dirs.is_empty() {
            extend_unique(&mut dirs, current_host_discovery_dirs(os, current_uid()));
            for p in daemon_procs {
                extend_unique(&mut dirs, get_netsim_discovery_dirs(&p.environ, os));
            }
        } else {
            extend_unique(&mut dirs, self.custom_ini_dirs.iter().cloned());
        }
        let max_ini_len = u64::try_from(MAX_INI_BYTES).unwrap_or(u64::MAX);
        let mut seen = HashSet::new();
        let mut ini_files = Vec::new();
        for d in dirs {
            let Ok(entries) = std::fs::read_dir(&d) else {
                continue;
            };
            let mut paths: Vec<PathBuf> = entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| {
                    extract_ini_instance_num(p).is_some()
                        && std::fs::symlink_metadata(p)
                            .is_ok_and(|m| m.file_type().is_file() && m.len() <= max_ini_len)
                })
                .collect();
            paths.sort();
            for p in paths {
                if seen.insert(std::fs::canonicalize(&p).unwrap_or_else(|_| p.clone())) {
                    ini_files.push(p);
                }
            }
        }
        ini_files.sort();
        ini_files
    }

    /// Pairs each INI with at most one live daemon and vice versa, strongest
    /// evidence first:
    /// 1. holders of the INI's lock files (Linux `/proc/locks`); an INI with
    ///    holders pairs only through them. Then `pid=`, if that daemon started
    ///    before the INI was written, which rejects reused PIDs.
    /// 2. The daemon that already owns the INI (earlier tick or its log).
    /// 3. For INIs without holders or `pid=` (older netsim), an unowned daemon
    ///    of the same instance that started before the INI was written.
    ///    Newer daemons choose first and take the oldest such INI, because an
    ///    older daemon's window also covers INIs written after it.
    ///
    /// Mirrors `NetsimTracker._match_inis_to_daemons()` in
    /// `python/emulator_resource_monitor.py`.
    fn match_inis_to_daemons(
        &self,
        inis: &[IniObservation],
        live: &HashSet<(u32, u64)>,
    ) -> Vec<Option<(u32, u64)>> {
        self.match_inis_to_daemons_with(inis, live, super::owner_uid())
    }

    fn match_inis_to_daemons_with(
        &self,
        inis: &[IniObservation],
        live: &HashSet<(u32, u64)>,
        monitor_uid: Option<u32>,
    ) -> Vec<Option<(u32, u64)>> {
        let is_same_user = |k: &(u32, u64)| self.daemons.get(k).is_some_and(|d| d.same_user);
        let uid_compatible = |ini: &IniObservation, key: &(u32, u64)| {
            if let (Some(ini_uid), Some(exp_uid)) =
                (ini.file_uid, self.daemons.get(key).and_then(|d| d.expected_uid))
            {
                return ini_uid == exp_uid;
            }
            monitor_uid.is_none_or(|uid| uid == 0)
                || ini.owned_by_self.is_none_or(|ini_own| ini_own == is_same_user(key))
        };
        let mut live_keys: Vec<(u32, u64)> = live.iter().copied().collect();
        live_keys.sort_by_key(|k| (!is_same_user(k), *k));
        let live_by_pid: HashMap<u32, (u32, u64)> = live_keys.iter().map(|&k| (k.0, k)).collect();
        let written_after_start = |ini: &IniObservation, key: &(u32, u64)| {
            self.daemons.get(key).is_some_and(|d| {
                // An unknown start time (create_time 0) or mtime accepts the INI.
                d.create_time == 0
                    || ini.mtime.is_none_or(|m| m >= d.start_wall - INI_MTIME_SLACK_S)
            })
        };
        let mut matched: Vec<Option<(u32, u64)>> = vec![None; inis.len()];
        let mut claimed: HashSet<(u32, u64)> = HashSet::new();

        for (slot, ini) in matched.iter_mut().zip(inis) {
            *slot = ini
                .holders
                .iter()
                .filter_map(|pid| live_by_pid.get(pid).copied())
                .find(|k| !claimed.contains(k));
            if let Some(k) = *slot {
                claimed.insert(k);
            }
        }
        for (slot, ini) in matched.iter_mut().zip(inis) {
            if slot.is_some() || !ini.holders.is_empty() {
                continue;
            }
            if let Some(k) = ini.pid.and_then(|pid| live_by_pid.get(&pid).copied()).filter(|k| {
                !claimed.contains(k) && uid_compatible(ini, k) && written_after_start(ini, k)
            }) {
                *slot = Some(k);
                claimed.insert(k);
            }
        }
        for (slot, ini) in matched.iter_mut().zip(inis) {
            if slot.is_some() || !ini.holders.is_empty() {
                continue;
            }
            *slot = live_keys.iter().copied().find(|k| {
                !claimed.contains(k)
                    && uid_compatible(ini, k)
                    && self
                        .daemons
                        .get(k)
                        .filter(|d| !d.client_mode_recorded)
                        .and_then(|d| d.ini_path.as_deref())
                        .is_some_and(|p| same_file(p, &ini.path))
            });
            if let Some(k) = *slot {
                claimed.insert(k);
            }
        }

        let start_of = |k: &(u32, u64)| self.daemons.get(k).map_or(0.0, |d| d.start_wall);
        let mut unowned: Vec<(u32, u64)> = live_keys
            .into_iter()
            .filter(|k| {
                !claimed.contains(k)
                    && self
                        .daemons
                        .get(k)
                        .is_some_and(|d| !d.client_mode_recorded && d.ini_path.is_none())
            })
            .collect();
        unowned.sort_by(|a, b| {
            (!is_same_user(a))
                .cmp(&(!is_same_user(b)))
                .then_with(|| start_of(b).total_cmp(&start_of(a)))
        });
        let mtime_or_last = |i: usize| inis[i].mtime.unwrap_or(f64::INFINITY);
        for k in unowned {
            let instance = self
                .daemons
                .get(&k)
                .and_then(|d| d.instance_num)
                .unwrap_or(DEFAULT_NETSIM_INSTANCE);
            let oldest = (0..inis.len())
                .filter(|&i| {
                    matched[i].is_none()
                        && inis[i].holders.is_empty()
                        && inis[i].pid.is_none()
                        && inis[i].instance == Some(instance)
                        && uid_compatible(&inis[i], &k)
                        && written_after_start(&inis[i], &k)
                })
                .min_by(|&a, &b| mtime_or_last(a).total_cmp(&mtime_or_last(b)));
            if let Some(i) = oldest {
                matched[i] = Some(k);
            }
        }
        matched
    }

    fn classify_spawner(
        ppid: Option<u32>,
        parent_name: Option<&str>,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    ) -> String {
        let Some(pp) = ppid else {
            return "STANDALONE".to_string();
        };
        if pp <= 1 {
            return "STANDALONE".to_string();
        }
        for g in emu_groups.values() {
            if g.pids.contains(&pp) {
                return "emulator".to_string();
            }
        }
        let comm_name = parent_name
            .map(|s| s.to_string())
            .or_else(|| std::fs::read_to_string(format!("/proc/{}/comm", pp)).ok());
        if let Some(comm) = comm_name {
            let low = comm.trim().to_lowercase();
            if low.contains("qemu") || low.contains("crosvm") {
                return "qemu".to_string();
            }
            if low.contains("emulator") || low.contains("launcherloop") {
                return "emulator".to_string();
            }
        }
        "STANDALONE".to_string()
    }

    /// Whether the fast socket sub-poll still has a connect latency to measure:
    /// an emulator is CONNECTING and has no transport timestamp yet.
    pub fn has_any_connecting(&self) -> bool {
        let has_active_daemon = self.daemons.values().any(|d| !d.exited && d.grpc_port.is_some());
        if !has_active_daemon {
            return false;
        }
        self.emulator_records
            .values()
            .any(|r| r.state == "CONNECTING" && !r.no_netsim && r.first_transport_ts.is_none())
    }

    /// Re-samples emulator sockets between intervals while an emulator is
    /// CONNECTING. Off Linux each call starts `lsof` or `netstat`.
    pub fn poll_sockets_fast(
        &mut self,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        now_wall: f64,
    ) {
        let active_ports: HashSet<u16> =
            self.daemons.values().filter(|d| !d.exited).filter_map(|d| d.grpc_port).collect();
        if active_ports.is_empty() {
            return;
        }
        let daemon_pids: HashSet<u32> =
            self.daemons.values().filter(|d| !d.exited).map(|d| d.pid).collect();
        let Some(port_to_conns) =
            self.collect_sockets(&active_ports, emu_groups, &daemon_pids, false)
        else {
            return;
        };
        for d in self.daemons.values() {
            if d.exited {
                continue;
            }
            let Some(port) = d.grpc_port else {
                continue;
            };
            if let Some(conns) = port_to_conns.get(&port) {
                for c in conns {
                    for rec in self.emulator_records.values_mut() {
                        if rec.is_settled() || rec.no_netsim || rec.state != "CONNECTING" {
                            continue;
                        }
                        if rec.serial == c.serial && rec.first_transport_ts.is_none() {
                            rec.first_transport_ts = Some(now_wall);
                            rec.netsim_connect_s = Some((now_wall - rec.worker_start_ts).max(0.0));
                        }
                    }
                }
            }
        }
    }

    /// Maps each emulator in this tick's host scan to its session record key,
    /// following the Python monitor's main loop: a bare-serial record moves to
    /// the AVD-qualified key once the AVD name is known, and otherwise a worker
    /// keeps the key it already has. A record under another key matches on
    /// serial and worker pid, so a different AVD on a reused port never takes
    /// it over. When the same AVD restarts on the same port with a new worker
    /// pid, any bare-serial record from before the AVD id was resolved merges
    /// the prior run's cumulative stats into the AVD-qualified key.
    fn claim_session_keys(
        &mut self,
        summary_emulators: &[(String, String, u32, f64)],
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        now_mono: f64,
    ) -> Vec<LiveEmulatorEntry> {
        let mut live = Vec::with_capacity(summary_emulators.len());
        for (s_key, serial, pid, first_seen_mono) in summary_emulators {
            let mut key = s_key.clone();
            let g_info = emu_groups.get(serial);
            let pid_ctime = Self::group_pid_ctime(g_info, *pid);
            if self.emulator_records.get(&key).is_none_or(|r| {
                *pid > 0
                    && r.worker_pid > 0
                    && (r.worker_pid != *pid
                        || (r.worker_create_time > 0
                            && pid_ctime > 0
                            && r.worker_create_time != pid_ctime))
                    && !Self::group_contains_worker(g_info, r)
                    && (r.is_inactive() || !Self::record_has_member(r, *pid, pid_ctime))
            }) {
                let same_worker = self
                    .emulator_records
                    .iter()
                    .find(|(k, r)| {
                        if *k == &key || r.serial != *serial {
                            return false;
                        }
                        if r.is_inactive() {
                            return *k != serial
                                && *pid > 0
                                && pid_ctime > 0
                                && ((r.worker_pid == *pid && r.worker_create_time == pid_ctime)
                                    || r.group_procs.get(pid).copied() == Some(pid_ctime));
                        }
                        let same_worker_pid = *pid > 0
                            && r.worker_pid == *pid
                            && (r.worker_create_time == 0
                                || pid_ctime == 0
                                || r.worker_create_time == pid_ctime);
                        same_worker_pid
                            || Self::group_contains_worker(g_info, r)
                            || Self::record_has_member(r, *pid, pid_ctime)
                    })
                    .map(|(k, _)| k.clone());
                if let Some(old_key) = same_worker {
                    if old_key == *serial {
                        if let Some(mut bare_rec) = self.emulator_records.remove(&old_key) {
                            if let Some(prov_dk) = self.provisional_daemons.remove(&old_key) {
                                self.provisional_daemons.insert(key.clone(), prov_dk);
                            }
                            if let Some(prior_bare) = bare_rec.prior_bare_snapshot.take() {
                                bare_rec.disconnect_count = bare_rec
                                    .disconnect_count
                                    .saturating_sub(prior_bare.disconnect_count);
                                bare_rec.connected_total_s = (bare_rec.connected_total_s
                                    - prior_bare.connected_total_s)
                                    .max(0.0);
                                self.emulator_records.insert(old_key, *prior_bare);
                            }
                            if let Some(prev_rec) = self.emulator_records.remove(&key) {
                                bare_rec.merge_prior_run(prev_rec, now_mono);
                            }
                            bare_rec.storage_key = key.clone();
                            self.emulator_records.insert(key.clone(), bare_rec);
                        }
                    } else if key == *serial || !self.emulator_records.contains_key(&key) {
                        key = old_key;
                    }
                }
            }
            live.push(LiveEmulatorEntry {
                storage_key: key,
                serial: serial.clone(),
                fallback_pid: *pid,
                first_seen_mono: *first_seen_mono,
            });
        }
        live
    }

    /// Records a tick on which a previously scanned emulator is missing from
    /// the host scan, and returns whether it has exited. While adb reports the
    /// serial live the emulator is held at its last state and any miss run is
    /// cleared. It exits after SETTLE_AFTER_MISSED_TICKS consecutive ticks
    /// missing from both, dated to the first of them (`missing_since_mono`).
    fn record_missed_tick(rec: &mut EmulatorNetsimRecord, adb_live: bool, now_mono: f64) -> bool {
        if adb_live {
            rec.missing_since_mono = None;
            rec.missed_ticks = 0;
            return false;
        }
        rec.missing_since_mono.get_or_insert(now_mono);
        if rec.missed_ticks < SETTLE_AFTER_MISSED_TICKS {
            rec.missed_ticks += 1;
        }
        rec.missed_ticks >= SETTLE_AFTER_MISSED_TICKS
    }

    /// Transitions for an emulator that joined no live daemon this tick, or
    /// whose worker has exited: a connected emulator becomes DISCONNECTED, and
    /// one that never reached CONNECTED fails.
    fn settle_unjoined_emulator(
        rec: &mut EmulatorNetsimRecord,
        prev_exit: Option<&str>,
        worker_gone: bool,
        now_mono: f64,
    ) {
        if rec.is_settled() {
            return;
        }
        if rec.state == "CONNECTED" || rec.state == "CONNECTED_NO_CHIPS" {
            rec.state = "DISCONNECTED".to_string();
            rec.disconnect_count += 1;
            if let Some(st) = rec.connected_start_mono.take() {
                rec.connected_total_s += (now_mono - st).max(0.0);
            }
        } else if prev_exit == Some("startup_timeout") {
            rec.state = "FAILED(launcher_abort)".to_string();
        } else if worker_gone {
            rec.state = "FAILED(worker_exit_no_connect)".to_string();
        }
    }

    /// Updates daemon and emulator state for a single monitoring tick.
    pub fn update(
        &mut self,
        daemon_procs: &[NetsimDaemonProc],
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        summary_emulators: &[(String, String, u32, f64)],
        connected_devices: &HashMap<String, String>,
        now_wall: f64,
        now_mono: f64,
    ) {
        let ini_files = self.discover_ini_files(daemon_procs);
        let lock_holders =
            if ini_files.is_empty() { HashMap::new() } else { self.read_proc_locks() };
        let seen_daemon_keys =
            self.discover_and_poll_daemons(daemon_procs, emu_groups, now_wall, now_mono);
        self.reconcile_ini_files(ini_files, &lock_holders, &seen_daemon_keys, now_wall);
        self.emit_pending_ready_events(&seen_daemon_keys);
        self.mark_exited_daemons(&seen_daemon_keys, now_wall, now_mono);
        let port_to_conns = self.poll_active_daemon_sockets(emu_groups);
        let live_emulators = self.claim_session_keys(summary_emulators, emu_groups, now_mono);
        let held_count =
            self.settle_missing_emulators(&live_emulators, connected_devices, now_mono);
        self.update_live_emulators(
            &live_emulators,
            emu_groups,
            connected_devices,
            &port_to_conns,
            held_count,
            (now_wall, now_mono),
        );
        self.finalize_daemons_and_harvest_pcaps(now_wall, now_mono);
    }

    /// Registers newly discovered `netsimd` processes, polls each active daemon's
    /// stderr log tailer, and records duplicate artifact directory warnings.
    fn discover_and_poll_daemons(
        &mut self,
        daemon_procs: &[NetsimDaemonProc],
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        now_wall: f64,
        now_mono: f64,
    ) -> HashSet<(u32, u64)> {
        let mut seen_daemon_keys = HashSet::new();
        let mut art_dir_counts: HashMap<PathBuf, usize> = HashMap::new();

        for p in daemon_procs {
            let d_key = (p.pid, p.start_time);
            seen_daemon_keys.insert(d_key);

            if !self.daemons.contains_key(&d_key) {
                self.register_new_daemon(p, emu_groups, now_wall, now_mono);
            } else if let Some(d) = self.daemons.get_mut(&d_key) {
                Self::clear_disallowed_daemon_art_dir(d);
            }
        }

        let mut ordered_keys: Vec<DaemonKey> = seen_daemon_keys.iter().copied().collect();
        ordered_keys.sort_by(|a, b| {
            let sw_a = self.daemons.get(a).map_or(0.0, |d| d.start_wall);
            let sw_b = self.daemons.get(b).map_or(0.0, |d| d.start_wall);
            sw_b.total_cmp(&sw_a).then_with(|| b.0.cmp(&a.0)).then_with(|| b.1.cmp(&a.1))
        });

        let mut events_by_key: HashMap<DaemonKey, Vec<(f64, String)>> = HashMap::new();
        let mut pre_polled: HashSet<DaemonKey> = HashSet::new();
        if self.enable_log {
            // Poll established (offset > 0) shared-log daemons with freeze_on_truncation first so
            // truncated prior servers mark `tailer.frozen = true` before newer sharers classify
            // shared-log ownership.
            for &d_key in &ordered_keys {
                let should_pre_poll = self
                    .daemons
                    .get(&d_key)
                    .filter(|d| {
                        !d.log_path.as_os_str().is_empty()
                            && !d.client_mode_recorded
                            && d.tailer.as_ref().is_some_and(|t| t.offset > 0)
                    })
                    .is_some_and(|d| {
                        seen_daemon_keys.iter().any(|ok| {
                            *ok != d_key
                                && self.daemons.get(ok).is_some_and(|od| {
                                    od.log_path == d.log_path
                                        && !od.client_mode_recorded
                                        && (od.start_wall > d.start_wall
                                            || (od.start_wall == d.start_wall && od.pid > d.pid))
                                })
                        })
                    });
                if !should_pre_poll {
                    continue;
                }
                if let Some(d) = self.daemons.get_mut(&d_key) {
                    Self::clear_disallowed_daemon_art_dir(d);
                    if !d.log_path.as_os_str().is_empty() {
                        let mut new_events = Vec::new();
                        Self::poll_daemon_log(
                            d,
                            now_wall,
                            true,
                            (false, false),
                            &HashSet::new(),
                            &mut new_events,
                        );
                        events_by_key.insert(d_key, new_events);
                        pre_polled.insert(d_key);
                    }
                }
            }
        }

        for &d_key in &ordered_keys {
            if let Some(d) = self.daemons.get_mut(&d_key) {
                Self::clear_disallowed_daemon_art_dir(d);
            }
            if pre_polled.contains(&d_key) {
                if let Some(d) = self.daemons.get(&d_key) {
                    if !d.artifact_dir.as_os_str().is_empty() {
                        *art_dir_counts.entry(d.artifact_dir.clone()).or_insert(0) += 1;
                    }
                }
                continue;
            }
            let (
                has_newer_server_sharer,
                has_older_server_sharer,
                has_newer_client_sharer,
                active_sharer_pids,
            ) = self
                .daemons
                .get(&d_key)
                .filter(|d| !d.log_path.as_os_str().is_empty())
                .map(|d| {
                    let d_has_offset = d.tailer.as_ref().is_some_and(|t| t.offset > 0);
                    let mut newer = false;
                    let mut older = false;
                    let mut newer_client = false;
                    let mut pids = HashSet::new();
                    for ok in &seen_daemon_keys {
                        if *ok == d_key {
                            continue;
                        }
                        let Some(od) = self.daemons.get(ok) else {
                            continue;
                        };
                        if od.log_path != d.log_path {
                            continue;
                        }
                        let od_is_newer = od.start_wall > d.start_wall
                            || (od.start_wall == d.start_wall && od.pid > d.pid);
                        if od.client_mode_recorded {
                            if od_is_newer {
                                newer_client = true;
                            }
                            continue;
                        }
                        let od_frozen = od.tailer.as_ref().is_some_and(|t| t.frozen);
                        if !od_frozen {
                            pids.insert(od.pid);
                        }
                        if od_is_newer {
                            let od_claimed = od.tailer.as_ref().is_some_and(|t| t.offset > 0)
                                || od.ready_ts.is_some()
                                || od.grpc_port.is_some()
                                || od.ini_path.is_some();
                            if d_has_offset || od_claimed {
                                newer = true;
                            }
                        } else if !od_frozen {
                            older = true;
                        }
                    }
                    (newer, older, newer_client, pids)
                })
                .unwrap_or_default();
            let mut new_events = Vec::new();
            if let Some(d) = self.daemons.get_mut(&d_key) {
                if self.enable_log {
                    if !has_newer_server_sharer {
                        Self::poll_daemon_log(
                            d,
                            now_wall,
                            false,
                            (has_older_server_sharer, has_newer_client_sharer),
                            &active_sharer_pids,
                            &mut new_events,
                        );
                    } else if d.tailer.as_ref().is_some_and(|t| t.offset > 0) {
                        Self::poll_daemon_log(
                            d,
                            now_wall,
                            true,
                            (has_older_server_sharer, has_newer_client_sharer),
                            &active_sharer_pids,
                            &mut new_events,
                        );
                    }
                }
                if !d.artifact_dir.as_os_str().is_empty() {
                    *art_dir_counts.entry(d.artifact_dir.clone()).or_insert(0) += 1;
                }
            }
            events_by_key.insert(d_key, new_events);
        }

        for p in daemon_procs {
            let d_key = (p.pid, p.start_time);
            if let Some(new_events) = events_by_key.remove(&d_key) {
                for (ts, ev) in new_events {
                    self.record_event(ts, ev);
                }
            }
        }

        for (art_dir, cnt) in art_dir_counts {
            if cnt > 1 {
                self.record_event(
                    now_wall,
                    format!("DUPLICATE_ARTIFACT_DIR({})", art_dir.display()),
                );
            }
        }

        seen_daemon_keys
    }

    fn resolve_daemon_expected_uid(
        same_user: bool,
        daemon_uid: Option<u32>,
        monitor_uid: Option<u32>,
    ) -> Option<u32> {
        #[cfg(unix)]
        {
            daemon_uid.or_else(|| same_user.then_some(monitor_uid).flatten().filter(|&u| u != 0))
        }
        #[cfg(not(unix))]
        {
            let _ = (same_user, daemon_uid, monitor_uid);
            None
        }
    }

    fn is_disallowed_existing_art_node(
        path: &Path,
        same_user: bool,
        monitor_uid: Option<u32>,
        eff_expected_uid: Option<u32>,
    ) -> bool {
        match std::fs::symlink_metadata(path) {
            Ok(m) => {
                if !m.file_type().is_dir() {
                    return true;
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::MetadataExt;
                    if (m.mode() & 0o002) != 0 {
                        return true;
                    }
                    if let Some(exp_uid) = eff_expected_uid {
                        return m.uid() != exp_uid;
                    }
                    if same_user {
                        monitor_uid.is_none()
                    } else {
                        monitor_uid.is_some_and(|uid| uid != 0 && m.uid() == uid)
                    }
                }
                #[cfg(not(unix))]
                {
                    let _ = (same_user, monitor_uid, eff_expected_uid);
                    false
                }
            }
            Err(e) => e.kind() != std::io::ErrorKind::NotFound,
        }
    }

    fn is_disallowed_daemon_art_dir_with(
        art_dir: &Path,
        same_user: bool,
        monitor_uid: Option<u32>,
        daemon_uid: Option<u32>,
    ) -> bool {
        if art_dir.as_os_str().is_empty() {
            return false;
        }
        #[cfg(unix)]
        if same_user && monitor_uid.is_none() && daemon_uid.is_none() {
            return true;
        }
        let eff_expected_uid =
            Self::resolve_daemon_expected_uid(same_user, daemon_uid, monitor_uid);
        if !has_trusted_existing_ancestors(art_dir, eff_expected_uid, 0) {
            return true;
        }
        if Self::is_disallowed_existing_art_node(art_dir, same_user, monitor_uid, eff_expected_uid)
        {
            return true;
        }
        if let Some(parent) = art_dir.parent().filter(|p| !p.as_os_str().is_empty()) {
            let is_android_parent = parent
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n == "android" || n.starts_with("android-"));
            if is_android_parent
                && Self::is_disallowed_existing_art_node(
                    parent,
                    same_user,
                    monitor_uid,
                    eff_expected_uid,
                )
            {
                return true;
            }
        }
        false
    }

    fn is_disallowed_daemon_art_dir(
        art_dir: &Path,
        same_user: bool,
        expected_uid: Option<u32>,
    ) -> bool {
        Self::is_disallowed_daemon_art_dir_with(
            art_dir,
            same_user,
            super::owner_uid(),
            expected_uid,
        )
    }

    fn clear_disallowed_daemon_art_dir(d: &mut DaemonRecord) {
        if Self::is_disallowed_daemon_art_dir(&d.artifact_dir, d.same_user, d.expected_uid) {
            d.artifact_dir = PathBuf::new();
            d.log_path = PathBuf::new();
            d.tailer = None;
            d.stats_cache = None;
            d.log_available = false;
        }
    }

    /// Creates a `DaemonRecord` for a newly observed `netsimd` process and
    /// records its initial timeline events.
    fn register_new_daemon(
        &mut self,
        p: &NetsimDaemonProc,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        now_wall: f64,
        now_mono: f64,
    ) {
        let d_key = (p.pid, p.start_time);
        let precise_start = get_proc_precise_start_time(p.pid, p.start_time as f64);
        let inst_num = extract_netsim_instance_num(&p.cmd, &p.environ);
        let expected_uid =
            Self::resolve_daemon_expected_uid(p.same_user, p.uid, super::owner_uid());
        let mut art_dir = if p.same_user {
            resolve_netsim_artifact_dir(&p.environ, current_host_env, self.host_os)
        } else if !p.environ.is_empty() {
            resolve_netsim_artifact_dir_for_os(&p.environ, self.host_os)
        } else {
            PathBuf::new()
        };
        if Self::is_disallowed_daemon_art_dir(&art_dir, p.same_user, expected_uid) {
            art_dir = PathBuf::new();
        }
        let log_path = if art_dir.as_os_str().is_empty() {
            PathBuf::new()
        } else {
            resolve_netsim_stderr_log_path(&art_dir, inst_num)
        };
        let spawner = Self::classify_spawner(p.parent, p.parent_name.as_deref(), emu_groups);
        let netsim_cli_bin =
            resolve_netsim_cli_bin_for_os(p.exe.as_deref(), p.same_user, self.host_os);
        let frontend_off = p.cmd.iter().any(|t| t == "--no-cli-ui" || t == "--no-web-ui");
        let pcap_cli_on = p.cmd.iter().any(|t| t == "--pcap");
        let start_wall = if precise_start > 0.0 { precise_start } else { now_wall };
        let min_log_ts = (p.start_time > 0).then_some(start_wall - LOG_FLOOR_SLACK_S);

        self.daemons.insert(
            d_key,
            DaemonRecord {
                daemon_key: d_key,
                pid: p.pid,
                create_time: p.start_time,
                start_wall,
                spawner: spawner.clone(),
                instance_num: inst_num,
                artifact_dir: art_dir,
                log_path: log_path.clone(),
                tailer: if self.enable_log && !log_path.as_os_str().is_empty() {
                    let mut tailer = NetsimLogTailer::new(log_path);
                    tailer.min_ts = min_log_ts;
                    tailer.require_self_owner = p.same_user;
                    tailer.expected_uid = expected_uid;
                    Some(tailer)
                } else {
                    None
                },
                netsim_cli_bin,
                same_user: p.same_user,
                expected_uid,
                version: None,
                ini_path: None,
                grpc_port: None,
                ready_ts: None,
                ready_recorded: false,
                netsim_daemon_ready_s: None,
                frontend_disabled: frontend_off,
                pcap_flag: pcap_cli_on,
                log_available: false,
                log_unavail_recorded: false,
                client_mode_recorded: false,
                exited: false,
                exit_reason_hint: None,
                exit_reason: None,
                orphaned: false,
                zero_devices_since_mono: Some(now_mono),
                stats_cache: None,
                pcap_live_bytes: 0,
                pcap_live_files: 0,
                active_socket_conns: 0,
                joined_emulator_count: 0,
            },
        );
        self.record_event(start_wall, format!("STARTED(pid={}, spawner={})", p.pid, spawner));
        if frontend_off {
            self.record_event(start_wall, format!("FRONTEND_DISABLED(pid={})", p.pid));
        }
    }

    /// Polls a daemon's stderr log tailer and synchronizes parsed metadata and
    /// lifecycle events onto the `DaemonRecord`.
    fn poll_daemon_log(
        d: &mut DaemonRecord,
        now_wall: f64,
        freeze_on_truncation: bool,
        (has_older_server_sharer, has_newer_client_sharer): (bool, bool),
        active_sharer_pids: &HashSet<u32>,
        new_events: &mut Vec<(f64, String)>,
    ) {
        let Some(ref mut tailer) = d.tailer else {
            d.log_available = false;
            if !d.log_unavail_recorded {
                d.log_unavail_recorded = true;
                new_events.push((now_wall, format!("LOG_UNAVAILABLE(pid={})", d.pid)));
            }
            return;
        };
        let prev_offset = tailer.offset;
        let ok = tailer.poll_with_truncation_policy(now_wall, freeze_on_truncation);
        d.log_available = ok;
        if !ok && !d.log_unavail_recorded {
            d.log_unavail_recorded = true;
            new_events.push((now_wall, format!("LOG_UNAVAILABLE(pid={})", d.pid)));
        }
        if d.ready_ts.is_none() && d.ini_path.is_none() && d.grpc_port.is_none() {
            let ini_pid = tailer
                .ini_path_logged
                .as_deref()
                .map(Path::new)
                .filter(|p| can_adopt_logged_ini_path(p, d.same_user, d.expected_uid))
                .and_then(|p| {
                    read_owned_regular_file_bounded(p, MAX_INI_BYTES, d.expected_uid).and_then(
                        |txt| {
                            parse_netsim_ini_str(&txt)
                                .get("pid")
                                .and_then(|s| s.parse::<u32>().ok())
                        },
                    )
                });
            if tailer.client_mode_spawn
                && ini_pid != Some(d.pid)
                && (ini_pid.is_some() || has_older_server_sharer)
            {
                if !d.client_mode_recorded {
                    d.client_mode_recorded = true;
                    new_events.push((now_wall, format!("CLIENT_MODE_SPAWN(pid={})", d.pid)));
                }
                tailer.connections.clear();
                tailer.created_devices.clear();
                tailer.chip_adds.clear();
                tailer.empty_devices.clear();
                tailer.wifi_associations.clear();
                return;
            }
            if !tailer.client_mode_spawn
                && prev_offset == 0
                && ini_pid.is_some_and(|ip| ip != d.pid && active_sharer_pids.contains(&ip))
            {
                tailer.offset = 0;
                tailer.reset_state();
                return;
            }
        }
        if let Some(ref art_logged) = tailer.artifact_dir_logged {
            let pb = PathBuf::from(art_logged);
            if is_real_dir(&pb)
                && !Self::is_disallowed_daemon_art_dir(&pb, d.same_user, d.expected_uid)
            {
                d.artifact_dir = pb;
            }
        }
        if let Some(ref v) = tailer.version {
            d.version = Some(v.clone());
        }
        if let Some(ref ip) = tailer.ini_path_logged {
            let pb = PathBuf::from(ip);
            if can_adopt_logged_ini_path(&pb, d.same_user, d.expected_uid)
                && d.ini_path.as_ref().is_none_or(|cur| !d.exited && !cur.exists())
            {
                d.ini_path = Some(pb);
            }
        }
        if let Some(gp) = tailer.grpc_port_logged {
            d.grpc_port = Some(gp);
        }
        if let Some(ref hint) = tailer.exit_reason_hint {
            if hint != "clean_unknown" {
                d.exit_reason_hint = Some(hint.clone());
            }
        }
        // A ready line from before this daemon started was written by an earlier daemon that
        // shared the log; this daemon truncates the log and writes its own.
        let own_ready_ts = tailer.ready_ts.filter(|&ts| ts >= d.start_wall - LOG_FLOOR_SLACK_S);
        if let Some(r_ts) = own_ready_ts.filter(|_| d.ready_ts.is_none()) {
            d.ready_ts = Some(r_ts);
            let ready_s = (r_ts - d.start_wall).max(0.0);
            d.netsim_daemon_ready_s = Some(ready_s);
        }
        if tailer.frontend_disabled && !d.frontend_disabled {
            d.frontend_disabled = true;
            new_events.push((now_wall, format!("FRONTEND_DISABLED(pid={})", d.pid)));
        }
        if tailer.pcap_flag {
            d.pcap_flag = true;
        }
        if tailer.client_mode_spawn
            && !d.client_mode_recorded
            && !has_newer_client_sharer
            && d.ready_ts.is_none()
            && d.ini_path.is_none()
            && d.grpc_port.is_none()
        {
            d.client_mode_recorded = true;
            new_events.push((now_wall, format!("CLIENT_MODE_SPAWN(pid={})", d.pid)));
        }
    }

    fn resolve_daemon_ready_port(d: &mut DaemonRecord) {
        if d.grpc_port.is_none() {
            if let Some(ref ini_path) = d.ini_path {
                if can_adopt_logged_ini_path(ini_path, d.same_user, d.expected_uid) {
                    if let Some(txt) =
                        read_owned_regular_file_bounded(ini_path, MAX_INI_BYTES, d.expected_uid)
                    {
                        let data = parse_netsim_ini_str(&txt);
                        if let Some(port) = data
                            .get("grpc.port")
                            .and_then(|s| s.parse::<u16>().ok())
                            .filter(|&p| p > 0)
                        {
                            d.grpc_port = Some(port);
                        }
                    }
                }
            }
        }
    }

    fn emit_ready_event_if_needed(d: &mut DaemonRecord, new_events: &mut Vec<(f64, String)>) {
        let Some(r_ts) = d.ready_ts.filter(|_| !d.ready_recorded) else {
            return;
        };
        d.ready_recorded = true;
        Self::resolve_daemon_ready_port(d);
        let ready_s = d.netsim_daemon_ready_s.unwrap_or_else(|| (r_ts - d.start_wall).max(0.0));
        let port_str = d.grpc_port.map(|p| p.to_string()).unwrap_or_else(|| "N/A".to_string());
        new_events.push((
            r_ts,
            format!("READY(pid={}, port={}, ready_s={:.2}s)", d.pid, port_str, ready_s),
        ));
    }

    fn emit_pending_ready_events(&mut self, seen_daemon_keys: &HashSet<(u32, u64)>) {
        let mut keys: Vec<DaemonKey> = seen_daemon_keys.iter().copied().collect();
        keys.sort_unstable();
        let mut ready_events = Vec::new();
        for dk in keys {
            if let Some(d) = self.daemons.get_mut(&dk) {
                Self::emit_ready_event_if_needed(d, &mut ready_events);
            }
        }
        for (ts, ev) in ready_events {
            self.record_event(ts, ev);
        }
    }

    fn backfill_daemon_version_from_stats(d: &mut DaemonRecord) {
        if d.version.is_some()
            || !is_real_dir(&d.artifact_dir)
            || Self::is_disallowed_daemon_art_dir(&d.artifact_dir, d.same_user, d.expected_uid)
        {
            return;
        }
        let stats_path = d.artifact_dir.join("netsim_session_stats.json");
        let Some((_, val)) = super::refresh_session_stats_cache_for_owner(
            &stats_path,
            d.start_wall,
            &mut d.stats_cache,
            d.same_user,
            d.expected_uid,
        ) else {
            return;
        };
        if let Some(ver) = val.get("version").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
            d.version = Some(ver.to_string());
        }
    }

    /// Pairs discovered `netsim*.ini` files with live daemons, tracking daemon
    /// restarts on the same INI path and recording stale INIs left by dead writers.
    fn reconcile_ini_files(
        &mut self,
        ini_files: Vec<PathBuf>,
        lock_holders: &HashMap<String, u32>,
        seen_daemon_keys: &HashSet<(u32, u64)>,
        now_wall: f64,
    ) {
        self.stale_inis.retain(|k, _| ini_files.iter().any(|p| same_file(k, p)));
        let seen_pids: HashSet<u32> = seen_daemon_keys.iter().map(|&(pid, _)| pid).collect();
        let inis: Vec<IniObservation> =
            ini_files.into_iter().map(|p| IniObservation::read(p, lock_holders)).collect();
        let matches = self.match_inis_to_daemons(&inis, seen_daemon_keys);
        for (ini, matched) in inis.into_iter().zip(matches) {
            if let Some(m_key) = matched {
                if let Some(d) = self.daemons.get_mut(&m_key) {
                    d.ini_path = Some(ini.path.clone());
                    if let Some(p) = ini.port {
                        d.grpc_port = Some(p);
                    }
                }
                let prev_owner = self
                    .last_active_by_ini
                    .iter()
                    .find(|(p, _)| same_file(p, &ini.path))
                    .map(|(_, &k)| k);
                if let Some(prev_key) = prev_owner.filter(|&prev_key| prev_key != m_key) {
                    self.restart_count += 1;
                    self.record_event(
                        now_wall,
                        format!(
                            "RESTARTED(ini={}, old_pid={}, new_pid={})",
                            ini.path.display(),
                            prev_key.0,
                            m_key.0
                        ),
                    );
                }
                self.stale_inis.retain(|p, _| !same_file(p, &ini.path));
                self.last_active_by_ini.retain(|p, _| !same_file(p, &ini.path));
                self.last_active_by_ini.insert(ini.path, m_key);
            } else if ini.holders.is_empty()
                && !self.stale_inis.keys().any(|p| same_file(p, &ini.path))
            {
                // A `pid=` naming a tracked daemon that did not match was
                // reused; only an untracked live writer keeps the INI fresh.
                let writer_alive =
                    ini.pid.is_some_and(|pid| !seen_pids.contains(&pid) && (self.pid_alive)(pid));
                if !writer_alive {
                    self.record_event(now_wall, format!("STALE_INI(path={})", ini.path.display()));
                    self.stale_inis.insert(ini.path, ini.data);
                }
            }
        }
    }

    /// Marks tracked daemons absent from this tick's process scan as exited,
    /// classifies their exit reason from final log lines or leftover INIs, and
    /// performs a final pcap harvest.
    fn mark_exited_daemons(
        &mut self,
        seen_daemon_keys: &HashSet<(u32, u64)>,
        now_wall: f64,
        now_mono: f64,
    ) {
        let active_log_paths: HashSet<PathBuf> = seen_daemon_keys
            .iter()
            .filter_map(|k| self.daemons.get(k))
            .map(|d| d.log_path.clone())
            .filter(|p| !p.as_os_str().is_empty())
            .collect();
        let active_server_sharers: Vec<(DaemonKey, PathBuf, f64, u32)> = seen_daemon_keys
            .iter()
            .filter_map(|k| self.daemons.get(k))
            .filter(|d| !d.log_path.as_os_str().is_empty() && !d.client_mode_recorded)
            .map(|d| (d.daemon_key, d.log_path.clone(), d.start_wall, d.pid))
            .collect();
        let active_artifact_dirs: HashSet<PathBuf> = seen_daemon_keys
            .iter()
            .filter_map(|k| self.daemons.get(k))
            .filter(|d| !d.client_mode_recorded)
            .map(|d| d.artifact_dir.clone())
            .filter(|p| !p.as_os_str().is_empty())
            .collect();
        let all_keys: Vec<DaemonKey> = self.daemons.keys().copied().collect();
        for d_key in all_keys {
            if seen_daemon_keys.contains(&d_key) {
                continue;
            }
            let mut new_events = Vec::new();
            if let Some(d) = self.daemons.get_mut(&d_key).filter(|d| !d.exited) {
                d.exited = true;
                Self::clear_disallowed_daemon_art_dir(d);
                if self.enable_log && !d.log_path.as_os_str().is_empty() {
                    if !active_log_paths.contains(&d.log_path) {
                        Self::poll_daemon_log(
                            d,
                            now_wall,
                            false,
                            (false, false),
                            &HashSet::new(),
                            &mut new_events,
                        );
                    } else {
                        let mut has_newer_server_sharer = false;
                        let mut has_older_server_sharer = false;
                        let mut active_sharer_pids = HashSet::new();
                        for (ok, olp, osw, opid) in &active_server_sharers {
                            if *ok == d_key || *olp != d.log_path {
                                continue;
                            }
                            active_sharer_pids.insert(*opid);
                            if *osw > d.start_wall || (*osw == d.start_wall && *opid > d.pid) {
                                has_newer_server_sharer = true;
                            } else {
                                has_older_server_sharer = true;
                            }
                        }
                        if !has_newer_server_sharer
                            || d.tailer.as_ref().is_some_and(|t| t.offset > 0)
                        {
                            Self::poll_daemon_log(
                                d,
                                now_wall,
                                true,
                                (has_older_server_sharer, false),
                                &active_sharer_pids,
                                &mut new_events,
                            );
                        }
                    }
                }
                Self::emit_ready_event_if_needed(d, &mut new_events);
                let can_harvest_exit = !d.client_mode_recorded
                    && !d.artifact_dir.as_os_str().is_empty()
                    && !active_artifact_dirs.contains(&d.artifact_dir);
                if can_harvest_exit {
                    Self::backfill_daemon_version_from_stats(d);
                }
                let ini_still_owned = d.ini_path.as_ref().is_some_and(|p| {
                    p.exists()
                        && self
                            .last_active_by_ini
                            .iter()
                            .find(|(owned, _)| same_file(owned, p))
                            .is_none_or(|(_, &owner)| owner == d_key)
                });
                let specific_hint = d.exit_reason_hint.clone().or_else(|| {
                    d.tailer
                        .as_ref()
                        .and_then(|t| t.exit_reason_hint.as_deref())
                        .filter(|&h| h != "clean_unknown")
                        .map(str::to_string)
                });
                let reason = specific_hint.unwrap_or_else(|| {
                    if ini_still_owned {
                        "killed_or_crashed".to_string()
                    } else {
                        "clean_unknown".to_string()
                    }
                });
                d.exit_reason = Some(reason.clone());
                new_events.push((now_wall, format!("EXITED(pid={}, reason={})", d.pid, reason)));
                if can_harvest_exit {
                    let joined: Vec<&EmulatorNetsimRecord> = self
                        .emulator_records
                        .values()
                        .filter(|e| e.daemon_key == Some(d_key))
                        .collect();
                    self.pcap_mgr.poll_and_manage_daemon(d, &joined, now_mono, true);
                }
                // Nothing reads an exited daemon's log or stats again.
                d.tailer = None;
                d.stats_cache = None;
                self.pcap_mgr.forget_daemon(d_key);
            }
            for (ts, ev) in new_events {
                self.record_event(ts, ev);
            }
        }
    }

    /// Samples loopback gRPC sockets to active daemons and updates each daemon's
    /// active socket count.
    fn poll_active_daemon_sockets(
        &mut self,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    ) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
        let active_ports: HashSet<u16> =
            self.daemons.values().filter(|d| !d.exited).filter_map(|d| d.grpc_port).collect();
        let daemon_pids: HashSet<u32> =
            self.daemons.values().filter(|d| !d.exited).map(|d| d.pid).collect();
        let port_to_conns =
            self.collect_sockets(&active_ports, emu_groups, &daemon_pids, true).unwrap_or_default();

        for d in self.daemons.values_mut() {
            d.joined_emulator_count = 0;
            d.active_socket_conns =
                d.grpc_port.and_then(|p| port_to_conns.get(&p).map(|v| v.len())).unwrap_or(0);
        }
        port_to_conns
    }

    /// Holds or settles session emulators missing from this tick's host scan and
    /// returns the count of held missing emulators for single-emulator fallback.
    fn settle_missing_emulators(
        &mut self,
        live_emulators: &[LiveEmulatorEntry],
        connected_devices: &HashMap<String, String>,
        now_mono: f64,
    ) -> usize {
        // As in the Python monitor, a session emulator missing from this tick's
        // host scan stays in the session. It keeps its last state while held,
        // then goes through settle_unjoined_emulator as of its first missed
        // tick. One whose serial now belongs to another live emulator was
        // replaced and settles at once. adb can keep an 'offline' entry for an
        // emulator that was killed, so only other adb states hold it.
        let live_keys: HashSet<&str> =
            live_emulators.iter().map(|e| e.storage_key.as_str()).collect();
        let live_serials: HashSet<&str> =
            live_emulators.iter().map(|e| e.serial.as_str()).collect();
        let mut held_count = 0;
        for (key, rec) in self.emulator_records.iter_mut() {
            // A live record's miss run is cleared in upsert_live_emulator_record,
            // after a worker change has used it to end the previous run.
            if live_keys.contains(key.as_str()) {
                continue;
            }
            // Settling again could change a settled state, e.g. DISCONNECTED
            // to FAILED(launcher_abort) once the daemon exits.
            if rec.is_inactive() {
                continue;
            }
            let adb_live = connected_devices.get(&rec.serial).is_some_and(|s| s != "offline");
            let exited = Self::record_missed_tick(rec, adb_live, now_mono);
            if !exited && !live_serials.contains(rec.serial.as_str()) {
                if !rec.no_netsim && rec.state != "DISABLED" {
                    held_count += 1;
                }
                continue;
            }
            if rec.no_netsim {
                rec.missed_ticks = SETTLE_AFTER_MISSED_TICKS;
                continue;
            }
            rec.adb_online_since_mono = None;
            let prev_exit = rec
                .daemon_key
                .and_then(|dk| self.daemons.get(&dk))
                .and_then(|d| d.exit_reason.as_deref());
            let exit_mono = rec.missing_since_mono.unwrap_or(now_mono);
            let worker_gone = true;
            Self::settle_unjoined_emulator(rec, prev_exit, worker_gone, exit_mono);
        }
        // A held emulator may still be running, so it keeps the fallback below
        // from giving its events to the other one.
        held_count
    }

    /// Updates each live emulator's record, pairs it with a daemon via endpoint,
    /// socket, or single-daemon fallback, and ingests daemon log and socket state.
    fn update_live_emulators(
        &mut self,
        live_emulators: &[LiveEmulatorEntry],
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        connected_devices: &HashMap<String, String>,
        port_to_conns: &HashMap<u16, Vec<LoopbackGrpcConn>>,
        held_count: usize,
        (now_wall, now_mono): (f64, f64),
    ) {
        let mut active_daemon_keys: Vec<DaemonKey> = self
            .daemons
            .values()
            .filter(|d| !d.exited && !d.client_mode_recorded)
            .map(|d| d.daemon_key)
            .collect();
        active_daemon_keys
            .sort_by_key(|dk| (!self.daemons.get(dk).is_some_and(|d| d.same_user), *dk));

        let mut active_entries: Vec<(&LiveEmulatorEntry, Option<DaemonKey>, bool)> =
            Vec::with_capacity(live_emulators.len());
        for entry in live_emulators {
            let g_info = emu_groups.get(&entry.serial);
            let join_match = self.classify_joined_daemon(
                &entry.serial,
                g_info,
                &active_daemon_keys,
                port_to_conns,
            );
            let prev_daemon_key =
                self.emulator_records.get(&entry.storage_key).and_then(|r| r.daemon_key);
            let rec = Self::upsert_live_emulator_record(
                &mut self.emulator_records,
                &self.daemons,
                entry,
                g_info,
                connected_devices,
                (now_wall, now_mono),
            );
            if prev_daemon_key.is_some() && rec.daemon_key.is_none() {
                self.provisional_daemons.remove(&entry.storage_key);
            }
            if rec.no_netsim {
                rec.state = "DISABLED".to_string();
                rec.daemon_key = None;
                rec.netsim_daemon_ready_s = None;
                self.provisional_daemons.remove(&entry.storage_key);
                continue;
            }
            let worker_exited = rec.worker_pid > 0 && !Self::group_contains_worker(g_info, rec);
            if worker_exited && rec.is_settled() {
                continue;
            }
            let target_port = Self::extract_target_port(g_info);
            let had_prior_attach = rec.first_transport_ts.is_some()
                || !rec.chip_attach_s.is_empty()
                || rec.unknown_chip_count > 0
                || matches!(
                    rec.state.as_str(),
                    "CONNECTED" | "CONNECTED_NO_CHIPS" | "DISCONNECTED"
                );
            let mut joined_key = join_match.map(|(dk, _)| dk);
            let mut joined_via_fallback = join_match.is_some_and(|(_, fb)| fb);
            if worker_exited && joined_key != rec.daemon_key {
                joined_key = None;
                joined_via_fallback = false;
            } else if let Some((dk, true)) = join_match {
                let port_conflicts = target_port
                    .zip(self.daemons.get(&dk).and_then(|d| d.grpc_port))
                    .is_some_and(|(tp, dp)| tp != dp);
                if port_conflicts {
                    joined_key = None;
                    joined_via_fallback = false;
                } else if let Some(old_dk) = rec.daemon_key.filter(|&old_dk| old_dk != dk) {
                    if had_prior_attach
                        && !Self::is_replacement_daemon(&self.daemons, old_dk, dk, target_port)
                    {
                        joined_key = None;
                        joined_via_fallback = false;
                    }
                }
            }
            if let Some(dk) = joined_key {
                if joined_via_fallback {
                    self.provisional_daemons.insert(entry.storage_key.clone(), dk);
                } else {
                    self.provisional_daemons.remove(&entry.storage_key);
                }
            } else {
                let worker_gone = worker_exited || !emu_groups.contains_key(&entry.serial);
                let is_provisional = self.provisional_daemons.get(&entry.storage_key).copied();
                if !had_prior_attach && rec.connected_total_s == 0.0 && rec.disconnect_count == 0 {
                    let drop_provisional = rec
                        .daemon_key
                        .and_then(|old_dk| self.daemons.get(&old_dk).map(|old_d| (old_dk, old_d)))
                        .is_some_and(|(old_dk, old_d)| {
                            if target_port.zip(old_d.grpc_port).is_some_and(|(tp, dp)| tp != dp) {
                                return true;
                            }
                            if worker_gone || old_d.exited || is_provisional != Some(old_dk) {
                                return false;
                            }
                            let pre_existing_bystander = old_d.ready_ts.is_some_and(|rts| {
                                rec.worker_start_ts > 0.0
                                    && rts < rec.worker_start_ts - LOG_FLOOR_SLACK_S
                            });
                            let relevant_others: Vec<DaemonKey> = active_daemon_keys
                                .iter()
                                .copied()
                                .filter(|&dk| {
                                    dk != old_dk
                                        && (!old_d.same_user
                                            || self.daemons.get(&dk).is_some_and(|d| d.same_user))
                                })
                                .collect();
                            let overlapping_restart = !pre_existing_bystander
                                && !relevant_others.is_empty()
                                && relevant_others.iter().all(|dk| {
                                    self.daemons.get(dk).is_some_and(|new_d| {
                                        new_d.same_user == old_d.same_user
                                            && Self::has_replacement_lineage(
                                                old_d,
                                                new_d,
                                                target_port,
                                            )
                                    })
                                });
                            !overlapping_restart
                        });
                    if drop_provisional {
                        rec.daemon_key = None;
                        rec.netsim_daemon_ready_s = None;
                        self.provisional_daemons.remove(&entry.storage_key);
                    }
                }
                let prev_exit = rec
                    .daemon_key
                    .and_then(|dk| self.daemons.get(&dk))
                    .and_then(|d| d.exit_reason.as_deref());
                Self::settle_unjoined_emulator(rec, prev_exit, worker_gone, now_mono);
                if rec.is_settled() {
                    self.provisional_daemons.remove(&entry.storage_key);
                    continue;
                }
            }
            active_entries.push((entry, joined_key, worker_exited));
        }

        let mut display_name_counts: HashMap<String, usize> = HashMap::new();
        for &(entry, _, _) in &active_entries {
            if let Some(dn) = emu_groups
                .get(&entry.serial)
                .and_then(|g| g.display_name.as_ref())
                .or_else(|| {
                    self.emulator_records
                        .get(&entry.storage_key)
                        .and_then(|r| r.device_name.as_ref())
                })
                .or_else(|| emu_groups.get(&entry.serial).and_then(|g| g.avd_id.as_ref()))
            {
                *display_name_counts.entry(dn.clone()).or_insert(0) += 1;
            }
        }

        let single_live = active_entries.len() + held_count == 1;
        // With one live emulator, tailer events that match no device are its
        // own unless they name another session emulator, such as one that
        // exited while sharing the daemon.
        let other_session_names: HashSet<String> = match active_entries.as_slice() {
            [(only, _, _)] if single_live => self
                .emulator_records
                .iter()
                .filter(|(k, _)| *k != &only.storage_key)
                .filter_map(|(_, r)| r.device_name.clone())
                .filter(|n| !n.is_empty())
                .collect(),
            _ => HashSet::new(),
        };

        let ctx = LiveEmulatorTickCtx {
            emu_groups,
            port_to_conns,
            display_name_counts,
            other_session_names,
            single_live,
            now_wall,
            now_mono,
        };

        for (entry, joined_key, worker_exited) in active_entries {
            let Some(dk) = joined_key else {
                continue;
            };
            let Some(rec) = self.emulator_records.get_mut(&entry.storage_key) else {
                continue;
            };
            if let Some(old_dk) = rec.daemon_key.filter(|&old_dk| old_dk != dk) {
                rec.chip_attach_s.clear();
                rec.chip_ids.clear();
                rec.unknown_chip_count = 0;
                let was_bystander = !Self::is_replacement_daemon(&self.daemons, old_dk, dk, None)
                    || self.daemons.get(&old_dk).is_some_and(|od| {
                        !od.exited
                            || od.ready_ts.is_some_and(|rts| {
                                rec.worker_start_ts > 0.0
                                    && rts < rec.worker_start_ts - LOG_FLOOR_SLACK_S
                            })
                    });
                if was_bystander
                    && rec.first_transport_ts.is_none()
                    && rec.connected_total_s == 0.0
                    && rec.disconnect_count == 0
                {
                    rec.netsim_daemon_ready_s = None;
                }
            }
            rec.daemon_key = Some(dk);
            if let Some(d) = self.daemons.get_mut(&dk) {
                Self::sync_joined_emulator_with_daemon(rec, d, &entry.serial, worker_exited, &ctx);
            }
        }
    }

    /// Inserts or updates a live emulator's `EmulatorNetsimRecord`, resetting
    /// per-run fields when a new worker PID replaces an earlier worker.
    fn upsert_live_emulator_record<'a>(
        emulator_records: &'a mut BTreeMap<String, EmulatorNetsimRecord>,
        daemons: &BTreeMap<DaemonKey, DaemonRecord>,
        entry: &LiveEmulatorEntry,
        g_info: Option<&EmuGroupNetsimInfo>,
        connected_devices: &HashMap<String, String>,
        (now_wall, now_mono): (f64, f64),
    ) -> &'a mut EmulatorNetsimRecord {
        let worker_pid = g_info.map(|g| g.primary_pid).unwrap_or(entry.fallback_pid);
        let worker_ctime = Self::group_pid_ctime(g_info, worker_pid);
        // The start time is read from /proc only for a new record, changed PID, or PID reuse.
        let needs_start = emulator_records.get(&entry.storage_key).is_none_or(|r| {
            (worker_pid > 0 && r.worker_pid != worker_pid)
                || (worker_ctime > 0 && r.worker_create_time != worker_ctime)
        });
        let known_worker_start = (needs_start && worker_pid > 0)
            .then(|| get_proc_precise_start_time(worker_pid, worker_ctime as f64))
            .filter(|&ts| ts > 0.0);
        let worker_start_ts = known_worker_start.unwrap_or(now_wall);

        let rec = emulator_records.entry(entry.storage_key.clone()).or_insert_with(|| {
            EmulatorNetsimRecord {
                storage_key: entry.storage_key.clone(),
                serial: entry.serial.clone(),
                avd_id: g_info.and_then(|g| g.avd_id.clone()),
                device_name: g_info
                    .and_then(|g| g.display_name.clone().or_else(|| g.avd_id.clone())),
                worker_pid,
                worker_create_time: worker_ctime,
                group_pids: Vec::new(),
                group_procs: BTreeMap::new(),
                worker_start_ts,
                first_seen_mono: entry.first_seen_mono,
                min_log_ts: known_worker_start,
                no_netsim: g_info.map(|g| g.no_netsim).unwrap_or(false),
                state: "CONNECTING".to_string(),
                ambiguous: false,
                daemon_key: None,
                netsim_daemon_ready_s: None,
                first_transport_ts: None,
                netsim_connect_s: None,
                chip_attach_s: BTreeMap::new(),
                chip_ids: BTreeSet::new(),
                unknown_chip_count: 0,
                wifi_associated_s: None,
                disconnect_count: 0,
                connected_start_mono: None,
                connected_total_s: 0.0,
                adb_online_since_mono: None,
                missing_since_mono: None,
                missed_ticks: 0,
                prior_bare_snapshot: None,
            }
        });

        let same_pid_reused = g_info.is_some()
            && worker_pid > 0
            && rec.worker_pid == worker_pid
            && worker_ctime > 0
            && if rec.worker_create_time > 0 {
                rec.worker_create_time != worker_ctime
            } else {
                rec.worker_start_ts > 0.0 && (worker_ctime as f64) > rec.worker_start_ts + 2.0
            };
        let stash_bare_if_needed = |r: &EmulatorNetsimRecord| {
            (entry.storage_key == entry.serial).then(|| {
                let prev_exit = r
                    .daemon_key
                    .and_then(|dk| daemons.get(&dk))
                    .and_then(|d| d.exit_reason.as_deref());
                Box::new(r.settled_bare_snapshot(prev_exit, now_mono))
            })
        };
        if same_pid_reused {
            let prior_bare = stash_bare_if_needed(rec);
            rec.reset_for_new_worker(
                worker_pid,
                worker_ctime,
                worker_start_ts,
                entry.first_seen_mono,
                g_info.map(|g| g.no_netsim).unwrap_or(false),
                now_mono,
            );
            rec.prior_bare_snapshot = prior_bare;
        } else if worker_pid > 0 && rec.worker_pid != worker_pid {
            let is_worker_upgrade = !rec.is_settled() && Self::group_contains_worker(g_info, rec);
            if !rec.is_settled() && (rec.worker_pid == 0 || is_worker_upgrade) {
                rec.worker_pid = worker_pid;
                rec.worker_create_time = worker_ctime;
                if rec.state == "CONNECTING" {
                    rec.worker_start_ts = worker_start_ts;
                    if rec.min_log_ts.is_some() || known_worker_start.is_some() {
                        rec.min_log_ts = known_worker_start.or(Some(worker_start_ts));
                    }
                }
            } else if Self::record_has_member(rec, worker_pid, worker_ctime) {
                // Keep the worker PID and start timestamp when the VM worker exits
                // first during shutdown and primary_pid falls back to the launcher
                // or GUI companion.
            } else {
                let prior_bare = stash_bare_if_needed(rec);
                rec.reset_for_new_worker(
                    worker_pid,
                    worker_ctime,
                    worker_start_ts,
                    entry.first_seen_mono,
                    g_info.map(|g| g.no_netsim).unwrap_or(false),
                    now_mono,
                );
                rec.prior_bare_snapshot = prior_bare;
            }
        } else {
            if rec.worker_create_time == 0 && worker_ctime > 0 {
                rec.worker_create_time = worker_ctime;
            }
            if rec.state == "FAILED(worker_exit_no_connect)" && g_info.is_some() && !rec.no_netsim {
                rec.state = "CONNECTING".to_string();
            }
        }
        if let Some(g) = g_info {
            for &p in &g.pids {
                if !rec.group_pids.contains(&p) {
                    rec.group_pids.push(p);
                }
                let p_ct = Self::group_pid_ctime(Some(g), p);
                rec.group_procs
                    .entry(p)
                    .and_modify(|ct| {
                        if *ct == 0 && p_ct > 0 {
                            *ct = p_ct;
                        }
                    })
                    .or_insert(p_ct);
            }
        }
        if worker_pid > 0 {
            if !rec.group_pids.contains(&worker_pid) {
                rec.group_pids.push(worker_pid);
            }
            rec.group_procs
                .entry(worker_pid)
                .and_modify(|ct| {
                    if *ct == 0 && worker_ctime > 0 {
                        *ct = worker_ctime;
                    }
                })
                .or_insert(worker_ctime);
        }
        rec.missing_since_mono = None;
        rec.missed_ticks = 0;
        rec.serial = entry.serial.clone();
        if let Some(g) = g_info {
            if g.avd_id.is_some() {
                rec.avd_id = g.avd_id.clone();
            }
            // pid_<pid>.ini, the source of the display name, is deleted
            // while the worker shuts down, so never trade a known name for
            // the AVD id fallback.
            if let Some(dn) = &g.display_name {
                rec.device_name = Some(dn.clone());
            } else if rec.device_name.is_none() {
                rec.device_name = g.avd_id.clone();
            }
            if g.no_netsim {
                rec.no_netsim = true;
            }
        }
        if !rec.no_netsim {
            if connected_devices.get(&entry.serial).map(|s| s.as_str()) == Some("device") {
                if rec.adb_online_since_mono.is_none() {
                    rec.adb_online_since_mono = Some(now_mono);
                }
            } else {
                rec.adb_online_since_mono = None;
            }
        }
        rec
    }

    fn extract_target_port(g_info: Option<&EmuGroupNetsimInfo>) -> Option<u16> {
        g_info
            .and_then(|g| g.grpc_endpoint.as_ref())
            .and_then(|ep| ep.rsplit(':').next())
            .and_then(|p| p.parse::<u16>().ok())
            .filter(|&p| p > 0)
    }

    fn effective_daemon_instance(d: &DaemonRecord) -> u32 {
        d.ini_path
            .as_deref()
            .and_then(extract_ini_instance_num)
            .or(d.instance_num)
            .unwrap_or(DEFAULT_NETSIM_INSTANCE)
    }

    fn has_replacement_lineage(
        old_d: &DaemonRecord,
        new_d: &DaemonRecord,
        target_port: Option<u16>,
    ) -> bool {
        if new_d.start_wall <= old_d.start_wall {
            return false;
        }
        if Self::effective_daemon_instance(old_d) != Self::effective_daemon_instance(new_d) {
            return false;
        }
        if target_port.zip(new_d.grpc_port).is_some_and(|(tp, dp)| tp != dp) {
            return false;
        }
        if !old_d.artifact_dir.as_os_str().is_empty()
            && !new_d.artifact_dir.as_os_str().is_empty()
            && !same_file(&old_d.artifact_dir, &new_d.artifact_dir)
        {
            return false;
        }
        true
    }

    fn is_replacement_daemon(
        daemons: &BTreeMap<DaemonKey, DaemonRecord>,
        old_dk: DaemonKey,
        new_dk: DaemonKey,
        target_port: Option<u16>,
    ) -> bool {
        let (Some(old_d), Some(new_d)) = (daemons.get(&old_dk), daemons.get(&new_dk)) else {
            return false;
        };
        old_d.exited && Self::has_replacement_lineage(old_d, new_d, target_port)
    }

    /// Selects the active daemon an emulator connects to, returning `(key, is_fallback)`:
    /// first by matching its configured gRPC endpoint port, then by an established
    /// loopback socket, and finally by single-daemon fallback (`is_fallback == true`).
    fn classify_joined_daemon(
        &self,
        serial: &str,
        g_info: Option<&EmuGroupNetsimInfo>,
        active_daemon_keys: &[DaemonKey],
        port_to_conns: &HashMap<u16, Vec<LoopbackGrpcConn>>,
    ) -> Option<(DaemonKey, bool)> {
        let target_port = Self::extract_target_port(g_info);
        if let Some(tp) = target_port {
            for &dk in active_daemon_keys {
                if self.daemons.get(&dk).and_then(|d| d.grpc_port) == Some(tp) {
                    return Some((dk, false));
                }
            }
        }
        for &dk in active_daemon_keys {
            let has_conn = self
                .daemons
                .get(&dk)
                .and_then(|d| d.grpc_port)
                .and_then(|gp| port_to_conns.get(&gp))
                .is_some_and(|conns| conns.iter().any(|c| c.serial == serial));
            if has_conn {
                return Some((dk, false));
            }
        }
        let port_compatible = |dk: &DaemonKey| {
            target_port
                .zip(self.daemons.get(dk).and_then(|d| d.grpc_port))
                .is_none_or(|(tp, dp)| tp == dp)
        };
        let mut same_user_daemons = active_daemon_keys
            .iter()
            .copied()
            .filter(|dk| self.daemons.get(dk).is_some_and(|d| d.same_user));
        if let Some(only_own) = same_user_daemons.next() {
            if same_user_daemons.next().is_none() && port_compatible(&only_own) {
                return Some((only_own, true));
            }
            return None;
        }
        if super::owner_uid() == Some(0)
            && active_daemon_keys.len() == 1
            && port_compatible(&active_daemon_keys[0])
        {
            return Some((active_daemon_keys[0], true));
        }
        None
    }

    #[cfg(test)]
    fn find_joined_daemon(
        &self,
        serial: &str,
        g_info: Option<&EmuGroupNetsimInfo>,
        active_daemon_keys: &[DaemonKey],
        port_to_conns: &HashMap<u16, Vec<LoopbackGrpcConn>>,
    ) -> Option<DaemonKey> {
        self.classify_joined_daemon(serial, g_info, active_daemon_keys, port_to_conns)
            .map(|(dk, _)| dk)
    }

    /// Matches a joined emulator against its daemon's sockets and log tailer
    /// events, updating transport latency, attached chips, and connection state.
    fn sync_joined_emulator_with_daemon(
        rec: &mut EmulatorNetsimRecord,
        d: &mut DaemonRecord,
        serial: &str,
        worker_exited: bool,
        ctx: &LiveEmulatorTickCtx<'_>,
    ) {
        if rec.netsim_daemon_ready_s.is_none() {
            if let Some(r_ts) = d.ready_ts {
                let base_ts = d.start_wall.min(rec.worker_start_ts);
                rec.netsim_daemon_ready_s = Some((r_ts - base_ts).max(0.0));
            } else if d.netsim_daemon_ready_s.is_some() {
                rec.netsim_daemon_ready_s = d.netsim_daemon_ready_s;
            }
        }

        let all_daemon_conns = d.grpc_port.and_then(|gp| ctx.port_to_conns.get(&gp));
        let emu_conns: Vec<LoopbackGrpcConn> = all_daemon_conns
            .map(|v| v.iter().filter(|c| c.serial == serial).cloned().collect())
            .unwrap_or_default();
        let emu_peer_ports: HashSet<u16> = emu_conns.iter().map(|c| c.peer_port).collect();
        let other_peer_ports: HashSet<u16> = all_daemon_conns
            .map(|v| v.iter().filter(|c| c.serial != serial).map(|c| c.peer_port).collect())
            .unwrap_or_default();

        if !emu_conns.is_empty() && rec.first_transport_ts.is_none() {
            rec.first_transport_ts = Some(ctx.now_wall);
        }

        let dev_name = rec.device_name.clone().unwrap_or_default();
        let shared_dev_name = !dev_name.is_empty()
            && ctx.display_name_counts.get(&dev_name).copied().unwrap_or(0) > 1;
        let mut matched_shared_name_without_peer_port = false;

        if let Some(ref tailer) = d.tailer {
            matched_shared_name_without_peer_port = Self::ingest_tailer_events_for_emulator(
                rec,
                tailer,
                &dev_name,
                (&emu_peer_ports, &other_peer_ports),
                shared_dev_name,
                ctx.single_live,
                &ctx.other_session_names,
            );
        } else {
            rec.unknown_chip_count = 0;
        }
        rec.ambiguous =
            shared_dev_name && (emu_peer_ports.is_empty() || matched_shared_name_without_peer_port);

        if let Some(ft) = rec.first_transport_ts {
            rec.netsim_connect_s = Some((ft - rec.worker_start_ts).max(0.0));
        }

        let prev_state = rec.state.clone();
        let has_chips = !rec.chip_attach_s.is_empty() || rec.unknown_chip_count > 0;

        if !emu_conns.is_empty() || (has_chips && !d.exited) {
            if !has_chips
                && rec
                    .adb_online_since_mono
                    .is_some_and(|t| (ctx.now_mono - t) >= NO_CHIPS_ADB_ONLINE_THRESHOLD_S)
            {
                rec.state = "CONNECTED_NO_CHIPS".to_string();
            } else {
                rec.state = "CONNECTED".to_string();
            }
            if rec.connected_start_mono.is_none() {
                rec.connected_start_mono = Some(ctx.now_mono);
            }
        } else if prev_state == "CONNECTED" || prev_state == "CONNECTED_NO_CHIPS" {
            rec.state = "DISCONNECTED".to_string();
            rec.disconnect_count += 1;
            if let Some(st) = rec.connected_start_mono.take() {
                rec.connected_total_s += (ctx.now_mono - st).max(0.0);
            }
        } else if !rec.is_settled() {
            if worker_exited || !ctx.emu_groups.contains_key(serial) {
                rec.state = "FAILED(worker_exit_no_connect)".to_string();
            } else if (ctx.now_mono - rec.first_seen_mono) >= CONNECT_STALL_TIMEOUT_S {
                rec.state = "STALLED".to_string();
            } else {
                rec.state = "CONNECTING".to_string();
            }
        }
        if !rec.is_settled() {
            d.joined_emulator_count += 1;
        }
    }

    /// Scans a daemon's log tailer connections and chip-add events for entries
    /// belonging to `rec`, returning whether any chip matched a shared device
    /// name without `peer_port` disambiguation.
    fn ingest_tailer_events_for_emulator(
        rec: &mut EmulatorNetsimRecord,
        tailer: &NetsimLogTailer,
        dev_name: &str,
        (emu_peer_ports, other_peer_ports): (&HashSet<u16>, &HashSet<u16>),
        shared_dev_name: bool,
        single_live: bool,
        other_session_names: &HashSet<String>,
    ) -> bool {
        let log_floor = rec.min_log_ts.map(|t| t - LOG_FLOOR_SLACK_S);
        let mut unknown_adds = 0usize;
        let mut matched_shared_name_without_peer_port = false;
        for (c_ts, _, c_dev) in &tailer.connections {
            if log_floor.is_some_and(|fl| *c_ts < fl) {
                continue;
            }
            let matches_conn = (!shared_dev_name && c_dev == dev_name)
                || (dev_name.is_empty() && single_live && !other_session_names.contains(c_dev));
            if matches_conn && rec.first_transport_ts.is_none_or(|t| *c_ts < t) {
                rec.first_transport_ts = Some(*c_ts);
            }
        }
        for add_ev in &tailer.chip_adds {
            if log_floor.is_some_and(|fl| add_ev.ts < fl) {
                continue;
            }
            if add_ev.peer_port.is_some_and(|pp| other_peer_ports.contains(&pp)) {
                continue;
            }
            let peer_matched = add_ev.peer_port.is_some_and(|pp| emu_peer_ports.contains(&pp));
            let matches_dev =
                if shared_dev_name && add_ev.peer_port.is_some() && !emu_peer_ports.is_empty() {
                    peer_matched
                } else {
                    peer_matched
                        || add_ev.device_name == dev_name
                        || (single_live && !other_session_names.contains(&add_ev.device_name))
                };
            if !matches_dev {
                continue;
            }
            if shared_dev_name && !peer_matched && add_ev.device_name == dev_name {
                matched_shared_name_without_peer_port = true;
            }
            if !add_ev.device_name.is_empty() && rec.device_name.is_none() {
                rec.device_name = Some(add_ev.device_name.clone());
            }
            if rec.first_transport_ts.is_none_or(|t| add_ev.ts < t) {
                rec.first_transport_ts = Some(add_ev.ts);
            }
            if let Some(ref cid) = add_ev.chip_id {
                rec.chip_ids.insert(cid.clone());
            }
            if add_ev.kind != "?" {
                let delta = (add_ev.ts - rec.worker_start_ts).max(0.0);
                rec.chip_attach_s.entry(add_ev.kind.clone()).or_insert((delta, false));
            } else {
                unknown_adds += 1;
            }
        }
        rec.unknown_chip_count = unknown_adds;
        matched_shared_name_without_peer_port
    }

    /// Evaluates daemon orphan timers, resolves single-emulator Wi-Fi association
    /// and `netsim_session_stats.json` chip durations, and runs periodic pcap
    /// management.
    fn finalize_daemons_and_harvest_pcaps(&mut self, now_wall: f64, now_mono: f64) {
        let d_keys: Vec<DaemonKey> = self.daemons.keys().copied().collect();
        for dk in d_keys {
            let mut all_count = 0usize;
            let mut live_single: Option<(String, f64, Option<f64>, bool)> = None;
            let mut live_count = 0usize;
            for e in self
                .emulator_records
                .values()
                .filter(|e| e.daemon_key == Some(dk) && !e.no_netsim && e.state != "DISABLED")
            {
                all_count += 1;
                if !e.is_settled() {
                    live_count += 1;
                    if live_count == 1 {
                        let need_stats = e.chip_attach_s.is_empty()
                            || e.chip_attach_s.values().any(|&(_, approx)| approx);
                        live_single = Some((
                            e.storage_key.clone(),
                            e.worker_start_ts,
                            e.min_log_ts,
                            need_stats,
                        ));
                    } else {
                        live_single = None;
                    }
                }
            }
            let mut orphan_ev = None;
            if let Some(d) = self.daemons.get_mut(&dk) {
                if d.exited || d.client_mode_recorded {
                    if d.client_mode_recorded {
                        d.zero_devices_since_mono = None;
                        d.orphaned = false;
                    }
                    continue;
                }
                Self::backfill_daemon_version_from_stats(d);
                let has_any_dev = live_count > 0
                    || d.tailer.as_ref().is_some_and(|t| !t.created_devices.is_empty());
                if !has_any_dev {
                    let since = *d.zero_devices_since_mono.get_or_insert(now_mono);
                    if (now_mono - since) >= DAEMON_ORPHAN_TIMEOUT_S && !d.orphaned {
                        d.orphaned = true;
                        orphan_ev = Some(format!("ORPHANED(pid={})", d.pid));
                    }
                } else {
                    d.zero_devices_since_mono = None;
                    d.orphaned = false;
                }

                // Device-less log events (ApActor association and session stats)
                // belong to the sole emulator only when no other session
                // emulator also used this daemon.
                if all_count == 1 {
                    if let Some((s_key, w_start, min_log_ts, need_stats)) = live_single {
                        let log_floor = min_log_ts.map(|t| t - LOG_FLOOR_SLACK_S);
                        let wifi_ts = d.tailer.as_ref().and_then(|t| {
                            t.wifi_associations
                                .iter()
                                .copied()
                                .find(|&wt| log_floor.is_none_or(|fl| wt >= fl))
                        });
                        let mut stats_resolved = None;
                        if need_stats
                            && is_real_dir(&d.artifact_dir)
                            && !Self::is_disallowed_daemon_art_dir(
                                &d.artifact_dir,
                                d.same_user,
                                d.expected_uid,
                            )
                        {
                            let stats_path = d.artifact_dir.join("netsim_session_stats.json");
                            let unk_ts: Vec<f64> = d
                                .tailer
                                .as_ref()
                                .map(|t| {
                                    t.chip_adds
                                        .iter()
                                        .filter(|a| {
                                            a.kind == "?" && log_floor.is_none_or(|fl| a.ts >= fl)
                                        })
                                        .map(|a| a.ts)
                                        .collect()
                                })
                                .unwrap_or_default();
                            let stats_min_ts = d.start_wall.max(min_log_ts.unwrap_or(0.0));
                            let (_, resolved) = resolve_session_stats_chips_for_owner(
                                &stats_path,
                                &unk_ts,
                                live_count,
                                stats_min_ts,
                                &mut d.stats_cache,
                                d.same_user,
                                d.expected_uid,
                            );
                            stats_resolved = resolved;
                        }
                        if let Some(rec) = self.emulator_records.get_mut(&s_key) {
                            if let Some(wt) = wifi_ts {
                                rec.wifi_associated_s = Some((wt - w_start).max(0.0));
                            }
                            if let Some(res_map) = stats_resolved {
                                // Never trade an exact attach time for an estimate.
                                for (k, (ev_ts, is_approx)) in res_map {
                                    let val = ((ev_ts - w_start).max(0.0), is_approx);
                                    let slot = rec.chip_attach_s.entry(k).or_insert(val);
                                    if slot.1 {
                                        *slot = val;
                                    }
                                }
                            }
                        }
                    }
                }

                let all_joined: Vec<&EmulatorNetsimRecord> = self
                    .emulator_records
                    .values()
                    .filter(|e| e.daemon_key == Some(dk) && !e.no_netsim && e.state != "DISABLED")
                    .collect();
                self.pcap_mgr.poll_and_manage_daemon(d, &all_joined, now_mono, false);
            }
            if let Some(ev) = orphan_ev {
                self.record_event(now_wall, ev);
            }
        }
    }

    /// Turns off the captures this monitor enabled and harvests artifacts.
    /// Runs at most once; later calls (including the one from `Drop`) are no-ops.
    pub fn cleanup(&mut self) {
        if std::mem::replace(&mut self.cleaned_up, true) || !self.pcap_mgr.enabled {
            return;
        }
        self.pcap_mgr.cleanup(&mut self.daemons, &self.emulator_records);
    }
}

#[cfg(test)]
mod tests {
    use super::proc::tests::env_of;
    use super::sockets::tests::{LSOF_OUT, emu_groups_with};
    use super::*;
    use std::time::Duration;

    #[test]
    fn test_netsim_tracker_standalone_and_orphaned_and_exit() {
        let dir = tempfile::tempdir().unwrap();
        let art_dir = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_dir).unwrap();
        let log_path = art_dir.join("netsim_stderr.log");
        std::fs::write(
            &log_path,
            "netsim I 09-25 22:37:05.612 netsimd.rs:451 - Netsim Version: 1.0.24, OS: linux, Arch: x86_64\n\
             netsim I 09-25 22:37:05.683 netsimd.rs:744 - Wrote to INI file /tmp/netsim.ini\n",
        )
        .unwrap();

        let mut env = HashMap::new();
        env.insert("TMPDIR".to_string(), dir.path().to_string_lossy().to_string());
        env.insert("USER".to_string(), "test".to_string());
        let procs = vec![NetsimDaemonProc {
            pid: 5_009_001,
            parent: Some(1),
            parent_name: None,
            start_time: 1790375825,
            exe: Some(PathBuf::from("/sdk/emulator/bin/netsimd")),
            cmd: vec!["/sdk/emulator/bin/netsimd".to_string(), "--no-shutdown".to_string()],
            environ: env,
            cpu: 1.0,
            ram_mb: 30.0,
            uid: None,
            same_user: true,
        }];

        let mut tracker = NetsimTracker::new(None, 500, true, dir.path());
        tracker.host_os = HostOs::Linux;
        tracker.custom_ini_dirs = vec![dir.path().to_path_buf()];
        tracker.custom_proc_locks = Some(String::new());

        tracker.update(&procs, &HashMap::new(), &[], &HashMap::new(), 1790375826.0, 10.0);
        assert!(
            tracker
                .timeline
                .iter()
                .any(|(_, e)| e.contains("STARTED(pid=5009001, spawner=STANDALONE)"))
        );
        assert!(tracker.timeline.iter().any(|(_, e)| e.contains("READY(")));

        tracker.update(&procs, &HashMap::new(), &[], &HashMap::new(), 1790375860.0, 45.0);
        assert!(tracker.timeline.iter().any(|(_, e)| e.contains("ORPHANED(pid=5009001)")));

        let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
        use std::io::Write;
        writeln!(f, "netsim I 09-25 22:37:50.000 netsimd.rs:800 - Shutting down gracefully")
            .unwrap();

        tracker.update(&[], &HashMap::new(), &[], &HashMap::new(), 1790375870.0, 55.0);
        assert!(
            tracker
                .timeline
                .iter()
                .any(|(_, e)| e.contains("EXITED(pid=5009001, reason=sigterm_sigint)"))
        );
    }

    /// PIDs above Linux's PID_MAX_LIMIT (2^22): no `/proc` lookup or liveness
    /// probe reaches a real process, so `start_wall` equals the create time.
    const DAEMON_A: u32 = 5_000_100;
    const DAEMON_B: u32 = 5_000_200;
    const DEAD_PID: u32 = 5_000_999;
    const START: u64 = 1_790_375_825;

    fn write_ini(path: &Path, content: &str, mtime: u64) {
        std::fs::write(path, content).unwrap();
        std::fs::File::options()
            .write(true)
            .open(path)
            .unwrap()
            .set_modified(UNIX_EPOCH + Duration::from_secs(mtime))
            .unwrap();
    }

    fn netsimd(pid: u32, start: u64, tmp: &Path) -> NetsimDaemonProc {
        NetsimDaemonProc {
            pid,
            parent: Some(1),
            start_time: start,
            exe: Some(PathBuf::from("/sdk/emulator/netsimd")),
            cmd: vec!["/sdk/emulator/netsimd".to_string()],
            environ: env_of(&[("TMPDIR", tmp.to_str().unwrap()), ("USER", "test")]),
            same_user: true,
            ..Default::default()
        }
    }

    /// A tracker that scans only `dirs` and sees no `/proc/locks` entries, as
    /// on macOS and Windows.
    fn tracker_for(dirs: &[&Path], out: &Path) -> NetsimTracker {
        let mut tracker = NetsimTracker::new(None, 500, false, out);
        tracker.host_os = HostOs::Linux;
        tracker.custom_ini_dirs = if dirs.is_empty() {
            vec![out.to_path_buf()]
        } else {
            dirs.iter().map(|d| d.to_path_buf()).collect()
        };
        tracker.custom_proc_locks = Some(String::new());
        tracker
    }

    fn tick(tracker: &mut NetsimTracker, procs: &[NetsimDaemonProc], now_wall: u64) {
        let now_mono = (now_wall - START) as f64;
        tracker.update(procs, &HashMap::new(), &[], &HashMap::new(), now_wall as f64, now_mono);
    }

    fn count_events(tracker: &NetsimTracker, prefix: &str) -> usize {
        tracker.timeline.iter().filter(|(_, e)| e.starts_with(prefix)).count()
    }

    #[test]
    fn test_pid_exists() {
        assert!(pid_exists(std::process::id()));
        assert!(!pid_exists(DEAD_PID));
    }

    #[cfg(unix)]
    #[test]
    fn test_same_file_resolves_symlinks() {
        let root = tempfile::tempdir().unwrap();
        let real = root.path().join("real");
        std::fs::create_dir_all(&real).unwrap();
        std::fs::write(real.join("netsim.ini"), "").unwrap();
        std::fs::write(root.path().join("other.ini"), "").unwrap();
        let alias = root.path().join("alias");
        std::os::unix::fs::symlink(&real, &alias).unwrap();
        assert!(same_file(&alias.join("netsim.ini"), &real.join("netsim.ini")));
        assert!(!same_file(&real.join("netsim.ini"), &root.path().join("other.ini")));
        // Missing files compare by path.
        assert!(same_file(Path::new("/nonexistent/a.ini"), Path::new("/nonexistent/a.ini")));
        assert!(!same_file(Path::new("/nonexistent/a.ini"), Path::new("/nonexistent/b.ini")));
    }

    #[cfg(unix)]
    #[test]
    fn test_discover_ini_files_dedupes_aliases_and_honors_custom_dirs() {
        let root = tempfile::tempdir().unwrap();
        let real = root.path().join("real");
        std::fs::create_dir_all(&real).unwrap();
        for name in [
            "netsim.ini",
            "netsim_2.ini",
            "netsim.ini.lock",
            "netsim_2.ini.init.lock",
            "netsim_x.ini",
            "pid_42.ini",
        ] {
            std::fs::write(real.join(name), "").unwrap();
        }
        // macOS reaches one dir as both /tmp and /private/tmp, for example.
        let alias = root.path().join("alias");
        std::os::unix::fs::symlink(&real, &alias).unwrap();
        // A daemon's own TMPDIR is not searched while custom dirs are set.
        let other = root.path().join("other");
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(other.join("netsim_3.ini"), "").unwrap();

        let tracker = tracker_for(&[&alias, &real], root.path());
        let found = tracker.discover_ini_files(&[netsimd(DAEMON_A, START, &other)]);
        // One path per file, spelled as first seen.
        assert_eq!(found, vec![alias.join("netsim.ini"), alias.join("netsim_2.ini")]);
    }

    #[test]
    fn test_netsim_tracker_stale_ini_does_not_steal_live_daemon() {
        // A leftover INI from a dead daemon must not override the live INI,
        // whichever sorts first and whether or not netsim wrote `pid=`.
        for with_pid in [false, true] {
            for stale_first in [false, true] {
                let ctx = format!("with_pid={with_pid} stale_first={stale_first}");
                let root = tempfile::tempdir().unwrap();
                let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
                std::fs::create_dir_all(&dir_a).unwrap();
                std::fs::create_dir_all(&dir_b).unwrap();
                let (stale_dir, live_dir) =
                    if stale_first { (&dir_a, &dir_b) } else { (&dir_b, &dir_a) };
                let (stale_ini, live_ini) =
                    (stale_dir.join("netsim.ini"), live_dir.join("netsim.ini"));
                let pid_line =
                    |pid: u32| if with_pid { format!("pid={pid}\n") } else { String::new() };
                write_ini(
                    &stale_ini,
                    &format!("{}grpc.port=1111\n", pid_line(DEAD_PID)),
                    START - 600,
                );
                write_ini(
                    &live_ini,
                    &format!("{}grpc.port=43929\n", pid_line(DAEMON_A)),
                    START + 1,
                );

                let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
                let procs = [netsimd(DAEMON_A, START, live_dir)];
                for now in [START + 10, START + 20] {
                    tick(&mut tracker, &procs, now);
                    let d = &tracker.daemons[&(DAEMON_A, START)];
                    assert_eq!(d.grpc_port, Some(43929), "{ctx}");
                    assert_eq!(d.ini_path.as_deref(), Some(live_ini.as_path()), "{ctx}");
                    assert!(tracker.stale_inis.contains_key(&stale_ini), "{ctx}");
                    assert!(!tracker.stale_inis.contains_key(&live_ini), "{ctx}");
                }
                assert_eq!(count_events(&tracker, "STALE_INI("), 1, "{ctx}");
                assert_eq!(tracker.restart_count, 0, "{ctx}");
            }
        }
    }

    #[test]
    fn test_netsim_tracker_rejects_ini_with_reused_pid() {
        // The INI predates the daemon whose PID it names, so that PID was
        // reused by a new netsimd and the INI is stale.
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=1111\n"), START - 600);
        let mut tracker = tracker_for(&[root.path()], root.path());
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);
        let d = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d.grpc_port, None);
        assert_eq!(d.ini_path, None);
        assert!(tracker.stale_inis.contains_key(&ini));
    }

    #[test]
    fn test_netsim_tracker_reused_pid_of_live_daemon_is_stale() {
        // As above, but a process with the reused PID is alive: it is the
        // tracked daemon, not the INI's writer, so the INI is still stale.
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=1111\n"), START - 600);
        let mut tracker = tracker_for(&[root.path()], root.path());
        tracker.pid_alive = |pid| pid == DAEMON_A;
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].ini_path, None);
        assert!(tracker.stale_inis.contains_key(&ini));
    }

    #[test]
    fn test_netsim_tracker_counts_restart_once() {
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
        let mut tracker = tracker_for(&[root.path()], root.path());
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);

        // netsimd B replaces A and rewrites the INI; later ticks see no restart.
        let b_start = START + 60;
        write_ini(&ini, &format!("pid={DAEMON_B}\ngrpc.port=43930\n"), b_start + 1);
        let procs_b = [netsimd(DAEMON_B, b_start, root.path())];
        tick(&mut tracker, &procs_b, b_start + 5);
        tick(&mut tracker, &procs_b, b_start + 10);

        assert_eq!(tracker.restart_count, 1);
        assert_eq!(count_events(&tracker, "RESTARTED("), 1);
        let restarted =
            format!("RESTARTED(ini={}, old_pid={DAEMON_A}, new_pid={DAEMON_B})", ini.display());
        assert!(tracker.timeline.iter().any(|(_, e)| *e == restarted), "{:?}", tracker.timeline);
        assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(43930));
        assert!(tracker.daemons[&(DAEMON_A, START)].exited);
        assert!(tracker.stale_inis.is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn test_netsim_tracker_lock_holder_beats_conflicting_pid() {
        // The INI's lock holder (B) wins over the daemon its `pid=` names (A).
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
        let lock = root.path().join("netsim.ini.lock");
        std::fs::write(&lock, "").unwrap();
        let lock_key = get_file_lock_key(&lock).unwrap();

        let mut tracker = tracker_for(&[root.path()], root.path());
        tracker.custom_proc_locks =
            Some(format!("1: FLOCK  ADVISORY  WRITE {DAEMON_B} {lock_key} 0 EOF\n"));
        let procs = [netsimd(DAEMON_A, START, root.path()), netsimd(DAEMON_B, START, root.path())];
        tick(&mut tracker, &procs, START + 10);

        let (a, b) = (&tracker.daemons[&(DAEMON_A, START)], &tracker.daemons[&(DAEMON_B, START)]);
        assert_eq!(b.ini_path.as_deref(), Some(ini.as_path()));
        assert_eq!(b.grpc_port, Some(43929));
        assert_eq!(a.ini_path, None);
        assert_eq!(a.grpc_port, None);
        assert!(tracker.stale_inis.is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn test_netsim_tracker_ini_with_untracked_holder_ignores_pid() {
        // An INI with lock holders pairs only through them: an untracked
        // holder (B) keeps the daemon its `pid=` names (A) from taking it,
        // and a held INI is not stale.
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
        let lock = root.path().join("netsim.ini.lock");
        std::fs::write(&lock, "").unwrap();
        let lock_key = get_file_lock_key(&lock).unwrap();

        let mut tracker = tracker_for(&[root.path()], root.path());
        tracker.custom_proc_locks =
            Some(format!("1: FLOCK  ADVISORY  WRITE {DAEMON_B} {lock_key} 0 EOF\n"));
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);

        let a = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(a.ini_path, None);
        assert_eq!(a.grpc_port, None);
        assert!(tracker.stale_inis.is_empty());
    }

    #[test]
    fn test_netsim_tracker_instance_fallback_pairs_by_ini_name() {
        // Without lock or pid evidence, `--instance 2` pairs with
        // netsim_2.ini; the default-instance INI has no daemon and is stale.
        let root = tempfile::tempdir().unwrap();
        let (ini1, ini2) = (root.path().join("netsim.ini"), root.path().join("netsim_2.ini"));
        write_ini(&ini1, "grpc.port=1111\n", START + 1);
        write_ini(&ini2, "grpc.port=43929\n", START + 1);
        let mut proc2 = netsimd(DAEMON_A, START, root.path());
        proc2.cmd.extend(["--instance".to_string(), "2".to_string()]);

        let mut tracker = tracker_for(&[root.path()], root.path());
        tick(&mut tracker, &[proc2], START + 10);
        let d = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d.instance_num, Some(2));
        assert_eq!(d.grpc_port, Some(43929));
        assert_eq!(d.ini_path.as_deref(), Some(ini2.as_path()));
        assert!(tracker.stale_inis.contains_key(&ini1));
    }

    #[test]
    fn test_netsim_tracker_pairs_same_instance_daemons_by_start_time() {
        // Two older-netsim daemons (no `pid=`) with different TMPDIRs each
        // pair with the INI written after their own start. B's INI sorts
        // first, so taking INIs in path order would give A the wrong one.
        let root = tempfile::tempdir().unwrap();
        let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
        std::fs::create_dir_all(&dir_a).unwrap();
        std::fs::create_dir_all(&dir_b).unwrap();
        let b_start = START + 100;
        write_ini(&dir_a.join("netsim.ini"), "grpc.port=2222\n", b_start + 1);
        write_ini(&dir_b.join("netsim.ini"), "grpc.port=1111\n", START + 1);

        let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
        let procs = [netsimd(DAEMON_A, START, &dir_b), netsimd(DAEMON_B, b_start, &dir_a)];
        tick(&mut tracker, &procs, b_start + 5);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(1111));
        assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(2222));
        assert!(tracker.stale_inis.is_empty());
    }

    #[test]
    fn test_netsim_tracker_newest_daemon_takes_oldest_ini() {
        // Both pid-less INIs were written after both daemons started, so
        // either daemon could own either one. B, the newer daemon, chooses
        // first and takes the INI written closest to its start.
        let root = tempfile::tempdir().unwrap();
        let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
        std::fs::create_dir_all(&dir_a).unwrap();
        std::fs::create_dir_all(&dir_b).unwrap();
        let b_start = START + 100;
        write_ini(&dir_a.join("netsim.ini"), "grpc.port=1111\n", b_start + 50);
        write_ini(&dir_b.join("netsim.ini"), "grpc.port=2222\n", b_start + 1);

        let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
        let procs = [netsimd(DAEMON_A, START, &dir_a), netsimd(DAEMON_B, b_start, &dir_b)];
        tick(&mut tracker, &procs, b_start + 60);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(1111));
        assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(2222));
        assert!(tracker.stale_inis.is_empty());
    }

    #[test]
    fn test_netsim_tracker_ini_instance_fallback_without_proc_locks() {
        let dir = tempfile::tempdir().unwrap();
        let ini_path = dir.path().join("netsim.ini");
        write_ini(&ini_path, "grpc.port=43929\n", START + 1);

        let procs = vec![NetsimDaemonProc {
            pid: DAEMON_A,
            parent: Some(8000),
            parent_name: Some("qemu-system-x86_64.exe".to_string()),
            start_time: START,
            exe: Some(PathBuf::from(r"C:\Android\Sdk\emulator\netsimd.exe")),
            cmd: vec![r"C:\Android\Sdk\emulator\netsimd.exe".to_string()],
            environ: env_of(&[("TMPDIR", dir.path().to_str().unwrap())]),
            cpu: 1.0,
            ram_mb: 30.0,
            uid: None,
            same_user: true,
        }];

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tick(&mut tracker, &procs, START + 10);
        let d = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d.spawner, "qemu");
        assert_eq!(d.grpc_port, Some(43929));
        assert_eq!(d.ini_path.as_deref(), Some(ini_path.as_path()));
        assert!(tracker.stale_inis.is_empty());
    }

    const WORKER_A: u32 = 5_000_300;
    const WORKER_B: u32 = 5_000_400;

    fn emu_group(serial: &str, pid: u32, avd_id: &str, name: Option<&str>) -> EmuGroupNetsimInfo {
        EmuGroupNetsimInfo {
            serial: serial.to_string(),
            primary_pid: pid,
            avd_id: Some(avd_id.to_string()),
            display_name: name.map(str::to_string),
            ..Default::default()
        }
    }

    /// One tick with no netsim daemons and the given live emulators, each as
    /// (storage key, group).
    fn emu_tick(tracker: &mut NetsimTracker, live: &[(&str, &EmuGroupNetsimInfo)], now_mono: f64) {
        emu_tick_with(tracker, &[], live, now_mono);
    }

    fn emu_tick_with(
        tracker: &mut NetsimTracker,
        procs: &[NetsimDaemonProc],
        live: &[(&str, &EmuGroupNetsimInfo)],
        now_mono: f64,
    ) {
        emu_tick_adb(tracker, procs, live, &[], now_mono);
    }

    /// Like emu_tick_with, with adb listing each (serial, state) in `adb`.
    fn emu_tick_adb(
        tracker: &mut NetsimTracker,
        procs: &[NetsimDaemonProc],
        live: &[(&str, &EmuGroupNetsimInfo)],
        adb: &[(&str, &str)],
        now_mono: f64,
    ) {
        let groups: HashMap<String, EmuGroupNetsimInfo> =
            live.iter().map(|(_, g)| (g.serial.clone(), (*g).clone())).collect();
        let summary: Vec<(String, String, u32, f64)> = live
            .iter()
            .map(|(k, g)| (k.to_string(), g.serial.clone(), g.primary_pid, now_mono))
            .collect();
        let adb: HashMap<String, String> =
            adb.iter().map(|(s, st)| (s.to_string(), st.to_string())).collect();
        let now_wall = START as f64 + now_mono;
        tracker.update(procs, &groups, &summary, &adb, now_wall, now_mono);
    }

    #[test]
    fn test_netsim_tracker_exited_emulator_disconnects() {
        // The emulator was connected from t=10 and has left the host scan by t=40.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.emulator_records.insert(
            "pixel_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "pixel_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: WORKER_A,
                state: "CONNECTED".to_string(),
                first_transport_ts: Some(START as f64 + 10.0),
                connected_start_mono: Some(10.0),
                adb_online_since_mono: Some(10.0),
                ..Default::default()
            },
        );

        // The first missed tick may be a scan hiccup, so the record is held.
        emu_tick(&mut tracker, &[], 40.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

        // The second settles it as of the first miss, so the hold adds no
        // connected time.
        emu_tick(&mut tracker, &[], 45.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!(rec.state, "DISCONNECTED");
        assert_eq!(rec.disconnect_count, 1);
        assert_eq!(rec.connected_total_s, 30.0);
        assert_eq!(rec.connected_start_mono, None);
        assert_eq!(rec.adb_online_since_mono, None);

        // Later ticks neither count the disconnect again nor add connected time,
        // and the CSV keeps reporting how the emulator ended.
        emu_tick(&mut tracker, &[], 50.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.connected_total_s, 30.0);
        let fields = tracker.get_csv_fields();
        assert_eq!(fields["netsim_state_pixel_emulator-5554"], "DISCONNECTED");
    }

    #[test]
    fn test_netsim_tracker_exited_emulator_that_never_connected_fails() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let group = emu_group("emulator-5554", WORKER_A, "pixel", None);
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 1.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");

        emu_tick(&mut tracker, &[], 2.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");
        emu_tick(&mut tracker, &[], 3.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!(rec.state, "FAILED(worker_exit_no_connect)");
        assert_eq!(rec.disconnect_count, 0);
    }

    #[test]
    fn test_netsim_tracker_csv_covers_emulators_adb_does_not_list() {
        // netsim can connect before adb lists the emulator (emu_tick passes no
        // adb devices), so the CSV has a column for every record.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let group = emu_group("emulator-5554", WORKER_A, "pixel", None);
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 1.0);
        let fields = tracker.get_csv_fields();
        assert_eq!(fields["netsim_state_pixel_emulator-5554"], "CONNECTING");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_netsim_tracker_worker_start_comes_from_proc() {
        // The worker's start time is the kernel's, whatever the tick's clock
        // says: here the tick is dated days before this process started.
        // /proc/uptime and clock ticks have 10 ms resolution, and the test
        // binary may have started only milliseconds before `now`.
        const PROC_TIME_RESOLUTION_S: f64 = 0.1;
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let now = now_wall_secs();
        let group = emu_group("emulator-5554", std::process::id(), "pixel", None);
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 10.0);
        let start = tracker.emulator_records["pixel_emulator-5554"].worker_start_ts;
        assert!(
            start <= now + PROC_TIME_RESOLUTION_S && start > now - 3600.0,
            "start={start} now={now}"
        );
    }

    #[test]
    fn test_netsim_tracker_unreadable_worker_start_falls_back_to_now() {
        // A worker whose start time cannot be read must not be dated to the
        // Unix epoch, or every latency derived from it would be ~1.8e9 s.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let gone = emu_group("emulator-5554", u32::MAX - 7, "pixel", None);
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &gone)], 10.0);
        let start = tracker.emulator_records["pixel_emulator-5554"].worker_start_ts;
        assert_eq!(start, START as f64 + 10.0);
    }

    #[test]
    fn test_netsim_tracker_session_keys_follow_worker() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let pixel = emu_group("emulator-5554", WORKER_A, "pixel", None);

        // The first scan has no AVD name yet, so the record is keyed by serial.
        emu_tick(&mut tracker, &[("emulator-5554", &pixel)], 1.0);
        assert!(tracker.emulator_records.contains_key("emulator-5554"));

        // Once the AVD name is known the same worker's record moves to the
        // AVD-qualified key instead of being left behind as an exited emulator.
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &pixel)], 2.0);
        let keys: Vec<&String> = tracker.emulator_records.keys().collect();
        assert_eq!(keys, ["pixel_emulator-5554"]);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!(rec.storage_key, "pixel_emulator-5554");
        assert_eq!(rec.first_seen_mono, 1.0);
        assert_eq!(rec.state, "CONNECTING");

        // A scan that briefly loses the AVD name keeps using that record.
        emu_tick(&mut tracker, &[("emulator-5554", &pixel)], 3.0);
        let keys: Vec<&String> = tracker.emulator_records.keys().collect();
        assert_eq!(keys, ["pixel_emulator-5554"]);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");

        // A different AVD on the reused port is a new worker: it gets its own
        // record and the previous emulator settles as exited.
        let other = emu_group("emulator-5554", WORKER_B, "other", None);
        emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 4.0);
        assert_eq!(
            tracker.emulator_records["pixel_emulator-5554"].state,
            "FAILED(worker_exit_no_connect)"
        );
        let rec = &tracker.emulator_records["other_emulator-5554"];
        assert_eq!((rec.worker_pid, rec.state.as_str()), (WORKER_B, "CONNECTING"));
    }

    #[test]
    fn test_netsim_tracker_never_downgrades_display_name() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let key = "pixel_emulator-5554";
        let device_name = |t: &NetsimTracker| t.emulator_records[key].device_name.clone();

        // Before pid_<pid>.ini is written only the AVD id is known.
        let no_ini = emu_group("emulator-5554", WORKER_A, "pixel", None);
        emu_tick(&mut tracker, &[(key, &no_ini)], 1.0);
        assert_eq!(device_name(&tracker).as_deref(), Some("pixel"));

        let with_ini = emu_group("emulator-5554", WORKER_A, "pixel", Some("Pixel Test"));
        emu_tick(&mut tracker, &[(key, &with_ini)], 2.0);
        assert_eq!(device_name(&tracker).as_deref(), Some("Pixel Test"));

        // The emulator deletes the ini while the worker shuts down.
        emu_tick(&mut tracker, &[(key, &no_ini)], 3.0);
        assert_eq!(device_name(&tracker).as_deref(), Some("Pixel Test"));
    }

    /// Phone and Glasses on one netsimd, whose log also has a device named
    /// after a serial.
    const SHARED_DAEMON_LOG: &str = concat!(
        "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n",
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
        "netsim I 09-25 22:37:08.000 netsimd.rs:106 - Handling new connection for \"\", device Glasses\n",
        "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Glasses)\n",
        "netsim I 09-25 22:37:09.000 service.rs:585 - DeviceActor: AddChip 3 (kind WIFI) to device 3 (emulator-5556)\n",
    );

    /// A tracker that tails the log of its one netsimd, which writes `log`.
    fn tracker_with_daemon_log(dir: &Path, log: &str) -> (NetsimTracker, [NetsimDaemonProc; 1]) {
        let art_dir = dir.join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_dir).unwrap();
        std::fs::write(art_dir.join("netsim_stderr.log"), log).unwrap();
        let mut tracker = tracker_for(&[dir], dir);
        tracker.enable_log = true;
        (tracker, [netsimd(DAEMON_A, START, dir)])
    }

    fn chip_ids(tracker: &NetsimTracker, key: &str) -> Vec<String> {
        tracker.emulator_records[key].chip_ids.iter().cloned().collect()
    }

    #[test]
    fn test_netsim_tracker_fallback_skips_exited_emulator_chips() {
        // Once Phone exits, Glasses is the only live emulator: it takes the chip
        // nobody claims, but never Phone's.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let glasses = emu_group("emulator-5556", WORKER_B, "glasses", Some("Glasses"));

        let both = [("phone_emulator-5554", &phone), ("glasses_emulator-5556", &glasses)];
        emu_tick_with(&mut tracker, &procs, &both, 10.0);
        assert_eq!(chip_ids(&tracker, "phone_emulator-5554"), ["1"]);
        assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2"]);
        let glasses_first_ts = tracker.emulator_records["glasses_emulator-5556"].first_transport_ts;
        assert!(glasses_first_ts.is_some());

        emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 20.0);
        // Phone missed one tick and may still be running, so Glasses is not
        // the only live emulator yet.
        let phone_rec = &tracker.emulator_records["phone_emulator-5554"];
        assert_eq!((phone_rec.state.as_str(), phone_rec.disconnect_count), ("CONNECTED", 0));
        assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2"]);

        emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 25.0);
        let phone_rec = &tracker.emulator_records["phone_emulator-5554"];
        assert_eq!((phone_rec.state.as_str(), phone_rec.disconnect_count), ("DISCONNECTED", 1));
        let glasses_rec = &tracker.emulator_records["glasses_emulator-5556"];
        assert_eq!(glasses_rec.state, "CONNECTED");
        assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2", "3"]);
        assert_eq!(glasses_rec.first_transport_ts, glasses_first_ts);
    }

    #[test]
    fn test_netsim_tracker_fallback_skips_exited_emulator_connections() {
        // An emulator whose name is not known yet matches no device while Phone
        // runs. As the only live emulator it takes the unclaimed connections and
        // chips, and their device name, but none of Phone's.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let unnamed = EmuGroupNetsimInfo {
            serial: "emulator-5556".to_string(),
            primary_pid: WORKER_B,
            ..Default::default()
        };

        let both = [("phone_emulator-5554", &phone), ("emulator-5556", &unnamed)];
        emu_tick_with(&mut tracker, &procs, &both, 10.0);
        let rec = &tracker.emulator_records["emulator-5556"];
        assert_eq!((rec.state.as_str(), rec.first_transport_ts), ("CONNECTING", None));

        // Phone is held for its first missed tick, so nothing moves yet.
        emu_tick_with(&mut tracker, &procs, &[("emulator-5556", &unnamed)], 20.0);
        let rec = &tracker.emulator_records["emulator-5556"];
        assert_eq!((rec.device_name.as_deref(), rec.first_transport_ts), (None, None));

        emu_tick_with(&mut tracker, &procs, &[("emulator-5556", &unnamed)], 25.0);
        let phone_first_ts = tracker.emulator_records["phone_emulator-5554"].first_transport_ts;
        let rec = &tracker.emulator_records["emulator-5556"];
        assert_eq!(rec.device_name.as_deref(), Some("Glasses"));
        assert_eq!(chip_ids(&tracker, "emulator-5556"), ["2", "3"]);
        assert!(rec.first_transport_ts.is_some_and(|t| phone_first_ts.is_some_and(|p| t > p)));
    }

    #[test]
    fn test_netsim_tracker_host_scan_miss_while_adb_lists_keeps_state() {
        // adb still lists the emulator, so a host-scan miss is a scan hiccup,
        // however long it lasts.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");

        let adb = HashMap::from([("emulator-5554".to_string(), "device".to_string())]);
        for now_mono in [20.0, 30.0, 40.0] {
            tracker.update(&procs, &HashMap::new(), &[], &adb, START as f64 + now_mono, now_mono);
            let rec = &tracker.emulator_records[key];
            assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
        }
    }

    #[test]
    fn test_netsim_tracker_one_tick_miss_is_not_a_disconnect() {
        // The emulator misses both the host scan and adb for one tick, then
        // comes back: it stays connected, with no disconnect and no lost time.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

        emu_tick_with(&mut tracker, &procs, &[], 20.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
        assert_eq!(tracker.get_csv_fields()["netsim_state_phone_emulator-5554"], "CONNECTED");

        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 30.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!(
            (rec.state.as_str(), rec.disconnect_count, rec.connected_start_mono),
            ("CONNECTED", 0, Some(10.0))
        );

        // Seeing it again ended the miss run, so a new miss is held again.
        emu_tick_with(&mut tracker, &procs, &[], 40.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
    }

    #[test]
    fn test_netsim_tracker_miss_run_restarts_when_adb_lists() {
        // Only consecutive ticks missing from both the host scan and adb
        // count, and the exit is dated to the first of them.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        let adb = [("emulator-5554", "device")];
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

        emu_tick_with(&mut tracker, &procs, &[], 20.0);
        emu_tick_adb(&mut tracker, &procs, &[], &adb, 30.0);
        emu_tick_adb(&mut tracker, &procs, &[], &adb, 40.0);
        emu_tick_with(&mut tracker, &procs, &[], 50.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
        // While held, the summary counts connected time only up to the miss.
        let summary = tracker.format_summary_sections(55.0).join("\n");
        assert!(summary.contains("connected_time=40.0s"), "{summary}");

        emu_tick_with(&mut tracker, &procs, &[], 60.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.connected_total_s, 40.0);
    }

    #[test]
    fn test_netsim_tracker_stale_adb_offline_entry_does_not_hold() {
        // adb can keep listing a killed emulator as 'offline'.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        let adb = [("emulator-5554", "offline")];
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

        emu_tick_adb(&mut tracker, &procs, &[], &adb, 20.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        emu_tick_adb(&mut tracker, &procs, &[], &adb, 30.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.connected_total_s, 10.0);
    }

    #[test]
    fn test_netsim_tracker_settled_emulator_stays_settled() {
        // A daemon that later reports a startup timeout does not turn an
        // emulator that already disconnected into a launcher failure.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
        emu_tick_with(&mut tracker, &procs, &[], 20.0);
        emu_tick_with(&mut tracker, &procs, &[], 30.0);
        let daemon_key = tracker.emulator_records[key].daemon_key.unwrap();
        assert_eq!(tracker.emulator_records[key].state, "DISCONNECTED");

        tracker.daemons.get_mut(&daemon_key).unwrap().exit_reason =
            Some("startup_timeout".to_string());
        emu_tick_with(&mut tracker, &procs, &[], 40.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    }

    #[test]
    fn test_netsim_tracker_replaced_emulator_settles_at_once() {
        // Another AVD takes the serial of a held emulator, so the held record
        // settles at once, still dated to its first missed tick.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.emulator_records.insert(
            "pixel_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "pixel_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: WORKER_A,
                state: "CONNECTED".to_string(),
                first_transport_ts: Some(START as f64 + 10.0),
                connected_start_mono: Some(10.0),
                ..Default::default()
            },
        );
        emu_tick(&mut tracker, &[], 40.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTED");

        let other = emu_group("emulator-5554", WORKER_B, "other", None);
        emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 45.0);
        let old = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!((old.state.as_str(), old.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(old.connected_total_s, 30.0);
        let new = &tracker.emulator_records["other_emulator-5554"];
        assert_eq!((new.worker_pid, new.state.as_str()), (WORKER_B, "CONNECTING"));
    }

    #[test]
    fn test_poll_sockets_fast_skips_settled_and_non_connecting_records() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
            .unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.host_os = HostOs::MacOs;
        tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
        let procs = [netsimd(DAEMON_A, START, dir.path())];
        emu_tick_with(&mut tracker, &procs, &[], 1.0);
        for (key, state, no_netsim) in [
            ("failed_emulator-5554", "FAILED(worker_exit_no_connect)", false),
            ("disc_emulator-5554", "DISCONNECTED", false),
            ("disabled_emulator-5554", "CONNECTING", true),
            ("connected_emulator-5554", "CONNECTED", false),
            ("live_emulator-5554", "CONNECTING", false),
        ] {
            tracker.emulator_records.insert(
                key.to_string(),
                EmulatorNetsimRecord {
                    storage_key: key.to_string(),
                    serial: "emulator-5554".to_string(),
                    worker_pid: 4001,
                    worker_start_ts: 100.0,
                    state: state.to_string(),
                    no_netsim,
                    ..Default::default()
                },
            );
        }

        tracker.poll_sockets_fast(&emu_groups_with(&[4001]), 105.0);
        for skipped in [
            "failed_emulator-5554",
            "disc_emulator-5554",
            "disabled_emulator-5554",
            "connected_emulator-5554",
        ] {
            let rec = &tracker.emulator_records[skipped];
            assert_eq!((rec.first_transport_ts, rec.netsim_connect_s), (None, None), "{skipped}");
        }
        let live = &tracker.emulator_records["live_emulator-5554"];
        assert_eq!((live.first_transport_ts, live.netsim_connect_s), (Some(105.0), Some(5.0)));
    }

    #[test]
    fn test_fast_poll_stops_once_connecting_emulator_has_transport() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
            .unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.host_os = HostOs::MacOs;
        tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
        emu_tick_with(&mut tracker, &[netsimd(DAEMON_A, START, dir.path())], &[], 1.0);
        tracker.emulator_records.insert(
            "live_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "live_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: 4001,
                worker_start_ts: 100.0,
                state: "CONNECTING".to_string(),
                ..Default::default()
            },
        );
        assert!(tracker.has_any_connecting());
        tracker.poll_sockets_fast(&emu_groups_with(&[4001]), 105.0);
        assert_eq!(tracker.emulator_records["live_emulator-5554"].first_transport_ts, Some(105.0));
        // Nothing is left for the sub-poll to measure until the next full tick.
        assert!(!tracker.has_any_connecting());
    }

    #[test]
    fn test_netsim_tracker_worker_exit_with_first_transport_settles_to_failed() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        // Unjoined path: worker opened a socket (`first_transport_ts` set)
        // while still CONNECTING, then exited before reaching CONNECTED.
        tracker.emulator_records.insert(
            "pixel_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "pixel_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: WORKER_A,
                worker_start_ts: START as f64 + 1.0,
                first_seen_mono: 1.0,
                state: "CONNECTING".to_string(),
                first_transport_ts: Some(START as f64 + 1.5),
                netsim_connect_s: Some(0.5),
                ..Default::default()
            },
        );
        emu_tick(&mut tracker, &[], 10.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");
        emu_tick(&mut tracker, &[], 20.0);
        assert_eq!(
            tracker.emulator_records["pixel_emulator-5554"].state,
            "FAILED(worker_exit_no_connect)"
        );

        // Joined path: UT-5 (live emulator with first_transport_ts set and no
        // active sockets or chips transitions to STALLED after 50s), then
        // settles to FAILED(worker_exit_no_connect) when `emu_groups` no longer
        // contains the worker.
        let conn_only_log = "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n";
        let dir2 = tempfile::tempdir().unwrap();
        let (mut tracker2, procs2) = tracker_with_daemon_log(dir2.path(), conn_only_log);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        let summary = vec![(key.to_string(), "emulator-5554".to_string(), WORKER_A, 1.0)];
        let mut groups = HashMap::new();
        groups.insert("emulator-5554".to_string(), phone);
        tracker2.update(&procs2, &groups, &summary, &HashMap::new(), START as f64 + 10.0, 10.0);
        assert!(tracker2.emulator_records[key].first_transport_ts.is_some());
        assert_eq!(tracker2.emulator_records[key].state, "CONNECTING");

        // UT-5: Still live at t=52.0 (>= 50s since first_seen_mono=1.0) -> STALLED.
        tracker2.update(&procs2, &groups, &summary, &HashMap::new(), START as f64 + 52.0, 52.0);
        assert_eq!(tracker2.emulator_records[key].state, "STALLED");

        // Worker exits from `emu_groups` while still listed in `summary` -> FAILED.
        tracker2.update(
            &procs2,
            &HashMap::new(),
            &summary,
            &HashMap::new(),
            START as f64 + 55.0,
            55.0,
        );
        let rec = &tracker2.emulator_records[key];
        assert!(rec.first_transport_ts.is_some());
        assert_eq!(rec.state, "FAILED(worker_exit_no_connect)");

        // Subsequent ticks do not overwrite the settled FAILED state back to CONNECTING.
        tracker2.update(
            &procs2,
            &HashMap::new(),
            &summary,
            &HashMap::new(),
            START as f64 + 60.0,
            60.0,
        );
        assert_eq!(tracker2.emulator_records[key].state, "FAILED(worker_exit_no_connect)");
    }

    #[test]
    fn test_netsim_tracker_cli_socket_failure_retains_cache_until_clear() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
            .unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.host_os = HostOs::MacOs;
        tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
        let procs = [netsimd(DAEMON_A, START, dir.path())];
        let mut phone = emu_group("emulator-5554", 4001, "phone", Some("Phone"));
        phone.pids = vec![4001];
        let key = "phone_emulator-5554";

        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

        // Transient CLI timeout/failure (None) keeps the cached connection for
        // live PID 4001 so the emulator does not flip to DISCONNECTED.
        tracker.socket_cli_runner = |_, _| None;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 20.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

        // A successful empty CLI scan (Some("")) clears the cache and settles
        // the socket-only connection to DISCONNECTED.
        tracker.socket_cli_runner = |_, _| Some(String::new());
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 30.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));

        // UT-7: Reconnect PID 4001 at t=40, then replace with PID 4002 at t=50
        // while lsof times out (None) -> stale cached socket for exited PID
        // 4001 is pruned rather than retained.
        tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 40.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        let mut phone_b = emu_group("emulator-5554", 4002, "phone", Some("Phone"));
        phone_b.pids = vec![4002];
        tracker.socket_cli_runner = |_, _| None;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 50.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTING");
    }

    #[test]
    fn test_netsim_tracker_same_avd_restart_resets_state_and_merges_bare_serial() {
        let phone_log = concat!(
            "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n",
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
        );
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), phone_log);
        let phone_a = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";

        // Run 1 (WORKER_A) connects at t=10.
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_a)], 10.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        assert_eq!(chip_ids(&tracker, key), ["1"]);
        let run1_log_ts = tracker.emulator_records[key].first_transport_ts.unwrap() as u64;

        // Run 2 (WORKER_B) of the same AVD restarts on the same port at t=60
        // (>=50s after Run 1): first seen under bare serial "emulator-5554"
        // before AVD id resolves, stamps `first_transport_ts`, then promoted to
        // "phone_emulator-5554" at t=61.
        let mut phone_b_unnamed = emu_group("emulator-5554", WORKER_B, "phone", None);
        phone_b_unnamed.avd_id = None;
        phone_b_unnamed.primary_start_time = run1_log_ts + 60;
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &phone_b_unnamed)], 60.0);
        assert!(tracker.emulator_records.contains_key("emulator-5554"));
        tracker.emulator_records.get_mut("emulator-5554").unwrap().first_transport_ts =
            Some(run1_log_ts as f64 + 61.0);

        let mut phone_b = emu_group("emulator-5554", WORKER_B, "phone", Some("Phone"));
        phone_b.primary_start_time = run1_log_ts + 60;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 61.0);
        assert!(!tracker.emulator_records.contains_key("emulator-5554"));
        let rec = &tracker.emulator_records[key];
        assert_eq!(rec.worker_pid, WORKER_B);
        assert_eq!(rec.disconnect_count, 1);
        assert_eq!(rec.connected_total_s, 50.0);
        // Pre-restart log entries from Run 1 are before WORKER_B's start time
        // and are ignored, while Tick-1's transport timestamp on the bare
        // record is preserved.
        assert_eq!(rec.state, "CONNECTING");
        assert!(rec.chip_attach_s.is_empty());
        assert!(rec.chip_ids.is_empty());
        assert_eq!(rec.first_transport_ts, Some(run1_log_ts as f64 + 61.0));
        assert_eq!(rec.netsim_connect_s, Some(1.0));

        // Direct same-key restart (WORKER_B -> 5_000_403) at t=70 without bare-serial
        // stage also resets per-run state and `first_seen_mono` so it starts in
        // CONNECTING (not STALLED) while preserving cumulative history.
        let mut phone_c = emu_group("emulator-5554", 5_000_403, "phone", Some("Phone"));
        phone_c.primary_start_time = run1_log_ts + 70;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_c)], 70.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!(rec.worker_pid, 5_000_403);
        assert_eq!(rec.disconnect_count, 1);
        assert_eq!(rec.connected_total_s, 50.0);
        assert_eq!(rec.state, "CONNECTING");
        assert_eq!(rec.first_transport_ts, None);
    }

    #[test]
    fn test_find_joined_daemon_priority_order() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let procs = [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START, dir.path())];
        tracker.discover_and_poll_daemons(&procs, &HashMap::new(), START as f64, 1.0);
        tracker.daemons.get_mut(&(DAEMON_A, START)).unwrap().grpc_port = Some(43929);
        tracker.daemons.get_mut(&(DAEMON_B, START)).unwrap().grpc_port = Some(43930);
        let active = [(DAEMON_A, START), (DAEMON_B, START)];

        // 1. Explicit gRPC endpoint port wins over socket connections.
        let mut g = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        g.grpc_endpoint = Some("localhost:43930".to_string());
        let conns = HashMap::from([(
            43929,
            vec![LoopbackGrpcConn {
                serial: "emulator-5554".to_string(),
                pid: WORKER_A,
                peer_port: 50000,
            }],
        )]);
        assert_eq!(
            tracker.find_joined_daemon("emulator-5554", Some(&g), &active, &conns),
            Some((DAEMON_B, START))
        );

        // 2. Without endpoint match, established loopback socket wins.
        g.grpc_endpoint = None;
        assert_eq!(
            tracker.find_joined_daemon("emulator-5554", Some(&g), &active, &conns),
            Some((DAEMON_A, START))
        );

        // 3. With multiple active daemons and neither endpoint nor socket match, returns None.
        assert_eq!(
            tracker.find_joined_daemon("emulator-5554", Some(&g), &active, &HashMap::new()),
            None
        );

        // 4. With a single active daemon, falls back to that daemon.
        assert_eq!(
            tracker.find_joined_daemon(
                "emulator-5554",
                Some(&g),
                &[(DAEMON_A, START)],
                &HashMap::new(),
            ),
            Some((DAEMON_A, START))
        );
    }

    #[test]
    fn test_discover_and_poll_daemons_records_duplicate_artifact_dir() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let procs = [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START, dir.path())];
        let seen = tracker.discover_and_poll_daemons(&procs, &HashMap::new(), START as f64, 1.0);
        assert_eq!(seen.len(), 2);
        assert_eq!(count_events(&tracker, "DUPLICATE_ARTIFACT_DIR("), 1);
    }

    #[test]
    fn test_settled_emulator_does_not_leak_wifi_or_duplicate_pcaps() {
        // When Phone and Glasses share a daemon and Phone associates with Wi-Fi
        // before exiting, Glasses must not inherit Phone's Wi-Fi association.
        let wifi_shared_log = concat!(
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
            "netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
            "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Glasses)\n",
        );
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), wifi_shared_log);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let glasses = emu_group("emulator-5556", WORKER_B, "glasses", Some("Glasses"));
        let both = [("phone_emulator-5554", &phone), ("glasses_emulator-5556", &glasses)];
        emu_tick_with(&mut tracker, &procs, &both, 10.0);
        assert_eq!(tracker.emulator_records["glasses_emulator-5556"].wifi_associated_s, None);

        emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 20.0);
        emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 25.0);
        assert_eq!(tracker.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
        assert_eq!(tracker.emulator_records["glasses_emulator-5556"].wifi_associated_s, None);

        // When a daemon's sole emulator settles to DISCONNECTED while netsimd
        // stays alive, periodic harvesting must keep its serial attribution
        // instead of re-copying its pcap under shared/.
        let dir2 = tempfile::tempdir().unwrap();
        let art2 = dir2.path().join("android-test").join("netsimd");
        let pcaps2 = art2.join("pcaps");
        std::fs::create_dir_all(&pcaps2).unwrap();
        std::fs::write(pcaps2.join("netsim-1-Phone-BLUETOOTH.pcap"), [0xd4u8; 64]).unwrap();
        std::fs::write(art2.join("netsim_stderr.log"), SHARED_DAEMON_LOG).unwrap();
        let out2 = dir2.path().join("out");
        let mut tracker2 = NetsimTracker::new(Some(""), 500, true, &out2);
        tracker2.host_os = HostOs::Linux;
        tracker2.custom_ini_dirs = vec![dir2.path().to_path_buf()];
        tracker2.custom_proc_locks = Some(String::new());
        let procs2 = [netsimd(DAEMON_A, START, dir2.path())];
        emu_tick_with(&mut tracker2, &procs2, &[("phone_emulator-5554", &phone)], 10.0);
        assert_eq!(tracker2.pcap_mgr.copied_files.len(), 1);

        emu_tick_with(&mut tracker2, &procs2, &[], 20.0);
        emu_tick_with(&mut tracker2, &procs2, &[], 30.0);
        assert_eq!(tracker2.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
        assert_eq!(tracker2.pcap_mgr.copied_files.len(), 1);
        assert!(!tracker2.pcap_mgr.copied_files.keys().any(|k| k.contains("shared")));
    }

    #[test]
    fn test_settled_record_does_not_hide_live_dashboard_row() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.emulator_records.insert(
            "pixel_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "pixel_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: WORKER_A,
                state: "DISCONNECTED".to_string(),
                disconnect_count: 1,
                ..Default::default()
            },
        );
        let other = emu_group("emulator-5554", WORKER_B, "other", None);
        emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 45.0);
        let snap = tracker.get_dashboard_snapshot(START as f64 + 45.0);
        assert_eq!(
            snap.emulator_rows.get("emulator-5554").map(String::as_str),
            Some("netsim: CONNECTING (no-chips) | disc=0")
        );
    }

    #[test]
    fn test_daemon_restart_preserves_exit_reason_and_ready_latency() {
        let dir = tempfile::tempdir().unwrap();
        let art = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art).unwrap();
        let ini_path = dir.path().join("netsim.ini");
        let log_path = art.join("netsim_stderr.log");

        std::fs::write(&ini_path, format!("pid={DAEMON_A}\nport=8554\n")).unwrap();
        std::fs::write(
            &log_path,
            concat!(
                "netsim I 09-25 22:37:06.000 server.rs:100 - Wrote to INI file /tmp/netsim.ini\n",
                "netsim I 09-25 22:37:08.000 server.rs:110 - Shutting down gracefully\n",
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone.primary_start_time = START - 1;

        let procs_a = [netsimd(DAEMON_A, START, dir.path())];
        emu_tick_with(&mut tracker, &procs_a, &[("phone_emulator-5554", &phone)], 10.0);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].netsim_daemon_ready_s, Some(1.0));
        assert_eq!(
            tracker.emulator_records["phone_emulator-5554"].netsim_daemon_ready_s,
            Some(2.0)
        );

        // DAEMON_B spawns at START + 15 before DAEMON_A finishes exiting and
        // before truncating DAEMON_A's log; by t=20 DAEMON_A has exited and
        // DAEMON_B has truncated netsim_stderr.log and written its own INI + ready line.
        let procs_ab =
            [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START + 15, dir.path())];
        emu_tick_with(&mut tracker, &procs_ab, &[("phone_emulator-5554", &phone)], 16.0);

        let procs_b = [netsimd(DAEMON_B, START + 15, dir.path())];
        std::fs::write(&ini_path, format!("pid={DAEMON_B}\nport=8555\n")).unwrap();
        std::fs::write(
            &log_path,
            "netsim I 09-25 22:37:22.000 server.rs:100 - Wrote to INI file /tmp/netsim.ini\n",
        )
        .unwrap();
        emu_tick_with(&mut tracker, &procs_b, &[("phone_emulator-5554", &phone)], 20.0);

        assert_eq!(
            tracker.daemons[&(DAEMON_A, START)].exit_reason.as_deref(),
            Some("sigterm_sigint")
        );
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].netsim_daemon_ready_s, Some(1.0));
        assert_eq!(tracker.daemons[&(DAEMON_B, START + 15)].netsim_daemon_ready_s, Some(2.0));
        assert_eq!(
            tracker.emulator_records["phone_emulator-5554"].netsim_daemon_ready_s,
            Some(2.0)
        );
    }

    #[test]
    fn test_daemon_restart_and_settled_records_update_connection_state() {
        let dir = tempfile::tempdir().unwrap();
        let art = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art).unwrap();
        let log_path = art.join("netsim_stderr.log");
        std::fs::write(&log_path, SHARED_DAEMON_LOG).unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";

        let procs_a = [netsimd(DAEMON_A, START, dir.path())];
        emu_tick_with(&mut tracker, &procs_a, &[(key, &phone)], 10.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        assert_eq!(tracker.get_csv_fields()["netsim_devices"], "1");

        // DAEMON_A exits and DAEMON_B starts with an empty log and no sockets:
        // Phone must drop DAEMON_A's chips, transition to DISCONNECTED, and not
        // count as an active joined device on DAEMON_B.
        std::fs::write(&log_path, "").unwrap();
        let procs_b = [netsimd(DAEMON_B, START + 15, dir.path())];
        emu_tick_with(&mut tracker, &procs_b, &[(key, &phone)], 20.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.connected_total_s, 10.0);
        assert!(rec.chip_attach_s.is_empty());
        assert_eq!(tracker.get_csv_fields()["netsim_devices"], "0");
    }

    #[test]
    fn test_wifi_association_recorded_after_same_avd_worker_restart() {
        let dir = tempfile::tempdir().unwrap();
        let art = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art).unwrap();
        let log_path = art.join("netsim_stderr.log");
        std::fs::write(
            &log_path,
            concat!(
                "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
                "netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let mut phone_a = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone_a.primary_start_time = START;
        let key = "phone_emulator-5554";
        let procs = [netsimd(DAEMON_A, START, dir.path())];

        emu_tick_with(&mut tracker, &procs, &[(key, &phone_a)], 10.0);
        assert_eq!(tracker.emulator_records[key].wifi_associated_s, Some(2.0));

        // Run 2 (WORKER_B) of the same AVD restarts at START + 20 and logs a new
        // Wi-Fi association at START + 23 (22:37:28.000).
        std::fs::write(
            &log_path,
            concat!(
                "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
                "netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
                "netsim I 09-25 22:37:26.000 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 2 (Phone)\n",
                "netsim I 09-25 22:37:28.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:01\n",
            ),
        )
        .unwrap();
        let mut phone_b = emu_group("emulator-5554", WORKER_B, "phone", Some("Phone"));
        phone_b.primary_start_time = START + 20;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 25.0);
        assert_eq!(tracker.emulator_records[key].wifi_associated_s, Some(3.0));
    }

    #[test]
    fn test_deleted_stale_ini_is_pruned_from_dashboard() {
        let dir = tempfile::tempdir().unwrap();
        let ini_path = dir.path().join("netsim.ini");
        write_ini(&ini_path, &format!("pid={DEAD_PID}\nport=8554\n"), START - 60);
        let mut tracker = tracker_for(&[dir.path()], dir.path());

        tick(&mut tracker, &[], START + 10);
        assert_eq!(
            tracker.get_dashboard_snapshot(START as f64 + 10.0).daemon_status_row.as_deref(),
            Some("STALE_INI (netsim.ini)")
        );

        std::fs::remove_file(&ini_path).unwrap();
        tick(&mut tracker, &[], START + 20);
        assert!(tracker.stale_inis.is_empty());
        assert_eq!(
            tracker.get_dashboard_snapshot(START as f64 + 20.0).daemon_status_row.as_deref(),
            Some("N/A")
        );
    }

    #[test]
    fn test_client_mode_and_exiting_daemon_log_isolation() {
        let dir = tempfile::tempdir().unwrap();
        let art = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art).unwrap();
        let log_path = art.join("netsim_stderr.log");
        std::fs::write(
            &log_path,
            concat!(
                "netsim I 09-25 22:37:05.612 netsimd.rs:451 - Netsim Version: 1.0.25, OS: linux, Arch: x86_64\n",
                "Args {\n    no_cli_ui: true,\n    pcap: true,\n}\n",
                "netsim I 09-25 22:37:05.650 netsimd.rs:700 - gRPC port: 43929\n",
                "netsim I 09-25 22:37:05.683 netsimd.rs:744 - Wrote to INI file /tmp/netsim.ini\n",
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let procs_a = [netsimd(DAEMON_A, START, dir.path())];
        tick(&mut tracker, &procs_a, START + 5);
        assert!(!tracker.daemons[&(DAEMON_A, START)].client_mode_recorded);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(43929));

        // Client-mode DAEMON_B spawns at START + 15 and appends "Lock held by another process"
        // to the shared log without truncating it.
        let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
        use std::io::Write;
        writeln!(f, "netsim W 09-25 22:37:20.500 netsimd.rs:500 - Lock held by another process")
            .unwrap();
        let procs_ab =
            [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START + 15, dir.path())];
        tick(&mut tracker, &procs_ab, START + 16);

        // Primary server DAEMON_A keeps its metadata and never records CLIENT_MODE_SPAWN;
        // client-mode DAEMON_B records CLIENT_MODE_SPAWN without inheriting DAEMON_A's port/flags.
        let da = &tracker.daemons[&(DAEMON_A, START)];
        let db = &tracker.daemons[&(DAEMON_B, START + 15)];
        assert!(!da.client_mode_recorded);
        assert!(db.client_mode_recorded);
        assert_eq!(db.grpc_port, None);
        assert_eq!(db.ini_path, None);
        assert!(!db.frontend_disabled);
        assert!(!db.pcap_flag);
    }

    #[test]
    fn test_qemu_next_supervisor_worker_transitions_preserve_session() {
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), "");
        let log_path = dir.path().join("android-test").join("netsimd").join("netsim_stderr.log");
        let launcher_pid = 5_000_350u32;
        let key = "phone_emulator-5554";

        // Startup: Tick 1 sees only supervisor launcher (5_000_350) under bare serial "emulator-5554";
        // Tick 2 sees supervisor (5_000_350) + worker (WORKER_A) under "phone_emulator-5554".
        let mut phone_launching = emu_group("emulator-5554", launcher_pid, "phone", None);
        phone_launching.avd_id = None;
        phone_launching.pids = vec![launcher_pid];
        phone_launching.primary_start_time = START - 1;
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &phone_launching)], 5.0);
        assert!(tracker.emulator_records.contains_key("emulator-5554"));

        std::fs::write(
            &log_path,
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
        )
        .unwrap();
        let mut phone_running = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone_running.pids = vec![launcher_pid, WORKER_A];
        phone_running.primary_start_time = START;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_running)], 10.0);
        assert!(!tracker.emulator_records.contains_key("emulator-5554"));
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        assert_eq!(chip_ids(&tracker, key), ["1"]);
        assert_eq!(tracker.emulator_records[key].chip_attach_s["BT"], (1.0, false));

        // Shutdown: Worker WORKER_A exits first while supervisor (5_000_350) is still alive for a tick.
        let mut phone_shutting_down =
            emu_group("emulator-5554", launcher_pid, "phone", Some("Phone"));
        phone_shutting_down.pids = vec![launcher_pid];
        phone_shutting_down.primary_start_time = START - 1;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_shutting_down)], 20.0);

        // Supervisor finishes exiting over the next two ticks.
        emu_tick_with(&mut tracker, &procs, &[], 22.0);
        emu_tick_with(&mut tracker, &procs, &[], 24.0);

        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.chip_attach_s["BT"], (1.0, false));
    }

    #[test]
    fn test_qemu_next_member_start_times_preserve_session_across_launcher_and_fishtank_shutdown() {
        // Case 1: Supervisor launcher started 5s BEFORE VM worker (START - 5 vs START),
        // lingers during shutdown under both AVD key and bare-serial fallback.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), "");
        let log_path = dir.path().join("android-test").join("netsimd").join("netsim_stderr.log");
        let launcher_pid = 5_000_500u32;
        let fishtank_pid = 5_000_501u32;
        let key = "phone_emulator-5554";

        std::fs::write(
            &log_path,
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
        )
        .unwrap();
        let mut phone_running = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone_running.pids = vec![launcher_pid, WORKER_A, fishtank_pid];
        phone_running.primary_start_time = START;
        phone_running.member_start_times = BTreeMap::from([
            (launcher_pid, START - 5),
            (WORKER_A, START),
            (fishtank_pid, START + 3),
        ]);
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_running)], 10.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        assert_eq!(tracker.emulator_records[key].chip_attach_s["BT"], (1.0, false));

        // Tick 2 (shutdown): VM worker and fishtank exited; launcher (START - 5) lingers,
        // first under AVD key and then under bare serial after pid_<worker>.ini is deleted.
        let mut launcher_lingering =
            emu_group("emulator-5554", launcher_pid, "phone", Some("Phone"));
        launcher_lingering.pids = vec![launcher_pid];
        launcher_lingering.primary_start_time = START - 5;
        launcher_lingering.member_start_times = BTreeMap::from([(launcher_pid, START - 5)]);
        emu_tick_with(&mut tracker, &procs, &[(key, &launcher_lingering)], 18.0);

        let mut launcher_bare = emu_group("emulator-5554", launcher_pid, "phone", None);
        launcher_bare.avd_id = None;
        launcher_bare.pids = vec![launcher_pid];
        launcher_bare.primary_start_time = START - 5;
        launcher_bare.member_start_times = BTreeMap::from([(launcher_pid, START - 5)]);
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &launcher_bare)], 19.0);
        assert!(
            !tracker.emulator_records.contains_key("emulator-5554"),
            "bare-serial shutdown launcher must map to existing AVD session"
        );

        emu_tick_with(&mut tracker, &procs, &[], 21.0);
        emu_tick_with(&mut tracker, &procs, &[], 23.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.chip_attach_s["BT"], (1.0, false));

        // Case 2: GUI companion (fishtank) started 3s AFTER VM worker (START + 3) and lingers
        // for a tick on shutdown when VM worker exits first; then fishtank's PID is reused later.
        let dir2 = tempfile::tempdir().unwrap();
        let (mut tracker2, procs2) = tracker_with_daemon_log(dir2.path(), "");
        let log_path2 = dir2.path().join("android-test").join("netsimd").join("netsim_stderr.log");
        std::fs::write(
            &log_path2,
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 1 (Phone)\n",
        )
        .unwrap();
        emu_tick_with(&mut tracker2, &procs2, &[(key, &phone_running)], 10.0);
        assert_eq!(tracker2.emulator_records[key].chip_attach_s["WIFI"], (1.0, false));

        let mut fishtank_lingering =
            emu_group("emulator-5554", fishtank_pid, "phone", Some("Phone"));
        fishtank_lingering.pids = vec![fishtank_pid];
        fishtank_lingering.primary_start_time = START + 3;
        fishtank_lingering.member_start_times = BTreeMap::from([(fishtank_pid, START + 3)]);
        emu_tick_with(&mut tracker2, &procs2, &[(key, &fishtank_lingering)], 18.0);
        emu_tick_with(&mut tracker2, &procs2, &[], 20.0);
        emu_tick_with(&mut tracker2, &procs2, &[], 22.0);
        let rec2 = &tracker2.emulator_records[key];
        assert_eq!((rec2.state.as_str(), rec2.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec2.chip_attach_s["WIFI"], (1.0, false));

        // Reusing fishtank_pid in a brand-new emulator session at START + 30 resets to CONNECTING.
        let mut reused_fishtank = emu_group("emulator-5554", fishtank_pid, "phone", Some("Phone"));
        reused_fishtank.pids = vec![fishtank_pid];
        reused_fishtank.primary_start_time = START + 30;
        reused_fishtank.member_start_times = BTreeMap::from([(fishtank_pid, START + 30)]);
        emu_tick_with(&mut tracker2, &[], &[(key, &reused_fishtank)], 30.0);
        assert_eq!(tracker2.emulator_records[key].state, "CONNECTING");
    }

    #[cfg(unix)]
    #[test]
    fn test_stale_ini_reached_through_symlink_alias_is_listed_once() {
        let root = tempfile::tempdir().unwrap();
        let real = root.path().join("real");
        std::fs::create_dir_all(&real).unwrap();
        let alias = root.path().join("alias");
        std::os::unix::fs::symlink(&real, &alias).unwrap();
        write_ini(
            &real.join("netsim.ini"),
            &format!("pid={DEAD_PID}\ngrpc.port=8554\n"),
            START - 60,
        );

        let mut tracker = tracker_for(&[&real], root.path());
        tick(&mut tracker, &[], START + 10);
        // A daemon's TMPDIR can reach the same file through a symlinked dir.
        tracker.custom_ini_dirs = vec![alias.clone()];
        tick(&mut tracker, &[], START + 20);
        assert_eq!(tracker.stale_inis.len(), 1, "{:?}", tracker.stale_inis.keys());
        assert_eq!(count_events(&tracker, "STALE_INI("), 1);
    }

    #[test]
    fn test_session_stats_keep_refining_approximate_chips() {
        // netsim 1.0.24 logs AddChip without a kind; session stats resolve
        // each kind against those timestamps. WIFI matches at once; BT only
        // after its AddChip line appears.
        let dir = tempfile::tempdir().unwrap();
        let log = "netsim I 09-25 22:37:06.000 service.rs:579 - DeviceActor: AddChip Phone (EMULATOR, Phone) to device Phone\n";
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), log);
        let art = dir.path().join("android-test").join("netsimd");
        let stats = art.join("netsim_session_stats.json");
        let payload = serde_json::json!({
            "device_stats": [{"device_id": 1, "kind": "EMULATOR"}],
            "radio_stats": [
                {"device_id": 1, "kind": "WIFI", "duration_secs": "99"},
                {"device_id": 1, "kind": "BLUETOOTH_LOW_ENERGY", "duration_secs": "90"}
            ]
        });
        write_ini(&stats, &payload.to_string(), START + 100);
        let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone.primary_start_time = START;
        let key = "phone_emulator-5554";

        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
        let chips = &tracker.emulator_records[key].chip_attach_s;
        assert_eq!(chips["WIFI"], (1.0, false));
        assert_eq!(chips["BT"], (10.0, true));

        let mut f =
            std::fs::OpenOptions::new().append(true).open(art.join("netsim_stderr.log")).unwrap();
        use std::io::Write;
        writeln!(f, "netsim I 09-25 22:37:15.000 service.rs:579 - DeviceActor: AddChip Phone (BLUETOOTH, Phone) to device Phone").unwrap();
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 12.0);
        let chips = &tracker.emulator_records[key].chip_attach_s;
        assert_eq!(chips["WIFI"], (1.0, false));
        assert_eq!(chips["BT"], (10.0, false));
    }

    #[test]
    fn test_same_avd_restart_after_held_miss_counts_connected_time_to_the_miss() {
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone_a = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let phone_b = emu_group("emulator-5554", WORKER_B, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_a)], 10.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        // Run 1 is missing for one tick (held), then run 2 takes the key.
        emu_tick_with(&mut tracker, &procs, &[], 20.0);
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 30.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!(rec.worker_pid, WORKER_B);
        assert_eq!(rec.disconnect_count, 1);
        assert_eq!(rec.connected_total_s, 10.0);
    }

    #[test]
    fn test_exited_daemon_releases_log_and_throttle_state() {
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        emu_tick_with(&mut tracker, &procs, &[("phone_emulator-5554", &phone)], 10.0);
        let d_key = (DAEMON_A, START);
        assert!(tracker.daemons[&d_key].tailer.is_some());
        tracker.pcap_mgr.last_harvest_by_daemon.insert(d_key, 10.0);
        tracker.pcap_mgr.checked_emulators.insert((d_key, "Phone".to_string()));

        emu_tick_with(&mut tracker, &[], &[("phone_emulator-5554", &phone)], 20.0);
        let d = &tracker.daemons[&d_key];
        assert!(d.exited);
        assert!(d.tailer.is_none());
        assert!(!tracker.pcap_mgr.last_harvest_by_daemon.contains_key(&d_key));
        assert!(tracker.pcap_mgr.checked_emulators.is_empty());
    }

    #[test]
    fn test_shared_log_truncation_client_exit_ready_port_and_1031_metadata() {
        let dir = tempfile::tempdir().unwrap();
        let art_dir = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_dir).unwrap();
        let custom_art = dir.path().join("custom_art");
        std::fs::create_dir_all(&custom_art).unwrap();
        let log_path = art_dir.join("netsim_stderr.log");
        let ini_path = dir.path().join("netsim.ini");
        write_ini(&ini_path, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        write_ini(
            &custom_art.join("netsim_session_stats.json"),
            r#"{"version":"1.0.30","device_stats":[],"radio_stats":[]}"#,
            START + 1,
        );

        std::fs::write(
            &log_path,
            format!(
                "netsim I 09-25 22:37:05.100 netsimd.rs:100 - Artifacts: \"{}\"\n\
                 netsim I 09-25 22:37:05.500 netsimd.rs:744 - Wrote to INI file {}\n",
                custom_art.display(),
                ini_path.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let proc_a = netsimd(DAEMON_A, START, dir.path());

        tick(&mut tracker, std::slice::from_ref(&proc_a), START + 6);
        let d_a = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d_a.artifact_dir, custom_art);
        assert_eq!(d_a.version.as_deref(), Some("1.0.30"));
        assert!(
            tracker
                .timeline
                .iter()
                .any(|(_, e)| e == "READY(pid=5000100, port=43929, ready_s=0.50s)"),
            "timeline={:?}",
            tracker.timeline
        );

        // Client-mode daemon spawns at START + 8 before flushing stderr, then writes
        // "Lock held by another process" + "NetsimDaemon main loop exited" and exits
        // while DAEMON_A stays alive.
        let daemon_c = DAEMON_B + 50;
        let proc_c = netsimd(daemon_c, START + 8, dir.path());
        tick(&mut tracker, &[proc_a.clone(), proc_c], START + 9);
        {
            use std::io::Write;
            let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
            writeln!(
                f,
                "netsim W 09-25 22:37:14.100 netsimd.rs:500 - Lock held by another process\n\
                 netsim I 09-25 22:37:14.200 netsimd.rs:900 - NetsimDaemon main loop exited"
            )
            .unwrap();
        }
        tick(&mut tracker, std::slice::from_ref(&proc_a), START + 10);
        assert!(
            tracker
                .timeline
                .iter()
                .any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={daemon_c})")),
            "timeline={:?}",
            tracker.timeline
        );

        // DAEMON_A logs graceful shutdown; on the next tick replacement DAEMON_B truncates
        // the shared log and binds port 43930 while DAEMON_A is still shutting down.
        {
            use std::io::Write;
            let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
            writeln!(f, "netsim I 09-25 22:37:15.500 netsimd.rs:800 - Shutting down gracefully")
                .unwrap();
        }
        tick(&mut tracker, std::slice::from_ref(&proc_a), START + 11);

        write_ini(&ini_path, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START + 12);
        let args_block = "    field: false,\n".repeat(25);
        std::fs::write(
            &log_path,
            format!(
                "netsim I 09-25 22:37:17.050 netsimd.rs:549 - Args {{\n{args_block}}}\n\
                 netsim I 09-25 22:37:17.100 netsimd.rs:750 - gRPC port: 43930\n\
                 netsim I 09-25 22:37:17.200 netsimd.rs:757 - Wrote to INI file {}\n",
                ini_path.display()
            ),
        )
        .unwrap();
        let proc_b = netsimd(DAEMON_B, START + 12, dir.path());
        tick(&mut tracker, &[proc_a, proc_b.clone()], START + 13);
        tick(&mut tracker, &[proc_b], START + 15);

        let d_a = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d_a.grpc_port, Some(43929));
        assert_eq!(d_a.exit_reason.as_deref(), Some("sigterm_sigint"));

        // Second check: "NetsimDaemon main loop exited" in shared log does not mask a
        // SIGKILL crash when the primary daemon leaves its INI behind, and unknown READY
        // port formats as port=N/A.
        let dir2 = tempfile::tempdir().unwrap();
        let art2 = dir2.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art2).unwrap();
        let ini2 = dir2.path().join("netsim.ini");
        write_ini(&ini2, &format!("pid={DAEMON_A}\n"), START);
        std::fs::write(
            art2.join("netsim_stderr.log"),
            "netsim I 09-25 22:37:05.500 netsimd.rs:744 - Wrote to INI file /nonexistent/netsim.ini\n\
             netsim I 09-25 22:37:06.000 netsimd.rs:900 - NetsimDaemon main loop exited\n",
        )
        .unwrap();
        let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
        tracker2.enable_log = true;
        tick(&mut tracker2, &[netsimd(DAEMON_A, START, dir2.path())], START + 5);
        assert!(
            tracker2
                .timeline
                .iter()
                .any(|(_, e)| e == "READY(pid=5000100, port=N/A, ready_s=0.50s)"),
            "timeline2={:?}",
            tracker2.timeline
        );
        tick(&mut tracker2, &[], START + 10);
        assert_eq!(
            tracker2.daemons[&(DAEMON_A, START)].exit_reason.as_deref(),
            Some("killed_or_crashed")
        );
    }

    #[test]
    fn test_session_key_pid_reuse_bystander_ready_disabled_and_settled_dashboard() {
        let dir_a = tempfile::tempdir().unwrap();
        let dir_b = tempfile::tempdir().unwrap();
        let art_a = dir_a.path().join("android-test").join("netsimd");
        let art_b = dir_b.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_a).unwrap();
        std::fs::create_dir_all(&art_b).unwrap();
        let ini_a = dir_a.path().join("netsim.ini");
        let ini_b = dir_b.path().join("netsim.ini");
        write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        std::fs::write(
            art_a.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Alpha)\n",
                ini_a.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
        tracker.enable_log = true;
        let proc_a = netsimd(DAEMON_A, START, dir_a.path());

        // Alpha connects on emulator-5554 with group_pids=[WORKER_A, WORKER_A + 1], then settles.
        let mut alpha = emu_group("emulator-5554", WORKER_A, "alpha", Some("Alpha"));
        alpha.pids = vec![WORKER_A, WORKER_A + 1];
        alpha.primary_start_time = START;
        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_a),
            &[("alpha_emulator-5554", &alpha)],
            5.0,
        );
        assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "CONNECTED");
        emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 10.0);
        emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 12.0);
        assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "DISCONNECTED");

        // A new emulator boots on emulator-5554 (bare serial first) reusing PID `WORKER_A + 1`:
        // it must NOT hijack settled `alpha_emulator-5554`.
        let mut zeta_bare = emu_group("emulator-5554", WORKER_A + 1, "zeta", None);
        zeta_bare.avd_id = None;
        zeta_bare.primary_start_time = START + 20;
        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_a),
            &[("emulator-5554", &zeta_bare)],
            20.0,
        );
        assert!(tracker.emulator_records.contains_key("emulator-5554"));
        assert_eq!(tracker.emulator_records["emulator-5554"].state, "CONNECTING");
        assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "DISCONNECTED");

        // Same-key restart reusing the exact same worker_pid with a newer start time also resets.
        let mut alpha_reused_pid = emu_group("emulator-5554", WORKER_A, "alpha", Some("Alpha"));
        alpha_reused_pid.primary_start_time = START + 21;
        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_a),
            &[("alpha_emulator-5554", &alpha_reused_pid)],
            21.0,
        );
        assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "CONNECTING");

        // Bystander single-daemon fallback on Tick 1 must not lock in DAEMON_A's ready_s once
        // phone60's own DAEMON_B spawns on port 43930 while DAEMON_A is still alive.
        let mut phone60 = emu_group("emulator-5560", WORKER_B, "phone60", Some("Phone60"));
        phone60.primary_start_time = START + 22;
        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_a),
            &[("phone60_emulator-5560", &phone60)],
            22.0,
        );
        assert!(tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s.is_some());
        write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START + 23);
        phone60.grpc_endpoint = Some("localhost:43930".to_string());
        std::fs::write(
            art_b.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:28.420 netsimd.rs:744 - Wrote to INI file {}\n",
                ini_b.display()
            ),
        )
        .unwrap();
        let proc_b = netsimd(DAEMON_B, START + 23, dir_b.path());
        emu_tick_with(
            &mut tracker,
            &[proc_a.clone(), proc_b],
            &[("phone60_emulator-5560", &phone60)],
            24.0,
        );
        let ready_60 = tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s;
        assert!((ready_60.unwrap_or(0.0) - 1.42).abs() < 0.05, "ready_60={ready_60:?}");

        // `-no-netsim` resolved on Tick 2 clears `daemon_key` so DISABLED never counts as joined.
        let mut disabled_emu = emu_group("emulator-5562", WORKER_B + 50, "no_net", Some("NoNet"));
        disabled_emu.primary_start_time = START + 25;
        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_a),
            &[("no_net_emulator-5562", &disabled_emu)],
            25.0,
        );
        disabled_emu.no_netsim = true;
        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_a),
            &[("no_net_emulator-5562", &disabled_emu)],
            27.0,
        );
        assert_eq!(tracker.emulator_records["no_net_emulator-5562"].state, "DISABLED");
        assert_eq!(tracker.emulator_records["no_net_emulator-5562"].daemon_key, None);

        // When all records on a serial are settled, dashboard picks the latest `first_seen_mono`.
        tracker.emulator_records.insert(
            "alpha_emulator-5570".to_string(),
            EmulatorNetsimRecord {
                storage_key: "alpha_emulator-5570".to_string(),
                serial: "emulator-5570".to_string(),
                first_seen_mono: 5.0,
                state: "FAILED(worker_exit_no_connect)".to_string(),
                ..Default::default()
            },
        );
        tracker.emulator_records.insert(
            "zeta_emulator-5570".to_string(),
            EmulatorNetsimRecord {
                storage_key: "zeta_emulator-5570".to_string(),
                serial: "emulator-5570".to_string(),
                first_seen_mono: 50.0,
                state: "DISCONNECTED".to_string(),
                disconnect_count: 1,
                ..Default::default()
            },
        );
        let dash = tracker.get_dashboard_snapshot(START as f64 + 60.0);
        assert!(
            dash.emulator_rows["emulator-5570"].contains("DISCONNECTED"),
            "row={}",
            dash.emulator_rows["emulator-5570"]
        );
    }

    #[test]
    fn test_exited_bystander_ready_reset_and_zombie_ini_writer_treated_as_stale() {
        let dir_a = tempfile::tempdir().unwrap();
        let dir_b = tempfile::tempdir().unwrap();
        let art_a = dir_a.path().join("android-test").join("netsimd");
        let art_b = dir_b.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_a).unwrap();
        std::fs::create_dir_all(&art_b).unwrap();
        let ini_a = dir_a.path().join("netsim.ini");
        let ini_b = dir_b.path().join("netsim.ini");
        write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        std::fs::write(
            art_a.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n",
                ini_a.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
        tracker.enable_log = true;
        let proc_a = netsimd(DAEMON_A, START, dir_a.path());

        // Pre-existing bystander DAEMON_A was already ready at START + 0.020; phone60 spawns
        // at START + 22 on Tick 1 while DAEMON_A is shutting down, and by Tick 2 DAEMON_A has
        // exited and phone60's own DAEMON_B is ready at START + 23.420 (ready_s = 1.42s).
        let mut phone60 = emu_group("emulator-5560", WORKER_B, "phone60", Some("Phone60"));
        phone60.primary_start_time = START + 22;
        emu_tick_with(&mut tracker, &[proc_a], &[("phone60_emulator-5560", &phone60)], 22.0);
        assert!(tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s.is_some());

        write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START + 23);
        phone60.grpc_endpoint = Some("localhost:43930".to_string());
        std::fs::write(
            art_b.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:28.420 netsimd.rs:744 - Wrote to INI file {}\n",
                ini_b.display()
            ),
        )
        .unwrap();
        let proc_b = netsimd(DAEMON_B, START + 23, dir_b.path());
        emu_tick_with(&mut tracker, &[proc_b], &[("phone60_emulator-5560", &phone60)], 24.0);
        let ready_60 = tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s;
        assert!((ready_60.unwrap_or(0.0) - 1.42).abs() < 0.05, "ready_60={ready_60:?}");

        #[cfg(target_os = "linux")]
        {
            let zdir = tempfile::tempdir().unwrap();
            let mut child = std::process::Command::new("/bin/true").spawn().unwrap();
            let zpid = child.id();
            let mut sys = sysinfo::System::new();
            let spid = sysinfo::Pid::from_u32(zpid);
            let mut saw_zombie = false;
            for _ in 0..500 {
                sys.refresh_processes_specifics(
                    sysinfo::ProcessesToUpdate::Some(&[spid]),
                    true,
                    sysinfo::ProcessRefreshKind::nothing(),
                );
                if sys.process(spid).is_some_and(|p| p.status() == sysinfo::ProcessStatus::Zombie) {
                    saw_zombie = true;
                    break;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            let zombie_alive = pid_exists(zpid);
            write_ini(
                &zdir.path().join("netsim.ini"),
                &format!("grpc.port=43999\npid={zpid}\n"),
                START,
            );
            let mut ztracker = tracker_for(&[zdir.path()], zdir.path());
            tick(&mut ztracker, &[], START + 10);
            child.wait().unwrap();
            assert!(saw_zombie, "child never entered Zombie state");
            assert!(!zombie_alive, "zombie PID must not be treated as alive by pid_exists");
            assert_eq!(ztracker.stale_inis.len(), 1);
        }
    }

    #[cfg(unix)]
    #[test]
    fn test_foreign_daemon_empty_env_does_not_hijack_own_artifacts_or_join() {
        let dir = tempfile::tempdir().unwrap();
        let out_dir = tempfile::tempdir().unwrap();
        let art_dir = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_dir).unwrap();
        let ini_own = dir.path().join("netsim.ini");
        write_ini(&ini_own, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        std::fs::write(
            art_dir.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.050 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.051 netsimd.rs:750 - gRPC port: 43929\n\
                 netsim I 09-25 22:37:05.200 device.rs:100 - DeviceActor: Create device Phone\n\
                 netsim I 09-25 22:37:05.300 device.rs:110 - DeviceActor: AddChip 10 (kind WIFI, name \"wlan0\") to device 1 (Phone)\n",
                ini_own.display()
            ),
        )
        .unwrap();
        std::fs::write(art_dir.join("netsim_artifacts_own.zip"), b"PK\x03\x04ownzip").unwrap();
        std::fs::write(art_dir.join("netsim_session_stats.json"), r#"{"version":"1.0.31"}"#)
            .unwrap();
        std::fs::File::options()
            .write(true)
            .open(art_dir.join("netsim_session_stats.json"))
            .unwrap()
            .set_modified(UNIX_EPOCH + Duration::from_secs(START + 1))
            .unwrap();

        let mut tracker = NetsimTracker::new(Some(""), 256, true, out_dir.path());
        tracker.host_os = HostOs::Linux;
        tracker.custom_ini_dirs = vec![dir.path().to_path_buf()];
        tracker.custom_proc_locks = Some(String::new());
        tracker.pcap_mgr.cli_runner = std::sync::Arc::new(|_, _| {
            Some(
                "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                 1  | Phone       | WIFI      | off   | 0         | 0       | 0\n"
                    .to_string(),
            )
        });

        let foreign_empty_1 = NetsimDaemonProc {
            pid: DAEMON_A - 20,
            parent: Some(1),
            start_time: START - 10,
            exe: Some(PathBuf::from("/sdk/emulator/netsimd")),
            cmd: vec!["/sdk/emulator/netsimd".to_string()],
            environ: HashMap::new(),
            same_user: false,
            ..Default::default()
        };
        let foreign_empty_2 = NetsimDaemonProc {
            pid: DAEMON_A - 19,
            parent: Some(1),
            start_time: START - 9,
            exe: Some(PathBuf::from("/sdk/emulator/netsimd")),
            cmd: vec!["/sdk/emulator/netsimd".to_string()],
            environ: HashMap::new(),
            same_user: false,
            ..Default::default()
        };
        let mut foreign_crafted = netsimd(DAEMON_A - 10, START - 5, dir.path());
        foreign_crafted.same_user = false;
        let own_proc = netsimd(DAEMON_A, START, dir.path());

        let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone.primary_start_time = START;
        phone.grpc_endpoint = None;

        emu_tick_with(
            &mut tracker,
            &[foreign_empty_1.clone(), foreign_empty_2.clone(), foreign_crafted.clone(), own_proc],
            &[("phone_emulator-5554", &phone)],
            1.0,
        );

        for fk in
            [(DAEMON_A - 20, START - 10), (DAEMON_A - 19, START - 9), (DAEMON_A - 10, START - 5)]
        {
            let fd = &tracker.daemons[&fk];
            assert!(
                fd.artifact_dir.as_os_str().is_empty(),
                "foreign daemon adopted {:?}",
                fd.artifact_dir
            );
            assert!(fd.log_path.as_os_str().is_empty());
            assert!(fd.tailer.is_none());
            assert_eq!(fd.grpc_port, None);
        }
        assert!(
            !tracker.timeline.iter().any(|(_, e)| e.contains("DUPLICATE_ARTIFACT_DIR")),
            "timeline={:?}",
            tracker.timeline
        );
        assert_eq!(
            tracker.emulator_records["phone_emulator-5554"].daemon_key,
            Some((DAEMON_A, START)),
            "emulator must join its sole same-user daemon despite active foreign daemons"
        );
        assert_eq!(tracker.emulator_records["phone_emulator-5554"].state, "CONNECTED");
        assert!(
            tracker.pcap_mgr.enabled_by_monitor.contains_key(&((DAEMON_A, START), 1)),
            "auto-capture must run against the sole same-user daemon"
        );

        // Own daemon exits while foreign daemons stay alive: exit harvest + stats backfill
        // must still run, and Phone must settle rather than falling back to a foreign daemon.
        let _ = std::fs::remove_file(&ini_own);
        emu_tick_with(
            &mut tracker,
            &[foreign_empty_1, foreign_empty_2, foreign_crafted],
            &[("phone_emulator-5554", &phone)],
            5.0,
        );
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].version.as_deref(), Some("1.0.31"));
        assert!(
            !tracker.pcap_mgr.rescued_zips.is_empty(),
            "own zip harvest must not be blocked by active foreign daemons"
        );
        assert_eq!(tracker.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
    }

    #[test]
    fn test_qemu_next_shutdown_companions_do_not_join_bystander_or_unsettle() {
        let dir_a = tempfile::tempdir().unwrap();
        let dir_b = tempfile::tempdir().unwrap();
        let art_a = dir_a.path().join("android-test").join("netsimd");
        let art_b = dir_b.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_a).unwrap();
        std::fs::create_dir_all(&art_b).unwrap();
        let ini_a = dir_a.path().join("netsim.ini");
        let ini_b = dir_b.path().join("netsim_2.ini");
        write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START);
        std::fs::write(
            art_a.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
                ini_a.display()
            ),
        )
        .unwrap();
        std::fs::write(
            art_b.join("netsim_2_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.030 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.400 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 1 (Glasses)\n\
                 netsim I 09-25 22:37:05.500 ap_actor.rs:100 - ApActor: Associated 02:00:00:00:00:01\n",
                ini_b.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
        tracker.enable_log = true;
        let proc_a = netsimd(DAEMON_A, START, dir_a.path());
        let mut proc_b = netsimd(DAEMON_B, START, dir_b.path());
        proc_b.cmd.push("--instance".to_string());
        proc_b.cmd.push("2".to_string());

        let launcher_pid = 5_000_600u32;
        let fishtank_pid = 5_000_601u32;
        let phone_key = "phone_emulator-5554";
        let glasses_key = "glasses_emulator-5556";

        let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone.pids = vec![launcher_pid, WORKER_A, fishtank_pid];
        phone.primary_start_time = START;
        phone.member_start_times = BTreeMap::from([
            (launcher_pid, START - 2),
            (WORKER_A, START),
            (fishtank_pid, START + 1),
        ]);
        phone.grpc_endpoint = Some("localhost:43929".to_string());

        let mut glasses = emu_group("emulator-5556", WORKER_B, "glasses", Some("Glasses"));
        glasses.primary_start_time = START;
        glasses.grpc_endpoint = Some("localhost:43930".to_string());

        emu_tick_with(
            &mut tracker,
            &[proc_a.clone(), proc_b.clone()],
            &[(phone_key, &phone), (glasses_key, &glasses)],
            10.0,
        );
        assert_eq!(tracker.emulator_records[phone_key].state, "CONNECTED");
        assert_eq!(tracker.emulator_records[phone_key].chip_attach_s["BT"], (1.0, false));
        assert_eq!(tracker.emulator_records[glasses_key].wifi_associated_s, Some(0.5));

        // VM worker and DAEMON_A exit simultaneously while fishtank lingers for 1 tick;
        // Phone must settle cleanly without joining bystander DAEMON_B or wiping its BT chip.
        let _ = std::fs::remove_file(&ini_a);
        let mut fishtank_only = emu_group("emulator-5554", fishtank_pid, "phone", Some("Phone"));
        fishtank_only.pids = vec![fishtank_pid];
        fishtank_only.primary_start_time = START + 1;
        fishtank_only.member_start_times = BTreeMap::from([(fishtank_pid, START + 1)]);
        fishtank_only.grpc_endpoint = None;

        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_b),
            &[(phone_key, &fishtank_only), (glasses_key, &glasses)],
            12.0,
        );
        let phone_rec = &tracker.emulator_records[phone_key];
        assert_eq!(phone_rec.daemon_key, Some((DAEMON_A, START)));
        assert_eq!(phone_rec.state, "DISCONNECTED");
        assert_eq!(phone_rec.disconnect_count, 1);
        assert_eq!(phone_rec.chip_attach_s.get("BT"), Some(&(1.0, false)));
        assert_eq!(tracker.emulator_records[glasses_key].wifi_associated_s, Some(0.5));

        // A settled record whose companion reappears briefly while its daemon is still alive
        // must remain settled rather than flipping back to CONNECTED.
        let dir_c = tempfile::tempdir().unwrap();
        let (mut tracker2, procs2) = tracker_with_daemon_log(
            dir_c.path(),
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
        );
        emu_tick_with(&mut tracker2, &procs2, &[(phone_key, &phone)], 10.0);
        emu_tick_with(&mut tracker2, &procs2, &[], 12.0);
        emu_tick_with(&mut tracker2, &procs2, &[], 14.0);
        assert_eq!(tracker2.emulator_records[phone_key].state, "DISCONNECTED");
        assert_eq!(tracker2.emulator_records[phone_key].disconnect_count, 1);

        let mut launcher_only = emu_group("emulator-5554", launcher_pid, "phone", Some("Phone"));
        launcher_only.pids = vec![launcher_pid];
        launcher_only.primary_start_time = START - 2;
        launcher_only.member_start_times = BTreeMap::from([(launcher_pid, START - 2)]);
        emu_tick_with(&mut tracker2, &procs2, &[(phone_key, &launcher_only)], 16.0);
        assert_eq!(tracker2.emulator_records[phone_key].state, "DISCONNECTED");
        assert_eq!(tracker2.emulator_records[phone_key].disconnect_count, 1);
    }

    #[test]
    fn test_logged_stale_ini_does_not_override_paired_ini_or_exit_reason_and_exited_disabled_row_yields()
     {
        let dir = tempfile::tempdir().unwrap();
        let art = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art).unwrap();
        let stale_ini = dir.path().join("netsim.ini");
        let own_ini = dir.path().join("netsim_2.ini");
        write_ini(&stale_ini, &format!("grpc.port=41111\npid={DEAD_PID}\n"), START - 30);
        write_ini(&own_ini, &format!("grpc.port=43930\npid={DAEMON_A}\n"), START);

        // Log contains an earlier stale `Wrote to INI file .../netsim.ini` line while
        // `reconcile_ini_files` pairs DAEMON_A (`--instance 2`) with `netsim_2.ini`.
        std::fs::write(
            art.join("netsim_2_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.010 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.020 netsimd.rs:750 - gRPC port: 43930\n",
                stale_ini.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let mut proc_a = netsimd(DAEMON_A, START, dir.path());
        proc_a.cmd.push("--instance".to_string());
        proc_a.cmd.push("2".to_string());

        emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 1.0);
        assert_eq!(
            tracker.daemons[&(DAEMON_A, START)].ini_path.as_deref(),
            Some(own_ini.as_path())
        );

        // Subsequent poll must NOT overwrite `own_ini` with `stale_ini`.
        emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 2.0);
        assert_eq!(
            tracker.daemons[&(DAEMON_A, START)].ini_path.as_deref(),
            Some(own_ini.as_path())
        );

        // Clean exit deletes `own_ini` (`netsim_2.ini`), leaving `stale_ini` (`netsim.ini`) on disk:
        // exit reason must be `clean_unknown`, not `killed_or_crashed`.
        let _ = std::fs::remove_file(&own_ini);
        emu_tick_with(&mut tracker, &[], &[], 4.0);
        assert_eq!(
            tracker.daemons[&(DAEMON_A, START)].exit_reason.as_deref(),
            Some("clean_unknown")
        );

        // Exited `-no-netsim` (`DISABLED`) record on emulator-5572 must not hide a newer
        // settled (`DISCONNECTED`) session on the same serial in dashboard snapshots.
        let mut disabled_emu = emu_group("emulator-5572", WORKER_A, "nonet", Some("NoNet"));
        disabled_emu.no_netsim = true;
        disabled_emu.primary_start_time = START + 5;
        emu_tick_with(&mut tracker, &[], &[("nonet_emulator-5572", &disabled_emu)], 5.0);
        emu_tick_with(&mut tracker, &[], &[], 7.0);
        emu_tick_with(&mut tracker, &[], &[], 9.0);
        assert_eq!(tracker.emulator_records["nonet_emulator-5572"].state, "DISABLED");

        tracker.emulator_records.insert(
            "later_emulator-5572".to_string(),
            EmulatorNetsimRecord {
                storage_key: "later_emulator-5572".to_string(),
                serial: "emulator-5572".to_string(),
                first_seen_mono: 20.0,
                state: "DISCONNECTED".to_string(),
                disconnect_count: 1,
                ..Default::default()
            },
        );
        let dash = tracker.get_dashboard_snapshot(START as f64 + 25.0);
        assert!(
            dash.emulator_rows["emulator-5572"].contains("DISCONNECTED"),
            "row={}",
            dash.emulator_rows["emulator-5572"]
        );
    }

    #[test]
    fn test_daemon_exit_before_worker_does_not_join_bystander_daemon() {
        let dir_a = tempfile::tempdir().unwrap();
        let dir_b = tempfile::tempdir().unwrap();
        let art_a = dir_a.path().join("android-test").join("netsimd");
        let art_b = dir_b.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_a).unwrap();
        std::fs::create_dir_all(&art_b).unwrap();
        let ini_a = dir_a.path().join("netsim.ini");
        let ini_b = dir_b.path().join("netsim_2.ini");
        write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START);
        std::fs::write(
            art_a.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Pixel 8)\n",
                ini_a.display()
            ),
        )
        .unwrap();
        let log_b = art_b.join("netsim_2_stderr.log");
        std::fs::write(
            &log_b,
            format!(
                "netsim I 09-25 22:37:05.030 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.400 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 1 (Pixel 8)\n",
                ini_b.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
        tracker.enable_log = true;
        let proc_a = netsimd(DAEMON_A, START, dir_a.path());
        let mut proc_b = netsimd(DAEMON_B, START, dir_b.path());
        proc_b.cmd.extend(["--instance".to_string(), "2".to_string()]);

        let mut phone = emu_group("emulator-5554", WORKER_A, "pixel8_a", Some("Pixel 8"));
        phone.pids = vec![WORKER_A];
        phone.primary_start_time = START;
        phone.member_start_times = BTreeMap::from([(WORKER_A, START)]);
        phone.grpc_endpoint = Some("localhost:43929".to_string());

        let mut glasses = emu_group("emulator-5556", WORKER_B, "pixel8_b", Some("Pixel 8"));
        glasses.pids = vec![WORKER_B];
        glasses.primary_start_time = START;
        glasses.member_start_times = BTreeMap::from([(WORKER_B, START)]);
        glasses.grpc_endpoint = Some("localhost:43930".to_string());

        let key_a = "pixel8_a_emulator-5554";
        let key_b = "pixel8_b_emulator-5556";
        emu_tick_with(
            &mut tracker,
            &[proc_a, proc_b.clone()],
            &[(key_a, &phone), (key_b, &glasses)],
            10.0,
        );
        assert_eq!(tracker.emulator_records[key_a].chip_attach_s.get("BT"), Some(&(1.0, false)));

        // DAEMON_A exits while WORKER_A is still alive (e.g. saving Quickboot snapshot);
        // Glasses on DAEMON_B associates with Wi-Fi at START + 11s.
        let _ = std::fs::remove_file(&ini_a);
        {
            use std::io::Write;
            writeln!(
                std::fs::OpenOptions::new().append(true).open(&log_b).unwrap(),
                "netsim I 09-25 22:37:16.000 ap_actor.rs:100 - ApActor: Associated 02:00:00:00:00:01"
            )
            .unwrap();
        }
        emu_tick_with(
            &mut tracker,
            std::slice::from_ref(&proc_b),
            &[(key_a, &phone), (key_b, &glasses)],
            12.0,
        );

        let rec_a = &tracker.emulator_records[key_a];
        let rec_b = &tracker.emulator_records[key_b];
        assert_eq!(rec_a.daemon_key, Some((DAEMON_A, START)));
        assert_eq!(rec_a.state, "DISCONNECTED");
        assert_eq!(rec_a.disconnect_count, 1);
        assert_eq!(rec_a.chip_attach_s.get("BT"), Some(&(1.0, false)));
        assert_eq!(rec_b.wifi_associated_s, Some(11.0));
    }

    #[test]
    fn test_prior_bare_serial_session_preserved_and_reused_pid_bare_migration() {
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(
            dir.path(),
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (emulator-5554)\n",
        );

        // Part 1: Unnamed Session 1 runs on emulator-5554 from t=5..25 (20s connected), then exits.
        let mut unnamed_g = EmuGroupNetsimInfo {
            serial: "emulator-5554".to_string(),
            port: 5554,
            primary_pid: WORKER_A,
            primary_start_time: START,
            pids: vec![WORKER_A],
            member_start_times: BTreeMap::from([(WORKER_A, START)]),
            ..Default::default()
        };
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &unnamed_g)], 5.0);
        assert_eq!(tracker.emulator_records["emulator-5554"].state, "CONNECTED");
        emu_tick_with(&mut tracker, &procs, &[], 25.0);
        emu_tick_with(&mut tracker, &procs, &[], 27.0);
        assert_eq!(tracker.emulator_records["emulator-5554"].state, "DISCONNECTED");
        assert_eq!(tracker.emulator_records["emulator-5554"].connected_total_s, 20.0);

        // Session 2 (named Pixel_8) launches at t=40 with 1 bare-serial launcher tick,
        // then resolves Pixel_8 at t=42.
        let launcher_pid = WORKER_B - 1;
        unnamed_g.primary_pid = launcher_pid;
        unnamed_g.primary_start_time = START + 40;
        unnamed_g.pids = vec![launcher_pid];
        unnamed_g.member_start_times = BTreeMap::from([(launcher_pid, START + 40)]);
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &unnamed_g)], 40.0);

        let mut g_pixel = emu_group("emulator-5554", WORKER_B, "Pixel_8", Some("Pixel 8"));
        g_pixel.primary_start_time = START + 41;
        g_pixel.pids = vec![launcher_pid, WORKER_B];
        g_pixel.member_start_times =
            BTreeMap::from([(launcher_pid, START + 40), (WORKER_B, START + 41)]);
        emu_tick_with(&mut tracker, &procs, &[("Pixel_8_emulator-5554", &g_pixel)], 42.0);

        let session1 = &tracker.emulator_records["emulator-5554"];
        assert_eq!(session1.state, "DISCONNECTED");
        assert_eq!(session1.disconnect_count, 1);
        assert_eq!(session1.connected_total_s, 20.0);

        let pixel_rec = &tracker.emulator_records["Pixel_8_emulator-5554"];
        assert_eq!(pixel_rec.disconnect_count, 0);
        assert_eq!(pixel_rec.connected_total_s, 0.0);
        assert_eq!(pixel_rec.first_seen_mono, 40.0);

        // Part 2: Reused OS PID across same-AVD restart with 1 bare-serial tick migrates cleanly.
        let dir2 = tempfile::tempdir().unwrap();
        let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
        let mut phone1 = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
        phone1.primary_start_time = START;
        phone1.pids = vec![WORKER_A];
        phone1.member_start_times = BTreeMap::from([(WORKER_A, START)]);
        emu_tick(&mut tracker2, &[("Phone_emulator-5554", &phone1)], 1.0);
        emu_tick(&mut tracker2, &[], 5.0);
        emu_tick(&mut tracker2, &[], 7.0);
        assert_eq!(
            tracker2.emulator_records["Phone_emulator-5554"].state,
            "FAILED(worker_exit_no_connect)"
        );

        let mut phone2_bare = EmuGroupNetsimInfo {
            serial: "emulator-5554".to_string(),
            port: 5554,
            primary_pid: WORKER_A,
            primary_start_time: START + 20,
            pids: vec![WORKER_A],
            member_start_times: BTreeMap::from([(WORKER_A, START + 20)]),
            ..Default::default()
        };
        emu_tick(&mut tracker2, &[("emulator-5554", &phone2_bare)], 20.0);
        tracker2.emulator_records.get_mut("emulator-5554").unwrap().first_transport_ts =
            Some(START as f64 + 20.5);

        phone2_bare.avd_id = Some("Phone".to_string());
        phone2_bare.display_name = Some("Phone".to_string());
        emu_tick(&mut tracker2, &[("Phone_emulator-5554", &phone2_bare)], 22.0);
        assert!(!tracker2.emulator_records.contains_key("emulator-5554"));
        assert_eq!(
            tracker2.emulator_records["Phone_emulator-5554"].first_transport_ts,
            Some(START as f64 + 20.5)
        );
    }

    #[test]
    fn test_duplicate_display_names_disambiguate_by_peer_port_or_stay_ambiguous() {
        fn two_emu_lsof(_: &str, _: &[&str]) -> Option<String> {
            Some(
                "p5000300\nf42\nn127.0.0.1:50001->127.0.0.1:43929\n\
                 p5000400\nf43\nn127.0.0.1:50002->127.0.0.1:43929\n"
                    .to_string(),
            )
        }

        // Part 1: Modern netsimd logs AddChipByGuid peer ports -> each duplicate-named
        // emulator claims only its own socket's chips and clears `ambiguous`.
        let dir1 = tempfile::tempdir().unwrap();
        std::fs::write(
            dir1.path().join("netsim.ini"),
            format!("grpc.port=43929\npid={DAEMON_A}\n"),
        )
        .unwrap();
        let modern_log = concat!(
            "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for WIFI on device Pixel 8\n",
            "netsim I 09-25 22:37:06.000 service.rs:550 - DeviceActor: AddChipByGuid 5554 for device 127.0.0.1:50001\n",
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel 8)\n",
            "netsim I 09-25 22:37:08.000 netsimd.rs:106 - Handling new connection for BLUETOOTH on device Pixel 8\n",
            "netsim I 09-25 22:37:08.000 service.rs:550 - DeviceActor: AddChipByGuid 5556 for device 127.0.0.1:50002\n",
            "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Pixel 8)\n",
        );
        let (mut tracker1, procs1) = tracker_with_daemon_log(dir1.path(), modern_log);
        tracker1.host_os = HostOs::MacOs;
        tracker1.socket_cli_runner = two_emu_lsof;

        let mut g_a = emu_group("emulator-5554", WORKER_A, "Pixel_8_a", Some("Pixel 8"));
        g_a.primary_start_time = START;
        g_a.pids = vec![WORKER_A];
        g_a.member_start_times = BTreeMap::from([(WORKER_A, START)]);

        let mut g_b = emu_group("emulator-5556", WORKER_B, "Pixel_8_b", Some("Pixel 8"));
        g_b.primary_start_time = START + 2;
        g_b.pids = vec![WORKER_B];
        g_b.member_start_times = BTreeMap::from([(WORKER_B, START + 2)]);

        emu_tick_with(
            &mut tracker1,
            &procs1,
            &[("Pixel_8_a_emulator-5554", &g_a), ("Pixel_8_b_emulator-5556", &g_b)],
            10.0,
        );

        let rec_a = &tracker1.emulator_records["Pixel_8_a_emulator-5554"];
        let rec_b = &tracker1.emulator_records["Pixel_8_b_emulator-5556"];
        assert_eq!(rec_a.chip_ids, BTreeSet::from(["1".to_string()]));
        assert!(rec_a.chip_attach_s.contains_key("WIFI"));
        assert!(!rec_a.chip_attach_s.contains_key("BT"));
        assert!(!rec_a.ambiguous);

        assert_eq!(rec_b.chip_ids, BTreeSet::from(["2".to_string()]));
        assert!(rec_b.chip_attach_s.contains_key("BT"));
        assert!(!rec_b.chip_attach_s.contains_key("WIFI"));
        assert!(!rec_b.ambiguous);

        // Part 2: Legacy netsimd without AddChipByGuid -> chips match only by shared
        // device_name without peer_port, so `ambiguous` must remain `true`.
        let dir2 = tempfile::tempdir().unwrap();
        std::fs::write(
            dir2.path().join("netsim.ini"),
            format!("grpc.port=43929\npid={DAEMON_A}\n"),
        )
        .unwrap();
        let legacy_log = "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel 8)\n";
        let (mut tracker2, procs2) = tracker_with_daemon_log(dir2.path(), legacy_log);
        tracker2.host_os = HostOs::MacOs;
        tracker2.socket_cli_runner = two_emu_lsof;

        emu_tick_with(
            &mut tracker2,
            &procs2,
            &[("Pixel_8_a_emulator-5554", &g_a), ("Pixel_8_b_emulator-5556", &g_b)],
            10.0,
        );
        assert!(tracker2.emulator_records["Pixel_8_a_emulator-5554"].ambiguous);
        assert!(tracker2.emulator_records["Pixel_8_b_emulator-5556"].ambiguous);
    }

    #[test]
    fn test_precise_start_time_rejects_recycled_pid_and_zero_ports_ignored() {
        let dir = tempfile::tempdir().unwrap();
        let ini_path = dir.path().join("netsim.ini");
        std::fs::write(&ini_path, format!("grpc.port=0\npid={DAEMON_A}\n")).unwrap();
        let obs = IniObservation::read(ini_path, &HashMap::new());
        assert_eq!(obs.port, None, "grpc.port=0 in netsim.ini must be rejected");

        let mut g = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
        g.grpc_endpoint = Some("localhost:0".to_string());
        assert_eq!(NetsimTracker::extract_target_port(Some(&g)), None);

        #[cfg(target_os = "linux")]
        {
            let stale_create = 1_000_000.0;
            let got = get_proc_precise_start_time(std::process::id(), stale_create);
            assert!(
                (got - stale_create).abs() < 1e-6,
                "recycled PID /proc/<pid>/stat outside 2s window must fall back to create_time, got {got}"
            );
        }

        #[cfg(unix)]
        {
            let real_art = dir.path().join("real_art");
            std::fs::create_dir_all(&real_art).unwrap();
            let android_tmp = dir.path().join("atmp");
            std::fs::create_dir_all(android_tmp.join("android-test")).unwrap();
            std::os::unix::fs::symlink(&real_art, android_tmp.join("android-test").join("netsimd"))
                .unwrap();
            let mut proc_sym = netsimd(DAEMON_A, START, dir.path());
            proc_sym
                .environ
                .insert("ANDROID_TMP".to_string(), android_tmp.to_string_lossy().into());

            let mut tracker = tracker_for(&[dir.path()], dir.path());
            tracker.discover_and_poll_daemons(&[proc_sym], &HashMap::new(), START as f64, 1.0);
            assert!(
                tracker.daemons[&(DAEMON_A, START)].artifact_dir.as_os_str().is_empty(),
                "leaf-symlinked artifact_dir must be cleared on daemon registration"
            );
        }
    }

    #[test]
    fn test_settled_bare_preserved_on_named_shutdown_and_worker_reset_clears_names() {
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(
            dir.path(),
            concat!(
                "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Wear_Bare)\n",
                "netsim I 09-25 22:37:46.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Phone)\n",
            ),
        );

        // Part 1: Unnamed bare-serial Session 1 runs t=5..25 (20s connected) and settles.
        let unnamed = EmuGroupNetsimInfo {
            serial: "emulator-5554".to_string(),
            port: 5554,
            primary_pid: WORKER_A,
            primary_start_time: START,
            pids: vec![WORKER_A],
            member_start_times: BTreeMap::from([(WORKER_A, START)]),
            ..Default::default()
        };
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &unnamed)], 5.0);
        emu_tick_with(&mut tracker, &procs, &[], 25.0);
        emu_tick_with(&mut tracker, &procs, &[], 27.0);
        assert_eq!(tracker.emulator_records["emulator-5554"].state, "DISCONNECTED");
        assert_eq!(tracker.emulator_records["emulator-5554"].connected_total_s, 20.0);

        // Named QEMU-next Session 2 ("Phone_emulator-5554") runs, then shuts down with its
        // launcher lingering under bare-serial fallback "emulator-5554".
        let launcher_pid = WORKER_B - 1;
        let mut phone = emu_group("emulator-5554", WORKER_B, "Phone", Some("Phone"));
        phone.primary_start_time = START + 40;
        phone.pids = vec![launcher_pid, WORKER_B];
        phone.member_start_times =
            BTreeMap::from([(launcher_pid, START + 39), (WORKER_B, START + 40)]);
        emu_tick_with(&mut tracker, &procs, &[("Phone_emulator-5554", &phone)], 42.0);
        assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "CONNECTED");

        let launcher_bare = EmuGroupNetsimInfo {
            serial: "emulator-5554".to_string(),
            port: 5554,
            primary_pid: launcher_pid,
            primary_start_time: START + 39,
            pids: vec![launcher_pid],
            member_start_times: BTreeMap::from([(launcher_pid, START + 39)]),
            ..Default::default()
        };
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &launcher_bare)], 50.0);
        emu_tick_with(&mut tracker, &procs, &[], 52.0);
        emu_tick_with(&mut tracker, &procs, &[], 54.0);

        let bare_rec = &tracker.emulator_records["emulator-5554"];
        assert_eq!(bare_rec.state, "DISCONNECTED");
        assert_eq!(bare_rec.connected_total_s, 20.0);
        assert_eq!(bare_rec.device_name.as_deref(), Some("Wear_Bare"));
        assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "DISCONNECTED");

        // Part 2: Session 3 starts with 1 bare-serial launcher tick at t=60, then resolves
        // avd_id="Pixel_8" with display_name=None (no pid_<pid>.ini avd.name yet).
        let dir2 = tempfile::tempdir().unwrap();
        let (mut tracker2, procs2) = tracker_with_daemon_log(
            dir2.path(),
            concat!(
                "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Wear_Bare)\n",
                "netsim I 09-25 22:37:47.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Pixel_8)\n",
            ),
        );
        emu_tick_with(&mut tracker2, &procs2, &[("emulator-5554", &unnamed)], 5.0);
        emu_tick_with(&mut tracker2, &procs2, &[], 25.0);
        emu_tick_with(&mut tracker2, &procs2, &[], 27.0);

        let unnamed2 = EmuGroupNetsimInfo {
            serial: "emulator-5554".to_string(),
            port: 5554,
            primary_pid: launcher_pid,
            primary_start_time: START + 40,
            pids: vec![launcher_pid],
            member_start_times: BTreeMap::from([(launcher_pid, START + 40)]),
            ..Default::default()
        };
        let other = emu_group("emulator-5556", WORKER_B + 10, "Other", Some("Other"));
        emu_tick_with(
            &mut tracker2,
            &procs2,
            &[("emulator-5554", &unnamed2), ("Other_emulator-5556", &other)],
            40.0,
        );
        assert_eq!(tracker2.emulator_records["emulator-5554"].avd_id, None);
        assert_eq!(tracker2.emulator_records["emulator-5554"].device_name, None);

        let mut pixel = emu_group("emulator-5554", WORKER_B, "Pixel_8", None);
        pixel.primary_start_time = START + 41;
        pixel.pids = vec![launcher_pid, WORKER_B];
        pixel.member_start_times =
            BTreeMap::from([(launcher_pid, START + 40), (WORKER_B, START + 41)]);
        emu_tick_with(
            &mut tracker2,
            &procs2,
            &[("Pixel_8_emulator-5554", &pixel), ("Other_emulator-5556", &other)],
            42.0,
        );

        assert_eq!(
            tracker2.emulator_records["emulator-5554"].device_name.as_deref(),
            Some("Wear_Bare")
        );
        let pixel_rec = &tracker2.emulator_records["Pixel_8_emulator-5554"];
        assert_eq!(pixel_rec.device_name.as_deref(), Some("Pixel_8"));
        assert_eq!(pixel_rec.state, "CONNECTED");
    }

    #[test]
    fn test_held_disabled_unattached_bystander_and_joined_worker_exit_parity() {
        // 1. A `-no-netsim` (`DISABLED`) emulator missing 1 host-scan tick (held)
        // must not inflate `held_count` or break `single_live` fallback for the
        // sole netsim-enabled emulator.
        let dir1 = tempfile::tempdir().unwrap();
        let (mut tracker1, procs1) = tracker_with_daemon_log(
            dir1.path(),
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip wlan0 (WIFI, 0) to device CustomDevice\n",
        );
        let phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
        let mut disabled = emu_group("emulator-5556", WORKER_B, "NoNetsim", Some("NoNetsim"));
        disabled.no_netsim = true;

        emu_tick_with(
            &mut tracker1,
            &procs1,
            &[("Phone_emulator-5554", &phone), ("NoNetsim_emulator-5556", &disabled)],
            5.0,
        );
        assert_eq!(tracker1.emulator_records["NoNetsim_emulator-5556"].state, "DISABLED");
        assert_eq!(tracker1.emulator_records["Phone_emulator-5554"].state, "CONNECTED");
        assert_eq!(tracker1.emulator_records["Phone_emulator-5554"].unknown_chip_count, 1);

        // `disabled` misses 1 tick (held); `phone` must stay CONNECTED with 0 disconnects.
        emu_tick_with(&mut tracker1, &procs1, &[("Phone_emulator-5554", &phone)], 10.0);
        let phone_rec = &tracker1.emulator_records["Phone_emulator-5554"];
        assert_eq!(phone_rec.state, "CONNECTED");
        assert_eq!(phone_rec.disconnect_count, 0);
        assert_eq!(phone_rec.unknown_chip_count, 1);

        // 2. An unattached emulator that provisionally parked on a bystander daemon
        // (`--instance 1`) can fall back to its own `--instance 2` daemon once the
        // bystander exits, unless its `grpc_endpoint` conflicts with the daemon's port.
        let root2 = tempfile::tempdir().unwrap();
        let (dir_a, dir_b) = (root2.path().join("a"), root2.path().join("b"));
        let art_b = dir_b.join("android-test").join("netsimd");
        std::fs::create_dir_all(&dir_a).unwrap();
        std::fs::create_dir_all(&art_b).unwrap();
        std::fs::write(
            art_b.join("netsim_2_stderr.log"),
            "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone60)\n",
        )
        .unwrap();
        write_ini(
            &dir_b.join("netsim_2.ini"),
            &format!("pid={DAEMON_B}\ngrpc.port=43929\n"),
            START + 2,
        );

        let mut tracker2 = tracker_for(&[&dir_a, &dir_b], root2.path());
        tracker2.enable_log = true;
        let daemon_a = netsimd(DAEMON_A, START, &dir_a);
        let mut daemon_b = netsimd(DAEMON_B, START + 2, &dir_b);
        daemon_b.cmd =
            vec!["/sdk/emulator/netsimd".to_string(), "--instance".to_string(), "2".to_string()];

        let phone60 = emu_group("emulator-5560", WORKER_A, "Phone60", Some("Phone60"));
        let mut conflict_emu = emu_group("emulator-5562", WORKER_B, "Conflict", Some("Conflict"));
        conflict_emu.grpc_endpoint = Some("localhost:49999".to_string());

        emu_tick_with(
            &mut tracker2,
            &[daemon_a],
            &[("Phone60_emulator-5560", &phone60), ("Conflict_emulator-5562", &conflict_emu)],
            1.0,
        );
        assert_eq!(
            tracker2.emulator_records["Phone60_emulator-5560"].daemon_key,
            Some((DAEMON_A, START))
        );
        assert_eq!(tracker2.emulator_records["Phone60_emulator-5560"].state, "CONNECTING");

        emu_tick_with(
            &mut tracker2,
            &[daemon_b],
            &[("Phone60_emulator-5560", &phone60), ("Conflict_emulator-5562", &conflict_emu)],
            3.0,
        );
        assert_eq!(
            tracker2.emulator_records["Phone60_emulator-5560"].daemon_key,
            Some((DAEMON_B, START + 2))
        );
        assert_eq!(tracker2.emulator_records["Phone60_emulator-5560"].state, "CONNECTED");
        assert_ne!(
            tracker2.emulator_records["Conflict_emulator-5562"].daemon_key,
            Some((DAEMON_B, START + 2))
        );
        assert_eq!(tracker2.emulator_records["Conflict_emulator-5562"].state, "CONNECTING");

        // 3. A joined QEMU-next emulator whose VM worker exits before connecting while
        // its launcher lingers in `emu_groups` settles immediately to
        // `FAILED(worker_exit_no_connect)` and drops `netsim_devices` to `0`.
        let dir3 = tempfile::tempdir().unwrap();
        let (mut tracker3, procs3) = tracker_with_daemon_log(dir3.path(), "");
        let launcher_pid = WORKER_A - 1;
        let mut qnext = emu_group("emulator-5554", WORKER_A, "Wear", Some("Wear"));
        qnext.primary_start_time = START + 2;
        qnext.pids = vec![launcher_pid, WORKER_A];
        qnext.member_start_times =
            BTreeMap::from([(launcher_pid, START + 1), (WORKER_A, START + 2)]);

        emu_tick_with(&mut tracker3, &procs3, &[("Wear_emulator-5554", &qnext)], 2.0);
        assert_eq!(tracker3.emulator_records["Wear_emulator-5554"].state, "CONNECTING");
        assert_eq!(tracker3.get_csv_fields()["netsim_devices"], "1");

        let mut lingering = qnext.clone();
        lingering.primary_pid = launcher_pid;
        lingering.primary_start_time = START + 1;
        lingering.pids = vec![launcher_pid];
        lingering.member_start_times = BTreeMap::from([(launcher_pid, START + 1)]);

        emu_tick_with(&mut tracker3, &procs3, &[("Wear_emulator-5554", &lingering)], 4.0);
        assert_eq!(
            tracker3.emulator_records["Wear_emulator-5554"].state,
            "FAILED(worker_exit_no_connect)"
        );
        assert_eq!(tracker3.get_csv_fields()["netsim_devices"], "0");
    }

    #[cfg(unix)]
    #[test]
    fn test_post_reg_symlink_and_preexisting_foreign_artifact_dir_rejected() {
        let root = tempfile::tempdir().unwrap();
        let secret_dir = root.path().join("secret");
        std::fs::create_dir_all(&secret_dir).unwrap();
        std::fs::write(
            secret_dir.join("netsim_stderr.log"),
            "netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 44444\n",
        )
        .unwrap();
        write_ini(
            &secret_dir.join("netsim_session_stats.json"),
            r#"{"version":"9.9.9-secret","device_stats":[{"device_id":1}],"radio_stats":[{"device_id":1,"kind":"WIFI","duration_secs":"2"}]}"#,
            START + 2,
        );

        let sym_art = root.path().join("sym_art");
        std::os::unix::fs::symlink(&secret_dir, &sym_art).unwrap();
        let mut direct_tailer = NetsimLogTailer::new(sym_art.join("netsim_stderr.log"));
        assert!(
            !direct_tailer.poll(START as f64 + 5.0),
            "tailer must reject symlinked parent directory"
        );
        assert_eq!(direct_tailer.grpc_port_logged, None);

        // Daemon registered before its artifact_dir exists must clear artifact_dir
        // if that path is subsequently created as a symlink.
        let tmp1 = tempfile::tempdir().unwrap();
        let mut tracker1 = tracker_for(&[tmp1.path()], tmp1.path());
        tracker1.enable_log = true;
        let proc1 = netsimd(DAEMON_A, START, tmp1.path());
        let phone1 = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        emu_tick_with(&mut tracker1, std::slice::from_ref(&proc1), &[("phone_5554", &phone1)], 1.0);
        std::fs::create_dir_all(tmp1.path().join("android-test")).unwrap();
        std::os::unix::fs::symlink(&secret_dir, tmp1.path().join("android-test").join("netsimd"))
            .unwrap();
        emu_tick_with(&mut tracker1, std::slice::from_ref(&proc1), &[("phone_5554", &phone1)], 3.0);
        let d1 = &tracker1.daemons[&(DAEMON_A, START)];
        assert!(d1.artifact_dir.as_os_str().is_empty());
        assert_eq!(d1.grpc_port, None);
        assert_eq!(d1.version, None);
        assert!(tracker1.emulator_records["phone_5554"].chip_attach_s.is_empty());

        // Pre-existing foreign daemon seen before the monitor user's artifact_dir exists
        // must drop that artifact_dir once the monitor user creates it, preserving own
        // daemon exit harvest and emulator disconnect isolation.
        let tmp2 = tempfile::tempdir().unwrap();
        let out2 = tempfile::tempdir().unwrap();
        let mut tracker2 = NetsimTracker::new(Some(""), 256, true, out2.path());
        tracker2.host_os = HostOs::Linux;
        tracker2.custom_ini_dirs = vec![tmp2.path().to_path_buf()];
        tracker2.custom_proc_locks = Some(String::new());

        let mut foreign_pre = netsimd(DAEMON_A - 10, START - 5, tmp2.path());
        foreign_pre.same_user = false;
        emu_tick_with(&mut tracker2, std::slice::from_ref(&foreign_pre), &[], 0.0);

        let own_art = tmp2.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&own_art).unwrap();
        let ini_own = tmp2.path().join("netsim.ini");
        write_ini(&ini_own, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        std::fs::write(
            own_art.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.050 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.051 netsimd.rs:750 - gRPC port: 43929\n\
                 netsim I 09-25 22:37:05.300 device.rs:110 - DeviceActor: AddChip 10 (kind WIFI, name \"wlan0\") to device 1 (Phone)\n",
                ini_own.display()
            ),
        )
        .unwrap();
        let own_proc = netsimd(DAEMON_A, START, tmp2.path());
        let mut phone2 = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        phone2.primary_start_time = START;
        phone2.grpc_endpoint = Some("localhost:43929".to_string());

        emu_tick_with(
            &mut tracker2,
            &[foreign_pre.clone(), own_proc],
            &[("phone_emulator-5554", &phone2)],
            2.0,
        );
        std::fs::write(own_art.join("netsim_artifacts_own.zip"), b"PK\x03\x04ownzip").unwrap();
        write_ini(&own_art.join("netsim_session_stats.json"), r#"{"version":"1.0.31"}"#, START + 3);
        let _ = std::fs::remove_file(&ini_own);
        emu_tick_with(
            &mut tracker2,
            std::slice::from_ref(&foreign_pre),
            &[("phone_emulator-5554", &phone2)],
            5.0,
        );
        assert!(tracker2.daemons[&(DAEMON_A - 10, START - 5)].artifact_dir.as_os_str().is_empty());
        assert_eq!(tracker2.daemons[&(DAEMON_A - 10, START - 5)].grpc_port, None);
        assert!(!tracker2.timeline.iter().any(|(_, e)| e.contains("DUPLICATE_ARTIFACT_DIR")));
        assert_eq!(tracker2.pcap_mgr.rescued_zips.len(), 1);
        assert_eq!(tracker2.daemons[&(DAEMON_A, START)].version.as_deref(), Some("1.0.31"));
        assert_eq!(tracker2.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
    }

    #[test]
    fn test_single_daemon_fallback_rejects_port_conflict_and_clears_unattached_daemon_key() {
        // 1. Newly seen emulator (`rec.daemon_key == None`) and unchanged fallback ticks must
        // reject a single active daemon listening on a different gRPC port.
        let dir1 = tempfile::tempdir().unwrap();
        let art1 = dir1.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art1).unwrap();
        write_ini(
            &dir1.path().join("netsim.ini"),
            &format!("pid={DAEMON_A}\ngrpc.port=43929\n"),
            START,
        );
        std::fs::write(
            art1.join("netsim_stderr.log"),
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel 8)\n",
        )
        .unwrap();

        let mut tracker1 = tracker_for(&[dir1.path()], dir1.path());
        tracker1.enable_log = true;
        let proc1 = netsimd(DAEMON_A, START, dir1.path());
        let mut conflict = emu_group("emulator-5562", WORKER_B, "Conflict", Some("Conflict"));
        conflict.primary_start_time = START + 2;
        conflict.grpc_endpoint = Some("localhost:49999".to_string());

        for t in [2.0, 4.0] {
            emu_tick_with(
                &mut tracker1,
                std::slice::from_ref(&proc1),
                &[("Conflict_emulator-5562", &conflict)],
                t,
            );
            let rec = &tracker1.emulator_records["Conflict_emulator-5562"];
            assert_eq!(rec.daemon_key, None);
            assert_eq!(rec.state, "CONNECTING");
            assert!(rec.chip_attach_s.is_empty());
            assert_eq!(tracker1.get_csv_fields()["netsim_devices"], "0");
        }

        // 2. Provisional Tick-1 fallback (`grpc_port == None` initially) detaches cleanly once
        // DAEMON_A resolves a conflicting port on Tick 2, preserving `all_count == 1` Wi-Fi
        // attribution for DAEMON_A's real emulator and avoiding false `FAILED(launcher_abort)`.
        let dir2 = tempfile::tempdir().unwrap();
        let art2 = dir2.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art2).unwrap();
        let ini2 = dir2.path().join("netsim.ini");
        let log2 = art2.join("netsim_stderr.log");
        let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
        tracker2.enable_log = true;
        let proc2 = netsimd(DAEMON_A, START, dir2.path());

        let mut phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
        phone.primary_start_time = START;
        phone.grpc_endpoint = Some("localhost:43929".to_string());

        emu_tick_with(
            &mut tracker2,
            std::slice::from_ref(&proc2),
            &[("Phone_emulator-5554", &phone), ("Conflict_emulator-5562", &conflict)],
            1.0,
        );
        assert_eq!(
            tracker2.emulator_records["Conflict_emulator-5562"].daemon_key,
            Some((DAEMON_A, START))
        );

        write_ini(&ini2, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
        std::fs::write(
            &log2,
            format!(
                "netsim I 09-25 22:37:05.500 netsimd.rs:744 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n\
                 netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n\
                 netsim W 09-25 22:37:09.000 netsimd.rs:800 - Startup timeout reached\n",
                ini2.display()
            ),
        )
        .unwrap();

        emu_tick_with(
            &mut tracker2,
            std::slice::from_ref(&proc2),
            &[("Phone_emulator-5554", &phone), ("Conflict_emulator-5562", &conflict)],
            3.0,
        );
        assert_eq!(tracker2.emulator_records["Conflict_emulator-5562"].daemon_key, None);
        assert_eq!(tracker2.emulator_records["Conflict_emulator-5562"].netsim_daemon_ready_s, None);
        assert_eq!(tracker2.emulator_records["Phone_emulator-5554"].wifi_associated_s, Some(2.0));

        // 3. Provisional Tick-1 fallback on bystander DAEMON_A (`--instance 1`) also clears
        // `daemon_key` and `netsim_daemon_ready_s` when unrelated DAEMON_B (`--instance 2`,
        // before binding its gRPC port) spawns while DAEMON_A remains alive.
        let dir2b = tempfile::tempdir().unwrap();
        let art2b = dir2b.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art2b).unwrap();
        let ini2b = dir2b.path().join("netsim.ini");
        write_ini(&ini2b, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START);
        std::fs::write(
            art2b.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.200 netsimd.rs:744 - Wrote to INI file {}\n",
                ini2b.display()
            ),
        )
        .unwrap();
        let mut tracker2b = tracker_for(&[dir2b.path()], dir2b.path());
        tracker2b.enable_log = true;
        let proc_a2b = netsimd(DAEMON_A, START, dir2b.path());
        let mut pending60 = emu_group("emulator-5560", WORKER_B, "Pending60", Some("Pending60"));
        pending60.primary_start_time = START + 2;
        emu_tick_with(
            &mut tracker2b,
            std::slice::from_ref(&proc_a2b),
            &[("Pending60_emulator-5560", &pending60)],
            2.0,
        );
        assert_eq!(
            tracker2b.emulator_records["Pending60_emulator-5560"].daemon_key,
            Some((DAEMON_A, START))
        );
        let mut proc_b2b = netsimd(DAEMON_B, START + 3, dir2b.path());
        proc_b2b.cmd = vec!["netsimd".to_string(), "--instance".to_string(), "2".to_string()];
        emu_tick_with(
            &mut tracker2b,
            &[proc_a2b, proc_b2b],
            &[("Pending60_emulator-5560", &pending60)],
            3.5,
        );
        assert_eq!(tracker2b.emulator_records["Pending60_emulator-5560"].daemon_key, None);
        assert_eq!(
            tracker2b.emulator_records["Pending60_emulator-5560"].netsim_daemon_ready_s,
            None
        );

        // 4. Port-compatible unattached emulator whose single daemon exits with `startup_timeout`
        // still settles to `FAILED(launcher_abort)`.
        let dir3 = tempfile::tempdir().unwrap();
        let (mut tracker3, procs3) = tracker_with_daemon_log(
            dir3.path(),
            "netsim W 09-25 22:37:09.000 netsimd.rs:800 - Startup timeout reached\n",
        );
        let mut compat = emu_group("emulator-5554", WORKER_A, "Compat", Some("Compat"));
        compat.primary_start_time = START;
        emu_tick_with(&mut tracker3, &procs3, &[("Compat_emulator-5554", &compat)], 1.0);
        emu_tick_with(&mut tracker3, &[], &[("Compat_emulator-5554", &compat)], 5.0);
        assert_eq!(
            tracker3.emulator_records["Compat_emulator-5554"].state,
            "FAILED(launcher_abort)"
        );
    }

    #[test]
    fn test_zero_offset_shared_log_tailer_skips_replacement_server_and_polls_client_exit() {
        // 1. Lingering DAEMON_A (`offset == 0` after 0-byte log on Tick 1) must not read
        // replacement server DAEMON_B's in-place truncated log or hijack endpoint matching.
        let dir1 = tempfile::tempdir().unwrap();
        let art1 = dir1.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art1).unwrap();
        let log1 = art1.join("netsim_stderr.log");
        std::fs::write(&log1, b"").unwrap();

        let mut tracker1 = tracker_for(&[dir1.path()], dir1.path());
        tracker1.enable_log = true;
        let proc_a1 = netsimd(DAEMON_A, START, dir1.path());
        emu_tick_with(&mut tracker1, std::slice::from_ref(&proc_a1), &[], 1.0);
        assert_eq!(tracker1.daemons[&(DAEMON_A, START)].grpc_port, None);

        let ini1 = dir1.path().join("netsim.ini");
        write_ini(&ini1, &format!("pid={DAEMON_B}\ngrpc.port=43930\n"), START + 2);
        std::fs::write(
            &log1,
            format!(
                "netsim I 09-25 22:37:07.100 netsimd.rs:451 - Netsim Version: 1.0.31, OS: linux\n\
                 netsim I 09-25 22:37:07.200 netsimd.rs:750 - gRPC port: 43930\n\
                 netsim I 09-25 22:37:07.300 netsimd.rs:744 - Wrote to INI file {}\n",
                ini1.display()
            ),
        )
        .unwrap();
        let proc_b1 = netsimd(DAEMON_B, START + 2, dir1.path());
        let mut phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
        phone.primary_start_time = START + 2;
        phone.grpc_endpoint = Some("localhost:43930".to_string());

        emu_tick_with(&mut tracker1, &[proc_a1, proc_b1], &[("Phone_emulator-5554", &phone)], 3.0);
        assert_eq!(tracker1.daemons[&(DAEMON_A, START)].grpc_port, None);
        assert_eq!(tracker1.daemons[&(DAEMON_A, START)].version, None);
        assert!(!tracker1.daemons[&(DAEMON_A, START)].ready_recorded);
        assert_eq!(
            tracker1.emulator_records["Phone_emulator-5554"].daemon_key,
            Some((DAEMON_B, START + 2))
        );

        // 2. Exited DAEMON_A (`offset == 0`, `inode.is_some()` on Tick 1) must not read
        // replacement server DAEMON_B's in-place truncated log inside `mark_exited_daemons`.
        let dir2 = tempfile::tempdir().unwrap();
        let art2 = dir2.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art2).unwrap();
        let log2 = art2.join("netsim_stderr.log");
        std::fs::write(&log2, b"").unwrap();

        let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
        tracker2.enable_log = true;
        let proc_a2 = netsimd(DAEMON_A, START, dir2.path());
        emu_tick_with(&mut tracker2, std::slice::from_ref(&proc_a2), &[], 1.0);

        let ini2 = dir2.path().join("netsim.ini");
        write_ini(&ini2, &format!("pid={DAEMON_B}\ngrpc.port=43930\n"), START + 2);
        std::fs::write(
            &log2,
            format!(
                "netsim I 09-25 22:37:07.100 netsimd.rs:451 - Netsim Version: 1.0.31, OS: linux\n\
                 netsim I 09-25 22:37:07.200 netsimd.rs:750 - gRPC port: 43930\n\
                 netsim I 09-25 22:37:07.300 netsimd.rs:744 - Wrote to INI file {}\n",
                ini2.display()
            ),
        )
        .unwrap();
        let proc_b2 = netsimd(DAEMON_B, START + 2, dir2.path());
        emu_tick_with(&mut tracker2, &[proc_b2], &[], 3.0);

        let exited_a = &tracker2.daemons[&(DAEMON_A, START)];
        assert_eq!(exited_a.grpc_port, None);
        assert_eq!(exited_a.version, None);
        assert!(!exited_a.ready_recorded);

        // 3. Conversely, a newer client-mode sharer (`offset == 0`, `inode == None` as on
        // Windows) neither blocks primary server DAEMON_A while active nor skips its own
        // final `CLIENT_MODE_SPAWN` poll upon exiting.
        let dir3 = tempfile::tempdir().unwrap();
        let art3 = dir3.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art3).unwrap();
        let log3 = art3.join("netsim_stderr.log");
        std::fs::write(&log3, b"").unwrap();

        let mut tracker3 = tracker_for(&[dir3.path()], dir3.path());
        tracker3.enable_log = true;
        let proc_a3 = netsimd(DAEMON_A, START, dir3.path());
        let daemon_c = DAEMON_B + 50;
        let proc_c3 = netsimd(daemon_c, START + 2, dir3.path());
        emu_tick_with(&mut tracker3, &[proc_a3.clone(), proc_c3], &[], 2.5);
        tracker3.daemons.get_mut(&(daemon_c, START + 2)).unwrap().tailer.as_mut().unwrap().inode =
            None;

        std::fs::write(
            &log3,
            "netsim I 09-25 22:37:05.500 netsimd.rs:750 - gRPC port: 43929\n\
             netsim W 09-25 22:37:07.500 netsimd.rs:500 - Lock held by another process\n",
        )
        .unwrap();
        emu_tick_with(&mut tracker3, &[proc_a3], &[], 4.0);
        assert_eq!(tracker3.daemons[&(DAEMON_A, START)].grpc_port, Some(43929));
        assert!(
            tracker3
                .timeline
                .iter()
                .any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={daemon_c})")),
            "timeline={:?}",
            tracker3.timeline
        );
    }

    #[test]
    fn test_au11_netsim_concurrent_client_mode_spawn_and_active_client_isolation() {
        // 1. Concurrent (<= 1s) client-mode spawn on shared log: DAEMON_A (server) and DAEMON_B
        // (client-mode logging "Lock held by another process") start within 1s. DAEMON_B must
        // record CLIENT_MODE_SPAWN without stealing DAEMON_A's version/port/READY or starving
        // DAEMON_A's 0-offset tailer.
        let dir = tempfile::tempdir().unwrap();
        let art = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art).unwrap();
        let ini_path = dir.path().join("netsim.ini");
        std::fs::write(&ini_path, format!("grpc.port=43929\npid={DAEMON_A}\n")).unwrap();
        let log_path = art.join("netsim_stderr.log");
        std::fs::write(
            &log_path,
            format!(
                "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
                 netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n\
                 netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.400 device.rs:120 - DeviceActor: AddChip 0 (kind WIFI) to device 1 (Pixel 8)\n\
                 netsim W 09-25 22:37:05.800 netsimd.rs:500 - Lock held by another process\n",
                ini_path.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let proc_srv = netsimd(DAEMON_A, START, dir.path());
        let proc_cli = netsimd(DAEMON_B, START + 1, dir.path());
        let mut phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Pixel 8"));
        phone.primary_start_time = START;
        phone.grpc_endpoint = None; // QEMU-now / single-daemon fallback

        // Active client-mode process alongside server: Phone must join DAEMON_A via fallback
        // and transition to CONNECTED without DAEMON_B breaking single-daemon fallback.
        emu_tick_with(
            &mut tracker,
            &[proc_srv.clone(), proc_cli.clone()],
            &[("Phone_emulator-5554", &phone)],
            2.0,
        );
        let d_srv = &tracker.daemons[&(DAEMON_A, START)];
        let d_cli = &tracker.daemons[&(DAEMON_B, START + 1)];
        assert!(d_cli.client_mode_recorded, "d_cli={d_cli:?}");
        assert_eq!(d_cli.grpc_port, None);
        assert_eq!(d_cli.ready_ts, None);
        assert!(!d_srv.client_mode_recorded);
        assert!(d_srv.log_available);
        assert_eq!(d_srv.version.as_deref(), Some("1.0.31"));
        assert!(d_srv.ready_ts.is_some());
        assert!(
            tracker
                .timeline
                .iter()
                .any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={DAEMON_B})"))
        );
        assert!(
            !tracker.timeline.iter().any(|(_, e)| e.starts_with(&format!("READY(pid={DAEMON_B},")))
        );
        let rec = &tracker.emulator_records["Phone_emulator-5554"];
        assert_eq!(rec.state, "CONNECTED");
        assert_eq!(rec.daemon_key, Some((DAEMON_A, START)));
        assert_eq!(rec.disconnect_count, 0);

        // Even after 35s with DAEMON_B lingering in client-mode, DAEMON_B is never marked ORPHANED,
        // and Phone remains CONNECTED with 0 disconnects.
        emu_tick_with(
            &mut tracker,
            &[proc_srv.clone(), proc_cli.clone()],
            &[("Phone_emulator-5554", &phone)],
            38.0,
        );
        assert!(!tracker.daemons[&(DAEMON_B, START + 1)].orphaned);
        assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "CONNECTED");
        assert_eq!(tracker.emulator_records["Phone_emulator-5554"].disconnect_count, 0);

        // 2. Pid-less INI fallback must pair with the server (DAEMON_A), never with an active
        // client_mode_recorded daemon (DAEMON_B) spawned within INI_MTIME_SLACK_S.
        let dir2 = tempfile::tempdir().unwrap();
        let art2 = dir2.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art2).unwrap();
        write_ini(&dir2.path().join("netsim.ini"), "grpc.port=43931\n", START + 1);
        std::fs::write(
            art2.join("netsim_stderr.log"),
            "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
             netsim W 09-25 22:37:06.100 netsimd.rs:500 - Lock held by another process\n",
        )
        .unwrap();
        let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
        tracker2.enable_log = true;
        emu_tick_with(
            &mut tracker2,
            &[netsimd(DAEMON_A, START, dir2.path()), netsimd(DAEMON_B, START + 1, dir2.path())],
            &[],
            2.0,
        );
        assert_eq!(tracker2.daemons[&(DAEMON_A, START)].grpc_port, Some(43931));
        assert_eq!(tracker2.daemons[&(DAEMON_B, START + 1)].grpc_port, None);

        // 3. Exited sub-second client-mode sharer (offset == 0 on Tick 1, flushed right before
        // exiting on Tick 2) records CLIENT_MODE_SPAWN without stealing DAEMON_A's port/READY.
        let dir3 = tempfile::tempdir().unwrap();
        let art3 = dir3.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art3).unwrap();
        let ini3 = dir3.path().join("netsim.ini");
        std::fs::write(&ini3, format!("grpc.port=43932\npid={DAEMON_A}\n")).unwrap();
        let log3 = art3.join("netsim_stderr.log");
        std::fs::write(
            &log3,
            format!(
                "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
                 netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43932\n\
                 netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n",
                ini3.display()
            ),
        )
        .unwrap();
        let mut tracker3 = tracker_for(&[dir3.path()], dir3.path());
        tracker3.enable_log = true;
        let proc_a3 = netsimd(DAEMON_A, START, dir3.path());
        let daemon_c = DAEMON_B + 50;
        let proc_c3 = netsimd(daemon_c, START + 1, dir3.path());
        emu_tick_with(&mut tracker3, &[proc_a3.clone(), proc_c3], &[], 1.5);
        assert_eq!(tracker3.daemons[&(DAEMON_A, START)].grpc_port, Some(43932));
        assert_eq!(tracker3.daemons[&(daemon_c, START + 1)].grpc_port, None);
        {
            use std::io::Write;
            let mut f = std::fs::OpenOptions::new().append(true).open(&log3).unwrap();
            writeln!(
                f,
                "netsim W 09-25 22:37:06.100 netsimd.rs:500 - Lock held by another process"
            )
            .unwrap();
        }
        emu_tick_with(&mut tracker3, &[proc_a3], &[], 2.5);
        let dc = &tracker3.daemons[&(daemon_c, START + 1)];
        assert!(dc.client_mode_recorded);
        assert_eq!(dc.grpc_port, None);
        assert_eq!(dc.ready_ts, None);
        assert!(
            tracker3
                .timeline
                .iter()
                .any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={daemon_c})"))
        );

        // 4. Concurrent client-mode daemon must not block primary server pcap exit harvest,
        // double-harvest in cleanup(), or report as netsim_up/netsim_pid in CSV.
        let dir4 = tempfile::tempdir().unwrap();
        let art4 = dir4.path().join("android-test").join("netsimd");
        let pcap4 = art4.join("pcaps");
        std::fs::create_dir_all(&pcap4).unwrap();
        let ini4 = dir4.path().join("netsim.ini");
        std::fs::write(&ini4, format!("grpc.port=43940\npid={DAEMON_B}\n")).unwrap();
        std::fs::write(
            art4.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
                 netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43940\n\
                 netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.400 device.rs:120 - DeviceActor: AddChip 0 (kind WIFI) to device 1 (Pixel 8)\n\
                 netsim W 09-25 22:37:05.600 netsimd.rs:500 - Lock held by another process\n",
                ini4.display()
            ),
        )
        .unwrap();
        std::fs::write(pcap4.join("netsim-0-Pixel_8-WIFI.pcap"), b"pcap-bytes").unwrap();
        let pcap_out4 = dir4.path().join("out_pcaps");
        let mut tracker4 =
            NetsimTracker::new(Some(pcap_out4.to_str().unwrap()), 50, true, dir4.path());
        tracker4.host_os = HostOs::Linux;
        tracker4.custom_ini_dirs = vec![dir4.path().to_path_buf()];
        tracker4.pcap_mgr.cli_runner = std::sync::Arc::new(|_, _| None);
        let proc_cli4 = netsimd(DAEMON_A, START + 1, dir4.path());
        let proc_srv4 = netsimd(DAEMON_B, START, dir4.path());
        let mut phone4 = emu_group("emulator-5554", WORKER_A, "Phone", Some("Pixel 8"));
        phone4.primary_start_time = START;
        phone4.grpc_endpoint = Some("localhost:43940".to_string());
        emu_tick_with(
            &mut tracker4,
            &[proc_cli4.clone(), proc_srv4],
            &[("Phone_emulator-5554", &phone4)],
            2.0,
        );
        let csv1 = tracker4.get_csv_fields();
        assert_eq!(csv1.get("netsim_up").map(String::as_str), Some("1"));
        assert_eq!(csv1.get("netsim_pid").map(String::as_str), Some(&DAEMON_B.to_string() as &str));

        let _ = std::fs::remove_file(&ini4);
        emu_tick_with(&mut tracker4, &[proc_cli4], &[], 4.0);
        let csv2 = tracker4.get_csv_fields();
        assert_eq!(csv2.get("netsim_up").map(String::as_str), Some("0"));
        assert_eq!(csv2.get("netsim_pid").map(String::as_str), Some(""));
        tracker4.cleanup();
        assert_eq!(tracker4.pcap_mgr.copied_files.len(), 1);
        assert!(
            tracker4.pcap_mgr.copied_files.keys().next().unwrap().contains(&format!("{DAEMON_B}-"))
        );
    }

    #[test]
    fn test_match_inis_to_daemons_enforces_uid_compatibility_across_all_unlocked_passes() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let proc_self = netsimd(DAEMON_A, START, dir.path());
        let mut proc_foreign = netsimd(DAEMON_B, START, dir.path());
        proc_foreign.same_user = false;
        emu_tick_with(&mut tracker, &[proc_self, proc_foreign], &[], 1.0);

        let ini_spoof_pid = dir.path().join("spoof_netsim.ini");
        let ini_own = dir.path().join("own_netsim.ini");
        std::fs::write(&ini_spoof_pid, "grpc.port=49999\n").unwrap();
        std::fs::write(&ini_own, "grpc.port=43929\n").unwrap();

        // `pid=` match: a foreign-owned unlocked INI claiming `pid=DAEMON_A` (`same_user=true`)
        // must be rejected so the legitimate `owned_by_self=Some(true)` INI still claims `DAEMON_A`.
        let obs_spoof = IniObservation {
            path: ini_spoof_pid.clone(),
            data: HashMap::new(),
            holders: Vec::new(),
            pid: Some(DAEMON_A),
            port: Some(49999),
            instance: Some(1),
            mtime: Some(START as f64 + 1.0),
            file_uid: None,
            owned_by_self: Some(false),
        };
        let obs_own = IniObservation {
            path: ini_own.clone(),
            data: HashMap::new(),
            holders: Vec::new(),
            pid: Some(DAEMON_A),
            port: Some(43929),
            instance: Some(1),
            mtime: Some(START as f64 + 1.0),
            file_uid: None,
            owned_by_self: Some(true),
        };
        let live = HashSet::from([(DAEMON_A, START), (DAEMON_B, START)]);
        assert_eq!(
            tracker.match_inis_to_daemons_with(&[obs_spoof, obs_own], &live, Some(1000)),
            vec![None, Some((DAEMON_A, START))]
        );

        // INI the daemon already owns (`d.ini_path`): if a daemon previously logged
        // `/tmp/netsim.ini`, a foreign-owned file planted at that path must be rejected when
        // `monitor_uid != Some(0)`, while root (`0`) remains allowed when `file_uid` is unknown
        // (`None`), and enforces `file_uid == expected_uid` when both UIDs are known (`sudo`
        // `monitor_uid = 0`, `daemon_uid = 1000`, spoof `2000`).
        tracker.daemons.get_mut(&(DAEMON_A, START)).unwrap().ini_path = Some(ini_spoof_pid.clone());
        let obs_logged_foreign = IniObservation {
            path: ini_spoof_pid.clone(),
            data: HashMap::new(),
            holders: Vec::new(),
            pid: None,
            port: Some(49999),
            instance: Some(1),
            mtime: Some(START as f64 + 1.0),
            file_uid: None,
            owned_by_self: Some(false),
        };
        let live_self_only = HashSet::from([(DAEMON_A, START)]);
        assert_eq!(
            tracker.match_inis_to_daemons_with(
                std::slice::from_ref(&obs_logged_foreign),
                &live_self_only,
                Some(1000),
            ),
            vec![None]
        );
        assert_eq!(
            tracker.match_inis_to_daemons_with(
                std::slice::from_ref(&obs_logged_foreign),
                &live_self_only,
                Some(0),
            ),
            vec![Some((DAEMON_A, START))]
        );

        tracker.daemons.get_mut(&(DAEMON_B, START)).unwrap().expected_uid = Some(1000);
        let obs_sudo_spoof = IniObservation {
            path: ini_spoof_pid.clone(),
            data: HashMap::new(),
            holders: Vec::new(),
            pid: Some(DAEMON_B),
            port: Some(49999),
            instance: Some(1),
            mtime: Some(START as f64 + 1.0),
            file_uid: Some(2000),
            owned_by_self: Some(false),
        };
        let obs_sudo_dev = IniObservation {
            path: ini_own,
            data: HashMap::new(),
            holders: Vec::new(),
            pid: Some(DAEMON_B),
            port: Some(43929),
            instance: Some(1),
            mtime: Some(START as f64 + 1.0),
            file_uid: Some(1000),
            owned_by_self: Some(false),
        };
        let live_b = HashSet::from([(DAEMON_B, START)]);
        assert_eq!(
            tracker.match_inis_to_daemons_with(&[obs_sudo_spoof, obs_sudo_dev], &live_b, Some(0)),
            vec![None, Some((DAEMON_B, START))]
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_is_disallowed_daemon_art_dir_enforces_same_user_ownership_and_android_parent() {
        use std::os::unix::fs::PermissionsExt;

        let uid = super::owner_uid().expect("owner_uid must resolve on Unix");
        let foreign_uid = uid.wrapping_add(1).max(1);
        let tmp = tempfile::tempdir().unwrap();

        // Not-yet-created `android-$USER/netsimd` under owned base dir is ALLOWED (`false`)
        // so newly spawned daemons keep their tailer until `netsimd` creates the directory.
        let pending_art = tmp.path().join("android-test").join("netsimd");
        assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(
            &pending_art,
            true,
            Some(uid),
            None
        ));

        // Pre-created `android-test` parent owned by `uid` (simulating a `foreign_uid` monitor)
        // is DISALLOWED (`true`) even BEFORE `netsimd` subdir exists.
        let android_parent = tmp.path().join("android-test");
        std::fs::create_dir_all(&android_parent).unwrap();
        assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
            &pending_art,
            true,
            Some(foreign_uid),
            None,
        ));
        assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(
            &pending_art,
            true,
            Some(uid),
            None
        ));

        // Existing `netsimd` subdir owned by another user is DISALLOWED for `same_user = true`,
        // allowed for `Some(0)` when `daemon_uid` is `None` or matches (`Some(uid)`), and
        // DISALLOWED under `sudo` (`Some(0)`) when `daemon_uid` mismatches (`Some(foreign_uid)`).
        std::fs::create_dir_all(&pending_art).unwrap();
        assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
            &pending_art,
            true,
            Some(foreign_uid),
            None,
        ));
        assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(
            &pending_art,
            true,
            Some(0),
            None
        ));
        assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(
            &pending_art,
            false,
            Some(0),
            Some(uid),
        ));
        assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
            &pending_art,
            false,
            Some(0),
            Some(foreign_uid),
        ));
        assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(&pending_art, true, None, None));

        // Symlinked `android-test` parent or foreign symlink prefix component is DISALLOWED.
        let real_target = tmp.path().join("real_target");
        std::fs::create_dir_all(real_target.join("netsimd")).unwrap();
        let sym_parent = tmp.path().join("android-symlink");
        std::os::unix::fs::symlink(&real_target, &sym_parent).unwrap();
        assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
            &sym_parent.join("netsimd"),
            true,
            Some(uid),
            None,
        ));
        assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
            &sym_parent.join("netsimd"),
            true,
            Some(0),
            None,
        ));

        let custom_sym = tmp.path().join("custom_link");
        std::os::unix::fs::symlink(&real_target, &custom_sym).unwrap();
        assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
            &custom_sym.join("netsimd"),
            true,
            Some(foreign_uid),
            None,
        ));

        // A world-writable artifact dir or `android-*` parent is DISALLOWED even with the
        // sticky bit, whether or not the `netsimd` subdir exists yet.
        let set_mode = |p: &std::path::Path, mode: u32| {
            std::fs::set_permissions(p, std::fs::Permissions::from_mode(mode)).unwrap();
        };
        let disallowed = |p: &std::path::Path| {
            NetsimTracker::is_disallowed_daemon_art_dir_with(p, true, Some(uid), None)
        };
        let not_yet_created = android_parent.join("pending");
        for mode in [0o1777, 0o1757, 0o777] {
            set_mode(&android_parent, 0o755);
            set_mode(&pending_art, mode);
            assert!(disallowed(&pending_art), "artifact dir mode {mode:o}");
            set_mode(&pending_art, 0o755);
            set_mode(&android_parent, mode);
            assert!(disallowed(&pending_art), "parent mode {mode:o}");
            assert!(disallowed(&not_yet_created), "parent mode {mode:o}, dir not created");
        }
        set_mode(&android_parent, 0o755);
        assert!(!disallowed(&pending_art));
        assert!(!disallowed(&not_yet_created));
    }

    #[cfg(unix)]
    #[test]
    fn test_poll_daemon_log_ignores_symlinked_or_foreign_logged_ini() {
        use std::os::unix::fs::PermissionsExt;

        let uid = super::owner_uid().expect("owner_uid must resolve on Unix");
        let foreign_uid = uid.wrapping_add(1).max(1);
        let dir = tempfile::tempdir().unwrap();
        let art = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art).unwrap();

        // The real pid-less INI in `dir` is still matched to DAEMON_A (as the oldest INI
        // without holders or `pid=`) when the log names a symlinked INI: that path must not
        // become `d.ini_path`.
        let real_ini = dir.path().join("netsim.ini");
        write_ini(&real_ini, "grpc.port=43945\n", START + 1);
        let spoof_target = dir.path().join("spoof_target.conf");
        std::fs::write(&spoof_target, "grpc.port=49999\npid=999999\n").unwrap();
        let sym_logged_ini = dir.path().join("sym_logged.ini");
        std::os::unix::fs::symlink(&spoof_target, &sym_logged_ini).unwrap();

        std::fs::write(
            art.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
                 netsim I 09-25 22:37:05.200 netsimd.rs:760 - Wrote to INI file {}\n",
                sym_logged_ini.display()
            ),
        )
        .unwrap();

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.enable_log = true;
        let proc_a = netsimd(DAEMON_A, START, dir.path());
        tick(&mut tracker, std::slice::from_ref(&proc_a), START + 5);
        let d_a = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(
            d_a.ini_path.as_deref(),
            Some(real_ini.as_path()),
            "symlinked ini_path_logged must not become d.ini_path or block the pid-less INI match"
        );
        assert_eq!(d_a.grpc_port, Some(43945));

        // A foreign daemon (`same_user: false`) whose log names an INI owned by the monitor's
        // user must not adopt it as `d.ini_path`: the file does not belong to the daemon's
        // user (`expected_uid`).
        let dir2 = tempfile::tempdir().unwrap();
        // The control below judges every directory down to the INI.
        std::fs::set_permissions(dir2.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
        let art2 = dir2.path().join("android-foreign").join("netsimd");
        std::fs::create_dir_all(&art2).unwrap();
        let own_ini2 = dir2.path().join("own_netsim.ini");
        std::fs::write(&own_ini2, format!("grpc.port=49998\npid={DAEMON_B}\n")).unwrap();
        std::fs::write(
            art2.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.200 netsimd.rs:760 - Wrote to INI file {}\n",
                own_ini2.display()
            ),
        )
        .unwrap();
        let mut d_foreign = DaemonRecord {
            daemon_key: (DAEMON_B, START),
            pid: DAEMON_B,
            create_time: START,
            start_wall: START as f64,
            spawner: "STANDALONE".to_string(),
            instance_num: None,
            artifact_dir: art2.clone(),
            log_path: art2.join("netsim_stderr.log"),
            tailer: Some(NetsimLogTailer::new(art2.join("netsim_stderr.log"))),
            netsim_cli_bin: "netsim".to_string(),
            same_user: false,
            expected_uid: Some(foreign_uid),
            version: None,
            ini_path: None,
            grpc_port: None,
            ready_ts: None,
            ready_recorded: false,
            netsim_daemon_ready_s: None,
            frontend_disabled: false,
            pcap_flag: false,
            log_available: true,
            log_unavail_recorded: false,
            client_mode_recorded: false,
            exited: false,
            exit_reason_hint: None,
            exit_reason: None,
            orphaned: false,
            zero_devices_since_mono: None,
            stats_cache: None,
            pcap_live_bytes: 0,
            pcap_live_files: 0,
            active_socket_conns: 0,
            joined_emulator_count: 0,
        };
        let mut events = Vec::new();
        NetsimTracker::poll_daemon_log(
            &mut d_foreign,
            START as f64 + 5.0,
            false,
            (false, false),
            &HashSet::new(),
            &mut events,
        );
        // The line was read, so the missing `ini_path` is the ownership check's doing.
        assert_eq!(
            d_foreign.tailer.as_ref().unwrap().ini_path_logged.as_deref(),
            own_ini2.to_str()
        );
        assert_eq!(d_foreign.ini_path, None, "foreign daemon must not adopt own_ini2");

        // Control: the monitor's own daemon adopts the same logged path.
        let mut d_own = DaemonRecord { same_user: true, expected_uid: Some(uid), ..d_foreign };
        NetsimTracker::poll_daemon_log(
            &mut d_own,
            START as f64 + 6.0,
            false,
            (false, false),
            &HashSet::new(),
            &mut events,
        );
        assert_eq!(d_own.ini_path.as_deref(), Some(own_ini2.as_path()));
    }

    #[test]
    fn test_netsim_tracker_windows_host_os_discovers_localappdata_ini_and_temp_artifacts() {
        let dir = tempfile::tempdir().unwrap();
        let local_app = dir.path().join("LocalAppData");
        let win_disc = local_app.join("Temp");
        let win_temp = dir.path().join("WinTemp");
        let win_art = win_temp.join("netsimd");
        std::fs::create_dir_all(&win_disc).unwrap();
        std::fs::create_dir_all(&win_art).unwrap();

        let ini_path = win_disc.join("netsim.ini");
        write_ini(&ini_path, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
        std::fs::write(
            win_art.join("netsim_stderr.log"),
            format!(
                "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
                 netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n\
                 netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n\
                 netsim I 09-25 22:37:05.400 device.rs:120 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
                ini_path.display()
            ),
        )
        .unwrap();

        let proc_win = NetsimDaemonProc {
            pid: DAEMON_A,
            parent: Some(1),
            start_time: START,
            exe: Some(PathBuf::from(r"C:\Android\Sdk\emulator\netsimd.exe")),
            cmd: vec![r"C:\Android\Sdk\emulator\netsimd.exe".to_string()],
            environ: env_of(&[
                ("LOCALAPPDATA", local_app.to_str().unwrap()),
                ("TEMP", win_temp.to_str().unwrap()),
            ]),
            same_user: true,
            ..Default::default()
        };
        assert_eq!(
            get_netsim_discovery_dirs(&proc_win.environ, HostOs::Windows).first(),
            Some(&win_disc)
        );

        let mut tracker = NetsimTracker::new(None, 256, true, dir.path());
        tracker.host_os = HostOs::Windows;
        // Without custom dirs, discovery applies the Windows layout to the daemon's environment.
        assert!(
            tracker.discover_ini_files(std::slice::from_ref(&proc_win)).contains(&ini_path),
            "host_os must reach discover_ini_files"
        );
        tracker.custom_ini_dirs = vec![win_disc.clone()];
        tracker.custom_proc_locks = Some(String::new());
        tracker.socket_cli_runner = |cmd, _| {
            assert_eq!(cmd, "netstat");
            Some(
                "  TCP    127.0.0.1:51001        127.0.0.1:43929        ESTABLISHED     4001\r\n"
                    .to_string(),
            )
        };

        let mut phone = emu_group("emulator-5554", 4001, "Phone", Some("Phone"));
        phone.primary_start_time = START;
        phone.pids = vec![4001];
        phone.grpc_endpoint = Some("127.0.0.1:43929".to_string());

        emu_tick_with(&mut tracker, &[proc_win], &[("Phone_emulator-5554", &phone)], 2.0);
        let d = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d.artifact_dir, win_art);
        assert_eq!(d.netsim_cli_bin, "netsim.exe");
        assert_eq!(d.grpc_port, Some(43929));
        assert_eq!(d.version.as_deref(), Some("1.0.31"));
        assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "CONNECTED");
    }
}
