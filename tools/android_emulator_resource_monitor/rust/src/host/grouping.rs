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
#[path = "../../tests/host/grouping_test.rs"]
mod tests;
