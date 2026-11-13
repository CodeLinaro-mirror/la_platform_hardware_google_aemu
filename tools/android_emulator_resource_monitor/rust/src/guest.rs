use crate::provider::GuestStats;
use std::collections::HashMap;
use std::time::Duration;
use tokio::process::Command;

const ADB_COMMAND_TIMEOUT: Duration = Duration::from_secs(5);
const BATCH_SEPARATOR: &str = "===ADB_MON_SPLIT===";
const KB_PER_MB: f32 = 1024.0;
const KB_PER_GB: f32 = 1_048_576.0;

/// Parsed sections of the single-command `adb shell` guest stats batch.
#[derive(Debug, Clone, PartialEq)]
pub struct BatchOutput<'a> {
    pub ram: String,
    pub is_booting: Option<bool>,
    pub data_space: Option<String>,
    pub err_count: u32,
    pub data_pct: Option<f32>,
    pub top_output: &'a str,
    pub swap: Option<String>,
}

pub fn parse_model_info(stdout: &str) -> Option<(String, bool)> {
    let parts: Vec<&str> = stdout.split("|!|").collect();
    if parts.len() >= 6 {
        let model = parts[0].trim().to_string();
        if model.is_empty() {
            return None;
        }
        let version = parts[1].trim().to_string();
        let api = parts[2].trim().to_string();
        let build = parts[3].trim().to_string();
        let size_lower = parts[4].to_lowercase();
        let size = if size_lower.contains("size:") {
            size_lower.split("size:").last().unwrap_or("N/A").trim().to_string()
        } else {
            "N/A".to_string()
        };
        let is_booting = parts[5].trim() != "1";

        let display_name = format!(
            "{} | Android {} (API {}) | Build: {} | Res: {}",
            model, version, api, build, size
        );
        Some((display_name, is_booting))
    } else {
        None
    }
}

/// Whether the cached model banner is missing or incomplete and should be re-queried.
pub fn should_retry_model(model: &str, is_booting: bool) -> bool {
    let model_lower = model.to_lowercase();
    model.is_empty()
        || !model_lower.contains("android")
        || model_lower.contains("unknown model")
        || model_lower.contains("error:")
        || model_lower.contains("offline")
        || (model_lower.contains("res: n/a") && is_booting)
}

/// Parses the `/proc/meminfo` section into `(RAM display, swap used)`.
///
/// Swap used is `SwapTotal - SwapFree`, the figure `vmstat` reports as `swpd`. `vmstat` itself
/// cannot open `/proc/vmstat` on API 28-29, so it is not part of the batch.
fn parse_meminfo(section: &str) -> (String, Option<String>) {
    let mut mem_total = 0_u64;
    let mut mem_avail = 0_u64;
    let mut mem_free = 0_u64;
    let mut buffers = 0_u64;
    let mut cached = 0_u64;
    let mut swap_total = None;
    let mut swap_free = None;

    for line in section.lines() {
        let lp: Vec<&str> = line.split_whitespace().collect();
        if lp.len() >= 2 {
            let val = lp[1].parse::<u64>().unwrap_or(0);
            if lp[0].starts_with("MemTotal") {
                mem_total = val;
            } else if lp[0].starts_with("MemAvailable") {
                mem_avail = val;
            } else if lp[0].starts_with("MemFree") {
                mem_free = val;
            } else if lp[0].starts_with("Buffers") {
                buffers = val;
            } else if lp[0] == "Cached:" {
                cached = val;
            } else if lp[0] == "SwapTotal:" {
                swap_total = lp[1].parse::<u64>().ok();
            } else if lp[0] == "SwapFree:" {
                swap_free = lp[1].parse::<u64>().ok();
            }
        }
    }
    let swap = swap_total
        .zip(swap_free)
        .map(|(total, free)| format!("{:.1} MB", total.saturating_sub(free) as f32 / KB_PER_MB));
    if mem_avail == 0 && (mem_free + buffers + cached) > 0 {
        mem_avail = mem_free + buffers + cached;
    }
    if mem_total == 0 {
        return ("N/A".to_string(), swap);
    }
    let used = mem_total.saturating_sub(mem_avail);
    let ram = format!(
        "{:.1}% ({:.1}GB/{:.1}GB)",
        (used as f32 / mem_total as f32) * 100.0,
        used as f32 / KB_PER_GB,
        mem_total as f32 / KB_PER_GB
    );
    (ram, swap)
}

