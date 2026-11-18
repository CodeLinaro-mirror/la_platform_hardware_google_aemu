use regex::Regex;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use super::*;

pub fn now_wall_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// Index of `starttime` (in clock ticks since boot) after `(comm)` in `/proc/<pid>/stat`.
const PROC_STAT_STARTTIME_INDEX: usize = 19;

/// Standard Linux `SC_CLK_TCK` user clock ticks per second.
const USER_HZ: f64 = 100.0;

/// Sub-second process start time from /proc/uptime and /proc/<pid>/stat.
///
/// The boot time comes from a clock read right after /proc/uptime. A timestamp
/// taken earlier in the tick would move the start back by however long the
/// tick has been running.
pub fn get_proc_precise_start_time(pid: u32, fallback_create_time: f64) -> f64 {
    let uptime = std::fs::read_to_string("/proc/uptime").ok();
    let now_wall = now_wall_secs();
    let stat_txt = std::fs::read_to_string(format!("/proc/{}/stat", pid)).ok();
    parse_proc_start_time(uptime.as_deref(), stat_txt.as_deref(), now_wall)
        .filter(|&t| fallback_create_time <= 0.0 || (t - fallback_create_time).abs() <= 2.0)
        .unwrap_or(fallback_create_time)
}

fn parse_proc_start_time(
    uptime_txt: Option<&str>,
    stat_txt: Option<&str>,
    now_wall: f64,
) -> Option<f64> {
    let uptime_secs = uptime_txt?.split_whitespace().next()?.parse::<f64>().ok()?;
    let stat = stat_txt?;
    let rparen = stat.rfind(')')?;
    let ticks = stat[rparen + 1..]
        .split_whitespace()
        .nth(PROC_STAT_STARTTIME_INDEX)?
        .parse::<u64>()
        .ok()?;
    let boot_time = now_wall - uptime_secs;
    Some(boot_time + (ticks as f64 / USER_HZ))
}

#[cfg(unix)]
pub fn decode_dev_key(st_dev: u64, st_ino: u64) -> String {
    let maj = ((st_dev >> 8) & 0xfff) | ((st_dev >> 32) & !0xfff);
    let min = (st_dev & 0xff) | ((st_dev >> 12) & !0xff);
    format!("{:02x}:{:02x}:{}", maj, min, st_ino)
}

#[cfg(unix)]
pub fn get_file_lock_key(path: &Path) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    let meta = std::fs::symlink_metadata(path).ok()?;
    if !meta.file_type().is_file() {
        return None;
    }
    Some(decode_dev_key(meta.dev(), meta.ino()))
}

#[cfg(not(unix))]
pub fn get_file_lock_key(_path: &Path) -> Option<String> {
    None
}

pub fn parse_proc_locks(proc_locks_text: &str) -> HashMap<String, u32> {
    static RE_DEVID: OnceLock<Regex> = OnceLock::new();
    let re_devid =
        RE_DEVID.get_or_init(|| Regex::new(r"^([0-9a-fA-F]+):([0-9a-fA-F]+):(\d+)$").unwrap());

    let mut holders = HashMap::new();
    for raw_line in proc_locks_text.lines() {
        let parts: Vec<&str> = raw_line.split_whitespace().collect();
        if parts.len() < 6 || parts.contains(&"->") {
            continue;
        }
        if !parts.contains(&"FLOCK") || !parts.contains(&"WRITE") {
            continue;
        }
        for (idx, token) in parts.iter().enumerate().skip(1) {
            let Some(caps) = re_devid.captures(token) else {
                continue;
            };
            if let (Some(pid), Ok(maj), Ok(min), Ok(ino)) = (
                parts[idx - 1].parse::<u32>().ok().filter(|&p| p > 0),
                u64::from_str_radix(&caps[1], 16),
                u64::from_str_radix(&caps[2], 16),
                caps[3].parse::<u64>(),
            ) {
                holders.insert(format!("{:02x}:{:02x}:{}", maj, min, ino), pid);
            }
            break;
        }
    }
    holders
}

/// Parses `KEY=VALUE` environment entries, dropping token-like keys and empty
/// keys (Windows keeps per-drive working directories as `=C:=C:\...`).
pub fn parse_env_entries<S: AsRef<str>>(
    entries: impl IntoIterator<Item = S>,
) -> HashMap<String, String> {
    entries
        .into_iter()
        .filter_map(|entry| {
            let (k, v) = entry.as_ref().split_once('=')?;
            (!k.is_empty() && !k.to_lowercase().contains("token"))
                .then(|| (k.to_string(), v.to_string()))
        })
        .collect()
}

