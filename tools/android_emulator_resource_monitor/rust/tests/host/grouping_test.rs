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
    std::fs::write(running.join("pid_4000052.ini"), "port.serial=5554\navd.id=Pixel_8\n").unwrap();
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
        parse_proc_status_uids("Name:\temulator_mon\nDumpable:\t0\nUid:\t1000\t1000\t1000\t1000\n"),
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