/// Parses the `df -h /data` section into `(display, Use%)`.
///
/// The data row is picked by its numeric `Use%` column rather than its mount point: on API 30-33
/// `df` names a bind mount of the /data device (such as `/storage/emulated/0/Android/obb`)
/// instead of `/data` (b/583871630). The emulator images tested print a single row; if a `df`
/// variant prints several, a row naming `/data` wins.
fn parse_df_data_section(section: &str) -> (Option<String>, Option<f32>) {
    let mut first_row = None;
    for line in section.lines() {
        let dp: Vec<&str> = line.split_whitespace().collect();
        let Some((idx, pct)) = dp.iter().enumerate().find_map(|(i, s)| {
            s.strip_suffix('%').and_then(|n| n.parse::<f32>().ok()).map(|pct| (i, pct))
        }) else {
            continue;
        };
        if idx < 2 {
            continue;
        }
        let row =
            (Some(format!("{} ({} used/{} avail)", dp[idx], dp[idx - 2], dp[idx - 1])), Some(pct));
        if line.contains("/data") {
            return row;
        }
        first_row.get_or_insert(row);
    }
    first_row.unwrap_or((None, None))
}

pub fn parse_batch_output(stdout: &str) -> BatchOutput<'_> {
    let parts: Vec<&str> = stdout.split(BATCH_SEPARATOR).collect();
    let (ram, swap) =
        parts.first().map(|s| parse_meminfo(s)).unwrap_or_else(|| ("N/A".to_string(), None));
    let (data_space, data_pct) =
        parts.get(1).map(|s| parse_df_data_section(s)).unwrap_or((None, None));
    let err_count = parts.get(2).and_then(|s| s.trim().parse::<u32>().ok()).unwrap_or(0);
    let is_booting = if parts.len() >= 5 || (parts.len() == 4 && !parts[3].trim().is_empty()) {
        Some(parts[3].trim() != "1")
    } else {
        None
    };
    let top_output = parts.get(4).copied().unwrap_or("");

    BatchOutput { ram, is_booting, data_space, err_count, data_pct, top_output, swap }
}

