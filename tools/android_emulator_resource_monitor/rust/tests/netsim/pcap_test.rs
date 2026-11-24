use super::*;

/// A daemon record for harvest tests: the artifact dir holds one real pcap,
/// one real zip and (on Unix) symlinked look-alikes pointing at a secret file.
fn harvest_fixture(tmp: &Path, same_user: bool) -> (DaemonRecord, PathBuf, PathBuf) {
    let art_dir = tmp.join("art");
    let pcaps = art_dir.join("pcaps");
    std::fs::create_dir_all(&pcaps).unwrap();
    let secret = tmp.join("secret.txt");
    std::fs::write(&secret, b"SECRET-DATA").unwrap();
    std::fs::write(pcaps.join("netsim-1-Pixel_8-WIFI.pcap"), [0xd4u8; 20]).unwrap();
    std::fs::write(art_dir.join("netsim_artifacts_1.zip"), b"PK\x03\x04zip").unwrap();
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(&secret, pcaps.join("netsim-2-Evil-BT.pcap")).unwrap();
        std::os::unix::fs::symlink(&secret, art_dir.join("netsim_artifacts_2.zip")).unwrap();
    }
    let ini_path = same_user.then(|| {
        let ini = tmp.join("netsim.ini");
        std::fs::write(&ini, "grpc.port=43929\npid=5001\n").unwrap();
        ini
    });
    let daemon = DaemonRecord {
        daemon_key: (5001, 100),
        pid: 5001,
        create_time: 100,
        start_wall: 100.0,
        spawner: "emulator".to_string(),
        instance_num: None,
        artifact_dir: art_dir,
        log_path: PathBuf::new(),
        tailer: None,
        netsim_cli_bin: "netsim".to_string(),
        same_user,
        expected_uid: same_user.then(owner_uid).flatten(),
        version: None,
        ini_path,
        grpc_port: None,
        ready_ts: None,
        ready_recorded: false,
        netsim_daemon_ready_s: None,
        frontend_disabled: false,
        pcap_flag: false,
        log_available: false,
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
    (daemon, tmp.join("out"), secret)
}

#[cfg(unix)]
fn copied_names(out: &Path) -> Vec<String> {
    fn walk(dir: &Path, acc: &mut Vec<String>) {
        if let Ok(rd) = std::fs::read_dir(dir) {
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, acc);
                } else {
                    acc.push(p.file_name().unwrap().to_string_lossy().to_string());
                }
            }
        }
    }
    let mut v = Vec::new();
    walk(out, &mut v);
    v.sort();
    v
}

/// Writes an executable script and returns once it can be run.
#[cfg(unix)]
fn write_script(path: &Path, body: &str) {
    use std::os::unix::fs::PermissionsExt;

    std::fs::write(path, body).unwrap();
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
    crate::test_support::retry_while_text_busy(|| std::process::Command::new(path).output())
        .unwrap();
}

#[test]
#[cfg(unix)]
fn test_harvest_copies_same_named_zip_from_restarted_daemon() {
    // A restarted daemon reuses netsim_artifacts_1.zip; the new file belongs
    // to the new daemon and is harvested into its directory too.
    let tmp = tempfile::tempdir().unwrap();
    let (mut first, out, _) = harvest_fixture(tmp.path(), true);
    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    mgr.harvest_daemon_files(&mut first, &[]);
    std::fs::write(first.artifact_dir.join("netsim_artifacts_1.zip"), b"PK\x03\x04new").unwrap();
    let mut second = DaemonRecord {
        daemon_key: (5002, 200),
        pid: 5002,
        create_time: 200,
        start_wall: 200.0,
        ..first.clone()
    };
    mgr.harvest_daemon_files(&mut second, &[]);
    let zips = copied_names(&out).iter().filter(|n| *n == "netsim_artifacts_1.zip").count();
    assert_eq!(zips, 2);
}

#[test]
#[cfg(unix)]
fn test_netsim_tracker_cleanup_runs_once() {
    let tmp = tempfile::tempdir().unwrap();
    let (daemon, out, _) = harvest_fixture(tmp.path(), true);
    let pcaps = daemon.artifact_dir.join("pcaps");
    let mut tracker = NetsimTracker::new(Some(""), 500, false, &out);
    tracker.custom_ini_dirs = vec![out.clone()];
    tracker.daemons.insert(daemon.daemon_key, daemon);
    tracker.cleanup();
    assert_eq!(copied_names(&out).len(), 2);

    // A pcap that shows up later is not harvested by a second cleanup or by Drop.
    std::fs::write(pcaps.join("netsim-3-Late-WIFI.pcap"), [0xd4u8; 20]).unwrap();
    tracker.cleanup();
    drop(tracker);
    assert_eq!(copied_names(&out).len(), 2);
}

#[test]
#[cfg(unix)]
fn test_netsim_tracker_cleanup_without_pcap_collection_copies_nothing() {
    let tmp = tempfile::tempdir().unwrap();
    let (daemon, out, _) = harvest_fixture(tmp.path(), true);
    let mut tracker = NetsimTracker::new(None, 500, false, &out);
    tracker.custom_ini_dirs = vec![out.clone()];
    tracker.daemons.insert(daemon.daemon_key, daemon);
    tracker.cleanup();
    drop(tracker);
    assert!(copied_names(&out).is_empty());
}

#[test]
#[cfg(unix)]
fn test_harvest_never_follows_symlinks_in_artifact_dir() {
    // A symlink planted in the daemon's artifact dir must not turn the
    // copy into a read of an arbitrary file; real files still copy.
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    mgr.harvest_daemon_files(&mut daemon, &[]);
    assert_eq!(
        copied_names(&out),
        vec!["netsim-1-Pixel_8-WIFI.pcap".to_string(), "netsim_artifacts_1.zip".to_string()]
    );
    fn walk_check(dir: &Path) {
        for e in std::fs::read_dir(dir).unwrap().flatten() {
            let p = e.path();
            if p.is_dir() {
                walk_check(&p);
            } else {
                let data = std::fs::read(&p).unwrap();
                assert!(!data.windows(6).any(|w| w == b"SECRET"), "{p:?} leaked secret");
            }
        }
    }
    walk_check(&out);
    // The live counters still describe what is on disk: the planted link counts as
    // itself, not as the file it points at.
    assert_eq!(daemon.pcap_live_files, 2);
    let link = daemon.artifact_dir.join("pcaps").join("netsim-2-Evil-BT.pcap");
    assert_eq!(daemon.pcap_live_bytes, 20 + std::fs::symlink_metadata(link).unwrap().len());
}

