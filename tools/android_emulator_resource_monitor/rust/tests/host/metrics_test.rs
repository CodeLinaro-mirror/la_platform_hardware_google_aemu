use super::*;

#[test]
fn test_cpu_percent_from_accumulated_cpu_time() {
    // A process seen for the first time (or a reused PID, which has a new
    // start time and so a new key) has no rate yet.
    assert_eq!(cpu_percent(None, 50_000, Some(2.0)), 0.0);
    // 1 s of CPU over 2 s of wall time: half a core.
    assert_eq!(cpu_percent(Some(10_000), 11_000, Some(2.0)), 50.0);
    // 3 cores busy for 1 s.
    assert_eq!(cpu_percent(Some(0), 3_000, Some(1.0)), 300.0);
    // No previous sample time, a zero interval or a counter that went
    // backwards never produce garbage.
    assert_eq!(cpu_percent(Some(0), 1_000, None), 0.0);
    assert_eq!(cpu_percent(Some(0), 1_000, Some(0.0)), 0.0);
    assert_eq!(cpu_percent(Some(2_000), 1_000, Some(1.0)), 0.0);
}

#[test]
fn test_parse_diskstats_counts_whole_physical_disks_only() {
    let sys_block = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(sys_block.path().join("sda/device")).unwrap();
    std::fs::create_dir_all(sys_block.path().join("nvme0n1/device")).unwrap();
    std::fs::create_dir_all(sys_block.path().join("dm-0")).unwrap();
    // Fields: major minor name reads merged sectors_read ms writes merged
    // sectors_written ...; LVM's dm-0 sits on sda1, so only whole disks count.
    let text = "   8       0 sda 100 0 2000 0 50 0 4000 0 0 0 0\n\
                   8       1 sda1 90 0 1800 0 40 0 3000 0 0 0 0\n\
                 259       0 nvme0n1 10 0 100 0 5 0 300 0 0 0 0\n\
                 253       0 dm-0 90 0 1800 0 40 0 3000 0 0 0 0\n\
                   7       0 loop0 1 0 8 0 0 0 0 0 0 0 0\n\
                 garbage\n";
    let (read, written) = parse_diskstats(text, |dev| is_physical_disk(sys_block.path(), dev));
    assert_eq!(read, (2000 + 100) * 512);
    assert_eq!(written, (4000 + 300) * 512);
    assert_eq!(parse_diskstats("", |_| true), (0, 0));
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
fn test_compute_memory_stats() {
    let gib = 1024 * 1024 * 1024;
    let stats = compute_memory_stats(4 * gib, 16 * gib, gib, 4 * gib);
    assert!((stats.ram_used_gb - 4.0).abs() < 1e-6);
    assert!((stats.ram_total_gb - 16.0).abs() < 1e-6);
    assert!((stats.ram_percent - 25.0).abs() < 1e-4);
    assert!((stats.swap_used_gb - 1.0).abs() < 1e-6);
    assert!((stats.swap_total_gb - 4.0).abs() < 1e-6);
    assert!((stats.swap_percent - 25.0).abs() < 1e-4);

    let no_swap = compute_memory_stats(0, 0, 0, 0);
    assert_eq!(no_swap.ram_percent, 0.0);
    assert_eq!(no_swap.swap_percent, 0.0);
}

#[test]
fn test_disk_counter_rate_and_windows_counters() {
    let mib = 1024 * 1024;
    assert_eq!(disk_counter_rate_mbs(10 * mib, 0, Some(2.0)), 0.0);
    assert!((disk_counter_rate_mbs(10 * mib, 2 * mib, Some(2.0)) - 4.0).abs() < 1e-6);
    assert_eq!(disk_counter_rate_mbs(2 * mib, 10 * mib, Some(2.0)), 0.0);
    assert_eq!(disk_counter_rate_mbs(10 * mib, 2 * mib, Some(0.0)), 0.0);
    assert_eq!(disk_counter_rate_mbs(10 * mib, 2 * mib, None), 0.0);

    let usages = [
        sysinfo::DiskUsage {
            total_read_bytes: 4 * mib,
            read_bytes: mib,
            total_written_bytes: 6 * mib,
            written_bytes: mib,
        },
        sysinfo::DiskUsage {
            total_read_bytes: 6 * mib,
            read_bytes: mib,
            total_written_bytes: 2 * mib,
            written_bytes: mib,
        },
    ];
    let (tot_r, tot_w) = sum_disk_usage_counters(usages);
    assert_eq!(tot_r, 10 * mib);
    assert_eq!(tot_w, 8 * mib);
    assert!((disk_counter_rate_mbs(tot_r, 6 * mib, Some(2.0)) - 2.0).abs() < 1e-6);
    assert!((disk_counter_rate_mbs(tot_w, 0, Some(2.0)) - 0.0).abs() < 1e-6);
    let mut disks = sysinfo::Disks::new();
    let (r1, w1) = read_sysinfo_disk_counters(&mut disks);
    let mounts_1: Vec<_> = disks.list().iter().map(|d| d.mount_point().to_path_buf()).collect();
    let (r2, w2) = read_sysinfo_disk_counters(&mut disks);
    let mounts_2: Vec<_> = disks.list().iter().map(|d| d.mount_point().to_path_buf()).collect();
    if mounts_1 == mounts_2 {
        assert!(
            r2 >= r1 && w2 >= w1,
            "cumulative disk counters regressed on unchanged mounts: ({r1}, {w1}) -> ({r2}, {w2})"
        );
    }
    assert!(disk_counter_rate_mbs(r2, r1, Some(1.0)) >= 0.0);
    assert!(disk_counter_rate_mbs(w2, w1, Some(1.0)) >= 0.0);
}

/// Blocks until the probe started by the last `poll` has finished, so that the
/// next `poll` records its outcome.
fn wait_for_probe<T>(cache: &TimedProbeCache<T>) {
    while cache.probe.as_ref().is_some_and(|probe| !probe.is_finished()) {
        std::thread::yield_now();
    }
}

#[test]
fn test_timed_gpu_cache_retries_after_probe_panic() {
    let mut gpu_cache = TimedProbeCache::<f32> { reading: Some(12.5), ..Default::default() };
    let interval = std::time::Duration::from_secs(5);

    let initial =
        gpu_cache.poll(interval, || -> Option<f32> { panic!("simulated GPU probe panic") });
    assert_eq!(initial, Some(12.5));
    wait_for_probe(&gpu_cache);
    assert_eq!(gpu_cache.poll(interval, || Some(0.0)), Some(12.5));
    assert!(!gpu_cache.failed);

    // Next poll after interval must be able to spawn a fresh probe and update the cache.
    gpu_cache.last_fetch = Some(std::time::Instant::now() - interval);
    let _ = gpu_cache.poll(interval, || Some(64.0));
    wait_for_probe(&gpu_cache);
    assert_eq!(gpu_cache.poll(interval, || Some(0.0)), Some(64.0));
}

#[test]
fn test_gpu_output_parsers() {
    let pmon = "# gpu        pid  type    sm   mem   enc   dec   command\n\
                # Idx          #   C/G     %     %     %     %   name\n\
                    0       1001     G    25    10     -     -   qemu-system-x86_64\n\
                    0       1001     C    15     5     -     -   qemu-system-x86_64\n\
                    0       1002     G     -     -     -     -   fishtank\n";
    let by_pid = parse_nvidia_pmon(pmon);
    assert_eq!(by_pid.get(&1001), Some(&40.0));
    assert_eq!(by_pid.get(&1002), Some(&0.0));

    assert_eq!(parse_intel_gpu_top("Render/3D: 42%\n"), Some(42.0));
    assert_eq!(parse_intel_gpu_top("no match"), None);

    assert_eq!(
        parse_mac_powermetrics_gpu("GPU HW active residency:  37.50% (100 MHz)\n"),
        Some(37.5)
    );
    assert_eq!(parse_mac_powermetrics_gpu("GPU active residency: 12.0%\n"), Some(12.0));
    assert_eq!(parse_mac_powermetrics_gpu("no match"), None);
}

#[test]
fn test_thermal_paging_and_battery_helpers() {
    let dir = tempfile::tempdir().unwrap();
    let z0 = dir.path().join("thermal_zone0");
    let z1 = dir.path().join("thermal_zone1");
    let z2 = dir.path().join("thermal_zone2");
    std::fs::create_dir_all(&z0).unwrap();
    std::fs::create_dir_all(&z1).unwrap();
    std::fs::create_dir_all(&z2).unwrap();
    std::fs::write(z0.join("temp"), "45000\n").unwrap();
    std::fs::write(z1.join("temp"), "62500\n").unwrap();
    std::fs::write(z2.join("temp"), "200000\n").unwrap(); // bogus > 150 C ignored
    assert_eq!(read_max_thermal_temp(dir.path()), Some(62.5));

    let initial_paging = compute_paging_stats(Some((100, 200)), 0, 0, 2.0, "KB/s");
    assert_eq!(initial_paging.display, "Calculating...");
    let next_paging = compute_paging_stats(Some((300, 260)), 100, 200, 2.0, "KB/s");
    assert_eq!(next_paging.display, "In: 100 / Out: 30 (KB/s)");
    assert!((next_paging.in_rate - 100.0).abs() < 1e-6);
    assert!((next_paging.out_rate - 30.0).abs() < 1e-6);

    let pwr = tempfile::tempdir().unwrap();
    let bat0 = pwr.path().join("BAT0");
    std::fs::create_dir_all(&bat0).unwrap();
    std::fs::write(bat0.join("capacity"), "85\n").unwrap();
    std::fs::write(bat0.join("status"), "Discharging\n").unwrap();
    let bat = read_linux_battery(pwr.path()).unwrap();
    assert_eq!(bat.display, "85% (Discharging)");
    assert_eq!(bat.percent, Some(85.0));

    let pmset = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t78%; discharging; 3:12 remaining\n";
    let mac_bat = parse_pmset_battery(pmset).unwrap();
    assert_eq!(mac_bat.display, "78% (Discharging)");
    assert_eq!(mac_bat.percent, Some(78.0));
}

#[test]
fn test_read_max_thermal_temp_ignores_non_positive_dummy_zones() {
    let dir = tempfile::tempdir().unwrap();
    let z0 = dir.path().join("thermal_zone0");
    let z1 = dir.path().join("thermal_zone1");
    std::fs::create_dir_all(&z0).unwrap();
    std::fs::create_dir_all(&z1).unwrap();
    std::fs::write(z0.join("temp"), "0\n").unwrap();
    std::fs::write(z1.join("temp"), "-273200\n").unwrap();
    assert_eq!(read_max_thermal_temp(dir.path()), None);
}

/// Writes `<drm>/<card>/device/gpu_busy_percent` holding `busy`.
fn write_drm_busy(drm: &std::path::Path, card: &str, busy: &str) {
    let device = drm.join(card).join("device");
    std::fs::create_dir_all(&device).unwrap();
    std::fs::write(device.join("gpu_busy_percent"), busy).unwrap();
}

#[test]
fn test_nvidia_gpu_takes_precedence_over_drm_sysfs() {
    let dir = tempfile::tempdir().unwrap();
    write_drm_busy(dir.path(), "card0", "0\n");

    let (pct, mem) = resolve_nvidia_or_drm_gpu(dir.path(), || (Some(65.0), Some(4096.0)));
    assert_eq!((pct, mem), (Some(65.0), Some(4096.0)));

    let (fallback_pct, fallback_mem) = resolve_nvidia_or_drm_gpu(dir.path(), || (None, None));
    assert_eq!((fallback_pct, fallback_mem), (Some(0.0), None));
}

#[test]
fn test_drm_gpu_busy_comes_from_the_first_card_that_reports_it() {
    // card0 (an iGPU without the node) and its connector are skipped; card1 reports.
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(dir.path().join("card0").join("device")).unwrap();
    write_drm_busy(dir.path(), "card0-DP-1", "99");
    write_drm_busy(dir.path(), "card1", "42\n");
    write_drm_busy(dir.path(), "card2", "7");
    assert_eq!(read_drm_gpu_busy(dir.path()), Some(42.0));

    // A garbled value is skipped like a missing one.
    write_drm_busy(dir.path(), "card1", "busy");
    assert_eq!(read_drm_gpu_busy(dir.path()), Some(7.0));
    assert_eq!(read_drm_gpu_busy(&dir.path().join("missing")), None);
}

#[test]
fn test_macos_process_refresh_enables_disk_usage_and_uses_sample_interval() {
    let mac_kind = process_refresh_kind_for_os(false, false);
    assert!(mac_kind.disk_usage(), "macOS ProcessRefreshKind must enable disk_usage");
    let linux_kind = process_refresh_kind_for_os(true, false);
    assert!(!linux_kind.disk_usage());

    let mib = 1024 * 1024;
    // First tick: sample_interval is 0.2s (baseline window), while elapsed_secs is ~0.0s.
    let (r, w) = process_disk_delta_rate_mbs(2 * mib, 4 * mib, Some(0.2), 0.0001);
    assert!((r - 10.0).abs() < 1e-6);
    assert!((w - 20.0).abs() < 1e-6);
}

#[cfg(target_os = "linux")]
#[test]
fn test_zombie_netsimd_is_not_a_candidate() {
    // An exited but unreaped child stays in the process table as a zombie
    // with no memory: it must not count as a running netsimd.
    let dir = tempfile::tempdir().unwrap();
    let fake_netsimd = dir.path().join("netsimd");
    std::fs::copy("/bin/true", &fake_netsimd).unwrap();
    let mut child = crate::test_support::retry_while_text_busy(|| {
        std::process::Command::new(&fake_netsimd).spawn()
    })
    .unwrap();
    let pid = sysinfo::Pid::from_u32(child.id());

    let mut sys = System::new();
    let mut saw_zombie = false;
    for _ in 0..500 {
        refresh_processes(&mut sys);
        if sys.process(pid).is_some_and(|p| p.status() == sysinfo::ProcessStatus::Zombie) {
            saw_zombie = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let listed = candidate_processes(&sys).any(|(p, _)| *p == pid);
    child.wait().unwrap();
    assert!(saw_zombie, "the child never showed up as a zombie");
    assert!(!listed, "a zombie netsimd was counted as running");
}

#[test]
fn test_paging_unsupported_counters_are_na() {
    let unsupported = compute_paging_stats(None, 100, 200, 2.0, "KB/s");
    assert_eq!(unsupported.display, "N/A");
    assert!(unsupported.in_rate.is_nan());
    assert!(unsupported.out_rate.is_nan());

    let first = compute_paging_stats(Some((100, 200)), 0, 0, 2.0, "KB/s");
    assert_eq!(first.display, "Calculating...");
}

#[test]
fn test_parse_mac_ioreg_gpu_idle_multi_entry_and_driver_mem_exclusion() {
    // Idle Apple Silicon M4 Pro: 0% utilization alongside In use system memory
    // AND In use system memory (driver), which must not be matched/double-counted.
    let idle_m4 = r#"
+-o AGXAcceleratorG16S  <class AGXAcceleratorG16S, id 0x1000004aa, registered, matched>
  {
    "model" = "Apple M4 Pro"
    "gpu-core-count" = 20
    "PerformanceStatistics" = {"Device Utilization %"=0,"Renderer Utilization %"=0,"Tiler Utilization %"=0,"In use system memory"=436862976,"In use system memory (driver)"=999999999,"Alloc system memory"=800000000}
  }
"#;
    let (pct, mem) = parse_mac_ioreg_gpu(idle_m4);
    assert_eq!(pct, Some(0.0));
    let expected_mib = (436_862_976_f64 / BYTES_PER_MIB) as f32;
    assert!((mem.unwrap() - expected_mib).abs() < 1e-3);

    // Multi-entry takes max utilization (clamped to 100) and sums In use or fallback Alloc memory.
    let multi = r#"
+-o Accel0
  {
    "PerformanceStatistics" = {"Device Utilization %"=12.5,"Renderer Utilization %"=45.0,"Tiler Utilization %"=18.0,"In use system memory"=104857600}
  }
+-o Accel1
  {
    "PerformanceStatistics" = {
      "Device Utilization %" = 120.0,
      "Alloc system memory" = 52428800,
      "In use system memory (driver)" = 999999999
    }
  }
"#;
    let (multi_pct, multi_mem) = parse_mac_ioreg_gpu(multi);
    assert_eq!(multi_pct, Some(100.0));
    assert!((multi_mem.unwrap() - 150.0).abs() < 1e-3);

    // Empty / malformed outputs return (None, None).
    assert_eq!(parse_mac_ioreg_gpu(""), (None, None));
    assert_eq!(parse_mac_ioreg_gpu("no performance stats here"), (None, None));
    assert_eq!(parse_mac_ioreg_gpu("\"PerformanceStatistics\" = no_braces"), (None, None));
}

#[test]
fn test_parse_mac_ioreg_gpu_models_scopes_entries_and_deduplicates() {
    let ioreg = r#"
+-o AGXAcceleratorG16S  <class AGXAcceleratorG16S>
  {
    "model" = "Apple M4 Pro"
    "gpu-core-count" = 20
  }
+-o AGXAcceleratorDuplicate  <class AGXAcceleratorG16S>
  {
    "model" = "Apple M4 Pro"
    "gpu-core-count" = 20
  }
+-o ModelWithoutCores  <class IOAccelerator>
  {
    "model" = <"Apple M3 Max">
  }
+-o NextEntryWithCoresOnly  <class IOAccelerator>
  {
    "gpu-core-count" = 38
  }
+-o ZeroCoreAndEmptyModel  <class IOAccelerator>
  {
    "model" = "   "
    "gpu-core-count" = 0
  }
"#;
    assert_eq!(
        parse_mac_ioreg_gpu_models(ioreg),
        vec!["Apple M4 Pro (20-core)".to_string(), "Apple M3 Max".to_string()]
    );
    assert!(parse_mac_ioreg_gpu_models("").is_empty());
}

#[test]
fn test_parse_ioreg_disk_counters_sums_multiple_drivers_and_handles_empty() {
    let disk_ioreg = r#"
+-o IOBlockStorageDriver  <class IOBlockStorageDriver>
  {
    "Statistics" = {"Bytes (Read)"=1048576,"Bytes (Write)"=2097152,"Operations (Read)"=10}
  }
+-o IOBlockStorageDriver  <class IOBlockStorageDriver>
  {
    "Statistics" = {"Bytes (Read)"=524288,"Bytes (Write)"=1048576}
  }
"#;
    assert_eq!(parse_ioreg_disk_counters(disk_ioreg), Some((1_572_864, 3_145_728)));
    assert_eq!(parse_ioreg_disk_counters(""), None);
    assert_eq!(parse_ioreg_disk_counters("\"Statistics\" = {}"), None);
    assert_eq!(parse_ioreg_disk_counters("\"Statistics\" = no_braces"), None);
}

#[test]
fn test_timed_gpu_cache_keeps_zero_load_and_memory_only_readings() {
    let mut gpu_cache = TimedProbeCache::<(Option<f32>, Option<f32>)>::default();
    let interval = std::time::Duration::from_secs(10);

    let _ = gpu_cache.poll(interval, || Some((Some(0.0), Some(416.6))));
    wait_for_probe(&gpu_cache);
    assert_eq!(
        gpu_cache.poll(interval, || Some((Some(99.0), Some(999.0)))),
        Some((Some(0.0), Some(416.6)))
    );
    assert!(!gpu_cache.failed);

    // Partial memory-only reading is valid and must not latch failed.
    gpu_cache.last_fetch = Some(std::time::Instant::now() - interval);
    let _ = gpu_cache.poll(interval, || Some((None, Some(256.0))));
    wait_for_probe(&gpu_cache);
    assert_eq!(gpu_cache.poll(interval, || None), Some((None, Some(256.0))));
    assert!(!gpu_cache.failed);

    // None latches failed = true on the next poll without spawning another probe.
    gpu_cache.last_fetch = Some(std::time::Instant::now() - interval);
    let _ = gpu_cache.poll(interval, || None);
    wait_for_probe(&gpu_cache);
    assert_eq!(gpu_cache.poll(interval, || Some((Some(50.0), Some(100.0)))), None);
    assert!(gpu_cache.failed);
}

#[test]
fn test_timed_gpu_cache_throttles_and_latches_failure() {
    let mut gpu_cache = TimedProbeCache::<f32>::default();
    let interval = std::time::Duration::from_secs(5);
    let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));

    let c1 = calls.clone();
    let _ = gpu_cache.poll(interval, move || {
        c1.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Some(42.5)
    });
    wait_for_probe(&gpu_cache);
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);

    // Subsequent polls within interval return the cached reading without spawning.
    let c2 = calls.clone();
    let cached = gpu_cache.poll(interval, move || {
        c2.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Some(99.0)
    });
    assert_eq!(cached, Some(42.5));
    assert!(!gpu_cache.failed);
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);

    // Once the throttle window elapses, a None probe latches failed and prevents future spawns.
    gpu_cache.last_fetch = Some(std::time::Instant::now() - interval);
    let _ = gpu_cache.poll(interval, || None);
    wait_for_probe(&gpu_cache);
    let c3 = calls.clone();
    assert_eq!(
        gpu_cache.poll(interval, move || {
            c3.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Some(1.0)
        }),
        None
    );
    assert!(gpu_cache.failed);
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);
}

