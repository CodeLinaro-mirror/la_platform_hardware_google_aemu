use super::*;

#[test]
fn test_parse_proc_locks_double_spaces() {
    let sample = "60: FLOCK  ADVISORY  WRITE 1859832 00:2b:99248 0 EOF\n\
                  61: POSIX  ADVISORY  WRITE 999 00:1a:12345 0 EOF\n";
    let holders = parse_proc_locks(sample);
    assert_eq!(holders.get("00:2b:99248"), Some(&1859832));
    assert!(!holders.contains_key("00:1a:12345"));
}

#[test]
fn test_parse_proc_locks_waiter() {
    let sample = "60: FLOCK  ADVISORY  WRITE 100 00:2b:99248 0 EOF\n\
                  61: -> FLOCK  ADVISORY  WRITE 200 00:2b:99248 0 EOF\n";
    let holders = parse_proc_locks(sample);
    assert_eq!(holders.get("00:2b:99248"), Some(&100));
}

#[test]
fn test_parse_netsim_ini_skips_token() {
    let dir = tempfile::tempdir().unwrap();
    let ini_path = dir.path().join("netsim.ini");
    std::fs::write(
        &ini_path,
        "grpc.port=43929\nhci.port=6402\npid=1859832\ngrpc.token=secret123\n",
    )
    .unwrap();
    let (data, meta) = read_netsim_ini(&ini_path);
    assert_eq!(data.get("grpc.port").map(|s| s.as_str()), Some("43929"));
    assert_eq!(data.get("pid").map(|s| s.as_str()), Some("1859832"));
    assert!(!data.contains_key("grpc.token"));
    // The metadata describes the file the keys were read from.
    assert_eq!(meta.map(|m| m.len()), std::fs::metadata(&ini_path).ok().map(|m| m.len()));

    // Only the key marks a secret: an AVD name or path may contain the word.
    let pid_ini = parse_netsim_ini_str(
        "# written by the emulator = yes\navd.name=Token_Phone\navd.id = token_phone \n\
         GRPC.Token.Path=/tmp/secret\nport.serial=5554\n",
    );
    assert_eq!(
        pid_ini,
        env_of(&[("avd.name", "Token_Phone"), ("avd.id", "token_phone"), ("port.serial", "5554")])
    );
}

pub(crate) fn env_of(pairs: &[(&str, &str)]) -> HashMap<String, String> {
    pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
}

fn paths(items: &[&str]) -> Vec<PathBuf> {
    items.iter().map(PathBuf::from).collect()
}

#[test]
fn test_get_netsim_discovery_dirs_linux() {
    let env = env_of(&[
        ("ANDROID_EMULATOR_DISCOVERY_DIR", "/disc"),
        ("TMPDIR", "/tmpdir"),
        ("XDG_RUNTIME_DIR", "/run/user/1000"),
        ("ANDROID_TMP", "/android_tmp"),
        ("HOME", "/home/alice"),
        ("LOCALAPPDATA", "/ignored"),
    ]);
    assert_eq!(
        get_netsim_discovery_dirs(&env, HostOs::Linux),
        paths(&["/disc", "/tmpdir", "/run/user/1000", "/tmp", "/android_tmp"])
    );
}

#[test]
fn test_get_netsim_discovery_dirs_macos() {
    // netsim appends Library/Caches/TemporaryItems to $HOME, or to /tmp.
    let env = env_of(&[("TMPDIR", "/var/folders/xy/T/"), ("HOME", "/Users/alice")]);
    assert_eq!(
        get_netsim_discovery_dirs(&env, HostOs::MacOs),
        paths(&[
            "/var/folders/xy/T/",
            "/Users/alice/Library/Caches/TemporaryItems",
            "/tmp/Library/Caches/TemporaryItems",
        ])
    );
}