#[test]
#[cfg(unix)]
fn test_harvest_skips_daemons_of_other_users() {
    // Another user's daemon can point ANDROID_TMP anywhere; the monitor
    // only reads pcap sizes and never copies from its artifact dir.
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), false);
    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    mgr.harvest_daemon_files(&mut daemon, &[]);
    assert!(copied_names(&out).is_empty());
    assert_eq!(mgr.total_copied_bytes, 0);
    assert_eq!(daemon.pcap_live_files, 2);
}

#[test]
fn test_capture_list_filter_simulation_and_pcap_manager() {
    let list_out = "\
ID | Device Name     | Chip Kind            | State | Timestamp | Records | Size (bytes)
---|-----------------|----------------------|-------|-----------|---------|-------------
1  | Pixel 8         | WIFI                 | off   | 22:37:06  | 0       | 0
10 | Pixel 8 Pro     | WIFI                 | off   | 22:37:06  | 0       | 0
11 | ERM QN Phone 37 | BLUETOOTH_LOW_ENERGY | off   | 22:37:17  | 0       | 0
";
    let rows = parse_netsim_capture_list(list_out).unwrap();
    assert_eq!(rows.len(), 3);
    assert!(select_safe_capture_patterns(&rows, "Pixel 8", None).is_none());
    assert!(select_safe_capture_patterns(&rows, "Pixel 8", Some(&rows[0])).is_none());
    let safe = select_safe_capture_patterns(&rows, "ERM QN Phone 37", Some(&rows[2])).unwrap();
    assert_eq!(safe[0], "11");

    let dir = tempfile::tempdir().unwrap();
    let art_dir = dir.path().join("art");
    let pcaps_dir = art_dir.join("pcaps");
    std::fs::create_dir_all(&pcaps_dir).unwrap();
    std::fs::write(
        pcaps_dir.join("netsim-11-ERM_QN_Phone_37-BLUETOOTH_LOW_ENERGY.pcap"),
        vec![b'x'; 128],
    )
    .unwrap();
    std::fs::write(art_dir.join("netsim_artifacts_123.zip"), vec![b'z'; 64]).unwrap();

    let out_dir = dir.path().join("out");
    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out_dir);
    let calls: std::sync::Arc<std::sync::Mutex<Vec<Vec<String>>>> =
        std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let calls_clone = calls.clone();
    let list_str = list_out.to_string();
    mgr.cli_runner = std::sync::Arc::new(move |_target, args| {
        assert!(
            !(args.len() >= 2 && args[0] == "capture" && args[1] == "get"),
            "capture get is forbidden"
        );
        calls_clone.lock().unwrap().push(args.to_vec());
        Some(list_str.clone())
    });

    let ini = dir.path().join("netsim.ini");
    std::fs::write(&ini, "grpc.port=43929\npid=5000\n").unwrap();
    let mut daemon = DaemonRecord {
        daemon_key: (5000, 100),
        pid: 5000,
        create_time: 100,
        start_wall: 100.0,
        spawner: "emulator".to_string(),
        instance_num: None,
        artifact_dir: art_dir,
        log_path: PathBuf::new(),
        tailer: None,
        netsim_cli_bin: "netsim".to_string(),
        same_user: true,
        expected_uid: owner_uid(),
        version: Some("1.0.24".to_string()),
        ini_path: Some(ini),
        grpc_port: Some(43929),
        ready_ts: Some(100.1),
        ready_recorded: true,
        netsim_daemon_ready_s: Some(0.1),
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
        active_socket_conns: 1,
        joined_emulator_count: 2,
    };

    let mut cids = BTreeSet::new();
    cids.insert("11".to_string());
    let joined = vec![
        EmulatorNetsimRecord {
            storage_key: "Pixel_8_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            avd_id: Some("Pixel_8".to_string()),
            device_name: Some("Pixel 8".to_string()),
            worker_pid: 1,
            worker_create_time: 0,
            group_pids: Vec::new(),
            group_procs: BTreeMap::new(),
            worker_start_ts: 100.0,
            first_seen_mono: 1.0,
            min_log_ts: None,
            no_netsim: false,
            state: "CONNECTED".to_string(),
            ambiguous: false,
            daemon_key: Some((5000, 100)),
            netsim_daemon_ready_s: None,
            first_transport_ts: Some(101.0),
            netsim_connect_s: Some(1.0),
            chip_attach_s: BTreeMap::new(),
            chip_ids: BTreeSet::new(),
            unknown_chip_count: 0,
            wifi_associated_s: None,
            disconnect_count: 0,
            connected_start_mono: Some(1.0),
            connected_total_s: 0.0,
            adb_online_since_mono: None,
            missing_since_mono: None,
            missed_ticks: 0,
            prior_bare_snapshot: None,
        },
        EmulatorNetsimRecord {
            storage_key: "ERM_emulator-5556".to_string(),
            serial: "emulator-5556".to_string(),
            avd_id: Some("erm_qn_phone37".to_string()),
            device_name: Some("ERM QN Phone 37".to_string()),
            worker_pid: 2,
            worker_create_time: 0,
            group_pids: Vec::new(),
            group_procs: BTreeMap::new(),
            worker_start_ts: 100.0,
            first_seen_mono: 1.0,
            min_log_ts: None,
            no_netsim: false,
            state: "CONNECTED".to_string(),
            ambiguous: false,
            daemon_key: Some((5000, 100)),
            netsim_daemon_ready_s: None,
            first_transport_ts: Some(101.0),
            netsim_connect_s: Some(1.0),
            chip_attach_s: BTreeMap::new(),
            chip_ids: cids,
            unknown_chip_count: 0,
            wifi_associated_s: None,
            disconnect_count: 0,
            connected_start_mono: Some(1.0),
            connected_total_s: 0.0,
            adb_online_since_mono: None,
            missing_since_mono: None,
            missed_ticks: 0,
            prior_bare_snapshot: None,
        },
    ];

    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 20.0, false);
    assert!(mgr.skipped_notes.iter().any(|n| n.contains("Pixel 8")));
    assert!(mgr.enabled_by_monitor.contains_key(&((5000, 100), 11)));
    assert!(!mgr.copied_files.is_empty());

    let mut daemons = BTreeMap::new();
    daemons.insert((5000, 100), daemon);
    let mut emu_recs = BTreeMap::new();
    for e in joined {
        emu_recs.insert(e.storage_key.clone(), e);
    }
    mgr.cleanup(&mut daemons, &emu_recs);
    let recorded_calls = calls.lock().unwrap();
    assert!(
        recorded_calls
            .iter()
            .any(|c| c.len() >= 3 && c[0] == "capture" && c[1] == "patch" && c[2] == "off")
    );
}

