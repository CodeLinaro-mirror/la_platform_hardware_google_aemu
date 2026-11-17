use super::*;
use crate::host::{EmulatorProcess, HostStats};
use std::collections::HashMap;

#[test]
fn test_unmapped_emulators_ui() {
    let emulators = vec![EmulatorProcess {
        pid: 1234,
        port: None,
        avd_name: None,
        cpu_usage: 10.0,
        ram_mb: 100.0,
        threads: 4,
        context_switches: 100,
        gpu_load: None,
    }];

    let stats = HostStats {
        cpu_usage: 10.0,
        ram_used_gb: 1.0,
        ram_total_gb: 4.0,
        ram_percent: 25.0,
        swap_used_gb: 0.0,
        swap_total_gb: 0.0,
        swap_percent: 0.0,
        disk_read_mbs: 0.0,
        disk_write_mbs: 0.0,
        emulators,
        netsim_cpu: 0.0,
        netsim_ram_mb: 0.0,
        netsim_count: 0,
        netsim_daemons: vec![],
        emu_groups: HashMap::new(),
        gpu_percent: None,
        gpu_memory_mb: None,
        thermal_temp: None,
        paging_str: "N/A".to_string(),
        paging_in: 0.0,
        paging_out: 0.0,
        battery_str: "N/A".to_string(),
        battery_percent: None,
    };

    let guest_results = HashMap::new();
    let mut prev_stats = HashMap::new();
    let online_indicator_until = HashMap::new();

    let dashboard = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 80,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &online_indicator_until,
        netsim_snapshot: None,
        host_timed_out: false,
    });

    assert!(dashboard.contains("Unmapped Emulator CPU"));
    assert!(dashboard.contains("10.0% (PIDs: 1234)"));
}

#[test]
fn test_unmapped_emulators_clamping() {
    let mut emulators = Vec::new();
    for i in 0..5 {
        emulators.push(EmulatorProcess {
            pid: 1000 + i,
            port: None,
            avd_name: None,
            cpu_usage: 2.0,
            ram_mb: 20.0,
            threads: 1,
            context_switches: 10,
            gpu_load: None,
        });
    }

    let stats = HostStats {
        cpu_usage: 10.0,
        ram_used_gb: 1.0,
        ram_total_gb: 4.0,
        ram_percent: 25.0,
        swap_used_gb: 0.0,
        swap_total_gb: 0.0,
        swap_percent: 0.0,
        disk_read_mbs: 0.0,
        disk_write_mbs: 0.0,
        emulators,
        netsim_cpu: 0.0,
        netsim_ram_mb: 0.0,
        netsim_count: 0,
        netsim_daemons: vec![],
        emu_groups: HashMap::new(),
        gpu_percent: None,
        gpu_memory_mb: None,
        thermal_temp: None,
        paging_str: "N/A".to_string(),
        paging_in: 0.0,
        paging_out: 0.0,
        battery_str: "N/A".to_string(),
        battery_percent: None,
    };

    let guest_results = HashMap::new();
    let mut prev_stats = HashMap::new();
    let online_indicator_until = HashMap::new();

    let dashboard = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 120,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &online_indicator_until,
        netsim_snapshot: None,
        host_timed_out: false,
    });

    assert!(dashboard.contains("Unmapped Emulator CPU"));
    assert!(dashboard.contains("1000, 1001, 1002, 1003 ... (+1 more)"));
}

#[test]
fn test_print_row_nan_placeholder() {
    let stats = HostStats {
        cpu_usage: 10.0,
        ram_used_gb: 1.0,
        ram_total_gb: 4.0,
        ram_percent: 25.0,
        swap_used_gb: 0.0,
        swap_total_gb: 0.0,
        swap_percent: 0.0,
        disk_read_mbs: 0.0,
        disk_write_mbs: 0.0,
        emulators: vec![],
        netsim_cpu: 0.0,
        netsim_ram_mb: 0.0,
        netsim_count: 0,
        netsim_daemons: vec![],
        emu_groups: HashMap::new(),
        gpu_percent: None,
        gpu_memory_mb: None,
        thermal_temp: None,
        paging_str: "N/A".to_string(),
        paging_in: 0.0,
        paging_out: 0.0,
        battery_str: "N/A".to_string(),
        battery_percent: None,
    };

    let mut guest_results = HashMap::new();
    guest_results.insert(
        "emulator-5554".to_string(),
        GuestUiStats {
            model: "Mock Device".to_string(),
            avd_name: None,
            cpu_total: "NaN%".to_string(),
            cpu_adjusted: "NaN%".to_string(),
            ram: "NaN%".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: false,
        },
    );
    let mut prev_stats = HashMap::new();
    let online_indicator_until = HashMap::new();

    let dashboard = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 80,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &online_indicator_until,
        netsim_snapshot: None,
        host_timed_out: false,
    });

    assert!(dashboard.contains("Total Device CPU Usage"));
}

