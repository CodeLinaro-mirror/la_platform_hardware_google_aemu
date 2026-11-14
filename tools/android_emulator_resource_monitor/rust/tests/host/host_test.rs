use super::*;

#[test]
fn test_real_host_provider_new_defaults() {
    let prov = RealHostProvider::new(System::new());
    assert_eq!(prov.last_pgin, 0);
    assert_eq!(prov.last_pgout, 0);
    assert!(prov.last_paging_at.is_none());
    assert_eq!(prov.last_disk_read, 0);
    assert_eq!(prov.last_disk_write, 0);
    assert!(prov.last_disk_at.is_none());
    assert!(!prov.intel_gpu_failed);
    assert!(!prov.mac_gpu_failed);
    assert!(!prov.windows_gpu_failed);
    assert!(!prov.nvidia_smi_failed);
    assert!(!prov.nvidia_pmon_failed);
    assert!(prov.pid_port_cache.is_empty());
    assert!(prov.is_first_run);
    assert!(prov.mac_gpu_cache.lock().unwrap().is_none());
    assert!(!prov.mac_gpu_fetching.load(std::sync::atomic::Ordering::SeqCst));
    assert!(prov.mac_gpu_last_fetch.is_none());
    assert!(prov.windows_gpu_cache.lock().unwrap().is_none());
    assert!(!prov.windows_gpu_fetching.load(std::sync::atomic::Ordering::SeqCst));
    assert!(prov.windows_gpu_last_fetch.is_none());
    assert!(prov.cpu_time_ms.is_empty());
    assert!(prov.last_sample_at.is_none());
}

#[test]
fn test_windows_gpu_background_cache_throttles_and_latches_failure() {
    assert_eq!(parse_windows_gpu(" 37.5 \r\n"), Some(37.5));
    assert_eq!(parse_windows_gpu("12,5\n"), Some(12.5));
    assert_eq!(parse_windows_gpu("NaN\n"), None);
    assert_eq!(parse_windows_gpu(""), None);

    let mut failed = false;
    let cache = std::sync::Arc::new(std::sync::Mutex::new(None));
    let fetching = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut last_fetch = None;
    let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));

    let c1 = calls.clone();
    let _ = poll_cached_gpu(
        &mut failed,
        &cache,
        &fetching,
        &mut last_fetch,
        WIN_GPU_FETCH_INTERVAL,
        move || {
            c1.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Some(42.5)
        },
    );
    for _ in 0..200 {
        if !fetching.load(std::sync::atomic::Ordering::SeqCst) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
    assert!(!failed);
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);

    // Subsequent polls within WIN_GPU_FETCH_INTERVAL return the cached reading without spawning.
    let c2 = calls.clone();
    let cached = poll_cached_gpu(
        &mut failed,
        &cache,
        &fetching,
        &mut last_fetch,
        WIN_GPU_FETCH_INTERVAL,
        move || {
            c2.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Some(99.0)
        },
    );
    assert_eq!(cached, Some(42.5));
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);

    // Once the throttle window elapses, a failing refresh stores NaN and latches failed on the next poll.
    last_fetch = Some(std::time::Instant::now() - WIN_GPU_FETCH_INTERVAL);
    let _ = poll_cached_gpu(
        &mut failed,
        &cache,
        &fetching,
        &mut last_fetch,
        WIN_GPU_FETCH_INTERVAL,
        || None,
    );
    for _ in 0..200 {
        if !fetching.load(std::sync::atomic::Ordering::SeqCst) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
    assert_eq!(
        poll_cached_gpu(
            &mut failed,
            &cache,
            &fetching,
            &mut last_fetch,
            WIN_GPU_FETCH_INTERVAL,
            || Some(1.0)
        ),
        None
    );
    assert!(failed);

    let mut prov = RealHostProvider::new(System::new());
    prov.windows_gpu_last_fetch = Some(std::time::Instant::now());
    *prov.windows_gpu_cache.lock().unwrap() = Some(33.5);
    assert_eq!(prov.sample_windows_gpu(), Some(33.5));
    assert!(!prov.windows_gpu_failed);
    *prov.windows_gpu_cache.lock().unwrap() = Some(f32::NAN);
    assert_eq!(prov.sample_windows_gpu(), None);
    assert!(prov.windows_gpu_failed);
}

#[test]
fn test_gpu_and_hardware_cli_failure_handling() {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        let failed_out = std::process::Output {
            status: std::process::ExitStatus::from_raw(256),
            stdout: b"NVIDIA-SMI has failed, driver not loaded\n".to_vec(),
            stderr: Vec::new(),
        };
        assert_eq!(parse_nvidia_gpu_hardware_info(&failed_out), None);
    }
    assert_eq!(parse_intel_gpu_top("Render/3D/0: 37.5%\n"), Some(37.5));

    let mut prov = RealHostProvider::new(System::new());
    *prov.mac_gpu_cache.lock().unwrap() = Some(f32::NAN);
    assert_eq!(prov.sample_mac_gpu(), None);
    assert!(prov.mac_gpu_failed);
}

