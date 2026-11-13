use super::*;

#[test]
fn test_log_tailer_1024_and_1025_and_truncation() {
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    let lines_1024 = concat!(
        "netsim I 09-25 22:37:05.612 netsimd.rs:446 - netsim artifacts path: \"/tmp/erm_qn/android-user/netsimd\"\n",
        "Args {\n",
        "    no_cli_ui: true,\n",
        "    pcap: false,\n",
        "}\n",
        "netsim I 09-25 22:37:05.612 netsimd.rs:451 - Netsim Version: 1.0.24, OS: linux, Arch: x86_64\n",
        "netsim I 09-25 22:37:05.683 netsimd.rs:744 - Wrote to INI file /tmp/erm_qn/netsim.ini\n",
        "netsim I 09-25 22:37:06.552 netsimd.rs:106 - Handling new connection for \"\", device ERM QN Phone 37\n",
        "netsim I 09-25 22:37:06.552 service.rs:671 - DeviceActor: AddChipByGuid for device ipv6:%5B::1%5D:59604\n",
        "netsim I 09-25 22:37:06.552 service.rs:635 - DeviceActor: Create device ERM QN Phone 37\n",
        "netsim I 09-25 22:37:06.552 service.rs:579 - DeviceActor: AddChip ERM QN Phone 37 (EMULATOR, ERM QN Phone 37) to device ERM QN Phone 37\n",
        "netsim I 09-25 22:37:41.889 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
    );
    std::fs::write(&log_path, lines_1024).unwrap();
    let mut tailer = NetsimLogTailer::new(log_path.clone());
    assert!(tailer.poll(1790375830.0));
    assert_eq!(tailer.version.as_deref(), Some("1.0.24"));
    assert!(tailer.frontend_disabled);
    assert!(tailer.ready_ts.is_some());
    assert_eq!(tailer.chip_adds.len(), 1);
    assert_eq!(tailer.chip_adds[0].kind, "?");
    assert_eq!(tailer.chip_adds[0].peer_port, Some(59604));
    assert_eq!(tailer.wifi_associations.len(), 1);

    let lines_1025 = concat!(
        "netsim I 09-25 22:38:00.100 netsimd.rs:456 - Netsim Version: 1.0.25, OS: linux, Arch: x86_64\n",
        "netsim I 09-25 22:38:01.000 service.rs:585 - DeviceActor: AddChip 3 (kind WIFI, name \"wlan0\") to device 1 (Pixel 8)\n",
    );
    std::fs::write(&log_path, lines_1025).unwrap();
    assert!(tailer.poll(1790375900.0));
    assert_eq!(tailer.truncation_count, 1);
    assert_eq!(tailer.version.as_deref(), Some("1.0.25"));
    assert_eq!(tailer.chip_adds.len(), 1);
    assert_eq!(tailer.chip_adds[0].chip_id.as_deref(), Some("3"));
    assert_eq!(tailer.chip_adds[0].kind, "WIFI");
    assert_eq!(tailer.chip_adds[0].device_name, "Pixel 8");
}

#[test]
fn test_log_tailer_partial_line() {
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    std::fs::write(&log_path, "line 1\npartial").unwrap();

    let mut tailer = NetsimLogTailer::new(log_path.clone());
    assert!(tailer.poll(1.0));
    assert_eq!(tailer.offset, 7);

    std::fs::write(&log_path, "line 1\npartial line 2\n").unwrap();
    assert!(tailer.poll(2.0));
    assert_eq!(tailer.offset, 22);
}

#[test]
fn test_log_tailer_holds_back_line_with_bare_carriage_return() {
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    std::fs::write(&log_path, "line 1\npart a\rpart b").unwrap();
    let mut tailer = NetsimLogTailer::new(log_path.clone());
    assert!(tailer.poll(1.0));
    // Only a newline ends a line; str::lines() does not split on a bare CR.
    assert_eq!(tailer.offset, 7);
}

#[test]
#[cfg(unix)]
fn test_log_tailer_inode_change() {
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    std::fs::write(&log_path, "line 1\n").unwrap();

    let mut tailer = NetsimLogTailer::new(log_path.clone());
    assert!(tailer.poll(1.0));

    std::fs::remove_file(&log_path).unwrap();
    std::fs::write(&log_path, "line 1\nline 2\n").unwrap();

    assert!(tailer.poll(2.0));
    assert_eq!(tailer.truncation_count, 1);
    assert_eq!(tailer.offset, 14); // 7 + 7 bytes
}