#[test]
fn test_ansi_stripping_truncation() {
    let stats = HostStats {
        cpu_usage: 10.0,
        ram_used_gb: 1.0,
        ram_total_gb: 4.0,
        ram_percent: 25.0,
        swap_used_gb: 0.0,
        swap_total_gb: 0.0,
        swap_percent: 0.0,
        disk_read_mbs: 0.0,
        disk_write_mbs: 0.0,
        emulators: vec![],
        netsim_cpu: 0.0,
        netsim_ram_mb: 0.0,
        netsim_count: 0,
        netsim_daemons: vec![],
        emu_groups: HashMap::new(),
        gpu_percent: None,
        gpu_memory_mb: None,
        thermal_temp: None,
        paging_str: "N/A".to_string(),
        paging_in: 0.0,
        paging_out: 0.0,
        battery_str: "N/A".to_string(),
        battery_percent: None,
    };

    let mut guest_results = HashMap::new();
    let long_val = "\x1b[91mVery Long String That Will Be Truncated\x1b[0m";
    guest_results.insert(
        "emulator-5554".to_string(),
        GuestUiStats {
            model: "Mock Device".to_string(),
            avd_name: None,
            cpu_total: "10%".to_string(),
            cpu_adjusted: "5%".to_string(),
            ram: long_val.to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: false,
        },
    );
    let mut prev_stats = HashMap::new();
    let online_indicator_until = HashMap::new();

    let dashboard = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 80,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &online_indicator_until,
        netsim_snapshot: None,
        host_timed_out: false,
    });

    assert!(dashboard.contains("Very Long"));
}

#[test]
fn test_truncated_guest_title_keeps_border_aligned() {
    let stats = HostStats {
        cpu_usage: 10.0,
        ram_used_gb: 1.0,
        ram_total_gb: 4.0,
        ram_percent: 25.0,
        swap_used_gb: 0.0,
        swap_total_gb: 0.0,
        swap_percent: 0.0,
        disk_read_mbs: 0.0,
        disk_write_mbs: 0.0,
        emulators: vec![],
        netsim_cpu: 0.0,
        netsim_ram_mb: 0.0,
        netsim_count: 0,
        netsim_daemons: vec![],
        emu_groups: HashMap::new(),
        gpu_percent: None,
        gpu_memory_mb: None,
        thermal_temp: None,
        paging_str: "N/A".to_string(),
        paging_in: 0.0,
        paging_out: 0.0,
        battery_str: "N/A".to_string(),
        battery_percent: None,
    };
    // Long enough that the title is cut and ends in '…' (3 UTF-8 bytes,
    // one column) ahead of the badge.
    let model =
        "x86_64 glasses emulator | Android 16 (API 36) | Build: BGQ1.260312.001.A2 | Res: 480x480";
    let mut guest_results = HashMap::new();
    guest_results.insert(
        "emulator-5554".to_string(),
        GuestUiStats {
            model: model.to_string(),
            avd_name: None,
            cpu_total: "10%".to_string(),
            cpu_adjusted: "5%".to_string(),
            ram: "26%".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: true,
        },
    );
    let mut prev_stats = HashMap::new();
    let online_indicator_until = HashMap::new();
    let re_ansi = ansi_regex();

    for term_width in [80u16, 121] {
        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        let top = dashboard.lines().find(|l| l.contains('┌')).unwrap();
        let header = dashboard.lines().find(|l| l.contains("[RECORDING BOOT STATS]")).unwrap();
        let header_plain = re_ansi.replace_all(header, "");
        assert!(header_plain.contains('…'), "{header_plain}");
        assert!(header_plain.ends_with('│'), "{header_plain}");
        assert_eq!(visible_width(re_ansi, header), visible_width(re_ansi, top), "{header_plain}");
    }
}

