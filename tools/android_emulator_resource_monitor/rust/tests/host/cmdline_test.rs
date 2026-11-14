use super::*;

#[test]
fn test_parse_cmdline() {
    let cmdline = vec![
        OsString::from("emulator"),
        OsString::from("-avd"),
        OsString::from("Pixel_6_API_31"),
        OsString::from("-port"),
        OsString::from("5554"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5554));
    assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
}

#[test]
fn test_parse_cmdline_concatenated_avd() {
    let cmdline = vec![OsString::from("emulator"), OsString::from("-avdPixel_6_API_31")];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, None);
    assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
}

#[test]
fn test_parse_cmdline_concatenated_full() {
    let cmdline = vec![OsString::from(
        "emulator-netdelaynone-netspeedfull-avdMedium_Phone_37_Pro-qt-hide-window",
    )];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, None);
    assert_eq!(avd, Some("Medium_Phone_37_Pro".to_string()));
}

#[test]
fn test_parse_cmdline_at_avd() {
    let cmdline = vec![OsString::from("emulator"), OsString::from("@Pixel_6_API_31")];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, None);
    assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
}

#[test]
fn test_parse_cmdline_qemu_next_device_args() {
    let cmdline = vec![
        OsString::from("qemu-system-x86_64"),
        OsString::from("-device"),
        OsString::from("avdstart,serial_number=5554,avd_name=Pixel_7"),
        OsString::from("-device"),
        OsString::from("virtio-goldfish-adb,host_port=5555"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5554));
    assert_eq!(avd, Some("Pixel_7".to_string()));
}

#[test]
fn test_parse_cmdline_fishtank() {
    let cmdline = vec![
        OsString::from("fishtank"),
        OsString::from("@Pixel_7"),
        OsString::from("-fishtank"),
        OsString::from("5554"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5554));
    assert_eq!(avd, Some("Pixel_7".to_string()));

    let cmdline_no_avd =
        vec![OsString::from("fishtank"), OsString::from("-fishtank"), OsString::from("5556")];
    let (port, avd) = parse_cmdline(&cmdline_no_avd);
    assert_eq!(port, Some(5556));
    assert_eq!(avd, None);
}

#[test]
fn test_parse_cmdline_name_flag() {
    let cmdline = vec![
        OsString::from("qemu-system-x86_64"),
        OsString::from("-name"),
        OsString::from("Pixel_Tablet"),
        OsString::from("-device"),
        OsString::from("virtio-goldfish-adb,host_port=5557"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5556));
    assert_eq!(avd, Some("Pixel_Tablet".to_string()));
}

#[test]
fn test_parse_cmdline_name_flag_guest_prefix() {
    let cmdline = vec![
        OsString::from("qemu-system-x86_64"),
        OsString::from("-name"),
        OsString::from("guest=Pixel_7_API_36,debug-threads=on"),
        OsString::from("-port"),
        OsString::from("5554"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5554));
    assert_eq!(avd, Some("Pixel_7_API_36".to_string()));
}

#[test]
fn test_parse_cmdline_equal_flags() {
    let cmdline = vec![
        OsString::from("qemu-system-x86_64"),
        OsString::from("-name=guest=Pixel_Tablet,process=qemu"),
        OsString::from("-port=5556"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5556));
    assert_eq!(avd, Some("Pixel_Tablet".to_string()));

    let cmdline_fish = vec![
        OsString::from("fishtank"),
        OsString::from("@Pixel_7"),
        OsString::from("-fishtank=5554"),
    ];
    let (port, avd) = parse_cmdline(&cmdline_fish);
    assert_eq!(port, Some(5554));
    assert_eq!(avd, Some("Pixel_7".to_string()));
}

#[test]
fn test_parse_cmdline_quoted_args() {
    let cmdline = vec![
        OsString::from("qemu-system-x86_64"),
        OsString::from("-device"),
        OsString::from("avdstart,serial_number=5554,avd_name=\"Pixel 7\""),
        OsString::from("-device=virtio-goldfish-adb,host_port=5555"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5554));
    assert_eq!(avd, Some("Pixel 7".to_string()));
}