#[test]
fn test_get_netsim_discovery_dirs_windows() {
    // netsim uses %LOCALAPPDATA%\Temp and never reads TEMP or TMP.
    let local = r"C:\Users\alice\AppData\Local";
    let env = env_of(&[
        ("LOCALAPPDATA", local),
        ("TEMP", r"C:\Temp"),
        ("TMP", r"C:\Tmp"),
        ("ANDROID_TMP", r"D:\android_tmp"),
    ]);
    assert_eq!(
        get_netsim_discovery_dirs(&env, HostOs::Windows),
        vec![
            PathBuf::from(local).join("Temp"),
            PathBuf::from("/tmp").join("Temp"),
            PathBuf::from(r"D:\android_tmp"),
        ]
    );
}

#[test]
fn test_get_netsim_discovery_dirs_skips_empty_and_duplicate_values() {
    let env = env_of(&[("TMPDIR", ""), ("XDG_RUNTIME_DIR", "/tmp"), ("ANDROID_TMP", "/tmp/")]);
    assert_eq!(get_netsim_discovery_dirs(&env, HostOs::Linux), paths(&["/tmp"]));
}

#[test]
fn test_host_discovery_dirs_adds_run_user_on_linux_only() {
    let empty = HashMap::new();
    assert_eq!(
        host_discovery_dirs(&empty, HostOs::Linux, Some(4242)),
        paths(&["/tmp", "/run/user/4242"])
    );
    assert_eq!(host_discovery_dirs(&empty, HostOs::Linux, None), paths(&["/tmp"]));
    assert_eq!(
        host_discovery_dirs(&empty, HostOs::MacOs, Some(501)),
        vec![PathBuf::from("/tmp").join("Library/Caches/TemporaryItems")]
    );
    assert_eq!(
        host_discovery_dirs(&empty, HostOs::Windows, Some(1)),
        vec![PathBuf::from("/tmp").join("Temp")]
    );
    // XDG_RUNTIME_DIR usually is /run/user/<uid> already; it is listed once.
    let xdg = env_of(&[("XDG_RUNTIME_DIR", "/run/user/1000")]);
    assert_eq!(
        host_discovery_dirs(&xdg, HostOs::Linux, Some(1000)),
        paths(&["/run/user/1000", "/tmp"])
    );
}

#[test]
fn test_host_os_current_matches_build_target() {
    let expected = match std::env::consts::OS {
        "macos" => HostOs::MacOs,
        "windows" => HostOs::Windows,
        _ => HostOs::Linux,
    };
    assert_eq!(HostOs::current(), expected);
}

#[test]
fn test_parse_env_entries_drops_tokens_and_empty_keys() {
    // `=C:=C:\work` is a Windows per-drive working directory entry.
    let env = parse_env_entries(["PATH=/bin", r"=C:=C:\work", "GH_TOKEN=x", "OPTS=a=b", "BARE"]);
    assert_eq!(env, env_of(&[("PATH", "/bin"), ("OPTS", "a=b")]));
}

#[test]
fn test_current_host_env_reads_process_env() {
    assert_eq!(current_host_env().get("PATH"), std::env::var("PATH").ok().as_ref());
}

#[test]
fn test_resolve_netsim_artifact_dir_windows() {
    let dir = |pairs: &[(&str, &str)]| {
        resolve_netsim_artifact_dir_for_os(&env_of(pairs), HostOs::Windows)
    };
    let under = |base: &str| PathBuf::from(base).join("netsimd");
    // GetTempPath() order is TMP, TEMP, USERPROFILE, then the Windows dir.
    // It is already per-user, so there is no android-$USER level.
    assert_eq!(dir(&[("TEMP", r"C:\T"), ("USER", "alice")]), under(r"C:\T"));
    assert_eq!(dir(&[("TEMP", r"C:\T"), ("TMP", r"C:\M")]), under(r"C:\M"));
    assert_eq!(dir(&[("TMP", ""), ("TEMP", r"C:\T")]), under(r"C:\T"));
    assert_eq!(dir(&[("USERPROFILE", r"C:\Users\alice")]), under(r"C:\Users\alice"));
    assert_eq!(dir(&[("SystemRoot", r"C:\WINDOWS")]), under(r"C:\WINDOWS"));
    assert_eq!(dir(&[]), under(r"C:\Windows"));
    assert_eq!(dir(&[("ANDROID_TMP", r"D:\A"), ("TMP", r"C:\M")]), under(r"D:\A"));
}