#[test]
fn test_timed_probe_cache_wait_initial_joins_in_flight_probe_when_empty() {
    type ProcMap = std::collections::HashMap<u32, (u64, u32)>;
    let interval = std::time::Duration::from_secs(2);
    let timeout = std::time::Duration::from_millis(500);
    let mut cache = TimedProbeCache::<ProcMap>::default();
    assert_eq!(cache.wait_initial(), None);

    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let gate = std::sync::Arc::new(std::sync::Barrier::new(2));
    let gate_clone = gate.clone();

    let first_poll = cache.poll(interval, move || {
        started_tx.send(()).unwrap();
        release_rx.recv_timeout(timeout).unwrap();
        Some(ProcMap::from([(5554, (48, 12_345))]))
    });
    started_rx.recv_timeout(timeout).unwrap();
    let releaser = std::thread::spawn(move || {
        gate_clone.wait();
        std::thread::yield_now();
        release_tx.send(()).unwrap();
    });
    gate.wait();
    let initial = cache.wait_initial();
    releaser.join().unwrap();

    assert_eq!(first_poll, None);
    assert_eq!(initial, Some(ProcMap::from([(5554, (48, 12_345))])));
    assert!(!cache.failed);
}

#[test]
fn test_timed_probe_cache_wait_initial_skips_join_when_already_populated() {
    type ProcMap = std::collections::HashMap<u32, (u64, u32)>;
    let interval = std::time::Duration::from_secs(2);
    let timeout = std::time::Duration::from_millis(500);
    let expected = ProcMap::from([(5554, (48, 12_345))]);
    let mut cache = TimedProbeCache {
        reading: Some(expected.clone()),
        last_fetch: Some(std::time::Instant::now() - interval),
        ..Default::default()
    };
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let probe_released = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let probe_flag = probe_released.clone();

    let refresh_poll = cache.poll(interval, move || {
        started_tx.send(()).unwrap();
        let _ = release_rx.recv_timeout(timeout);
        probe_flag.store(true, std::sync::atomic::Ordering::SeqCst);
        Some(ProcMap::from([(5554, (64, 20_000))]))
    });
    started_rx.recv_timeout(timeout).unwrap();
    let immediate = cache.wait_initial();
    let returned_before_probe = !probe_released.load(std::sync::atomic::Ordering::SeqCst);
    release_tx.send(()).unwrap();
    wait_for_probe(&cache);

    assert_eq!(refresh_poll, Some(expected.clone()));
    assert_eq!(immediate, Some(expected));
    assert!(returned_before_probe);
    assert_eq!(cache.poll(interval, || None), Some(ProcMap::from([(5554, (64, 20_000))])));
}