#[test]
#[cfg(unix)]
fn test_pcap_manager_throttles_per_daemon_and_after_ambiguous_skip() {
    let tmp_a = tempfile::tempdir().unwrap();
    let tmp_b = tempfile::tempdir().unwrap();
    let (mut first, out, _) = harvest_fixture(tmp_a.path(), true);
    let (mut second, _, _) = harvest_fixture(tmp_b.path(), true);
    second.daemon_key = (5002, 200);
    second.pid = 5002;
    second.create_time = 200;
    second.start_wall = 200.0;

    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let calls_clone = calls.clone();
    let list_out = "\
ID | Device Name | Chip Kind | State | Timestamp | Records | Size (bytes)
---|-------------|-----------|-------|-----------|---------|-------------
1  | Pixel 8     | WIFI      | off   | 22:37:06  | 0       | 0
10 | Pixel 8 Pro | WIFI      | off   | 22:37:06  | 0       | 0
";
    mgr.cli_runner = std::sync::Arc::new(move |_target, _args| {
        *calls_clone.lock().unwrap() += 1;
        Some(list_out.to_string())
    });
    first.grpc_port = Some(43929);
    second.grpc_port = Some(43930);

    let joined_a = [EmulatorNetsimRecord {
        storage_key: "Pixel_8_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("Pixel 8".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(first.daemon_key),
        ..Default::default()
    }];

    // Both daemons are polled on the same tick (now_mono = 20.0): neither
    // daemon may starve the other.
    let joined_a_refs: Vec<&EmulatorNetsimRecord> = joined_a.iter().collect();
    mgr.poll_and_manage_daemon(&mut first, &joined_a_refs, 20.0, false);
    mgr.poll_and_manage_daemon(&mut second, &[], 20.0, false);
    assert!(first.pcap_live_bytes > 0);
    assert!(second.pcap_live_bytes > 0);

    // A follow-up tick 2s later (< HARVEST_INTERVAL_S) with the same
    // ambiguous device must be throttled rather than re-running CLI.
    let calls_after_first_tick = *calls.lock().unwrap();
    mgr.poll_and_manage_daemon(&mut first, &joined_a_refs, 22.0, false);
    assert_eq!(*calls.lock().unwrap(), calls_after_first_tick);
}

#[test]
fn test_pcap_manager_passes_ini_tmpdir_and_instance_and_records_cli_errors() {
    let missing_ini_out = concat!(
        "netsim E 11-09 08:06:41.842 ini_file.rs:139 - Unable to find netsim ini file: \"/tmp/netsim_2.ini\"\n",
        "netsim E 11-09 08:06:41.842 main.rs:349 - error=Failed to connect to : invalid socket address\n",
    );
    assert!(parse_netsim_capture_list(missing_ini_out).is_err());
    assert!(parse_netsim_capture_list("Failed to connect to server").is_err());
    assert!(parse_netsim_capture_list("error=connection failed").is_err());

    assert_eq!(
        build_netsim_cli_args(44709, Some(1), &["capture".to_string(), "list".to_string()]),
        vec!["-v", "-p", "44709", "capture", "list"]
    );
    assert_eq!(
        build_netsim_cli_args(42747, Some(2), &["capture".to_string(), "list".to_string()]),
        vec!["-v", "-i", "2", "-p", "42747", "capture", "list"]
    );

    let tmp = tempfile::tempdir().unwrap();
    let ini_dir = tmp.path().join("emu_tmp");
    std::fs::create_dir_all(&ini_dir).unwrap();
    let ini_path = ini_dir.join("netsim_2.ini");
    std::fs::write(&ini_path, "grpc.port=42747\npid=5001\n").unwrap();

    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    daemon.ini_path = Some(ini_path);
    daemon.grpc_port = Some(42747);

    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let calls_clone = calls.clone();
    let fail_first = std::sync::Arc::new(std::sync::Mutex::new(true));
    let fail_first_clone = fail_first.clone();
    let list_ok = "\
ID | Device Name | Chip Kind | State | Timestamp | Records | Size (bytes)
---|-------------|-----------|-------|-----------|---------|-------------
0  | erm_live37  | WIFI      | off   | 00:00:00  | 0       | 0
";
    mgr.cli_runner = std::sync::Arc::new(move |target, args| {
        calls_clone.lock().unwrap().push((target.clone(), args.to_vec()));
        let mut fail = fail_first_clone.lock().unwrap();
        if *fail {
            *fail = false;
            return Some(missing_ini_out.to_string());
        }
        Some(list_ok.to_string())
    });

    let joined = [EmulatorNetsimRecord {
        storage_key: "erm_live37_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("erm_live37".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    }];

    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    // First poll gets CLI error -> surfaced in skipped_notes, emulator stays unchecked.
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
    assert!(mgr.skipped_notes.iter().any(|n| n.contains("Unable to find netsim ini file")));
    assert!(mgr.enabled_by_monitor.is_empty());

    // Second poll 1s later retries immediately because capture list had failed,
    // passes ini_dir and instance 2, and enables capture.
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 11.0, false);
    assert!(mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 0)));

    let mut daemons = BTreeMap::new();
    daemons.insert(daemon.daemon_key, daemon);
    let mut emu_recs = BTreeMap::new();
    for e in joined {
        emu_recs.insert(e.storage_key.clone(), e);
    }
    mgr.cleanup(&mut daemons, &emu_recs);

    let recorded = calls.lock().unwrap();
    let expected_target = NetsimCliTarget {
        netsim_bin: "netsim".to_string(),
        grpc_port: 42747,
        ini_dir: Some(ini_dir),
        instance_num: Some(2),
    };
    assert!(recorded.iter().all(|(t, _)| t == &expected_target));
    assert!(
        recorded.iter().any(|(_, a)| a == &["capture", "patch", "on", "0", "erm_live37", "WIFI"])
    );
    assert!(
        recorded.iter().any(|(_, a)| a == &["capture", "patch", "off", "0", "erm_live37", "WIFI"])
    );
}

