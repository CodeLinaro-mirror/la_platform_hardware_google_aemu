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
pub(super) mod tests {
    use super::*;

    #[test]
    fn test_loopback_endpoint_port() {
        assert_eq!(loopback_endpoint_port("127.0.0.1:43929"), Some(43929));
        assert_eq!(loopback_endpoint_port("127.8.9.10:1"), Some(1));
        assert_eq!(loopback_endpoint_port("[::1]:43929"), Some(43929));
        assert_eq!(loopback_endpoint_port("[::ffff:127.0.0.1]:9"), Some(9));
        assert_eq!(loopback_endpoint_port("LOCALHOST:80"), Some(80));
        for ep in [
            "10.0.0.5:43929",
            "0.0.0.0:43929",
            "[::]:0",
            "*:*",
            "127.0.0.1:x",
            "127.0.0.1:70000",
            "127.0.0.1",
        ] {
            assert_eq!(loopback_endpoint_port(ep), None, "{ep}");
        }
    }

    fn emulator_5554() -> HashMap<u32, String> {
        HashMap::from([(4001, "emulator-5554".to_string())])
    }

    pub(crate) fn emu_groups_with(pids: &[u32]) -> HashMap<String, EmuGroupNetsimInfo> {
        let group = EmuGroupNetsimInfo {
            serial: "emulator-5554".to_string(),
            pids: pids.to_vec(),
            ..Default::default()
        };
        HashMap::from([("emulator-5554".to_string(), group)])
    }

    fn conn_summary(conns: &[LoopbackGrpcConn]) -> Vec<(&str, u32, u16)> {
        conns.iter().map(|c| (c.serial.as_str(), c.pid, c.peer_port)).collect()
    }

    /// Connections from emulator PID 4001 to 43929 in `LSOF_OUT` and
    /// `NETSTAT_OUT`.
    const EXPECTED_CONNS: [(&str, u32, u16); 2] =
        [("emulator-5554", 4001, 59604), ("emulator-5554", 4001, 59605)];

    /// `lsof -n -P -F pn` output: `p` starts a process, `f` a file, `n` names
    /// it. Besides the expected rows it has a non-loopback peer, a duplicate,
    /// a non-target port, a listener and a PID outside the emulator groups.
    pub(crate) const LSOF_OUT: &str = concat!(
        "p4001\n",
        "f42\nn127.0.0.1:59604->127.0.0.1:43929\n",
        "f43\nn[::1]:59605->[::1]:43929\n",
        "f44\nn10.0.0.5:59606->10.0.0.9:43929\n",
        "f45\nn127.0.0.1:59604->127.0.0.1:43929\n",
        "f46\nn127.0.0.1:59607->127.0.0.1:8554\n",
        "f47\nn127.0.0.1:43929\n",
        "p9999\n",
        "f3\nn127.0.0.1:60000->127.0.0.1:43929\n",
    );

    /// `netstat -ano` output with the same kinds of noise as `LSOF_OUT`, plus
    /// a TIME_WAIT row and a UDP row (whose State column is blank).
    const NETSTAT_OUT: &str = concat!(
        "\nActive Connections\n\n",
        "  Proto  Local Address          Foreign Address        State           PID\n",
        "  TCP    127.0.0.1:59604        127.0.0.1:43929        ESTABLISHED     4001\n",
        "  TCP    [::1]:59605            [::1]:43929            ESTABLISHED     4001\n",
        "  TCP    127.0.0.1:59604        127.0.0.1:43929        ESTABLISHED     4001\n",
        "  TCP    192.168.1.5:59606      192.168.1.9:43929      ESTABLISHED     4001\n",
        "  TCP    127.0.0.1:59607        127.0.0.1:8554         ESTABLISHED     4001\n",
        "  TCP    127.0.0.1:60000        127.0.0.1:43929        TIME_WAIT       4001\n",
        "  TCP    127.0.0.1:60001        127.0.0.1:43929        ESTABLISHED     9999\n",
        "  TCP    127.0.0.1:43929        0.0.0.0:0              LISTENING       7000\n",
        "  UDP    127.0.0.1:43929        *:*                                    4001\n",
    );

