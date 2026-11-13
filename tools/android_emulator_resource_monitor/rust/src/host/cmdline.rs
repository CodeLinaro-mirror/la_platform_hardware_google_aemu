use std::ffi::OsString;

const MAX_PARENT_WALK_DEPTH: usize = 16;

fn extract_device_prop(s: &str, key: &str) -> Option<String> {
    if let Some(pos) = s.find(key) {
        let rest = &s[pos + key.len()..];
        let val = if let Some(comma_pos) = rest.find(',') { &rest[..comma_pos] } else { rest };
        let cleaned = val.trim_matches(|c| c == '\'' || c == '"');
        if !cleaned.is_empty() {
            return Some(cleaned.to_string());
        }
    }
    None
}

fn parse_avd_value(raw: &str) -> Option<String> {
    let clean = raw.trim_matches(|c| c == '\'' || c == '"');
    let clean = clean.strip_prefix("guest=").unwrap_or(clean);
    let val = clean.split(',').next().unwrap_or("");
    (!val.is_empty()).then(|| val.to_string())
}

fn parse_concatenated_avd(arg: &str) -> Option<String> {
    let target = if arg.starts_with("-avd") && !arg.starts_with("-avd_name=") {
        arg
    } else {
        let base = arg.rsplit(['/', '\\']).next().unwrap_or(arg);
        if base.contains("emulator") && base.contains("-avd") {
            base
        } else {
            return None;
        }
    };
    let idx = target.find("-avd")?;
    let rest = target[idx + 4..].trim_matches(|c| c == '\'' || c == '"');
    let candidate = match rest.find('-') {
        Some(end_idx) => &rest[..end_idx],
        None => rest,
    };
    (!candidate.is_empty()).then(|| candidate.to_string())
}

fn extract_device_port_and_avd(
    arg: &str,
    explicit_port: &mut Option<u16>,
    fallback_port: &mut Option<u16>,
    explicit_avd: &mut Option<String>,
    fallback_avd: &mut Option<String>,
) {
    if explicit_port.is_none() {
        *explicit_port =
            extract_device_prop(arg, "serial_number=").and_then(|val| val.parse::<u16>().ok());
    }
    if fallback_port.is_none() {
        *fallback_port = extract_device_prop(arg, "host_port=")
            .and_then(|val| val.parse::<u16>().ok())
            .filter(|&hp| hp > 0)
            .map(|hp| hp - 1);
    }
    if explicit_avd.is_none() {
        *explicit_avd = extract_device_prop(arg, "avd_id=");
    }
    if fallback_avd.is_none() {
        *fallback_avd = extract_device_prop(arg, "avd_name=");
    }
}

pub(super) fn parse_cmdline_details(
    cmdline: &[OsString],
) -> (Option<u16>, Option<String>, Option<String>, bool) {
    let mut explicit_port = None;
    let mut fallback_port = None;
    let mut explicit_avd = None;
    let mut fallback_avd = None;
    let mut no_netsim = false;
    let mut skip_next = false;

    for i in 0..cmdline.len() {
        if skip_next {
            skip_next = false;
            continue;
        }
        let arg = cmdline[i].to_str().unwrap_or("");
        if arg == "-no-netsim" || arg == "--no-netsim" {
            no_netsim = true;
            continue;
        }
        if (arg == "-port" || arg == "-ports" || arg == "-fishtank") && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("");
            let val = next_arg.split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                explicit_port = Some(p);
            }
            skip_next = true;
        } else if arg.starts_with("-port=")
            || arg.starts_with("-ports=")
            || arg.starts_with("-fishtank=")
        {
            let val = arg.split('=').nth(1).unwrap_or("").split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                explicit_port = Some(p);
            }
        } else if (arg == "-avd" || arg == "-name") && i + 1 < cmdline.len() {
            if let Some(val) = parse_avd_value(cmdline[i + 1].to_str().unwrap_or("")) {
                explicit_avd = Some(val);
            }
            skip_next = true;
        } else if let Some(stripped) =
            arg.strip_prefix("-avd=").or_else(|| arg.strip_prefix("-name="))
        {
            if let Some(val) = parse_avd_value(stripped) {
                explicit_avd = Some(val);
            }
        } else if let Some(val) = parse_concatenated_avd(arg) {
            explicit_avd = Some(val);
        } else if arg.starts_with('@') && arg.len() > 1 {
            let val = arg[1..].trim_matches(|c| c == '\'' || c == '"');
            if !val.is_empty() {
                explicit_avd = Some(val.to_string());
            }
        }

        extract_device_port_and_avd(
            arg,
            &mut explicit_port,
            &mut fallback_port,
            &mut explicit_avd,
            &mut fallback_avd,
        );

        if arg == "-device" && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("");
            extract_device_port_and_avd(
                next_arg,
                &mut explicit_port,
                &mut fallback_port,
                &mut explicit_avd,
                &mut fallback_avd,
            );
            skip_next = true;
        }
    }

    (explicit_port.or(fallback_port), explicit_avd, fallback_avd, no_netsim)
}