#[test]
fn test_b2_parse_cmdline_avd_with_substring_avd() {
    let cmdline = vec![
        OsString::from("emulator"),
        OsString::from("-avd"),
        OsString::from("my-avd"),
        OsString::from("-ports"),
        OsString::from("5554,5557"),
        OsString::from("-device"),
        OsString::from("virtio-goldfish-adb,host_port=5555"),
    ];
    let (port, avd) = parse_cmdline(&cmdline);
    assert_eq!(port, Some(5554));
    assert_eq!(avd, Some("my-avd".to_string()));

    let cmdline2 =
        vec![OsString::from("emulator"), OsString::from("-avd"), OsString::from("my-avd-test")];
    let (_, avd2) = parse_cmdline(&cmdline2);
    assert_eq!(avd2, Some("my-avd-test".to_string()));
}

#[test]
fn test_parse_cmdline_netsim_endpoint() {
    // launch_qemu.cc passes the endpoint as a -device property.
    let worker = vec![
        OsString::from("qemu-system-x86_64"),
        OsString::from("-device"),
        OsString::from("avdstart,serial_number=5554,avd_name=Pixel_7"),
        OsString::from("-device"),
        OsString::from("netsim-connection,id=netsim,grpc_endpoint=localhost:8877"),
    ];
    assert_eq!(parse_cmdline_netsim_endpoint(&worker).as_deref(), Some("localhost:8877"));
    let quoted = vec![
        OsString::from("-device"),
        OsString::from("netsim-connection,grpc_endpoint='[::1]:8877',id=netsim"),
    ];
    assert_eq!(parse_cmdline_netsim_endpoint(&quoted).as_deref(), Some("[::1]:8877"));
    // A -no-netsim worker has no netsim-connection device.
    let no_netsim = vec![OsString::from("qemu-system-x86_64"), OsString::from("-no-netsim")];
    assert_eq!(parse_cmdline_netsim_endpoint(&no_netsim), None);
    assert_eq!(
        parse_cmdline_with_no_netsim(&[
            OsString::from("emulator"),
            OsString::from("-avd"),
            OsString::from("Pixel_7"),
            OsString::from("-no-netsim"),
        ]),
        (None, Some("Pixel_7".to_string()), true)
    );
    // Option values equal to -no-netsim / --no-netsim are consumed by their
    // parent option and must not flip no_netsim.
    assert_eq!(
        parse_cmdline_with_no_netsim(&[
            OsString::from("emulator"),
            OsString::from("-avd"),
            OsString::from("-no-netsim"),
            OsString::from("-port"),
            OsString::from("5554"),
        ]),
        (Some(5554), Some("-no-netsim".to_string()), false)
    );
    assert_eq!(
        parse_cmdline_with_no_netsim(&[
            OsString::from("qemu-system-x86_64"),
            OsString::from("-device"),
            OsString::from("--no-netsim"),
            OsString::from("-port"),
            OsString::from("5554"),
        ]),
        (Some(5554), None, false)
    );
}

#[test]
fn test_parse_cmdline_serial_number_beats_host_port_and_ignores_path_avd() {
    let cmdline = vec![
        OsString::from("/tmp/emulator-avd-ci/qemu-system-x86_64"),
        OsString::from("-device"),
        OsString::from("virtio-goldfish-adb,host_port=5557"),
        OsString::from("-device"),
        OsString::from("avdstart,serial_number=5554,avd_name=Pixel_7"),
    ];
    assert_eq!(
        parse_cmdline_with_no_netsim(&cmdline),
        (Some(5554), Some("Pixel_7".to_string()), false)
    );
}

#[test]
fn test_is_emulator_process_excludes_truncated_comm_and_script_args() {
    // Linux 15-byte TASK_COMM_LEN truncation of monitor binaries/scripts.
    assert!(!is_emulator_process("emulator-resour", None, &[]));
    assert!(!is_emulator_process("emulator_resour", None, &[]));
    assert!(!is_emulator_process(
        "emulator-resour",
        Some(std::path::Path::new("/usr/bin/python3")),
        &[OsString::from("python3"), OsString::from("./emulator-resource-monitor")]
    ));
    assert!(!is_emulator_process(
        "emulator_resour",
        Some(std::path::Path::new("/usr/bin/python3")),
        &[
            OsString::from("python3"),
            OsString::from("-u"),
            OsString::from("/opt/tools/emulator_resource_monitor.py")
        ]
    ));
    // Real QEMU worker whose image path happens to sit under a directory named
    // android_resource_monitor must still be classified as an emulator.
    assert!(is_emulator_process(
        "qemu-system-x86",
        Some(std::path::Path::new("/sdk/emulator/qemu-system-x86_64")),
        &[
            OsString::from("qemu-system-x86_64"),
            OsString::from("-sdcard"),
            OsString::from("/workspace/android_resource_monitor/sdcard.img")
        ]
    ));
}
