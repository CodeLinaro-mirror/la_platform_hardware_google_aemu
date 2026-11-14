use super::*;
use crate::provider::GuestProvider;

#[test]
fn test_parse_adb_devices_keeps_no_permissions_devices() {
    let out = "List of devices attached\n\
               emulator-5554\tdevice\n\
               0123456789\tno permissions (missing udev rules?); see [...]\n\
               error: no devices/emulators found\n\
               adb: no devices/emulators found\n";
    let devices = parse_adb_devices(out);
    assert_eq!(devices.len(), 2, "unexpected phantom devices: {devices:?}");
    assert_eq!(devices.get("0123456789").map(String::as_str), Some("no permissions"));
    assert_eq!(devices.get("emulator-5554").map(String::as_str), Some("device"));
}

#[test]
fn test_find_adb_on_path_returns_first_match() {
    let path = std::env::join_paths(["/opt/tools", "/usr/local/bin", "/usr/bin"]).unwrap();
    let local_adb =
        std::path::Path::new("/usr/local/bin").join("adb").to_string_lossy().into_owned();
    let usr_adb = std::path::Path::new("/usr/bin").join("adb").to_string_lossy().into_owned();
    let is_file = |p: &str| p == local_adb || p == usr_adb;
    assert_eq!(find_adb_on_path(Some(&path), &is_file).as_deref(), Some(local_adb.as_str()));
    assert_eq!(find_adb_on_path(Some(&path), &|_| false), None);
    assert_eq!(find_adb_on_path(None, &|_| true), None);
}

#[test]
fn test_parse_model_info() {
    let stdout = "Mock Phone|!|14|!|34|!|BuildID|!|Size: 1080x2400|!|1";
    let res = parse_model_info(stdout);
    assert!(res.is_some());
    let (model, is_booting) = res.unwrap();
    assert!(model.contains("Mock Phone"));
    assert!(!is_booting);
}

#[test]
fn test_parse_model_info_robust_resolution() {
    let stdout =
        "Mock Phone|!|14|!|34|!|BuildID|!|Physical size: 450x450\nOverride size: 450x450|!|1";
    let res = parse_model_info(stdout);
    assert!(res.is_some());
    let (model, _) = res.unwrap();
    assert!(model.contains("Res: 450x450"), "Expected Res: 450x450, got {}", model);
}

#[test]
fn test_parse_batch_output() {
    let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/dev/block/dm-5 10G 2G 8G 20% /data\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
    let out = parse_batch_output(stdout);
    assert!(out.ram.contains("50.0%"));
    assert_eq!(out.is_booting, Some(false));
    assert_eq!(out.data_space, Some("20% (2G used/8G avail)".to_string()));
    assert_eq!(out.err_count, 5);
    assert_eq!(out.data_pct, Some(20.0));
}

#[test]
fn test_parse_batch_output_with_swap() {
    let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\nSwapTotal: 1048576 kB\nSwapFree: 955268 kB\n===ADB_MON_SPLIT===\n/dev/block/dm-5 10G 2G 8G 20% /data\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1\n===ADB_MON_SPLIT===\n(top out)";
    let out = parse_batch_output(stdout);
    assert!(out.ram.contains("50.0%"));
    assert_eq!(out.is_booting, Some(false));
    assert_eq!(out.data_space, Some("20% (2G used/8G avail)".to_string()));
    assert_eq!(out.err_count, 5);
    assert_eq!(out.data_pct, Some(20.0));
    assert_eq!(out.top_output, "\n(top out)");
    assert_eq!(out.swap, Some("91.1 MB".to_string()));
}

#[test]
fn test_parse_top_output() {
    let stdout = "Tasks: 100\n400% cpu 200% idle";
    let (diff, real) = parse_top_output(stdout);
    assert_eq!(diff, "200%");
    assert!(real.contains("50.0%"));
}

#[test]
fn test_parse_batch_output_boot_correction() {
    let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/data space\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n0";
    let out = parse_batch_output(stdout);
    assert_eq!(out.is_booting, Some(true));
}