pub fn parse_top_output(stdout: &str) -> (String, String) {
    let mut cpu_total = "N/A".to_string();
    let mut cpu_adjusted = "N/A".to_string();

    // Toolbox `top` (API 24) prints each frame's tick deltas instead of a `%cpu` line, such as
    // `User 3 + Nice 1 + Sys 3 + Idle 391 + IOW 0 + IRQ 0 + SIRQ 0 = 398`. Its first frame spans
    // only a few ticks, so a reply needs both `-n 2` frames and the last one is used. The kernel's
    // iowait counter can go backwards, so deltas may be negative.
    static RE_TOOLBOX_TICKS: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_toolbox_ticks = RE_TOOLBOX_TICKS.get_or_init(|| {
        regex::Regex::new(r"Idle (-?\d+) \+ IOW -?\d+ \+ IRQ -?\d+ \+ SIRQ -?\d+ = (-?\d+)")
            .unwrap()
    });
    let frames: Vec<_> = re_toolbox_ticks.captures_iter(stdout).collect();
    if let [_, .., ticks] = frames.as_slice() {
        if let (Ok(idle), Ok(total)) = (ticks[1].parse::<f32>(), ticks[2].parse::<f32>()) {
            if total > 0.0 {
                let busy = ((total - idle) / total * 100.0).clamp(0.0, 100.0);
                return (format!("{busy:.1}%"), format!("{busy:.1}% (Cores unknown)"));
            }
        }
    }

    let target_block = stdout.rsplit("Tasks:").next().unwrap_or(stdout);

    let re_ansi = crate::ui::ansi_regex();

    static RE_USAGE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_usage = RE_USAGE.get_or_init(|| regex::Regex::new(r"(?i)(\d+)\s*%?\s*cpu").unwrap());

    static RE_IDLE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_idle = RE_IDLE.get_or_init(|| regex::Regex::new(r"(?i)(\d+)\s*%?\s*idle").unwrap());

    static RE_ID: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_id =
        RE_ID.get_or_init(|| regex::Regex::new(r"(?i)(\d+(?:[.,]\d+)?)\s*%?\s*id\b").unwrap());

    for line in target_block.lines().take(10) {
        let clean_line = re_ansi.replace_all(line, "");
        let line_trimmed = clean_line.trim();

        let t_match = re_usage.captures(line_trimmed);
        let i_match = re_idle.captures(line_trimmed);

        if let (Some(t), Some(i)) = (t_match, i_match) {
            if let (Ok(t_val), Ok(i_val)) = (t[1].parse::<f32>(), i[1].parse::<f32>()) {
                if t_val >= 90.0 {
                    let diff = (t_val - i_val).max(0.0);
                    let cores = (t_val / 100.0).round().max(1.0);
                    cpu_total = format!("{:.0}%", diff);
                    cpu_adjusted = format!("{:.1}% (over {} cores)", diff / cores, cores);
                    break;
                }
            }
        } else if let Some(id_match) = re_id.captures(line_trimmed) {
            let id_str = id_match[1].replace(',', ".");
            if let Ok(id_val) = id_str.parse::<f32>() {
                let real_pct = (100.0 - id_val).max(0.0);
                cpu_total = format!("{:.1}%", real_pct);
                cpu_adjusted = format!("{:.1}% (Cores unknown)", real_pct);
                break;
            }
        }
    }

    (cpu_total, cpu_adjusted)
}

pub fn parse_adb_devices(stdout: &str) -> HashMap<String, String> {
    const VALID_STATES: &[&str] = &[
        "device",
        "offline",
        "unauthorized",
        "bootloader",
        "recovery",
        "sideload",
        "authorizing",
        "connecting",
        // `no permissions (...)`: missing udev rules; listed, but not queryable.
        "no permissions",
    ];
    let mut devices = HashMap::new();
    for raw_line in stdout.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('*') || line.starts_with("List of devices") {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 2 {
            let state = if parts[1..].starts_with(&["no", "permissions"]) {
                "no permissions"
            } else {
                parts[1]
            };
            if VALID_STATES.contains(&state) {
                devices.insert(parts[0].to_string(), state.to_string());
            }
        }
    }
    devices
}

const MODEL_INFO_SCRIPT: &str = "m=$(getprop ro.product.model); v=$(getprop ro.build.version.release); a=$(getprop ro.build.version.sdk); b=$(getprop ro.build.id); w=$(wm size 2>/dev/null); c=$(getprop sys.boot_completed); echo \"$m|!|$v|!|$a|!|$b|!|$w|!|$c\"";

/// One `adb shell` round trip per tick. The guest commands also have to run on API 24-28 images:
/// API 24 `logcat` rejects a comma-separated `-b` list, toybox `top` (API 26-28) rejects the
/// fractional `-d 0.2`, and toolbox `top` (API 24) rejects `-b`. Toolbox `top` also dies of a
/// divide-by-zero when its first frame spans no clock ticks, so the integer-delay form gets a
/// second try.
const BATCH_STATS_SCRIPT: &str = r#"cat /proc/meminfo; echo '===ADB_'"MON"'_SPLIT==='; df -h /data 2>/dev/null; echo '===ADB_'"MON"'_SPLIT==='; logcat -d -t 500 -b main -b crash *:E 2>/dev/null | grep -v "^---------" | wc -l; echo '===ADB_'"MON"'_SPLIT==='; getprop sys.boot_completed; echo '===ADB_'"MON"'_SPLIT==='; (top -b -n 2 -d 0.2 || top -n 2 -d 1 || top -n 2 -d 1)"#;

