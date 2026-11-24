use std::collections::{HashMap, HashSet};
use std::time::Duration;

use super::*;

#[derive(Debug, Clone)]
pub struct LoopbackGrpcConn {
    pub serial: String,
    pub pid: u32,
    pub peer_port: u16,
}

/// Upper bound for one `lsof` / `netstat` run on the sampling path.
const SOCKET_CLI_TIMEOUT: Duration = Duration::from_millis(1500);

pub(super) fn default_socket_cli_runner(program: &str, args: &[&str]) -> Option<String> {
    run_cli_with_timeout(program, args, SOCKET_CLI_TIMEOUT)
}

/// Emulator-group connections to each target port, keyed by port. Off Linux
/// each call runs `lsof` or `netstat` (bounded by `SOCKET_CLI_TIMEOUT`), since
/// sysinfo cannot list sockets.
#[cfg(all(test, target_os = "linux"))]
pub fn collect_loopback_grpc_sockets(
    target_ports: &HashSet<u16>,
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    match HostOs::current() {
        HostOs::Linux => {
            collect_loopback_grpc_sockets_from_proc(target_ports, emu_groups, daemon_pids)
        }
        os => collect_loopback_grpc_sockets_via_cli(
            os,
            target_ports,
            emu_groups,
            daemon_pids,
            &default_socket_cli_runner,
        )
        .unwrap_or_else(|| empty_port_map(target_ports)),
    }
}

pub(super) fn empty_port_map(target_ports: &HashSet<u16>) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    target_ports.iter().map(|&p| (p, Vec::new())).collect()
}

/// Groups `(pid, local_port, remote_port)` rows by target remote port,
/// keeping emulator PIDs only and dropping duplicate rows.
fn group_loopback_conns(
    rows: impl IntoIterator<Item = (u32, u16, u16)>,
    target_ports: &HashSet<u16>,
    pid_to_serial: &HashMap<u32, String>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    let mut port_to_conns = empty_port_map(target_ports);
    let mut seen: HashSet<(u16, u32, u16)> = HashSet::new();
    for (pid, local_port, remote_port) in rows {
        let (Some(serial), Some(conns)) =
            (pid_to_serial.get(&pid), port_to_conns.get_mut(&remote_port))
        else {
            continue;
        };
        if seen.insert((remote_port, pid, local_port)) {
            conns.push(LoopbackGrpcConn { serial: serial.clone(), pid, peer_port: local_port });
        }
    }
    port_to_conns
}

/// `(pid, local_port, remote_port)` of ESTABLISHED (`st == 01`) sockets in
/// `/proc/net/tcp{,6}` content whose inode belongs to a known PID.
fn proc_net_tcp_rows(content: &str) -> Vec<(u16, u16, u64)> {
    let port =
        |addr: &str| u16::from_str_radix(addr.split(':').nth(1)?, 16).ok().filter(|&p| p > 0);
    let is_loopback = |addr: &str| {
        let ip = addr.split(':').next().unwrap_or("");
        (ip.len() == 8 && (ip.ends_with("7F") || ip.ends_with("7f")))
            || ip == "00000000000000000000000001000000"
            || (ip.len() == 32
                && ip[..24].eq_ignore_ascii_case("0000000000000000FFFF0000")
                && (ip.ends_with("7F") || ip.ends_with("7f")))
    };
    content
        .lines()
        .skip(1)
        .filter_map(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 10 || parts[3] != "01" {
                return None;
            }
            if !is_loopback(parts[1]) || !is_loopback(parts[2]) {
                return None;
            }
            let inode = parts[9].parse::<u64>().ok()?;
            Some((port(parts[1])?, port(parts[2])?, inode))
        })
        .collect()
}

pub(super) fn collect_loopback_grpc_sockets_from_proc(
    target_ports: &HashSet<u16>,
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    let pid_to_serial = emulator_pid_to_serial(emu_groups, daemon_pids);
    if target_ports.is_empty() || pid_to_serial.is_empty() {
        return empty_port_map(target_ports);
    }

    let tcp_rows: Vec<(u16, u16, u64)> = ["/proc/net/tcp", "/proc/net/tcp6"]
        .into_iter()
        .filter_map(|f| std::fs::read_to_string(f).ok())
        .flat_map(|content| proc_net_tcp_rows(&content))
        .collect();

    let mut relevant_inodes: HashSet<u64> = HashSet::new();
    for &(lport, rport, inode) in &tcp_rows {
        if target_ports.contains(&lport) || target_ports.contains(&rport) {
            relevant_inodes.insert(inode);
        }
    }

    if relevant_inodes.is_empty() {
        return empty_port_map(target_ports);
    }

    let mut inode_to_pid: HashMap<u64, u32> = HashMap::new();
    for &pid in pid_to_serial.keys() {
        let Ok(entries) = std::fs::read_dir(format!("/proc/{}/fd", pid)) else {
            continue;
        };
        for entry in entries.flatten() {
            let Some(ino) = std::fs::read_link(entry.path())
                .ok()
                .and_then(|link| {
                    link.to_str()
                        .and_then(|s| s.strip_prefix("socket:["))
                        .and_then(|s| s.strip_suffix(']'))
                        .and_then(|s| s.parse::<u64>().ok())
                })
                .filter(|ino| relevant_inodes.contains(ino))
            else {
                continue;
            };
            inode_to_pid.insert(ino, pid);
        }
    }

    let resolved_rows = tcp_rows.into_iter().filter_map(|(lport, rport, inode)| {
        inode_to_pid.get(&inode).map(|&pid| (pid, lport, rport))
    });

    group_loopback_conns(resolved_rows, target_ports, &pid_to_serial)
}