#[test]
fn test_long_host_specs_are_cut_to_the_box_width() {
    let specs = "Linux | AMD EPYC 7B13 64-Core Processor | 251.8 GB RAM | GPU: NVIDIA L4 (22.5GB)";
    let short_specs = "Linux | 8 GB RAM";
    let re_ansi = ansi_regex();
    for (hardware_specs, term_width) in [(specs, 77u16), (specs, 121), (short_specs, 77)] {
        let mut buf = Vec::new();
        render_header_section(
            &mut buf,
            &DashboardInput {
                host_stats: &crate::initial_host_stats(),
                guest_results: &HashMap::new(),
                prev_stats: &mut HashMap::new(),
                iter_count: 1,
                term_width,
                is_once: true,
                hardware_specs,
                force_clear: true,
                online_indicator_until: &HashMap::new(),
                netsim_snapshot: None,
                host_timed_out: false,
            },
            usize::from(term_width - 1),
        );
        let top = buf.iter().find(|l| l.contains('┌')).unwrap();
        let line = buf.iter().find(|l| l.contains("Host Specs:")).unwrap();
        let plain = re_ansi.replace_all(line, "");
        assert_eq!(visible_width(re_ansi, line), visible_width(re_ansi, top), "{plain}");
        assert!(plain.ends_with('│'), "{plain}");
        let fits = visible_width(re_ansi, &format!(" Host Specs: {hardware_specs}"))
            <= usize::from(term_width) - 5;
        assert_eq!(plain.contains(hardware_specs), fits, "{plain}");
        assert_eq!(plain.contains('…'), !fits, "{plain}");
    }
}

#[test]
fn test_ui_row_helpers_and_progress_bar() {
    assert_eq!(format_progress_bar(0.0, 10), "[          ]");
    assert_eq!(format_progress_bar(50.0, 10), "[█████     ]");
    assert_eq!(format_progress_bar(55.0, 10), "[█████▌    ]");
    assert_eq!(format_progress_bar(150.0, 10), "[██████████]");

    assert!(is_row_alert("Laptop Battery Status", "8%", 8.0));
    assert!(!is_row_alert("Laptop Battery Status", "25%", 25.0));
    assert!(is_row_alert("Host Thermal Temperature", "90.0°C", 90.0));
    assert!(is_row_alert("Complete System CPU", "95.0%", 95.0));
    assert!(!is_row_alert("Host QEMU CPU", "200.0%", 200.0));
    assert!(is_row_alert("Host QEMU CPU", "800.0%", 800.0));

    let mut prev = HashMap::new();
    assert_eq!(record_trend_arrow(&mut prev, "host_cpu", 10.0), "→");
    assert_eq!(record_trend_arrow(&mut prev, "host_cpu", 11.0), "↑");
    assert_eq!(record_trend_arrow(&mut prev, "host_cpu", 9.0), "↓");
}

#[test]
fn test_placeholder_no_alert_and_ansi_and_alert_badge_preserved() {
    let mut stats = crate::initial_host_stats();
    stats.ram_percent = 95.0;
    stats.ram_used_gb = 30.4;
    stats.ram_total_gb = 32.0;

    let mut guest_results = HashMap::new();
    guest_results.insert(
        "emulator-5554".to_string(),
        GuestUiStats {
            model: "Mock Device".to_string(),
            avd_name: None,
            cpu_total: "10%".to_string(),
            cpu_adjusted: "5%".to_string(),
            ram: "\x1b[91mVery Long String That Will Be Truncated\x1b[0m".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: false,
        },
    );
    let mut prev_stats = HashMap::new();
    let online_indicator_until = HashMap::new();
    let dashboard = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 80,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &online_indicator_until,
        netsim_snapshot: None,
        host_timed_out: false,
    });

    assert!(
        !dashboard.contains("Calculating... [!!]"),
        "Placeholder battery status must not alert:\n{dashboard}"
    );
    let host_ram_line = dashboard.lines().find(|l| l.contains("Complete System RAM")).unwrap();
    assert!(
        host_ram_line.contains("[!!]"),
        "Alerting 95% RAM row at width 80 must preserve [!!] suffix:\n{host_ram_line}"
    );
    let re_ansi = ansi_regex();
    let top = dashboard.lines().find(|l| l.contains('┌')).unwrap();
    let guest_ram_line = dashboard.lines().find(|l| l.contains("Guest RAM Usage")).unwrap();
    assert_eq!(
        visible_width(re_ansi, guest_ram_line),
        visible_width(re_ansi, top),
        "ANSI-colored value row must keep right border aligned:\n{guest_ram_line}"
    );
}