/// Rejects empty or `adb`-error stdout without touching non-zero exit codes (since the final
/// `top` fallback in `BATCH_STATS_SCRIPT` exits non-zero on images where neither form runs).
pub(crate) fn sanitize_adb_shell_stdout(stdout: &str) -> Option<&str> {
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return None;
    }
    let check_region = if stdout.contains(BATCH_SEPARATOR) {
        let mut sections = stdout.splitn(5, BATCH_SEPARATOR);
        let p0 = sections.next().unwrap_or("");
        let p1 = sections.next().unwrap_or("");
        let p2 = sections.next().unwrap_or("");
        let p3 = sections.next().unwrap_or("");
        format!("{p0}\n{p1}\n{p2}\n{p3}")
    } else {
        trimmed.to_string()
    };
    let lower = check_region.to_lowercase();
    let has_adb_error = lower.starts_with("adb:")
        || lower.starts_with("error:")
        || ["adb:", "error:", "device offline", "unauthorized", "no devices"]
            .iter()
            .any(|m| lower.contains(m))
        || (!stdout.contains(BATCH_SEPARATOR)
            && !stdout.contains("|!|")
            && lower.contains("offline"));
    (!has_adb_error).then_some(stdout)
}

pub(crate) fn validate_sudo_user(raw: Option<&str>) -> Option<&str> {
    let u = raw?.trim();
    if u.is_empty()
        || u == "root"
        || u.starts_with('-')
        || !u.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'))
    {
        return None;
    }
    Some(u)
}

#[cfg(any(unix, test))]
pub(crate) fn parse_passwd_home(passwd_contents: &str, username: &str) -> Option<String> {
    for line in passwd_contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split(':');
        let Some(name) = fields.next() else {
            continue;
        };
        if name != username {
            continue;
        }
        let Some(home) = fields.nth(4).map(str::trim).filter(|h| !h.is_empty()) else {
            continue;
        };
        return Some(home.to_string());
    }
    None
}

#[cfg(any(unix, test))]
fn resolve_effective_uid_with(
    proc_status: Option<&str>,
    fallback: impl FnOnce() -> Option<u32>,
) -> Option<u32> {
    proc_status
        .and_then(crate::host::parse_proc_status_uids)
        .map(|(_, euid)| euid)
        .or_else(fallback)
}

#[cfg(unix)]
fn query_process_self_euid() -> Option<u32> {
    use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System, UpdateKind};
    let pid = Pid::from_u32(std::process::id());
    let mut sys = System::new();
    sys.refresh_processes_specifics(
        ProcessesToUpdate::Some(&[pid]),
        true,
        ProcessRefreshKind::nothing().with_user(UpdateKind::OnlyIfNotSet),
    );
    if let Some(euid) = sys.process(pid).and_then(|p| p.effective_user_id()).map(|u| **u) {
        return Some(euid);
    }
    let out = crate::netsim::run_with_timeout("id", &["-u"], Duration::from_secs(2))?;
    if !out.status.success() {
        return None;
    }
    String::from_utf8_lossy(&out.stdout).trim().parse::<u32>().ok()
}

#[cfg(any(unix, test))]
fn resolve_effective_uid_from(
    status_path: &std::path::Path,
    fallback: impl FnOnce() -> Option<u32>,
) -> Option<u32> {
    resolve_effective_uid_with(std::fs::read_to_string(status_path).ok().as_deref(), fallback)
}

/// Effective UID (`euid`) of the monitor process on Unix, cached for the process lifetime.
///
/// Kept distinct from `netsim::security::owner_uid()`, which resolves real UID (`ruid` / col 1 of
/// `/proc/self/status` and `sysinfo::Process::user_id()`) for daemon/filesystem ownership, whereas
/// passwordless `sudo -n -u <SUDO_USER> adb` requires `euid == 0` (`os.geteuid() == 0` in Python).
/// Never stats `/proc/self`, whose owner Linux resets to `0` under `PR_SET_DUMPABLE=0`.
#[cfg(unix)]
fn effective_uid() -> Option<u32> {
    static EUID: std::sync::OnceLock<Option<u32>> = std::sync::OnceLock::new();
    *EUID.get_or_init(|| {
        resolve_effective_uid_from(
            std::path::Path::new("/proc/self/status"),
            query_process_self_euid,
        )
    })
}