#[test]
fn test_timed_probe_cache_wait_initial_latches_failure() {
    type ProcMap = std::collections::HashMap<u32, (u64, u32)>;
    let interval = std::time::Duration::from_secs(2);
    let timeout = std::time::Duration::from_millis(500);
    let mut cache = TimedProbeCache::<ProcMap>::default();
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let gate = std::sync::Arc::new(std::sync::Barrier::new(2));
    let gate_clone = gate.clone();

    let _ = cache.poll(interval, move || {
        started_tx.send(()).unwrap();
        release_rx.recv_timeout(timeout).unwrap();
        None
    });
    started_rx.recv_timeout(timeout).unwrap();
    let releaser = std::thread::spawn(move || {
        gate_clone.wait();
        std::thread::yield_now();
        release_tx.send(()).unwrap();
    });
    gate.wait();
    let initial = cache.wait_initial();
    releaser.join().unwrap();

    cache.last_fetch = Some(std::time::Instant::now() - interval);
    let spawns = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let spawns_clone = spawns.clone();
    let after_fail_poll = cache.poll(interval, move || {
        spawns_clone.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Some(ProcMap::from([(5554, (1, 1))]))
    });
    cache.reading = Some(ProcMap::from([(5554, (9, 9))]));

    assert_eq!(initial, None);
    assert!(cache.failed);
    assert_eq!(after_fail_poll, None);
    assert_eq!(cache.wait_initial(), None);
    assert_eq!(spawns.load(std::sync::atomic::Ordering::SeqCst), 0);
}