#[test]
fn test_resolve_session_stats_chips_exact_and_approx() {
    let dir = tempfile::tempdir().unwrap();
    let stats_path = dir.path().join("netsim_session_stats.json");
    let payload = serde_json::json!({
        "duration_secs": "130",
        "device_stats": [{"device_id": 1, "kind": "EMULATOR"}],
        "radio_stats": [
            {"device_id": 1, "kind": "WIFI", "duration_secs": "129"},
            {"device_id": 1, "kind": "UWB", "duration_secs": "129"},
            {"device_id": 1, "kind": "BLUETOOTH_LOW_ENERGY", "duration_secs": "118"},
            {"device_id": 1, "kind": "BLUETOOTH_CLASSIC", "duration_secs": "117"}
        ]
    });
    std::fs::write(&stats_path, payload.to_string()).unwrap();
    let mtime = std::fs::metadata(&stats_path)
        .unwrap()
        .modified()
        .unwrap()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let unk = vec![mtime - 129.1, mtime - 128.9];
    let mut cache = None;
    let (num_devs, resolved) =
        resolve_session_stats_chips(&stats_path, &unk, 1, mtime - 130.0, &mut cache);
    assert_eq!(num_devs, 1);
    let res = resolved.unwrap();
    assert!(!res["WIFI"].1);
    assert!(!res["UWB"].1);
    assert!(res["BT"].1);

    // The same file, seen by a daemon that started after it was written,
    // was left by an earlier session.
    let mut cache = None;
    let (num_devs, resolved) =
        resolve_session_stats_chips(&stats_path, &unk, 1, mtime + 1.0, &mut cache);
    assert_eq!((num_devs, resolved), (0, None));
    assert!(cache.is_none());
}

#[test]
fn test_resolve_session_stats_chips_rematches_new_timestamps_on_cached_file() {
    // The cache holds the parsed file, not the answer: chip-add timestamps
    // that arrive later are still matched although the file did not change.
    let dir = tempfile::tempdir().unwrap();
    let stats_path = dir.path().join("netsim_session_stats.json");
    let payload = serde_json::json!({
        "duration_secs": "130",
        "device_stats": [{"device_id": 1, "kind": "EMULATOR"}],
        "radio_stats": [
            {"device_id": 1, "kind": "WIFI", "duration_secs": "129"},
            {"device_id": 1, "kind": "BLUETOOTH_LOW_ENERGY", "duration_secs": "118"}
        ]
    });
    std::fs::write(&stats_path, payload.to_string()).unwrap();
    let mtime = std::fs::metadata(&stats_path)
        .unwrap()
        .modified()
        .unwrap()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let late = [mtime - 129.1];
    let mut cache = None;
    let (_, before) = resolve_session_stats_chips(&stats_path, &[], 1, mtime - 130.0, &mut cache);
    let (_, after) = resolve_session_stats_chips(&stats_path, &late, 1, mtime - 130.0, &mut cache);
    let (_, uncached) =
        resolve_session_stats_chips(&stats_path, &late, 1, mtime - 130.0, &mut None);
    assert_ne!(before, uncached);
    assert_eq!(after, uncached);
}