#[cfg(any(unix, test))]
fn active_sudo_user_with(euid: Option<u32>, raw_sudo_user: Option<&str>) -> Option<&str> {
    if euid != Some(0) {
        return None;
    }
    validate_sudo_user(raw_sudo_user)
}

#[cfg(unix)]
fn lookup_unix_user_home(username: &str) -> Option<String> {
    let valid = validate_sudo_user(Some(username))?;
    if let Ok(passwd) = std::fs::read_to_string("/etc/passwd") {
        if let Some(home) = parse_passwd_home(&passwd, valid) {
            return Some(home);
        }
    }
    if let Some(out) =
        crate::netsim::run_with_timeout("getent", &["passwd", valid], Duration::from_secs(2))
    {
        if out.status.success() {
            let text = String::from_utf8_lossy(&out.stdout);
            if let Some(home) = parse_passwd_home(&text, valid) {
                return Some(home);
            }
        }
    }
    for prefix in ["/Users", "/home"] {
        let cand = format!("{prefix}/{valid}");
        if std::path::Path::new(&cand).is_dir() {
            return Some(cand);
        }
    }
    None
}

fn active_sudo_user() -> Option<&'static str> {
    #[cfg(unix)]
    {
        static SUDO_TARGET: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();
        SUDO_TARGET
            .get_or_init(|| {
                let raw = std::env::var("SUDO_USER").ok();
                active_sudo_user_with(effective_uid(), raw.as_deref()).map(str::to_string)
            })
            .as_deref()
    }
    #[cfg(not(unix))]
    {
        None
    }
}

fn active_sudo_home() -> Option<String> {
    #[cfg(unix)]
    {
        active_sudo_user().and_then(lookup_unix_user_home)
    }
    #[cfg(not(unix))]
    {
        None
    }
}

pub(crate) fn adb_invocation<'a>(
    adb_bin: &'a str,
    sudo_user: Option<&str>,
) -> (&'a str, Vec<String>) {
    if let Some(valid_user) = validate_sudo_user(sudo_user) {
        (
            "sudo",
            vec!["-n".to_string(), "-u".to_string(), valid_user.to_string(), adb_bin.to_string()],
        )
    } else {
        (adb_bin, Vec::new())
    }
}