pub fn read_proc_environ(pid: u32) -> HashMap<String, String> {
    std::fs::read(format!("/proc/{}/environ", pid))
        .map(|bytes| parse_env_entries(bytes.split(|&b| b == 0).map(String::from_utf8_lossy)))
        .unwrap_or_default()
}

pub fn parse_netsim_ini_str(content: &str) -> HashMap<String, String> {
    let mut data = HashMap::new();
    for raw_line in content.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            let k_trim = k.trim();
            let v_trim = v.trim();
            if k_trim.to_lowercase().contains("token") {
                continue;
            }
            data.insert(k_trim.to_string(), v_trim.to_string());
        }
    }
    data
}

/// Maximum size accepted for a netsim or emulator discovery INI file.
pub const MAX_INI_BYTES: usize = 64 * 1024;

/// Reads a netsim INI without following a symlink or blocking on a FIFO: its keys (see
/// `parse_netsim_ini_str()`) and the metadata of the descriptor they were read from, so
/// that owner and mtime describe the bytes that were parsed. A file that cannot be opened
/// yields neither; an oversized or non-UTF-8 one yields no keys.
pub fn read_netsim_ini(path: &Path) -> (HashMap<String, String>, Option<std::fs::Metadata>) {
    let Some((file, meta)) = open_owned_regular_file(path, None) else {
        return (HashMap::new(), None);
    };
    let data = read_open_file_bounded(file, &meta, MAX_INI_BYTES)
        .map(|text| parse_netsim_ini_str(&text))
        .unwrap_or_default();
    (data, Some(meta))
}

/// Default netsim instance (netsim `os_utils.rs` `DEFAULT_INSTANCE`).
pub const DEFAULT_NETSIM_INSTANCE: u32 = 1;

/// Instance number encoded in a netsim INI file name: `netsim.ini` is the
/// default instance and `netsim_<N>.ini` is instance N (netsim
/// `get_ini_filename()`). Other names, including the `.lock` files, are not
/// netsim INIs.
pub fn extract_ini_instance_num(ini_path: &Path) -> Option<u32> {
    let fname = ini_path.file_name()?.to_str()?;
    if fname == "netsim.ini" {
        return Some(DEFAULT_NETSIM_INSTANCE);
    }
    let digits = fname.strip_prefix("netsim_")?.strip_suffix(".ini")?;
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    digits.parse().ok()
}

/// Host families whose netsim and emulator discovery paths differ.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostOs {
    Linux,
    MacOs,
    Windows,
}

impl HostOs {
    /// The OS the monitor runs on; other Unixes use the Linux paths.
    pub const fn current() -> Self {
        if cfg!(target_os = "macos") {
            HostOs::MacOs
        } else if cfg!(target_os = "windows") {
            HostOs::Windows
        } else {
            HostOs::Linux
        }
    }
}

/// Appends each non-empty path that is not already present, keeping order.
pub(super) fn extend_unique(dirs: &mut Vec<PathBuf>, more: impl IntoIterator<Item = PathBuf>) {
    for p in more {
        if !p.as_os_str().is_empty() && !dirs.contains(&p) {
            dirs.push(p);
        }
    }
}

fn discovery_dirs_with(var: impl Fn(&str) -> Option<PathBuf>, os: HostOs) -> Vec<PathBuf> {
    let (root_env, subdir) = match os {
        HostOs::Linux => ("XDG_RUNTIME_DIR", ""),
        HostOs::MacOs => ("HOME", "Library/Caches/TemporaryItems"),
        HostOs::Windows => ("LOCALAPPDATA", "Temp"),
    };
    let under = |base: PathBuf| if subdir.is_empty() { base } else { base.join(subdir) };
    let mut dirs = Vec::new();
    extend_unique(
        &mut dirs,
        [
            var("ANDROID_EMULATOR_DISCOVERY_DIR"),
            var("TMPDIR"),
            var(root_env).map(under),
            Some(under(PathBuf::from("/tmp"))),
            var("ANDROID_TMP"),
        ]
        .into_iter()
        .flatten(),
    );
    dirs
}