#[test]
fn test_resolve_netsim_artifact_dir_unix() {
    for os in [HostOs::Linux, HostOs::MacOs] {
        let dir = |pairs: &[(&str, &str)]| resolve_netsim_artifact_dir_for_os(&env_of(pairs), os);
        assert_eq!(
            dir(&[("TMPDIR", "/var/tmp"), ("USER", "alice")]),
            PathBuf::from("/var/tmp/android-alice/netsimd")
        );
        assert_eq!(
            dir(&[("ANDROID_TMP", "/a"), ("TMPDIR", "/var/tmp"), ("USER", "alice")]),
            PathBuf::from("/a/android-alice/netsimd")
        );
        // temp_dir() ignores TEMP and TMP off Windows; an empty USER is unset.
        assert_eq!(
            dir(&[("TEMP", "/ignored"), ("TMP", "/ignored"), ("USER", "")]),
            PathBuf::from("/tmp/android/netsimd")
        );
    }
}

#[test]
fn test_resolve_netsim_artifact_dir_falls_back_to_host_env() {
    // An unreadable (empty) daemon env resolves from the monitor's env.
    let host = || env_of(&[("TMPDIR", "/host_tmp"), ("USER", "bob")]);
    assert_eq!(
        resolve_netsim_artifact_dir(&HashMap::new(), host, HostOs::Linux),
        PathBuf::from("/host_tmp/android-bob/netsimd")
    );
    // A readable daemon env is used as-is, without reading the host env.
    assert_eq!(
        resolve_netsim_artifact_dir(
            &env_of(&[("USER", "alice")]),
            || panic!("host env must not be read"),
            HostOs::Linux
        ),
        PathBuf::from("/tmp/android-alice/netsimd")
    );
}

#[test]
fn test_extract_ini_instance_num() {
    let num = |name: &str| extract_ini_instance_num(Path::new(name));
    assert_eq!(num("netsim.ini"), Some(DEFAULT_NETSIM_INSTANCE));
    assert_eq!(num("/tmp/netsim_2.ini"), Some(2));
    assert_eq!(num("netsim_10.ini"), Some(10));
    for other in [
        "netsim.ini.lock",
        "netsim_2.ini.lock",
        "netsim_2.ini.init.lock",
        "netsim_.ini",
        "netsim_+3.ini",
        "netsim_x.ini",
        "netsim_99999999999.ini",
        "pid_1234.ini",
        "netsim",
    ] {
        assert_eq!(num(other), None, "{other}");
    }
}

#[test]
fn test_parse_proc_start_time() {
    // Field 19 after `(comm)` is starttime in 100 Hz ticks (250 ticks = 2.5s after boot).
    let stat = "1234 (qemu-system (x86)) S 1 1234 1234 0 -1 4194304 0 0 0 0 10 5 0 0 20 0 4 0 250 1000 100";
    let start = parse_proc_start_time(Some("100.00 200.00\n"), Some(stat), 1000.0).unwrap();
    assert!((start - 902.5).abs() < 1e-6);
    assert_eq!(parse_proc_start_time(None, Some(stat), 1000.0), None);
    assert_eq!(parse_proc_start_time(Some("100.0"), Some("malformed"), 1000.0), None);
}