#[test]
#[cfg(unix)]
fn test_harvest_counts_current_bytes_of_a_pcap_that_shrank() {
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    let pcap = daemon.artifact_dir.join("pcaps").join("netsim-1-Pixel_8-WIFI.pcap");
    let set = |len: usize, mtime: u64| {
        std::fs::write(&pcap, vec![0xd4u8; len]).unwrap();
        std::fs::File::options()
            .write(true)
            .open(&pcap)
            .unwrap()
            .set_modified(UNIX_EPOCH + std::time::Duration::from_secs(mtime))
            .unwrap();
    };
    let mut mgr = NetsimPcapManager::new(Some(""), 1, &out);
    let zip_bytes = 7;
    set(800 * 1024, 1000);
    mgr.harvest_daemon_files(&mut daemon, &[]);
    // The capture restarts: the file shrinks, then grows again.
    set(100 * 1024, 1010);
    mgr.harvest_daemon_files(&mut daemon, &[]);
    set(500 * 1024, 1020);
    mgr.harvest_daemon_files(&mut daemon, &[]);
    assert!(!mgr.cap_reached);
    assert_eq!(mgr.total_copied_bytes, 500 * 1024 + zip_bytes);
}

#[test]
#[cfg(unix)]
fn test_harvest_sends_pcap_of_a_shared_device_name_to_shared() {
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    let emu = |serial: &str| EmulatorNetsimRecord {
        storage_key: format!("pixel_{serial}"),
        serial: serial.to_string(),
        device_name: Some("Pixel 8".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    };
    let joined = [emu("emulator-5554"), emu("emulator-5556")];
    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    mgr.harvest_daemon_files(&mut daemon, &joined_refs);
    let keys: Vec<&String> = mgr.copied_files.keys().filter(|k| k.ends_with(".pcap")).collect();
    assert_eq!(keys.len(), 1);
    assert!(keys[0].contains("/shared/"), "{keys:?}");
}

#[test]
#[cfg(unix)]
fn test_pcap_manager_skips_cli_when_disabled_and_lists_once_per_daemon_on_cleanup() {
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    daemon.grpc_port = Some(43929);

    let mut mgr_disabled = NetsimPcapManager::new(None, 500, &out);
    let disabled_calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let disabled_calls_clone = disabled_calls.clone();
    mgr_disabled.cli_runner = std::sync::Arc::new(move |_target, _args| {
        *disabled_calls_clone.lock().unwrap() += 1;
        Some(String::new())
    });
    mgr_disabled.poll_and_manage_daemon(&mut daemon, &[], 10.0, false);
    assert_eq!(*disabled_calls.lock().unwrap(), 0);

    let list_out = "\
ID | Device Name | Chip Kind            | State | Timestamp | Records | Size (bytes)
---|-------------|----------------------|-------|-----------|---------|-------------
1  | erm_live37  | WIFI                 | off   | 00:00:00  | 0       | 0
2  | erm_live37  | BLUETOOTH_LOW_ENERGY | off   | 00:00:00  | 0       | 0
";
    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Vec<String>>::new()));
    let calls_clone = calls.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_target, args| {
        calls_clone.lock().unwrap().push(args.to_vec());
        Some(list_out.to_string())
    });
    let joined = [EmulatorNetsimRecord {
        storage_key: "erm_live37_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("erm_live37".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    }];
    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
    assert_eq!(mgr.enabled_by_monitor.len(), 2);

    calls.lock().unwrap().clear();
    let mut daemons = BTreeMap::from([(daemon.daemon_key, daemon)]);
    let emu_recs = BTreeMap::new();
    mgr.cleanup(&mut daemons, &emu_recs);

    let cleanup_calls = calls.lock().unwrap();
    let list_count = cleanup_calls.iter().filter(|a| a.as_slice() == ["capture", "list"]).count();
    let off_count = cleanup_calls
        .iter()
        .filter(|a| a.len() >= 3 && a[0] == "capture" && a[1] == "patch" && a[2] == "off")
        .count();
    assert_eq!(list_count, 1);
    assert_eq!(off_count, 2);
}

#[test]
#[cfg(unix)]
fn test_cleanup_restores_prefix_colliding_captures_and_skips_exited_daemons() {
    let user_rows = parse_netsim_capture_list(
        "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
         1  | erm_live37  | WIFI      | on    | 00:00:00  | 0       | 0\n\
         10 | erm_live37b | WIFI      | on    | 00:00:05  | 0       | 0\n",
    )
    .unwrap();
    let only_1 = HashSet::from([1u32]);
    assert_eq!(
        select_safe_disable_patterns(&user_rows, "erm_live37", &user_rows[0], &only_1),
        None
    );

    let tmp = tempfile::tempdir().unwrap();
    let (mut d_old, out, _) = harvest_fixture(tmp.path(), true);
    d_old.exited = true;
    d_old.grpc_port = Some(43929);

    let mut d_live = d_old.clone();
    d_live.daemon_key = (5002, 200);
    d_live.pid = 5002;
    d_live.create_time = 200;
    d_live.start_wall = 200.0;
    d_live.exited = false;
    d_live.grpc_port = Some(43930);

    let mut mgr = NetsimPcapManager::new(Some(""), 1, &out);
    let state = std::sync::Arc::new(std::sync::Mutex::new((false, false, 0usize)));
    let state_clone = state.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_target, args| {
        let mut st = state_clone.lock().unwrap();
        st.2 += 1;
        let call_no = st.2;
        if args == ["capture", "list"] {
            let s1 = if st.0 { "on" } else { "off" };
            let s10 = if st.1 { "on" } else { "off" };
            let mut text = format!(
                "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                 1  | erm_live37  | WIFI      | {s1}  | 00:00:00  | 0       | 0\n"
            );
            if call_no >= 3 {
                text.push_str(&format!(
                    "10 | erm_live37b | WIFI      | {s10} | 00:00:05  | 0       | 0\n"
                ));
            }
            return Some(text);
        }
        if args.len() >= 3 && args[0] == "capture" && args[1] == "patch" {
            let turn_on = args[2] == "on";
            let pats: Vec<String> = args[3..].iter().map(|p| p.to_uppercase()).collect();
            if pats
                .iter()
                .all(|p| "1".contains(p) || "ERM_LIVE37".contains(p) || "WIFI".contains(p))
            {
                st.0 = turn_on;
            }
            if call_no >= 3
                && pats
                    .iter()
                    .all(|p| "10".contains(p) || "ERM_LIVE37B".contains(p) || "WIFI".contains(p))
            {
                st.1 = turn_on;
            }
            return Some(
                "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n".to_string(),
            );
        }
        None
    });

    let emu1 = EmulatorNetsimRecord {
        storage_key: "erm_live37_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("erm_live37".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(d_live.daemon_key),
        chip_ids: BTreeSet::from(["1".to_string()]),
        ..Default::default()
    };
    let emu2 = EmulatorNetsimRecord {
        storage_key: "erm_live37b_emulator-5556".to_string(),
        serial: "emulator-5556".to_string(),
        device_name: Some("erm_live37b".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(d_live.daemon_key),
        chip_ids: BTreeSet::from(["10".to_string()]),
        ..Default::default()
    };
    let old_emu = EmulatorNetsimRecord {
        storage_key: "old_emulator-5558".to_string(),
        serial: "emulator-5558".to_string(),
        device_name: Some("OldPhone".to_string()),
        state: "DISCONNECTED".to_string(),
        daemon_key: Some(d_old.daemon_key),
        ..Default::default()
    };

    mgr.poll_and_manage_daemon(&mut d_live, &[&emu1], 10.0, false);
    assert!(state.lock().unwrap().0);
    mgr.poll_and_manage_daemon(&mut d_live, &[&emu1, &emu2], 25.0, false);
    assert!(state.lock().unwrap().1);

    // forget_daemon drops enabled_by_monitor entries for an exited daemon.
    mgr.enabled_by_monitor.insert(
        (d_old.daemon_key, 99),
        EnabledChipRecord {
            target: NetsimCliTarget {
                netsim_bin: "netsim".to_string(),
                grpc_port: 43929,
                ini_dir: None,
                instance_num: None,
            },
            ini_path: None,
            device_name: "OldPhone".to_string(),
            row: CaptureRow {
                id: 99,
                device_name: "OldPhone".to_string(),
                chip_kind: "WIFI".to_string(),
                raw_chip_kind: "WIFI".to_string(),
                state: "off".to_string(),
                timestamp: String::new(),
                records: 0,
                size_bytes: 0,
            },
        },
    );
    mgr.forget_daemon(d_old.daemon_key);
    assert!(!mgr.enabled_by_monitor.contains_key(&(d_old.daemon_key, 99)));

    let mut daemons = BTreeMap::from([(d_old.daemon_key, d_old), (d_live.daemon_key, d_live)]);
    let emu_recs = BTreeMap::from([
        (old_emu.storage_key.clone(), old_emu),
        (emu1.storage_key.clone(), emu1),
        (emu2.storage_key.clone(), emu2),
    ]);
    mgr.cleanup(&mut daemons, &emu_recs);

    let st = state.lock().unwrap();
    assert!(!st.0, "chip 1 remained on after cleanup");
    assert!(!st.1, "chip 10 remained on after cleanup");
    assert!(!out.join("netsim_pcaps").join("5001-100").exists());
    assert!(out.join("netsim_pcaps").join("5002-200").exists());
}

#[test]
#[cfg(unix)]
fn test_pcap_manager_skips_foreign_daemons_caps_cli_retries_and_sanitizes_notes() {
    let tmp = tempfile::tempdir().unwrap();
    let (mut foreign_daemon, out, _) = harvest_fixture(tmp.path(), false);
    let foreign_ini_dir = tmp.path().join("foreign_tmp");
    std::fs::create_dir_all(&foreign_ini_dir).unwrap();
    foreign_daemon.ini_path = Some(foreign_ini_dir.join("netsim.ini"));
    foreign_daemon.grpc_port = Some(43929);

    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let calls_clone = calls.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_target, _args| {
        let mut c = calls_clone.lock().unwrap();
        *c += 1;
        Some(format!(
            "netsim E 11-09 08:24:10.{:03} main.rs:78 - gRPC error UNIMPLEMENTED\nError: unsupported",
            *c
        ))
    });
    let joined = [EmulatorNetsimRecord {
        storage_key: "Phone_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("Phone".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(foreign_daemon.daemon_key),
        ..Default::default()
    }];

    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    // Foreign-user daemon never invokes the CLI.
    mgr.poll_and_manage_daemon(&mut foreign_daemon, &joined_refs, 10.0, false);
    assert_eq!(*calls.lock().unwrap(), 0, "foreign daemon invoked CLI");

    // Disabled-only or empty emulator lists on a same-user daemon also skip CLI.
    let tmp2 = tempfile::tempdir().unwrap();
    let (mut own_daemon, _, _) = harvest_fixture(tmp2.path(), true);
    own_daemon.grpc_port = Some(43930);
    let disabled = [EmulatorNetsimRecord {
        storage_key: "NoNet_emulator-5556".to_string(),
        serial: "emulator-5556".to_string(),
        device_name: Some("NoNet".to_string()),
        state: "DISABLED".to_string(),
        no_netsim: true,
        daemon_key: Some(own_daemon.daemon_key),
        ..Default::default()
    }];
    let disabled_refs: Vec<&EmulatorNetsimRecord> = disabled.iter().collect();
    mgr.poll_and_manage_daemon(&mut own_daemon, &disabled_refs, 10.0, false);
    assert_eq!(*calls.lock().unwrap(), 0, "DISABLED emulator invoked CLI");

    // Persistent CLI errors allow at most 1 immediate retry within the 10s window,
    // and strip timestamps + newlines so skipped_notes stays single-line and bounded.
    for t in [20.0, 22.0, 24.0, 26.0, 28.0] {
        mgr.poll_and_manage_daemon(&mut own_daemon, &joined_refs, t, false);
    }
    assert_eq!(*calls.lock().unwrap(), 2);
    assert_eq!(mgr.skipped_notes.len(), 1);
    assert!(!mgr.skipped_notes[0].contains('\n'));
    assert!(mgr.skipped_notes[0].contains("gRPC error UNIMPLEMENTED; Error: unsupported"));

    // Missing/timed-out CLI (`None`) records a diagnostic skipped_note.
    let mut mgr_none = NetsimPcapManager::new(Some(""), 500, &out);
    mgr_none.cli_runner = std::sync::Arc::new(|_, _| None);
    mgr_none.poll_and_manage_daemon(&mut own_daemon, &joined_refs, 40.0, false);
    assert_eq!(mgr_none.skipped_notes.len(), 1);
    assert!(mgr_none.skipped_notes[0].contains("unavailable or timed out"));
}

