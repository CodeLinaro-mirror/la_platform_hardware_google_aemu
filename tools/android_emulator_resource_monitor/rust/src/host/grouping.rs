use std::ffi::OsString;

use super::EmulatorProcess;
#[cfg(any(target_os = "macos", target_os = "windows"))]
use super::HOST_CMD_TIMEOUT;
use super::cmdline::{
    is_descendant_of_self, is_emulator_process, is_netsimd_process, is_worker_process,
    parse_cmdline_details, parse_cmdline_netsim_endpoint,
};

const EMU_CONSOLE_PORT_RANGE: std::ops::RangeInclusive<u16> = 5554..=5584;
#[cfg(target_os = "linux")]
const TCP_LISTEN_STATE_HEX: &str = "0A";
const HIERARCHY_PROPAGATION_PASSES: usize = 2;

fn is_emulator_console_port(port: u16) -> bool {
    EMU_CONSOLE_PORT_RANGE.contains(&port) && port % 2 == 0
}

#[cfg(any(target_os = "macos", target_os = "windows", test))]
fn parse_listening_console_port(addr: &str) -> Option<u16> {
    let port = addr.rsplit(':').next()?.parse::<u16>().ok()?;
    is_emulator_console_port(port).then_some(port)
}

#[cfg(target_os = "linux")]
fn get_socket_inodes(pid: u32) -> Vec<u64> {
    let mut inodes = Vec::new();
    if let Ok(entries) = std::fs::read_dir(format!("/proc/{}/fd", pid)) {
        for entry in entries.flatten() {
            if let Ok(link) = std::fs::read_link(entry.path()) {
                let link_str = link.to_string_lossy();
                if let Some(inode) = link_str
                    .strip_prefix("socket:[")
                    .and_then(|s| s.strip_suffix(']'))
                    .and_then(|s| s.parse::<u64>().ok())
                {
                    inodes.push(inode);
                }
            }
        }
    }
    inodes
}