/// Directories that may hold netsim's `netsim[_<N>].ini` and the emulator's
/// `avd/running/pid_<pid>.ini` for a process with environment `environ`.
///
/// netsim's `get_discovery_directory()` (`common/src/util/os_utils.rs`) uses
/// `TMPDIR`, else the OS root variable plus subdir, else `/tmp` plus subdir.
/// The monitor cannot tell which branch a daemon took, so every candidate is
/// returned. Set-but-empty variables count as unset, whereas netsim would use
/// an empty `TMPDIR` as-is.
pub fn get_netsim_discovery_dirs(environ: &HashMap<String, String>, os: HostOs) -> Vec<PathBuf> {
    discovery_dirs_with(|k| environ.get(k).filter(|s| !s.is_empty()).map(PathBuf::from), os)
}

/// The monitor's own environment (entries that are valid UTF-8).
pub fn current_host_env() -> HashMap<String, String> {
    std::env::vars_os()
        .filter_map(|(k, v)| Some((k.into_string().ok()?, v.into_string().ok()?)))
        .collect()
}

/// `get_netsim_discovery_dirs()` for the monitor's own environment without
/// allocating a full environment `HashMap`. On Linux this adds `/run/user/<uid>`,
/// which the emulator uses for a process of user `uid` when `XDG_RUNTIME_DIR` is unset.
pub fn current_host_discovery_dirs(os: HostOs, uid: Option<u32>) -> Vec<PathBuf> {
    let mut dirs = discovery_dirs_with(
        |k| std::env::var_os(k).filter(|s| !s.is_empty()).map(PathBuf::from),
        os,
    );
    if os == HostOs::Linux {
        extend_unique(&mut dirs, uid.map(|uid| PathBuf::from(format!("/run/user/{uid}"))));
    }
    dirs
}

/// `get_netsim_discovery_dirs()` for a supplied `host_env` map (used in tests).
#[cfg(test)]
pub fn host_discovery_dirs(
    host_env: &HashMap<String, String>,
    os: HostOs,
    uid: Option<u32>,
) -> Vec<PathBuf> {
    let mut dirs = get_netsim_discovery_dirs(host_env, os);
    if os == HostOs::Linux {
        extend_unique(&mut dirs, uid.map(|uid| PathBuf::from(format!("/run/user/{uid}"))));
    }
    dirs
}

fn discover_emulator_pid_ini_with(
    worker_pid: u32,
    environ: &HashMap<String, String>,
    host_dirs: Vec<PathBuf>,
    os: HostOs,
    monitor_uid: Option<u32>,
    expected_uid: Option<u32>,
) -> HashMap<String, String> {
    if worker_pid == 0 {
        return HashMap::new();
    }
    #[cfg(unix)]
    let eff_expected_uid = match expected_uid {
        Some(uid) => Some(uid),
        None => match monitor_uid {
            Some(0) => None,
            Some(uid) => Some(uid),
            None => return HashMap::new(),
        },
    };
    #[cfg(not(unix))]
    let eff_expected_uid: Option<u32> = None;

    let mut dirs = get_netsim_discovery_dirs(environ, os);
    extend_unique(&mut dirs, host_dirs);
    for d in dirs {
        let avd_dir = d.join("avd");
        let running_dir = avd_dir.join("running");
        if !is_trusted_owned_real_dir_with_monitor(&avd_dir, eff_expected_uid, monitor_uid, 0)
            || !is_trusted_owned_real_dir_with_monitor(
                &running_dir,
                eff_expected_uid,
                monitor_uid,
                0,
            )
        {
            continue;
        }
        let ini_path = running_dir.join(format!("pid_{worker_pid}.ini"));
        if let Some(content) =
            read_owned_regular_file_bounded(&ini_path, MAX_INI_BYTES, eff_expected_uid)
        {
            let parsed = parse_netsim_ini_str(&content);
            if !parsed.is_empty() {
                return parsed;
            }
        }
    }
    HashMap::new()
}

fn discover_emulator_pid_ini_for(
    worker_pid: u32,
    environ: &HashMap<String, String>,
    expected_uid: Option<u32>,
    os: HostOs,
    monitor_uid: Option<u32>,
    host_dirs_for: impl FnOnce(Option<u32>) -> Vec<PathBuf>,
) -> HashMap<String, String> {
    let expected_uid = expected_uid.or_else(|| monitor_uid.filter(|&u| u != 0));
    // `/run/user/<uid>` is per user: look under the worker's uid, which under
    // sudo is not the monitor's.
    discover_emulator_pid_ini_with(
        worker_pid,
        environ,
        host_dirs_for(expected_uid.or(monitor_uid)),
        os,
        monitor_uid,
        expected_uid,
    )
}