#[cfg(unix)]
#[test]
fn test_ini_stats_and_log_readers_reject_fifos_symlinks_and_oversized_files() {
    let dir = tempfile::tempdir().unwrap();
    let target_ini = dir.path().join("real_target.conf");
    std::fs::write(&target_ini, "grpc.port=42424\npid=1234\n").unwrap();

    let sym_ini = dir.path().join("netsim_2.ini");
    std::os::unix::fs::symlink(&target_ini, &sym_ini).unwrap();
    let (keys, meta) = read_netsim_ini(&sym_ini);
    assert!(keys.is_empty() && meta.is_none(), "symlink INI must be rejected");

    let avd_dir = dir.path().join("avd").join("running");
    std::fs::create_dir_all(&avd_dir).unwrap();
    let sym_pid_ini = avd_dir.join("pid_424242.ini");
    std::os::unix::fs::symlink(&target_ini, &sym_pid_ini).unwrap();
    let env = env_of(&[("ANDROID_EMULATOR_DISCOVERY_DIR", dir.path().to_str().unwrap())]);
    assert!(
        discover_emulator_pid_ini(424242, &env, None).is_empty(),
        "symlink pid_<pid>.ini must be rejected"
    );

    let huge_ini = dir.path().join("netsim_4.ini");
    std::fs::write(&huge_ini, format!("grpc.port=43929\n#{}", "x".repeat(70 * 1024))).unwrap();
    let (keys, meta) = read_netsim_ini(&huge_ini);
    assert!(keys.is_empty(), "oversized INI (>64 KiB) must be rejected");
    // It contributes no keys, but still says who wrote it and when.
    assert!(meta.is_some_and(|m| m.len() > MAX_INI_BYTES as u64));

    let fifo_ini = dir.path().join("netsim_3.ini");
    let mkfifo_ok = std::process::Command::new("mkfifo").arg(&fifo_ini).status().unwrap().success();
    assert!(mkfifo_ok);
    let fifo_clone = fifo_ini.clone();
    let (tx, rx) = std::sync::mpsc::channel();
    let reader = std::thread::spawn(move || {
        let (parsed, _) = read_netsim_ini(&fifo_clone);
        let _ = tx.send(parsed);
    });
    let recv_res = rx.recv_timeout(std::time::Duration::from_millis(200));
    if recv_res.is_err() {
        let _ = std::fs::OpenOptions::new().write(true).open(&fifo_ini);
    }
    reader.join().unwrap();
    assert_eq!(recv_res, Ok(HashMap::new()), "FIFO INI blocked or parsed data");

    // Symlinked session stats and stderr log files must also be refused.
    let real_stats = dir.path().join("real_stats.json");
    std::fs::write(
        &real_stats,
        r#"{"version":"1.0.31","device_stats":[{"device_id":1}],"radio_stats":[{"device_id":1,"kind":"WIFI","duration_secs":"5"}]}"#,
    )
    .unwrap();
    let sym_stats = dir.path().join("netsim_session_stats.json");
    std::os::unix::fs::symlink(&real_stats, &sym_stats).unwrap();
    let mut cache = None;
    assert_eq!(resolve_session_stats_chips(&sym_stats, &[], 1, 0.0, &mut cache), (0, None));

    let real_log = dir.path().join("real.log");
    std::fs::write(&real_log, "netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 43929\n")
        .unwrap();
    let sym_log = dir.path().join("netsim_stderr.log");
    std::os::unix::fs::symlink(&real_log, &sym_log).unwrap();
    let mut tailer = NetsimLogTailer::new(sym_log);
    assert!(!tailer.poll(100.0), "symlinked netsim_stderr.log must be rejected");
    assert_eq!(tailer.grpc_port_logged, None);

    assert_eq!(get_file_lock_key(&sym_ini), None);
    assert_eq!(get_file_lock_key(&fifo_ini), None);

    let out_dir = dir.path().join("out");
    std::fs::create_dir_all(&out_dir).unwrap();
    let mut tracker = NetsimTracker::new(None, 256, true, &out_dir);
    tracker.custom_ini_dirs = vec![dir.path().to_path_buf()];
    tracker.pid_alive = |_| false;
    tracker.update(&[], &HashMap::new(), &[], &HashMap::new(), 100.0, 100.0);
    assert!(!tracker.stale_inis.contains_key(&sym_ini));
    assert!(!tracker.stale_inis.contains_key(&fifo_ini));
    assert!(!tracker.stale_inis.contains_key(&huge_ini));
}