#[test]
fn test_guest_adjusted_cpu_trend_arrow_updates_across_frames() {
    let stats = crate::initial_host_stats();
    let mut prev_stats = HashMap::new();
    let online_indicator_until = HashMap::new();

    let mut guest_results = HashMap::new();
    guest_results.insert(
        "emulator-5554".to_string(),
        GuestUiStats {
            model: "Pixel 6".to_string(),
            avd_name: None,
            cpu_total: "40%".to_string(),
            cpu_adjusted: "10.0% (over 4 cores)".to_string(),
            ram: "50.0% (2.0GB/4.0GB)".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: false,
        },
    );

    let frame1 = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 120,
        is_once: false,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &online_indicator_until,
        netsim_snapshot: None,
        host_timed_out: false,
    });
    assert!(frame1.contains("10.0% (over 4 cores) →"));

    guest_results.get_mut("emulator-5554").unwrap().cpu_adjusted =
        "80.0% (over 4 cores)".to_string();
    let frame2 = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 2,
        term_width: 120,
        is_once: false,
        hardware_specs: "Specs",
        force_clear: false,
        online_indicator_until: &online_indicator_until,
        netsim_snapshot: None,
        host_timed_out: false,
    });
    assert!(
        frame2.contains("80.0% (over 4 cores) ↑"),
        "Expected upward trend arrow on frame 2, got:\n{frame2}"
    );
}

#[test]
fn test_ui_placeholder_color_qemu_ram_blue_and_offline_errs_na() {
    let mut stats = crate::initial_host_stats();
    stats.battery_str = "N/A".to_string();
    stats.emulators = vec![EmulatorProcess {
        pid: 100,
        port: Some(5554),
        avd_name: Some("Pixel_6".to_string()),
        cpu_usage: 50.0,
        ram_mb: 2048.0,
        threads: 40,
        context_switches: 0,
        gpu_load: None,
    }];
    let mut guest_results = HashMap::new();
    guest_results.insert(
        "emulator-5554".to_string(),
        GuestUiStats {
            model: "Pixel 6".to_string(),
            avd_name: None,
            cpu_total: "NaN%".to_string(),
            cpu_adjusted: "N/A (offline)".to_string(),
            ram: "N/A (offline)".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: true,
        },
    );
    let mut prev_stats = HashMap::new();
    let online = HashMap::new();
    let dashboard = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 100,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &online,
        netsim_snapshot: None,
        host_timed_out: false,
    });
    let qemu_ram_line = dashboard.lines().find(|l| l.contains("Host QEMU RAM")).unwrap();
    let cpu_tot_line = dashboard.lines().find(|l| l.contains("Total Device CPU Usage")).unwrap();
    let errs_line = dashboard.lines().find(|l| l.contains("Guest Logcat Errors")).unwrap();
    assert!(
        qemu_ram_line.contains("\x1b[94m2048.0 MB"),
        "Host QEMU RAM should be colored BLUE (\\x1b[94m) like Python, got: {qemu_ram_line:?}"
    );
    assert!(
        cpu_tot_line.contains("\x1b[96m["),
        "NaN% placeholder row should be colored CYAN (\\x1b[96m), got: {cpu_tot_line:?}"
    );
    assert!(
        errs_line.contains("N/A"),
        "Offline guest should show N/A for Guest Logcat Errors, got: {errs_line:?}"
    );
}

#[test]
fn test_timed_out_host_and_guest_rows_show_timeout_without_trend() {
    let mut stats = crate::initial_host_stats();
    stats.cpu_usage = 42.0;
    let mut guest = GuestUiStats {
        model: "Pixel 6".to_string(),
        avd_name: None,
        cpu_total: "40%".to_string(),
        cpu_adjusted: "10.0% (over 4 cores)".to_string(),
        ram: "50.0% (2.0GB/4.0GB)".to_string(),
        data_space: None,
        swap: None,
        err_count: 0,
        is_disconnected: false,
        recording_boot_stats: false,
    };
    guest.mark_timed_out();
    guest.mark_timed_out();
    assert_eq!(guest.cpu_adjusted, "10.0% (over 4 cores) (Timeout)");
    let guest_results = HashMap::from([("emulator-5554".to_string(), guest)]);
    let mut prev_stats = HashMap::new();
    let dashboard = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 2,
        term_width: 120,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &HashMap::new(),
        netsim_snapshot: None,
        host_timed_out: true,
    });
    let cpu_line = dashboard.lines().find(|l| l.contains("Complete System CPU")).unwrap();
    assert!(cpu_line.contains("42.0% (Timeout)"), "{cpu_line}");
    assert!(!cpu_line.contains('→'), "{cpu_line}");
    let adj_line = dashboard.lines().find(|l| l.contains("Adjusted CPU Load")).unwrap();
    assert!(adj_line.contains("(over 4 cores) (Timeout)"), "{adj_line}");
    assert!(!adj_line.contains('→'), "{adj_line}");
    assert!(!prev_stats.contains_key("host_cpu"));
}