#[test]
fn test_parse_batch_output_safe_separator() {
    let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/data space with random text\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
    let out = parse_batch_output(stdout);
    assert!(out.ram.contains("50.0%"));
    assert_eq!(out.is_booting, Some(false));
}

#[test]
fn test_parse_batch_output_df_variation() {
    let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n10G   2G   8G  20% /data\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
    let out = parse_batch_output(stdout);
    assert!(out.ram.contains("50.0%"));
    assert_eq!(out.data_space, Some("20% (2G used/8G avail)".to_string()));
    assert_eq!(out.data_pct, Some(20.0));
}

#[test]
fn test_guest_cpu_cores_calculation() {
    let stdout = "Tasks: 100\n400% cpu 100% idle";
    let (diff, real) = parse_top_output(stdout);
    assert_eq!(diff, "300%");
    assert!(real.contains("over 4 cores"));
}

#[test]
fn test_parse_batch_output_missing_data_partition() {
    let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
    let out = parse_batch_output(stdout);
    assert!(out.ram.contains("50.0%"));
    assert_eq!(out.data_space, None);
    assert_eq!(out.data_pct, None);
}

#[test]
fn test_parse_top_output_no_cpu_line() {
    let stdout = "Tasks: 100\nNo CPU line here";
    let (diff, real) = parse_top_output(stdout);
    assert_eq!(diff, "N/A");
    assert_eq!(real, "N/A");
}

#[test]
fn test_parse_top_output_with_io_wait() {
    let stdout = "400%cpu  5%user  1%nice  7%sys 215%idle 167%iow   5%irq   0%sirq   0%host";
    let (diff, real) = parse_top_output(stdout);
    assert_eq!(diff, "185%");
    assert!(real.contains("over 4 cores"));
}

#[test]
fn test_retry_conditions() {
    let retry_cases = vec![
        ("", true),
        ("Unknown", true),
        ("offline", true),
        ("Error: some error", true),
        ("Android 12 | Res: N/A", true),
        ("Just some string", true),
    ];

    for (model, is_booting) in retry_cases {
        assert!(
            should_retry_model(model, is_booting),
            "Failed for case: model='{}', is_booting={}",
            model,
            is_booting
        );
    }

    let no_retry_cases =
        vec![("Android 12 | Res: 1080x2400", true), ("Android 12 | Res: N/A", false)];

    for (model, is_booting) in no_retry_cases {
        assert!(
            !should_retry_model(model, is_booting),
            "Failed for case: model='{}', is_booting={}",
            model,
            is_booting
        );
    }
}

#[tokio::test]
async fn test_get_guest_stats_offline_preserves_is_booting() {
    let provider = RealGuestProvider;
    let stats = provider.get_guest_stats("emulator-5554", "Android 12", false, "offline").await;
    assert_eq!(stats.model, "Android 12");
    assert!(!stats.is_booting);
    assert_eq!(stats.cpu_total, "N/A (offline)");

    let booting_stats =
        provider.get_guest_stats("emulator-5554", "Android 12", true, "offline").await;
    assert!(booting_stats.is_booting);
}

#[test]
fn test_sanitize_and_truncated_batch_preserve_booted_state() {
    for bad in [
        "",
        "adb: error: closed",
        "error: device offline",
        "error: device unauthorized.",
        "error: no devices/emulators found",
        "MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\nadb: error: closed",
    ] {
        assert_eq!(sanitize_adb_shell_stdout(bad), None, "accepted error stdout: {bad:?}");
    }
    // Truncated batch outputs lack a complete 4th section and must return is_booting = None.
    assert_eq!(parse_batch_output("MemTotal: 4000000 kB\n").is_booting, None);
    assert_eq!(
        parse_batch_output(
            "MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n"
        )
        .is_booting,
        None
    );
    // Empty sys.boot_completed followed by separator #4 still detects booting.
    assert_eq!(
        parse_batch_output(
            "MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n\n===ADB_MON_SPLIT===\n"
        )
        .is_booting,
        Some(true)
    );
    // An app name containing "offline" inside `top` (section 4) is not rejected.
    let with_offline_app = "MemTotal: 4000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n1\n===ADB_MON_SPLIT===\ncom.example.offline\n";
    assert!(sanitize_adb_shell_stdout(with_offline_app).is_some());
}