#[cfg(test)]
pub fn parse_cmdline_with_no_netsim(cmdline: &[OsString]) -> (Option<u16>, Option<String>, bool) {
    let (port, explicit_avd, fallback_avd, no_netsim) = parse_cmdline_details(cmdline);
    (port, explicit_avd.or(fallback_avd), no_netsim)
}

#[cfg(test)]
pub fn parse_cmdline(cmdline: &[OsString]) -> (Option<u16>, Option<String>) {
    let (port, avd_name, _) = parse_cmdline_with_no_netsim(cmdline);
    (port, avd_name)
}

/// The netsim gRPC endpoint that the QEMU-next launcher passes to the worker
/// as `-device netsim-connection,id=netsim,grpc_endpoint=<host:port>`.
pub(super) fn parse_cmdline_netsim_endpoint(cmdline: &[OsString]) -> Option<String> {
    cmdline.iter().find_map(|arg| extract_device_prop(arg.to_str()?, "grpc_endpoint="))
}

fn extract_basenames(
    name: &str,
    exe: Option<&std::path::Path>,
    cmd: &[OsString],
) -> (String, String, String) {
    let name_low = name.to_lowercase();
    let exe_base = exe
        .and_then(|p| p.file_name())
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let cmd0_base = cmd
        .first()
        .and_then(|s| std::path::Path::new(s).file_name())
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    (name_low, exe_base, cmd0_base)
}

pub fn is_netsimd_process(name: &str, exe: Option<&std::path::Path>, cmd: &[OsString]) -> bool {
    let (name_low, exe_base, cmd0_base) = extract_basenames(name, exe, cmd);
    fn strip_exe(s: &str) -> &str {
        s.strip_suffix(".exe").unwrap_or(s)
    }
    strip_exe(&name_low) == "netsimd"
        || strip_exe(&exe_base) == "netsimd"
        || strip_exe(&cmd0_base) == "netsimd"
}

fn is_monitor_token(t: &str) -> bool {
    t.contains("resource_monitor")
        || t.contains("resource-monitor")
        || t.contains("emulator_resour")
        || t.contains("emulator-resour")
}

pub fn is_emulator_process(name: &str, exe: Option<&std::path::Path>, cmd: &[OsString]) -> bool {
    let (name_low, exe_base, cmd0_base) = extract_basenames(name, exe, cmd);
    if is_netsimd_process(name, exe, cmd) {
        return false;
    }
    let tokens = [&name_low, &exe_base, &cmd0_base];
    for t in tokens {
        if t.contains("qemu-img")
            || t.starts_with("crashpad")
            || t.contains("terminal")
            || t.contains("crash")
            || t.starts_with("netsim")
            || is_monitor_token(t)
            || t.contains("emulator-check")
        {
            return false;
        }
    }
    for arg in cmd {
        let arg_str = arg.to_string_lossy();
        let base = arg_str.rsplit(['/', '\\']).next().unwrap_or(&arg_str).to_lowercase();
        if is_monitor_token(&base) {
            return false;
        }
    }
    let is_emu_token = |t: &str| {
        let b = t.strip_suffix(".exe").unwrap_or(t);
        b.starts_with("qemu-system")
            || b == "qemu"
            || b == "emulator"
            || b.starts_with("emulator64")
            || b.starts_with("emulator-")
            || b.contains("launcherloop")
            || b.contains("crosvm")
            || b.contains("fishtank")
    };
    is_emu_token(&name_low) || is_emu_token(&exe_base) || is_emu_token(&cmd0_base)
}

pub(super) fn is_worker_process(
    name: &str,
    exe: Option<&std::path::Path>,
    cmd: &[OsString],
) -> bool {
    let (name_low, exe_base, cmd0_base) = extract_basenames(name, exe, cmd);
    let is_worker_token = |t: &str| {
        let b = t.strip_suffix(".exe").unwrap_or(t);
        b.starts_with("qemu-system") || b == "qemu" || b.contains("crosvm")
    };
    is_worker_token(&name_low) || is_worker_token(&exe_base) || is_worker_token(&cmd0_base)
}

/// Cheap pre-filter: every process that any grouping rule could accept.
pub(super) fn is_candidate_process(
    name: &str,
    exe: Option<&std::path::Path>,
    cmd: &[OsString],
) -> bool {
    is_netsimd_process(name, exe, cmd) || is_emulator_process(name, exe, cmd)
}

pub(super) fn is_descendant_of_self(
    pid: u32,
    initial_parent: Option<u32>,
    self_pid: u32,
    parent_of: &dyn Fn(u32) -> Option<u32>,
) -> bool {
    if self_pid == 0 {
        return false;
    }
    let mut curr = Some(pid);
    for step in 0..MAX_PARENT_WALK_DEPTH {
        let Some(p) = curr else {
            break;
        };
        if p == self_pid {
            return true;
        }
        curr = if step == 0 { initial_parent.or_else(|| parent_of(p)) } else { parent_of(p) };
    }
    false
}

#[cfg(test)]
mod tests {
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
}