fn adb_command(program: &str) -> Command {
    let (exe, prefix_args) = adb_invocation(program, active_sudo_user());
    let mut cmd = Command::new(exe);
    cmd.args(prefix_args).stdin(std::process::Stdio::null()).kill_on_drop(true);
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

async fn run_adb_shell(adb_bin: &str, serial: &str, script: &str) -> Option<String> {
    let output = tokio::time::timeout(
        ADB_COMMAND_TIMEOUT,
        adb_command(adb_bin).args(["-s", serial, "shell", script]).output(),
    )
    .await
    .ok()?
    .ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    sanitize_adb_shell_stdout(&stdout).is_some().then(|| stdout.into_owned())
}

pub(crate) fn build_online_guest_stats(
    known_model: &str,
    mut is_booting: bool,
    model_stdout: Option<&str>,
    batch_stdout: Option<&str>,
) -> GuestStats {
    let mut model = known_model.to_string();
    let mut observed_booting: Option<bool> = None;

    if let Some(stdout) = model_stdout {
        if let Some((m, b)) = parse_model_info(stdout) {
            model = m;
            is_booting = b;
            observed_booting = Some(b);
        }
    }

    let Some(stdout) = batch_stdout else {
        return GuestStats::shell_failed(
            model,
            observed_booting.unwrap_or(is_booting),
            observed_booting.is_some(),
        );
    };

    let parsed = parse_batch_output(stdout);
    if let Some(b) = parsed.is_booting {
        observed_booting = Some(b);
    }
    let (cpu_total, cpu_adjusted) = parse_top_output(parsed.top_output);
    GuestStats {
        model,
        is_booting: observed_booting.unwrap_or(is_booting),
        boot_state_known: observed_booting.is_some(),
        cpu_total,
        cpu_adjusted,
        ram: parsed.ram,
        data_space: parsed.data_space,
        swap: parsed.swap,
        err_count: parsed.err_count,
        data_pct: parsed.data_pct,
    }
}

pub struct RealGuestProvider;

impl crate::provider::GuestProvider for RealGuestProvider {
    async fn get_devices(&self) -> HashMap<String, String> {
        let adb_bin = find_adb();
        let output =
            tokio::time::timeout(ADB_COMMAND_TIMEOUT, adb_command(adb_bin).arg("devices").output())
                .await;

        let Ok(Ok(output)) = output else {
            return HashMap::new();
        };

        let stdout = String::from_utf8_lossy(&output.stdout);
        parse_adb_devices(&stdout)
    }

    async fn get_guest_stats(
        &self,
        serial: &str,
        known_model: &str,
        is_booting: bool,
        status: &str,
    ) -> GuestStats {
        if status != "device" {
            return GuestStats::unavailable(known_model, is_booting, status);
        }

        let adb_bin = find_adb();
        let model_stdout = if should_retry_model(known_model, is_booting) {
            run_adb_shell(adb_bin, serial, MODEL_INFO_SCRIPT).await
        } else {
            None
        };
        let batch_stdout = run_adb_shell(adb_bin, serial, BATCH_STATS_SCRIPT).await;
        build_online_guest_stats(
            known_model,
            is_booting,
            model_stdout.as_deref(),
            batch_stdout.as_deref(),
        )
    }
}

/// Joins `parts` onto `root` with the separator style `root` already uses, so
/// Windows-style SDK paths resolve identically on every host.
fn join_sdk_path(root: &str, parts: &[&str]) -> String {
    let sep = if root.contains('\\') { '\\' } else { '/' };
    let mut out = root.trim_end_matches(['/', '\\']).to_string();
    for part in parts {
        out.push(sep);
        out.push_str(part);
    }
    out
}

fn adb_bin_candidates(prefer_exe: bool) -> [&'static str; 2] {
    if prefer_exe { ["adb.exe", "adb"] } else { ["adb", "adb.exe"] }
}

pub fn resolve_adb_path(
    android_home: Option<&str>,
    android_sdk_root: Option<&str>,
    home_dir: Option<&str>,
    exists_fn: &dyn Fn(&str) -> bool,
) -> Option<String> {
    for root in [android_home, android_sdk_root].into_iter().flatten() {
        if !root.is_empty() {
            let prefer_exe = cfg!(windows) || root.contains('\\');
            for bin in adb_bin_candidates(prefer_exe) {
                let cand = join_sdk_path(root, &["platform-tools", bin]);
                if exists_fn(&cand) {
                    return Some(cand);
                }
            }
        }
    }
    if let Some(home) = home_dir.filter(|h| !h.is_empty()) {
        let prefer_exe = cfg!(windows) || home.contains('\\');
        for sdk in [
            &["Android", "Sdk"][..],
            &["Library", "Android", "sdk"],
            &["AppData", "Local", "Android", "Sdk"],
        ] {
            for bin in adb_bin_candidates(prefer_exe) {
                let mut parts = sdk.to_vec();
                parts.extend(["platform-tools", bin]);
                let cand = join_sdk_path(home, &parts);
                if exists_fn(&cand) {
                    return Some(cand);
                }
            }
        }
    }
    None
}

pub(crate) fn resolve_adb_path_with_sudo(
    android_home: Option<&str>,
    android_sdk_root: Option<&str>,
    home_dir: Option<&str>,
    sudo_home_dir: Option<&str>,
    exists_fn: &dyn Fn(&str) -> bool,
) -> Option<String> {
    resolve_adb_path(android_home, android_sdk_root, home_dir, exists_fn).or_else(|| {
        let sudo_home = sudo_home_dir.filter(|h| !h.is_empty())?;
        resolve_adb_path(None, None, Some(sudo_home), exists_fn)
    })
}

/// Returns the adb binary to run: the first `adb` on PATH (like Python's
/// `shutil.which`), else the SDK copy (ANDROID_HOME, ANDROID_SDK_ROOT or the
/// default SDK location, including `SUDO_USER`'s home when running under sudo
/// on Unix), else plain `adb`. Resolved once per process.
pub fn find_adb() -> &'static str {
    static ADB: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    ADB.get_or_init(|| {
        let env = |key: &str| std::env::var(key).ok().filter(|v| !v.is_empty());
        // HOME is usually unset on Windows outside MSYS shells.
        let home = env("HOME").or_else(|| env("USERPROFILE"));
        let sudo_home = active_sudo_home();
        let is_file = |p: &str| std::path::Path::new(p).is_file();
        find_adb_on_path(std::env::var_os("PATH").as_deref(), &is_file)
            .or_else(|| {
                resolve_adb_path_with_sudo(
                    env("ANDROID_HOME").as_deref(),
                    env("ANDROID_SDK_ROOT").as_deref(),
                    home.as_deref(),
                    sudo_home.as_deref(),
                    &is_file,
                )
            })
            .unwrap_or_else(|| "adb".to_string())
    })
}