    #[test]
    fn test_parse_lsof_established_loopback() {
        let res =
            parse_lsof_established_loopback(LSOF_OUT, &HashSet::from([43929]), &emulator_5554());
        assert_eq!(res.len(), 1);
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_parse_netstat_established_loopback() {
        let res = parse_netstat_established_loopback(
            NETSTAT_OUT,
            &HashSet::from([43929]),
            &emulator_5554(),
        );
        assert_eq!(res.len(), 1);
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_proc_net_tcp_rows() {
        // Ports are hex (0xE8D4 = 59604, 0xAB99 = 43929). Only ESTABLISHED
        // (st 01) rows whose inode belongs to a known PID are returned.
        let tcp = concat!(
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n",
            "   0: 0100007F:E8D4 0100007F:AB99 01 00000000:00000000 00:00000000 00000000 1000 0 111\n",
            "   1: 0100007F:E8D5 0100007F:AB99 06 00000000:00000000 03:00000000 00000000    0 0 0\n",
            "   2: 0100007F:E8D6 0100007F:AB99 01 00000000:00000000 00:00000000 00000000 1000 0 999\n",
            "   3: 0100007F:E8D8 0500000A:AB99 01 00000000:00000000 00:00000000 00000000 1000 0 444\n",
        );
        let tcp6 = concat!(
            "  sl  local_address                         remote_address                        st\n",
            "   0: 00000000000000000000000001000000:E8D7 00000000000000000000000001000000:AB99 01 ",
            "00000000:00000000 00:00000000 00000000 1000 0 222\n",
            "   1: 0000000000000000FFFF00000100007F:E8D9 0000000000000000FFFF00000100007F:AB99 01 ",
            "00000000:00000000 00:00000000 00000000 1000 0 333\n",
        );
        assert_eq!(proc_net_tcp_rows(tcp), [(59604, 43929, 111), (59606, 43929, 999)]);
        assert_eq!(proc_net_tcp_rows(tcp6), [(59607, 43929, 222), (59609, 43929, 333)]);
    }

    #[test]
    fn test_collect_loopback_grpc_sockets_via_cli_macos_runs_lsof() {
        // netsimd (7000) is left out of the lsof PID list, which is sorted.
        let calls = std::cell::RefCell::new(Vec::new());
        let res = collect_loopback_grpc_sockets_via_cli(
            HostOs::MacOs,
            &HashSet::from([43929]),
            &emu_groups_with(&[4002, 7000, 4001]),
            &HashSet::from([7000]),
            &|program: &str, args: &[&str]| {
                calls.borrow_mut().push(format!("{program} {}", args.join(" ")));
                Some(LSOF_OUT.to_string())
            },
        )
        .unwrap();
        assert_eq!(
            calls.into_inner(),
            ["lsof -n -P -F pn -iTCP -sTCP:ESTABLISHED -a -p 4001,4002"]
        );
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_collect_loopback_grpc_sockets_via_cli_windows_runs_netstat() {
        let calls = std::cell::RefCell::new(Vec::new());
        let res = collect_loopback_grpc_sockets_via_cli(
            HostOs::Windows,
            &HashSet::from([43929]),
            &emu_groups_with(&[4001]),
            &HashSet::new(),
            &|program: &str, args: &[&str]| {
                calls.borrow_mut().push(format!("{program} {}", args.join(" ")));
                Some(NETSTAT_OUT.to_string())
            },
        )
        .unwrap();
        assert_eq!(calls.into_inner(), ["netstat -ano"]);
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_collect_loopback_grpc_sockets_via_cli_degrades_to_empty() {
        let ports = HashSet::from([43929, 43930]);
        // A tool that fails or times out returns None so callers can keep
        // last-known connections instead of flipping CONNECTED to DISCONNECTED.
        for os in [HostOs::MacOs, HostOs::Windows] {
            let res = collect_loopback_grpc_sockets_via_cli(
                os,
                &ports,
                &emu_groups_with(&[4001]),
                &HashSet::new(),
                &|_: &str, _: &[&str]| None,
            );
            assert!(res.is_none());
        }
        // With no emulator PID to attribute connections to, no tool runs.
        let res = collect_loopback_grpc_sockets_via_cli(
            HostOs::MacOs,
            &ports,
            &emu_groups_with(&[7000]),
            &HashSet::from([7000]),
            &|_: &str, _: &[&str]| panic!("no tool should run"),
        )
        .unwrap();
        assert_eq!(res.len(), 2);
        assert!(res.values().all(Vec::is_empty));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_collect_loopback_grpc_sockets_finds_own_connection_via_proc() {
        // A real loopback connection owned by this process, found through
        // /proc/self/fd and /proc/net/tcp.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let client = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        let client_port = client.local_addr().unwrap().port();
        let me = std::process::id();
        let ports = HashSet::from([port]);
        let res = collect_loopback_grpc_sockets(&ports, &emu_groups_with(&[me]), &HashSet::new());
        assert_eq!(conn_summary(&res[&port]), [("emulator-5554", me, client_port)]);
        // A daemon PID is never attributed to an emulator.
        let res =
            collect_loopback_grpc_sockets(&ports, &emu_groups_with(&[me]), &HashSet::from([me]));
        assert!(res[&port].is_empty());
    }

    #[test]
    fn test_zero_port_endpoints_rejected_across_socket_parsers() {
        assert_eq!(loopback_endpoint_port("127.0.0.1:0"), None);
        assert_eq!(loopback_endpoint_port("[::1]:0"), None);

        let lsof = "p4001\nf10\nn127.0.0.1:0->127.0.0.1:43929\n";
        let lsof_res =
            parse_lsof_established_loopback(lsof, &HashSet::from([43929]), &emulator_5554());
        assert!(lsof_res[&43929].is_empty());

        let netstat = "  TCP    127.0.0.1:0    127.0.0.1:43929    ESTABLISHED     4001\n";
        let netstat_res =
            parse_netstat_established_loopback(netstat, &HashSet::from([43929]), &emulator_5554());
        assert!(netstat_res[&43929].is_empty());

        let tcp_zero = concat!(
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n",
            "   0: 0100007F:0000 0100007F:AB99 01 00000000:00000000 00:00000000 00000000 1000 0 111\n",
            "   1: 0100007F:E8D4 0100007F:0000 01 00000000:00000000 00:00000000 00000000 1000 0 222\n",
        );
        assert!(proc_net_tcp_rows(tcp_zero).is_empty());
    }
}