#[cfg(target_os = "linux")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let inodes = get_socket_inodes(pid);
    if inodes.is_empty() {
        return None;
    }
    for tcp_file in ["/proc/net/tcp", "/proc/net/tcp6"] {
        if let Ok(tcp) = std::fs::read_to_string(tcp_file) {
            for line in tcp.lines().skip(1) {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 10 && parts[3] == TCP_LISTEN_STATE_HEX {
                    let inode = parts[9].parse::<u64>().unwrap_or(0);
                    if inodes.contains(&inode) {
                        let local = parts[1];
                        if let Some(port_hex) = local.split(':').nth(1) {
                            if let Ok(port) = u16::from_str_radix(port_hex, 16) {
                                if is_emulator_console_port(port) {
                                    return Some(port);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

#[cfg(any(target_os = "macos", test))]
fn parse_macos_lsof_listening_port(stdout: &str, pid: u32) -> Option<u16> {
    let mut current_pid: Option<u32> = None;
    for line in stdout.lines().map(str::trim) {
        if let Some(rest) = line.strip_prefix('p') {
            current_pid = rest.parse::<u32>().ok();
        } else if current_pid == Some(pid) {
            if let Some(ep) = line.strip_prefix('n') {
                if !ep.contains("->") {
                    if let Some(port) = parse_listening_console_port(ep) {
                        return Some(port);
                    }
                }
            }
        }
    }
    None
}

#[cfg(target_os = "macos")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let pid_str = pid.to_string();
    let output = crate::netsim::run_with_timeout(
        "lsof",
        &["-n", "-P", "-F", "pn", "-a", "-iTCP", "-sTCP:LISTEN", "-p", &pid_str],
        HOST_CMD_TIMEOUT,
    )?;

    parse_macos_lsof_listening_port(&String::from_utf8_lossy(&output.stdout), pid)
}

#[cfg(any(target_os = "windows", test))]
fn parse_windows_netstat_listening_port(stdout: &str, pid: u32) -> Option<u16> {
    for line in stdout.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 5 && parts[3] == "LISTENING" && parts[4].parse::<u32>().ok() == Some(pid)
        {
            if let Some(port) = parse_listening_console_port(parts[1]) {
                return Some(port);
            }
        }
    }
    None
}

#[cfg(target_os = "windows")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let output = crate::netsim::run_with_timeout("netstat", &["-ano"], HOST_CMD_TIMEOUT)?;
    parse_windows_netstat_listening_port(&String::from_utf8_lossy(&output.stdout), pid)
}

/// One process as sampled by the host provider.
#[derive(Clone, Debug, Default)]
pub struct ProcSample {
    pub pid: u32,
    pub parent: Option<u32>,
    pub name: String,
    pub exe: Option<std::path::PathBuf>,
    pub cmd: Vec<OsString>,
    pub start_time: u64,
    pub cpu: f32,
    pub ram_mb: f64,
    /// Real uid on unix; `None` on Windows or when sysinfo has no owner.
    pub uid: Option<u32>,
}

const MAX_UNMAPPED_PORT_PROBES: u8 = 30;
const UNMAPPED_PORT_PROBE_WINDOW: std::time::Duration = std::time::Duration::from_secs(60);

#[derive(Clone, Debug)]
pub struct PortCacheEntry {
    pub port: Option<u16>,
    pub avd_name: Option<String>,
    pub explicit_avd: Option<String>,
    pub first_probe_at: std::time::Instant,
    pub unmapped_probes: u8,
}

impl PortCacheEntry {
    fn is_settled_unmapped(&self, now: std::time::Instant) -> bool {
        self.port.is_none()
            && (self.unmapped_probes >= MAX_UNMAPPED_PORT_PROBES
                || now.duration_since(self.first_probe_at) >= UNMAPPED_PORT_PROBE_WINDOW)
    }
}

pub type PortCache = std::collections::HashMap<(u32, u64), PortCacheEntry>;

/// Per-tick lookups used while grouping processes; injected so tests can use fakes.
pub struct ProcessLookups<'a> {
    pub self_pid: u32,
    /// The monitor's uid (`None` on Windows or when sysinfo has no owner).
    pub self_uid: Option<u32>,
    pub gpu_by_pid: &'a std::collections::HashMap<u32, f32>,
    pub parent_of: &'a dyn Fn(u32) -> Option<u32>,
    pub listening_port: &'a dyn Fn(u32) -> Option<u16>,
    pub proc_status: &'a dyn Fn(u32) -> (u64, u32),
    /// Environment of a process (`/proc` on Linux, sysinfo elsewhere).
    pub environ_of: &'a dyn Fn(u32) -> std::collections::HashMap<String, String>,
    /// Name of any live process, sampled or not.
    pub name_of: &'a dyn Fn(u32) -> Option<String>,
}

/// Real uid of a sysinfo process on unix; Windows SIDs are not compared.
#[cfg(unix)]
pub fn process_uid(p: &sysinfo::Process) -> Option<u32> {
    p.user_id().map(|u| **u)
}

#[cfg(not(unix))]
pub fn process_uid(_p: &sysinfo::Process) -> Option<u32> {
    None
}

/// Whether a process runs as the monitor's user.
///
/// Windows has no uids; there the temp dir a daemon reports is already per
/// logon session, so every daemon counts as the monitor's own. On unix an
/// unknown owner is treated as foreign.
pub fn is_same_user(proc_uid: Option<u32>, self_uid: Option<u32>) -> bool {
    if cfg!(windows) {
        return true;
    }
    match (proc_uid, self_uid) {
        (Some(p), Some(s)) => p == s,
        _ => false,
    }
}

pub struct ProcessAggregate {
    pub emulators: Vec<EmulatorProcess>,
    pub netsim_cpu: f32,
    pub netsim_ram_mb: f64,
    pub netsim_count: usize,
    pub netsim_daemons: Vec<crate::netsim::NetsimDaemonProc>,
    pub emu_groups: std::collections::HashMap<String, crate::netsim::EmuGroupNetsimInfo>,
}

/// Parses `(real_uid, effective_uid)` from `/proc/<pid>/status`
/// (`Uid:\t<real>\t<effective>\t<saved_set>\t<fs>`).
///
/// Unlike `stat("/proc/self")`, which Linux resets to `GLOBAL_ROOT_UID` (`0`) under
/// `PR_SET_DUMPABLE=0` and sets to `euid` otherwise, `/proc/self/status` always reports
/// the process's true real and effective UIDs.
#[cfg(any(unix, test))]
pub fn parse_proc_status_uids(status: &str) -> Option<(u32, u32)> {
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("Uid:") {
            let mut fields = rest.split_whitespace();
            let real_uid = fields.next()?.parse::<u32>().ok()?;
            let effective_uid = fields.next()?.parse::<u32>().ok()?;
            return Some((real_uid, effective_uid));
        }
    }
    None
}

#[cfg(any(target_os = "linux", test))]
fn parse_proc_status(status: &str) -> (u64, u32) {
    let mut vol: u64 = 0;
    let mut nonvol: u64 = 0;
    let mut threads: u64 = 0;
    for line in status.lines() {
        if line.starts_with("Threads:") {
            threads =
                line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
        } else if line.starts_with("voluntary_ctxt_switches:") {
            vol = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
        } else if line.starts_with("nonvoluntary_ctxt_switches:") {
            nonvol =
                line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
        }
    }
    let total_cs = u32::try_from(vol.saturating_add(nonvol)).unwrap_or(u32::MAX);
    (threads, total_cs)
}

/// Returns (threads, voluntary + nonvoluntary context switches) from /proc/<pid>/status.
#[cfg(target_os = "linux")]
pub(super) fn read_proc_status(pid: u32) -> (u64, u32) {
    std::fs::read_to_string(format!("/proc/{}/status", pid))
        .map(|s| parse_proc_status(&s))
        .unwrap_or((0, 0))
}

#[cfg(not(target_os = "linux"))]
pub(super) fn read_proc_status(_pid: u32) -> (u64, u32) {
    (0, 0)
}

struct ResolvedEmuSample<'a> {
    sample: &'a ProcSample,
    parent: Option<u32>,
    port: Option<u16>,
    avd_name: Option<String>,
    /// AVD id from this process's own `-avd`, `-name`, `@` or `avd_id=` flag.
    explicit_avd: Option<String>,
    no_netsim: bool,
    is_worker: bool,
}

impl ResolvedEmuSample<'_> {
    fn merge_identity(
        &mut self,
        other_port: Option<u16>,
        other_avd: Option<&String>,
        other_avd_explicit: bool,
        other_no_netsim: bool,
    ) {
        if self.port.is_none() && other_port.is_some() {
            self.port = other_port;
        }
        if self.avd_name.is_none() || (self.explicit_avd.is_none() && other_avd_explicit) {
            if let Some(avd) = other_avd {
                self.avd_name = Some(avd.clone());
                if other_avd_explicit {
                    self.explicit_avd = Some(avd.clone());
                }
            }
        }
        if other_no_netsim {
            self.no_netsim = true;
        }
    }
}

#[derive(Clone)]
struct EmuIdentity {
    port: Option<u16>,
    avd_name: Option<String>,
    avd_explicit: bool,
    no_netsim: bool,
}

fn collect_netsim_daemon(
    sample: &ProcSample,
    lookups: &ProcessLookups,
) -> crate::netsim::NetsimDaemonProc {
    let parent = sample.parent.or_else(|| (lookups.parent_of)(sample.pid));
    let parent_name = parent.and_then(lookups.name_of);
    crate::netsim::NetsimDaemonProc {
        pid: sample.pid,
        parent,
        parent_name,
        start_time: sample.start_time,
        exe: sample.exe.clone(),
        cmd: sample.cmd.iter().map(|s| s.to_string_lossy().into_owned()).collect(),
        environ: (lookups.environ_of)(sample.pid),
        cpu: sample.cpu,
        ram_mb: sample.ram_mb,
        uid: sample.uid,
        same_user: is_same_user(sample.uid, lookups.self_uid),
    }
}

fn resolve_emu_sample<'a>(
    sample: &'a ProcSample,
    lookups: &ProcessLookups,
    port_cache: &PortCache,
) -> ResolvedEmuSample<'a> {
    let cache_key = (sample.pid, sample.start_time);
    let (cmd_port, cmd_explicit_avd, fallback_avd, no_netsim) = parse_cmdline_details(&sample.cmd);
    let (port, explicit_avd, avd_name) = if let Some(cached) = port_cache.get(&cache_key) {
        let exp = cached.explicit_avd.clone().or(cmd_explicit_avd);
        let avd = exp.clone().or_else(|| cached.avd_name.clone()).or(fallback_avd);
        (cached.port.or(cmd_port), exp, avd)
    } else {
        let avd = cmd_explicit_avd.clone().or(fallback_avd);
        (cmd_port, cmd_explicit_avd, avd)
    };
    let parent = sample.parent.or_else(|| (lookups.parent_of)(sample.pid));
    let is_worker = is_worker_process(&sample.name, sample.exe.as_deref(), &sample.cmd);
    ResolvedEmuSample { sample, parent, port, avd_name, explicit_avd, no_netsim, is_worker }
}

