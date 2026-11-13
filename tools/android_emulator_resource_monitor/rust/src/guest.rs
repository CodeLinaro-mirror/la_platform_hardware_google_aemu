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
#[path = "../tests/guest_test.rs"]
mod tests;
