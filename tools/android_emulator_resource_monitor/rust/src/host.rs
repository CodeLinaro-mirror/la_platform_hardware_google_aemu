use sysinfo::System;
use std::ffi::OsString;

#[derive(Clone)]
pub struct EmulatorProcess {
    pub pid: u32,
    pub port: Option<u16>,
    pub avd_name: Option<String>,
    pub cpu_usage: f32,
    pub ram_mb: f64,
    pub threads: u64,
    pub context_switches: u32,
    pub gpu_load: Option<f32>,
}

pub struct HostStats {
    pub cpu_usage: f32,
    pub ram_used_gb: f64,
    pub ram_total_gb: f64,
    pub ram_percent: f32,
    pub swap_used_gb: f64,
    pub swap_total_gb: f64,
    pub swap_percent: f32,
    pub disk_read_mbs: f64,
    pub disk_write_mbs: f64,
    pub emulators: Vec<EmulatorProcess>,
    pub netsim_cpu: f32,
    pub netsim_ram_mb: f64,
    pub netsim_count: usize,
    pub netsim_daemons: Vec<crate::netsim::NetsimDaemonProc>,
    pub emu_groups: std::collections::HashMap<String, crate::netsim::EmuGroupNetsimInfo>,
    pub gpu_percent: Option<f32>,
    pub gpu_memory_mb: Option<f32>,
    pub thermal_temp: Option<f32>,
    pub paging_str: String,
    pub paging_in: f64,
    pub paging_out: f64,
    pub battery_str: String,
    pub battery_percent: Option<f32>,
}


fn extract_device_prop(s: &str, key: &str) -> Option<String> {
    if let Some(pos) = s.find(key) {
        let rest = &s[pos + key.len()..];
        let val = if let Some(comma_pos) = rest.find(',') {
            &rest[..comma_pos]
        } else {
            rest
        };
        let cleaned = val.trim_matches(|c| c == '\'' || c == '"');
        if !cleaned.is_empty() {
            return Some(cleaned.to_string());
        }
    }
    None
}

pub fn parse_cmdline_with_no_netsim(cmdline: &[OsString]) -> (Option<u16>, Option<String>, bool) {
    let mut port = None;
    let mut avd_name = None;
    let mut no_netsim = false;
    let mut skip_next = false;

    for i in 0..cmdline.len() {
        if skip_next {
            skip_next = false;
            continue;
        }
        let arg = cmdline[i].to_str().unwrap_or("");
        if arg == "-no-netsim" || arg == "--no-netsim" {
            no_netsim = true;
            continue;
        }
        if (arg == "-port" || arg == "-ports" || arg == "-fishtank") && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("");
            let val = next_arg.split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                port = Some(p);
            }
            skip_next = true;
        } else if arg.starts_with("-port=") || arg.starts_with("-ports=") {
            let val = arg.split('=').nth(1).unwrap_or("").split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                port = Some(p);
            }
        } else if arg.starts_with("-fishtank=") {
            let val = arg.split('=').nth(1).unwrap_or("").split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                port = Some(p);
            }
        } else if (arg == "-avd" || arg == "-name") && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("").trim_matches(|c| c == '\'' || c == '"');
            let next_clean = if let Some(stripped) = next_arg.strip_prefix("guest=") {
                stripped
            } else {
                next_arg
            };
            let val = next_clean.split(',').next().unwrap_or("");
            if !val.is_empty() {
                avd_name = Some(val.to_string());
            }
            skip_next = true;
        } else if let Some(stripped) = arg.strip_prefix("-avd=").or_else(|| arg.strip_prefix("-name=")) {
            let next_clean = stripped.trim_matches(|c| c == '\'' || c == '"');
            let next_clean = if let Some(s) = next_clean.strip_prefix("guest=") {
                s
            } else {
                next_clean
            };
            let val = next_clean.split(',').next().unwrap_or("");
            if !val.is_empty() {
                avd_name = Some(val.to_string());
            }
        } else if (arg.starts_with("-avd") && !arg.starts_with("-avd_name="))
            || (arg.contains("emulator") && arg.contains("-avd"))
        {
            if let Some(idx) = arg.find("-avd") {
                let rest = arg[idx + 4..].trim_matches(|c| c == '\'' || c == '"');
                if let Some(end_idx) = rest.find('-') {
                    avd_name = Some(rest[..end_idx].to_string());
                } else if !rest.is_empty() {
                    avd_name = Some(rest.to_string());
                }
            }
        } else if arg.starts_with('@') && arg.len() > 1 {
            let val = arg[1..].trim_matches(|c| c == '\'' || c == '"');
            if !val.is_empty() {
                avd_name = Some(val.to_string());
            }
        }

        if port.is_none() {
            if let Some(p) = extract_device_prop(arg, "serial_number=")
                .and_then(|val| val.parse::<u16>().ok())
            {
                port = Some(p);
            }
        }
        if port.is_none() {
            if let Some(hp) = extract_device_prop(arg, "host_port=")
                .and_then(|val| val.parse::<u16>().ok())
                .filter(|&hp| hp > 0)
            {
                port = Some(hp - 1);
            }
        }
        if avd_name.is_none() {
            if let Some(val) = extract_device_prop(arg, "avd_id=") {
                avd_name = Some(val);
            } else if let Some(val) = extract_device_prop(arg, "avd_name=") {
                avd_name = Some(val);
            }
        }

        if arg == "-device" && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("");
            if port.is_none() {
                if let Some(p) = extract_device_prop(next_arg, "serial_number=")
                    .and_then(|val| val.parse::<u16>().ok())
                {
                    port = Some(p);
                }
            }
            if port.is_none() {
                if let Some(hp) = extract_device_prop(next_arg, "host_port=")
                    .and_then(|val| val.parse::<u16>().ok())
                    .filter(|&hp| hp > 0)
                {
                    port = Some(hp - 1);
                }
            }
            if avd_name.is_none() {
                if let Some(val) = extract_device_prop(next_arg, "avd_id=") {
                    avd_name = Some(val);
                } else if let Some(val) = extract_device_prop(next_arg, "avd_name=") {
                    avd_name = Some(val);
                }
            }
            skip_next = true;
        }
    }

    (port, avd_name, no_netsim)
}