#[test]
#[cfg(unix)]
fn test_pcap_manager_allows_cli_for_same_user_daemon_with_system_tmp_ini() {
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    let tmp_ini =
        tempfile::Builder::new().prefix("netsim_").suffix(".ini").tempfile_in("/tmp").unwrap();
    std::fs::write(tmp_ini.path(), "grpc.port=43929\npid=5001\n").unwrap();
    daemon.ini_path = Some(tmp_ini.path().to_path_buf());
    daemon.grpc_port = Some(43929);

    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let calls_clone = calls.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_target, _args| {
        *calls_clone.lock().unwrap() += 1;
        Some(
            "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
             1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                .to_string(),
        )
    });
    let joined = [EmulatorNetsimRecord {
        storage_key: "Phone_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("Phone".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    }];
    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
    assert!(*calls.lock().unwrap() > 0, "same-user daemon with /tmp INI never invoked CLI");
    assert!(mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 1)));
}

#[test]
fn test_pcap_manager_new_expands_tilde_and_saturates_max_mb() {
    if let Some(home) = std::env::var_os("HOME")
        .filter(|v| !v.is_empty())
        .or_else(|| std::env::var_os("USERPROFILE").filter(|v| !v.is_empty()))
        .map(PathBuf::from)
    {
        let mgr_custom = NetsimPcapManager::new(Some("~/custom_pcaps"), 500, Path::new("."));
        assert_eq!(mgr_custom.pcap_dir, Some(home.join("custom_pcaps")));

        let mgr_out = NetsimPcapManager::new(Some(""), 500, Path::new("~/erm_out"));
        assert_eq!(mgr_out.pcap_dir, Some(home.join("erm_out").join("netsim_pcaps")));
    }

    let fake_home = Path::new("/fake/home");
    assert_eq!(expand_tilde_path_with(Path::new("~"), Some(fake_home)), fake_home);
    assert_eq!(expand_tilde_path_with(Path::new("~/"), Some(fake_home)), fake_home);
    assert_eq!(expand_tilde_path_with(Path::new("~\\sub"), Some(fake_home)), fake_home.join("sub"));
    assert_eq!(
        expand_tilde_path_with(Path::new("~other/sub"), Some(fake_home)),
        Path::new("~other/sub")
    );
    assert_eq!(expand_tilde_path_with(Path::new("~/sub"), None), Path::new("~/sub"));

    let mgr_max = NetsimPcapManager::new(Some(""), u64::MAX, Path::new("/tmp"));
    assert_eq!(mgr_max.max_bytes, u64::MAX);
}