#[cfg(unix)]
#[test]
fn test_discover_emulator_pid_ini_enforces_real_dir_and_uid_ownership() {
    let uid = owner_uid().expect("owner_uid must resolve on Unix");
    let foreign_uid = uid.wrapping_add(1).max(1);
    let pid = 5_009_123u32;

    let tmp = tempfile::tempdir().unwrap();
    let shared_tmp = tmp.path().join("shared_tmp");
    let running = shared_tmp.join("avd").join("running");
    std::fs::create_dir_all(&running).unwrap();
    std::fs::write(
        running.join(format!("pid_{pid}.ini")),
        "port.serial=5554\navd.id=Pixel_8\navd.name=Pixel 8\nnetsim.endpoint=localhost:43929\n",
    )
    .unwrap();

    let env = env_of(&[("ANDROID_EMULATOR_DISCOVERY_DIR", shared_tmp.to_str().unwrap())]);
    // Same-UID monitor accepts real `avd/running/pid_<pid>.ini`.
    let ok =
        discover_emulator_pid_ini_with(pid, &env, Vec::new(), HostOs::Linux, Some(uid), Some(uid));
    assert_eq!(ok.get("avd.id").map(String::as_str), Some("Pixel_8"));
    assert_eq!(ok.get("netsim.endpoint").map(String::as_str), Some("localhost:43929"));

    // Foreign-UID monitor rejects unowned `avd/running/pid_<pid>.ini` (prevents `/tmp` spoofing).
    assert!(
        discover_emulator_pid_ini_with(
            pid,
            &env,
            Vec::new(),
            HostOs::Linux,
            Some(foreign_uid),
            Some(foreign_uid),
        )
        .is_empty()
    );
    // Unknown UID on Unix fails closed.
    assert!(
        discover_emulator_pid_ini_with(pid, &env, Vec::new(), HostOs::Linux, None, None).is_empty()
    );
    // Root (`sudo`) monitor (`Some(0)`) accepts files matching target worker `expected_uid`
    // (`Some(uid)`), while rejecting third-party spoofed files (`expected_uid = Some(foreign_uid)`).
    let root_dev_ok =
        discover_emulator_pid_ini_with(pid, &env, Vec::new(), HostOs::Linux, Some(0), Some(uid));
    assert_eq!(root_dev_ok.get("avd.id").map(String::as_str), Some("Pixel_8"));
    assert!(
        discover_emulator_pid_ini_with(
            pid,
            &env,
            Vec::new(),
            HostOs::Linux,
            Some(0),
            Some(foreign_uid),
        )
        .is_empty(),
        "sudo root monitor must reject pid_<pid>.ini whose owner mismatches target worker UID"
    );
    let root_unknown_ok =
        discover_emulator_pid_ini_with(pid, &env, Vec::new(), HostOs::Linux, Some(0), None);
    assert_eq!(root_unknown_ok.get("avd.id").map(String::as_str), Some("Pixel_8"));

    // Untrusted symlink base directory (`TMPDIR` -> symlink owned by non-expected user)
    // is rejected via full ancestor validation even when `avd` and `running` are real dirs.
    let sym_root_base = tmp.path().join("sym_discovery_root");
    std::os::unix::fs::symlink(&shared_tmp, &sym_root_base).unwrap();
    let env_sym_root =
        env_of(&[("ANDROID_EMULATOR_DISCOVERY_DIR", sym_root_base.to_str().unwrap())]);
    assert_eq!(
        discover_emulator_pid_ini_with(
            pid,
            &env_sym_root,
            Vec::new(),
            HostOs::Linux,
            Some(uid),
            Some(uid),
        )
        .get("avd.id")
        .map(String::as_str),
        Some("Pixel_8")
    );
    assert!(
        discover_emulator_pid_ini_with(
            pid,
            &env_sym_root,
            Vec::new(),
            HostOs::Linux,
            Some(0),
            None,
        )
        .is_empty(),
        "non-root symlink ancestor must be rejected when expected_uid is None"
    );

    // Symlinked `<dir>/avd` or `<dir>/avd/running` is rejected even for same-UID and root.
    let sym_base = tmp.path().join("sym_avd_base");
    std::fs::create_dir_all(&sym_base).unwrap();
    std::os::unix::fs::symlink(shared_tmp.join("avd"), sym_base.join("avd")).unwrap();
    let env_sym_avd = env_of(&[("ANDROID_EMULATOR_DISCOVERY_DIR", sym_base.to_str().unwrap())]);
    assert!(
        discover_emulator_pid_ini_with(
            pid,
            &env_sym_avd,
            Vec::new(),
            HostOs::Linux,
            Some(uid),
            Some(uid),
        )
        .is_empty()
    );
    assert!(
        discover_emulator_pid_ini_with(
            pid,
            &env_sym_avd,
            Vec::new(),
            HostOs::Linux,
            Some(0),
            Some(uid),
        )
        .is_empty()
    );

    let sym_run_base = tmp.path().join("sym_run_base");
    std::fs::create_dir_all(sym_run_base.join("avd")).unwrap();
    std::os::unix::fs::symlink(&running, sym_run_base.join("avd").join("running")).unwrap();
    let env_sym_run = env_of(&[("ANDROID_EMULATOR_DISCOVERY_DIR", sym_run_base.to_str().unwrap())]);
    assert!(
        discover_emulator_pid_ini_with(
            pid,
            &env_sym_run,
            Vec::new(),
            HostOs::Linux,
            Some(uid),
            Some(uid),
        )
        .is_empty()
    );
}