#[allow(dead_code)]
pub fn parse_cmdline(cmdline: &[OsString]) -> (Option<u16>, Option<String>) {
    let (port, avd_name, _) = parse_cmdline_with_no_netsim(cmdline);
    (port, avd_name)
}

/// The netsim gRPC endpoint that the QEMU-next launcher passes to the worker
/// as `-device netsim-connection,id=netsim,grpc_endpoint=<host:port>`.
fn parse_cmdline_netsim_endpoint(cmdline: &[OsString]) -> Option<String> {
    cmdline.iter().find_map(|arg| extract_device_prop(arg.to_str()?, "grpc_endpoint="))
}

#[cfg(target_os = "linux")]
fn get_socket_inodes(pid: u32) -> Vec<u64> {
    let mut inodes = Vec::new();
    if let Ok(entries) = std::fs::read_dir(format!("/proc/{}/fd", pid)) {
        for entry in entries {
            if let Ok(entry) = entry {
                if let Ok(link) = std::fs::read_link(entry.path()) {
                    let link_str = link.to_string_lossy();
                    if link_str.starts_with("socket:[") && link_str.ends_with("]") {
                        let inode_str = &link_str[8..link_str.len() - 1];
                        if let Ok(inode) = inode_str.parse::<u64>() {
                            inodes.push(inode);
                        }
                    }
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
                if parts.len() >= 10 && parts[3] == "0A" {
                    let inode = parts[9].parse::<u64>().unwrap_or(0);
                    if inodes.contains(&inode) {
                        let local = parts[1];
                        if let Some(port_hex) = local.split(':').nth(1) {
                            if let Ok(port) = u16::from_str_radix(port_hex, 16) {
                                if port >= 5554 && port <= 5584 && port % 2 == 0 {
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

#[cfg(target_os = "macos")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let output = std::process::Command::new("lsof")
        .args(&["-nP", "-iTCP", "-sTCP:LISTEN", "-p", &pid.to_string()])
        .output()
        .ok()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if line.contains("LISTEN") {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 9 {
                let addr = parts[8];
                if let Some(port_str) = addr.split(':').last() {
                    if let Ok(port) = port_str.parse::<u16>() {
                        if port >= 5554 && port <= 5584 && port % 2 == 0 {
                            return Some(port);
                        }
                    }
                }
            }
        }
    }
    None
}

#[cfg(target_os = "windows")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let output = std::process::Command::new("netstat")
        .args(&["-ano"])
        .output()
        .ok()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if line.contains("LISTENING") && line.contains(&pid.to_string()) {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 5 {
                let addr = parts[1];
                if let Some(port_str) = addr.split(':').last() {
                    if let Ok(port) = port_str.parse::<u16>() {
                        if port >= 5554 && port <= 5584 && port % 2 == 0 {
                            return Some(port);
                        }
                    }
                }
            }
        }
    }
    None
}
pub fn parse_nvidia_smi(stdout: &str) -> (Option<f32>, Option<f32>) {
    if let Some(line) = stdout.lines().next() {
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() >= 2 {
            let gpu = parts[0].trim().parse::<f32>().ok();
            let mem = parts[1].trim().parse::<f32>().ok();
            return (gpu, mem);
        }
    }
    (None, None)
}

pub fn parse_vmstat(stdout: &str) -> (u64, u64) {
    let mut pgin = 0;
    let mut pgout = 0;
    for line in stdout.lines() {
        if line.starts_with("pgpgin ") {
            pgin = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
        } else if line.starts_with("pgpgout ") {
            pgout = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
        }
    }
    (pgin, pgout)
}

pub fn parse_vm_stat(stdout: &str) -> (u64, u64) {
    let mut pgin = 0;
    let mut pgout = 0;
    for line in stdout.lines() {
        if line.contains("Pages paged in:") || line.starts_with("Pageins:") {
            pgin = line.split_whitespace().last().and_then(|s| s.trim_end_matches('.').parse::<u64>().ok()).unwrap_or(0);
        } else if line.contains("Pages paged out:") || line.starts_with("Pageouts:") {
            pgout = line.split_whitespace().last().and_then(|s| s.trim_end_matches('.').parse::<u64>().ok()).unwrap_or(0);
        }
    }
    (pgin, pgout)
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

pub type PortCache = std::collections::HashMap<(u32, u64), (Option<u16>, Option<String>)>;

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

fn extract_basenames(
    name: &str,
    exe: Option<&std::path::Path>,
    cmd: &[OsString],
) -> (String, String, String) {
    let name_low = name.to_lowercase();
    let exe_base = exe
        .and_then(|p| p.file_name())
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let cmd0_base = cmd
        .first()
        .and_then(|s| std::path::Path::new(s).file_name())
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    (name_low, exe_base, cmd0_base)
}

pub fn is_netsimd_process(name: &str, exe: Option<&std::path::Path>, cmd: &[OsString]) -> bool {
    let (name_low, exe_base, cmd0_base) = extract_basenames(name, exe, cmd);
    fn strip_exe(s: &str) -> &str {
        s.strip_suffix(".exe").unwrap_or(s)
    }
    strip_exe(&name_low) == "netsimd"
        || strip_exe(&exe_base) == "netsimd"
        || strip_exe(&cmd0_base) == "netsimd"
}

pub fn is_emulator_process(name: &str, exe: Option<&std::path::Path>, cmd: &[OsString]) -> bool {
    let (name_low, exe_base, cmd0_base) = extract_basenames(name, exe, cmd);
    if is_netsimd_process(name, exe, cmd) {
        return false;
    }
    let tokens = [&name_low, &exe_base, &cmd0_base];
    for t in tokens {
        if t.contains("qemu-img")
            || t.starts_with("crashpad")
            || t.contains("terminal")
            || t.contains("crash")
            || t.starts_with("netsim")
        {
            return false;
        }
    }
    let is_emu_token = |t: &str| {
        let b = t.strip_suffix(".exe").unwrap_or(t);
        b.starts_with("qemu-system")
            || b == "qemu"
            || b == "emulator"
            || b.starts_with("emulator64")
            || b.starts_with("emulator-")
            || b.contains("launcherloop")
            || b.contains("crosvm")
            || b.contains("fishtank")
    };
    is_emu_token(&name_low) || is_emu_token(&exe_base) || is_emu_token(&cmd0_base)
}

fn is_worker_process(name: &str, exe: Option<&std::path::Path>, cmd: &[OsString]) -> bool {
    let (name_low, exe_base, cmd0_base) = extract_basenames(name, exe, cmd);
    let is_worker_token = |t: &str| {
        let b = t.strip_suffix(".exe").unwrap_or(t);
        b.starts_with("qemu-system") || b == "qemu" || b.contains("crosvm")
    };
    is_worker_token(&name_low) || is_worker_token(&exe_base) || is_worker_token(&cmd0_base)
}

/// Cheap pre-filter: every process that any grouping rule could accept.
fn is_candidate_process(name: &str, exe: Option<&std::path::Path>, cmd: &[OsString]) -> bool {
    is_netsimd_process(name, exe, cmd) || is_emulator_process(name, exe, cmd)
}

fn is_descendant_of_self(
    pid: u32,
    self_pid: u32,
    parent_of: &dyn Fn(u32) -> Option<u32>,
) -> bool {
    if self_pid == 0 {
        return false;
    }
    let mut curr = Some(pid);
    for _ in 0..16 {
        let Some(p) = curr else {
            break;
        };
        if p == self_pid {
            return true;
        }
        curr = parent_of(p);
    }
    false
}

/// Returns (threads, voluntary + nonvoluntary context switches) from /proc/<pid>/status.
#[cfg(target_os = "linux")]
fn read_proc_status(pid: u32) -> (u64, u32) {
    let mut vol = 0;
    let mut nonvol = 0;
    let mut threads = 0;
    if let Ok(status) = std::fs::read_to_string(format!("/proc/{}/status", pid)) {
        for line in status.lines() {
            if line.starts_with("Threads:") {
                threads = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
            } else if line.starts_with("voluntary_ctxt_switches:") {
                vol = line.split_whitespace().nth(1).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
            } else if line.starts_with("nonvoluntary_ctxt_switches:") {
                nonvol = line.split_whitespace().nth(1).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
            }
        }
    }
    (threads, vol + nonvol)
}

#[cfg(not(target_os = "linux"))]
fn read_proc_status(_pid: u32) -> (u64, u32) {
    (0, 0)
}

#[allow(clippy::type_complexity, clippy::collapsible_if, clippy::needless_range_loop)]
pub fn aggregate_processes(
    samples: &[ProcSample],
    lookups: &ProcessLookups,
    port_cache: &mut PortCache,
) -> ProcessAggregate {
    let mut accumulated_emus: std::collections::HashMap<u16, EmulatorProcess> = std::collections::HashMap::new();
    let mut primary_rank_per_port: std::collections::HashMap<u16, (bool, f32, f64, u64)> =
        std::collections::HashMap::new();
    let mut pids_per_port: std::collections::HashMap<u16, Vec<u32>> = std::collections::HashMap::new();
    let mut no_netsim_per_port: std::collections::HashMap<u16, bool> = std::collections::HashMap::new();
    let mut netsim_endpoint_per_port: std::collections::HashMap<u16, String> =
        std::collections::HashMap::new();
    let mut unmapped_emus: Vec<EmulatorProcess> = Vec::new();
    let mut netsim_cpu = 0.0;
    let mut netsim_ram_mb = 0.0;
    let mut netsim_count = 0;
    let mut netsim_daemons = Vec::new();

    let mut emu_samples: Vec<(&ProcSample, Option<u16>, Option<String>, bool, bool)> = Vec::new();

    for sample in samples {
        let is_netsimd = is_netsimd_process(&sample.name, sample.exe.as_deref(), &sample.cmd);
        if is_netsimd {
            let parent = sample.parent.or_else(|| (lookups.parent_of)(sample.pid));
            if is_descendant_of_self(sample.pid, lookups.self_pid, lookups.parent_of)
                || parent == Some(lookups.self_pid)
            {
                continue;
            }
            netsim_cpu += sample.cpu;
            netsim_ram_mb += sample.ram_mb;
            netsim_count += 1;
            let parent_name = parent.and_then(lookups.name_of);
            netsim_daemons.push(crate::netsim::NetsimDaemonProc {
                pid: sample.pid,
                parent,
                parent_name,
                start_time: sample.start_time,
                exe: sample.exe.clone(),
                cmd: sample
                    .cmd
                    .iter()
                    .map(|s| s.to_string_lossy().into_owned())
                    .collect(),
                environ: (lookups.environ_of)(sample.pid),
                cpu: sample.cpu,
                ram_mb: sample.ram_mb,
                same_user: is_same_user(sample.uid, lookups.self_uid),
            });
            continue;
        }

        if !is_emulator_process(&sample.name, sample.exe.as_deref(), &sample.cmd) {
            continue;
        }

        let cache_key = (sample.pid, sample.start_time);
        let (cmd_port, cmd_avd, no_netsim) = parse_cmdline_with_no_netsim(&sample.cmd);
        let (port, avd_name) = if let Some(cached) = port_cache.get(&cache_key) {
            (cached.0.or(cmd_port), cached.1.clone().or(cmd_avd))
        } else {
            let mut p = cmd_port;
            if p.is_none() {
                p = (lookups.listening_port)(sample.pid);
            }
            (p, cmd_avd)
        };
        let is_worker = is_worker_process(&sample.name, sample.exe.as_deref(), &sample.cmd);
        emu_samples.push((sample, port, avd_name, no_netsim, is_worker));
    }

    // Propagate port, avd_name, and no_netsim between parent and child emulator processes.
    for _ in 0..2 {
        let mut by_pid: std::collections::HashMap<u32, (Option<u16>, Option<String>, bool)> =
            std::collections::HashMap::new();
        for (s, p, avd, nn, _) in &emu_samples {
            by_pid.insert(s.pid, (*p, avd.clone(), *nn));
        }
        for i in 0..emu_samples.len() {
            let parent_pid = emu_samples[i]
                .0
                .parent
                .or_else(|| (lookups.parent_of)(emu_samples[i].0.pid));
            if let Some(pp) = parent_pid {
                if let Some((pp_port, pp_avd, pp_nn)) = by_pid.get(&pp).cloned() {
                    if emu_samples[i].1.is_none() && pp_port.is_some() {
                        emu_samples[i].1 = pp_port;
                    }
                    if emu_samples[i].2.is_none() && pp_avd.is_some() {
                        emu_samples[i].2 = pp_avd;
                    }
                    if pp_nn {
                        emu_samples[i].3 = true;
                    }
                }
            }
        }
        // Also propagate from child to parent (e.g. worker has port via -device, LauncherLoop has avd).
        let snapshot: Vec<(u32, Option<u32>, Option<u16>, Option<String>, bool)> = emu_samples
            .iter()
            .map(|(s, p, a, nn, _)| {
                (
                    s.pid,
                    s.parent.or_else(|| (lookups.parent_of)(s.pid)),
                    *p,
                    a.clone(),
                    *nn,
                )
            })
            .collect();
        for (_, parent_opt, c_port, c_avd, c_nn) in snapshot {
            if let Some(pp) = parent_opt {
                for item in &mut emu_samples {
                    if item.0.pid == pp {
                        if item.1.is_none() && c_port.is_some() {
                            item.1 = c_port;
                        }
                        if item.2.is_none() && c_avd.is_some() {
                            item.2 = c_avd.clone();
                        }
                        if c_nn {
                            item.3 = true;
                        }
                    }
                }
            }
        }
    }

    for (sample, port, avd_name, no_netsim, is_worker) in emu_samples {
        if port.is_some() {
            port_cache.insert((sample.pid, sample.start_time), (port, avd_name.clone()));
        }
        let (threads, context_switches) = (lookups.proc_status)(sample.pid);
        let cpu = sample.cpu;
        let ram = sample.ram_mb;

        let emu = EmulatorProcess {
            pid: sample.pid,
            port,
            avd_name: avd_name.clone(),
            cpu_usage: cpu,
            ram_mb: ram,
            threads,
            context_switches,
            gpu_load: lookups.gpu_by_pid.get(&sample.pid).cloned(),
        };

        if let Some(p) = port {
            pids_per_port.entry(p).or_default().push(sample.pid);
            if no_netsim {
                no_netsim_per_port.insert(p, true);
            }
            if let Some(endpoint) = parse_cmdline_netsim_endpoint(&sample.cmd) {
                netsim_endpoint_per_port.entry(p).or_insert(endpoint);
            }
            let prev_rank = primary_rank_per_port
                .get(&p)
                .copied()
                .unwrap_or((false, -1.0, -1.0, 0));
            let is_new_primary = (is_worker && !prev_rank.0)
                || (is_worker == prev_rank.0
                    && (cpu > prev_rank.1 || (cpu == prev_rank.1 && emu.ram_mb > prev_rank.2)));
            if is_new_primary {
                primary_rank_per_port.insert(p, (is_worker, cpu, emu.ram_mb, sample.start_time));
            }

            accumulated_emus
                .entry(p)
                .and_modify(|e| {
                    e.cpu_usage += emu.cpu_usage;
                    e.ram_mb += emu.ram_mb;
                    e.threads += emu.threads;
                    e.context_switches += emu.context_switches;
                    if is_new_primary {
                        e.pid = emu.pid;
                        if emu.avd_name.is_some() {
                            e.avd_name = emu.avd_name.clone();
                        }
                    } else if e.avd_name.is_none() && emu.avd_name.is_some() {
                        e.avd_name = emu.avd_name.clone();
                    }
                    if let Some(g) = emu.gpu_load {
                        e.gpu_load = Some(e.gpu_load.unwrap_or(0.0) + g);
                    }
                })
                .or_insert_with(|| {
                    primary_rank_per_port.insert(p, (is_worker, cpu, emu.ram_mb, sample.start_time));
                    emu.clone()
                });
        } else {
            unmapped_emus.push(emu);
        }
    }

    let mut emu_groups = std::collections::HashMap::new();
    let mut emulators: Vec<EmulatorProcess> = accumulated_emus
        .into_iter()
        .map(|(p, mut e)| {
            let serial = format!("emulator-{}", p);
            let env = (lookups.environ_of)(e.pid);
            let pid_ini = crate::netsim::discover_emulator_pid_ini(e.pid, &env);
            let avd_id = pid_ini
                .get("avd.id")
                .cloned()
                .or_else(|| e.avd_name.clone());
            if e.avd_name.is_none() && avd_id.is_some() {
                e.avd_name = avd_id.clone();
            }
            // Only avd.name: consumers fall back to avd_id themselves and must
            // be able to tell the two apart.
            let display_name = pid_ini.get("avd.name").map(|s| s.replace('_', " "));
            // The emulator's gRPC plugin writes the endpoint as `netsim.endpoint`.
            let grpc_endpoint = netsim_endpoint_per_port
                .remove(&p)
                .or_else(|| pid_ini.get("netsim.endpoint").cloned());
            let primary_start_time = primary_rank_per_port.get(&p).map(|r| r.3).unwrap_or(0);
            emu_groups.insert(
                serial.clone(),
                crate::netsim::EmuGroupNetsimInfo {
                    serial,
                    port: p,
                    primary_pid: e.pid,
                    primary_start_time,
                    pids: pids_per_port.remove(&p).unwrap_or_else(|| vec![e.pid]),
                    avd_id,
                    display_name,
                    grpc_endpoint,
                    no_netsim: no_netsim_per_port.get(&p).copied().unwrap_or(false),
                },
            );
            e
        })
        .collect();

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


pub struct RealHostProvider {
    pub sys: System,
    pub last_pgin: u64,
    pub last_pgout: u64,
    pub last_disk_read: u64,
    pub last_disk_write: u64,
    pub intel_gpu_failed: bool,
    pub mac_gpu_failed: bool,
    pub nvidia_smi_failed: bool,
    pub pid_port_cache: std::collections::HashMap<(u32, u64), (Option<u16>, Option<String>)>,
    pub is_first_run: bool,
    pub mac_gpu_cache: std::sync::Arc<std::sync::Mutex<Option<f32>>>,
    pub mac_gpu_fetching: std::sync::Arc<std::sync::atomic::AtomicBool>,
    pub mac_gpu_last_fetch: Option<std::time::Instant>,
}


impl crate::provider::HostProvider for RealHostProvider {
    fn get_cpu_cores(&self) -> usize {
        self.sys.cpus().len()
    }

    fn get_host_stats(&mut self, elapsed_secs: f64) -> HostStats {
        let sys = &mut self.sys;

        if self.is_first_run {
            sys.refresh_cpu_all();
            sys.refresh_memory();
            std::thread::sleep(std::time::Duration::from_millis(200));
            self.is_first_run = false;
        }

        sys.refresh_cpu_all();
        sys.refresh_memory();

        sys.refresh_processes_specifics(
            sysinfo::ProcessesToUpdate::All,
            true,
            sysinfo::ProcessRefreshKind::nothing()
                .with_cpu()
                .with_memory()
                .with_cmd(sysinfo::UpdateKind::Always)
                .with_exe(sysinfo::UpdateKind::Always)
                // Owners decide whose netsimd artifacts and CLI are trusted.
                .with_user(sysinfo::UpdateKind::OnlyIfNotSet)
                // Linux reads the few environments it needs from /proc.
                .with_environ(if cfg!(target_os = "linux") {
                    sysinfo::UpdateKind::Never
                } else {
                    sysinfo::UpdateKind::OnlyIfNotSet
                }),
        );

    let current_keys: std::collections::HashSet<(u32, u64)> = sys.processes().iter().map(|(p, proc)| (p.as_u32(), proc.start_time())).collect();
    self.pid_port_cache.retain(|k, _| current_keys.contains(k));

    let cpu_usage = sys.global_cpu_usage();

    let total_mem = sys.total_memory();
    let used_mem = sys.used_memory();

    let ram_used_gb = used_mem as f64 / (1024.0 * 1024.0 * 1024.0);
    let ram_total_gb = total_mem as f64 / (1024.0 * 1024.0 * 1024.0);
    let ram_percent = (used_mem as f32 / total_mem as f32) * 100.0;

    let total_swap = sys.total_swap();
    let used_swap = sys.used_swap();

    let swap_used_gb = used_swap as f64 / (1024.0 * 1024.0 * 1024.0);
    let swap_total_gb = total_swap as f64 / (1024.0 * 1024.0 * 1024.0);
    let swap_percent = if total_swap > 0 { (used_swap as f32 / total_swap as f32) * 100.0 } else { 0.0 };

    #[allow(unused_variables, unused_mut)]
    let mut total_read_bytes = 0;
    #[allow(unused_variables, unused_mut)]
    let mut total_write_bytes = 0;

    let mut emu_gpu_stats = std::collections::HashMap::new();
    if !self.nvidia_smi_failed {
        if let Ok(output) = std::process::Command::new("nvidia-smi")
            .args(&["pmon", "-c", "1"])
            .output()
        {
            if output.status.success() {
                let stdout = String::from_utf8_lossy(&output.stdout);
                for line in stdout.lines() {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() >= 4 {
                        if let Ok(pid) = parts[1].parse::<u32>() {
                            let sm_str = parts[3];
                            let sm_val = if sm_str != "-" && sm_str != "?" {
                                sm_str.parse::<f32>().unwrap_or(0.0)
                            } else {
                                0.0
                            };
                            *emu_gpu_stats.entry(pid).or_insert(0.0) += sm_val;
                        }
                    }
                }
            } else {
                self.nvidia_smi_failed = true;
            }
        } else {
            self.nvidia_smi_failed = true;
        }
    }

    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    for process in sys.processes().values() {
        let usage = process.disk_usage();
        total_read_bytes += usage.read_bytes;
        total_write_bytes += usage.written_bytes;
    }

    let parents: std::collections::HashMap<u32, u32> = sys
        .processes()
        .iter()
        .filter(|(_, p)| p.thread_kind().is_none())
        .filter_map(|(pid, p)| p.parent().map(|pp| (pid.as_u32(), pp.as_u32())))
        .collect();
    let samples: Vec<ProcSample> = sys
        .processes()
        .iter()
        .filter(|(_, p)| {
            p.thread_kind().is_none()
                && is_candidate_process(&p.name().to_string_lossy(), p.exe(), p.cmd())
        })
        .map(|(pid, p)| ProcSample {
            pid: pid.as_u32(),
            parent: p.parent().map(|pp| pp.as_u32()),
            name: p.name().to_string_lossy().into_owned(),
            exe: p.exe().map(|e| e.to_path_buf()),
            cmd: p.cmd().to_vec(),
            start_time: p.start_time(),
            cpu: p.cpu_usage(),
            ram_mb: p.memory() as f64 / (1024.0 * 1024.0),
            uid: process_uid(p),
        })
        .collect();
    let self_uid = sys.process(sysinfo::Pid::from_u32(std::process::id())).and_then(process_uid);
    let environ_of = |pid: u32| {
        if cfg!(target_os = "linux") {
            crate::netsim::read_proc_environ(pid)
        } else {
            sys.process(sysinfo::Pid::from_u32(pid))
                .map(|p| {
                    crate::netsim::parse_env_entries(
                        p.environ().iter().map(|e| e.to_string_lossy()),
                    )
                })
                .unwrap_or_default()
        }
    };
    let name_of = |pid: u32| {
        sys.process(sysinfo::Pid::from_u32(pid))
            .map(|p| p.name().to_string_lossy().into_owned())
    };
    let lookups = ProcessLookups {
        self_pid: std::process::id(),
        self_uid,
        gpu_by_pid: &emu_gpu_stats,
        parent_of: &|pid| parents.get(&pid).copied(),
        listening_port: &get_listening_port,
        proc_status: &read_proc_status,
        environ_of: &environ_of,
        name_of: &name_of,
    };
    let ProcessAggregate {
        emulators,
        netsim_cpu,
        netsim_ram_mb,
        netsim_count,
        netsim_daemons,
        emu_groups,
    } = aggregate_processes(&samples, &lookups, &mut self.pid_port_cache);

    #[cfg(target_os = "linux")]
    let (disk_read_mbs, disk_write_mbs) = {
        let mut curr_read = 0;
        let mut curr_write = 0;
        if let Ok(diskstats) = std::fs::read_to_string("/proc/diskstats") {
            for line in diskstats.lines() {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 10 {
                    let dev = parts[2];
                    let is_disk = dev.starts_with("sd") && dev.len() == 3 ||
                                  dev.starts_with("nvme") && !dev.contains('p') ||
                                  dev.starts_with("vd") && dev.len() == 3 ||
                                  dev.starts_with("hd") && dev.len() == 3 ||
                                  dev.starts_with("mmcblk") && !dev.contains('p');

                    if is_disk {
                        let sectors_read = parts[5].parse::<u64>().unwrap_or(0);
                        let sectors_written = parts[9].parse::<u64>().unwrap_or(0);
                        curr_read += sectors_read * 512;
                        curr_write += sectors_written * 512;
                    }
                }
            }
        }

        let read_mb = if self.last_disk_read > 0 && elapsed_secs > 0.001 {
            (curr_read.saturating_sub(self.last_disk_read) as f64 / (1024.0 * 1024.0)) / elapsed_secs
        } else {
            0.0
        };
        let write_mb = if self.last_disk_write > 0 && elapsed_secs > 0.001 {
            (curr_write.saturating_sub(self.last_disk_write) as f64 / (1024.0 * 1024.0)) / elapsed_secs
        } else {
            0.0
        };

        self.last_disk_read = curr_read;
        self.last_disk_write = curr_write;

        (read_mb, write_mb)
    };

    #[cfg(target_os = "windows")]
    let (disk_read_mbs, disk_write_mbs) = {
        let mut read_mb = 0.0;
        let mut write_mb = 0.0;
        if let Ok(output) = std::process::Command::new("powershell")
            .args(&["-NoProfile", "-NonInteractive", "-Command", "Get-Counter '\\PhysicalDisk(_Total)\\Disk Read Bytes/sec', '\\PhysicalDisk(_Total)\\Disk Write Bytes/sec' | Select-Object -ExpandProperty CounterSamples | ForEach-Object { $_.CookedValue }"])
            .output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let lines: Vec<&str> = stdout.lines().filter(|l| !l.trim().is_empty()).collect();
            if lines.len() >= 2 {
                read_mb = lines[0].trim().parse::<f64>().unwrap_or(0.0) / (1024.0 * 1024.0);
                write_mb = lines[1].trim().parse::<f64>().unwrap_or(0.0) / (1024.0 * 1024.0);
            }
        }
        (read_mb, write_mb)
    };

    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    let (disk_read_mbs, disk_write_mbs) = {
        let read_mb = if elapsed_secs > 0.001 { (total_read_bytes as f64 / (1024.0 * 1024.0)) / elapsed_secs } else { 0.0 };
        let write_mb = if elapsed_secs > 0.001 { (total_write_bytes as f64 / (1024.0 * 1024.0)) / elapsed_secs } else { 0.0 };
        (read_mb, write_mb)
    };

    let mut gpu_percent = if std::path::Path::new("/sys/class/drm/card0/device/gpu_busy_percent").exists() {
        std::fs::read_to_string("/sys/class/drm/card0/device/gpu_busy_percent")
            .ok()
            .and_then(|s| s.trim().parse::<f32>().ok())
    } else {
        None
    };

    let mut gpu_memory_mb = None;

    if gpu_percent.is_none() && !self.nvidia_smi_failed {
        if let Ok(output) = std::process::Command::new("nvidia-smi")
            .args(&["--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"])
            .output() {
            if output.status.success() {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let (g, m) = parse_nvidia_smi(&stdout);
                gpu_percent = g;
                gpu_memory_mb = m;
            } else {
                self.nvidia_smi_failed = true;
            }
        } else {
            self.nvidia_smi_failed = true;
        }
    }

    if gpu_percent.is_none() && cfg!(target_os = "windows") {
        if let Ok(output) = std::process::Command::new("powershell")
            .args(&["-NoProfile", "-NonInteractive", "-Command", "Get-Counter '\\GPU Engine(*)\\Utilization Percentage' | Select-Object -ExpandProperty CounterSamples | Measure-Object -Property CookedValue -Maximum | Select-Object -ExpandProperty Maximum"])
            .output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            if let Ok(val) = stdout.trim().replace(',', ".").parse::<f32>() {
                gpu_percent = Some(val);
            }
        }
    }

    if gpu_percent.is_none() && cfg!(target_os = "linux") && !self.intel_gpu_failed {
        if let Ok(output) = std::process::Command::new("intel_gpu_top")
            .args(&["-s", "1", "-n", "1"])
            .output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            static RE_INTEL: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
            let re_intel = RE_INTEL.get_or_init(|| regex::Regex::new(r"(?i)Render/3D:\s+(\d+)%").unwrap());
            if let Some(caps) = re_intel.captures(&stdout) {
                if let Ok(val) = caps[1].parse::<f32>() {
                    gpu_percent = Some(val);
                }
            } else {
                self.intel_gpu_failed = true;
            }
        } else {
            self.intel_gpu_failed = true;
        }
    }

    if gpu_percent.is_none() && cfg!(target_os = "macos") && !self.mac_gpu_failed {
        let now = std::time::Instant::now();
        let should_fetch = match self.mac_gpu_last_fetch {
            Some(last) => now.duration_since(last).as_secs() >= 10,
            None => true,
        };

        if should_fetch && !self.mac_gpu_fetching.load(std::sync::atomic::Ordering::SeqCst) {
            self.mac_gpu_fetching.store(true, std::sync::atomic::Ordering::SeqCst);
            self.mac_gpu_last_fetch = Some(now);

            let cache_clone = self.mac_gpu_cache.clone();
            let fetching_clone = self.mac_gpu_fetching.clone();

            std::thread::spawn(move || {
                if let Ok(output) = std::process::Command::new("powermetrics")
                    .args(&["-i", "500", "-n", "1", "--samplers", "gpu_power"])
                    .output() {
                    let stdout = String::from_utf8_lossy(&output.stdout);
                    static RE_MAC: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
                    let re_mac = RE_MAC.get_or_init(|| regex::Regex::new(r"(?i)GPU (?:HW )?active residency:\s+([\d.]+)%").unwrap());
                    if let Some(caps) = re_mac.captures(&stdout) {
                        if let Ok(val) = caps[1].parse::<f32>() {
                            let mut cache = cache_clone.lock().unwrap();
                            *cache = Some(val);
                        }
                    }
                }
                fetching_clone.store(false, std::sync::atomic::Ordering::SeqCst);
            });
        }

        let cache = self.mac_gpu_cache.lock().unwrap();
        gpu_percent = *cache;
    }


    let thermal_temp = {
        let mut max_t: Option<f32> = None;
        if cfg!(target_os = "linux") {
            if let Ok(entries) = std::fs::read_dir("/sys/class/thermal") {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name.starts_with("thermal_zone") {
                        let temp_path = entry.path().join("temp");
                        if let Ok(temp_str) = std::fs::read_to_string(temp_path) {
                            if let Ok(t) = temp_str.trim().parse::<f32>() {
                                let t_c = t / 1000.0;
                                if t_c < 150.0 {
                                    max_t = Some(max_t.map_or(t_c, |curr| curr.max(t_c)));
                                }
                            }
                        }
                    }
                }
            }
        }
        max_t
    };

    let mut pgin = 0;
    let mut pgout = 0;

    if cfg!(target_os = "linux") {
        if let Ok(vmstat) = std::fs::read_to_string("/proc/vmstat") {
            let (i, o) = parse_vmstat(&vmstat);
            pgin = i;
            pgout = o;
        }
    } else if cfg!(target_os = "macos") {
        if let Ok(output) = std::process::Command::new("vm_stat").output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let (i, o) = parse_vm_stat(&stdout);
            pgin = i;
            pgout = o;
        }
    }


    let mut in_rate = 0.0;
    let mut out_rate = 0.0;
    let mut paging_str = "Calculating...".to_string();
    if self.last_pgin > 0 || self.last_pgout > 0 {
        in_rate = (pgin.saturating_sub(self.last_pgin)) as f64 / elapsed_secs;
        out_rate = (pgout.saturating_sub(self.last_pgout)) as f64 / elapsed_secs;
        let unit = if cfg!(target_os = "macos") { "pages/s" } else { "KB/s" };
        paging_str = format!("In: {:.0} / Out: {:.0} ({})", in_rate, out_rate, unit);
    }
    self.last_pgin = pgin;
    self.last_pgout = pgout;

    let mut battery_str = "N/A".to_string();
    let mut battery_percent = None;
    if cfg!(target_os = "linux") {
        if let Ok(entries) = std::fs::read_dir("/sys/class/power_supply") {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.starts_with("BAT") {
                    let cap_path = entry.path().join("capacity");
                    let stat_path = entry.path().join("status");
                    if let (Ok(cap), Ok(stat)) = (std::fs::read_to_string(cap_path), std::fs::read_to_string(stat_path)) {
                        battery_str = format!("{}% ({})", cap.trim(), stat.trim());
                        battery_percent = cap.trim().parse::<f32>().ok();
                        break;
                    }
                }
            }
        }
    } else if cfg!(target_os = "macos") {
        if let Ok(output) = std::process::Command::new("pmset").args(&["-g", "batt"]).output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            if let Some(line) = stdout.lines().nth(1) {
                if let Some(idx) = line.find('%') {
                    let pct = &line[..idx];
                    let pct = pct.split_whitespace().last().unwrap_or("N/A");
                    battery_percent = pct.parse::<f32>().ok();
                    let status = if line.contains("discharging") { "Discharging" } else { "Plugged" };
                    battery_str = format!("{}% ({})", pct, status);
                }
            }
        }
    }

    HostStats {
        cpu_usage,
        ram_used_gb,
        ram_total_gb,
        ram_percent,
        swap_used_gb,
        swap_total_gb,
        swap_percent,
        disk_read_mbs,
        disk_write_mbs,
        emulators,
        netsim_cpu,
        netsim_ram_mb,
        netsim_count,
        netsim_daemons,
        emu_groups,
        gpu_percent,
        gpu_memory_mb,
        thermal_temp,
        paging_str,
        paging_in: in_rate,
        paging_out: out_rate,
        battery_str,
        battery_percent,
    }

    }
}

pub fn get_static_hardware_info() -> String {
    let mut sys = System::new_all();
    sys.refresh_all();

    let os = System::name().unwrap_or_else(|| "Unknown OS".to_string());
    let cpu = sys.cpus().first().map(|c| c.brand()).unwrap_or("Unknown CPU").trim().to_string();
    let ram = sys.total_memory() as f64 / (1024.0 * 1024.0 * 1024.0);

    let mut gpu_str = "N/A".to_string();
    if let Ok(output) = std::process::Command::new("nvidia-smi")
        .args(&["--query-gpu=gpu_name,memory.total", "--format=csv,noheader,nounits"])
        .output() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        if let Some(line) = stdout.lines().next() {
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() >= 2 {
                let name = parts[0].trim();
                let mem = parts[1].trim().parse::<f32>().unwrap_or(0.0) / 1024.0;
                gpu_str = format!("{} ({:.1}GB)", name, mem);
            }
        }
    }

    format!("{} | {} | {:.1} GB RAM | GPU: {}", os, cpu, ram, gpu_str)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    #[test]
    fn test_parse_cmdline() {
        let cmdline = vec![
            OsString::from("emulator"),
            OsString::from("-avd"),
            OsString::from("Pixel_6_API_31"),
            OsString::from("-port"),
            OsString::from("5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
    }

    #[test]
    fn test_parse_cmdline_concatenated_avd() {
        let cmdline = vec![
            OsString::from("emulator"),
            OsString::from("-avdPixel_6_API_31"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, None);
        assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
    }

    #[test]
    fn test_parse_cmdline_concatenated_full() {
        let cmdline = vec![
            OsString::from("emulator-netdelaynone-netspeedfull-avdMedium_Phone_37_Pro-qt-hide-window"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, None);
        assert_eq!(avd, Some("Medium_Phone_37_Pro".to_string()));
    }

    #[test]
    fn test_parse_cmdline_at_avd() {
        let cmdline = vec![
            OsString::from("emulator"),
            OsString::from("@Pixel_6_API_31"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, None);
        assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
    }
    #[test]
    fn test_parse_nvidia_smi() {
        let stdout = "30.0, 1000.0\n";
        let (gpu, mem) = parse_nvidia_smi(stdout);
        assert_eq!(gpu, Some(30.0));
        assert_eq!(mem, Some(1000.0));

        let stdout_invalid = "invalid\n";
        let (gpu, mem) = parse_nvidia_smi(stdout_invalid);
        assert_eq!(gpu, None);
        assert_eq!(mem, None);
    }

    #[test]
    fn test_parse_vmstat() {
        let stdout = "pgpgin 1000\npgpgout 2000\n";
        let (in_val, out_val) = parse_vmstat(stdout);
        assert_eq!(in_val, 1000);
        assert_eq!(out_val, 2000);
    }

    #[test]
    fn test_parse_vm_stat() {
        let stdout = "Pages paged in: 1000.\nPages paged out: 2000.\n";
        let (in_val, out_val) = parse_vm_stat(stdout);
        assert_eq!(in_val, 1000);
        assert_eq!(out_val, 2000);
    }

    #[test]
    fn test_parse_cmdline_qemu_next_device_args() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-device"),
            OsString::from("avdstart,serial_number=5554,avd_name=Pixel_7"),
            OsString::from("-device"),
            OsString::from("virtio-goldfish-adb,host_port=5555"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7".to_string()));
    }

    #[test]
    fn test_parse_cmdline_fishtank() {
        let cmdline = vec![
            OsString::from("fishtank"),
            OsString::from("@Pixel_7"),
            OsString::from("-fishtank"),
            OsString::from("5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7".to_string()));

        let cmdline_no_avd = vec![
            OsString::from("fishtank"),
            OsString::from("-fishtank"),
            OsString::from("5556"),
        ];
        let (port, avd) = parse_cmdline(&cmdline_no_avd);
        assert_eq!(port, Some(5556));
        assert_eq!(avd, None);
    }

    #[test]
    fn test_parse_cmdline_name_flag() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-name"),
            OsString::from("Pixel_Tablet"),
            OsString::from("-device"),
            OsString::from("virtio-goldfish-adb,host_port=5557"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5556));
        assert_eq!(avd, Some("Pixel_Tablet".to_string()));
    }

    #[test]
    fn test_parse_cmdline_name_flag_guest_prefix() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-name"),
            OsString::from("guest=Pixel_7_API_36,debug-threads=on"),
            OsString::from("-port"),
            OsString::from("5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7_API_36".to_string()));
    }

    #[test]
    fn test_parse_cmdline_equal_flags() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-name=guest=Pixel_Tablet,process=qemu"),
            OsString::from("-port=5556"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5556));
        assert_eq!(avd, Some("Pixel_Tablet".to_string()));

        let cmdline_fish = vec![
            OsString::from("fishtank"),
            OsString::from("@Pixel_7"),
            OsString::from("-fishtank=5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline_fish);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7".to_string()));
    }

    #[test]
    fn test_parse_cmdline_quoted_args() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-device"),
            OsString::from("avdstart,serial_number=5554,avd_name=\"Pixel 7\""),
            OsString::from("-device=virtio-goldfish-adb,host_port=5555"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel 7".to_string()));
    }

    #[test]
    fn test_b2_parse_cmdline_avd_with_substring_avd() {
        let cmdline = vec![
            OsString::from("emulator"),
            OsString::from("-avd"),
            OsString::from("my-avd"),
            OsString::from("-ports"),
            OsString::from("5554,5557"),
            OsString::from("-device"),
            OsString::from("virtio-goldfish-adb,host_port=5555"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("my-avd".to_string()));

        let cmdline2 = vec![
            OsString::from("emulator"),
            OsString::from("-avd"),
            OsString::from("my-avd-test"),
        ];
        let (_, avd2) = parse_cmdline(&cmdline2);
        assert_eq!(avd2, Some("my-avd-test".to_string()));
    }

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
    fn test_parse_cmdline_netsim_endpoint() {
        // launch_qemu.cc passes the endpoint as a -device property.
        let worker = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-device"),
            OsString::from("avdstart,serial_number=5554,avd_name=Pixel_7"),
            OsString::from("-device"),
            OsString::from("netsim-connection,id=netsim,grpc_endpoint=localhost:8877"),
        ];
        assert_eq!(parse_cmdline_netsim_endpoint(&worker).as_deref(), Some("localhost:8877"));
        let quoted = vec![
            OsString::from("-device"),
            OsString::from("netsim-connection,grpc_endpoint='[::1]:8877',id=netsim"),
        ];
        assert_eq!(parse_cmdline_netsim_endpoint(&quoted).as_deref(), Some("[::1]:8877"));
        // A -no-netsim worker has no netsim-connection device.
        let no_netsim = vec![OsString::from("qemu-system-x86_64"), OsString::from("-no-netsim")];
        assert_eq!(parse_cmdline_netsim_endpoint(&no_netsim), None);
        assert_eq!(
            parse_cmdline_with_no_netsim(&[
                OsString::from("emulator"),
                OsString::from("-avd"),
                OsString::from("Pixel_7"),
                OsString::from("-no-netsim"),
            ]),
            (None, Some("Pixel_7".to_string()), true)
        );
        // Option values equal to -no-netsim / --no-netsim are consumed by their
        // parent option and must not flip no_netsim.
        assert_eq!(
            parse_cmdline_with_no_netsim(&[
                OsString::from("emulator"),
                OsString::from("-avd"),
                OsString::from("-no-netsim"),
                OsString::from("-port"),
                OsString::from("5554"),
            ]),
            (Some(5554), Some("-no-netsim".to_string()), false)
        );
        assert_eq!(
            parse_cmdline_with_no_netsim(&[
                OsString::from("qemu-system-x86_64"),
                OsString::from("-device"),
                OsString::from("--no-netsim"),
                OsString::from("-port"),
                OsString::from("5554"),
            ]),
            (Some(5554), None, false)
        );
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
}