/// Parsed `avd/running/pid_<worker_pid>.ini` of an emulator process with
/// environment `environ`, or an empty map. The emulator writes it only under
/// `<root>/avd/running` (`EmulatorAdvertisement::GetDiscoveryDirectory()`).
pub fn discover_emulator_pid_ini(
    worker_pid: u32,
    environ: &HashMap<String, String>,
    expected_uid: Option<u32>,
) -> HashMap<String, String> {
    let os = HostOs::current();
    discover_emulator_pid_ini_for(worker_pid, environ, expected_uid, os, owner_uid(), |uid| {
        current_host_discovery_dirs(os, uid)
    })
}

/// The instance number netsim's `get_instance()` takes from one source: a
/// `u16` above 0.
fn parse_instance_num(text: &str) -> Option<u32> {
    text.parse::<u16>().ok().filter(|&n| n > 0).map(u32::from)
}

/// Mirrors netsim's `get_instance()`: `NETSIM_INSTANCE`, else the
/// `-i`/`--instance` flag; a source without a valid number is skipped.
pub fn extract_netsim_instance_num(
    cmdline: &[String],
    environ: &HashMap<String, String>,
) -> Option<u32> {
    if let Some(n) = environ.get("NETSIM_INSTANCE").and_then(|v| parse_instance_num(v)) {
        return Some(n);
    }
    for (idx, tok) in cmdline.iter().enumerate() {
        if tok == "-i" || tok == "--instance" {
            if let Some(n) = cmdline.get(idx + 1).and_then(|s| parse_instance_num(s)) {
                return Some(n);
            }
        } else if let Some(n) = tok.strip_prefix("--instance=").and_then(parse_instance_num) {
            return Some(n);
        }
    }
    None
}

/// Mirrors `netsimd_temp_dir()` in netsim `common/src/system/mod.rs`:
/// `ANDROID_TMP`, else `std::env::temp_dir()`, then `android-$USER` (or
/// `android`) off Windows, then `netsimd`. Set-but-empty variables count as
/// unset. On macOS with `TMPDIR` unset, `temp_dir()` asks
/// `confstr(_CS_DARWIN_USER_TEMP_DIR)`; this falls back to `/tmp` instead.
pub fn resolve_netsim_artifact_dir_for_os(
    environ: &HashMap<String, String>,
    os: HostOs,
) -> PathBuf {
    let var = |k: &str| environ.get(k).filter(|s| !s.is_empty());
    if os == HostOs::Windows {
        // GetTempPath() is already per-user, so netsimd adds no android-$USER.
        let base = ["ANDROID_TMP", "TMP", "TEMP", "USERPROFILE", "SystemRoot"]
            .into_iter()
            .find_map(var)
            .map_or_else(|| PathBuf::from(r"C:\Windows"), PathBuf::from);
        return base.join("netsimd");
    }
    let base = var("ANDROID_TMP")
        .or_else(|| var("TMPDIR"))
        .map_or_else(|| PathBuf::from("/tmp"), PathBuf::from);
    let subdir = var("USER").map_or_else(|| "android".to_string(), |u| format!("android-{}", u));
    base.join(subdir).join("netsimd")
}

/// Resolves a daemon's artifact dir. An unreadable (empty) daemon environment
/// falls back to `host_env()`, which netsimd normally inherits from the same
/// login session.
pub fn resolve_netsim_artifact_dir(
    environ: &HashMap<String, String>,
    host_env: impl FnOnce() -> HashMap<String, String>,
    os: HostOs,
) -> PathBuf {
    if environ.is_empty() {
        return resolve_netsim_artifact_dir_for_os(&host_env(), os);
    }
    resolve_netsim_artifact_dir_for_os(environ, os)
}

pub fn resolve_netsim_stderr_log_path(artifact_dir: &Path, instance_num: Option<u32>) -> PathBuf {
    let prefix = match instance_num {
        Some(n) if n > 1 => format!("{}_", n),
        _ => String::new(),
    };
    artifact_dir.join(format!("netsim_{}stderr.log", prefix))
}

#[cfg(test)]
#[path = "../../tests/netsim/proc_test.rs"]
pub(super) mod tests;