#[test]
fn test_physical_and_disconnected_cards_omit_absent_qemu_and_replacement_netsim() {
    let mut stats = crate::initial_host_stats();
    stats.emulators = vec![EmulatorProcess {
        pid: 999,
        port: Some(5554),
        avd_name: Some("Pixel_9".to_string()),
        cpu_usage: 80.0,
        ram_mb: 2048.0,
        threads: 16,
        context_switches: 0,
        gpu_load: None,
    }];
    let usb_guest = GuestUiStats {
        model: "Pixel 9 Pro".to_string(),
        avd_name: None,
        cpu_total: "20%".to_string(),
        cpu_adjusted: "5.0% (over 4 cores)".to_string(),
        ram: "40.0% (3.2GB/8.0GB)".to_string(),
        data_space: None,
        swap: None,
        err_count: 0,
        is_disconnected: false,
        recording_boot_stats: false,
    };
    let replaced_disc =
        GuestUiStats::disconnected("Pixel 6".to_string(), Some("Pixel_6".to_string()));
    let settled_disc =
        GuestUiStats::disconnected("Pixel 7".to_string(), Some("Pixel_7".to_string()));
    let guest_results = HashMap::from([
        ("HT4A12345678".to_string(), usb_guest),
        ("emulator-5554".to_string(), replaced_disc),
        ("emulator-5556".to_string(), settled_disc),
    ]);
    let ns_snap = NetsimDashboardSnapshot {
        daemon_rows: vec![],
        daemon_status_row: None,
        emulator_rows: HashMap::from([
            (
                "emulator-5554".to_string(),
                "netsim: CONNECTED 0.40s (wifi:0.5s) | disc=0".to_string(),
            ),
            (
                "emulator-5556".to_string(),
                "netsim: DISCONNECTED 0.30s (wifi:0.4s) | disc=1".to_string(),
            ),
        ]),
    };
    let mut prev_stats = HashMap::new();
    let dash = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &guest_results,
        prev_stats: &mut prev_stats,
        iter_count: 2,
        term_width: 110,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &HashMap::new(),
        netsim_snapshot: Some(&ns_snap),
        host_timed_out: false,
    });
    assert!(!dash.contains("Host QEMU CPU"), "Spurious Host QEMU CPU row:\n{dash}");
    assert!(!dash.contains("Host QEMU RAM"), "Spurious Host QEMU RAM row:\n{dash}");
    assert!(
        !dash.contains("netsim: CONNECTED"),
        "Replacement emulator's live Netsim Status leaked into [DISCONNECTED] card:\n{dash}"
    );
    assert!(
        dash.contains("netsim: DISCONNECTED"),
        "Settled Netsim Status should remain visible when port is unoccupied:\n{dash}"
    );
}

#[test]
fn test_display_os_name_and_gpu_fallback_text() {
    assert_eq!(display_os_name("Darwin"), "macOS");
    assert_eq!(display_os_name("Linux"), "Linux");

    let mut stats = crate::initial_host_stats();
    stats.gpu_percent = None;
    stats.gpu_memory_mb = None;
    let mut prev_stats = HashMap::new();
    let dash_na = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &HashMap::new(),
        prev_stats: &mut prev_stats,
        iter_count: 1,
        term_width: 100,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &HashMap::new(),
        netsim_snapshot: None,
        host_timed_out: false,
    });
    let gpu_line = dash_na.lines().find(|l| l.contains("Host GPU Load")).unwrap();
    assert!(gpu_line.contains("N/A"), "{gpu_line}");
    assert_eq!(gpu_line.contains("Requires sudo"), cfg!(target_os = "linux"), "{gpu_line}");
    let gpu_mem_line = dash_na.lines().find(|l| l.contains("Host GPU Memory")).unwrap();
    assert!(gpu_mem_line.contains("N/A"), "{gpu_mem_line}");

    stats.gpu_percent = Some(0.0);
    stats.gpu_memory_mb = Some(416.6);
    let dash_zero = build_dashboard(DashboardInput {
        host_stats: &stats,
        guest_results: &HashMap::new(),
        prev_stats: &mut prev_stats,
        iter_count: 2,
        term_width: 100,
        is_once: true,
        hardware_specs: "Specs",
        force_clear: true,
        online_indicator_until: &HashMap::new(),
        netsim_snapshot: None,
        host_timed_out: false,
    });
    let gpu_zero_line = dash_zero.lines().find(|l| l.contains("Host GPU Load")).unwrap();
    assert!(gpu_zero_line.contains("0.0%"), "{gpu_zero_line}");
    let gpu_zero_mem = dash_zero.lines().find(|l| l.contains("Host GPU Memory")).unwrap();
    assert!(gpu_zero_mem.contains("416.6MB"), "{gpu_zero_mem}");
}
