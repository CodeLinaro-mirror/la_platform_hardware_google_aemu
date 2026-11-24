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
    assert!(!prov.mac_gpu.failed);
    assert!(!prov.mac_proc_status.failed);
    assert!(!prov.windows_gpu.failed);
    assert!(!prov.nvidia_smi_failed);
    assert!(!prov.nvidia_pmon_failed);
    assert!(prov.pid_port_cache.is_empty());
    assert!(prov.is_first_run);
    assert!(prov.mac_gpu.reading.is_none());
    assert!(prov.mac_gpu.last_fetch.is_none());
    assert!(prov.mac_proc_status.reading.is_none());
    assert!(prov.mac_proc_status.last_fetch.is_none());
    assert!(prov.windows_gpu.reading.is_none());
    assert!(prov.windows_gpu.last_fetch.is_none());
    assert!(prov.cpu_time_ms.is_empty());
    assert!(prov.last_sample_at.is_none());
}

#[test]
fn test_windows_gpu_parser_and_provider_integration() {
    assert_eq!(parse_windows_gpu(" 37.5 \r\n"), Some(37.5));
    assert_eq!(parse_windows_gpu("12,5\n"), Some(12.5));
    assert_eq!(parse_windows_gpu("NaN\n"), None);
    assert_eq!(parse_windows_gpu(""), None);

    let mut prov = RealHostProvider::new(System::new());
    prov.windows_gpu.last_fetch = Some(std::time::Instant::now());
    prov.windows_gpu.reading = Some(33.5);
    assert_eq!(prov.sample_windows_gpu(), Some(33.5));
    assert!(!prov.windows_gpu.failed);
    prov.windows_gpu.failed = true;
    assert_eq!(prov.sample_windows_gpu(), None);
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
    prov.mac_gpu.failed = true;
    assert_eq!(prov.sample_mac_gpu(), (None, None));
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

#[test]
fn test_format_static_os_name_and_mac_gpu_valid_cache() {
    assert_eq!(format_static_os_name(Some("Darwin"), Some("27.2.0")), "Darwin 27.2.0");
    assert_eq!(format_static_os_name(Some("Darwin"), None), "Darwin");
    assert_eq!(format_static_os_name(Some("Ubuntu"), Some("6.8.0")), "Ubuntu");
    assert_eq!(format_static_os_name(None, Some("6.8.0")), "Unknown OS");

    let mut prov = RealHostProvider::new(System::new());
    prov.mac_gpu.last_fetch = Some(std::time::Instant::now());
    prov.mac_gpu.reading = Some((Some(0.0), Some(416.6)));
    assert_eq!(prov.sample_mac_gpu(), (Some(0.0), Some(416.6)));
    assert!(!prov.mac_gpu.failed);
}

#[test]
fn test_poll_mac_proc_status_waits_initial_caches_and_clears_when_idle() {
    let mut prov = RealHostProvider::new(System::new());

    // Invalid PID 0 resolves hermetically via read_mac_top_proc_status without invoking top,
    // exercising the wait_initial join path on an initially empty mac_proc_status cache.
    let initial = prov.poll_mac_proc_status(vec![0], true);
    assert!(initial.is_empty());
    assert_eq!(prov.mac_proc_status.reading, Some(std::collections::HashMap::new()));
    assert!(!prov.mac_proc_status.failed);

    let cached_map = std::collections::HashMap::from([(5554, (48_u64, 9_000_u32))]);
    prov.mac_proc_status.last_fetch = Some(std::time::Instant::now());
    prov.mac_proc_status.reading = Some(cached_map.clone());
    assert_eq!(prov.poll_mac_proc_status(vec![5554], false), cached_map);

    let idle = prov.poll_mac_proc_status(Vec::new(), false);
    assert!(idle.is_empty());
    assert!(prov.mac_proc_status.reading.is_none());
    assert!(!prov.mac_proc_status.failed);
}