#[test]
fn test_timed_probe_cache_clear_reaps_finished_probe_and_clears_reading() {
    type ProcMap = std::collections::HashMap<u32, (u64, u32)>;
    let interval = std::time::Duration::from_secs(2);
    let mut cache = TimedProbeCache {
        reading: Some(ProcMap::from([(5554, (32, 900))])),
        last_fetch: Some(std::time::Instant::now() - interval),
        ..Default::default()
    };

    let _ = cache.poll(interval, || Some(ProcMap::from([(5556, (40, 1_500))])));
    wait_for_probe(&cache);
    cache.clear();

    assert_eq!(cache.reading, None);
    assert!(cache.probe.is_none());
    assert!(!cache.failed);
}

#[test]
fn test_timed_probe_cache_clear_latches_and_preserves_failure() {
    type ProcMap = std::collections::HashMap<u32, (u64, u32)>;
    let interval = std::time::Duration::from_secs(2);
    let mut cache = TimedProbeCache::<ProcMap>::default();

    let _ = cache.poll(interval, || None);
    wait_for_probe(&cache);
    cache.clear();
    assert!(cache.failed);
    assert!(cache.probe.is_none());

    cache.reading = Some(ProcMap::from([(5558, (10, 50))]));
    cache.clear();
    cache.last_fetch = Some(std::time::Instant::now() - interval);
    let spawns = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let spawns_clone = spawns.clone();
    let after_clear_poll = cache.poll(interval, move || {
        spawns_clone.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Some(ProcMap::from([(5558, (10, 50))]))
    });

    assert_eq!(cache.reading, None);
    assert!(cache.failed);
    assert_eq!(after_clear_poll, None);
    assert_eq!(spawns.load(std::sync::atomic::Ordering::SeqCst), 0);
}