/// Propagates `port`, `avd_name`, and `no_netsim` between parent and child
/// emulator processes across two passes so supervisor, worker, and GUI share
/// the same emulator identity regardless of which process carries the flag.
fn propagate_emu_hierarchy(emu_samples: &mut [ResolvedEmuSample<'_>]) {
    for _ in 0..HIERARCHY_PROPAGATION_PASSES {
        let by_pid: std::collections::HashMap<u32, EmuIdentity> = emu_samples
            .iter()
            .map(|e| {
                (
                    e.sample.pid,
                    EmuIdentity {
                        port: e.port,
                        avd_name: e.avd_name.clone(),
                        avd_explicit: e.explicit_avd.is_some(),
                        no_netsim: e.no_netsim,
                    },
                )
            })
            .collect();

        for item in emu_samples.iter_mut() {
            let Some(pp) = item.parent else {
                continue;
            };
            if let Some(parent_id) = by_pid.get(&pp) {
                item.merge_identity(
                    parent_id.port,
                    parent_id.avd_name.as_ref(),
                    parent_id.avd_explicit,
                    parent_id.no_netsim,
                );
            }
        }

        let child_snapshots: Vec<(u32, EmuIdentity)> = emu_samples
            .iter()
            .filter_map(|e| {
                Some((
                    e.parent?,
                    EmuIdentity {
                        port: e.port,
                        avd_name: e.avd_name.clone(),
                        avd_explicit: e.explicit_avd.is_some(),
                        no_netsim: e.no_netsim,
                    },
                ))
            })
            .collect();

        for (parent_pid, child_id) in &child_snapshots {
            for item in emu_samples.iter_mut() {
                if item.sample.pid == *parent_pid {
                    item.merge_identity(
                        child_id.port,
                        child_id.avd_name.as_ref(),
                        child_id.avd_explicit,
                        child_id.no_netsim,
                    );
                }
            }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct PrimaryRank {
    is_worker: bool,
    cpu: f32,
    ram_mb: f64,
    start_time: u64,
}

impl PrimaryRank {
    fn outranks(&self, prev: &Self) -> bool {
        (self.is_worker && !prev.is_worker)
            || (self.is_worker == prev.is_worker
                && (self.cpu > prev.cpu || (self.cpu == prev.cpu && self.ram_mb > prev.ram_mb)))
    }
}

struct PortGroup {
    emu: EmulatorProcess,
    primary_rank: PrimaryRank,
    primary_uid: Option<u32>,
    pids: Vec<u32>,
    member_start_times: std::collections::BTreeMap<u32, u64>,
    avd_id: Option<String>,
    no_netsim: bool,
    netsim_endpoint: Option<String>,
}

impl PortGroup {
    fn new(
        emu: EmulatorProcess,
        primary_rank: PrimaryRank,
        primary_uid: Option<u32>,
        no_netsim: bool,
        netsim_endpoint: Option<String>,
    ) -> Self {
        let pid = emu.pid;
        let member_start_times = std::collections::BTreeMap::from([(pid, primary_rank.start_time)]);
        Self {
            emu,
            primary_rank,
            primary_uid,
            pids: vec![pid],
            member_start_times,
            avd_id: None,
            no_netsim,
            netsim_endpoint,
        }
    }

    fn merge(
        &mut self,
        emu: EmulatorProcess,
        rank: PrimaryRank,
        sample_uid: Option<u32>,
        no_netsim: bool,
        endpoint: Option<String>,
    ) {
        self.pids.push(emu.pid);
        self.member_start_times.insert(emu.pid, rank.start_time);
        if no_netsim {
            self.no_netsim = true;
        }
        if self.netsim_endpoint.is_none() {
            self.netsim_endpoint = endpoint;
        }
        let is_new_primary = rank.outranks(&self.primary_rank);
        if is_new_primary {
            self.primary_rank = rank;
            self.emu.pid = emu.pid;
            self.primary_uid = sample_uid.or(self.primary_uid);
            if emu.avd_name.is_some() {
                self.emu.avd_name = emu.avd_name;
            }
        } else {
            if self.primary_uid.is_none() {
                self.primary_uid = sample_uid;
            }
            if self.emu.avd_name.is_none() && emu.avd_name.is_some() {
                self.emu.avd_name = emu.avd_name;
            }
        }
        self.emu.cpu_usage += emu.cpu_usage;
        self.emu.ram_mb += emu.ram_mb;
        self.emu.threads += emu.threads;
        self.emu.context_switches = self.emu.context_switches.saturating_add(emu.context_switches);
        if let Some(g) = emu.gpu_load {
            self.emu.gpu_load = Some(self.emu.gpu_load.unwrap_or(0.0) + g);
        }
    }
}

fn group_emu_samples(
    emu_samples: Vec<ResolvedEmuSample<'_>>,
    lookups: &ProcessLookups,
    port_cache: &mut PortCache,
    now: std::time::Instant,
) -> (std::collections::HashMap<u16, PortGroup>, Vec<EmulatorProcess>) {
    let mut port_groups: std::collections::HashMap<u16, PortGroup> =
        std::collections::HashMap::new();
    let mut unmapped_emus: Vec<EmulatorProcess> = Vec::new();

    for resolved in emu_samples {
        let sample = resolved.sample;
        let cache_key = (sample.pid, sample.start_time);
        if resolved.port.is_some() {
            port_cache.insert(
                cache_key,
                PortCacheEntry {
                    port: resolved.port,
                    avd_name: resolved.avd_name.clone(),
                    explicit_avd: resolved.explicit_avd.clone(),
                    first_probe_at: now,
                    unmapped_probes: 0,
                },
            );
        } else {
            let entry = port_cache.entry(cache_key).or_insert_with(|| PortCacheEntry {
                port: None,
                avd_name: resolved.avd_name.clone(),
                explicit_avd: resolved.explicit_avd.clone(),
                first_probe_at: now,
                unmapped_probes: 0,
            });
            if entry.avd_name.is_none()
                || (entry.explicit_avd.is_none() && resolved.explicit_avd.is_some())
            {
                entry.avd_name = resolved.avd_name.clone();
            }
            if entry.explicit_avd.is_none() {
                entry.explicit_avd = resolved.explicit_avd.clone();
            }
            entry.unmapped_probes = entry.unmapped_probes.saturating_add(1);
        }
        let (threads, context_switches) = (lookups.proc_status)(sample.pid);
        let emu = EmulatorProcess {
            pid: sample.pid,
            port: resolved.port,
            avd_name: resolved.avd_name,
            cpu_usage: sample.cpu,
            ram_mb: sample.ram_mb,
            threads,
            context_switches,
            gpu_load: lookups.gpu_by_pid.get(&sample.pid).copied(),
        };

        if let Some(port) = resolved.port {
            let rank = PrimaryRank {
                is_worker: resolved.is_worker,
                cpu: sample.cpu,
                ram_mb: sample.ram_mb,
                start_time: sample.start_time,
            };
            let endpoint = parse_cmdline_netsim_endpoint(&sample.cmd);
            let group =
                match port_groups.entry(port) {
                    std::collections::hash_map::Entry::Occupied(entry) => {
                        let g = entry.into_mut();
                        g.merge(emu, rank, sample.uid, resolved.no_netsim, endpoint);
                        g
                    }
                    std::collections::hash_map::Entry::Vacant(entry) => entry.insert(
                        PortGroup::new(emu, rank, sample.uid, resolved.no_netsim, endpoint),
                    ),
                };
            if group.avd_id.is_none() {
                group.avd_id = resolved.explicit_avd;
            }
        } else {
            unmapped_emus.push(emu);
        }
    }

    (port_groups, unmapped_emus)
}

fn expected_discovery_uid(sample_uid: Option<u32>, self_uid: Option<u32>) -> Option<u32> {
    if is_same_user(sample_uid, self_uid) { self_uid.filter(|&u| u == 0) } else { sample_uid }
}

fn finalize_port_group(
    port: u16,
    group: PortGroup,
    lookups: &ProcessLookups,
    cached_ini: Option<std::collections::HashMap<String, String>>,
) -> (EmulatorProcess, crate::netsim::EmuGroupNetsimInfo) {
    let mut emu = group.emu;
    let serial = format!("emulator-{}", port);
    let pid_ini = cached_ini.unwrap_or_else(|| {
        let env = (lookups.environ_of)(emu.pid);
        let expected_uid = expected_discovery_uid(group.primary_uid, lookups.self_uid);
        crate::netsim::discover_emulator_pid_ini(emu.pid, &env, expected_uid)
    });
    let avd_id = pid_ini
        .get("avd.id")
        .filter(|s| !s.is_empty())
        .cloned()
        .or_else(|| group.avd_id.filter(|s| !s.is_empty()))
        .or_else(|| emu.avd_name.clone().filter(|s| !s.is_empty()));
    emu.avd_name = avd_id.clone();
    // Only avd.name: consumers fall back to avd_id themselves and must
    // be able to tell the two apart.
    let display_name =
        pid_ini.get("avd.name").filter(|s| !s.is_empty()).map(|s| s.replace('_', " "));
    // The emulator's gRPC plugin writes the endpoint as `netsim.endpoint`.
    let grpc_endpoint = group
        .netsim_endpoint
        .filter(|s| !s.is_empty())
        .or_else(|| pid_ini.get("netsim.endpoint").filter(|s| !s.is_empty()).cloned());
    let info = crate::netsim::EmuGroupNetsimInfo {
        serial,
        port,
        primary_pid: emu.pid,
        primary_start_time: group.primary_rank.start_time,
        pids: group.pids,
        member_start_times: group.member_start_times,
        avd_id,
        display_name,
        grpc_endpoint,
        no_netsim: group.no_netsim,
    };
    (emu, info)
}

pub fn aggregate_processes(
    samples: &[ProcSample],
    lookups: &ProcessLookups,
    port_cache: &mut PortCache,
) -> ProcessAggregate {
    let mut netsim_cpu = 0.0;
    let mut netsim_ram_mb = 0.0;
    let mut netsim_count = 0;
    let mut netsim_daemons = Vec::new();
    let mut emu_samples = Vec::new();

    for sample in samples {
        if is_descendant_of_self(sample.pid, sample.parent, lookups.self_pid, lookups.parent_of) {
            continue;
        }
        if is_netsimd_process(&sample.name, sample.exe.as_deref(), &sample.cmd) {
            netsim_cpu += sample.cpu;
            netsim_ram_mb += sample.ram_mb;
            netsim_count += 1;
            netsim_daemons.push(collect_netsim_daemon(sample, lookups));
        } else if is_emulator_process(&sample.name, sample.exe.as_deref(), &sample.cmd) {
            emu_samples.push(resolve_emu_sample(sample, lookups, port_cache));
        }
    }

    propagate_emu_hierarchy(&mut emu_samples);
    let now = std::time::Instant::now();
    let mut ini_by_pid: std::collections::HashMap<u32, std::collections::HashMap<String, String>> =
        std::collections::HashMap::new();
    let mut found_ini_identity = false;
    for item in &mut emu_samples {
        let cache_key = (item.sample.pid, item.sample.start_time);
        let already_settled = port_cache
            .get(&cache_key)
            .is_some_and(|entry| entry.port.is_some() || entry.is_settled_unmapped(now));
        if item.port.is_none() && !already_settled {
            let env = (lookups.environ_of)(item.sample.pid);
            let expected_uid = expected_discovery_uid(item.sample.uid, lookups.self_uid);
            let ini = crate::netsim::discover_emulator_pid_ini(item.sample.pid, &env, expected_uid);
            if let Some(id) = ini.get("avd.id").filter(|s| !s.is_empty()) {
                if item.explicit_avd.is_none() {
                    item.explicit_avd = Some(id.clone());
                    item.avd_name = Some(id.clone());
                    found_ini_identity = true;
                }
            }
            if let Some(p) =
                ini.get("port.serial").and_then(|s| s.parse::<u16>().ok()).filter(|&p| p > 0)
            {
                item.port = Some(p);
                found_ini_identity = true;
            }
            ini_by_pid.insert(item.sample.pid, ini);
        }
    }
    if found_ini_identity {
        propagate_emu_hierarchy(&mut emu_samples);
    }

    for check_workers in [true, false] {
        let mut found_socket_port = false;
        for item in &mut emu_samples {
            if item.is_worker != check_workers {
                continue;
            }
            let cache_key = (item.sample.pid, item.sample.start_time);
            let already_settled = port_cache
                .get(&cache_key)
                .is_some_and(|entry| entry.port.is_some() || entry.is_settled_unmapped(now));
            if item.port.is_none() && !already_settled {
                if let Some(port) = (lookups.listening_port)(item.sample.pid) {
                    item.port = Some(port);
                    found_socket_port = true;
                }
            }
        }
        if found_socket_port {
            propagate_emu_hierarchy(&mut emu_samples);
        }
    }
    let (port_groups, unmapped_emus) = group_emu_samples(emu_samples, lookups, port_cache, now);

    let mut emu_groups = std::collections::HashMap::with_capacity(port_groups.len());
    let mut emulators = Vec::with_capacity(port_groups.len() + unmapped_emus.len());
    for (port, group) in port_groups {
        let cached_ini = ini_by_pid.remove(&group.emu.pid);
        let (emu, info) = finalize_port_group(port, group, lookups, cached_ini);
        emu_groups.insert(info.serial.clone(), info);
        emulators.push(emu);
    }
    emulators.extend(unmapped_emus);

    ProcessAggregate {
        emulators,
        netsim_cpu,
        netsim_ram_mb,
        netsim_count,
        netsim_daemons,
        emu_groups,
    }
}

#[cfg(test)]
mod tests {
    use super::super::cmdline::*;
    use super::super::metrics::compute_paging_stats;
    use super::*;

    #[test]
    fn test_b1_b4_b5_b6_aggregate_processes() {
        let gpu_map = std::collections::HashMap::new();
        let parents: std::collections::HashMap<u32, u32> =
            [(1001, 1000), (2002, 9999)].into_iter().collect();
        let environ_calls = std::cell::RefCell::new(Vec::new());
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|pid| parents.get(&pid).copied(),
            listening_port: &|_| None,
            proc_status: &|_| (4, 50),
            environ_of: &|pid| {
                environ_calls.borrow_mut().push(pid);
                if pid == 2000 {
                    std::collections::HashMap::from([("TMPDIR".to_string(), "/tmp/x".to_string())])
                } else {
                    std::collections::HashMap::new()
                }
            },
            name_of: &|pid| (pid == 1000).then(|| "LauncherLoop".to_string()),
        };
        let samples = vec![
            // B6: LauncherLoop (comm = "LauncherLoop", exe = "/sdk/emulator/emulator")
            ProcSample {
                pid: 1000,
                parent: Some(1),
                name: "LauncherLoop".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![
                    OsString::from("/sdk/emulator/emulator"),
                    OsString::from("-avd"),
                    OsString::from("my-avd"),
                ],
                start_time: 100,
                cpu: 85.0, // GUI/Launcher spike higher than worker!
                ram_mb: 120.0,
                uid: Some(1000),
            },
            // Worker: qemu-system-x86_64 child of 1000
            ProcSample {
                pid: 1001,
                parent: Some(1000),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![
                    OsString::from("qemu-system-x86_64"),
                    OsString::from("-device"),
                    OsString::from("virtio-goldfish-adb,host_port=5555"),
                ],
                start_time: 101,
                cpu: 20.0,
                ram_mb: 2048.0,
                uid: Some(1000),
            },
            // B5: qemu-img should be excluded
            ProcSample {
                pid: 1002,
                parent: Some(1),
                name: "qemu-img".to_string(),
                exe: Some(std::path::PathBuf::from("/usr/bin/qemu-img")),
                cmd: vec![OsString::from("qemu-img"), OsString::from("info")],
                start_time: 102,
                cpu: 50.0,
                ram_mb: 64.0,
                uid: Some(1000),
            },
            // B4: netsim CLI and netsim-ui and child of self_pid should be excluded, real netsimd included
            ProcSample {
                pid: 2000,
                parent: Some(1000),
                name: "netsimd".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/netsimd")),
                cmd: vec![OsString::from("/sdk/emulator/netsimd")],
                start_time: 100,
                cpu: 3.5,
                ram_mb: 42.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 2001,
                parent: Some(1),
                name: "netsim".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/netsim")),
                cmd: vec![OsString::from("netsim"), OsString::from("devices")],
                start_time: 105,
                cpu: 10.0,
                ram_mb: 15.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 2002,
                parent: Some(9999),
                name: "netsimd".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/netsimd")),
                cmd: vec![OsString::from("netsimd")],
                start_time: 106,
                cpu: 9.0,
                ram_mb: 10.0,
                uid: Some(1000),
            },
        ];

        let mut cache = PortCache::new();
        let agg = aggregate_processes(&samples, &lookups, &mut cache);
        assert_eq!(agg.netsim_count, 1);
        assert!((agg.netsim_cpu - 3.5).abs() < 1e-4);
        assert_eq!(agg.emulators.len(), 1);
        let emu = &agg.emulators[0];
        // B1: Worker PID (1001) stays primary even though LauncherLoop (1000) has higher CPU (85% > 20%)
        assert_eq!(emu.pid, 1001);
        assert_eq!(emu.port, Some(5554));
        assert_eq!(emu.avd_name.as_deref(), Some("my-avd"));
        // B6: Both LauncherLoop and worker CPU/RAM are summed
        assert!((emu.cpu_usage - 105.0).abs() < 1e-4);
        assert!((emu.ram_mb - 2168.0).abs() < 1e-4);
        // netsimd's parent name and env, and the primary emulator's env (for
        // its pid_<pid>.ini), come from the injected lookups on every host.
        let daemon = &agg.netsim_daemons[0];
        assert_eq!(daemon.pid, 2000);
        assert_eq!(daemon.parent_name.as_deref(), Some("LauncherLoop"));
        assert_eq!(daemon.environ.get("TMPDIR").map(String::as_str), Some("/tmp/x"));
        assert_eq!(*environ_calls.borrow(), [2000, 1001]);
    }

    #[test]
    fn test_aggregate_processes_netsim_endpoint_sources() {
        // Worker 4_000_001 names its netsim endpoint on the cmdline. Worker
        // 4_000_003 does not, so the endpoint comes from its pid_<pid>.ini,
        // where the emulator writes netsim.endpoint (never grpc.endpoint).
        let discovery = tempfile::tempdir().unwrap();
        let running = discovery.path().join("avd").join("running");
        std::fs::create_dir_all(&running).unwrap();
        std::fs::write(
            running.join("pid_4000003.ini"),
            "avd.id=Pixel_8\ngrpc.endpoint=localhost:1111\nnetsim.endpoint=localhost:9999\n",
        )
        .unwrap();
        let discovery_env = std::collections::HashMap::from([(
            "ANDROID_EMULATOR_DISCOVERY_DIR".to_string(),
            discovery.path().to_string_lossy().into_owned(),
        )]);
        let gpu_map = std::collections::HashMap::new();
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            listening_port: &|_| None,
            proc_status: &|_| (1, 1),
            environ_of: &|_| discovery_env.clone(),
            name_of: &|_| None,
        };
        let worker = |pid: u32, devices: &[&str]| {
            let mut cmd = vec![OsString::from("qemu-system-x86_64")];
            for device in devices {
                cmd.extend([OsString::from("-device"), OsString::from(*device)]);
            }
            ProcSample {
                pid,
                parent: Some(1),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd,
                start_time: 100,
                cpu: 10.0,
                ram_mb: 100.0,
                uid: Some(1000),
            }
        };
        let samples = vec![
            worker(
                4_000_001,
                &[
                    "avdstart,serial_number=5554",
                    "netsim-connection,id=netsim,grpc_endpoint=localhost:8877",
                ],
            ),
            worker(4_000_003, &["avdstart,serial_number=5556"]),
        ];
        let agg = aggregate_processes(&samples, &lookups, &mut PortCache::new());
        let endpoint = |serial: &str| agg.emu_groups[serial].grpc_endpoint.as_deref();
        assert_eq!(endpoint("emulator-5554"), Some("localhost:8877"));
        assert_eq!(endpoint("emulator-5556"), Some("localhost:9999"));
        assert_eq!(agg.emu_groups["emulator-5556"].avd_id.as_deref(), Some("Pixel_8"));
    }

    #[test]
    fn test_aggregate_processes_display_name_comes_only_from_pid_ini() {
        // Worker 4_000_005 has a pid_<pid>.ini with avd.name. Worker 4_000_007
        // only has an AVD id on its cmdline, so it has no display name and the
        // netsim tracker falls back to the AVD id itself.
        let discovery = tempfile::tempdir().unwrap();
        let running = discovery.path().join("avd").join("running");
        std::fs::create_dir_all(&running).unwrap();
        std::fs::write(running.join("pid_4000005.ini"), "avd.id=Pixel_8\navd.name=Pixel_8_Pro\n")
            .unwrap();
        let discovery_env = std::collections::HashMap::from([(
            "ANDROID_EMULATOR_DISCOVERY_DIR".to_string(),
            discovery.path().to_string_lossy().into_owned(),
        )]);
        let gpu_map = std::collections::HashMap::new();
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            listening_port: &|_| None,
            proc_status: &|_| (1, 1),
            environ_of: &|_| discovery_env.clone(),
            name_of: &|_| None,
        };
        let worker = |pid: u32, args: &[&str]| ProcSample {
            pid,
            parent: Some(1),
            name: "qemu-system-x86_64".to_string(),
            exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
            cmd: std::iter::once("qemu-system-x86_64")
                .chain(args.iter().copied())
                .map(OsString::from)
                .collect(),
            start_time: 100,
            cpu: 10.0,
            ram_mb: 100.0,
            uid: Some(1000),
        };
        let samples = vec![
            worker(4_000_005, &["-device", "avdstart,serial_number=5558"]),
            worker(4_000_007, &["-avd", "Tablet", "-device", "avdstart,serial_number=5560"]),
        ];
        let agg = aggregate_processes(&samples, &lookups, &mut PortCache::new());
        let with_ini = &agg.emu_groups["emulator-5558"];
        assert_eq!(with_ini.display_name.as_deref(), Some("Pixel 8 Pro"));
        assert_eq!(with_ini.avd_id.as_deref(), Some("Pixel_8"));
        let without_ini = &agg.emu_groups["emulator-5560"];
        assert_eq!(without_ini.display_name, None);
        assert_eq!(without_ini.avd_id.as_deref(), Some("Tablet"));
    }

    #[test]
    fn test_primary_rank_and_hierarchy_propagation() {
        let non_worker = PrimaryRank { is_worker: false, cpu: 90.0, ram_mb: 500.0, start_time: 10 };
        let worker_low = PrimaryRank { is_worker: true, cpu: 10.0, ram_mb: 100.0, start_time: 20 };
        let worker_high_ram =
            PrimaryRank { is_worker: true, cpu: 10.0, ram_mb: 2048.0, start_time: 30 };
        assert!(worker_low.outranks(&non_worker));
        assert!(!non_worker.outranks(&worker_low));
        assert!(worker_high_ram.outranks(&worker_low));

        let s_parent = ProcSample { pid: 10, parent: Some(1), ..Default::default() };
        let s_child = ProcSample { pid: 11, parent: Some(10), ..Default::default() };
        let mut resolved = vec![
            ResolvedEmuSample {
                sample: &s_parent,
                parent: s_parent.parent,
                port: None,
                avd_name: Some("Pixel_9".to_string()),
                explicit_avd: Some("Pixel_9".to_string()),
                no_netsim: true,
                is_worker: false,
            },
            ResolvedEmuSample {
                sample: &s_child,
                parent: s_child.parent,
                port: Some(5554),
                avd_name: None,
                explicit_avd: None,
                no_netsim: false,
                is_worker: true,
            },
        ];
        propagate_emu_hierarchy(&mut resolved);
        assert_eq!(resolved[0].port, Some(5554));
        assert_eq!(resolved[1].avd_name.as_deref(), Some("Pixel_9"));
        assert!(resolved[1].no_netsim);
    }

    #[test]
    fn test_emulator_console_port_helpers() {
        assert!(is_emulator_console_port(5554));
        assert!(is_emulator_console_port(5584));
        assert!(!is_emulator_console_port(5555));
        assert!(!is_emulator_console_port(5552));
        assert!(!is_emulator_console_port(5586));

        assert_eq!(parse_listening_console_port("127.0.0.1:5554"), Some(5554));
        assert_eq!(parse_listening_console_port("[::1]:5556"), Some(5556));
        assert_eq!(parse_listening_console_port("127.0.0.1:5555"), None);
        assert_eq!(parse_listening_console_port("invalid"), None);
    }

    #[test]
    fn test_context_switches_saturate_and_paging_guards_zero_interval() {
        let stats = compute_paging_stats(Some((200, 100)), 100, 100, 0.0, "KB/s");
        assert_eq!(stats.in_rate, 0.0);
        assert_eq!(stats.out_rate, 0.0);
        assert_eq!(stats.display, "In: 0 / Out: 0 (KB/s)");

        let make_emu = |pid: u32, cs: u32| EmulatorProcess {
            pid,
            port: Some(5554),
            avd_name: Some("Pixel_7".to_string()),
            cpu_usage: 10.0,
            ram_mb: 100.0,
            threads: 4,
            context_switches: cs,
            gpu_load: None,
        };
        let rank = PrimaryRank { is_worker: true, cpu: 10.0, ram_mb: 100.0, start_time: 1 };
        let mut group = PortGroup::new(make_emu(1000, 3_000_000_000), rank, None, false, None);
        group.merge(make_emu(1001, 2_000_000_000), rank, None, false, None);
        assert_eq!(group.emu.context_switches, u32::MAX);

        let status = "Threads:\t12\nvoluntary_ctxt_switches:\t3000000000\nnonvoluntary_ctxt_switches:\t2000000000\n";
        assert_eq!(parse_proc_status(status), (12, u32::MAX));
    }

    #[test]
    fn test_parse_windows_netstat_listening_port_matches_exact_pid_column() {
        let netstat = "  Proto  Local Address          Foreign Address        State           PID\n\
                         TCP    127.0.0.1:5554         0.0.0.0:0              LISTENING       12345\n\
                         TCP    [::1]:5556             [::]:0                 LISTENING       67890\n";
        assert_eq!(parse_windows_netstat_listening_port(netstat, 555), None);
        assert_eq!(parse_windows_netstat_listening_port(netstat, 5554), None);
        assert_eq!(parse_windows_netstat_listening_port(netstat, 1234), None);
        assert_eq!(parse_windows_netstat_listening_port(netstat, 12345), Some(5554));
        assert_eq!(parse_windows_netstat_listening_port(netstat, 67890), Some(5556));
    }

    #[test]
    fn test_parse_macos_lsof_listening_port_filters_by_pid_and_listen_endpoint() {
        // Without -a, lsof includes other processes' TCP listeners; p<pid> must match target pid.
        let tabular_foreign = "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n\
                               qemu-sys 9999 user   21u  IPv4 0x1234      0t0  TCP 127.0.0.1:5554 (LISTEN)\n";
        assert_eq!(parse_macos_lsof_listening_port(tabular_foreign, 12345), None);

        let field_out = "p9999\n\
                         n127.0.0.1:5554\n\
                         p12345\n\
                         n127.0.0.1:5555\n\
                         n127.0.0.1:51234->127.0.0.1:5556\n\
                         n[::1]:5556\n\
                         p67890\n\
                         n*:5558\n";
        assert_eq!(parse_macos_lsof_listening_port(field_out, 5554), None);
        assert_eq!(parse_macos_lsof_listening_port(field_out, 12345), Some(5556));
        assert_eq!(parse_macos_lsof_listening_port(field_out, 67890), Some(5558));
    }

    #[test]
    fn test_avd_id_beats_avd_name_across_cmdline_hierarchy_and_pid_ini() {
        // On a single cmdline, -avd / avd_id= beats avd_name= regardless of flag order.
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-avd"),
            OsString::from("Pixel_8"),
            OsString::from("-device"),
            OsString::from("avdstart,serial_number=5554,avd_name=\"Pixel 8 Pro\""),
        ];
        assert_eq!(parse_cmdline(&cmdline), (Some(5554), Some("Pixel_8".to_string())));

        // Across supervisor (-avd) + worker (avd_name=) and pid_<pid>.ini (avd.id),
        // both EmulatorProcess::avd_name and EmuGroupNetsimInfo::avd_id resolve to the AVD id.
        let discovery = tempfile::tempdir().unwrap();
        let running = discovery.path().join("avd").join("running");
        std::fs::create_dir_all(&running).unwrap();
        std::fs::write(running.join("pid_4000010.ini"), "avd.id=Pixel_8\navd.name=Pixel_8_Pro\n")
            .unwrap();
        let discovery_env = std::collections::HashMap::from([(
            "ANDROID_EMULATOR_DISCOVERY_DIR".to_string(),
            discovery.path().to_string_lossy().into_owned(),
        )]);
        let gpu_map = std::collections::HashMap::new();
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            listening_port: &|_| None,
            proc_status: &|_| (1, 1),
            environ_of: &|_| discovery_env.clone(),
            name_of: &|_| None,
        };
        let samples = vec![
            ProcSample {
                pid: 4_000_009,
                parent: Some(1),
                name: "emulator".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![
                    OsString::from("emulator"),
                    OsString::from("-avd"),
                    OsString::from("Pixel_8"),
                    OsString::from("-port"),
                    OsString::from("5554"),
                ],
                start_time: 100,
                cpu: 1.0,
                ram_mb: 30.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_010,
                parent: Some(4_000_009),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![
                    OsString::from("qemu-system-x86_64"),
                    OsString::from("-device"),
                    OsString::from("avdstart,serial_number=5554,avd_name=\"Pixel 8 Pro\""),
                ],
                start_time: 100,
                cpu: 50.0,
                ram_mb: 2048.0,
                uid: Some(1000),
            },
        ];
        let agg = aggregate_processes(&samples, &lookups, &mut PortCache::new());
        assert_eq!(agg.emu_groups["emulator-5554"].avd_id.as_deref(), Some("Pixel_8"));
        assert_eq!(agg.emulators[0].avd_name.as_deref(), Some("Pixel_8"));

        // Even before pid_<pid>.ini exists, supervisor -avd Pixel_8 beats worker avd_name="Pixel 8 Pro".
        let empty_lookups =
            ProcessLookups { environ_of: &|_| std::collections::HashMap::new(), ..lookups };
        let agg_no_ini = aggregate_processes(&samples, &empty_lookups, &mut PortCache::new());
        assert_eq!(agg_no_ini.emu_groups["emulator-5554"].avd_id.as_deref(), Some("Pixel_8"));
        assert_eq!(agg_no_ini.emulators[0].avd_name.as_deref(), Some("Pixel_8"));
    }

    #[test]
    fn test_listening_port_probed_only_after_hierarchy_propagation() {
        let gpu_map = std::collections::HashMap::new();
        let listening_calls = std::cell::RefCell::new(Vec::new());
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|pid| (pid == 1001).then_some(1000),
            listening_port: &|pid| {
                listening_calls.borrow_mut().push(pid);
                (pid == 2000).then_some(5556)
            },
            proc_status: &|_| (1, 1),
            environ_of: &|_| std::collections::HashMap::new(),
            name_of: &|_| None,
        };
        let samples = vec![
            // Supervisor (1000) gets port 5554 from child worker (1001) via hierarchy propagation;
            // listening_port must not be called for 1000 or 1001.
            ProcSample {
                pid: 1000,
                parent: Some(1),
                name: "emulator".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![
                    OsString::from("emulator"),
                    OsString::from("-avd"),
                    OsString::from("Pixel_8"),
                ],
                start_time: 100,
                cpu: 1.0,
                ram_mb: 30.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 1001,
                parent: Some(1000),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![
                    OsString::from("qemu-system-x86_64"),
                    OsString::from("-device"),
                    OsString::from("avdstart,serial_number=5554"),
                ],
                start_time: 100,
                cpu: 50.0,
                ram_mb: 2048.0,
                uid: Some(1000),
            },
            // Standalone emulator (2000) has no port on cmdline or in hierarchy;
            // listening_port is called only for 2000.
            ProcSample {
                pid: 2000,
                parent: Some(1),
                name: "emulator".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![
                    OsString::from("emulator"),
                    OsString::from("-avd"),
                    OsString::from("Pixel_7"),
                ],
                start_time: 100,
                cpu: 10.0,
                ram_mb: 512.0,
                uid: Some(1000),
            },
        ];
        let agg = aggregate_processes(&samples, &lookups, &mut PortCache::new());
        assert_eq!(*listening_calls.borrow(), vec![2000]);
        assert!(agg.emu_groups.contains_key("emulator-5554"));
        assert!(agg.emu_groups.contains_key("emulator-5556"));
    }

    #[test]
    fn test_is_emulator_process_excludes_monitor_and_descendants_of_self() {
        assert!(!is_emulator_process(
            "emulator-resource-monitor",
            Some(std::path::Path::new("/usr/bin/emulator-resource-monitor")),
            &[OsString::from("emulator-resource-monitor")]
        ));
        assert!(!is_emulator_process(
            "emulator_resource_monitor",
            Some(std::path::Path::new("/usr/bin/emulator_resource_monitor")),
            &[OsString::from("emulator_resource_monitor")]
        ));
        assert!(!is_emulator_process(
            "emulator-check",
            Some(std::path::Path::new("/sdk/emulator/emulator-check")),
            &[OsString::from("emulator-check")]
        ));

        let gpu_map = std::collections::HashMap::new();
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            listening_port: &|_| None,
            proc_status: &|_| (1, 1),
            environ_of: &|_| std::collections::HashMap::new(),
            name_of: &|_| None,
        };
        let samples = vec![ProcSample {
            pid: 3000,
            parent: Some(9999),
            name: "emulator".to_string(),
            exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
            cmd: vec![OsString::from("emulator"), OsString::from("-version")],
            start_time: 100,
            cpu: 5.0,
            ram_mb: 10.0,
            uid: Some(1000),
        }];
        let agg = aggregate_processes(&samples, &lookups, &mut PortCache::new());
        assert!(agg.emulators.is_empty());
    }

    #[test]
    fn test_explicit_avd_propagates_two_hops_and_pid_ini_resolves_port_serial() {
        let discovery = tempfile::tempdir().unwrap();
        let running = discovery.path().join("avd").join("running");
        std::fs::create_dir_all(&running).unwrap();
        std::fs::write(running.join("pid_4000022.ini"), "port.serial=5554\navd.name=Pixel_8_Pro\n")
            .unwrap();
        let discovery_env = std::collections::HashMap::from([(
            "ANDROID_EMULATOR_DISCOVERY_DIR".to_string(),
            discovery.path().to_string_lossy().into_owned(),
        )]);
        let gpu_map = std::collections::HashMap::new();
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            // Simulate localized netstat / unavailable listening_port: port must come from pid.ini.
            listening_port: &|_| None,
            proc_status: &|_| (1, 1),
            environ_of: &|_| discovery_env.clone(),
            name_of: &|_| None,
        };
        let samples = vec![
            ProcSample {
                pid: 4_000_020,
                parent: Some(1),
                name: "emulator".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![
                    OsString::from("emulator"),
                    OsString::from("-avd"),
                    OsString::from("Pixel_8"),
                ],
                start_time: 100,
                cpu: 1.0,
                ram_mb: 20.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_021,
                parent: Some(4_000_020),
                name: "LauncherLoop".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![OsString::from("LauncherLoop")],
                start_time: 101,
                cpu: 2.0,
                ram_mb: 50.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_022,
                parent: Some(4_000_021),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![
                    OsString::from("qemu-system-x86_64"),
                    OsString::from("-device"),
                    OsString::from("avdstart,avd_name=\"Pixel 8 Pro\""),
                ],
                start_time: 102,
                cpu: 40.0,
                ram_mb: 2048.0,
                uid: Some(1000),
            },
        ];

        let mut cache = PortCache::new();
        let agg = aggregate_processes(&samples, &lookups, &mut cache);
        assert_eq!(agg.emu_groups["emulator-5554"].avd_id.as_deref(), Some("Pixel_8"));
        assert_eq!(agg.emulators[0].avd_name.as_deref(), Some("Pixel_8"));

        // Even after outer wrapper 4_000_020 exits, PortCache retains explicit_avd="Pixel_8" on 4_000_022.
        let agg_after = aggregate_processes(&samples[1..], &lookups, &mut cache);
        assert_eq!(agg_after.emu_groups["emulator-5554"].avd_id.as_deref(), Some("Pixel_8"));
        assert_eq!(agg_after.emulators[0].avd_name.as_deref(), Some("Pixel_8"));
    }

    #[test]
    fn test_port_cache_stops_probing_portless_emulator_after_startup_window() {
        let gpu_map = std::collections::HashMap::new();
        let probes = std::cell::Cell::new(0usize);
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            listening_port: &|_| {
                probes.set(probes.get() + 1);
                None
            },
            proc_status: &|_| (1, 1),
            environ_of: &|_| std::collections::HashMap::new(),
            name_of: &|_| None,
        };
        let samples = vec![ProcSample {
            pid: 4_000_030,
            parent: Some(1),
            name: "qemu-system-x86_64".to_string(),
            exe: Some(std::path::PathBuf::from("/usr/bin/qemu-system-x86_64")),
            cmd: vec![
                OsString::from("qemu-system-x86_64"),
                OsString::from("-m"),
                OsString::from("2048"),
            ],
            start_time: 200,
            cpu: 15.0,
            ram_mb: 512.0,
            uid: Some(1000),
        }];
        let mut cache = PortCache::new();
        for _ in 0..100 {
            let agg = aggregate_processes(&samples, &lookups, &mut cache);
            assert_eq!(agg.emulators.len(), 1);
            assert_eq!(agg.emulators[0].port, None);
        }
        assert!(
            probes.get() <= 30,
            "Portless VM must settle as unmapped after ~30 probes instead of probing forever (got {})",
            probes.get()
        );
    }

    #[test]
    fn test_empty_pid_ini_values_do_not_clobber_avd_or_endpoint() {
        let discovery = tempfile::tempdir().unwrap();
        let running = discovery.path().join("avd").join("running");
        std::fs::create_dir_all(&running).unwrap();
        std::fs::write(
            running.join("pid_4000040.ini"),
            "port.serial=5554\navd.id=\navd.name=\nnetsim.endpoint=\n",
        )
        .unwrap();
        std::fs::write(
            running.join("pid_4000041.ini"),
            "port.serial=5556\navd.id=\navd.name=\nnetsim.endpoint=\n",
        )
        .unwrap();
        let discovery_env = std::collections::HashMap::from([(
            "ANDROID_EMULATOR_DISCOVERY_DIR".to_string(),
            discovery.path().to_string_lossy().into_owned(),
        )]);
        let gpu_map = std::collections::HashMap::new();
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            listening_port: &|_| None,
            proc_status: &|_| (1, 1),
            environ_of: &|_| discovery_env.clone(),
            name_of: &|_| None,
        };
        let samples = vec![
            ProcSample {
                pid: 4_000_040,
                parent: Some(1),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![
                    OsString::from("qemu-system-x86_64"),
                    OsString::from("-avd"),
                    OsString::from("Pixel_8"),
                    OsString::from("-port"),
                    OsString::from("5554"),
                    OsString::from("-device"),
                    OsString::from("netsim-connection,id=netsim,grpc_endpoint=127.0.0.1:64001"),
                ],
                start_time: 300,
                cpu: 25.0,
                ram_mb: 1024.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_041,
                parent: Some(1),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![
                    OsString::from("qemu-system-x86_64"),
                    OsString::from("-port"),
                    OsString::from("5556"),
                ],
                start_time: 301,
                cpu: 10.0,
                ram_mb: 512.0,
                uid: Some(1000),
            },
        ];

        let mut cache = PortCache::new();
        let agg = aggregate_processes(&samples, &lookups, &mut cache);

        let g5554 = &agg.emu_groups["emulator-5554"];
        assert_eq!(g5554.avd_id.as_deref(), Some("Pixel_8"));
        assert_eq!(g5554.display_name, None);
        assert_eq!(g5554.grpc_endpoint.as_deref(), Some("127.0.0.1:64001"));
        let emu5554 = agg.emulators.iter().find(|e| e.port == Some(5554)).unwrap();
        assert_eq!(emu5554.avd_name.as_deref(), Some("Pixel_8"));

        let g5556 = &agg.emu_groups["emulator-5556"];
        assert_eq!(g5556.avd_id, None);
        assert_eq!(g5556.display_name, None);
        assert_eq!(g5556.grpc_endpoint, None);
        let emu5556 = agg.emulators.iter().find(|e| e.port == Some(5556)).unwrap();
        assert_eq!(emu5556.avd_name, None);
    }

    #[test]
    fn test_pid_ini_port_serial_and_worker_socket_avoid_listening_port_on_wrappers() {
        let discovery = tempfile::tempdir().unwrap();
        let running = discovery.path().join("avd").join("running");
        std::fs::create_dir_all(&running).unwrap();
        std::fs::write(running.join("pid_4000052.ini"), "port.serial=5554\navd.id=Pixel_8\n")
            .unwrap();
        let discovery_env = std::collections::HashMap::from([(
            "ANDROID_EMULATOR_DISCOVERY_DIR".to_string(),
            discovery.path().to_string_lossy().into_owned(),
        )]);
        let gpu_map = std::collections::HashMap::new();
        let called_pids: std::cell::RefCell<Vec<u32>> = std::cell::RefCell::new(Vec::new());
        let lookups = ProcessLookups {
            self_pid: 9999,
            self_uid: Some(1000),
            gpu_by_pid: &gpu_map,
            parent_of: &|_| None,
            listening_port: &|pid| {
                called_pids.borrow_mut().push(pid);
                if pid == 4_000_062 { Some(5556) } else { None }
            },
            proc_status: &|_| (1, 1),
            environ_of: &|_| discovery_env.clone(),
            name_of: &|_| None,
        };

        // Case A: worker pid_4000052.ini has port.serial=5554 -> 0 listening_port calls.
        let samples_ini = vec![
            ProcSample {
                pid: 4_000_050,
                parent: Some(1),
                name: "emulator".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![OsString::from("emulator"), OsString::from("@Pixel_8")],
                start_time: 400,
                cpu: 1.0,
                ram_mb: 20.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_051,
                parent: Some(4_000_050),
                name: "LauncherLoop".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![OsString::from("LauncherLoop")],
                start_time: 401,
                cpu: 2.0,
                ram_mb: 40.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_052,
                parent: Some(4_000_051),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![OsString::from("qemu-system-x86_64")],
                start_time: 402,
                cpu: 35.0,
                ram_mb: 2048.0,
                uid: Some(1000),
            },
        ];
        let mut cache = PortCache::new();
        let agg_ini = aggregate_processes(&samples_ini, &lookups, &mut cache);
        assert!(
            called_pids.borrow().is_empty(),
            "listening_port should not run when worker pid.ini resolves port.serial, got {:?}",
            *called_pids.borrow()
        );
        assert_eq!(agg_ini.emu_groups["emulator-5554"].pids.len(), 3);
        assert_eq!(agg_ini.emu_groups["emulator-5554"].avd_id.as_deref(), Some("Pixel_8"));

        // Case B: no pid.ini yet, worker 4_000_062 socket binds 5556 -> only worker is probed.
        let samples_sock = vec![
            ProcSample {
                pid: 4_000_060,
                parent: Some(1),
                name: "emulator".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![OsString::from("emulator"), OsString::from("@Pixel_9")],
                start_time: 500,
                cpu: 1.0,
                ram_mb: 20.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_061,
                parent: Some(4_000_060),
                name: "LauncherLoop".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/emulator")),
                cmd: vec![OsString::from("LauncherLoop")],
                start_time: 501,
                cpu: 2.0,
                ram_mb: 40.0,
                uid: Some(1000),
            },
            ProcSample {
                pid: 4_000_062,
                parent: Some(4_000_061),
                name: "qemu-system-x86_64".to_string(),
                exe: Some(std::path::PathBuf::from("/sdk/emulator/qemu-system-x86_64")),
                cmd: vec![OsString::from("qemu-system-x86_64")],
                start_time: 502,
                cpu: 35.0,
                ram_mb: 2048.0,
                uid: Some(1000),
            },
        ];
        let agg_sock = aggregate_processes(&samples_sock, &lookups, &mut cache);
        assert_eq!(*called_pids.borrow(), vec![4_000_062]);
        assert_eq!(agg_sock.emu_groups["emulator-5556"].pids.len(), 3);
    }

    #[test]
    fn test_parse_proc_status_uids_extracts_real_and_effective_uids() {
        assert_eq!(
            parse_proc_status_uids(
                "Name:\temulator_mon\nDumpable:\t0\nUid:\t1000\t1000\t1000\t1000\n"
            ),
            Some((1000, 1000))
        );
        assert_eq!(
            parse_proc_status_uids("Name:\temulator_mon\nUid:\t1000\t0\t0\t0\n"),
            Some((1000, 0))
        );
        assert_eq!(
            parse_proc_status_uids("Name:\temulator_mon\nUid:\t0\t501\t0\t501\n"),
            Some((0, 501))
        );
        assert_eq!(parse_proc_status_uids("Uid:\t1000\n"), None);
        assert_eq!(parse_proc_status_uids("Uid:\tabc\t0\t0\t0\n"), None);
        assert_eq!(parse_proc_status_uids("Name:\temulator_mon\n"), None);
    }
}