/// Returns the port of a `host:port` / `[host]:port` endpoint if its host is
/// loopback.
fn loopback_endpoint_port(ep: &str) -> Option<u16> {
    let (host, port) = ep.rsplit_once(':')?;
    let host = host.trim_matches(|c| c == '[' || c == ']').to_ascii_lowercase();
    let loopback = matches!(host.as_str(), "::1" | "::ffff:127.0.0.1" | "localhost")
        || host.starts_with("127.");
    if loopback { port.parse::<u16>().ok().filter(|&p| p > 0) } else { None }
}

/// `(pid, local_port, remote_port)` of loopback connections in `lsof -F pn`
/// output (`p<pid>` / `n<src>-><dst>` lines). lsof itself applies the
/// ESTABLISHED filter (`-sTCP:ESTABLISHED`).
fn lsof_loopback_rows(lsof_out: &str) -> Vec<(u32, u16, u16)> {
    let mut rows = Vec::new();
    let mut current_pid: Option<u32> = None;
    for line in lsof_out.lines().map(str::trim) {
        if let Some(rest) = line.strip_prefix('p') {
            current_pid = rest.parse::<u32>().ok();
        } else if let Some(row) =
            line.strip_prefix('n').and_then(|ep| parse_lsof_endpoint(current_pid, ep))
        {
            rows.push(row);
        }
    }
    rows
}

fn parse_lsof_endpoint(pid: Option<u32>, endpoint: &str) -> Option<(u32, u16, u16)> {
    let pid = pid?;
    let (src, dst) = endpoint.split_once("->")?;
    Some((pid, loopback_endpoint_port(src)?, loopback_endpoint_port(dst)?))
}

/// `(pid, local_port, remote_port)` of ESTABLISHED loopback TCP rows in
/// Windows `netstat -ano` output (`TCP <local> <foreign> <state> <pid>`).
/// netstat localizes the State column, so non-English Windows yields no rows.
fn netstat_loopback_rows(netstat_out: &str) -> Vec<(u32, u16, u16)> {
    netstat_out
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            let [proto, local, foreign, state, pid, ..] = parts[..] else {
                return None;
            };
            if !proto.eq_ignore_ascii_case("TCP") || !state.eq_ignore_ascii_case("ESTABLISHED") {
                return None;
            }
            Some((
                pid.parse().ok()?,
                loopback_endpoint_port(local)?,
                loopback_endpoint_port(foreign)?,
            ))
        })
        .collect()
}

pub fn parse_lsof_established_loopback(
    lsof_out: &str,
    target_ports: &HashSet<u16>,
    pid_to_serial: &HashMap<u32, String>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    group_loopback_conns(lsof_loopback_rows(lsof_out), target_ports, pid_to_serial)
}

pub fn parse_netstat_established_loopback(
    netstat_out: &str,
    target_ports: &HashSet<u16>,
    pid_to_serial: &HashMap<u32, String>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    group_loopback_conns(netstat_loopback_rows(netstat_out), target_ports, pid_to_serial)
}

pub(super) fn emulator_pid_to_serial(
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
) -> HashMap<u32, String> {
    let mut pid_to_serial = HashMap::new();
    for (serial, g) in emu_groups {
        for &pid in &g.pids {
            if !daemon_pids.contains(&pid) {
                pid_to_serial.insert(pid, serial.clone());
            }
        }
    }
    pid_to_serial
}

/// Socket discovery for hosts without `/proc`: `lsof` on macOS, `netstat` on
/// Windows. `run(program, args)` returns the tool's stdout, or `None` when the
/// tool failed to start or timed out.
pub(super) fn collect_loopback_grpc_sockets_via_cli(
    os: HostOs,
    target_ports: &HashSet<u16>,
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
    run: &dyn Fn(&str, &[&str]) -> Option<String>,
) -> Option<HashMap<u16, Vec<LoopbackGrpcConn>>> {
    let pid_to_serial = emulator_pid_to_serial(emu_groups, daemon_pids);
    if target_ports.is_empty() || pid_to_serial.is_empty() {
        return Some(empty_port_map(target_ports));
    }
    match os {
        // No `-p TCP`: it lists IPv4 only, and emulators reach netsimd over [::1].
        HostOs::Windows => run("netstat", &["-ano"])
            .map(|out| parse_netstat_established_loopback(&out, target_ports, &pid_to_serial)),
        HostOs::MacOs => {
            let mut pids: Vec<u32> = pid_to_serial.keys().copied().collect();
            pids.sort_unstable();
            let pids = pids.iter().map(u32::to_string).collect::<Vec<_>>().join(",");
            run("lsof", &["-n", "-P", "-F", "pn", "-iTCP", "-sTCP:ESTABLISHED", "-a", "-p", &pids])
                .map(|out| parse_lsof_established_loopback(&out, target_ports, &pid_to_serial))
        }
        // Linux reads /proc instead (collect_loopback_grpc_sockets_from_proc).
        HostOs::Linux => None,
    }
}

#[cfg(test)]
#[path = "../../tests/netsim/sockets_test.rs"]
pub(super) mod tests;
