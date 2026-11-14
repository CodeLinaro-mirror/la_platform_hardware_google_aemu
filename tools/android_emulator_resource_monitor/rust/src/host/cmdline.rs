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
#[path = "../../tests/host/cmdline_test.rs"]
mod tests;