#[test]
#[cfg(unix)]
fn test_pcap_manager_rejects_cli_when_artifact_dir_is_symlink_or_unowned() {
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    let real_art = daemon.artifact_dir.clone();
    let ini = tmp.path().join("netsim.ini");
    std::fs::write(&ini, "grpc.port=43929\npid=5001\n").unwrap();
    daemon.ini_path = Some(ini);
    daemon.grpc_port = Some(43929);

    let sym_art = tmp.path().join("symlink_art");
    std::os::unix::fs::symlink(&real_art, &sym_art).unwrap();
    daemon.artifact_dir = sym_art;

    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let calls_clone = calls.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_, _| {
        *calls_clone.lock().unwrap() += 1;
        Some(
            "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
             1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                .to_string(),
        )
    });
    let joined = [EmulatorNetsimRecord {
        storage_key: "Phone_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("Phone".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    }];
    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
    assert_eq!(*calls.lock().unwrap(), 0, "symlinked artifact_dir must block netsim CLI");
    assert!(mgr.copied_files.is_empty(), "symlinked artifact_dir must not harvest files");
    assert_eq!(daemon.pcap_live_files, 0);

    // Real artifact_dir with a symlinked `pcaps` subdirectory must not harvest pcaps.
    let sym_pcaps_art = tmp.path().join("real_art_sym_pcaps");
    std::fs::create_dir_all(&sym_pcaps_art).unwrap();
    std::os::unix::fs::symlink(real_art.join("pcaps"), sym_pcaps_art.join("pcaps")).unwrap();
    daemon.artifact_dir = sym_pcaps_art;
    mgr.harvest_daemon_files(&mut daemon, &joined_refs);
    assert!(mgr.copied_files.is_empty(), "symlinked pcaps subdir must not harvest pcaps");
    assert_eq!(daemon.pcap_live_files, 0);

    // Dangling symlink artifact_dir must also block CLI execution.
    let dangling_art = tmp.path().join("dangling_art");
    std::os::unix::fs::symlink(tmp.path().join("missing_target"), &dangling_art).unwrap();
    daemon.artifact_dir = dangling_art;
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 20.0, false);
    assert_eq!(*calls.lock().unwrap(), 0, "dangling symlink artifact_dir must block CLI");
    assert!(mgr.copied_files.is_empty());
}