/// The first `adb` / `adb.exe` in the directories listed in `path_var`.
fn find_adb_on_path(
    path_var: Option<&std::ffi::OsStr>,
    is_file: &dyn Fn(&str) -> bool,
) -> Option<String> {
    find_adb_on_path_with_order(path_var, cfg!(windows), is_file)
}

fn find_adb_on_path_with_order(
    path_var: Option<&std::ffi::OsStr>,
    prefer_exe: bool,
    is_file: &dyn Fn(&str) -> bool,
) -> Option<String> {
    std::env::split_paths(path_var?)
        .flat_map(|dir| {
            let dir_prefer_exe = prefer_exe || dir.to_string_lossy().contains('\\');
            adb_bin_candidates(dir_prefer_exe).map(|bin| dir.join(bin))
        })
        .map(|p| p.to_string_lossy().into_owned())
        .find(|p| is_file(p))
}

#[cfg(test)]
mod tests {
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

        let win_sdk_root = resolve_adb_path(Some(r"D:\sdk\"), None, None, &|p| {
            p == r"D:\sdk\platform-tools\adb.exe"
        });
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

        let idmap_only = "Tasks: 210 total, 1 running\n 512 system 20 0 10G 4.0M 3.2M S 0.0 0.2 0:00.12 idmap2d\n";
        assert_eq!(parse_top_output(idmap_only), ("N/A".to_string(), "N/A".to_string()));

        let real_id = "%Cpu(s): 5.0 us, 2.0 sy, 0.0 ni, 93.0 id, 0.0 wa\n";
        assert_eq!(
            parse_top_output(real_id),
            ("7.0%".to_string(), "7.0% (Cores unknown)".to_string())
        );

        let sdk_adb =
            std::path::Path::new("/sdk/platform-tools").join("adb").to_string_lossy().into_owned();
        let sdk_adb_exe = std::path::Path::new("/sdk/platform-tools")
            .join("adb.exe")
            .to_string_lossy()
            .into_owned();
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
            parse_with_last(
                "User 200 + Nice 0 + Sys 200 + Idle -2 + IOW 0 + IRQ 0 + SIRQ 0 = 398\n"
            ),
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
        assert_eq!(
            parse_df_data_section("Filesystem Size Used Avail Use% Mounted on\n"),
            (None, None)
        );
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
        assert!(
            BATCH_STATS_SCRIPT.ends_with("(top -b -n 2 -d 0.2 || top -n 2 -d 1 || top -n 2 -d 1)")
        );
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
}