#[test]
fn test_parse_adb_devices_filters_noise() {
    let stdout = "* daemon not running; starting now at tcp:5037\n\
                  * daemon started successfully\n\
                  List of devices attached\n\
                  emulator-5554\tdevice\n\
                  emulator-5556\toffline\n\
                  adb server version (41) doesn't match this client\n";
    let devs = parse_adb_devices(stdout);
    assert_eq!(devs.len(), 2);
    assert_eq!(devs.get("emulator-5554").map(|s| s.as_str()), Some("device"));
    assert_eq!(devs.get("emulator-5556").map(|s| s.as_str()), Some("offline"));
}

#[test]
fn test_resolve_adb_path_sdk_fallbacks() {
    let found = resolve_adb_path(None, None, Some("/home/user"), &|p| {
        p == "/home/user/Android/Sdk/platform-tools/adb"
    });
    assert_eq!(found.as_deref(), Some("/home/user/Android/Sdk/platform-tools/adb"));

    let win_found = resolve_adb_path(None, None, Some(r"C:\Users\alice"), &|p| {
        p == r"C:\Users\alice\AppData\Local\Android\Sdk\platform-tools\adb.exe"
    });
    assert_eq!(
        win_found.as_deref(),
        Some(r"C:\Users\alice\AppData\Local\Android\Sdk\platform-tools\adb.exe")
    );

    let win_sdk_root =
        resolve_adb_path(Some(r"D:\sdk\"), None, None, &|p| p == r"D:\sdk\platform-tools\adb.exe");
    assert_eq!(win_sdk_root.as_deref(), Some(r"D:\sdk\platform-tools\adb.exe"));
}

#[test]
fn test_windows_adb_exe_precedence_and_top_id_word_boundary() {
    let both_present = resolve_adb_path(Some(r"C:\Sdk"), None, None, &|p| {
        p == r"C:\Sdk\platform-tools\adb" || p == r"C:\Sdk\platform-tools\adb.exe"
    });
    assert_eq!(both_present.as_deref(), Some(r"C:\Sdk\platform-tools\adb.exe"));

    let both_home = resolve_adb_path(None, None, Some(r"C:\Users\alice"), &|p| {
        p == r"C:\Users\alice\AppData\Local\Android\Sdk\platform-tools\adb"
            || p == r"C:\Users\alice\AppData\Local\Android\Sdk\platform-tools\adb.exe"
    });
    assert_eq!(
        both_home.as_deref(),
        Some(r"C:\Users\alice\AppData\Local\Android\Sdk\platform-tools\adb.exe")
    );

    let idmap_only =
        "Tasks: 210 total, 1 running\n 512 system 20 0 10G 4.0M 3.2M S 0.0 0.2 0:00.12 idmap2d\n";
    assert_eq!(parse_top_output(idmap_only), ("N/A".to_string(), "N/A".to_string()));

    let real_id = "%Cpu(s): 5.0 us, 2.0 sy, 0.0 ni, 93.0 id, 0.0 wa\n";
    assert_eq!(parse_top_output(real_id), ("7.0%".to_string(), "7.0% (Cores unknown)".to_string()));

    let sdk_adb =
        std::path::Path::new("/sdk/platform-tools").join("adb").to_string_lossy().into_owned();
    let sdk_adb_exe =
        std::path::Path::new("/sdk/platform-tools").join("adb.exe").to_string_lossy().into_owned();
    let path = std::env::join_paths(["/sdk/platform-tools"]).unwrap();
    let path_both = |p: &str| p == sdk_adb || p == sdk_adb_exe;
    assert_eq!(
        find_adb_on_path_with_order(Some(&path), true, &path_both).as_deref(),
        Some(sdk_adb_exe.as_str())
    );
    assert_eq!(
        find_adb_on_path_with_order(Some(&path), false, &path_both).as_deref(),
        Some(sdk_adb.as_str())
    );
}

#[cfg(unix)]
#[tokio::test]
async fn test_adb_command_redirects_stdin_to_devnull() {
    // `cat` with no arguments blocks indefinitely if stdin is inherited from a live TTY/pipe,
    // and exits immediately with empty stdout when stdin is redirected to /dev/null.
    let out = tokio::time::timeout(Duration::from_millis(500), adb_command("cat").output())
        .await
        .expect("adb_command must not block reading inherited stdin")
        .expect("cat should execute");
    assert!(out.status.success());
    assert!(out.stdout.is_empty());
}

#[test]
fn test_parse_batch_output_memfree_fallback() {
    let stdout = "MemTotal: 4000000 kB\nMemFree: 1000000 kB\nBuffers: 500000 kB\nCached: 500000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n1";
    let out = parse_batch_output(stdout);
    assert!(out.ram.contains("50.0%"));
    assert_eq!(out.is_booting, Some(false));
}

#[test]
fn test_parse_top_output_toolbox_uses_last_frame() {
    // Captured from an API 24 google_apis x86_64 emulator: toolbox `top` rejects `-b`, so the
    // batch falls back to `top -n 2 -d 1`, whose frames carry tick deltas instead of `%cpu`.
    // The first frame spans a single tick; the second spans the one-second delay.
    let stdout = "Invalid argument \"-b\".\n\
         Usage: top [ -m max_procs ] [ -n iterations ] [ -d delay ] [ -s sort_column ] [ -t ] [ -h ]\n\
         User 0%, System 0%, IOW 0%, IRQ 0%\n\
         User 0 + Nice 0 + Sys 0 + Idle 1 + IOW 0 + IRQ 0 + SIRQ 0 = 1\n\
         \n\
         \x20 PID USER     PR  NI CPU% S  #THR     VSS     RSS PCY Name\n\
         \x204283 shell    20   0 200% R     1   9868K   2024K  fg top\n\
         \x20   2 root     20   0   0% S     1      0K      0K  fg kthreadd\n\
         \n\
         \n\
         User 1%, System 0%, IOW 0%, IRQ 0%\n\
         User 3 + Nice 1 + Sys 3 + Idle 391 + IOW 0 + IRQ 0 + SIRQ 0 = 398\n\
         \n\
         \x20 PID USER     PR  NI CPU% S  #THR     VSS     RSS PCY Name\n\
         \x201707 system   20   0   0% S    86 1766384K 114392K  fg system_server\n";
    let (cpu_total, cpu_adjusted) = parse_top_output(stdout);
    assert_eq!(cpu_total, "1.8%");
    assert_eq!(cpu_adjusted, "1.8% (Cores unknown)");
}

#[test]
fn test_parse_top_output_toolbox_failures_read_na() {
    let na = ("N/A".to_string(), "N/A".to_string());
    // Only the one-tick first frame (captured on API 24), as left by a truncated reply.
    assert_eq!(
        parse_top_output("User 0 + Nice 0 + Sys 0 + Idle 1 + IOW 0 + IRQ 0 + SIRQ 0 = 1\n"),
        na
    );
    // Synthetic: toolbox dies of its divide-by-zero before it prints a zero-tick frame, so
    // this guard is defensive.
    assert_eq!(
        parse_top_output(
            "User 0 + Nice 0 + Sys 0 + Idle 1 + IOW 0 + IRQ 0 + SIRQ 0 = 1\n\
             User 0 + Nice 0 + Sys 0 + Idle 0 + IOW 0 + IRQ 0 + SIRQ 0 = 0\n"
        ),
        na
    );
    // Captured on API 24 with a single fallback attempt, which died of the divide-by-zero.
    assert_eq!(
        parse_top_output(
            "\nInvalid argument \"-b\".\n\
             Usage: top [ -m max_procs ] [ -n iterations ] [ -d delay ] [ -s sort_column ] [ -t ] [ -h ]\n\
             \x20   -m num  Maximum number of processes to display.\n\
             \x20   -n num  Updates to show before exiting.\n\
             \x20   -d num  Seconds to wait between updates.\n\
             \x20   -s col  Column to sort by (cpu,vss,rss,thr).\n\
             \x20   -H      Show threads instead of processes.\n\
             \x20   -h      Display this help screen.\n\
             Floating point exception \n\n"
        ),
        na
    );
}

#[test]
fn test_parse_top_output_toolbox_accepts_negative_deltas() {
    // Synthetic (no capture shows a negative delta): the kernel's aggregate iowait can go
    // backwards, and toolbox prints signed deltas. The first frame is captured on API 24.
    let first = "User 0 + Nice 0 + Sys 0 + Idle 1 + IOW 0 + IRQ 0 + SIRQ 0 = 1\n";
    let parse_with_last = |last: &str| parse_top_output(&format!("{first}{last}"));
    assert_eq!(
        parse_with_last("User 3 + Nice 1 + Sys 3 + Idle 391 + IOW -1 + IRQ 0 + SIRQ 0 = 397\n"),
        ("1.5%".to_string(), "1.5% (Cores unknown)".to_string())
    );
    // A negative idle delta is clamped to 100% instead of reading above it.
    assert_eq!(
        parse_with_last("User 200 + Nice 0 + Sys 200 + Idle -2 + IOW 0 + IRQ 0 + SIRQ 0 = 398\n"),
        ("100.0%".to_string(), "100.0% (Cores unknown)".to_string())
    );
    // An iowait drop larger than the busy ticks is clamped to 0% instead of reading below it.
    assert_eq!(
        parse_with_last("User 1 + Nice 0 + Sys 1 + Idle 402 + IOW -3 + IRQ 0 + SIRQ 0 = 401\n"),
        ("0.0%".to_string(), "0.0% (Cores unknown)".to_string())
    );
    // A first frame with a negative total still counts as a frame.
    assert_eq!(
        parse_top_output(
            "User 0 + Nice 0 + Sys 0 + Idle 1 + IOW -2 + IRQ 0 + SIRQ 0 = -1\n\
             User 3 + Nice 1 + Sys 3 + Idle 391 + IOW 0 + IRQ 0 + SIRQ 0 = 398\n"
        ),
        ("1.8%".to_string(), "1.8% (Cores unknown)".to_string())
    );
}

#[test]
fn test_parse_model_info_rejects_empty_model() {
    assert_eq!(parse_model_info("|!||!||!||!||!|"), None);
}

#[test]
fn test_parse_top_output_toybox_interactive_fallback() {
    // Captured from an API 26 google_apis x86_64 emulator: toybox 0.7 `top` rejects `-d 0.2`,
    // and the `top -n 2 -d 1` fallback runs in interactive mode, with ANSI escapes, CRLF line
    // endings and frames separated by a bare carriage return.
    let stdout = "\x1b[?25l\x1b[0m\x1b[H\x1b[J\x1b[s\x1b[999C\x1b[999B\x1b[6n\x1b[u\
         Tasks: 172 total,   1 running, 171 sleeping,   0 stopped,   0 zombie            \r\n\
         Mem:   2046764k total,  1758436k used,   288328k free,    30552k buffers        \r\n\
         Swap:        0k total,        0k used,        0k free,  1010980k cached         \r\n\
         400%cpu   4%user   0%nice  12%sys 381%idle   0%iow   0%irq   4%sirq   0%host    \r\n\
         \x1b[7m  PID USER         PR  NI VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS            \x1b[0m\r\n\
         \x202274 u0_a66       20   0 1.6G  79M  64M S  7.6   3.9   0:00.13 com.google.andr+\r\n\
         \x203008 u0_a11       20   0 1.6G  96M  77M S  0.0   4.8   0:01.87 android.process+\r\
         \x1b[H\x1b[JTasks: 172 total,   2 running, 170 sleeping,   0 stopped,   0 zombie            \r\n\
         Mem:   2046764k total,  1758544k used,   288220k free,    30552k buffers        \r\n\
         Swap:        0k total,        0k used,        0k free,  1010980k cached         \r\n\
         400%cpu   1%user   0%nice   2%sys 397%idle   0%iow   0%irq   0%sirq   0%host    \r\n\
         \x1b[7m  PID USER         PR  NI VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS            \x1b[0m\r\n\
         \x201427 system       20   0  16M 4.5M 3.8M S  2.0   0.2   0:01.07 android.hardwar+\r\n";
    let (cpu_total, cpu_adjusted) = parse_top_output(stdout);
    assert_eq!(cpu_total, "3%");
    assert_eq!(cpu_adjusted, "0.8% (over 4 cores)");
}

#[test]
fn test_parse_df_data_section_skips_non_row_data_lines() {
    let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\ndf: /data/preload: Permission denied\n/dev/block/dm-5 10G 2G 8G 20% /data\n===ADB_MON_SPLIT===\n0\n===ADB_MON_SPLIT===\n1";
    let out = parse_batch_output(stdout);
    assert_eq!(out.data_space.as_deref(), Some("20% (2G used/8G avail)"));
    assert_eq!(out.data_pct, Some(20.0));
}

#[test]
fn test_parse_batch_output_api33_df_names_bind_mount_instead_of_data() {
    // Captured from an API 33 google_apis x86_64 emulator (b/583871630): `df -h /data` reports
    // the /data device under its last bind mount, so the row never mentions /data.
    let stdout = "MemTotal:        2012912 kB\nMemFree:          173820 kB\nMemAvailable:     557316 kB\n\
         SwapTotal:       1509680 kB\nSwapFree:        1054000 kB\n\
         ===ADB_MON_SPLIT===\n\
         Filesystem       Size Used Avail Use% Mounted on\n\
         /dev/block/dm-34  10G 574M  8.9G   6% /storage/emulated/0/Android/obb\n\
         ===ADB_MON_SPLIT===\n26\n===ADB_MON_SPLIT===\n1\n===ADB_MON_SPLIT===\n(top out)\n";
    let out = parse_batch_output(stdout);
    assert_eq!(out.ram, "72.3% (1.4GB/1.9GB)");
    assert_eq!(out.data_space.as_deref(), Some("6% (574M used/8.9G avail)"));
    assert_eq!(out.data_pct, Some(6.0));
    assert_eq!(out.err_count, 26);
    assert_eq!(out.is_booting, Some(false));
    assert_eq!(out.swap.as_deref(), Some("445.0 MB"));
}

#[test]
fn test_parse_df_data_section_skips_header_row() {
    // The header's `Use%` is not a value.
    assert_eq!(parse_df_data_section("Filesystem Size Used Avail Use% Mounted on\n"), (None, None));
}

#[test]
fn test_parse_df_data_section_prefers_row_naming_data() {
    // Synthetic: no captured image prints more than one row for `df -h /data`.
    assert_eq!(
        parse_df_data_section(
            "/dev/block/dm-34 10G 574M 8.9G 6% /storage/emulated/0/Android/obb\n\
             /dev/block/dm-35 20G 2.0G 18G 10% /data"
        ),
        (Some("10% (2.0G used/18G avail)".to_string()), Some(10.0))
    );
}

#[test]
fn test_parse_meminfo_reads_swap_on_api28_29_images() {
    // Captured from API 29 and API 28 google_apis x86_64 emulators, where `vmstat` fails with
    // "Bad '/proc/vmstat': Permission denied". The API 29 guest had 380 MB swapped out.
    let api29 = "MemTotal:        2040256 kB\nMemFree:          123444 kB\nMemAvailable:     700088 kB\n\
         Buffers:           22828 kB\nCached:           743748 kB\nSwapCached:        11812 kB\n\
         SwapTotal:       1530188 kB\nSwapFree:        1141068 kB\n";
    assert_eq!(
        parse_meminfo(api29),
        ("65.7% (1.3GB/1.9GB)".to_string(), Some("380.0 MB".to_string()))
    );
    let api28 = "MemTotal:        2046740 kB\nMemFree:          289244 kB\nMemAvailable:    1152960 kB\n\
         SwapTotal:             0 kB\nSwapFree:              0 kB\n";
    assert_eq!(parse_meminfo(api28).1.as_deref(), Some("0.0 MB"));

    // No swap lines means no swap figure, not zero.
    assert_eq!(
        parse_meminfo("MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n"),
        ("50.0% (1.9GB/3.8GB)".to_string(), None)
    );
}

#[test]
fn test_batch_stats_script_runs_on_api24_to_28_images() {
    // API 24 `logcat` fails on `-b main,crash` ("Unable to open log device"), which left the
    // error count at 0, and `vmstat` cannot read /proc/vmstat on API 28-29. API 24 toolbox
    // `top` crashed with "Floating point exception" in 1 of 15 live runs.
    assert!(BATCH_STATS_SCRIPT.contains("logcat -d -t 500 -b main -b crash *:E"));
    assert!(!BATCH_STATS_SCRIPT.contains("main,crash"));
    assert!(!BATCH_STATS_SCRIPT.contains("vmstat"));
    assert!(BATCH_STATS_SCRIPT.ends_with("(top -b -n 2 -d 0.2 || top -n 2 -d 1 || top -n 2 -d 1)"));
    assert_eq!(BATCH_STATS_SCRIPT.matches("_SPLIT===").count(), 4);
}

#[test]
fn test_effective_uid_and_sudo_user_distinguish_euid_from_ruid_and_nondumpable() {
    // Setuid/seteuid(0): real=1000, effective=0 -> must return effective (0), not real (1000).
    let seteuid_root = "Name:\temulator_mon\nUid:\t1000\t0\t0\t0\nGid:\t1000\t0\t0\t0\n";
    let euid_root = resolve_effective_uid_with(Some(seteuid_root), || panic!("no fallback"));
    assert_eq!(euid_root, Some(0));
    assert_eq!(active_sudo_user_with(euid_root, Some("alice")), Some("alice"));

    // Dropped root privileges via seteuid(501): real=0, effective=501 -> must return 501
    // and refuse `sudo -n -u` invocation (`None`).
    let dropped_root = "Name:\temulator_mon\nUid:\t0\t501\t0\t501\n";
    let euid_dropped = resolve_effective_uid_with(Some(dropped_root), || panic!("no fallback"));
    assert_eq!(euid_dropped, Some(501));
    assert_eq!(active_sudo_user_with(euid_dropped, Some("alice")), None);

    // Non-dumpable (`PR_SET_DUMPABLE=0`) unprivileged status reports true euid (1000), never 0.
    let nondump = "Name:\temulator_mon\nDumpable:\t0\nUid:\t1000\t1000\t1000\t1000\n";
    let euid_nondump = resolve_effective_uid_with(Some(nondump), || Some(0));
    assert_eq!(euid_nondump, Some(1000));
    assert_eq!(active_sudo_user_with(euid_nondump, Some("alice")), None);

    // Missing `/proc/self/status` (macOS/BSD) falls back cleanly to `query_process_self_euid`.
    let tmp = tempfile::tempdir().unwrap();
    let status_file = tmp.path().join("proc_status");
    std::fs::write(&status_file, dropped_root).unwrap();
    assert_eq!(resolve_effective_uid_from(&status_file, || Some(0)), Some(501));
    assert_eq!(
        resolve_effective_uid_from(&tmp.path().join("missing_status"), || Some(502)),
        Some(502)
    );
    assert_eq!(resolve_effective_uid_with(Some("Uid:\t1000\n"), || None), None);

    #[cfg(unix)]
    {
        let euid = effective_uid().expect("effective_uid must resolve on Unix");
        assert_eq!(Some(euid), effective_uid());
        assert_eq!(Some(euid), query_process_self_euid());
    }
}