#[test]
#[cfg(unix)]
fn test_pcap_manager_rechecks_on_chip_attach_and_routes_duplicate_chip_ids_to_shared() {
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    daemon.grpc_port = Some(43929);

    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let state = std::sync::Arc::new(std::sync::Mutex::new((false, 0usize)));
    let state_clone = state.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_, args| {
        let mut st = state_clone.lock().unwrap();
        st.1 += 1;
        if args == ["capture", "list"] {
            if !st.0 {
                return Some(
                    "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n"
                        .to_string(),
                );
            }
            return Some(
                "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                 1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                    .to_string(),
            );
        }
        Some("ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n".to_string())
    });

    let mut emu = EmulatorNetsimRecord {
        storage_key: "Phone_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("Phone".to_string()),
        state: "CONNECTING".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    };

    // Tick at t=10.0: 0 chips attached -> lists once, records 0-chip baseline.
    mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 10.0, false);
    assert_eq!(state.lock().unwrap().1, 1);

    // Tick at t=12.0 (< HARVEST_INTERVAL_S, still 0 chips): throttled, no CLI spam.
    mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 12.0, false);
    assert_eq!(state.lock().unwrap().1, 1);

    // Tick at t=14.0 (< HARVEST_INTERVAL_S): WIFI chip attaches -> must recheck immediately.
    state.lock().unwrap().0 = true;
    emu.state = "CONNECTED".to_string();
    emu.chip_ids.insert("1".to_string());
    emu.chip_attach_s.insert("WIFI".to_string(), (1.0, false));
    mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 14.0, false);
    assert!(
        mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 1)),
        "newly attached chip should be auto-enabled without waiting 10s"
    );
    let calls_after_enable = state.lock().unwrap().1;

    // Tick at t=16.0 (< 24.0, chip count unchanged at 1): throttled again.
    mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 16.0, false);
    assert_eq!(state.lock().unwrap().1, calls_after_enable);

    // Duplicate chip_id claim across two emulators must route pcap to `shared/`.
    let pcaps_dir = daemon.artifact_dir.join("pcaps");
    std::fs::write(pcaps_dir.join("netsim-7-Pixel_8-WIFI.pcap"), b"shared-bytes").unwrap();
    let make_dup = |serial: &str| EmulatorNetsimRecord {
        storage_key: format!("Pixel_8_{serial}"),
        serial: serial.to_string(),
        device_name: Some("Pixel 8".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        chip_ids: BTreeSet::from(["7".to_string()]),
        ..Default::default()
    };
    let dup_a = make_dup("emulator-5554");
    let dup_b = make_dup("emulator-5556");
    let mut mgr2 = NetsimPcapManager::new(Some(""), 500, &out);
    mgr2.harvest_daemon_files(&mut daemon, &[&dup_a, &dup_b]);
    let shared_hits: Vec<&String> =
        mgr2.copied_files.keys().filter(|k| k.ends_with("netsim-7-Pixel_8-WIFI.pcap")).collect();
    assert_eq!(shared_hits.len(), 1);
    assert!(shared_hits[0].contains("/shared/"), "{shared_hits:?}");
}