#[test]
fn test_log_tailer_bounds_accumulated_event_lists() {
    // Verifies NetsimLogTailer caps accumulated event/connection/chip lists
    // to MAX_TAILER_EVENTS while retaining the most recent entries.
    let mut tailer = NetsimLogTailer::new(PathBuf::new());
    let limit = MAX_TAILER_EVENTS;
    for i in 0..(limit + 25) {
        let now = 1000.0 + i as f64;
        tailer.feed_line(
            &format!("I0330 00:00:01.000Z src/lib.rs:1] Handling new connection for BLUETOOTH on device dev-{i}"),
            now,
        );
        tailer.feed_line(
            &format!("I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Create device dev-{i}"),
            now,
        );
        tailer.feed_line(
            &format!(r#"I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: AddChip {i} (kind BLUETOOTH, name "bt-{i}") to device {i} (dev-{i})"#),
            now,
        );
        tailer.feed_line(
            &format!("I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Removed chip {i} (kind BLUETOOTH) from device {i}"),
            now,
        );
        tailer.feed_line(
            &format!("I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Device dev-{i} is empty, auto-deleting"),
            now,
        );
        tailer
            .feed_line("I0330 00:00:01.000Z src/lib.rs:1] Wrote to INI file /tmp/netsim.ini", now);
    }

    let last_name = format!("dev-{}", limit + 24);
    assert_eq!(tailer.connections.len(), limit);
    assert_eq!(tailer.connections.last().unwrap().2, last_name);
    assert_eq!(tailer.created_devices.len(), limit);
    assert_eq!(tailer.created_devices.last().unwrap().1, last_name);
    assert_eq!(tailer.chip_adds.len(), limit);
    assert_eq!(tailer.chip_adds.last().unwrap().device_name, last_name);
    assert_eq!(tailer.empty_devices.len(), limit);
    assert_eq!(tailer.empty_devices.last().unwrap().1, last_name);
    assert_eq!(tailer.events.len(), limit);
}

#[test]
fn test_resolve_session_stats_chips_skips_unspecified_radio_entries() {
    let dir = tempfile::tempdir().unwrap();
    let stats_path = dir.path().join("netsim_session_stats.json");
    let payload = serde_json::json!({
        "device_stats": [{"device_id": 1, "kind": "EMULATOR"}],
        "radio_stats": [
            {"device_id": 1, "kind": "UNSPECIFIED", "duration_secs": "129"},
            {"device_id": 1, "kind": "?", "duration_secs": "129"},
            {"device_id": 1, "kind": "", "duration_secs": "129"},
            {"device_id": 1, "kind": "WIFI", "duration_secs": "129"}
        ]
    });
    std::fs::write(&stats_path, payload.to_string()).unwrap();
    std::fs::File::options()
        .write(true)
        .open(&stats_path)
        .unwrap()
        .set_modified(UNIX_EPOCH + std::time::Duration::from_secs(2000))
        .unwrap();
    let mut cache = None;
    let (_, resolved) = resolve_session_stats_chips(&stats_path, &[1871.42], 1, 1800.0, &mut cache);
    let resolved = resolved.unwrap();
    assert_eq!(resolved.len(), 1);
    assert_eq!(resolved.get("WIFI"), Some(&(1871.42, false)));
}

#[test]
fn test_log_tailer_detects_inplace_truncation_longer_or_equal_to_offset() {
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    let run1 = "netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 43929\n";
    std::fs::write(&log_path, run1).unwrap();

    let mut tailer = NetsimLogTailer::new(log_path.clone());
    assert!(tailer.poll(100.0));
    assert_eq!(tailer.grpc_port_logged, Some(43929));

    // In-place O_TRUNC rewrite longer than run1 (preserving inode):
    // freeze_on_truncation = true freezes without reading run2's port.
    let mut frozen_tailer = tailer.clone();
    let run2 = concat!(
        "netsim I 09-25 22:37:10.100 netsimd.rs:451 - Netsim Version: 1.0.31, OS: linux\n",
        "netsim I 09-25 22:37:10.200 netsimd.rs:750 - gRPC port: 43930\n",
    );
    assert!(run2.len() > run1.len());
    std::fs::write(&log_path, run2).unwrap();
    assert!(frozen_tailer.poll_with_truncation_policy(110.0, true));
    assert!(frozen_tailer.frozen);
    assert_eq!(frozen_tailer.grpc_port_logged, Some(43929));

    // freeze_on_truncation = false resets from byte 0 so Version on line 1 is parsed.
    assert!(tailer.poll_with_truncation_policy(110.0, false));
    assert_eq!(tailer.truncation_count, 1);
    assert_eq!(tailer.version.as_deref(), Some("1.0.31"));
    assert_eq!(tailer.grpc_port_logged, Some(43930));

    // Equal-length in-place rewrite is also detected when mtime changes.
    let run3 = concat!(
        "netsim I 09-25 22:37:20.100 netsimd.rs:451 - Netsim Version: 1.0.32, OS: linux\n",
        "netsim I 09-25 22:37:20.200 netsimd.rs:750 - gRPC port: 43931\n",
    );
    assert_eq!(run3.len(), run2.len());
    std::fs::write(&log_path, run3).unwrap();
    std::fs::File::options()
        .write(true)
        .open(&log_path)
        .unwrap()
        .set_modified(std::time::SystemTime::now() + std::time::Duration::from_secs(5))
        .unwrap();
    assert!(tailer.poll_with_truncation_policy(120.0, false));
    assert_eq!(tailer.truncation_count, 2);
    assert_eq!(tailer.version.as_deref(), Some("1.0.32"));
    assert_eq!(tailer.grpc_port_logged, Some(43931));
}

#[test]
fn test_log_tailer_extends_short_head_prefix_and_catches_later_truncation() {
    use std::io::Write;
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    let banner = "netsim I 09-25 22:37:05.100 netsimd.rs:451 - OS: linux\n";
    assert!(banner.len() < HEAD_PREFIX_MAX_BYTES);
    std::fs::write(&log_path, banner).unwrap();

    let mut tailer = NetsimLogTailer::new(log_path.clone());
    assert!(tailer.poll(100.0));
    assert_eq!(tailer.head_prefix.len(), banner.len());

    let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
    let line2 = "netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n";
    f.write_all(line2.as_bytes()).unwrap();
    drop(f);
    assert!(tailer.poll(101.0));
    assert_eq!(tailer.grpc_port_logged, Some(43929));
    assert_eq!(tailer.head_prefix.len(), banner.len() + line2.len());
    assert!(tailer.head_prefix.len() < HEAD_PREFIX_MAX_BYTES);

    let mut f2 = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
    writeln!(f2, "netsim I 09-25 22:37:05.300 netsimd.rs:451 - Netsim Version: 1.0.30").unwrap();
    drop(f2);
    assert!(tailer.poll(102.0));
    assert_eq!(tailer.head_prefix.len(), HEAD_PREFIX_MAX_BYTES);

    // In-place truncation sharing the identical short first line must still be detected.
    let run2 = format!(
        "{banner}netsim I 09-25 22:37:15.200 netsimd.rs:750 - gRPC port: 43935\n\
         netsim I 09-25 22:37:15.300 netsimd.rs:451 - Netsim Version: 1.0.33, OS: linux\n\
         netsim I 09-25 22:37:15.400 netsimd.rs:744 - Wrote to INI file /tmp/netsim.ini\n"
    );
    std::fs::write(&log_path, run2).unwrap();
    assert!(tailer.poll_with_truncation_policy(115.0, false));
    assert_eq!(tailer.truncation_count, 1);
    assert_eq!(tailer.grpc_port_logged, Some(43935));
    assert_eq!(tailer.version.as_deref(), Some("1.0.33"));
}

#[test]
fn test_log_tailer_bounds_single_poll_read_and_skips_oversize_lines() {
    use std::io::Write;
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    let cap: usize = 16 * 1024 * 1024;
    let mut f = std::fs::File::create(&log_path).unwrap();
    let oversize_len = cap + 4096;
    f.write_all(&vec![b'x'; oversize_len]).unwrap();
    f.write_all(
        b"\nnetsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n\
          netsim I 09-25 22:37:05.300 netsimd.rs:750 - gRPC port: 0\n\
          netsim I 09-25 22:37:05.400 service.rs:550 - DeviceActor: AddChipByGuid 1 for device 127.0.0.1:0\n",
    )
    .unwrap();
    drop(f);

    let mut tailer = NetsimLogTailer::new(log_path);
    assert!(tailer.poll(100.0));
    assert_eq!(tailer.offset, cap as u64, "first poll must bound read to 16 MiB and advance");
    assert_eq!(tailer.grpc_port_logged, None);

    assert!(tailer.poll(101.0));
    assert_eq!(tailer.grpc_port_logged, Some(43929), "port 43929 parsed and port 0 ignored");
    assert_eq!(tailer.pending_guid_port, None, "peer_port 0 must be ignored");
}

#[test]
fn test_log_tailer_preserves_line_straddling_poll_cap_after_oversize_line() {
    use std::io::Write;
    let dir = tempfile::tempdir().unwrap();
    let log_path = dir.path().join("netsim_stderr.log");
    let mut f = std::fs::File::create(&log_path).unwrap();
    // End the oversize line 40 bytes before the second 16 MiB poll boundary so the
    // following normal log line straddles the boundary without containing a newline
    // inside Poll 2's post-newline remainder slice.
    let oversize_len = (2 * MAX_LOG_POLL_BYTES) - 41;
    let next_line = b"netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n";
    f.write_all(&vec![b'x'; oversize_len]).unwrap();
    f.write_all(b"\n").unwrap();
    f.write_all(next_line).unwrap();
    drop(f);
    let expected_end = (oversize_len + 1 + next_line.len()) as u64;

    let mut tailer = NetsimLogTailer::new(log_path);
    assert!(tailer.poll(100.0));
    assert_eq!(tailer.offset, MAX_LOG_POLL_BYTES as u64);
    assert!(tailer.skipping_oversize_line);

    assert!(tailer.poll(101.0));
    assert!(!tailer.skipping_oversize_line);
    assert_eq!(tailer.offset, (oversize_len + 1) as u64);
    assert_eq!(tailer.grpc_port_logged, None);

    assert!(tailer.poll(102.0));
    assert_eq!(tailer.grpc_port_logged, Some(43929));
    assert_eq!(tailer.offset, expected_end);
}

#[test]
fn test_log_tailer_invalid_guid_port_clears_prior_pending_port() {
    let mut tailer = NetsimLogTailer::new(PathBuf::new());
    tailer.feed_line(
        "netsim I 09-25 22:37:06.000 service.rs:550 - DeviceActor: AddChipByGuid 1 for device 127.0.0.1:50001",
        100.0,
    );
    assert_eq!(tailer.pending_guid_port.map(|(_, p)| p), Some(50001));

    tailer.feed_line(
        "netsim I 09-25 22:37:06.100 service.rs:550 - DeviceActor: AddChipByGuid 2 for device 127.0.0.1:0",
        100.1,
    );
    assert_eq!(tailer.pending_guid_port, None);
    tailer.feed_line(
        "netsim I 09-25 22:37:06.100 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 2 (Pixel 8)",
        100.1,
    );
    assert_eq!(tailer.chip_adds[0].peer_port, None);

    tailer.feed_line(
        "netsim I 09-25 22:37:06.200 service.rs:550 - DeviceActor: AddChipByGuid 3 for device 127.0.0.1:50002",
        100.2,
    );
    tailer.feed_line(
        "netsim I 09-25 22:37:06.300 service.rs:550 - DeviceActor: AddChipByGuid 4 for device 127.0.0.1:70000",
        100.3,
    );
    assert_eq!(tailer.pending_guid_port, None);
}

#[cfg(unix)]
#[test]
fn test_log_tailer_and_stats_enforce_self_owner_uid_checks() {
    let uid = super::owner_uid().expect("owner_uid must resolve on Unix");
    let foreign_uid = uid.wrapping_add(1).max(1);
    let tmp = tempfile::tempdir().unwrap();
    let art = tmp.path().join("netsimd");
    std::fs::create_dir_all(&art).unwrap();

    // 0-byte and non-empty logs are rejected when `require_self_owner` sees a foreign UID.
    let log_path = art.join("netsim_stderr.log");
    std::fs::write(&log_path, b"").unwrap();
    let mut tailer_foreign = NetsimLogTailer::new(log_path.clone());
    tailer_foreign.require_self_owner = true;
    tailer_foreign.owner_uid_override = Some(Some(foreign_uid));
    assert!(!tailer_foreign.poll(100.0));

    std::fs::write(&log_path, "netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 43929\n")
        .unwrap();
    assert!(!tailer_foreign.poll(101.0));
    assert_eq!(tailer_foreign.grpc_port_logged, None);

    // Same-UID and root (`Some(0)`) accept the log, whereas `None` on Unix fails closed.
    let mut tailer_own = NetsimLogTailer::new(log_path.clone());
    tailer_own.require_self_owner = true;
    tailer_own.owner_uid_override = Some(Some(uid));
    assert!(tailer_own.poll(101.0));
    assert_eq!(tailer_own.grpc_port_logged, Some(43929));

    let mut tailer_root = NetsimLogTailer::new(log_path.clone());
    tailer_root.require_self_owner = true;
    tailer_root.owner_uid_override = Some(Some(0));
    assert!(tailer_root.poll(101.0));

    // Under `sudo` (`monitor_uid = Some(0)`) the log must belong to the daemon's user
    // (`expected_uid`), also on the 0-byte and mtime-cache fast paths.
    let mut tailer_sudo_dev = NetsimLogTailer::new(log_path.clone());
    tailer_sudo_dev.owner_uid_override = Some(Some(0));
    tailer_sudo_dev.expected_uid = Some(uid);
    assert!(tailer_sudo_dev.poll(101.0));
    assert_eq!(tailer_sudo_dev.grpc_port_logged, Some(43929));

    let mut tailer_sudo_spoof = NetsimLogTailer::new(log_path.clone());
    tailer_sudo_spoof.owner_uid_override = Some(Some(0));
    tailer_sudo_spoof.expected_uid = Some(foreign_uid);
    assert!(
        !tailer_sudo_spoof.poll(101.0),
        "sudo monitor must reject log whose owner mismatches daemon expected_uid"
    );
    assert_eq!(tailer_sudo_spoof.grpc_port_logged, None);

    let empty_log = art.join("empty_stderr.log");
    std::fs::write(&empty_log, b"").unwrap();
    let mut tailer_sudo_spoof_empty = NetsimLogTailer::new(empty_log);
    tailer_sudo_spoof_empty.owner_uid_override = Some(Some(0));
    tailer_sudo_spoof_empty.expected_uid = Some(foreign_uid);
    assert!(
        !tailer_sudo_spoof_empty.poll(101.0),
        "0-byte fast path must still enforce expected_uid under sudo"
    );

    let mut tailer_none = NetsimLogTailer::new(log_path);
    tailer_none.require_self_owner = true;
    tailer_none.owner_uid_override = Some(None);
    assert!(!tailer_none.poll(101.0));

    // Session stats also verify UID ownership before reading or reusing cached state.
    let stats_path = art.join("netsim_session_stats.json");
    std::fs::write(
        &stats_path,
        r#"{"version":"1.0.31","device_stats":[{"device_id":1}],"radio_stats":[{"device_id":1,"kind":"WIFI","duration_secs":"5"}]}"#,
    )
    .unwrap();
    let mut cache = None;
    let (cnt, res) = resolve_session_stats_chips_with_uid(
        &stats_path,
        &[],
        1,
        0.0,
        &mut cache,
        StatsOwnerPolicy {
            require_self_owner: true,
            monitor_uid: Some(uid),
            expected_uid: Some(uid),
        },
    );
    assert_eq!(cnt, 1);
    assert!(res.is_some());
    assert!(cache.is_some());

    // Even with a populated `cache`, a foreign-UID check or sudo spoof check rejects the stats file.
    assert_eq!(
        resolve_session_stats_chips_with_uid(
            &stats_path,
            &[],
            1,
            0.0,
            &mut cache,
            StatsOwnerPolicy {
                require_self_owner: true,
                monitor_uid: Some(foreign_uid),
                expected_uid: Some(foreign_uid),
            },
        ),
        (0, None)
    );
    assert_eq!(
        resolve_session_stats_chips_with_uid(
            &stats_path,
            &[],
            1,
            0.0,
            &mut cache,
            StatsOwnerPolicy {
                require_self_owner: false,
                monitor_uid: Some(0),
                expected_uid: Some(foreign_uid),
            },
        ),
        (0, None)
    );
    assert_eq!(
        resolve_session_stats_chips_with_uid(
            &stats_path,
            &[],
            1,
            0.0,
            &mut cache,
            StatsOwnerPolicy {
                require_self_owner: false,
                monitor_uid: Some(0),
                expected_uid: Some(uid),
            },
        )
        .0,
        1
    );
    assert_eq!(
        resolve_session_stats_chips_with_uid(
            &stats_path,
            &[],
            1,
            0.0,
            &mut cache,
            StatsOwnerPolicy { require_self_owner: true, monitor_uid: None, expected_uid: None },
        ),
        (0, None)
    );
    assert_eq!(
        resolve_session_stats_chips_with_uid(
            &stats_path,
            &[],
            1,
            0.0,
            &mut cache,
            StatsOwnerPolicy { require_self_owner: true, monitor_uid: Some(0), expected_uid: None },
        )
        .0,
        1
    );
}