#[cfg(unix)]
#[test]
fn test_discover_emulator_pid_ini_looks_under_the_workers_uid() {
    let uid = owner_uid().expect("owner_uid must resolve on Unix");
    let pid = 5_009_124u32;
    // Stands in for `/run/user/<uid>` of the user the worker runs as.
    let tmp = tempfile::tempdir().unwrap();
    let running = tmp.path().join("avd").join("running");
    std::fs::create_dir_all(&running).unwrap();
    std::fs::write(running.join(format!("pid_{pid}.ini")), "avd.id=Pixel_9\n").unwrap();

    let asked = std::cell::Cell::new(None);
    let no_env = HashMap::new();
    // A root monitor (sudo) watching the user's emulator must not look under its own uid.
    let found =
        discover_emulator_pid_ini_for(pid, &no_env, Some(uid), HostOs::Linux, Some(0), |u| {
            asked.set(Some(u));
            vec![tmp.path().to_path_buf()]
        });
    assert_eq!(asked.get(), Some(Some(uid)));
    assert_eq!(found.get("avd.id").map(String::as_str), Some("Pixel_9"));

    // A same-user monitor passes no expected uid: its own uid is the worker's.
    discover_emulator_pid_ini_for(pid, &no_env, None, HostOs::Linux, Some(uid), |u| {
        asked.set(Some(u));
        Vec::new()
    });
    assert_eq!(asked.get(), Some(Some(uid)));
    // Root watching a worker whose uid is unknown has only its own uid to go by.
    discover_emulator_pid_ini_for(pid, &no_env, None, HostOs::Linux, Some(0), |u| {
        asked.set(Some(u));
        Vec::new()
    });
    assert_eq!(asked.get(), Some(Some(0)));
}

#[test]
fn test_extract_netsim_instance_num_mirrors_netsim_get_instance() {
    let num = |toks: &[&str], env: &[(&str, &str)]| {
        let cmd: Vec<String> = toks.iter().map(|t| t.to_string()).collect();
        extract_netsim_instance_num(&cmd, &env_of(env))
    };
    assert_eq!(num(&["netsimd"], &[]), None);
    assert_eq!(num(&["netsimd", "-i", "3"], &[]), Some(3));
    assert_eq!(num(&["netsimd", "-i", "+3"], &[]), Some(3));
    assert_eq!(num(&["netsimd", "--instance", "4"], &[]), Some(4));
    assert_eq!(num(&["netsimd", "--instance=5"], &[]), Some(5));
    // The environment wins over the flag.
    assert_eq!(num(&["netsimd", "-i", "3"], &[("NETSIM_INSTANCE", "2")]), Some(2));
    assert_eq!(num(&["netsimd", "-i"], &[("NETSIM_INSTANCE", "65535")]), Some(65535));
    // netsim skips a source that holds 0 or no u16.
    for bad in ["0", "65536", "-1", " 2", "x", ""] {
        assert_eq!(num(&["netsimd", "-i", "3"], &[("NETSIM_INSTANCE", bad)]), Some(3), "{bad:?}");
        assert_eq!(num(&["netsimd", "-i", bad], &[]), None, "{bad:?}");
        assert_eq!(num(&["netsimd", &format!("--instance={bad}")], &[]), None, "{bad:?}");
    }
}