#[test]
fn test_static_hardware_system_skips_process_table_scan() {
    let sys = static_hardware_system();
    assert!(
        sys.processes().is_empty(),
        "static_hardware_system should not scan all processes (found {})",
        sys.processes().len()
    );
    assert!(sys.total_memory() > 0);
    assert!(!sys.cpus().is_empty());
}

#[test]
fn test_nvidia_pmon_failure_does_not_disable_nvidia_gpu_query() {
    let ok = |stdout: &str| std::process::Output {
        status: std::process::ExitStatus::default(),
        stdout: stdout.as_bytes().to_vec(),
        stderr: Vec::new(),
    };
    let mut prov = RealHostProvider::new(System::new());
    let by_pid = prov.record_nvidia_pmon(Some(ok(
        "    0       1234     C    30    10     0     0   qemu-system-x86\n",
    )));
    assert_eq!(by_pid.get(&1234), Some(&30.0));
    assert!(!prov.nvidia_pmon_failed);

    assert!(prov.record_nvidia_pmon(None).is_empty());
    assert!(prov.nvidia_pmon_failed);
    assert!(
        !prov.nvidia_smi_failed,
        "a failed or skipped pmon call must not latch nvidia_smi_failed and disable --query-gpu"
    );
    assert_eq!(prov.record_nvidia_gpu(Some(ok("30, 1000\n"))), (Some(30.0), Some(1000.0)));
    assert!(!prov.nvidia_smi_failed);

    let mut prov = RealHostProvider::new(System::new());
    assert_eq!(prov.record_nvidia_gpu(None), (None, None));
    assert!(prov.nvidia_smi_failed);
    assert!(prov.nvidia_pmon_failed, "a failed --query-gpu also disables pmon");
}

#[test]
fn test_real_host_provider_default_skips_process_table_scan() {
    let prov = RealHostProvider::default();
    assert!(
        prov.sys.processes().is_empty(),
        "RealHostProvider::default() should not scan all processes before refresh_system (found {})",
        prov.sys.processes().len()
    );
    assert!(!prov.sys.cpus().is_empty());
    assert_eq!(format_cpu_brand(None), "Unknown CPU");
    assert_eq!(format_cpu_brand(Some("   ")), "Unknown CPU");
    assert_eq!(format_cpu_brand(Some(" Apple M3 Max ")), "Apple M3 Max");
}

#[test]
fn test_disk_and_paging_counter_rates_use_counter_timestamps() {
    let mib = 1024 * 1024;
    let t0 = std::time::Instant::now();
    let t1 = t0 + std::time::Duration::from_millis(1200);
    let t2 = t0 + std::time::Duration::from_millis(2000);
    let t3 = t0 + std::time::Duration::from_millis(3000);
    let t4 = t0 + std::time::Duration::from_millis(4000);

    let mut prov = RealHostProvider::new(System::new());
    // Baseline snapshot at t0 (before 200ms baseline sleep + 1.0s GPU CLI).
    assert_eq!(prov.record_disk_counters(10 * mib, 20 * mib, t0), (0.0, 0.0));
    let first_pg = prov.record_paging_counters(Some((1000, 2000)), t0, 0.2, "KB/s");
    assert_eq!(first_pg.display, "Calculating...");

    // Tick 1 counter read at t0 + 1.2s divides by 1.2s (not refresh_system's 0.2s).
    let (r1, w1) = prov.record_disk_counters(22 * mib, 44 * mib, t1);
    assert!((r1 - 10.0).abs() < 1e-6);
    assert!((w1 - 20.0).abs() < 1e-6);
    let pg1 = prov.record_paging_counters(Some((1120, 2240)), t1, 0.2, "KB/s");
    assert!((pg1.in_rate - 100.0).abs() < 1e-6);
    assert!((pg1.out_rate - 200.0).abs() < 1e-6);

    // Tick 2 at t0 + 2.0s (after GPU CLI latches off) divides by the true 0.8s gap
    // rather than refresh_system's 1.8s sample_interval.
    let (r2, w2) = prov.record_disk_counters(30 * mib, 52 * mib, t2);
    assert!((r2 - 10.0).abs() < 1e-6);
    assert!((w2 - 10.0).abs() < 1e-6);
    let pg2 = prov.record_paging_counters(Some((1200, 2320)), t2, 1.8, "KB/s");
    assert!((pg2.in_rate - 100.0).abs() < 1e-6);
    assert!((pg2.out_rate - 100.0).abs() < 1e-6);

    // A transient None paging sample preserves last_paging_at = t2 so recovery at t4
    // divides the 2-tick delta by t4 - t2 = 2.0s.
    let pg_miss = prov.record_paging_counters(None, t3, 1.0, "pages/s");
    assert_eq!(pg_miss.display, "N/A");
    assert_eq!(prov.last_paging_at, Some(t2));
    let pg_rec = prov.record_paging_counters(Some((1400, 2720)), t4, 1.0, "pages/s");
    assert!((pg_rec.in_rate - 100.0).abs() < 1e-6);
    assert!((pg_rec.out_rate - 200.0).abs() < 1e-6);
}