#[cfg(unix)]
#[test]
fn test_pcap_cli_skips_missing_ini_dir_and_sets_netsim_instance_env() {
    let tmp = tempfile::tempdir().unwrap();
    let fake_cli = tmp.path().join("fake_netsim");
    write_script(
        &fake_cli,
        "#!/bin/sh\nprintf 'INST=%s\\nTMP=%s\\nARGS=%s\\n' \"$NETSIM_INSTANCE\" \"$TMPDIR\" \"$*\"\n",
    );

    // `capture list`/`capture patch` must not execute when `ini_dir` is `None`.
    let no_ini_target = NetsimCliTarget {
        netsim_bin: fake_cli.to_string_lossy().to_string(),
        grpc_port: 43929,
        ini_dir: None,
        instance_num: Some(2),
    };
    assert_eq!(
        default_cli_runner(&no_ini_target, &["capture".to_string(), "list".to_string()]),
        None
    );
    assert_eq!(
        default_cli_runner(
            &no_ini_target,
            &["capture".to_string(), "patch".to_string(), "on".to_string(), "1".to_string()],
        ),
        None
    );

    // With `ini_dir` present, `default_cli_runner` passes both `TMPDIR` and `NETSIM_INSTANCE`
    // (defaulting `None` / `0` to `DEFAULT_NETSIM_INSTANCE` = `1`).
    let ini_dir = tmp.path().join("ini_dir");
    std::fs::create_dir_all(&ini_dir).unwrap();
    let default_inst_target = NetsimCliTarget {
        netsim_bin: fake_cli.to_string_lossy().to_string(),
        grpc_port: 43929,
        ini_dir: Some(ini_dir.clone()),
        instance_num: None,
    };
    let out1 =
        default_cli_runner(&default_inst_target, &["capture".to_string(), "list".to_string()])
            .expect("capture list with ini_dir must run");
    assert!(out1.contains("INST=1\n"), "out1={out1}");
    assert!(out1.contains(&format!("TMP={}\n", ini_dir.display())), "out1={out1}");

    let inst2_target = NetsimCliTarget { instance_num: Some(2), ..default_inst_target };
    let out2 = default_cli_runner(&inst2_target, &["capture".to_string(), "list".to_string()])
        .expect("capture list for instance 2 must run");
    assert!(out2.contains("INST=2\n"), "out2={out2}");
    assert!(out2.contains("ARGS=-v -i 2 -p 43929 capture list"), "out2={out2}");

    // `poll_and_manage_daemon` skips CLI without false error notes when `ini_path` is `None`,
    // while still harvesting on-disk PCAP/ZIP artifacts.
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    daemon.ini_path = None;
    daemon.grpc_port = Some(43929);
    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let calls_clone = calls.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_, _| {
        *calls_clone.lock().unwrap() += 1;
        None
    });
    let joined = [EmulatorNetsimRecord {
        storage_key: "Phone_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("Phone".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    }];
    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
    assert_eq!(*calls.lock().unwrap(), 0, "daemon with ini_path=None must not invoke CLI");
    assert!(mgr.skipped_notes.is_empty(), "notes={:?}", mgr.skipped_notes);
    assert!(mgr.cli_error_counts.is_empty());
    assert!(!mgr.copied_files.is_empty(), "on-disk artifacts must still be harvested");
}

#[cfg(unix)]
#[test]
fn test_run_cli_vets_an_adopted_sibling_before_every_run() {
    use std::os::unix::fs::PermissionsExt;

    let tmp = tempfile::tempdir().unwrap();
    let sdk = tmp.path().join("sdk");
    std::fs::create_dir(&sdk).unwrap();
    // Both directories would otherwise take their mode from the umask.
    for dir in [tmp.path(), sdk.as_path()] {
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let cli = sdk.join("netsim");
    std::fs::write(&cli, b"#!/bin/sh\n").unwrap();
    std::fs::set_permissions(&cli, std::fs::Permissions::from_mode(0o755)).unwrap();

    let mut mgr = NetsimPcapManager::new(Some(""), 500, tmp.path());
    let runs = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let runs_clone = runs.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_, _| {
        *runs_clone.lock().unwrap() += 1;
        Some("ran".to_string())
    });
    let args = ["capture".to_string(), "list".to_string()];
    let sibling = NetsimCliTarget {
        netsim_bin: cli.to_string_lossy().to_string(),
        grpc_port: 43929,
        ini_dir: None,
        instance_num: None,
    };
    assert_eq!(mgr.run_cli(&sibling, &args).as_deref(), Some("ran"));
    assert_eq!(*runs.lock().unwrap(), 1);

    // Once others can write to its directory, the sibling is no longer run.
    std::fs::set_permissions(&sdk, std::fs::Permissions::from_mode(0o777)).unwrap();
    assert_eq!(mgr.run_cli(&sibling, &args), None);
    std::fs::set_permissions(&sdk, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(mgr.run_cli(&sibling, &args).as_deref(), Some("ran"));
    std::fs::remove_file(&cli).unwrap();
    assert_eq!(mgr.run_cli(&sibling, &args), None);
    assert_eq!(*runs.lock().unwrap(), 2);

    // A bare name is found through the monitor's own PATH and is not vetted.
    let bare = NetsimCliTarget { netsim_bin: "netsim".to_string(), ..sibling };
    assert_eq!(mgr.run_cli(&bare, &args).as_deref(), Some("ran"));
    assert_eq!(*runs.lock().unwrap(), 3);
}

#[cfg(unix)]
#[test]
fn test_pcap_cli_overrides_ambient_netsim_instance_and_cleanup_revalidates_ini() {
    // Changing this process's environment is `unsafe` with other test threads running, so a
    // child running only this test gets `NETSIM_INSTANCE=7`: `default_cli_runner` must still
    // pass `NETSIM_INSTANCE=1` for `instance_num` `None` and `Some(1)`.
    if std::env::var_os("ERM_SUBTEST_NETSIM_INST_OVERRIDE").is_some() {
        assert_eq!(std::env::var("NETSIM_INSTANCE").as_deref(), Ok("7"));
        let tmp = tempfile::tempdir().unwrap();
        let fake_cli = tmp.path().join("fake_netsim");
        write_script(&fake_cli, "#!/bin/sh\nprintf 'INST=%s\\n' \"$NETSIM_INSTANCE\"\n");
        let ini_dir = tmp.path().join("ini_dir");
        std::fs::create_dir_all(&ini_dir).unwrap();
        for inst in [None, Some(1)] {
            let target = NetsimCliTarget {
                netsim_bin: fake_cli.to_string_lossy().to_string(),
                grpc_port: 43929,
                ini_dir: Some(ini_dir.clone()),
                instance_num: inst,
            };
            let out = default_cli_runner(&target, &["capture".to_string(), "list".to_string()])
                .expect("default_cli_runner must run");
            assert_eq!(out, "INST=1\n", "inst={inst:?} leaked ambient NETSIM_INSTANCE=7");
        }
        return;
    }

    let child = std::process::Command::new(std::env::current_exe().unwrap())
        .env("NETSIM_INSTANCE", "7")
        .env("ERM_SUBTEST_NETSIM_INST_OVERRIDE", "1")
        .args([
            "--exact",
            "netsim::pcap::tests::test_pcap_cli_overrides_ambient_netsim_instance_and_cleanup_revalidates_ini",
            "--nocapture",
        ])
        .output()
        .expect("must run child subtest");
    let child_stdout = String::from_utf8_lossy(&child.stdout);
    assert!(
        child.status.success(),
        "ambient NETSIM_INSTANCE subtest failed: stdout={child_stdout} stderr={}",
        String::from_utf8_lossy(&child.stderr)
    );
    // A name that matches no test also exits successfully.
    assert!(child_stdout.contains("test result: ok. 1 passed"), "{child_stdout}");

    // Shutdown `cleanup` (`restore_captures_on_exit`) must re-verify `is_trusted_ini_path`
    // and skip `capture list` / `capture patch off` if `/tmp/netsim_*.ini` was unlinked or
    // replaced by a symlink before shutdown.
    let tmp = tempfile::tempdir().unwrap();
    let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
    let sys_ini =
        tempfile::Builder::new().prefix("netsim_").suffix(".ini").tempfile_in("/tmp").unwrap();
    let sys_ini_path = sys_ini.path().to_path_buf();
    std::fs::write(&sys_ini_path, "grpc.port=43929\npid=5001\n").unwrap();
    daemon.ini_path = Some(sys_ini_path.clone());
    daemon.grpc_port = Some(43929);

    let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
    let calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Vec<String>>::new()));
    let calls_clone = calls.clone();
    mgr.cli_runner = std::sync::Arc::new(move |_, args| {
        calls_clone.lock().unwrap().push(args.to_vec());
        Some(
            "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
             1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                .to_string(),
        )
    });
    let joined = [EmulatorNetsimRecord {
        storage_key: "Phone_emulator-5554".to_string(),
        serial: "emulator-5554".to_string(),
        device_name: Some("Phone".to_string()),
        state: "CONNECTED".to_string(),
        daemon_key: Some(daemon.daemon_key),
        ..Default::default()
    }];
    let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
    mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
    assert!(mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 1)));
    calls.lock().unwrap().clear();

    // Unlink INI in sticky `/tmp`: `NotFound` inside root-owned `/tmp` fails
    // `is_trusted_ini_path`, so `cleanup` must skip CLI invocations completely.
    drop(sys_ini);
    assert!(!sys_ini_path.exists());
    let mut daemons = BTreeMap::from([(daemon.daemon_key, daemon)]);
    let emu_recs = BTreeMap::from([(joined[0].storage_key.clone(), joined[0].clone())]);
    mgr.cleanup(&mut daemons, &emu_recs);
    assert!(
        calls.lock().unwrap().is_empty(),
        "cleanup invoked CLI after /tmp INI was deleted: {:?}",
        *calls.lock().unwrap()
    );
    // The summary says which capture the monitor could not turn off again.
    assert_eq!(
        mgr.skipped_notes,
        ["Left capture on for 'Phone' (chip 1): netsim INI is missing or no longer trusted"]
    );
}
