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
#[path = "../../tests/netsim/tracker_test.rs"]
mod tests;
