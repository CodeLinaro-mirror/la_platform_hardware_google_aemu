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
    let res = parse_lsof_established_loopback(LSOF_OUT, &HashSet::from([43929]), &emulator_5554());
    assert_eq!(res.len(), 1);
    assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
}

#[test]
fn test_parse_netstat_established_loopback() {
    let res =
        parse_netstat_established_loopback(NETSTAT_OUT, &HashSet::from([43929]), &emulator_5554());
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
    assert_eq!(calls.into_inner(), ["lsof -n -P -F pn -iTCP -sTCP:ESTABLISHED -a -p 4001,4002"]);
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
    let res = collect_loopback_grpc_sockets(&ports, &emu_groups_with(&[me]), &HashSet::from([me]));
    assert!(res[&port].is_empty());
}

#[test]
fn test_zero_port_endpoints_rejected_across_socket_parsers() {
    assert_eq!(loopback_endpoint_port("127.0.0.1:0"), None);
    assert_eq!(loopback_endpoint_port("[::1]:0"), None);

    let lsof = "p4001\nf10\nn127.0.0.1:0->127.0.0.1:43929\n";
    let lsof_res = parse_lsof_established_loopback(lsof, &HashSet::from([43929]), &emulator_5554());
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
