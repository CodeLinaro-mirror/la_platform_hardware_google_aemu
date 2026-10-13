#![allow(clippy::collapsible_if, clippy::type_complexity)]

use chrono::{TimeZone, Utc};
use regex::Regex;
use serde_json::Value;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureRow {
    pub id: u32,
    pub device_name: String,
    pub chip_kind: String,
    pub raw_chip_kind: String,
    pub state: String,
    pub timestamp: String,
    pub records: u64,
    pub size_bytes: u64,
}

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct ChipAddEvent {
    pub ts: f64,
    pub chip_id: Option<String>,
    pub kind: String,
    pub chip_name: String,
    pub device_id: Option<String>,
    pub device_name: String,
    pub peer_port: Option<u16>,
}

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct ChipRemoveEvent {
    pub ts: f64,
    pub chip_id: String,
    pub kind: String,
    pub device_id: String,
}

#[derive(Debug, Clone, Default)]
pub struct NetsimDaemonProc {
    pub pid: u32,
    pub parent: Option<u32>,
    pub parent_name: Option<String>,
    pub start_time: u64,
    pub exe: Option<PathBuf>,
    pub cmd: Vec<String>,
    pub environ: HashMap<String, String>,
    pub cpu: f32,
    pub ram_mb: f64,
    /// The daemon runs as the monitor's user (see `host::is_same_user`).
    /// Only such daemons may nominate a sibling `netsim` CLI or have their
    /// artifact directory harvested.
    pub same_user: bool,
}

#[derive(Debug, Clone, Default)]
pub struct EmuGroupNetsimInfo {
    pub serial: String,
    pub port: u16,
    pub primary_pid: u32,
    pub primary_start_time: u64,
    pub pids: Vec<u32>,
    pub avd_id: Option<String>,
    pub display_name: Option<String>,
    pub grpc_endpoint: Option<String>,
    pub no_netsim: bool,
}

#[derive(Debug, Clone, Default)]
pub struct NetsimDashboardSnapshot {
    pub daemon_rows: Vec<(String, String)>,
    pub daemon_status_row: Option<String>,
    pub emulator_rows: HashMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct StatsCache {
    pub mtime: f64,
    pub num_devices: usize,
    pub resolved: Option<BTreeMap<String, (f64, bool)>>,
}

pub fn normalize_chip_kind(kind_str: &str) -> String {
    let k = kind_str.trim().to_uppercase();
    if k.is_empty() {
        return "?".to_string();
    }
    match k.as_str() {
        "BLUETOOTH_LOW_ENERGY" | "BLUETOOTH_CLASSIC" | "BLUETOOTH" | "BLE" => "BT".to_string(),
        _ => k,
    }
}

pub fn now_wall_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// Sub-second process start time from /proc/uptime and /proc/<pid>/stat.
///
/// The boot time comes from a clock read right after /proc/uptime. A timestamp
/// taken earlier in the tick would move the start back by however long the
/// tick has been running.
pub fn get_proc_precise_start_time(pid: u32, fallback_create_time: f64) -> f64 {
    let uptime = std::fs::read_to_string("/proc/uptime");
    let now_wall = now_wall_secs();
    if let (Ok(uptime_txt), Ok(stat_txt)) =
        (uptime, std::fs::read_to_string(format!("/proc/{}/stat", pid)))
    {
        if let Some(up_tok) = uptime_txt.split_whitespace().next() {
            if let Ok(uptime_secs) = up_tok.parse::<f64>() {
                if let Some(rparen) = stat_txt.rfind(')') {
                    let after = &stat_txt[rparen + 1..];
                    let fields: Vec<&str> = after.split_whitespace().collect();
                    if fields.len() > 19 {
                        if let Ok(ticks) = fields[19].parse::<u64>() {
                            let boot_time = now_wall - uptime_secs;
                            return boot_time + (ticks as f64 / 100.0);
                        }
                    }
                }
            }
        }
    }
    fallback_create_time
}

pub fn decode_dev_key(st_dev: u64, st_ino: u64) -> String {
    let maj = ((st_dev >> 8) & 0xfff) | ((st_dev >> 32) & !0xfff);
    let min = (st_dev & 0xff) | ((st_dev >> 12) & !0xff);
    format!("{:02x}:{:02x}:{}", maj, min, st_ino)
}

#[cfg(unix)]
pub fn get_file_lock_key(path: &Path) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    let meta = std::fs::metadata(path).ok()?;
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
        if parts.len() < 6 {
            continue;
        }
        if !parts.contains(&"FLOCK") || !parts.contains(&"WRITE") {
            continue;
        }
        for (idx, token) in parts.iter().enumerate() {
            if idx >= 1 {
                if let Some(caps) = re_devid.captures(token) {
                    if let (Ok(pid), Ok(maj), Ok(min), Ok(ino)) = (
                        parts[idx - 1].parse::<u32>(),
                        u64::from_str_radix(&caps[1], 16),
                        u64::from_str_radix(&caps[2], 16),
                        caps[3].parse::<u64>(),
                    ) {
                        if pid > 0 {
                            holders.insert(format!("{:02x}:{:02x}:{}", maj, min, ino), pid);
                        }
                    }
                    break;
                }
            }
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

pub fn parse_netsim_ini(path: &Path) -> HashMap<String, String> {
    if let Ok(content) = std::fs::read_to_string(path) {
        parse_netsim_ini_str(&content)
    } else {
        HashMap::new()
    }
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
fn extend_unique(dirs: &mut Vec<PathBuf>, more: impl IntoIterator<Item = PathBuf>) {
    for p in more {
        if !p.as_os_str().is_empty() && !dirs.contains(&p) {
            dirs.push(p);
        }
    }
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
    let var = |k: &str| environ.get(k).filter(|s| !s.is_empty()).map(PathBuf::from);
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

/// The monitor's own environment (entries that are valid UTF-8).
pub fn current_host_env() -> HashMap<String, String> {
    std::env::vars_os()
        .filter_map(|(k, v)| Some((k.into_string().ok()?, v.into_string().ok()?)))
        .collect()
}

/// The monitor's uid on Linux (owner of `/proc/self`).
fn current_uid() -> Option<u32> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        std::fs::metadata("/proc/self").ok().map(|m| m.uid())
    }
    #[cfg(not(unix))]
    {
        None
    }
}

/// The monitor's uid for ownership checks: `/proc/self` on Linux, else the
/// owner of the current executable (macOS has no `/proc`).
fn owner_uid() -> Option<u32> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        current_uid().or_else(|| {
            std::env::current_exe().ok().and_then(|p| std::fs::metadata(p).ok()).map(|m| m.uid())
        })
    }
    #[cfg(not(unix))]
    {
        None
    }
}

/// A directory owned by the monitor's user (any directory on Windows).
pub fn is_owned_dir(path: &Path) -> bool {
    let Ok(m) = std::fs::metadata(path) else {
        return false;
    };
    if !m.is_dir() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        match owner_uid() {
            Some(uid) => m.uid() == uid,
            None => false,
        }
    }
    #[cfg(not(unix))]
    {
        true
    }
}

/// A regular file that only the monitor's user or root can replace.
///
/// The file and its directory must be owned by the monitor's user or root
/// and must not be group- or world-writable, so a binary found next to
/// another process's executable cannot be one an unprivileged user planted.
/// Windows has no POSIX ownership; there only the regular-file check applies.
pub fn is_trusted_executable(path: &Path) -> bool {
    let (Ok(m), Some(dir)) = (std::fs::metadata(path), path.parent()) else {
        return false;
    };
    let dir = if dir.as_os_str().is_empty() { Path::new(".") } else { dir };
    let Ok(dm) = std::fs::metadata(dir) else {
        return false;
    };
    if !m.is_file() || !dm.is_dir() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let Some(uid) = owner_uid() else {
            return false;
        };
        [&m, &dm].iter().all(|e| (e.uid() == uid || e.uid() == 0) && e.mode() & 0o022 == 0)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

/// Picks the `netsim` CLI to drive a daemon with.
///
/// A sibling of the daemon's executable is used only when the daemon runs as
/// the monitor's user and the sibling passes `is_trusted_executable`.
/// Otherwise the bare name resolves through the monitor's own PATH.
pub fn resolve_netsim_cli_bin(daemon_exe: Option<&Path>, same_user: bool) -> String {
    let default = if cfg!(windows) { "netsim.exe" } else { "netsim" };
    if !same_user {
        return default.to_string();
    }
    daemon_exe
        .and_then(|e| e.parent())
        .and_then(|dir| {
            ["netsim.exe", "netsim"]
                .iter()
                .map(|name| dir.join(name))
                .find(|cand| is_trusted_executable(cand))
        })
        .map(|cand| cand.to_string_lossy().to_string())
        .unwrap_or_else(|| default.to_string())
}

/// Copies `src` to `dst` without following a symlink at `src`.
///
/// The artifact dir comes from the daemon's environment, so a symlink planted
/// there must not turn the copy into a read of an arbitrary file. The source
/// is checked with `symlink_metadata` and, on unix, the open descriptor is
/// verified to be that same inode. Returns the bytes copied, or `None` when
/// `src` is not a plain file.
pub fn copy_regular_file(src: &Path, dst: &Path) -> Option<u64> {
    let lm = std::fs::symlink_metadata(src).ok()?;
    if !lm.file_type().is_file() {
        return None;
    }
    let mut fsrc = std::fs::File::open(src).ok()?;
    let fm = fsrc.metadata().ok()?;
    if !fm.is_file() {
        return None;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if (fm.dev(), fm.ino()) != (lm.dev(), lm.ino()) {
            return None;
        }
    }
    let mut fdst = std::fs::File::create(dst).ok()?;
    std::io::copy(&mut fsrc, &mut fdst).ok()
}

/// `get_netsim_discovery_dirs()` for the monitor's own environment. On Linux
/// this adds `/run/user/<uid>`, which the emulator uses for `avd/running`
/// when `XDG_RUNTIME_DIR` is unset (Studio `RunningEmulatorCatalog`).
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

/// Parsed `avd/running/pid_<worker_pid>.ini` of an emulator process with
/// environment `environ`, or an empty map. The emulator writes it only under
/// `<root>/avd/running` (`EmulatorAdvertisement::GetDiscoveryDirectory()`).
pub fn discover_emulator_pid_ini(
    worker_pid: u32,
    environ: &HashMap<String, String>,
) -> HashMap<String, String> {
    let os = HostOs::current();
    let mut dirs = get_netsim_discovery_dirs(environ, os);
    extend_unique(&mut dirs, host_discovery_dirs(&current_host_env(), os, current_uid()));
    dirs.iter()
        .map(|d| d.join("avd").join("running").join(format!("pid_{worker_pid}.ini")))
        .filter(|p| p.is_file())
        .map(|p| parse_netsim_ini(&p))
        .find(|parsed| !parsed.is_empty())
        .unwrap_or_default()
}

pub fn extract_netsim_instance_num(
    cmdline: &[String],
    environ: &HashMap<String, String>,
) -> Option<u32> {
    if let Some(val) = environ.get("NETSIM_INSTANCE") {
        if let Ok(n) = val.parse::<u32>() {
            return Some(n);
        }
    }
    for (idx, tok) in cmdline.iter().enumerate() {
        if (tok == "-i" || tok == "--instance") && idx + 1 < cmdline.len() {
            if let Ok(n) = cmdline[idx + 1].parse::<u32>() {
                return Some(n);
            }
        } else if let Some(rest) = tok.strip_prefix("--instance=") {
            if let Ok(n) = rest.parse::<u32>() {
                return Some(n);
            }
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

pub fn parse_netsim_log_timestamp(line: &str, now_utc: f64) -> (Option<char>, Option<f64>, String) {
    static RE_LINE: OnceLock<Regex> = OnceLock::new();
    let re = RE_LINE.get_or_init(|| {
        Regex::new(
            r"^netsim\s+([EWIDT])\s+(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+(\S+)\s+-\s+(.*)$",
        )
        .unwrap()
    });
    let Some(caps) = re.captures(line) else {
        return (None, None, line.trim().to_string());
    };
    let level = caps[1].chars().next();
    let month: u32 = caps[2].parse().unwrap_or(1);
    let day: u32 = caps[3].parse().unwrap_or(1);
    let hh: u32 = caps[4].parse().unwrap_or(0);
    let mm: u32 = caps[5].parse().unwrap_or(0);
    let ss: u32 = caps[6].parse().unwrap_or(0);
    let ms: f64 = caps[7].parse().unwrap_or(0.0);
    let msg = caps[9].to_string();

    let now_dt = Utc.timestamp_opt(now_utc as i64, 0).single().unwrap_or_else(Utc::now);
    let year = chrono::Datelike::year(&now_dt);
    let mut ts = Utc
        .with_ymd_and_hms(year, month, day, hh, mm, ss)
        .single()
        .map(|dt| dt.timestamp() as f64 + ms / 1000.0)
        .unwrap_or(now_utc);
    if ts - now_utc > 86400.0 {
        if let Some(dt_prev) = Utc.with_ymd_and_hms(year - 1, month, day, hh, mm, ss).single() {
            ts = dt_prev.timestamp() as f64 + ms / 1000.0;
        }
    }
    (level, Some(ts), msg)
}

#[derive(Debug, Clone)]
pub struct NetsimLogTailer {
    pub log_path: PathBuf,
    pub offset: u64,
    pub version: Option<String>,
    pub artifact_dir_logged: Option<String>,
    pub ini_path_logged: Option<String>,
    pub grpc_port_logged: Option<u16>,
    pub ready_ts: Option<f64>,
    pub frontend_disabled: bool,
    pub pcap_flag: bool,
    pub client_mode_spawn: bool,
    pub exit_reason_hint: Option<String>,
    pub events: Vec<(f64, String)>,
    pub connections: Vec<(f64, Option<String>, String)>,
    pub created_devices: Vec<(f64, String)>,
    pub chip_adds: Vec<ChipAddEvent>,
    pub chip_removals: Vec<ChipRemoveEvent>,
    pub empty_devices: Vec<(f64, String)>,
    pub pending_guid_port: Option<(f64, u16)>,
    pub wifi_associated_ts: Option<f64>,
    pub last_timestamp: Option<f64>,
    pub truncation_count: u32,
}

pub const MAX_TAILER_EVENTS: usize = 512;

fn push_bounded<T>(items: &mut Vec<T>, item: T) {
    items.push(item);
    if items.len() > MAX_TAILER_EVENTS {
        let excess = items.len() - MAX_TAILER_EVENTS;
        items.drain(0..excess);
    }
}

impl NetsimLogTailer {
    pub fn new(log_path: PathBuf) -> Self {
        Self {
            log_path,
            offset: 0,
            version: None,
            artifact_dir_logged: None,
            ini_path_logged: None,
            grpc_port_logged: None,
            ready_ts: None,
            frontend_disabled: false,
            pcap_flag: false,
            client_mode_spawn: false,
            exit_reason_hint: None,
            events: Vec::new(),
            connections: Vec::new(),
            created_devices: Vec::new(),
            chip_adds: Vec::new(),
            chip_removals: Vec::new(),
            empty_devices: Vec::new(),
            pending_guid_port: None,
            wifi_associated_ts: None,
            last_timestamp: None,
            truncation_count: 0,
        }
    }

    pub fn reset_state(&mut self) {
        self.version = None;
        self.artifact_dir_logged = None;
        self.ini_path_logged = None;
        self.grpc_port_logged = None;
        self.ready_ts = None;
        self.frontend_disabled = false;
        self.pcap_flag = false;
        self.client_mode_spawn = false;
        self.exit_reason_hint = None;
        self.events.clear();
        self.connections.clear();
        self.created_devices.clear();
        self.chip_adds.clear();
        self.chip_removals.clear();
        self.empty_devices.clear();
        self.pending_guid_port = None;
        self.wifi_associated_ts = None;
        self.last_timestamp = None;
    }

    pub fn poll(&mut self, now_utc: f64) -> bool {
        let Ok(meta) = std::fs::metadata(&self.log_path) else {
            return false;
        };
        let st_size = meta.len();
        if st_size < self.offset {
            self.offset = 0;
            self.truncation_count += 1;
            self.reset_state();
            push_bounded(&mut self.events, (now_utc, "LOG_TRUNCATED".to_string()));
        }
        if st_size == self.offset {
            return true;
        }
        let Ok(mut f) = std::fs::File::open(&self.log_path) else {
            return false;
        };
        use std::io::{Read, Seek, SeekFrom};
        if f.seek(SeekFrom::Start(self.offset)).is_err() {
            return false;
        }
        let mut buf = Vec::new();
        if f.read_to_end(&mut buf).is_err() {
            return false;
        }
        self.offset += buf.len() as u64;
        let text = String::from_utf8_lossy(&buf);
        for line in text.lines() {
            self.feed_line(line, now_utc);
        }
        true
    }

    pub fn feed_line(&mut self, raw_line: &str, now_utc: f64) {
        static RE_ART: OnceLock<Regex> = OnceLock::new();
        static RE_VER: OnceLock<Regex> = OnceLock::new();
        static RE_INI: OnceLock<Regex> = OnceLock::new();
        static RE_PORT: OnceLock<Regex> = OnceLock::new();
        static RE_WROTE: OnceLock<Regex> = OnceLock::new();
        static RE_GUID: OnceLock<Regex> = OnceLock::new();
        static RE_CONN_HEAD: OnceLock<Regex> = OnceLock::new();
        static RE_CONN_1024: OnceLock<Regex> = OnceLock::new();
        static RE_CREATE: OnceLock<Regex> = OnceLock::new();
        static RE_ADD_HEAD: OnceLock<Regex> = OnceLock::new();
        static RE_ADD_1024: OnceLock<Regex> = OnceLock::new();
        static RE_REM: OnceLock<Regex> = OnceLock::new();
        static RE_EMPTY: OnceLock<Regex> = OnceLock::new();
        static RE_WIFI: OnceLock<Regex> = OnceLock::new();

        let stripped = raw_line.trim();
        if stripped.is_empty() {
            return;
        }
        let (_level, ts, msg) = parse_netsim_log_timestamp(raw_line, now_utc);
        if let Some(t) = ts {
            self.last_timestamp = Some(t);
        }
        let eff_ts = ts.or(self.last_timestamp).unwrap_or(now_utc);

        if stripped == "no_cli_ui: true," || msg.contains("no_cli_ui: true") {
            if !self.frontend_disabled {
                self.frontend_disabled = true;
                push_bounded(&mut self.events, (eff_ts, "FRONTEND_DISABLED".to_string()));
            }
        }
        if stripped == "pcap: true," || msg.contains("pcap: true") {
            self.pcap_flag = true;
        }

        let re_art =
            RE_ART.get_or_init(|| Regex::new(r#"netsim artifacts path:\s*"([^"]+)""#).unwrap());
        if let Some(caps) = re_art.captures(&msg) {
            self.artifact_dir_logged = Some(caps[1].to_string());
        }

        let re_ver = RE_VER.get_or_init(|| Regex::new(r"Netsim Version:\s*([^,\s]+)").unwrap());
        if let Some(caps) = re_ver.captures(&msg) {
            self.version = Some(caps[1].to_string());
        }

        let re_ini = RE_INI.get_or_init(|| Regex::new(r"INI file path:\s*(\S+)").unwrap());
        if let Some(caps) = re_ini.captures(&msg) {
            self.ini_path_logged = Some(caps[1].to_string());
        }

        let re_port = RE_PORT.get_or_init(|| Regex::new(r"gRPC port:\s*(\d+)").unwrap());
        if let Some(caps) = re_port.captures(&msg) {
            if let Ok(p) = caps[1].parse::<u16>() {
                self.grpc_port_logged = Some(p);
            }
        }

        let re_wrote = RE_WROTE.get_or_init(|| Regex::new(r"Wrote to INI file\s+(\S+)").unwrap());
        if let Some(caps) = re_wrote.captures(&msg) {
            self.ini_path_logged = Some(caps[1].to_string());
            if self.ready_ts.is_none() {
                self.ready_ts = Some(eff_ts);
            }
            push_bounded(&mut self.events, (eff_ts, "READY".to_string()));
        }

        if msg.contains("Lock held by another process") {
            self.client_mode_spawn = true;
            push_bounded(&mut self.events, (eff_ts, "CLIENT_MODE_SPAWN".to_string()));
        }

        if msg.contains("Startup timeout reached") || msg.contains("No devices connected within") {
            self.exit_reason_hint = Some("startup_timeout".to_string());
        } else if msg.contains("Idle timeout reached") {
            self.exit_reason_hint = Some("idle_timeout".to_string());
        } else if msg.contains("Shutting down gracefully") || msg.contains("Received signal") {
            self.exit_reason_hint = Some("sigterm_sigint".to_string());
        } else if msg.contains("NetsimDaemon main loop exited") && self.exit_reason_hint.is_none() {
            self.exit_reason_hint = Some("clean_unknown".to_string());
        }

        let re_guid = RE_GUID.get_or_init(|| {
            Regex::new(r"DeviceActor:\s*AddChipByGuid(?:.*?)\s+for device\s+\S+?:(\d+)\s*$")
                .unwrap()
        });
        if let Some(caps) = re_guid.captures(&msg) {
            if let Ok(p) = caps[1].parse::<u16>() {
                self.pending_guid_port = Some((eff_ts, p));
            }
            return;
        }

        let re_conn_head = RE_CONN_HEAD.get_or_init(|| {
            Regex::new(r"Handling new connection for\s+(\S+)\s+on device\s+(.+?)\s*$").unwrap()
        });
        if let Some(caps) = re_conn_head.captures(&msg) {
            push_bounded(
                &mut self.connections,
                (eff_ts, Some(normalize_chip_kind(&caps[1])), caps[2].trim().to_string()),
            );
            return;
        }

        let re_conn_1024 = RE_CONN_1024.get_or_init(|| {
            Regex::new(r#"Handling new connection for\s+"([^"]*)",\s+device\s+(.+?)\s*$"#).unwrap()
        });
        if let Some(caps) = re_conn_1024.captures(&msg) {
            push_bounded(&mut self.connections, (eff_ts, None, caps[2].trim().to_string()));
            return;
        }

        let re_create = RE_CREATE
            .get_or_init(|| Regex::new(r"DeviceActor:\s*Create device\s+(.+?)\s*$").unwrap());
        if let Some(caps) = re_create.captures(&msg) {
            push_bounded(&mut self.created_devices, (eff_ts, caps[1].trim().to_string()));
            return;
        }

        let re_add_head = RE_ADD_HEAD.get_or_init(|| {
            Regex::new(
                r#"DeviceActor:\s*AddChip\s+(\S+)\s+\(kind\s+([A-Za-z0-9_]+)(?:,\s*name\s*"([^"]*)")?\)\s+to device\s+(\S+)\s+\((.+)\)\s*$"#,
            )
            .unwrap()
        });
        if let Some(caps) = re_add_head.captures(&msg) {
            let peer_port = self
                .pending_guid_port
                .and_then(|(gts, p)| if (eff_ts - gts).abs() <= 2.0 { Some(p) } else { None });
            push_bounded(
                &mut self.chip_adds,
                ChipAddEvent {
                    ts: eff_ts,
                    chip_id: Some(caps[1].to_string()),
                    kind: normalize_chip_kind(&caps[2]),
                    chip_name: caps.get(3).map(|m| m.as_str().to_string()).unwrap_or_default(),
                    device_id: Some(caps[4].to_string()),
                    device_name: caps[5].trim().to_string(),
                    peer_port,
                },
            );
            return;
        }

        let re_add_1024 = RE_ADD_1024.get_or_init(|| {
            Regex::new(
                r"DeviceActor:\s*AddChip\s+(.+?)\s+\(([^,]+),\s*(.+?)\)\s+to device\s+(.+?)\s*$",
            )
            .unwrap()
        });
        if let Some(caps) = re_add_1024.captures(&msg) {
            let peer_port = self
                .pending_guid_port
                .and_then(|(gts, p)| if (eff_ts - gts).abs() <= 2.0 { Some(p) } else { None });
            push_bounded(
                &mut self.chip_adds,
                ChipAddEvent {
                    ts: eff_ts,
                    chip_id: None,
                    kind: "?".to_string(),
                    chip_name: caps[1].trim().to_string(),
                    device_id: None,
                    device_name: caps[4].trim().to_string(),
                    peer_port,
                },
            );
            return;
        }

        let re_rem = RE_REM.get_or_init(|| {
            Regex::new(
                r"DeviceActor:\s*Removed chip\s+(\S+)\s+\(kind\s+([A-Za-z0-9_]+)\)\s+(?:to|from) device\s+(\S+)",
            )
            .unwrap()
        });
        if let Some(caps) = re_rem.captures(&msg) {
            push_bounded(
                &mut self.chip_removals,
                ChipRemoveEvent {
                    ts: eff_ts,
                    chip_id: caps[1].to_string(),
                    kind: normalize_chip_kind(&caps[2]),
                    device_id: caps[3].to_string(),
                },
            );
            return;
        }

        let re_empty = RE_EMPTY.get_or_init(|| {
            Regex::new(r"DeviceActor:\s*Device\s+(\S+)\s+is empty,\s*auto-deleting").unwrap()
        });
        if let Some(caps) = re_empty.captures(&msg) {
            push_bounded(&mut self.empty_devices, (eff_ts, caps[1].trim().to_string()));
            return;
        }

        let re_wifi = RE_WIFI
            .get_or_init(|| Regex::new(r"ApActor:\s*Associated\s+([0-9a-fA-F:]{17})").unwrap());
        if re_wifi.is_match(&msg) && self.wifi_associated_ts.is_none() {
            self.wifi_associated_ts = Some(eff_ts);
        }
    }
}

/// A stats file older than `not_before` (the daemon's start) was left by an
/// earlier daemon in the same artifact dir and is ignored.
pub fn resolve_session_stats_chips(
    stats_json_path: &Path,
    unknown_add_timestamps: &[f64],
    joined_emulator_count: usize,
    not_before: f64,
    cached: &mut Option<StatsCache>,
) -> (usize, Option<BTreeMap<String, (f64, bool)>>) {
    if stats_json_path.to_string_lossy().ends_with(".tmp") {
        return (0, None);
    }
    let Ok(meta) = std::fs::metadata(stats_json_path) else {
        return (0, None);
    };
    let stats_mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    if stats_mtime < not_before {
        return (0, None);
    }

    if let Some(c) = cached {
        if (c.mtime - stats_mtime).abs() < 1e-6 {
            return (c.num_devices, c.resolved.clone());
        }
    }

    let Ok(text) = std::fs::read_to_string(stats_json_path) else {
        if let Some(c) = cached {
            return (c.num_devices, c.resolved.clone());
        }
        return (0, None);
    };
    let Ok(val) = serde_json::from_str::<Value>(&text) else {
        if let Some(c) = cached {
            return (c.num_devices, c.resolved.clone());
        }
        return (0, None);
    };

    let device_stats =
        val.get("device_stats").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let radio_stats =
        val.get("radio_stats").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let num_devices = device_stats.len();

    if num_devices != 1 || joined_emulator_count != 1 {
        *cached = Some(StatsCache { mtime: stats_mtime, num_devices, resolved: None });
        return (num_devices, None);
    }

    let dev_id = device_stats[0].get("device_id").cloned();
    let mut kind_durations: BTreeMap<String, i64> = BTreeMap::new();
    for r in &radio_stats {
        if let Some(ref target_id) = dev_id {
            if let Some(rid) = r.get("device_id") {
                if !rid.is_null() && rid != target_id {
                    continue;
                }
            }
        }
        let Some(raw_kind) = r.get("kind").and_then(|v| v.as_str()) else {
            continue;
        };
        let norm_k = normalize_chip_kind(raw_kind);
        let dur: i64 = match r.get("duration_secs") {
            Some(Value::String(s)) => s.parse::<f64>().unwrap_or(0.0) as i64,
            Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0) as i64,
            _ => 0,
        };
        let entry = kind_durations.entry(norm_k).or_insert(dur);
        if dur > *entry {
            *entry = dur;
        }
    }

    if kind_durations.is_empty() {
        *cached = Some(StatsCache { mtime: stats_mtime, num_devices, resolved: None });
        return (num_devices, None);
    }

    let mut sorted_unk: Vec<f64> = unknown_add_timestamps.to_vec();
    sorted_unk.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mut used = HashSet::new();
    let mut resolved = BTreeMap::new();

    for (k, dur) in &kind_durations {
        let est_k = stats_mtime - (*dur as f64);
        let mut matched_ts = None;
        for (idx, &ev_ts) in sorted_unk.iter().enumerate() {
            if used.contains(&idx) {
                continue;
            }
            if (ev_ts - est_k).abs() <= 2.0 {
                matched_ts = Some(ev_ts);
                used.insert(idx);
                break;
            }
        }
        if let Some(m_ts) = matched_ts {
            resolved.insert(k.clone(), (m_ts, false));
        } else {
            resolved.insert(k.clone(), (est_k, true));
        }
    }

    *cached =
        Some(StatsCache { mtime: stats_mtime, num_devices, resolved: Some(resolved.clone()) });
    (num_devices, Some(resolved))
}

pub fn parse_netsim_capture_list(output: &str) -> Result<Vec<CaptureRow>, String> {
    if output.contains("UNIMPLEMENTED")
        || output.contains("gRPC error")
        || output.contains("Connection refused")
        || output.contains("transport error")
    {
        return Err(output.trim().to_string());
    }
    let mut rows = Vec::new();
    for raw_line in output.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with("---") || line.starts_with("ID") {
            continue;
        }
        if line.contains('|') {
            let cols: Vec<&str> = line.split('|').map(|c| c.trim()).collect();
            if cols.len() >= 7 {
                if let Ok(id) = cols[0].parse::<u32>() {
                    rows.push(CaptureRow {
                        id,
                        device_name: cols[1].to_string(),
                        chip_kind: normalize_chip_kind(cols[2]),
                        raw_chip_kind: cols[2].to_string(),
                        state: cols[3].to_lowercase(),
                        timestamp: cols[4].to_string(),
                        records: cols[5].parse().unwrap_or(0),
                        size_bytes: cols[6].parse().unwrap_or(0),
                    });
                    continue;
                }
            }
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 6 {
            if let (Ok(id), Ok(size_b)) =
                (parts[0].parse::<u32>(), parts[parts.len() - 1].parse::<u64>())
            {
                let records = parts[parts.len() - 2].parse::<u64>().unwrap_or(0);
                let ts_tok = parts[parts.len() - 3].to_string();
                let state_tok = parts[parts.len() - 4].to_lowercase();
                let raw_kind = parts[parts.len() - 5].to_string();
                let dev_name = parts[1..parts.len() - 5].join(" ");
                if (state_tok == "on" || state_tok == "off") && !dev_name.is_empty() {
                    rows.push(CaptureRow {
                        id,
                        device_name: dev_name,
                        chip_kind: normalize_chip_kind(&raw_kind),
                        raw_chip_kind: raw_kind,
                        state: state_tok,
                        timestamp: ts_tok,
                        records,
                        size_bytes: size_b,
                    });
                }
            }
        }
    }
    Ok(rows)
}

pub fn simulate_netsim_capture_filter(rows: &[CaptureRow], patterns: &[String]) -> Vec<CaptureRow> {
    if patterns.is_empty() {
        return rows.to_vec();
    }
    let up_pats: Vec<String> = patterns.iter().map(|p| p.to_uppercase()).collect();
    rows.iter()
        .filter(|r| {
            let hay_id = r.id.to_string().to_uppercase();
            let hay_dev = r.device_name.to_uppercase();
            let hay_kind = r.raw_chip_kind.to_uppercase();
            up_pats
                .iter()
                .all(|p| hay_id.contains(p) || hay_dev.contains(p) || hay_kind.contains(p))
        })
        .cloned()
        .collect()
}

pub fn select_safe_capture_patterns(
    rows: &[CaptureRow],
    target_device_name: &str,
    target_row: Option<&CaptureRow>,
) -> Option<Vec<String>> {
    if rows.is_empty() || target_device_name.is_empty() {
        return None;
    }
    if let Some(tr) = target_row {
        let pats =
            vec![tr.id.to_string(), target_device_name.to_string(), tr.raw_chip_kind.clone()];
        let matched = simulate_netsim_capture_filter(rows, &pats);
        if !matched.is_empty()
            && matched.iter().all(|m| m.device_name == target_device_name && m.id == tr.id)
        {
            return Some(pats);
        }
        return None;
    }
    let pats = vec![target_device_name.to_string()];
    let matched = simulate_netsim_capture_filter(rows, &pats);
    if !matched.is_empty() && matched.iter().all(|m| m.device_name == target_device_name) {
        return Some(pats);
    }
    None
}

pub fn sanitize_netsim_filename(name: &str) -> String {
    name.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect()
}

pub type CliRunnerFn = dyn Fn(&str, u16, &[String]) -> Option<String> + Send + Sync + 'static;

fn default_cli_runner(netsim_bin: &str, grpc_port: u16, args: &[String]) -> Option<String> {
    if args.len() >= 2 && args[0] == "capture" && args[1] == "get" {
        return None;
    }
    let bin = if netsim_bin.is_empty() { "netsim" } else { netsim_bin };
    let port_str = grpc_port.to_string();
    let mut full_args = Vec::with_capacity(3 + args.len());
    full_args.extend(["-v", "-p", port_str.as_str()]);
    full_args.extend(args.iter().map(String::as_str));
    let (stdout, stderr) = run_cli_output_with_timeout(bin, &full_args, Duration::from_secs(5))?;
    if stderr.is_empty() { Some(stdout) } else { Some(format!("{}\n{}", stdout, stderr)) }
}

#[derive(Clone)]
pub struct EnabledChipRecord {
    pub netsim_bin: String,
    pub grpc_port: u16,
    pub device_name: String,
    pub row: CaptureRow,
}

pub struct NetsimPcapManager {
    pub enabled: bool,
    pub pcap_dir: Option<PathBuf>,
    pub max_bytes: u64,
    pub total_copied_bytes: u64,
    pub copied_files: BTreeMap<String, (u64, u64)>,
    pub rescued_zips: HashSet<PathBuf>,
    pub enabled_by_monitor: HashMap<((u32, u64), u32), EnabledChipRecord>,
    pub skipped_notes: Vec<String>,
    pub last_harvest_mono: f64,
    pub cap_reached: bool,
    pub cli_runner: std::sync::Arc<CliRunnerFn>,
}

impl NetsimPcapManager {
    pub fn new(pcap_dir_opt: Option<&str>, max_mb: u64, out_dir: &Path) -> Self {
        let (enabled, pcap_dir) = match pcap_dir_opt {
            None => (false, None),
            Some("") => (true, Some(out_dir.join("netsim_pcaps"))),
            Some(p) => (true, Some(PathBuf::from(p))),
        };
        Self {
            enabled,
            pcap_dir,
            max_bytes: max_mb.max(1) * 1024 * 1024,
            total_copied_bytes: 0,
            copied_files: BTreeMap::new(),
            rescued_zips: HashSet::new(),
            enabled_by_monitor: HashMap::new(),
            skipped_notes: Vec::new(),
            last_harvest_mono: 0.0,
            cap_reached: false,
            cli_runner: std::sync::Arc::new(default_cli_runner),
        }
    }

    pub fn run_cli(&self, netsim_bin: &str, grpc_port: u16, args: &[String]) -> Option<String> {
        (self.cli_runner)(netsim_bin, grpc_port, args)
    }

    pub fn poll_and_manage_daemon(
        &mut self,
        daemon: &mut DaemonRecord,
        joined_emulators: &[EmulatorNetsimRecord],
        now_mono: f64,
        shutting_down: bool,
    ) {
        let interval = if shutting_down { 1.0 } else { 10.0 };
        let d_key = daemon.daemon_key;
        let has_unmanaged_emu = self.enabled
            && joined_emulators.iter().any(|emu| {
                emu.device_name.as_deref().is_some_and(|dn| {
                    !dn.is_empty()
                        && !self
                            .enabled_by_monitor
                            .iter()
                            .any(|((dk, _), info)| *dk == d_key && info.device_name == dn)
                })
            });
        if self.last_harvest_mono > 0.0
            && (now_mono - self.last_harvest_mono) < interval
            && !shutting_down
            && !has_unmanaged_emu
        {
            return;
        }
        self.last_harvest_mono = now_mono;

        let mut rows = Vec::new();
        if let Some(port) = daemon.grpc_port {
            if !daemon.exited {
                let args = vec!["capture".to_string(), "list".to_string()];
                if let Some(out) = self.run_cli(&daemon.netsim_cli_bin, port, &args) {
                    if let Ok(parsed) = parse_netsim_capture_list(&out) {
                        rows = parsed.clone();
                        daemon.capture_rows = parsed;
                    }
                }
            }
        }

        if self.enabled && !rows.is_empty() && !daemon.exited {
            if let Some(port) = daemon.grpc_port {
                let d_key = daemon.daemon_key;
                for emu in joined_emulators {
                    let Some(ref dev_name) = emu.device_name else {
                        continue;
                    };
                    for r in rows.iter().filter(|r| &r.device_name == dev_name) {
                        if r.state == "off" {
                            let Some(pats) = select_safe_capture_patterns(&rows, dev_name, Some(r))
                            else {
                                let note = format!(
                                    "Skipped auto-enable for '{}' (chip {}) due to ambiguous CLI substring match",
                                    dev_name, r.id
                                );
                                if !self.skipped_notes.contains(&note) {
                                    self.skipped_notes.push(note);
                                }
                                continue;
                            };
                            let mut cmd_args =
                                vec!["capture".to_string(), "patch".to_string(), "on".to_string()];
                            cmd_args.extend(pats);
                            if let Some(patch_out) =
                                self.run_cli(&daemon.netsim_cli_bin, port, &cmd_args)
                            {
                                if parse_netsim_capture_list(&patch_out).is_ok() {
                                    self.enabled_by_monitor.insert(
                                        (d_key, r.id),
                                        EnabledChipRecord {
                                            netsim_bin: daemon.netsim_cli_bin.clone(),
                                            grpc_port: port,
                                            device_name: dev_name.clone(),
                                            row: r.clone(),
                                        },
                                    );
                                }
                            }
                        }
                    }
                }
            }
        }

        self.harvest_daemon_files(daemon, joined_emulators);
    }

    pub fn harvest_daemon_files(
        &mut self,
        daemon: &mut DaemonRecord,
        joined_emulators: &[EmulatorNetsimRecord],
    ) {
        let art_dir = &daemon.artifact_dir;
        if !art_dir.is_dir() {
            return;
        }
        let pcaps_dir = art_dir.join("pcaps");
        let mut live_bytes = 0u64;
        let mut live_files = 0usize;
        if let Ok(entries) = std::fs::read_dir(&pcaps_dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.extension().and_then(|s| s.to_str()) == Some("pcap") {
                    if let Ok(m) = entry.metadata() {
                        live_bytes += m.len();
                        live_files += 1;
                    }
                }
            }
        }
        daemon.pcap_live_bytes = live_bytes;
        daemon.pcap_live_files = live_files;

        let Some(ref base_pcap_dir) = self.pcap_dir else {
            return;
        };
        if !self.enabled {
            return;
        }
        // The artifact dir is taken from the daemon's environment. Copy out of
        // it only when the daemon and the directory both belong to the
        // monitor's user, and never through symlinks (`copy_regular_file`).
        if !daemon.same_user || !is_owned_dir(art_dir) {
            return;
        }

        let daemon_subdir =
            base_pcap_dir.join(format!("{}-{}", daemon.pid, daemon.start_wall as u64));

        if let Ok(entries) = std::fs::read_dir(art_dir) {
            for entry in entries.flatten() {
                let fname = entry.file_name().to_string_lossy().to_string();
                if fname.starts_with("netsim_artifacts_") && fname.ends_with(".zip") {
                    let zpath = entry.path();
                    if !self.rescued_zips.contains(&zpath) {
                        if let Ok(m) = std::fs::symlink_metadata(&zpath) {
                            if !m.file_type().is_file() {
                                continue;
                            }
                            if self.total_copied_bytes + m.len() <= self.max_bytes {
                                if std::fs::create_dir_all(&daemon_subdir).is_ok() {
                                    let dst = daemon_subdir.join(&fname);
                                    if let Some(copied) = copy_regular_file(&zpath, &dst) {
                                        self.rescued_zips.insert(zpath);
                                        self.total_copied_bytes += copied;
                                        self.copied_files
                                            .insert(dst.to_string_lossy().to_string(), (copied, 0));
                                    }
                                }
                            } else {
                                self.cap_reached = true;
                            }
                        }
                    }
                }
            }
        }

        if !is_owned_dir(&pcaps_dir) {
            return;
        }

        let mut chip_to_serial = HashMap::new();
        let mut name_to_serial = HashMap::new();
        for emu in joined_emulators {
            let serial = emu.serial.clone();
            if let Some(ref dn) = emu.device_name {
                name_to_serial.insert(sanitize_netsim_filename(dn), serial.clone());
            }
            for cid in &emu.chip_ids {
                chip_to_serial.insert(cid.clone(), serial.clone());
            }
        }
        let default_serial = if joined_emulators.len() == 1 {
            joined_emulators[0].serial.clone()
        } else {
            "shared".to_string()
        };

        static RE_PCAP: OnceLock<Regex> = OnceLock::new();
        let re_pcap = RE_PCAP
            .get_or_init(|| Regex::new(r"^netsim-(\d+)-(.+)-([A-Za-z0-9_]+)\.pcap$").unwrap());

        if let Ok(entries) = std::fs::read_dir(&pcaps_dir) {
            let mut paths: Vec<PathBuf> = entries.flatten().map(|e| e.path()).collect();
            paths.sort();
            for src in paths {
                let Some(fname) = src.file_name().and_then(|s| s.to_str()) else {
                    continue;
                };
                if !fname.ends_with(".pcap") {
                    continue;
                }
                let Ok(m) = std::fs::symlink_metadata(&src) else {
                    continue;
                };
                if !m.file_type().is_file() {
                    continue;
                }
                let mut target_serial = default_serial.clone();
                if let Some(caps) = re_pcap.captures(fname) {
                    let cid_str = &caps[1];
                    let safe_dev = &caps[2];
                    if let Some(s) = chip_to_serial.get(cid_str) {
                        target_serial = s.clone();
                    } else if let Some(s) = name_to_serial.get(safe_dev) {
                        target_serial = s.clone();
                    }
                }
                let dst_dir = daemon_subdir.join(&target_serial);
                let dst = dst_dir.join(fname);
                let dst_key = dst.to_string_lossy().to_string();
                let mtime_secs = m
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                if let Some(&(prev_sz, prev_mt)) = self.copied_files.get(&dst_key) {
                    if prev_sz == m.len() && prev_mt == mtime_secs {
                        continue;
                    }
                }
                let prev_sz = self.copied_files.get(&dst_key).map(|&(s, _)| s).unwrap_or(0);
                let delta = m.len().saturating_sub(prev_sz);
                if self.total_copied_bytes + delta > self.max_bytes {
                    self.cap_reached = true;
                    let note = format!(
                        "Reached --netsim-pcap-max-mb cap ({} MB); skipped {}",
                        self.max_bytes / (1024 * 1024),
                        fname
                    );
                    if !self.skipped_notes.contains(&note) {
                        self.skipped_notes.push(note);
                    }
                    continue;
                }
                if std::fs::create_dir_all(&dst_dir).is_ok()
                    && copy_regular_file(&src, &dst).is_some()
                {
                    self.copied_files.insert(dst_key, (m.len(), mtime_secs));
                    self.total_copied_bytes += delta;
                }
            }
        }
    }

    pub fn cleanup(
        &mut self,
        daemons: &mut BTreeMap<(u32, u64), DaemonRecord>,
        emulator_records: &BTreeMap<String, EmulatorNetsimRecord>,
    ) {
        let enabled_list: Vec<(((u32, u64), u32), EnabledChipRecord)> =
            self.enabled_by_monitor.iter().map(|(k, v)| (*k, v.clone())).collect();
        for ((d_key, _), info) in enabled_list {
            if let Some(d) = daemons.get(&d_key) {
                if d.exited {
                    continue;
                }
            }
            let list_args = vec!["capture".to_string(), "list".to_string()];
            let Some(out) = self.run_cli(&info.netsim_bin, info.grpc_port, &list_args) else {
                continue;
            };
            let Ok(rows) = parse_netsim_capture_list(&out) else {
                continue;
            };
            if let Some(pats) =
                select_safe_capture_patterns(&rows, &info.device_name, Some(&info.row))
            {
                let mut off_args =
                    vec!["capture".to_string(), "patch".to_string(), "off".to_string()];
                off_args.extend(pats);
                let _ = self.run_cli(&info.netsim_bin, info.grpc_port, &off_args);
            }
        }
        let all_emus: Vec<EmulatorNetsimRecord> = emulator_records.values().cloned().collect();
        for d in daemons.values_mut() {
            let joined: Vec<EmulatorNetsimRecord> =
                all_emus.iter().filter(|e| e.daemon_key == Some(d.daemon_key)).cloned().collect();
            self.harvest_daemon_files(d, &joined);
        }
    }
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct DaemonRecord {
    pub daemon_key: (u32, u64),
    pub pid: u32,
    pub create_time: u64,
    pub start_wall: f64,
    pub first_seen_mono: f64,
    pub last_seen_mono: f64,
    pub spawner: String,
    pub cmdline: Vec<String>,
    pub environ: HashMap<String, String>,
    pub instance_num: Option<u32>,
    pub artifact_dir: PathBuf,
    pub log_path: PathBuf,
    pub tailer: Option<NetsimLogTailer>,
    pub netsim_cli_bin: String,
    /// Copied from `NetsimDaemonProc::same_user`.
    pub same_user: bool,
    pub version: Option<String>,
    pub ini_path: Option<PathBuf>,
    pub grpc_port: Option<u16>,
    pub ready_ts: Option<f64>,
    pub netsim_daemon_ready_s: Option<f64>,
    pub frontend_disabled: bool,
    pub pcap_flag: bool,
    pub log_available: bool,
    pub log_unavail_recorded: bool,
    pub client_mode_recorded: bool,
    pub exited: bool,
    pub exit_reason: Option<String>,
    pub orphaned: bool,
    pub zero_devices_since_mono: Option<f64>,
    pub stats_cache: Option<StatsCache>,
    pub capture_rows: Vec<CaptureRow>,
    pub pcap_live_bytes: u64,
    pub pcap_live_files: usize,
    pub active_socket_conns: usize,
    pub joined_emulator_count: usize,
}

#[derive(Debug, Clone, Default)]
pub struct EmulatorNetsimRecord {
    pub storage_key: String,
    pub serial: String,
    pub avd_id: Option<String>,
    pub device_name: Option<String>,
    pub worker_pid: u32,
    pub worker_start_ts: f64,
    pub first_seen_mono: f64,
    pub no_netsim: bool,
    pub state: String,
    pub ambiguous: bool,
    pub daemon_key: Option<(u32, u64)>,
    pub netsim_daemon_ready_s: Option<f64>,
    pub first_transport_ts: Option<f64>,
    pub netsim_connect_s: Option<f64>,
    pub chip_attach_s: BTreeMap<String, (f64, bool)>,
    pub chip_ids: BTreeSet<String>,
    pub unknown_chip_count: usize,
    pub wifi_associated_s: Option<f64>,
    pub disconnect_count: u32,
    pub connected_start_mono: Option<f64>,
    pub connected_total_s: f64,
    pub adb_online_since_mono: Option<f64>,
    /// First tick of the current run of host-scan misses.
    pub missing_since_mono: Option<f64>,
    /// Ticks of that run on which adb did not list the serial either.
    pub missed_ticks: u32,
    /// Earliest daemon log timestamp accepted after a same-AVD worker restart.
    pub min_log_ts: Option<f64>,
}

impl EmulatorNetsimRecord {
    pub fn is_settled(&self) -> bool {
        self.state == "DISCONNECTED" || self.state.starts_with("FAILED")
    }

    /// Carries cumulative connection totals from an earlier run on the same
    /// key into a newly migrated bare-serial record and sets `min_log_ts` so
    /// pre-restart daemon log entries are ignored.
    pub fn merge_prior_run(&mut self, mut prev: EmulatorNetsimRecord, now_mono: f64) {
        if prev.state == "CONNECTED" || prev.state == "CONNECTED_NO_CHIPS" {
            prev.disconnect_count += 1;
            if let Some(st) = prev.connected_start_mono.take() {
                prev.connected_total_s += (now_mono - st).max(0.0);
            }
        }
        self.disconnect_count += prev.disconnect_count;
        self.connected_total_s += prev.connected_total_s;
        self.min_log_ts = Some(self.worker_start_ts);
    }

    /// Resets per-run connection and chip state when a new worker PID replaces
    /// a previous worker on the same session key, preserving cumulative
    /// `disconnect_count` and `connected_total_s`.
    pub fn reset_for_new_worker(
        &mut self,
        worker_pid: u32,
        worker_start_ts: f64,
        _first_seen_mono: f64,
        no_netsim: bool,
        now_mono: f64,
    ) {
        if self.state == "CONNECTED" || self.state == "CONNECTED_NO_CHIPS" {
            self.disconnect_count += 1;
            if let Some(st) = self.connected_start_mono.take() {
                self.connected_total_s += (now_mono - st).max(0.0);
            }
        }
        self.worker_pid = worker_pid;
        self.worker_start_ts = worker_start_ts;
        self.first_seen_mono = now_mono;
        self.no_netsim = no_netsim;
        self.state = "CONNECTING".to_string();
        self.ambiguous = false;
        self.daemon_key = None;
        self.netsim_daemon_ready_s = None;
        self.first_transport_ts = None;
        self.netsim_connect_s = None;
        self.chip_attach_s.clear();
        self.chip_ids.clear();
        self.unknown_chip_count = 0;
        self.wifi_associated_s = None;
        self.connected_start_mono = None;
        self.adb_online_since_mono = None;
        self.missing_since_mono = None;
        self.missed_ticks = 0;
        self.min_log_ts = Some(worker_start_ts);
    }
}

use std::collections::BTreeSet;

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct LoopbackGrpcConn {
    pub serial: String,
    pub pid: u32,
    pub peer_port: u16,
}

/// Upper bound for one `lsof` / `netstat` run on the sampling path.
const SOCKET_CLI_TIMEOUT: Duration = Duration::from_millis(1500);

fn default_socket_cli_runner(program: &str, args: &[&str]) -> Option<String> {
    run_cli_with_timeout(program, args, SOCKET_CLI_TIMEOUT)
}

/// Emulator-group connections to each target port, keyed by port. Off Linux
/// each call runs `lsof` or `netstat` (bounded by `SOCKET_CLI_TIMEOUT`), since
/// sysinfo cannot list sockets.
#[cfg(test)]
pub fn collect_loopback_grpc_sockets(
    target_ports: &HashSet<u16>,
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    match HostOs::current() {
        HostOs::Linux => {
            collect_loopback_grpc_sockets_from_proc(target_ports, emu_groups, daemon_pids)
        }
        os => collect_loopback_grpc_sockets_via_cli(
            os,
            target_ports,
            emu_groups,
            daemon_pids,
            &default_socket_cli_runner,
        )
        .unwrap_or_else(|| empty_port_map(target_ports)),
    }
}

fn empty_port_map(target_ports: &HashSet<u16>) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    target_ports.iter().map(|&p| (p, Vec::new())).collect()
}

/// Groups `(pid, local_port, remote_port)` rows by target remote port,
/// keeping emulator PIDs only and dropping duplicate rows.
fn group_loopback_conns(
    rows: impl IntoIterator<Item = (u32, u16, u16)>,
    target_ports: &HashSet<u16>,
    pid_to_serial: &HashMap<u32, String>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    let mut port_to_conns = empty_port_map(target_ports);
    let mut seen: HashSet<(u16, u32, u16)> = HashSet::new();
    for (pid, local_port, remote_port) in rows {
        if let (Some(serial), Some(conns)) =
            (pid_to_serial.get(&pid), port_to_conns.get_mut(&remote_port))
        {
            if seen.insert((remote_port, pid, local_port)) {
                conns.push(LoopbackGrpcConn { serial: serial.clone(), pid, peer_port: local_port });
            }
        }
    }
    port_to_conns
}

/// `(pid, local_port, remote_port)` of ESTABLISHED (`st == 01`) sockets in
/// `/proc/net/tcp{,6}` content whose inode belongs to a known PID.
fn proc_net_tcp_rows(content: &str, inode_to_pid: &HashMap<u64, u32>) -> Vec<(u32, u16, u16)> {
    let port = |addr: &str| u16::from_str_radix(addr.split(':').nth(1)?, 16).ok();
    content
        .lines()
        .skip(1)
        .filter_map(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 10 || parts[3] != "01" {
                return None;
            }
            let pid = *inode_to_pid.get(&parts[9].parse::<u64>().ok()?)?;
            Some((pid, port(parts[1])?, port(parts[2])?))
        })
        .collect()
}

fn collect_loopback_grpc_sockets_from_proc(
    target_ports: &HashSet<u16>,
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    let pid_to_serial = emulator_pid_to_serial(emu_groups, daemon_pids);
    if target_ports.is_empty() || pid_to_serial.is_empty() {
        return empty_port_map(target_ports);
    }
    let mut inode_to_pid: HashMap<u64, u32> = HashMap::new();
    for &pid in pid_to_serial.keys() {
        let Ok(entries) = std::fs::read_dir(format!("/proc/{}/fd", pid)) else {
            continue;
        };
        for entry in entries.flatten() {
            if let Some(ino) = std::fs::read_link(entry.path()).ok().and_then(|link| {
                link.to_str()
                    .and_then(|s| s.strip_prefix("socket:[")?.strip_suffix(']'))
                    .and_then(|s| s.parse::<u64>().ok())
            }) {
                inode_to_pid.insert(ino, pid);
            }
        }
    }
    if inode_to_pid.is_empty() {
        return empty_port_map(target_ports);
    }
    let rows = ["/proc/net/tcp", "/proc/net/tcp6"]
        .into_iter()
        .filter_map(|f| std::fs::read_to_string(f).ok())
        .flat_map(|content| proc_net_tcp_rows(&content, &inode_to_pid));
    group_loopback_conns(rows, target_ports, &pid_to_serial)
}

/// Returns the port of a `host:port` / `[host]:port` endpoint if its host is
/// loopback.
fn loopback_endpoint_port(ep: &str) -> Option<u16> {
    let (host, port) = ep.rsplit_once(':')?;
    let host = host.trim_matches(|c| c == '[' || c == ']').to_ascii_lowercase();
    let loopback = matches!(host.as_str(), "::1" | "::ffff:127.0.0.1" | "localhost")
        || host.starts_with("127.");
    if loopback { port.parse().ok() } else { None }
}

/// `(pid, local_port, remote_port)` of loopback connections in `lsof -F pn`
/// output (`p<pid>` / `n<src>-><dst>` lines). lsof itself applies the
/// ESTABLISHED filter (`-sTCP:ESTABLISHED`).
fn lsof_loopback_rows(lsof_out: &str) -> Vec<(u32, u16, u16)> {
    let mut rows = Vec::new();
    let mut current_pid: Option<u32> = None;
    for line in lsof_out.lines().map(str::trim) {
        if let Some(rest) = line.strip_prefix('p') {
            current_pid = rest.parse::<u32>().ok();
        } else if let Some(endpoint) = line.strip_prefix('n') {
            if let (Some(pid), Some((src, dst))) = (current_pid, endpoint.split_once("->")) {
                if let (Some(src_port), Some(dst_port)) =
                    (loopback_endpoint_port(src), loopback_endpoint_port(dst))
                {
                    rows.push((pid, src_port, dst_port));
                }
            }
        }
    }
    rows
}

/// `(pid, local_port, remote_port)` of ESTABLISHED loopback TCP rows in
/// Windows `netstat -ano` output (`TCP <local> <foreign> <state> <pid>`).
/// netstat localizes the State column, so non-English Windows yields no rows.
fn netstat_loopback_rows(netstat_out: &str) -> Vec<(u32, u16, u16)> {
    netstat_out
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            let [proto, local, foreign, state, pid, ..] = parts[..] else {
                return None;
            };
            if !proto.eq_ignore_ascii_case("TCP") || !state.eq_ignore_ascii_case("ESTABLISHED") {
                return None;
            }
            Some((
                pid.parse().ok()?,
                loopback_endpoint_port(local)?,
                loopback_endpoint_port(foreign)?,
            ))
        })
        .collect()
}

pub fn parse_lsof_established_loopback(
    lsof_out: &str,
    target_ports: &HashSet<u16>,
    pid_to_serial: &HashMap<u32, String>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    group_loopback_conns(lsof_loopback_rows(lsof_out), target_ports, &pid_to_serial)
}

pub fn parse_netstat_established_loopback(
    netstat_out: &str,
    target_ports: &HashSet<u16>,
    pid_to_serial: &HashMap<u32, String>,
) -> HashMap<u16, Vec<LoopbackGrpcConn>> {
    group_loopback_conns(netstat_loopback_rows(netstat_out), target_ports, pid_to_serial)
}

fn emulator_pid_to_serial(
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
) -> HashMap<u32, String> {
    let mut pid_to_serial = HashMap::new();
    for (serial, g) in emu_groups {
        for &pid in &g.pids {
            if !daemon_pids.contains(&pid) {
                pid_to_serial.insert(pid, serial.clone());
            }
        }
    }
    pid_to_serial
}

/// Socket discovery for hosts without `/proc`: `lsof` on macOS, `netstat` on
/// Windows. `run(program, args)` returns the tool's stdout, or `None` when the
/// tool failed to start or timed out.
fn collect_loopback_grpc_sockets_via_cli(
    os: HostOs,
    target_ports: &HashSet<u16>,
    emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    daemon_pids: &HashSet<u32>,
    run: &dyn Fn(&str, &[&str]) -> Option<String>,
) -> Option<HashMap<u16, Vec<LoopbackGrpcConn>>> {
    let pid_to_serial = emulator_pid_to_serial(emu_groups, daemon_pids);
    if target_ports.is_empty() || pid_to_serial.is_empty() {
        return Some(empty_port_map(target_ports));
    }
    match os {
        // No `-p TCP`: it lists IPv4 only, and emulators reach netsimd over [::1].
        HostOs::Windows => run("netstat", &["-ano"])
            .map(|out| parse_netstat_established_loopback(&out, target_ports, &pid_to_serial)),
        HostOs::MacOs => {
            let mut pids: Vec<u32> = pid_to_serial.keys().copied().collect();
            pids.sort_unstable();
            let pids = pids.iter().map(u32::to_string).collect::<Vec<_>>().join(",");
            run("lsof", &["-n", "-P", "-F", "pn", "-iTCP", "-sTCP:ESTABLISHED", "-a", "-p", &pids])
                .map(|out| parse_lsof_established_loopback(&out, target_ports, &pid_to_serial))
        }
        // Linux reads /proc instead (collect_loopback_grpc_sockets_from_proc).
        HostOs::Linux => None,
    }
}

/// Runs `program` and returns `(stdout, stderr)` (exit status ignored), or
/// `None` if it cannot start or does not finish within `timeout`, in which case
/// it is killed.
fn run_cli_output_with_timeout(
    program: &str,
    args: &[&str],
    timeout: Duration,
) -> Option<(String, String)> {
    use std::io::Read;
    let mut child = std::process::Command::new(program)
        .args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .ok()?;
    let mut stdout = child.stdout.take()?;
    let mut stderr = child.stderr.take()?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let err_handle = std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = stderr.read_to_end(&mut buf);
            buf
        });
        let mut out_buf = Vec::new();
        let _ = stdout.read_to_end(&mut out_buf);
        let err_buf = err_handle.join().unwrap_or_default();
        let _ = tx.send((out_buf, err_buf));
    });
    let out = rx.recv_timeout(timeout).ok();
    // Reaps a finished child, or stops one that timed out; the reader threads
    // then see EOF and exit.
    let _ = child.kill();
    let _ = child.wait();
    out.map(|(out_buf, err_buf)| {
        (
            String::from_utf8_lossy(&out_buf).into_owned(),
            String::from_utf8_lossy(&err_buf).into_owned(),
        )
    })
}

/// Runs `program` and returns its stdout (exit status ignored), or None if it
/// cannot start or does not finish within `timeout`, in which case it is
/// killed.
fn run_cli_with_timeout(program: &str, args: &[&str], timeout: Duration) -> Option<String> {
    run_cli_output_with_timeout(program, args, timeout).map(|(stdout, _)| stdout)
}

/// Clock slack when comparing an INI's mtime with its writer's start time.
const INI_MTIME_SLACK_S: f64 = 2.0;

/// 1s slack for `/proc` stat `starttime` tick rounding vs. millisecond log timestamps.
const LOG_FLOOR_SLACK_S: f64 = 1.0;

/// Ticks an emulator must be missing from both the host scan and adb before
/// it counts as exited, so a one-tick miss is not reported as a disconnect.
const SETTLE_AFTER_MISSED_TICKS: u32 = 2;

/// Whether `a` and `b` name the same file (canonical paths when both resolve).
fn same_file(a: &Path, b: &Path) -> bool {
    a == b
        || matches!(
            (std::fs::canonicalize(a), std::fs::canonicalize(b)),
            (Ok(x), Ok(y)) if x == y
        )
}

/// Whether a process with this PID exists, on any host.
fn pid_exists(pid: u32) -> bool {
    use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System};
    let pid = Pid::from_u32(pid);
    let mut sys = System::new();
    sys.refresh_processes_specifics(
        ProcessesToUpdate::Some(&[pid]),
        true,
        ProcessRefreshKind::nothing(),
    );
    sys.process(pid).is_some()
}

/// A discovered netsim INI plus the evidence used to pair it with a daemon.
struct IniObservation {
    path: PathBuf,
    data: HashMap<String, String>,
    /// Holders of `<ini>.lock`, then of `<ini>.init.lock` (Linux `/proc/locks`).
    holders: Vec<u32>,
    pid: Option<u32>,
    port: Option<u16>,
    instance: Option<u32>,
    mtime: Option<f64>,
}

impl IniObservation {
    fn read(path: PathBuf, lock_holders: &HashMap<String, u32>) -> Self {
        let data = parse_netsim_ini(&path);
        let holders = [".lock", ".init.lock"]
            .into_iter()
            .filter_map(|suffix| {
                let mut lock_path = path.clone().into_os_string();
                lock_path.push(suffix);
                get_file_lock_key(Path::new(&lock_path)).and_then(|k| lock_holders.get(&k).copied())
            })
            .collect();
        let pid = data.get("pid").and_then(|s| s.parse::<u32>().ok()).filter(|&p| p > 0);
        let port = data.get("grpc.port").and_then(|s| s.parse::<u16>().ok());
        let mtime = std::fs::metadata(&path)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64());
        let instance = extract_ini_instance_num(&path);
        Self { path, data, holders, pid, port, instance, mtime }
    }
}

pub struct NetsimTracker {
    pub enable_log: bool,
    pub pcap_mgr: NetsimPcapManager,
    pub daemons: BTreeMap<(u32, u64), DaemonRecord>,
    pub stale_inis: BTreeMap<PathBuf, HashMap<String, String>>,
    pub timeline: Vec<(f64, String)>,
    pub emulator_records: BTreeMap<String, EmulatorNetsimRecord>,
    pub restart_count: u32,
    pub last_active_by_ini: HashMap<PathBuf, (u32, u64)>,
    pub custom_ini_dirs: Vec<PathBuf>,
    pub custom_proc_locks: Option<String>,
    /// Liveness probe for the `pid=` writer of an unmatched INI; tests
    /// replace it.
    pub pid_alive: fn(u32) -> bool,
    pub socket_os: HostOs,
    pub socket_cli_runner: fn(&str, &[&str]) -> Option<String>,
    pub last_socket_conns: HashMap<u16, Vec<LoopbackGrpcConn>>,
}

impl NetsimTracker {
    pub fn new(pcap_dir: Option<&str>, pcap_max_mb: u64, enable_log: bool, out_dir: &Path) -> Self {
        Self {
            enable_log,
            pcap_mgr: NetsimPcapManager::new(pcap_dir, pcap_max_mb, out_dir),
            daemons: BTreeMap::new(),
            stale_inis: BTreeMap::new(),
            timeline: Vec::new(),
            emulator_records: BTreeMap::new(),
            restart_count: 0,
            last_active_by_ini: HashMap::new(),
            custom_ini_dirs: Vec::new(),
            custom_proc_locks: None,
            pid_alive: pid_exists,
            socket_os: HostOs::current(),
            socket_cli_runner: default_socket_cli_runner,
            last_socket_conns: HashMap::new(),
        }
    }

    fn is_settled(rec: &EmulatorNetsimRecord) -> bool {
        rec.is_settled()
    }

    fn collect_sockets(
        &mut self,
        target_ports: &HashSet<u16>,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        daemon_pids: &HashSet<u32>,
        use_cache_on_err: bool,
    ) -> Option<HashMap<u16, Vec<LoopbackGrpcConn>>> {
        if target_ports.is_empty() {
            self.last_socket_conns.clear();
            return Some(HashMap::new());
        }
        let pid_to_serial = emulator_pid_to_serial(emu_groups, daemon_pids);
        if pid_to_serial.is_empty() {
            let empty = empty_port_map(target_ports);
            self.last_socket_conns = empty.clone();
            return Some(empty);
        }
        let raw = match self.socket_os {
            HostOs::Linux => {
                Some(collect_loopback_grpc_sockets_from_proc(target_ports, emu_groups, daemon_pids))
            }
            os => collect_loopback_grpc_sockets_via_cli(
                os,
                target_ports,
                emu_groups,
                daemon_pids,
                &self.socket_cli_runner,
            ),
        };
        if let Some(conns) = raw {
            self.last_socket_conns = conns.clone();
            return Some(conns);
        }
        if !use_cache_on_err {
            return None;
        }
        // Retain cached sockets only for still-live PIDs across transient lsof/netstat
        // timeouts; full eviction requires an explicit empty Some(...) CLI result.
        let mut cached = empty_port_map(target_ports);
        for &p in target_ports {
            if let Some(prev) = self.last_socket_conns.get(&p) {
                let live: Vec<LoopbackGrpcConn> = prev
                    .iter()
                    .filter(|c| pid_to_serial.get(&c.pid) == Some(&c.serial))
                    .cloned()
                    .collect();
                cached.insert(p, live);
            }
        }
        Some(cached)
    }

    fn record_event(&mut self, ts: f64, text: String) {
        if self.timeline.last().map(|(_, s)| s.as_str()) != Some(text.as_str()) {
            push_bounded(&mut self.timeline, (ts, text));
        }
    }

    fn read_proc_locks(&self) -> HashMap<String, u32> {
        if let Some(ref txt) = self.custom_proc_locks {
            return parse_proc_locks(txt);
        }
        if let Ok(txt) = std::fs::read_to_string("/proc/locks") {
            return parse_proc_locks(&txt);
        }
        HashMap::new()
    }

    /// netsim INIs in the monitor's and each daemon's discovery dirs, one path
    /// per file (macOS `/tmp` and `$TMPDIR` are symlinks, so one INI can be
    /// reachable twice). `custom_ini_dirs`, when set, replaces the search.
    fn discover_ini_files(&self, daemon_procs: &[NetsimDaemonProc]) -> Vec<PathBuf> {
        let os = HostOs::current();
        let mut dirs = Vec::new();
        if self.custom_ini_dirs.is_empty() {
            extend_unique(&mut dirs, host_discovery_dirs(&current_host_env(), os, current_uid()));
            for p in daemon_procs {
                extend_unique(&mut dirs, get_netsim_discovery_dirs(&p.environ, os));
            }
        } else {
            extend_unique(&mut dirs, self.custom_ini_dirs.iter().cloned());
        }
        let mut seen = HashSet::new();
        let mut ini_files = Vec::new();
        for d in dirs {
            let Ok(entries) = std::fs::read_dir(&d) else {
                continue;
            };
            let mut paths: Vec<PathBuf> = entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| extract_ini_instance_num(p).is_some())
                .collect();
            paths.sort();
            for p in paths {
                if seen.insert(std::fs::canonicalize(&p).unwrap_or_else(|_| p.clone())) {
                    ini_files.push(p);
                }
            }
        }
        ini_files.sort();
        ini_files
    }

    /// Pairs each INI with at most one live daemon and vice versa, strongest
    /// evidence first:
    /// 1. holders of the INI's lock files (Linux `/proc/locks`); an INI with
    ///    holders pairs only through them. Then `pid=`, if that daemon started
    ///    before the INI was written, which rejects reused PIDs.
    /// 2. The daemon that already owns the INI (earlier tick or its log).
    /// 3. For INIs without holders or `pid=` (older netsim), an unowned daemon
    ///    of the same instance that started before the INI was written.
    ///    Newer daemons choose first and take the oldest such INI, because an
    ///    older daemon's window also covers INIs written after it.
    ///
    /// Mirrors `NetsimTracker._match_inis_to_daemons()` in
    /// `python/emulator_resource_monitor.py`.
    fn match_inis_to_daemons(
        &self,
        inis: &[IniObservation],
        live: &HashSet<(u32, u64)>,
    ) -> Vec<Option<(u32, u64)>> {
        let mut live_keys: Vec<(u32, u64)> = live.iter().copied().collect();
        live_keys.sort_unstable();
        let live_by_pid: HashMap<u32, (u32, u64)> = live_keys.iter().map(|&k| (k.0, k)).collect();
        let written_after_start = |ini: &IniObservation, key: &(u32, u64)| {
            self.daemons.get(key).is_some_and(|d| {
                // An unknown start time (create_time 0) or mtime accepts the INI.
                d.create_time == 0
                    || ini.mtime.is_none_or(|m| m >= d.start_wall - INI_MTIME_SLACK_S)
            })
        };
        let mut matched: Vec<Option<(u32, u64)>> = vec![None; inis.len()];
        let mut claimed: HashSet<(u32, u64)> = HashSet::new();

        for (slot, ini) in matched.iter_mut().zip(inis) {
            *slot = ini
                .holders
                .iter()
                .filter_map(|pid| live_by_pid.get(pid).copied())
                .find(|k| !claimed.contains(k));
            if let Some(k) = *slot {
                claimed.insert(k);
            }
        }
        for (slot, ini) in matched.iter_mut().zip(inis) {
            if slot.is_none() && ini.holders.is_empty() {
                if let Some(k) = ini.pid.and_then(|pid| live_by_pid.get(&pid).copied()) {
                    if !claimed.contains(&k) && written_after_start(ini, &k) {
                        *slot = Some(k);
                        claimed.insert(k);
                    }
                }
            }
        }
        for (slot, ini) in matched.iter_mut().zip(inis) {
            if slot.is_some() || !ini.holders.is_empty() {
                continue;
            }
            *slot = live_keys.iter().copied().find(|k| {
                !claimed.contains(k)
                    && self
                        .daemons
                        .get(k)
                        .and_then(|d| d.ini_path.as_deref())
                        .is_some_and(|p| same_file(p, &ini.path))
            });
            if let Some(k) = *slot {
                claimed.insert(k);
            }
        }

        let start_of = |k: &(u32, u64)| self.daemons.get(k).map_or(0.0, |d| d.start_wall);
        let mut unowned: Vec<(u32, u64)> = live_keys
            .into_iter()
            .filter(|k| {
                !claimed.contains(k) && self.daemons.get(k).is_some_and(|d| d.ini_path.is_none())
            })
            .collect();
        unowned.sort_by(|a, b| start_of(b).total_cmp(&start_of(a)));
        let mtime_or_last = |i: usize| inis[i].mtime.unwrap_or(f64::INFINITY);
        for k in unowned {
            let instance = self
                .daemons
                .get(&k)
                .and_then(|d| d.instance_num)
                .unwrap_or(DEFAULT_NETSIM_INSTANCE);
            let oldest = (0..inis.len())
                .filter(|&i| {
                    matched[i].is_none()
                        && inis[i].holders.is_empty()
                        && inis[i].pid.is_none()
                        && inis[i].instance == Some(instance)
                        && written_after_start(&inis[i], &k)
                })
                .min_by(|&a, &b| mtime_or_last(a).total_cmp(&mtime_or_last(b)));
            if let Some(i) = oldest {
                matched[i] = Some(k);
            }
        }
        matched
    }

    fn classify_spawner(
        ppid: Option<u32>,
        parent_name: Option<&str>,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
    ) -> String {
        let Some(pp) = ppid else {
            return "STANDALONE".to_string();
        };
        if pp <= 1 {
            return "STANDALONE".to_string();
        }
        for g in emu_groups.values() {
            if g.pids.contains(&pp) {
                return "emulator".to_string();
            }
        }
        let comm_name = parent_name
            .map(|s| s.to_string())
            .or_else(|| std::fs::read_to_string(format!("/proc/{}/comm", pp)).ok());
        if let Some(comm) = comm_name {
            let low = comm.trim().to_lowercase();
            if low.contains("qemu") || low.contains("crosvm") {
                return "qemu".to_string();
            }
            if low.contains("emulator") || low.contains("launcherloop") {
                return "emulator".to_string();
            }
        }
        "STANDALONE".to_string()
    }

    pub fn has_any_connecting(&self) -> bool {
        let has_active_daemon = self.daemons.values().any(|d| !d.exited && d.grpc_port.is_some());
        if !has_active_daemon {
            return false;
        }
        self.emulator_records.values().any(|r| r.state == "CONNECTING")
    }

    /// Re-samples emulator sockets between intervals while an emulator is
    /// CONNECTING. Off Linux each call starts `lsof` or `netstat`.
    pub fn poll_sockets_fast(
        &mut self,
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        now_wall: f64,
    ) {
        let active_ports: HashSet<u16> =
            self.daemons.values().filter(|d| !d.exited).filter_map(|d| d.grpc_port).collect();
        if active_ports.is_empty() {
            return;
        }
        let daemon_pids: HashSet<u32> =
            self.daemons.values().filter(|d| !d.exited).map(|d| d.pid).collect();
        let Some(port_to_conns) =
            self.collect_sockets(&active_ports, emu_groups, &daemon_pids, false)
        else {
            return;
        };
        for d in self.daemons.values() {
            if d.exited {
                continue;
            }
            let Some(port) = d.grpc_port else {
                continue;
            };
            if let Some(conns) = port_to_conns.get(&port) {
                for c in conns {
                    for rec in self.emulator_records.values_mut() {
                        if Self::is_settled(rec) || rec.no_netsim || rec.state != "CONNECTING" {
                            continue;
                        }
                        if rec.serial == c.serial && rec.first_transport_ts.is_none() {
                            rec.first_transport_ts = Some(now_wall);
                            rec.netsim_connect_s = Some((now_wall - rec.worker_start_ts).max(0.0));
                        }
                    }
                }
            }
        }
    }

    /// Maps each emulator in this tick's host scan to its session record key,
    /// following the Python monitor's main loop: a bare-serial record moves to
    /// the AVD-qualified key once the AVD name is known, and otherwise a worker
    /// keeps the key it already has. A record under another key matches on
    /// serial and worker pid, so a different AVD on a reused port never takes
    /// it over. When the same AVD restarts on the same port with a new worker
    /// pid, any bare-serial record from before the AVD id was resolved merges
    /// the prior run's cumulative stats into the AVD-qualified key.
    fn claim_session_keys(
        &mut self,
        summary_emulators: &[(String, String, u32, f64)],
        now_mono: f64,
    ) -> Vec<(String, String, u32, f64)> {
        let mut live = Vec::with_capacity(summary_emulators.len());
        for (s_key, serial, pid, first_seen_mono) in summary_emulators {
            let mut key = s_key.clone();
            if self
                .emulator_records
                .get(&key)
                .is_none_or(|r| *pid > 0 && r.worker_pid > 0 && r.worker_pid != *pid)
            {
                let same_worker = self
                    .emulator_records
                    .iter()
                    .find(|(k, r)| *k != &key && r.serial == *serial && r.worker_pid == *pid)
                    .map(|(k, _)| k.clone());
                if let Some(old_key) = same_worker {
                    if old_key == *serial {
                        if let Some(mut bare_rec) = self.emulator_records.remove(&old_key) {
                            if let Some(prev_rec) = self.emulator_records.remove(&key) {
                                bare_rec.merge_prior_run(prev_rec, now_mono);
                            }
                            bare_rec.storage_key = key.clone();
                            self.emulator_records.insert(key.clone(), bare_rec);
                        }
                    } else if !self.emulator_records.contains_key(&key) {
                        key = old_key;
                    }
                }
            }
            live.push((key, serial.clone(), *pid, *first_seen_mono));
        }
        live
    }

    /// Records a tick on which a previously scanned emulator is missing from
    /// the host scan, and returns whether it has exited. While adb reports the
    /// serial live the emulator is held at its last state and any miss run is
    /// cleared. It exits after SETTLE_AFTER_MISSED_TICKS consecutive ticks
    /// missing from both, dated to the first of them (`missing_since_mono`).
    fn record_missed_tick(rec: &mut EmulatorNetsimRecord, adb_live: bool, now_mono: f64) -> bool {
        if adb_live {
            rec.missing_since_mono = None;
            rec.missed_ticks = 0;
            return false;
        }
        rec.missing_since_mono.get_or_insert(now_mono);
        if rec.missed_ticks < SETTLE_AFTER_MISSED_TICKS {
            rec.missed_ticks += 1;
        }
        rec.missed_ticks >= SETTLE_AFTER_MISSED_TICKS
    }

    /// Transitions for an emulator that joined no live daemon this tick, or
    /// whose worker has exited: a connected emulator becomes DISCONNECTED, and
    /// one that never reached CONNECTED fails.
    fn settle_unjoined_emulator(
        rec: &mut EmulatorNetsimRecord,
        prev_exit: Option<&str>,
        worker_gone: bool,
        now_mono: f64,
    ) {
        if Self::is_settled(rec) {
            return;
        }
        if rec.state == "CONNECTED" || rec.state == "CONNECTED_NO_CHIPS" {
            rec.state = "DISCONNECTED".to_string();
            rec.disconnect_count += 1;
            if let Some(st) = rec.connected_start_mono.take() {
                rec.connected_total_s += (now_mono - st).max(0.0);
            }
        } else if prev_exit == Some("startup_timeout") {
            rec.state = "FAILED(launcher_abort)".to_string();
        } else if worker_gone {
            rec.state = "FAILED(worker_exit_no_connect)".to_string();
        }
    }

    pub fn update(
        &mut self,
        daemon_procs: &[NetsimDaemonProc],
        emu_groups: &HashMap<String, EmuGroupNetsimInfo>,
        summary_emulators: &[(String, String, u32, f64)],
        connected_devices: &HashMap<String, String>,
        now_wall: f64,
        now_mono: f64,
    ) {
        let lock_holders = self.read_proc_locks();
        let ini_files = self.discover_ini_files(daemon_procs);
        let mut seen_daemon_keys = HashSet::new();
        let mut art_dir_counts: HashMap<PathBuf, usize> = HashMap::new();

        for p in daemon_procs {
            let d_key = (p.pid, p.start_time);
            seen_daemon_keys.insert(d_key);

            if !self.daemons.contains_key(&d_key) {
                let precise_start = get_proc_precise_start_time(p.pid, p.start_time as f64);
                let inst_num = extract_netsim_instance_num(&p.cmd, &p.environ);
                let art_dir =
                    resolve_netsim_artifact_dir(&p.environ, current_host_env, HostOs::current());
                let log_path = resolve_netsim_stderr_log_path(&art_dir, inst_num);
                let spawner =
                    Self::classify_spawner(p.parent, p.parent_name.as_deref(), emu_groups);
                let netsim_cli_bin = resolve_netsim_cli_bin(p.exe.as_deref(), p.same_user);
                let frontend_off = p.cmd.iter().any(|t| t == "--no-cli-ui" || t == "--no-web-ui");
                let pcap_cli_on = p.cmd.iter().any(|t| t == "--pcap");
                let start_wall = if precise_start > 0.0 { precise_start } else { now_wall };

                self.daemons.insert(
                    d_key,
                    DaemonRecord {
                        daemon_key: d_key,
                        pid: p.pid,
                        create_time: p.start_time,
                        start_wall,
                        first_seen_mono: now_mono,
                        last_seen_mono: now_mono,
                        spawner: spawner.clone(),
                        cmdline: p.cmd.clone(),
                        environ: p.environ.clone(),
                        instance_num: inst_num,
                        artifact_dir: art_dir,
                        log_path: log_path.clone(),
                        tailer: if self.enable_log {
                            Some(NetsimLogTailer::new(log_path))
                        } else {
                            None
                        },
                        netsim_cli_bin,
                        same_user: p.same_user,
                        version: None,
                        ini_path: None,
                        grpc_port: None,
                        ready_ts: None,
                        netsim_daemon_ready_s: None,
                        frontend_disabled: frontend_off,
                        pcap_flag: pcap_cli_on,
                        log_available: false,
                        log_unavail_recorded: false,
                        client_mode_recorded: false,
                        exited: false,
                        exit_reason: None,
                        orphaned: false,
                        zero_devices_since_mono: Some(now_mono),
                        stats_cache: None,
                        capture_rows: Vec::new(),
                        pcap_live_bytes: 0,
                        pcap_live_files: 0,
                        active_socket_conns: 0,
                        joined_emulator_count: 0,
                    },
                );
                self.record_event(
                    start_wall,
                    format!("STARTED(pid={}, spawner={})", p.pid, spawner),
                );
                if frontend_off {
                    self.record_event(start_wall, format!("FRONTEND_DISABLED(pid={})", p.pid));
                }
            }

            let mut new_events = Vec::new();
            if let Some(d) = self.daemons.get_mut(&d_key) {
                d.last_seen_mono = now_mono;
                if self.enable_log {
                    if let Some(ref mut tailer) = d.tailer {
                        let ok = tailer.poll(now_wall);
                        d.log_available = ok;
                        if !ok && !d.log_unavail_recorded {
                            d.log_unavail_recorded = true;
                            new_events.push((now_wall, format!("LOG_UNAVAILABLE(pid={})", d.pid)));
                        }
                        if let Some(ref art_logged) = tailer.artifact_dir_logged {
                            let pb = PathBuf::from(art_logged);
                            if pb.is_dir() {
                                d.artifact_dir = pb;
                            }
                        }
                        if let Some(ref v) = tailer.version {
                            d.version = Some(v.clone());
                        }
                        if let Some(ref ip) = tailer.ini_path_logged {
                            d.ini_path = Some(PathBuf::from(ip));
                        }
                        if let Some(gp) = tailer.grpc_port_logged {
                            d.grpc_port = Some(gp);
                        }
                        if let Some(r_ts) = tailer.ready_ts {
                            if d.ready_ts.is_none() {
                                d.ready_ts = Some(r_ts);
                                let ready_s = (r_ts - d.start_wall).max(0.0);
                                d.netsim_daemon_ready_s = Some(ready_s);
                                new_events.push((
                                    r_ts,
                                    format!(
                                        "READY(pid={}, port={}, ready_s={:.2}s)",
                                        d.pid,
                                        d.grpc_port.unwrap_or(0),
                                        ready_s
                                    ),
                                ));
                            }
                        }
                        if tailer.frontend_disabled && !d.frontend_disabled {
                            d.frontend_disabled = true;
                            new_events
                                .push((now_wall, format!("FRONTEND_DISABLED(pid={})", d.pid)));
                        }
                        if tailer.pcap_flag {
                            d.pcap_flag = true;
                        }
                        if tailer.client_mode_spawn && !d.client_mode_recorded {
                            d.client_mode_recorded = true;
                            new_events
                                .push((now_wall, format!("CLIENT_MODE_SPAWN(pid={})", d.pid)));
                        }
                    }
                }
                *art_dir_counts.entry(d.artifact_dir.clone()).or_insert(0) += 1;
            }
            for (ts, ev) in new_events {
                self.record_event(ts, ev);
            }
        }

        for (art_dir, cnt) in art_dir_counts {
            if cnt > 1 {
                self.record_event(
                    now_wall,
                    format!("DUPLICATE_ARTIFACT_DIR({})", art_dir.display()),
                );
            }
        }

        let seen_pids: HashSet<u32> = seen_daemon_keys.iter().map(|&(pid, _)| pid).collect();
        let inis: Vec<IniObservation> =
            ini_files.into_iter().map(|p| IniObservation::read(p, &lock_holders)).collect();
        let matches = self.match_inis_to_daemons(&inis, &seen_daemon_keys);
        for (ini, matched) in inis.into_iter().zip(matches) {
            if let Some(m_key) = matched {
                if let Some(d) = self.daemons.get_mut(&m_key) {
                    d.ini_path = Some(ini.path.clone());
                    if let Some(p) = ini.port {
                        d.grpc_port = Some(p);
                    }
                }
                if let Some(&prev_key) =
                    self.last_active_by_ini.get(&ini.path).filter(|&&prev_key| prev_key != m_key)
                {
                    self.restart_count += 1;
                    self.record_event(
                        now_wall,
                        format!(
                            "RESTARTED(ini={}, old_pid={}, new_pid={})",
                            ini.path.display(),
                            prev_key.0,
                            m_key.0
                        ),
                    );
                }
                self.stale_inis.remove(&ini.path);
                self.last_active_by_ini.insert(ini.path, m_key);
            } else if ini.holders.is_empty() && !self.stale_inis.contains_key(&ini.path) {
                // A `pid=` naming a tracked daemon that did not match was
                // reused; only an untracked live writer keeps the INI fresh.
                let writer_alive =
                    ini.pid.is_some_and(|pid| !seen_pids.contains(&pid) && (self.pid_alive)(pid));
                if !writer_alive {
                    self.record_event(now_wall, format!("STALE_INI(path={})", ini.path.display()));
                    self.stale_inis.insert(ini.path, ini.data);
                }
            }
        }

        let all_keys: Vec<(u32, u64)> = self.daemons.keys().copied().collect();
        for d_key in all_keys {
            if !seen_daemon_keys.contains(&d_key) {
                let mut exit_ev = None;
                if let Some(d) = self.daemons.get_mut(&d_key) {
                    if !d.exited {
                        d.exited = true;
                        if self.enable_log {
                            if let Some(ref mut t) = d.tailer {
                                t.poll(now_wall);
                            }
                        }
                        let reason = d
                            .tailer
                            .as_ref()
                            .and_then(|t| t.exit_reason_hint.clone())
                            .unwrap_or_else(|| {
                                if d.ini_path.as_ref().map(|p| p.exists()).unwrap_or(false) {
                                    "killed_or_crashed".to_string()
                                } else {
                                    "clean_unknown".to_string()
                                }
                            });
                        d.exit_reason = Some(reason.clone());
                        exit_ev = Some(format!("EXITED(pid={}, reason={})", d.pid, reason));
                        let joined: Vec<EmulatorNetsimRecord> = self
                            .emulator_records
                            .values()
                            .filter(|e| e.daemon_key == Some(d_key))
                            .cloned()
                            .collect();
                        self.pcap_mgr.poll_and_manage_daemon(d, &joined, now_mono, true);
                    }
                }
                if let Some(ev) = exit_ev {
                    self.record_event(now_wall, ev);
                }
            }
        }

        let active_ports: HashSet<u16> =
            self.daemons.values().filter(|d| !d.exited).filter_map(|d| d.grpc_port).collect();
        let daemon_pids: HashSet<u32> =
            self.daemons.values().filter(|d| !d.exited).map(|d| d.pid).collect();
        let port_to_conns =
            self.collect_sockets(&active_ports, emu_groups, &daemon_pids, true).unwrap_or_default();

        for d in self.daemons.values_mut() {
            d.joined_emulator_count = 0;
            d.active_socket_conns =
                d.grpc_port.and_then(|p| port_to_conns.get(&p).map(|v| v.len())).unwrap_or(0);
        }

        let live_emulators = self.claim_session_keys(summary_emulators, now_mono);

        // As in the Python monitor, a session emulator missing from this tick's
        // host scan stays in the session. It keeps its last state while held,
        // then goes through settle_unjoined_emulator as of its first missed
        // tick. One whose serial now belongs to another live emulator was
        // replaced and settles at once. adb can keep an 'offline' entry for an
        // emulator that was killed, so only other adb states hold it.
        let live_keys: HashSet<&str> = live_emulators.iter().map(|(k, ..)| k.as_str()).collect();
        let live_serials: HashSet<&str> =
            live_emulators.iter().map(|(_, s, ..)| s.as_str()).collect();
        let mut held_count = 0;
        for (key, rec) in self.emulator_records.iter_mut() {
            if live_keys.contains(key.as_str()) {
                rec.missing_since_mono = None;
                rec.missed_ticks = 0;
                continue;
            }
            // Settling again could change a settled state, e.g. DISCONNECTED
            // to FAILED(launcher_abort) once the daemon exits.
            if Self::is_settled(rec) {
                continue;
            }
            let adb_live = connected_devices.get(&rec.serial).is_some_and(|s| s != "offline");
            let exited = Self::record_missed_tick(rec, adb_live, now_mono);
            if !exited && !live_serials.contains(rec.serial.as_str()) {
                held_count += 1;
                continue;
            }
            if rec.no_netsim {
                continue;
            }
            rec.adb_online_since_mono = None;
            let prev_exit = rec
                .daemon_key
                .and_then(|dk| self.daemons.get(&dk))
                .and_then(|d| d.exit_reason.as_deref());
            let exit_mono = rec.missing_since_mono.unwrap_or(now_mono);
            let worker_gone = true;
            Self::settle_unjoined_emulator(rec, prev_exit, worker_gone, exit_mono);
        }
        // A held emulator may still be running, so it keeps the fallback below
        // from giving its events to the other one.
        let single_live = live_emulators.len() + held_count == 1;

        let mut display_name_counts: HashMap<String, usize> = HashMap::new();
        for (_, serial, _, _) in &live_emulators {
            if let Some(g) = emu_groups.get(serial) {
                if let Some(dn) = g.display_name.as_ref().or(g.avd_id.as_ref()) {
                    *display_name_counts.entry(dn.clone()).or_insert(0) += 1;
                }
            }
        }

        let active_daemon_keys: Vec<(u32, u64)> =
            self.daemons.values().filter(|d| !d.exited).map(|d| d.daemon_key).collect();

        // With one live emulator, tailer events that match no device are its
        // own unless they name another session emulator, such as one that
        // exited while sharing the daemon.
        let other_session_names: HashSet<String> = match live_emulators.as_slice() {
            [(live_key, ..)] if single_live => self
                .emulator_records
                .iter()
                .filter(|(k, _)| *k != live_key)
                .filter_map(|(_, r)| r.device_name.clone())
                .filter(|n| !n.is_empty())
                .collect(),
            _ => HashSet::new(),
        };

        for (s_key, serial, fallback_pid, first_seen_mono) in &live_emulators {
            let g_info = emu_groups.get(serial);
            let worker_pid = g_info.map(|g| g.primary_pid).unwrap_or(*fallback_pid);
            let worker_ctime = g_info.map(|g| g.primary_start_time).unwrap_or(0);
            let worker_start_ts = if worker_pid > 0 {
                get_proc_precise_start_time(worker_pid, worker_ctime as f64)
            } else {
                now_wall
            };

            let rec = self.emulator_records.entry(s_key.clone()).or_insert_with(|| {
                EmulatorNetsimRecord {
                    storage_key: s_key.clone(),
                    serial: serial.clone(),
                    avd_id: g_info.and_then(|g| g.avd_id.clone()),
                    device_name: g_info
                        .and_then(|g| g.display_name.clone().or_else(|| g.avd_id.clone())),
                    worker_pid,
                    worker_start_ts,
                    first_seen_mono: *first_seen_mono,
                    min_log_ts: None,
                    no_netsim: g_info.map(|g| g.no_netsim).unwrap_or(false),
                    state: "CONNECTING".to_string(),
                    ambiguous: false,
                    daemon_key: None,
                    netsim_daemon_ready_s: None,
                    first_transport_ts: None,
                    netsim_connect_s: None,
                    chip_attach_s: BTreeMap::new(),
                    chip_ids: BTreeSet::new(),
                    unknown_chip_count: 0,
                    wifi_associated_s: None,
                    disconnect_count: 0,
                    connected_start_mono: None,
                    connected_total_s: 0.0,
                    adb_online_since_mono: None,
                    missing_since_mono: None,
                    missed_ticks: 0,
                }
            });

            if worker_pid > 0 && rec.worker_pid != worker_pid {
                if rec.worker_pid > 0 {
                    rec.reset_for_new_worker(
                        worker_pid,
                        worker_start_ts,
                        *first_seen_mono,
                        g_info.map(|g| g.no_netsim).unwrap_or(false),
                        now_mono,
                    );
                } else {
                    rec.worker_pid = worker_pid;
                    rec.worker_start_ts = worker_start_ts;
                    if rec.min_log_ts.is_some() {
                        rec.min_log_ts = Some(worker_start_ts);
                    }
                }
            }
            rec.serial = serial.clone();
            if let Some(g) = g_info {
                if g.avd_id.is_some() {
                    rec.avd_id = g.avd_id.clone();
                }
                // pid_<pid>.ini, the source of the display name, is deleted
                // while the worker shuts down, so never trade a known name for
                // the AVD id fallback.
                if let Some(dn) = &g.display_name {
                    rec.device_name = Some(dn.clone());
                } else if rec.device_name.is_none() {
                    rec.device_name = g.avd_id.clone();
                }
                if g.no_netsim {
                    rec.no_netsim = true;
                }
            }
            if rec.no_netsim {
                rec.state = "DISABLED".to_string();
                continue;
            }

            if connected_devices.get(serial).map(|s| s.as_str()) == Some("device") {
                if rec.adb_online_since_mono.is_none() {
                    rec.adb_online_since_mono = Some(now_mono);
                }
            } else {
                rec.adb_online_since_mono = None;
            }

            let target_port = g_info
                .and_then(|g| g.grpc_endpoint.as_ref())
                .and_then(|ep| ep.rsplit(':').next())
                .and_then(|p| p.parse::<u16>().ok());

            let mut joined_key = None;
            if let Some(tp) = target_port {
                for &dk in &active_daemon_keys {
                    if self.daemons.get(&dk).and_then(|d| d.grpc_port) == Some(tp) {
                        joined_key = Some(dk);
                        break;
                    }
                }
            }
            if joined_key.is_none() {
                for &dk in &active_daemon_keys {
                    if let Some(gp) = self.daemons.get(&dk).and_then(|d| d.grpc_port) {
                        if let Some(conns) = port_to_conns.get(&gp) {
                            if conns.iter().any(|c| &c.serial == serial) {
                                joined_key = Some(dk);
                                break;
                            }
                        }
                    }
                }
            }
            if joined_key.is_none() && active_daemon_keys.len() == 1 {
                joined_key = Some(active_daemon_keys[0]);
            }

            if let Some(dk) = joined_key {
                rec.daemon_key = Some(dk);
                if let Some(d) = self.daemons.get_mut(&dk) {
                    d.joined_emulator_count += 1;
                    if let Some(r_ts) = d.ready_ts {
                        let base_ts = d.start_wall.min(rec.worker_start_ts);
                        rec.netsim_daemon_ready_s = Some((r_ts - base_ts).max(0.0));
                    } else if d.netsim_daemon_ready_s.is_some() {
                        rec.netsim_daemon_ready_s = d.netsim_daemon_ready_s;
                    }

                    let emu_conns: Vec<LoopbackGrpcConn> = d
                        .grpc_port
                        .and_then(|gp| port_to_conns.get(&gp))
                        .map(|v| v.iter().filter(|c| &c.serial == serial).cloned().collect())
                        .unwrap_or_default();
                    let emu_peer_ports: HashSet<u16> =
                        emu_conns.iter().map(|c| c.peer_port).collect();

                    if !emu_conns.is_empty() && rec.first_transport_ts.is_none() {
                        rec.first_transport_ts = Some(now_wall);
                    }

                    let dev_name = rec.device_name.clone().unwrap_or_default();
                    rec.ambiguous = !dev_name.is_empty()
                        && display_name_counts.get(&dev_name).copied().unwrap_or(0) > 1
                        && emu_peer_ports.is_empty();

                    let log_floor = rec.min_log_ts.map(|t| t - LOG_FLOOR_SLACK_S);
                    let mut unknown_adds = Vec::new();
                    if let Some(ref tailer) = d.tailer {
                        for (c_ts, _, c_dev) in &tailer.connections {
                            if log_floor.is_some_and(|fl| *c_ts < fl) {
                                continue;
                            }
                            if c_dev == &dev_name
                                || (dev_name.is_empty()
                                    && single_live
                                    && !other_session_names.contains(c_dev))
                            {
                                if rec.first_transport_ts.is_none_or(|t| *c_ts < t) {
                                    rec.first_transport_ts = Some(*c_ts);
                                }
                            }
                        }
                        for add_ev in &tailer.chip_adds {
                            if log_floor.is_some_and(|fl| add_ev.ts < fl) {
                                continue;
                            }
                            let mut matches_dev = add_ev.device_name == dev_name
                                || add_ev.peer_port.is_some_and(|pp| emu_peer_ports.contains(&pp));
                            if !matches_dev
                                && single_live
                                && !other_session_names.contains(&add_ev.device_name)
                            {
                                matches_dev = true;
                            }
                            if !matches_dev {
                                continue;
                            }
                            if !add_ev.device_name.is_empty() && rec.device_name.is_none() {
                                rec.device_name = Some(add_ev.device_name.clone());
                            }
                            if rec.first_transport_ts.is_none_or(|t| add_ev.ts < t) {
                                rec.first_transport_ts = Some(add_ev.ts);
                            }
                            if let Some(ref cid) = add_ev.chip_id {
                                rec.chip_ids.insert(cid.clone());
                            }
                            if add_ev.kind != "?" {
                                let delta = (add_ev.ts - rec.worker_start_ts).max(0.0);
                                rec.chip_attach_s
                                    .entry(add_ev.kind.clone())
                                    .or_insert((delta, false));
                            } else {
                                unknown_adds.push(add_ev.ts);
                            }
                        }
                    }
                    rec.unknown_chip_count = unknown_adds.len();

                    if let Some(ft) = rec.first_transport_ts {
                        rec.netsim_connect_s = Some((ft - rec.worker_start_ts).max(0.0));
                    }

                    let prev_state = rec.state.clone();
                    let has_chips = !rec.chip_attach_s.is_empty() || rec.unknown_chip_count > 0;

                    if !emu_conns.is_empty() || (has_chips && !d.exited) {
                        if !has_chips
                            && rec.adb_online_since_mono.is_some_and(|t| (now_mono - t) >= 120.0)
                        {
                            rec.state = "CONNECTED_NO_CHIPS".to_string();
                        } else {
                            rec.state = "CONNECTED".to_string();
                        }
                        if rec.connected_start_mono.is_none() {
                            rec.connected_start_mono = Some(now_mono);
                        }
                    } else if prev_state == "CONNECTED" || prev_state == "CONNECTED_NO_CHIPS" {
                        rec.state = "DISCONNECTED".to_string();
                        rec.disconnect_count += 1;
                        if let Some(st) = rec.connected_start_mono.take() {
                            rec.connected_total_s += (now_mono - st).max(0.0);
                        }
                    } else if !Self::is_settled(rec) {
                        if !emu_groups.contains_key(serial) {
                            rec.state = "FAILED(worker_exit_no_connect)".to_string();
                        } else {
                            let elapsed_wait = now_mono - rec.first_seen_mono;
                            if elapsed_wait >= 50.0 {
                                rec.state = "STALLED".to_string();
                            } else {
                                rec.state = "CONNECTING".to_string();
                            }
                        }
                    }
                }
            } else {
                let prev_exit = rec
                    .daemon_key
                    .and_then(|dk| self.daemons.get(&dk))
                    .and_then(|d| d.exit_reason.as_deref());
                let worker_gone = !emu_groups.contains_key(serial);
                Self::settle_unjoined_emulator(rec, prev_exit, worker_gone, now_mono);
            }
        }

        let d_keys: Vec<(u32, u64)> = self.daemons.keys().copied().collect();
        for dk in d_keys {
            let joined: Vec<EmulatorNetsimRecord> = self
                .emulator_records
                .values()
                .filter(|e| e.daemon_key == Some(dk) && !Self::is_settled(e))
                .cloned()
                .collect();
            let mut orphan_ev = None;
            if let Some(d) = self.daemons.get_mut(&dk) {
                if d.exited {
                    continue;
                }
                let has_any_dev = !joined.is_empty()
                    || d.tailer.as_ref().is_some_and(|t| !t.created_devices.is_empty());
                if !has_any_dev {
                    let since = *d.zero_devices_since_mono.get_or_insert(now_mono);
                    if (now_mono - since) >= 30.0 && !d.orphaned {
                        d.orphaned = true;
                        orphan_ev = Some(format!("ORPHANED(pid={})", d.pid));
                    }
                } else {
                    d.zero_devices_since_mono = None;
                    d.orphaned = false;
                }

                if joined.len() == 1 {
                    let s_key = joined[0].storage_key.clone();
                    let w_start = joined[0].worker_start_ts;
                    let log_floor = joined[0].min_log_ts.map(|t| t - LOG_FLOOR_SLACK_S);
                    let wifi_ts = d
                        .tailer
                        .as_ref()
                        .and_then(|t| t.wifi_associated_ts)
                        .filter(|wt| log_floor.is_none_or(|fl| *wt >= fl));
                    let need_stats = joined[0].chip_attach_s.is_empty()
                        || joined[0].chip_attach_s.values().all(|&(_, approx)| approx);
                    let mut stats_resolved = None;
                    if need_stats {
                        let stats_path = d.artifact_dir.join("netsim_session_stats.json");
                        let unk_ts: Vec<f64> = d
                            .tailer
                            .as_ref()
                            .map(|t| {
                                t.chip_adds
                                    .iter()
                                    .filter(|a| {
                                        a.kind == "?" && log_floor.is_none_or(|fl| a.ts >= fl)
                                    })
                                    .map(|a| a.ts)
                                    .collect()
                            })
                            .unwrap_or_default();
                        let stats_min_ts = d.start_wall.max(joined[0].min_log_ts.unwrap_or(0.0));
                        let (_, resolved) = resolve_session_stats_chips(
                            &stats_path,
                            &unk_ts,
                            joined.len(),
                            stats_min_ts,
                            &mut d.stats_cache,
                        );
                        stats_resolved = resolved;
                    }
                    if let Some(rec) = self.emulator_records.get_mut(&s_key) {
                        if let Some(wt) = wifi_ts {
                            rec.wifi_associated_s = Some((wt - w_start).max(0.0));
                        }
                        if let Some(res_map) = stats_resolved {
                            for (k, (ev_ts, is_approx)) in res_map {
                                rec.chip_attach_s
                                    .insert(k, ((ev_ts - w_start).max(0.0), is_approx));
                            }
                        }
                    }
                }

                let joined_updated: Vec<EmulatorNetsimRecord> = self
                    .emulator_records
                    .values()
                    .filter(|e| e.daemon_key == Some(dk) && !Self::is_settled(e))
                    .cloned()
                    .collect();
                self.pcap_mgr.poll_and_manage_daemon(d, &joined_updated, now_mono, false);
            }
            if let Some(ev) = orphan_ev {
                self.record_event(now_wall, ev);
            }
        }
    }

    pub fn format_chip_summary(rec: &EmulatorNetsimRecord) -> String {
        if !rec.chip_attach_s.is_empty() {
            let parts: Vec<String> = rec
                .chip_attach_s
                .iter()
                .map(|(k, &(val_s, is_approx))| {
                    let prefix = if is_approx { "~" } else { "" };
                    format!("{}:{}{:.1}s", k, prefix, val_s)
                })
                .collect();
            return parts.join(",");
        }
        if rec.unknown_chip_count > 0 {
            return format!("chips={}", rec.unknown_chip_count);
        }
        "no-chips".to_string()
    }

    pub fn get_dashboard_snapshot(&self, now_wall: f64) -> NetsimDashboardSnapshot {
        let mut daemon_rows = Vec::new();
        let active_daemons: Vec<&DaemonRecord> =
            self.daemons.values().filter(|d| !d.exited).collect();
        for (idx, d) in active_daemons.iter().enumerate() {
            let uptime_s = (now_wall - d.start_wall).max(0.0) as u64;
            let ver = d.version.as_deref().unwrap_or("?");
            let ready_str = d
                .netsim_daemon_ready_s
                .map(|r| format!("{:.2}s", r))
                .unwrap_or_else(|| "N/A".to_string());
            let ui_str = if d.frontend_disabled { "off" } else { "on" };
            let log_str = if d.log_available { "ok" } else { "unavail" };
            let pcap_mode = if self.pcap_mgr.enabled {
                "auto"
            } else if d.pcap_flag {
                "on"
            } else {
                "off"
            };
            let pcap_str = format!("{} ({:.1}KB)", pcap_mode, d.pcap_live_bytes as f64 / 1024.0);
            let label = if active_daemons.len() == 1 {
                "Netsim Daemon".to_string()
            } else {
                format!("Netsim Daemon #{}", idx + 1)
            };
            let val = format!(
                "pid={} | v={} | up={}s | restarts={} | ready={} | conns={} | ui={} | log={} | pcap={}",
                d.pid,
                ver,
                uptime_s,
                self.restart_count,
                ready_str,
                d.active_socket_conns,
                ui_str,
                log_str,
                pcap_str
            );
            daemon_rows.push((label, val));
        }

        let daemon_status_row = if active_daemons.is_empty() {
            if !self.stale_inis.is_empty() {
                let names: Vec<String> = self
                    .stale_inis
                    .keys()
                    .filter_map(|p| p.file_name().map(|s| s.to_string_lossy().to_string()))
                    .collect();
                Some(format!("STALE_INI ({})", names.join(", ")))
            } else {
                Some("N/A".to_string())
            }
        } else {
            None
        };

        let mut emulator_rows = HashMap::new();
        for rec in self.emulator_records.values() {
            let amb = if rec.ambiguous { " [ambiguous]" } else { "" };
            let conn_s = rec.netsim_connect_s.map(|c| format!(" {:.2}s", c)).unwrap_or_default();
            let chips = Self::format_chip_summary(rec);
            emulator_rows.insert(
                rec.serial.clone(),
                format!(
                    "netsim: {}{} ({}) | disc={}{}",
                    rec.state, conn_s, chips, rec.disconnect_count, amb
                ),
            );
        }

        NetsimDashboardSnapshot { daemon_rows, daemon_status_row, emulator_rows }
    }

    /// This tick's CSV columns: daemon totals and every emulator's state. Covers
    /// emulators adb does not list yet, since netsim can connect first, and ones
    /// that have exited.
    pub fn get_csv_fields(&self) -> BTreeMap<String, String> {
        let active_daemons: Vec<&DaemonRecord> =
            self.daemons.values().filter(|d| !d.exited).collect();
        let mut fields = BTreeMap::new();
        if let Some(d0) = active_daemons.first() {
            let conns: usize = active_daemons.iter().map(|d| d.active_socket_conns).sum();
            let devs: usize = active_daemons.iter().map(|d| d.joined_emulator_count).sum();
            fields.insert("netsim_up".to_string(), "1".to_string());
            fields.insert("netsim_pid".to_string(), d0.pid.to_string());
            fields.insert("netsim_restarts".to_string(), self.restart_count.to_string());
            fields.insert("netsim_conns".to_string(), conns.to_string());
            fields.insert("netsim_devices".to_string(), devs.to_string());
        } else {
            fields.insert("netsim_up".to_string(), "0".to_string());
            fields.insert("netsim_pid".to_string(), String::new());
            fields.insert("netsim_restarts".to_string(), self.restart_count.to_string());
            fields.insert("netsim_conns".to_string(), "0".to_string());
            fields.insert("netsim_devices".to_string(), "0".to_string());
        }
        for (k, rec) in &self.emulator_records {
            fields.insert(format!("netsim_state_{}", k), rec.state.clone());
        }
        fields
    }

    pub fn format_summary_sections(&self, now_mono: f64) -> Vec<String> {
        let mut lines = vec!["Netsim daemon lifecycle:".to_string()];
        if self.timeline.is_empty() && self.daemons.is_empty() {
            lines.push("  No netsimd daemon activity observed.".to_string());
        } else {
            for d in self.daemons.values() {
                let status = if d.exited {
                    format!("EXITED({})", d.exit_reason.as_deref().unwrap_or("unknown"))
                } else if d.orphaned {
                    "ORPHANED".to_string()
                } else {
                    "RUNNING".to_string()
                };
                let ready_s = d
                    .netsim_daemon_ready_s
                    .map(|r| format!("{:.2}s", r))
                    .unwrap_or_else(|| "N/A".to_string());
                lines.push(format!(
                    "  Daemon pid={} spawner={} version={} port={} ready_s={} status={} restarts={}",
                    d.pid,
                    d.spawner,
                    d.version.as_deref().unwrap_or("N/A"),
                    d.grpc_port
                        .map(|p| p.to_string())
                        .unwrap_or_else(|| "N/A".to_string()),
                    ready_s,
                    status,
                    self.restart_count
                ));
            }
            for (ts, ev) in &self.timeline {
                let dt = chrono::Local
                    .timestamp_opt(*ts as i64, 0)
                    .single()
                    .unwrap_or_else(chrono::Local::now);
                lines.push(format!("  [{}] {}", dt.format("%H:%M:%S"), ev));
            }
        }
        lines.push(String::new());

        lines.push("Netsim attach:".to_string());
        if self.emulator_records.is_empty() {
            lines.push("  No emulator netsim sessions recorded.".to_string());
        } else {
            for (key, rec) in &self.emulator_records {
                let ready_s = rec
                    .netsim_daemon_ready_s
                    .map(|r| format!("{:.2}s", r))
                    .unwrap_or_else(|| "N/A".to_string());
                let conn_s = rec
                    .netsim_connect_s
                    .map(|c| format!("{:.2}s", c))
                    .unwrap_or_else(|| "N/A".to_string());
                let chips = Self::format_chip_summary(rec);
                let wifi_s = rec
                    .wifi_associated_s
                    .map(|w| format!("{:.2}s", w))
                    .unwrap_or_else(|| "N/A".to_string());
                let mut tot_conn = rec.connected_total_s;
                if let Some(st) = rec.connected_start_mono {
                    let end = rec.missing_since_mono.unwrap_or(now_mono);
                    tot_conn += (end - st).max(0.0);
                }
                let amb = if rec.ambiguous { " (ambiguous)" } else { "" };
                let dev_label = rec.device_name.as_deref().unwrap_or(&rec.serial);
                lines.push(format!(
                    "  {} ({}){}: state={} ready_s={} connect_s={} chips=[{}] wifi_assoc_s={} disconnects={} connected_time={:.1}s",
                    key,
                    dev_label,
                    amb,
                    rec.state,
                    ready_s,
                    conn_s,
                    chips,
                    wifi_s,
                    rec.disconnect_count,
                    tot_conn
                ));
            }
        }
        lines.push(String::new());

        lines.push("Netsim pcap manifest:".to_string());
        if !self.pcap_mgr.enabled {
            lines.push("  Pcap collection disabled (pass --netsim-pcap).".to_string());
        } else {
            if self.pcap_mgr.copied_files.is_empty() {
                lines.push("  No pcap files copied.".to_string());
            } else {
                for (path, &(sz, _)) in &self.pcap_mgr.copied_files {
                    lines.push(format!("  {} ({} bytes)", path, sz));
                }
            }
            for note in &self.pcap_mgr.skipped_notes {
                lines.push(format!("  Note: {}", note));
            }
        }
        lines.push(String::new());
        lines
    }

    pub fn cleanup(&mut self) {
        self.pcap_mgr.cleanup(&mut self.daemons, &self.emulator_records);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_proc_locks_double_spaces() {
        let sample = "60: FLOCK  ADVISORY  WRITE 1859832 00:2b:99248 0 EOF\n\
                      61: POSIX  ADVISORY  WRITE 999 00:1a:12345 0 EOF\n";
        let holders = parse_proc_locks(sample);
        assert_eq!(holders.get("00:2b:99248"), Some(&1859832));
        assert!(!holders.contains_key("00:1a:12345"));
    }

    #[test]
    fn test_parse_netsim_ini_skips_token() {
        let dir = tempfile::tempdir().unwrap();
        let ini_path = dir.path().join("netsim.ini");
        std::fs::write(
            &ini_path,
            "grpc.port=43929\nhci.port=6402\npid=1859832\ngrpc.token=secret123\n",
        )
        .unwrap();
        let data = parse_netsim_ini(&ini_path);
        assert_eq!(data.get("grpc.port").map(|s| s.as_str()), Some("43929"));
        assert_eq!(data.get("pid").map(|s| s.as_str()), Some("1859832"));
        assert!(!data.contains_key("grpc.token"));
    }

    #[test]
    fn test_log_tailer_1024_and_1025_and_truncation() {
        let dir = tempfile::tempdir().unwrap();
        let log_path = dir.path().join("netsim_stderr.log");
        let lines_1024 = concat!(
            "netsim I 09-25 22:37:05.612 netsimd.rs:446 - netsim artifacts path: \"/tmp/erm_qn/android-user/netsimd\"\n",
            "Args {\n",
            "    no_cli_ui: true,\n",
            "    pcap: false,\n",
            "}\n",
            "netsim I 09-25 22:37:05.612 netsimd.rs:451 - Netsim Version: 1.0.24, OS: linux, Arch: x86_64\n",
            "netsim I 09-25 22:37:05.683 netsimd.rs:744 - Wrote to INI file /tmp/erm_qn/netsim.ini\n",
            "netsim I 09-25 22:37:06.552 netsimd.rs:106 - Handling new connection for \"\", device ERM QN Phone 37\n",
            "netsim I 09-25 22:37:06.552 service.rs:671 - DeviceActor: AddChipByGuid for device ipv6:%5B::1%5D:59604\n",
            "netsim I 09-25 22:37:06.552 service.rs:635 - DeviceActor: Create device ERM QN Phone 37\n",
            "netsim I 09-25 22:37:06.552 service.rs:579 - DeviceActor: AddChip ERM QN Phone 37 (EMULATOR, ERM QN Phone 37) to device ERM QN Phone 37\n",
            "netsim I 09-25 22:37:41.889 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
        );
        std::fs::write(&log_path, lines_1024).unwrap();
        let mut tailer = NetsimLogTailer::new(log_path.clone());
        assert!(tailer.poll(1790375830.0));
        assert_eq!(tailer.version.as_deref(), Some("1.0.24"));
        assert!(tailer.frontend_disabled);
        assert!(tailer.ready_ts.is_some());
        assert_eq!(tailer.chip_adds.len(), 1);
        assert_eq!(tailer.chip_adds[0].kind, "?");
        assert_eq!(tailer.chip_adds[0].peer_port, Some(59604));
        assert!(tailer.wifi_associated_ts.is_some());

        let lines_1025 = concat!(
            "netsim I 09-25 22:38:00.100 netsimd.rs:456 - Netsim Version: 1.0.25, OS: linux, Arch: x86_64\n",
            "netsim I 09-25 22:38:01.000 service.rs:585 - DeviceActor: AddChip 3 (kind WIFI, name \"wlan0\") to device 1 (Pixel 8)\n",
        );
        std::fs::write(&log_path, lines_1025).unwrap();
        assert!(tailer.poll(1790375900.0));
        assert_eq!(tailer.truncation_count, 1);
        assert_eq!(tailer.version.as_deref(), Some("1.0.25"));
        assert_eq!(tailer.chip_adds.len(), 1);
        assert_eq!(tailer.chip_adds[0].chip_id.as_deref(), Some("3"));
        assert_eq!(tailer.chip_adds[0].kind, "WIFI");
        assert_eq!(tailer.chip_adds[0].device_name, "Pixel 8");
    }

    #[test]
    fn test_resolve_session_stats_chips_exact_and_approx() {
        let dir = tempfile::tempdir().unwrap();
        let stats_path = dir.path().join("netsim_session_stats.json");
        let payload = serde_json::json!({
            "duration_secs": "130",
            "device_stats": [{"device_id": 1, "kind": "EMULATOR"}],
            "radio_stats": [
                {"device_id": 1, "kind": "WIFI", "duration_secs": "129"},
                {"device_id": 1, "kind": "UWB", "duration_secs": "129"},
                {"device_id": 1, "kind": "BLUETOOTH_LOW_ENERGY", "duration_secs": "118"},
                {"device_id": 1, "kind": "BLUETOOTH_CLASSIC", "duration_secs": "117"}
            ]
        });
        std::fs::write(&stats_path, payload.to_string()).unwrap();
        let mtime = std::fs::metadata(&stats_path)
            .unwrap()
            .modified()
            .unwrap()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        let unk = vec![mtime - 129.1, mtime - 128.9];
        let mut cache = None;
        let (num_devs, resolved) =
            resolve_session_stats_chips(&stats_path, &unk, 1, mtime - 130.0, &mut cache);
        assert_eq!(num_devs, 1);
        let res = resolved.unwrap();
        assert!(!res["WIFI"].1);
        assert!(!res["UWB"].1);
        assert!(res["BT"].1);

        // The same file, seen by a daemon that started after it was written,
        // was left by an earlier session.
        let mut cache = None;
        let (num_devs, resolved) =
            resolve_session_stats_chips(&stats_path, &unk, 1, mtime + 1.0, &mut cache);
        assert_eq!((num_devs, resolved), (0, None));
        assert!(cache.is_none());
    }

    /// A daemon record for harvest tests: the artifact dir holds one real pcap,
    /// one real zip and symlinked look-alikes pointing at a secret file.
    #[cfg(unix)]
    fn harvest_fixture(tmp: &Path, same_user: bool) -> (DaemonRecord, PathBuf, PathBuf) {
        let art_dir = tmp.join("art");
        let pcaps = art_dir.join("pcaps");
        std::fs::create_dir_all(&pcaps).unwrap();
        let secret = tmp.join("secret.txt");
        std::fs::write(&secret, b"SECRET-DATA").unwrap();
        std::fs::write(pcaps.join("netsim-1-Pixel_8-WIFI.pcap"), [0xd4u8; 20]).unwrap();
        std::fs::write(art_dir.join("netsim_artifacts_1.zip"), b"PK\x03\x04zip").unwrap();
        std::os::unix::fs::symlink(&secret, pcaps.join("netsim-2-Evil-BT.pcap")).unwrap();
        std::os::unix::fs::symlink(&secret, art_dir.join("netsim_artifacts_2.zip")).unwrap();
        let daemon = DaemonRecord {
            daemon_key: (5001, 100),
            pid: 5001,
            create_time: 100,
            start_wall: 100.0,
            first_seen_mono: 1.0,
            last_seen_mono: 1.0,
            spawner: "emulator".to_string(),
            cmdline: Vec::new(),
            environ: HashMap::new(),
            instance_num: None,
            artifact_dir: art_dir,
            log_path: PathBuf::new(),
            tailer: None,
            netsim_cli_bin: "netsim".to_string(),
            same_user,
            version: None,
            ini_path: None,
            grpc_port: None,
            ready_ts: None,
            netsim_daemon_ready_s: None,
            frontend_disabled: false,
            pcap_flag: false,
            log_available: false,
            log_unavail_recorded: false,
            client_mode_recorded: false,
            exited: false,
            exit_reason: None,
            orphaned: false,
            zero_devices_since_mono: None,
            stats_cache: None,
            capture_rows: Vec::new(),
            pcap_live_bytes: 0,
            pcap_live_files: 0,
            active_socket_conns: 0,
            joined_emulator_count: 0,
        };
        (daemon, tmp.join("out"), secret)
    }

    fn copied_names(out: &Path) -> Vec<String> {
        fn walk(dir: &Path, acc: &mut Vec<String>) {
            if let Ok(rd) = std::fs::read_dir(dir) {
                for e in rd.flatten() {
                    let p = e.path();
                    if p.is_dir() {
                        walk(&p, acc);
                    } else {
                        acc.push(p.file_name().unwrap().to_string_lossy().to_string());
                    }
                }
            }
        }
        let mut v = Vec::new();
        walk(out, &mut v);
        v.sort();
        v
    }

    #[test]
    #[cfg(unix)]
    fn test_harvest_never_follows_symlinks_in_artifact_dir() {
        // A symlink planted in the daemon's artifact dir must not turn the
        // copy into a read of an arbitrary file; real files still copy.
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        mgr.harvest_daemon_files(&mut daemon, &[]);
        assert_eq!(
            copied_names(&out),
            vec!["netsim-1-Pixel_8-WIFI.pcap".to_string(), "netsim_artifacts_1.zip".to_string()]
        );
        fn walk_check(dir: &Path) {
            for e in std::fs::read_dir(dir).unwrap().flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk_check(&p);
                } else {
                    let data = std::fs::read(&p).unwrap();
                    assert!(!data.windows(6).any(|w| w == b"SECRET"), "{p:?} leaked secret");
                }
            }
        }
        walk_check(&out);
        // The live counters still describe what is on disk.
        assert_eq!(daemon.pcap_live_files, 2);
    }

    #[test]
    #[cfg(unix)]
    fn test_harvest_skips_daemons_of_other_users() {
        // Another user's daemon can point ANDROID_TMP anywhere; the monitor
        // only reads pcap sizes and never copies from its artifact dir.
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), false);
        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        mgr.harvest_daemon_files(&mut daemon, &[]);
        assert!(copied_names(&out).is_empty());
        assert_eq!(mgr.total_copied_bytes, 0);
        assert_eq!(daemon.pcap_live_files, 2);
    }

    #[test]
    #[cfg(unix)]
    fn test_copy_regular_file_refuses_symlink_and_copies_plain_file() {
        let tmp = tempfile::tempdir().unwrap();
        let plain = tmp.path().join("plain.bin");
        std::fs::write(&plain, b"abc").unwrap();
        let link = tmp.path().join("link.bin");
        std::os::unix::fs::symlink(&plain, &link).unwrap();
        assert_eq!(copy_regular_file(&link, &tmp.path().join("o1")), None);
        assert!(!tmp.path().join("o1").exists());
        assert_eq!(copy_regular_file(tmp.path(), &tmp.path().join("o2")), None);
        assert_eq!(copy_regular_file(&plain, &tmp.path().join("o3")), Some(3));
        assert_eq!(std::fs::read(tmp.path().join("o3")).unwrap(), b"abc");
    }

    #[test]
    #[cfg(unix)]
    fn test_resolve_netsim_cli_bin_only_adopts_trusted_sibling() {
        use std::os::unix::fs::PermissionsExt;
        // The daemon's exe dir comes from another process; only a same-user
        // daemon may nominate a sibling, and only one nobody else can replace.
        let tmp = tempfile::tempdir().unwrap();
        let sdk = tmp.path().join("sdk");
        std::fs::create_dir(&sdk).unwrap();
        std::fs::set_permissions(&sdk, std::fs::Permissions::from_mode(0o755)).unwrap();
        let daemon_exe = sdk.join("netsimd");
        let cli = sdk.join("netsim");
        for p in [&daemon_exe, &cli] {
            std::fs::write(p, b"#!/bin/sh\n").unwrap();
            std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let cli_str = cli.to_string_lossy().to_string();

        assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), cli_str);
        // Foreign daemon: never trust its neighbourhood.
        assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), false), "netsim");
        assert_eq!(resolve_netsim_cli_bin(None, true), "netsim");
        // Group- or world-writable binary.
        std::fs::set_permissions(&cli, std::fs::Permissions::from_mode(0o775)).unwrap();
        assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
        std::fs::set_permissions(&cli, std::fs::Permissions::from_mode(0o755)).unwrap();
        // World-writable directory (anyone could swap the file).
        std::fs::set_permissions(&sdk, std::fs::Permissions::from_mode(0o1777)).unwrap();
        assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
        std::fs::set_permissions(&sdk, std::fs::Permissions::from_mode(0o755)).unwrap();
        // A symlink named netsim is judged by its target.
        std::fs::remove_file(&cli).unwrap();
        let real = sdk.join("netsim-real");
        std::fs::write(&real, b"#!/bin/sh\n").unwrap();
        std::fs::set_permissions(&real, std::fs::Permissions::from_mode(0o755)).unwrap();
        std::os::unix::fs::symlink(&real, &cli).unwrap();
        assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), cli_str);
        // Missing sibling falls back to PATH.
        std::fs::remove_file(&cli).unwrap();
        std::fs::remove_file(&real).unwrap();
        assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
    }

    #[test]
    fn test_is_same_user() {
        use crate::host::is_same_user;
        if cfg!(windows) {
            assert!(is_same_user(None, None));
        } else {
            assert!(is_same_user(Some(1000), Some(1000)));
            assert!(!is_same_user(Some(1001), Some(1000)));
            assert!(!is_same_user(None, Some(1000)));
            assert!(!is_same_user(Some(1000), None));
        }
    }

    #[test]
    fn test_capture_list_filter_simulation_and_pcap_manager() {
        let list_out = "\
ID | Device Name     | Chip Kind            | State | Timestamp | Records | Size (bytes)
---|-----------------|----------------------|-------|-----------|---------|-------------
1  | Pixel 8         | WIFI                 | off   | 22:37:06  | 0       | 0
10 | Pixel 8 Pro     | WIFI                 | off   | 22:37:06  | 0       | 0
11 | ERM QN Phone 37 | BLUETOOTH_LOW_ENERGY | off   | 22:37:17  | 0       | 0
";
        let rows = parse_netsim_capture_list(list_out).unwrap();
        assert_eq!(rows.len(), 3);
        assert!(select_safe_capture_patterns(&rows, "Pixel 8", None).is_none());
        assert!(select_safe_capture_patterns(&rows, "Pixel 8", Some(&rows[0])).is_none());
        let safe = select_safe_capture_patterns(&rows, "ERM QN Phone 37", Some(&rows[2])).unwrap();
        assert_eq!(safe[0], "11");

        let dir = tempfile::tempdir().unwrap();
        let art_dir = dir.path().join("art");
        let pcaps_dir = art_dir.join("pcaps");
        std::fs::create_dir_all(&pcaps_dir).unwrap();
        std::fs::write(
            pcaps_dir.join("netsim-11-ERM_QN_Phone_37-BLUETOOTH_LOW_ENERGY.pcap"),
            vec![b'x'; 128],
        )
        .unwrap();
        std::fs::write(art_dir.join("netsim_artifacts_123.zip"), vec![b'z'; 64]).unwrap();

        let out_dir = dir.path().join("out");
        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out_dir);
        let calls: std::sync::Arc<std::sync::Mutex<Vec<Vec<String>>>> =
            std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let calls_clone = calls.clone();
        let list_str = list_out.to_string();
        mgr.cli_runner = std::sync::Arc::new(move |_bin, _port, args| {
            assert!(
                !(args.len() >= 2 && args[0] == "capture" && args[1] == "get"),
                "capture get is forbidden"
            );
            calls_clone.lock().unwrap().push(args.to_vec());
            Some(list_str.clone())
        });

        let mut daemon = DaemonRecord {
            daemon_key: (5000, 100),
            pid: 5000,
            create_time: 100,
            start_wall: 100.0,
            first_seen_mono: 1.0,
            last_seen_mono: 1.0,
            spawner: "emulator".to_string(),
            cmdline: Vec::new(),
            environ: HashMap::new(),
            instance_num: None,
            artifact_dir: art_dir,
            log_path: PathBuf::new(),
            tailer: None,
            netsim_cli_bin: "netsim".to_string(),
            same_user: true,
            version: Some("1.0.24".to_string()),
            ini_path: None,
            grpc_port: Some(43929),
            ready_ts: Some(100.1),
            netsim_daemon_ready_s: Some(0.1),
            frontend_disabled: false,
            pcap_flag: false,
            log_available: true,
            log_unavail_recorded: false,
            client_mode_recorded: false,
            exited: false,
            exit_reason: None,
            orphaned: false,
            zero_devices_since_mono: None,
            stats_cache: None,
            capture_rows: Vec::new(),
            pcap_live_bytes: 0,
            pcap_live_files: 0,
            active_socket_conns: 1,
            joined_emulator_count: 2,
        };

        let mut cids = BTreeSet::new();
        cids.insert("11".to_string());
        let joined = vec![
            EmulatorNetsimRecord {
                storage_key: "Pixel_8_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                avd_id: Some("Pixel_8".to_string()),
                device_name: Some("Pixel 8".to_string()),
                worker_pid: 1,
                worker_start_ts: 100.0,
                first_seen_mono: 1.0,
                min_log_ts: None,
                no_netsim: false,
                state: "CONNECTED".to_string(),
                ambiguous: false,
                daemon_key: Some((5000, 100)),
                netsim_daemon_ready_s: None,
                first_transport_ts: Some(101.0),
                netsim_connect_s: Some(1.0),
                chip_attach_s: BTreeMap::new(),
                chip_ids: BTreeSet::new(),
                unknown_chip_count: 0,
                wifi_associated_s: None,
                disconnect_count: 0,
                connected_start_mono: Some(1.0),
                connected_total_s: 0.0,
                adb_online_since_mono: None,
                missing_since_mono: None,
                missed_ticks: 0,
            },
            EmulatorNetsimRecord {
                storage_key: "ERM_emulator-5556".to_string(),
                serial: "emulator-5556".to_string(),
                avd_id: Some("erm_qn_phone37".to_string()),
                device_name: Some("ERM QN Phone 37".to_string()),
                worker_pid: 2,
                worker_start_ts: 100.0,
                first_seen_mono: 1.0,
                min_log_ts: None,
                no_netsim: false,
                state: "CONNECTED".to_string(),
                ambiguous: false,
                daemon_key: Some((5000, 100)),
                netsim_daemon_ready_s: None,
                first_transport_ts: Some(101.0),
                netsim_connect_s: Some(1.0),
                chip_attach_s: BTreeMap::new(),
                chip_ids: cids,
                unknown_chip_count: 0,
                wifi_associated_s: None,
                disconnect_count: 0,
                connected_start_mono: Some(1.0),
                connected_total_s: 0.0,
                adb_online_since_mono: None,
                missing_since_mono: None,
                missed_ticks: 0,
            },
        ];

        mgr.poll_and_manage_daemon(&mut daemon, &joined, 20.0, false);
        assert!(mgr.skipped_notes.iter().any(|n| n.contains("Pixel 8")));
        assert!(mgr.enabled_by_monitor.contains_key(&((5000, 100), 11)));
        assert!(!mgr.copied_files.is_empty());

        let mut daemons = BTreeMap::new();
        daemons.insert((5000, 100), daemon);
        let mut emu_recs = BTreeMap::new();
        for e in joined {
            emu_recs.insert(e.storage_key.clone(), e);
        }
        mgr.cleanup(&mut daemons, &emu_recs);
        let recorded_calls = calls.lock().unwrap();
        assert!(
            recorded_calls
                .iter()
                .any(|c| c.len() >= 3 && c[0] == "capture" && c[1] == "patch" && c[2] == "off")
        );
    }

    #[test]
    fn test_netsim_tracker_standalone_and_orphaned_and_exit() {
        let dir = tempfile::tempdir().unwrap();
        let art_dir = dir.path().join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_dir).unwrap();
        let log_path = art_dir.join("netsim_stderr.log");
        std::fs::write(
            &log_path,
            "netsim I 09-25 22:37:05.612 netsimd.rs:451 - Netsim Version: 1.0.24, OS: linux, Arch: x86_64\n\
             netsim I 09-25 22:37:05.683 netsimd.rs:744 - Wrote to INI file /tmp/netsim.ini\n",
        )
        .unwrap();

        let mut env = HashMap::new();
        env.insert("TMPDIR".to_string(), dir.path().to_string_lossy().to_string());
        env.insert("USER".to_string(), "test".to_string());
        let procs = vec![NetsimDaemonProc {
            pid: 9001,
            parent: Some(1),
            parent_name: None,
            start_time: 1790375825,
            exe: Some(PathBuf::from("/sdk/emulator/bin/netsimd")),
            cmd: vec!["/sdk/emulator/bin/netsimd".to_string(), "--no-shutdown".to_string()],
            environ: env,
            cpu: 1.0,
            ram_mb: 30.0,
            same_user: true,
        }];

        let mut tracker = NetsimTracker::new(None, 500, true, dir.path());
        tracker.custom_ini_dirs = vec![dir.path().to_path_buf()];
        tracker.custom_proc_locks = Some(String::new());

        tracker.update(&procs, &HashMap::new(), &[], &HashMap::new(), 1790375826.0, 10.0);
        assert!(
            tracker
                .timeline
                .iter()
                .any(|(_, e)| e.contains("STARTED(pid=9001, spawner=STANDALONE)"))
        );
        assert!(tracker.timeline.iter().any(|(_, e)| e.contains("READY(")));

        tracker.update(&procs, &HashMap::new(), &[], &HashMap::new(), 1790375860.0, 45.0);
        assert!(tracker.timeline.iter().any(|(_, e)| e.contains("ORPHANED(pid=9001)")));

        let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
        use std::io::Write;
        writeln!(f, "netsim I 09-25 22:37:50.000 netsimd.rs:800 - Shutting down gracefully")
            .unwrap();

        tracker.update(&[], &HashMap::new(), &[], &HashMap::new(), 1790375870.0, 55.0);
        assert!(
            tracker
                .timeline
                .iter()
                .any(|(_, e)| e.contains("EXITED(pid=9001, reason=sigterm_sigint)"))
        );
    }

    /// PIDs above Linux's PID_MAX_LIMIT (2^22): no `/proc` lookup or liveness
    /// probe reaches a real process, so `start_wall` equals the create time.
    const DAEMON_A: u32 = 5_000_100;
    const DAEMON_B: u32 = 5_000_200;
    const DEAD_PID: u32 = 5_000_999;
    const START: u64 = 1_790_375_825;

    fn env_of(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    fn paths(items: &[&str]) -> Vec<PathBuf> {
        items.iter().map(PathBuf::from).collect()
    }

    fn write_ini(path: &Path, content: &str, mtime: u64) {
        std::fs::write(path, content).unwrap();
        std::fs::File::options()
            .write(true)
            .open(path)
            .unwrap()
            .set_modified(UNIX_EPOCH + Duration::from_secs(mtime))
            .unwrap();
    }

    fn netsimd(pid: u32, start: u64, tmp: &Path) -> NetsimDaemonProc {
        NetsimDaemonProc {
            pid,
            parent: Some(1),
            start_time: start,
            exe: Some(PathBuf::from("/sdk/emulator/netsimd")),
            cmd: vec!["/sdk/emulator/netsimd".to_string()],
            environ: env_of(&[("TMPDIR", tmp.to_str().unwrap()), ("USER", "test")]),
            same_user: true,
            ..Default::default()
        }
    }

    /// A tracker that scans only `dirs` and sees no `/proc/locks` entries, as
    /// on macOS and Windows.
    fn tracker_for(dirs: &[&Path], out: &Path) -> NetsimTracker {
        let mut tracker = NetsimTracker::new(None, 500, false, out);
        tracker.custom_ini_dirs = dirs.iter().map(|d| d.to_path_buf()).collect();
        tracker.custom_proc_locks = Some(String::new());
        tracker
    }

    fn tick(tracker: &mut NetsimTracker, procs: &[NetsimDaemonProc], now_wall: u64) {
        let now_mono = (now_wall - START) as f64;
        tracker.update(procs, &HashMap::new(), &[], &HashMap::new(), now_wall as f64, now_mono);
    }

    fn count_events(tracker: &NetsimTracker, prefix: &str) -> usize {
        tracker.timeline.iter().filter(|(_, e)| e.starts_with(prefix)).count()
    }

    #[test]
    fn test_get_netsim_discovery_dirs_linux() {
        let env = env_of(&[
            ("ANDROID_EMULATOR_DISCOVERY_DIR", "/disc"),
            ("TMPDIR", "/tmpdir"),
            ("XDG_RUNTIME_DIR", "/run/user/1000"),
            ("ANDROID_TMP", "/android_tmp"),
            ("HOME", "/home/alice"),
            ("LOCALAPPDATA", "/ignored"),
        ]);
        assert_eq!(
            get_netsim_discovery_dirs(&env, HostOs::Linux),
            paths(&["/disc", "/tmpdir", "/run/user/1000", "/tmp", "/android_tmp"])
        );
    }

    #[test]
    fn test_get_netsim_discovery_dirs_macos() {
        // netsim appends Library/Caches/TemporaryItems to $HOME, or to /tmp.
        let env = env_of(&[("TMPDIR", "/var/folders/xy/T/"), ("HOME", "/Users/alice")]);
        assert_eq!(
            get_netsim_discovery_dirs(&env, HostOs::MacOs),
            paths(&[
                "/var/folders/xy/T/",
                "/Users/alice/Library/Caches/TemporaryItems",
                "/tmp/Library/Caches/TemporaryItems",
            ])
        );
    }

    #[test]
    fn test_get_netsim_discovery_dirs_windows() {
        // netsim uses %LOCALAPPDATA%\Temp and never reads TEMP or TMP.
        let local = r"C:\Users\alice\AppData\Local";
        let env = env_of(&[
            ("LOCALAPPDATA", local),
            ("TEMP", r"C:\Temp"),
            ("TMP", r"C:\Tmp"),
            ("ANDROID_TMP", r"D:\android_tmp"),
        ]);
        assert_eq!(
            get_netsim_discovery_dirs(&env, HostOs::Windows),
            vec![
                PathBuf::from(local).join("Temp"),
                PathBuf::from("/tmp").join("Temp"),
                PathBuf::from(r"D:\android_tmp"),
            ]
        );
    }

    #[test]
    fn test_get_netsim_discovery_dirs_skips_empty_and_duplicate_values() {
        let env = env_of(&[("TMPDIR", ""), ("XDG_RUNTIME_DIR", "/tmp"), ("ANDROID_TMP", "/tmp/")]);
        assert_eq!(get_netsim_discovery_dirs(&env, HostOs::Linux), paths(&["/tmp"]));
    }

    #[test]
    fn test_host_discovery_dirs_adds_run_user_on_linux_only() {
        let empty = HashMap::new();
        assert_eq!(
            host_discovery_dirs(&empty, HostOs::Linux, Some(4242)),
            paths(&["/tmp", "/run/user/4242"])
        );
        assert_eq!(host_discovery_dirs(&empty, HostOs::Linux, None), paths(&["/tmp"]));
        assert_eq!(
            host_discovery_dirs(&empty, HostOs::MacOs, Some(501)),
            vec![PathBuf::from("/tmp").join("Library/Caches/TemporaryItems")]
        );
        assert_eq!(
            host_discovery_dirs(&empty, HostOs::Windows, Some(1)),
            vec![PathBuf::from("/tmp").join("Temp")]
        );
        // XDG_RUNTIME_DIR usually is /run/user/<uid> already; it is listed once.
        let xdg = env_of(&[("XDG_RUNTIME_DIR", "/run/user/1000")]);
        assert_eq!(
            host_discovery_dirs(&xdg, HostOs::Linux, Some(1000)),
            paths(&["/run/user/1000", "/tmp"])
        );
    }

    #[test]
    fn test_host_os_current_matches_build_target() {
        let expected = match std::env::consts::OS {
            "macos" => HostOs::MacOs,
            "windows" => HostOs::Windows,
            _ => HostOs::Linux,
        };
        assert_eq!(HostOs::current(), expected);
    }

    #[test]
    fn test_parse_env_entries_drops_tokens_and_empty_keys() {
        // `=C:=C:\work` is a Windows per-drive working directory entry.
        let env =
            parse_env_entries(["PATH=/bin", r"=C:=C:\work", "GH_TOKEN=x", "OPTS=a=b", "BARE"]);
        assert_eq!(env, env_of(&[("PATH", "/bin"), ("OPTS", "a=b")]));
    }

    #[test]
    fn test_current_host_env_reads_process_env() {
        assert_eq!(current_host_env().get("PATH"), std::env::var("PATH").ok().as_ref());
    }

    #[test]
    fn test_resolve_netsim_artifact_dir_windows() {
        let dir = |pairs: &[(&str, &str)]| {
            resolve_netsim_artifact_dir_for_os(&env_of(pairs), HostOs::Windows)
        };
        let under = |base: &str| PathBuf::from(base).join("netsimd");
        // GetTempPath() order is TMP, TEMP, USERPROFILE, then the Windows dir.
        // It is already per-user, so there is no android-$USER level.
        assert_eq!(dir(&[("TEMP", r"C:\T"), ("USER", "alice")]), under(r"C:\T"));
        assert_eq!(dir(&[("TEMP", r"C:\T"), ("TMP", r"C:\M")]), under(r"C:\M"));
        assert_eq!(dir(&[("TMP", ""), ("TEMP", r"C:\T")]), under(r"C:\T"));
        assert_eq!(dir(&[("USERPROFILE", r"C:\Users\alice")]), under(r"C:\Users\alice"));
        assert_eq!(dir(&[("SystemRoot", r"C:\WINDOWS")]), under(r"C:\WINDOWS"));
        assert_eq!(dir(&[]), under(r"C:\Windows"));
        assert_eq!(dir(&[("ANDROID_TMP", r"D:\A"), ("TMP", r"C:\M")]), under(r"D:\A"));
    }

    #[test]
    fn test_resolve_netsim_artifact_dir_unix() {
        for os in [HostOs::Linux, HostOs::MacOs] {
            let dir =
                |pairs: &[(&str, &str)]| resolve_netsim_artifact_dir_for_os(&env_of(pairs), os);
            assert_eq!(
                dir(&[("TMPDIR", "/var/tmp"), ("USER", "alice")]),
                PathBuf::from("/var/tmp/android-alice/netsimd")
            );
            assert_eq!(
                dir(&[("ANDROID_TMP", "/a"), ("TMPDIR", "/var/tmp"), ("USER", "alice")]),
                PathBuf::from("/a/android-alice/netsimd")
            );
            // temp_dir() ignores TEMP and TMP off Windows; an empty USER is unset.
            assert_eq!(
                dir(&[("TEMP", "/ignored"), ("TMP", "/ignored"), ("USER", "")]),
                PathBuf::from("/tmp/android/netsimd")
            );
        }
    }

    #[test]
    fn test_resolve_netsim_artifact_dir_falls_back_to_host_env() {
        // An unreadable (empty) daemon env resolves from the monitor's env.
        let host = || env_of(&[("TMPDIR", "/host_tmp"), ("USER", "bob")]);
        assert_eq!(
            resolve_netsim_artifact_dir(&HashMap::new(), host, HostOs::Linux),
            PathBuf::from("/host_tmp/android-bob/netsimd")
        );
        // A readable daemon env is used as-is, without reading the host env.
        assert_eq!(
            resolve_netsim_artifact_dir(
                &env_of(&[("USER", "alice")]),
                || panic!("host env must not be read"),
                HostOs::Linux
            ),
            PathBuf::from("/tmp/android-alice/netsimd")
        );
    }

    #[test]
    fn test_extract_ini_instance_num() {
        let num = |name: &str| extract_ini_instance_num(Path::new(name));
        assert_eq!(num("netsim.ini"), Some(DEFAULT_NETSIM_INSTANCE));
        assert_eq!(num("/tmp/netsim_2.ini"), Some(2));
        assert_eq!(num("netsim_10.ini"), Some(10));
        for other in [
            "netsim.ini.lock",
            "netsim_2.ini.lock",
            "netsim_2.ini.init.lock",
            "netsim_.ini",
            "netsim_+3.ini",
            "netsim_x.ini",
            "netsim_99999999999.ini",
            "pid_1234.ini",
            "netsim",
        ] {
            assert_eq!(num(other), None, "{other}");
        }
    }

    #[test]
    fn test_loopback_endpoint_port() {
        assert_eq!(loopback_endpoint_port("127.0.0.1:43929"), Some(43929));
        assert_eq!(loopback_endpoint_port("127.8.9.10:1"), Some(1));
        assert_eq!(loopback_endpoint_port("[::1]:43929"), Some(43929));
        assert_eq!(loopback_endpoint_port("[::ffff:127.0.0.1]:9"), Some(9));
        assert_eq!(loopback_endpoint_port("LOCALHOST:80"), Some(80));
        for ep in [
            "10.0.0.5:43929",
            "0.0.0.0:43929",
            "[::]:0",
            "*:*",
            "127.0.0.1:x",
            "127.0.0.1:70000",
            "127.0.0.1",
        ] {
            assert_eq!(loopback_endpoint_port(ep), None, "{ep}");
        }
    }

    fn emulator_5554() -> HashMap<u32, String> {
        HashMap::from([(4001, "emulator-5554".to_string())])
    }

    fn emu_groups_with(pids: &[u32]) -> HashMap<String, EmuGroupNetsimInfo> {
        let group = EmuGroupNetsimInfo {
            serial: "emulator-5554".to_string(),
            pids: pids.to_vec(),
            ..Default::default()
        };
        HashMap::from([("emulator-5554".to_string(), group)])
    }

    fn conn_summary(conns: &[LoopbackGrpcConn]) -> Vec<(&str, u32, u16)> {
        conns.iter().map(|c| (c.serial.as_str(), c.pid, c.peer_port)).collect()
    }

    /// Connections from emulator PID 4001 to 43929 in `LSOF_OUT` and
    /// `NETSTAT_OUT`.
    const EXPECTED_CONNS: [(&str, u32, u16); 2] =
        [("emulator-5554", 4001, 59604), ("emulator-5554", 4001, 59605)];

    /// `lsof -n -P -F pn` output: `p` starts a process, `f` a file, `n` names
    /// it. Besides the expected rows it has a non-loopback peer, a duplicate,
    /// a non-target port, a listener and a PID outside the emulator groups.
    const LSOF_OUT: &str = concat!(
        "p4001\n",
        "f42\nn127.0.0.1:59604->127.0.0.1:43929\n",
        "f43\nn[::1]:59605->[::1]:43929\n",
        "f44\nn10.0.0.5:59606->10.0.0.9:43929\n",
        "f45\nn127.0.0.1:59604->127.0.0.1:43929\n",
        "f46\nn127.0.0.1:59607->127.0.0.1:8554\n",
        "f47\nn127.0.0.1:43929\n",
        "p9999\n",
        "f3\nn127.0.0.1:60000->127.0.0.1:43929\n",
    );

    /// `netstat -ano` output with the same kinds of noise as `LSOF_OUT`, plus
    /// a TIME_WAIT row and a UDP row (whose State column is blank).
    const NETSTAT_OUT: &str = concat!(
        "\nActive Connections\n\n",
        "  Proto  Local Address          Foreign Address        State           PID\n",
        "  TCP    127.0.0.1:59604        127.0.0.1:43929        ESTABLISHED     4001\n",
        "  TCP    [::1]:59605            [::1]:43929            ESTABLISHED     4001\n",
        "  TCP    127.0.0.1:59604        127.0.0.1:43929        ESTABLISHED     4001\n",
        "  TCP    192.168.1.5:59606      192.168.1.9:43929      ESTABLISHED     4001\n",
        "  TCP    127.0.0.1:59607        127.0.0.1:8554         ESTABLISHED     4001\n",
        "  TCP    127.0.0.1:60000        127.0.0.1:43929        TIME_WAIT       4001\n",
        "  TCP    127.0.0.1:60001        127.0.0.1:43929        ESTABLISHED     9999\n",
        "  TCP    127.0.0.1:43929        0.0.0.0:0              LISTENING       7000\n",
        "  UDP    127.0.0.1:43929        *:*                                    4001\n",
    );

    #[test]
    fn test_parse_lsof_established_loopback() {
        let res =
            parse_lsof_established_loopback(LSOF_OUT, &HashSet::from([43929]), &emulator_5554());
        assert_eq!(res.len(), 1);
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_parse_netstat_established_loopback() {
        let res = parse_netstat_established_loopback(
            NETSTAT_OUT,
            &HashSet::from([43929]),
            &emulator_5554(),
        );
        assert_eq!(res.len(), 1);
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_proc_net_tcp_rows() {
        // Ports are hex (0xE8D4 = 59604, 0xAB99 = 43929). Only ESTABLISHED
        // (st 01) rows whose inode belongs to a known PID are returned.
        let tcp = concat!(
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n",
            "   0: 0100007F:E8D4 0100007F:AB99 01 00000000:00000000 00:00000000 00000000 1000 0 111\n",
            "   1: 0100007F:E8D5 0100007F:AB99 06 00000000:00000000 03:00000000 00000000    0 0 0\n",
            "   2: 0100007F:E8D6 0100007F:AB99 01 00000000:00000000 00:00000000 00000000 1000 0 999\n",
        );
        let tcp6 = concat!(
            "  sl  local_address                         remote_address                        st\n",
            "   0: 00000000000000000000000001000000:E8D7 00000000000000000000000001000000:AB99 01 ",
            "00000000:00000000 00:00000000 00000000 1000 0 222\n",
        );
        let inode_to_pid = HashMap::from([(111, 4001), (222, 4002)]);
        assert_eq!(proc_net_tcp_rows(tcp, &inode_to_pid), [(4001, 59604, 43929)]);
        assert_eq!(proc_net_tcp_rows(tcp6, &inode_to_pid), [(4002, 59607, 43929)]);
    }

    #[test]
    fn test_collect_loopback_grpc_sockets_via_cli_macos_runs_lsof() {
        // netsimd (7000) is left out of the lsof PID list, which is sorted.
        let calls = std::cell::RefCell::new(Vec::new());
        let res = collect_loopback_grpc_sockets_via_cli(
            HostOs::MacOs,
            &HashSet::from([43929]),
            &emu_groups_with(&[4002, 7000, 4001]),
            &HashSet::from([7000]),
            &|program: &str, args: &[&str]| {
                calls.borrow_mut().push(format!("{program} {}", args.join(" ")));
                Some(LSOF_OUT.to_string())
            },
        )
        .unwrap();
        assert_eq!(
            calls.into_inner(),
            ["lsof -n -P -F pn -iTCP -sTCP:ESTABLISHED -a -p 4001,4002"]
        );
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_collect_loopback_grpc_sockets_via_cli_windows_runs_netstat() {
        let calls = std::cell::RefCell::new(Vec::new());
        let res = collect_loopback_grpc_sockets_via_cli(
            HostOs::Windows,
            &HashSet::from([43929]),
            &emu_groups_with(&[4001]),
            &HashSet::new(),
            &|program: &str, args: &[&str]| {
                calls.borrow_mut().push(format!("{program} {}", args.join(" ")));
                Some(NETSTAT_OUT.to_string())
            },
        )
        .unwrap();
        assert_eq!(calls.into_inner(), ["netstat -ano"]);
        assert_eq!(conn_summary(&res[&43929]), EXPECTED_CONNS);
    }

    #[test]
    fn test_collect_loopback_grpc_sockets_via_cli_degrades_to_empty() {
        let ports = HashSet::from([43929, 43930]);
        // A tool that fails or times out returns None so callers can keep
        // last-known connections instead of flipping CONNECTED to DISCONNECTED.
        for os in [HostOs::MacOs, HostOs::Windows] {
            let res = collect_loopback_grpc_sockets_via_cli(
                os,
                &ports,
                &emu_groups_with(&[4001]),
                &HashSet::new(),
                &|_: &str, _: &[&str]| None,
            );
            assert!(res.is_none());
        }
        // With no emulator PID to attribute connections to, no tool runs.
        let res = collect_loopback_grpc_sockets_via_cli(
            HostOs::MacOs,
            &ports,
            &emu_groups_with(&[7000]),
            &HashSet::from([7000]),
            &|_: &str, _: &[&str]| panic!("no tool should run"),
        )
        .unwrap();
        assert_eq!(res.len(), 2);
        assert!(res.values().all(Vec::is_empty));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_collect_loopback_grpc_sockets_finds_own_connection_via_proc() {
        // A real loopback connection owned by this process, found through
        // /proc/self/fd and /proc/net/tcp.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let client = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        let client_port = client.local_addr().unwrap().port();
        let me = std::process::id();
        let ports = HashSet::from([port]);
        let res = collect_loopback_grpc_sockets(&ports, &emu_groups_with(&[me]), &HashSet::new());
        assert_eq!(conn_summary(&res[&port]), [("emulator-5554", me, client_port)]);
        // A daemon PID is never attributed to an emulator.
        let res =
            collect_loopback_grpc_sockets(&ports, &emu_groups_with(&[me]), &HashSet::from([me]));
        assert!(res[&port].is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn test_run_cli_with_timeout() {
        let limit = Duration::from_secs(5);
        assert_eq!(run_cli_with_timeout("sh", &["-c", "echo hi"], limit).as_deref(), Some("hi\n"));
        // The exit status is ignored.
        assert_eq!(
            run_cli_with_timeout("sh", &["-c", "echo out; exit 3"], limit).as_deref(),
            Some("out\n")
        );
        assert_eq!(run_cli_with_timeout("/nonexistent/netstat", &[], limit), None);
        // A hung tool is killed at the deadline.
        let started = std::time::Instant::now();
        let hung = run_cli_with_timeout("sh", &["-c", "exec sleep 5"], Duration::from_millis(200));
        assert_eq!(hung, None);
        assert!(started.elapsed() < Duration::from_secs(3), "took {:?}", started.elapsed());
    }

    #[test]
    fn test_pid_exists() {
        assert!(pid_exists(std::process::id()));
        assert!(!pid_exists(DEAD_PID));
    }

    #[cfg(unix)]
    #[test]
    fn test_same_file_resolves_symlinks() {
        let root = tempfile::tempdir().unwrap();
        let real = root.path().join("real");
        std::fs::create_dir_all(&real).unwrap();
        std::fs::write(real.join("netsim.ini"), "").unwrap();
        std::fs::write(root.path().join("other.ini"), "").unwrap();
        let alias = root.path().join("alias");
        std::os::unix::fs::symlink(&real, &alias).unwrap();
        assert!(same_file(&alias.join("netsim.ini"), &real.join("netsim.ini")));
        assert!(!same_file(&real.join("netsim.ini"), &root.path().join("other.ini")));
        // Missing files compare by path.
        assert!(same_file(Path::new("/nonexistent/a.ini"), Path::new("/nonexistent/a.ini")));
        assert!(!same_file(Path::new("/nonexistent/a.ini"), Path::new("/nonexistent/b.ini")));
    }

    #[cfg(unix)]
    #[test]
    fn test_discover_ini_files_dedupes_aliases_and_honors_custom_dirs() {
        let root = tempfile::tempdir().unwrap();
        let real = root.path().join("real");
        std::fs::create_dir_all(&real).unwrap();
        for name in [
            "netsim.ini",
            "netsim_2.ini",
            "netsim.ini.lock",
            "netsim_2.ini.init.lock",
            "netsim_x.ini",
            "pid_42.ini",
        ] {
            std::fs::write(real.join(name), "").unwrap();
        }
        // macOS reaches one dir as both /tmp and /private/tmp, for example.
        let alias = root.path().join("alias");
        std::os::unix::fs::symlink(&real, &alias).unwrap();
        // A daemon's own TMPDIR is not searched while custom dirs are set.
        let other = root.path().join("other");
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(other.join("netsim_3.ini"), "").unwrap();

        let tracker = tracker_for(&[&alias, &real], root.path());
        let found = tracker.discover_ini_files(&[netsimd(DAEMON_A, START, &other)]);
        // One path per file, spelled as first seen.
        assert_eq!(found, vec![alias.join("netsim.ini"), alias.join("netsim_2.ini")]);
    }

    #[test]
    fn test_netsim_tracker_stale_ini_does_not_steal_live_daemon() {
        // A leftover INI from a dead daemon must not override the live INI,
        // whichever sorts first and whether or not netsim wrote `pid=`.
        for with_pid in [false, true] {
            for stale_first in [false, true] {
                let ctx = format!("with_pid={with_pid} stale_first={stale_first}");
                let root = tempfile::tempdir().unwrap();
                let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
                std::fs::create_dir_all(&dir_a).unwrap();
                std::fs::create_dir_all(&dir_b).unwrap();
                let (stale_dir, live_dir) =
                    if stale_first { (&dir_a, &dir_b) } else { (&dir_b, &dir_a) };
                let (stale_ini, live_ini) =
                    (stale_dir.join("netsim.ini"), live_dir.join("netsim.ini"));
                let pid_line =
                    |pid: u32| if with_pid { format!("pid={pid}\n") } else { String::new() };
                write_ini(
                    &stale_ini,
                    &format!("{}grpc.port=1111\n", pid_line(DEAD_PID)),
                    START - 600,
                );
                write_ini(
                    &live_ini,
                    &format!("{}grpc.port=43929\n", pid_line(DAEMON_A)),
                    START + 1,
                );

                let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
                let procs = [netsimd(DAEMON_A, START, live_dir)];
                for now in [START + 10, START + 20] {
                    tick(&mut tracker, &procs, now);
                    let d = &tracker.daemons[&(DAEMON_A, START)];
                    assert_eq!(d.grpc_port, Some(43929), "{ctx}");
                    assert_eq!(d.ini_path.as_deref(), Some(live_ini.as_path()), "{ctx}");
                    assert!(tracker.stale_inis.contains_key(&stale_ini), "{ctx}");
                    assert!(!tracker.stale_inis.contains_key(&live_ini), "{ctx}");
                }
                assert_eq!(count_events(&tracker, "STALE_INI("), 1, "{ctx}");
                assert_eq!(tracker.restart_count, 0, "{ctx}");
            }
        }
    }

    #[test]
    fn test_netsim_tracker_rejects_ini_with_reused_pid() {
        // The INI predates the daemon whose PID it names, so that PID was
        // reused by a new netsimd and the INI is stale.
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=1111\n"), START - 600);
        let mut tracker = tracker_for(&[root.path()], root.path());
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);
        let d = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d.grpc_port, None);
        assert_eq!(d.ini_path, None);
        assert!(tracker.stale_inis.contains_key(&ini));
    }

    #[test]
    fn test_netsim_tracker_reused_pid_of_live_daemon_is_stale() {
        // As above, but a process with the reused PID is alive: it is the
        // tracked daemon, not the INI's writer, so the INI is still stale.
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=1111\n"), START - 600);
        let mut tracker = tracker_for(&[root.path()], root.path());
        tracker.pid_alive = |pid| pid == DAEMON_A;
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].ini_path, None);
        assert!(tracker.stale_inis.contains_key(&ini));
    }

    #[test]
    fn test_netsim_tracker_counts_restart_once() {
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
        let mut tracker = tracker_for(&[root.path()], root.path());
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);

        // netsimd B replaces A and rewrites the INI; later ticks see no restart.
        let b_start = START + 60;
        write_ini(&ini, &format!("pid={DAEMON_B}\ngrpc.port=43930\n"), b_start + 1);
        let procs_b = [netsimd(DAEMON_B, b_start, root.path())];
        tick(&mut tracker, &procs_b, b_start + 5);
        tick(&mut tracker, &procs_b, b_start + 10);

        assert_eq!(tracker.restart_count, 1);
        assert_eq!(count_events(&tracker, "RESTARTED("), 1);
        let restarted =
            format!("RESTARTED(ini={}, old_pid={DAEMON_A}, new_pid={DAEMON_B})", ini.display());
        assert!(tracker.timeline.iter().any(|(_, e)| *e == restarted), "{:?}", tracker.timeline);
        assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(43930));
        assert!(tracker.daemons[&(DAEMON_A, START)].exited);
        assert!(tracker.stale_inis.is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn test_netsim_tracker_lock_holder_beats_conflicting_pid() {
        // The INI's lock holder (B) wins over the daemon its `pid=` names (A).
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
        let lock = root.path().join("netsim.ini.lock");
        std::fs::write(&lock, "").unwrap();
        let lock_key = get_file_lock_key(&lock).unwrap();

        let mut tracker = tracker_for(&[root.path()], root.path());
        tracker.custom_proc_locks =
            Some(format!("1: FLOCK  ADVISORY  WRITE {DAEMON_B} {lock_key} 0 EOF\n"));
        let procs = [netsimd(DAEMON_A, START, root.path()), netsimd(DAEMON_B, START, root.path())];
        tick(&mut tracker, &procs, START + 10);

        let (a, b) = (&tracker.daemons[&(DAEMON_A, START)], &tracker.daemons[&(DAEMON_B, START)]);
        assert_eq!(b.ini_path.as_deref(), Some(ini.as_path()));
        assert_eq!(b.grpc_port, Some(43929));
        assert_eq!(a.ini_path, None);
        assert_eq!(a.grpc_port, None);
        assert!(tracker.stale_inis.is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn test_netsim_tracker_ini_with_untracked_holder_ignores_pid() {
        // An INI with lock holders pairs only through them: an untracked
        // holder (B) keeps the daemon its `pid=` names (A) from taking it,
        // and a held INI is not stale.
        let root = tempfile::tempdir().unwrap();
        let ini = root.path().join("netsim.ini");
        write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
        let lock = root.path().join("netsim.ini.lock");
        std::fs::write(&lock, "").unwrap();
        let lock_key = get_file_lock_key(&lock).unwrap();

        let mut tracker = tracker_for(&[root.path()], root.path());
        tracker.custom_proc_locks =
            Some(format!("1: FLOCK  ADVISORY  WRITE {DAEMON_B} {lock_key} 0 EOF\n"));
        tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);

        let a = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(a.ini_path, None);
        assert_eq!(a.grpc_port, None);
        assert!(tracker.stale_inis.is_empty());
    }

    #[test]
    fn test_netsim_tracker_instance_fallback_pairs_by_ini_name() {
        // Without lock or pid evidence, `--instance 2` pairs with
        // netsim_2.ini; the default-instance INI has no daemon and is stale.
        let root = tempfile::tempdir().unwrap();
        let (ini1, ini2) = (root.path().join("netsim.ini"), root.path().join("netsim_2.ini"));
        write_ini(&ini1, "grpc.port=1111\n", START + 1);
        write_ini(&ini2, "grpc.port=43929\n", START + 1);
        let mut proc2 = netsimd(DAEMON_A, START, root.path());
        proc2.cmd.extend(["--instance".to_string(), "2".to_string()]);

        let mut tracker = tracker_for(&[root.path()], root.path());
        tick(&mut tracker, &[proc2], START + 10);
        let d = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d.instance_num, Some(2));
        assert_eq!(d.grpc_port, Some(43929));
        assert_eq!(d.ini_path.as_deref(), Some(ini2.as_path()));
        assert!(tracker.stale_inis.contains_key(&ini1));
    }

    #[test]
    fn test_netsim_tracker_pairs_same_instance_daemons_by_start_time() {
        // Two older-netsim daemons (no `pid=`) with different TMPDIRs each
        // pair with the INI written after their own start. B's INI sorts
        // first, so taking INIs in path order would give A the wrong one.
        let root = tempfile::tempdir().unwrap();
        let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
        std::fs::create_dir_all(&dir_a).unwrap();
        std::fs::create_dir_all(&dir_b).unwrap();
        let b_start = START + 100;
        write_ini(&dir_a.join("netsim.ini"), "grpc.port=2222\n", b_start + 1);
        write_ini(&dir_b.join("netsim.ini"), "grpc.port=1111\n", START + 1);

        let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
        let procs = [netsimd(DAEMON_A, START, &dir_b), netsimd(DAEMON_B, b_start, &dir_a)];
        tick(&mut tracker, &procs, b_start + 5);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(1111));
        assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(2222));
        assert!(tracker.stale_inis.is_empty());
    }

    #[test]
    fn test_netsim_tracker_newest_daemon_takes_oldest_ini() {
        // Both pid-less INIs were written after both daemons started, so
        // either daemon could own either one. B, the newer daemon, chooses
        // first and takes the INI written closest to its start.
        let root = tempfile::tempdir().unwrap();
        let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
        std::fs::create_dir_all(&dir_a).unwrap();
        std::fs::create_dir_all(&dir_b).unwrap();
        let b_start = START + 100;
        write_ini(&dir_a.join("netsim.ini"), "grpc.port=1111\n", b_start + 50);
        write_ini(&dir_b.join("netsim.ini"), "grpc.port=2222\n", b_start + 1);

        let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
        let procs = [netsimd(DAEMON_A, START, &dir_a), netsimd(DAEMON_B, b_start, &dir_b)];
        tick(&mut tracker, &procs, b_start + 60);
        assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(1111));
        assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(2222));
        assert!(tracker.stale_inis.is_empty());
    }

    #[test]
    fn test_netsim_tracker_ini_instance_fallback_without_proc_locks() {
        let dir = tempfile::tempdir().unwrap();
        let ini_path = dir.path().join("netsim.ini");
        write_ini(&ini_path, "grpc.port=43929\n", START + 1);

        let procs = vec![NetsimDaemonProc {
            pid: DAEMON_A,
            parent: Some(8000),
            parent_name: Some("qemu-system-x86_64.exe".to_string()),
            start_time: START,
            exe: Some(PathBuf::from(r"C:\Android\Sdk\emulator\netsimd.exe")),
            cmd: vec![r"C:\Android\Sdk\emulator\netsimd.exe".to_string()],
            environ: env_of(&[("TMPDIR", dir.path().to_str().unwrap())]),
            cpu: 1.0,
            ram_mb: 30.0,
            same_user: true,
        }];

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tick(&mut tracker, &procs, START + 10);
        let d = &tracker.daemons[&(DAEMON_A, START)];
        assert_eq!(d.spawner, "qemu");
        assert_eq!(d.grpc_port, Some(43929));
        assert_eq!(d.ini_path.as_deref(), Some(ini_path.as_path()));
        assert!(tracker.stale_inis.is_empty());
    }

    const WORKER_A: u32 = 5_000_300;
    const WORKER_B: u32 = 5_000_400;

    fn emu_group(serial: &str, pid: u32, avd_id: &str, name: Option<&str>) -> EmuGroupNetsimInfo {
        EmuGroupNetsimInfo {
            serial: serial.to_string(),
            primary_pid: pid,
            avd_id: Some(avd_id.to_string()),
            display_name: name.map(str::to_string),
            ..Default::default()
        }
    }

    /// One tick with no netsim daemons and the given live emulators, each as
    /// (storage key, group).
    fn emu_tick(tracker: &mut NetsimTracker, live: &[(&str, &EmuGroupNetsimInfo)], now_mono: f64) {
        emu_tick_with(tracker, &[], live, now_mono);
    }

    fn emu_tick_with(
        tracker: &mut NetsimTracker,
        procs: &[NetsimDaemonProc],
        live: &[(&str, &EmuGroupNetsimInfo)],
        now_mono: f64,
    ) {
        emu_tick_adb(tracker, procs, live, &[], now_mono);
    }

    /// Like emu_tick_with, with adb listing each (serial, state) in `adb`.
    fn emu_tick_adb(
        tracker: &mut NetsimTracker,
        procs: &[NetsimDaemonProc],
        live: &[(&str, &EmuGroupNetsimInfo)],
        adb: &[(&str, &str)],
        now_mono: f64,
    ) {
        let groups: HashMap<String, EmuGroupNetsimInfo> =
            live.iter().map(|(_, g)| (g.serial.clone(), (*g).clone())).collect();
        let summary: Vec<(String, String, u32, f64)> = live
            .iter()
            .map(|(k, g)| (k.to_string(), g.serial.clone(), g.primary_pid, now_mono))
            .collect();
        let adb: HashMap<String, String> =
            adb.iter().map(|(s, st)| (s.to_string(), st.to_string())).collect();
        let now_wall = START as f64 + now_mono;
        tracker.update(procs, &groups, &summary, &adb, now_wall, now_mono);
    }

    #[test]
    fn test_netsim_tracker_exited_emulator_disconnects() {
        // The emulator was connected from t=10 and has left the host scan by t=40.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.emulator_records.insert(
            "pixel_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "pixel_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: WORKER_A,
                state: "CONNECTED".to_string(),
                first_transport_ts: Some(START as f64 + 10.0),
                connected_start_mono: Some(10.0),
                adb_online_since_mono: Some(10.0),
                ..Default::default()
            },
        );

        // The first missed tick may be a scan hiccup, so the record is held.
        emu_tick(&mut tracker, &[], 40.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

        // The second settles it as of the first miss, so the hold adds no
        // connected time.
        emu_tick(&mut tracker, &[], 45.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!(rec.state, "DISCONNECTED");
        assert_eq!(rec.disconnect_count, 1);
        assert_eq!(rec.connected_total_s, 30.0);
        assert_eq!(rec.connected_start_mono, None);
        assert_eq!(rec.adb_online_since_mono, None);

        // Later ticks neither count the disconnect again nor add connected time,
        // and the CSV keeps reporting how the emulator ended.
        emu_tick(&mut tracker, &[], 50.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.connected_total_s, 30.0);
        let fields = tracker.get_csv_fields();
        assert_eq!(fields["netsim_state_pixel_emulator-5554"], "DISCONNECTED");
    }

    #[test]
    fn test_netsim_tracker_exited_emulator_that_never_connected_fails() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let group = emu_group("emulator-5554", WORKER_A, "pixel", None);
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 1.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");

        emu_tick(&mut tracker, &[], 2.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");
        emu_tick(&mut tracker, &[], 3.0);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!(rec.state, "FAILED(worker_exit_no_connect)");
        assert_eq!(rec.disconnect_count, 0);
    }

    #[test]
    fn test_netsim_tracker_csv_covers_emulators_adb_does_not_list() {
        // netsim can connect before adb lists the emulator (emu_tick passes no
        // adb devices), so the CSV has a column for every record.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let group = emu_group("emulator-5554", WORKER_A, "pixel", None);
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 1.0);
        let fields = tracker.get_csv_fields();
        assert_eq!(fields["netsim_state_pixel_emulator-5554"], "CONNECTING");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_netsim_tracker_worker_start_comes_from_proc() {
        // The worker's start time is the kernel's, whatever the tick's clock
        // says: here the tick is dated days before this process started.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let now = now_wall_secs();
        let group = emu_group("emulator-5554", std::process::id(), "pixel", None);
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 10.0);
        let start = tracker.emulator_records["pixel_emulator-5554"].worker_start_ts;
        assert!(start <= now && start > now - 3600.0, "start={start} now={now}");
    }

    #[test]
    fn test_netsim_tracker_session_keys_follow_worker() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let pixel = emu_group("emulator-5554", WORKER_A, "pixel", None);

        // The first scan has no AVD name yet, so the record is keyed by serial.
        emu_tick(&mut tracker, &[("emulator-5554", &pixel)], 1.0);
        assert!(tracker.emulator_records.contains_key("emulator-5554"));

        // Once the AVD name is known the same worker's record moves to the
        // AVD-qualified key instead of being left behind as an exited emulator.
        emu_tick(&mut tracker, &[("pixel_emulator-5554", &pixel)], 2.0);
        let keys: Vec<&String> = tracker.emulator_records.keys().collect();
        assert_eq!(keys, ["pixel_emulator-5554"]);
        let rec = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!(rec.storage_key, "pixel_emulator-5554");
        assert_eq!(rec.first_seen_mono, 1.0);
        assert_eq!(rec.state, "CONNECTING");

        // A scan that briefly loses the AVD name keeps using that record.
        emu_tick(&mut tracker, &[("emulator-5554", &pixel)], 3.0);
        let keys: Vec<&String> = tracker.emulator_records.keys().collect();
        assert_eq!(keys, ["pixel_emulator-5554"]);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");

        // A different AVD on the reused port is a new worker: it gets its own
        // record and the previous emulator settles as exited.
        let other = emu_group("emulator-5554", WORKER_B, "other", None);
        emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 4.0);
        assert_eq!(
            tracker.emulator_records["pixel_emulator-5554"].state,
            "FAILED(worker_exit_no_connect)"
        );
        let rec = &tracker.emulator_records["other_emulator-5554"];
        assert_eq!((rec.worker_pid, rec.state.as_str()), (WORKER_B, "CONNECTING"));
    }

    #[test]
    fn test_netsim_tracker_never_downgrades_display_name() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        let key = "pixel_emulator-5554";
        let device_name = |t: &NetsimTracker| t.emulator_records[key].device_name.clone();

        // Before pid_<pid>.ini is written only the AVD id is known.
        let no_ini = emu_group("emulator-5554", WORKER_A, "pixel", None);
        emu_tick(&mut tracker, &[(key, &no_ini)], 1.0);
        assert_eq!(device_name(&tracker).as_deref(), Some("pixel"));

        let with_ini = emu_group("emulator-5554", WORKER_A, "pixel", Some("Pixel Test"));
        emu_tick(&mut tracker, &[(key, &with_ini)], 2.0);
        assert_eq!(device_name(&tracker).as_deref(), Some("Pixel Test"));

        // The emulator deletes the ini while the worker shuts down.
        emu_tick(&mut tracker, &[(key, &no_ini)], 3.0);
        assert_eq!(device_name(&tracker).as_deref(), Some("Pixel Test"));
    }

    /// Phone and Glasses on one netsimd, whose log also has a device named
    /// after a serial.
    const SHARED_DAEMON_LOG: &str = concat!(
        "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n",
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
        "netsim I 09-25 22:37:08.000 netsimd.rs:106 - Handling new connection for \"\", device Glasses\n",
        "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Glasses)\n",
        "netsim I 09-25 22:37:09.000 service.rs:585 - DeviceActor: AddChip 3 (kind WIFI) to device 3 (emulator-5556)\n",
    );

    /// A tracker that tails the log of its one netsimd, which writes `log`.
    fn tracker_with_daemon_log(dir: &Path, log: &str) -> (NetsimTracker, [NetsimDaemonProc; 1]) {
        let art_dir = dir.join("android-test").join("netsimd");
        std::fs::create_dir_all(&art_dir).unwrap();
        std::fs::write(art_dir.join("netsim_stderr.log"), log).unwrap();
        let mut tracker = tracker_for(&[dir], dir);
        tracker.enable_log = true;
        (tracker, [netsimd(DAEMON_A, START, dir)])
    }

    fn chip_ids(tracker: &NetsimTracker, key: &str) -> Vec<String> {
        tracker.emulator_records[key].chip_ids.iter().cloned().collect()
    }

    #[test]
    fn test_netsim_tracker_fallback_skips_exited_emulator_chips() {
        // Once Phone exits, Glasses is the only live emulator: it takes the chip
        // nobody claims, but never Phone's.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let glasses = emu_group("emulator-5556", WORKER_B, "glasses", Some("Glasses"));

        let both = [("phone_emulator-5554", &phone), ("glasses_emulator-5556", &glasses)];
        emu_tick_with(&mut tracker, &procs, &both, 10.0);
        assert_eq!(chip_ids(&tracker, "phone_emulator-5554"), ["1"]);
        assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2"]);
        let glasses_first_ts = tracker.emulator_records["glasses_emulator-5556"].first_transport_ts;
        assert!(glasses_first_ts.is_some());

        emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 20.0);
        // Phone missed one tick and may still be running, so Glasses is not
        // the only live emulator yet.
        let phone_rec = &tracker.emulator_records["phone_emulator-5554"];
        assert_eq!((phone_rec.state.as_str(), phone_rec.disconnect_count), ("CONNECTED", 0));
        assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2"]);

        emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 25.0);
        let phone_rec = &tracker.emulator_records["phone_emulator-5554"];
        assert_eq!((phone_rec.state.as_str(), phone_rec.disconnect_count), ("DISCONNECTED", 1));
        let glasses_rec = &tracker.emulator_records["glasses_emulator-5556"];
        assert_eq!(glasses_rec.state, "CONNECTED");
        assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2", "3"]);
        assert_eq!(glasses_rec.first_transport_ts, glasses_first_ts);
    }

    #[test]
    fn test_netsim_tracker_fallback_skips_exited_emulator_connections() {
        // An emulator whose name is not known yet matches no device while Phone
        // runs. As the only live emulator it takes the unclaimed connections and
        // chips, and their device name, but none of Phone's.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let unnamed = EmuGroupNetsimInfo {
            serial: "emulator-5556".to_string(),
            primary_pid: WORKER_B,
            ..Default::default()
        };

        let both = [("phone_emulator-5554", &phone), ("emulator-5556", &unnamed)];
        emu_tick_with(&mut tracker, &procs, &both, 10.0);
        let rec = &tracker.emulator_records["emulator-5556"];
        assert_eq!((rec.state.as_str(), rec.first_transport_ts), ("CONNECTING", None));

        // Phone is held for its first missed tick, so nothing moves yet.
        emu_tick_with(&mut tracker, &procs, &[("emulator-5556", &unnamed)], 20.0);
        let rec = &tracker.emulator_records["emulator-5556"];
        assert_eq!((rec.device_name.as_deref(), rec.first_transport_ts), (None, None));

        emu_tick_with(&mut tracker, &procs, &[("emulator-5556", &unnamed)], 25.0);
        let phone_first_ts = tracker.emulator_records["phone_emulator-5554"].first_transport_ts;
        let rec = &tracker.emulator_records["emulator-5556"];
        assert_eq!(rec.device_name.as_deref(), Some("Glasses"));
        assert_eq!(chip_ids(&tracker, "emulator-5556"), ["2", "3"]);
        assert!(rec.first_transport_ts.is_some_and(|t| phone_first_ts.is_some_and(|p| t > p)));
    }

    #[test]
    fn test_netsim_tracker_host_scan_miss_while_adb_lists_keeps_state() {
        // adb still lists the emulator, so a host-scan miss is a scan hiccup,
        // however long it lasts.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");

        let adb = HashMap::from([("emulator-5554".to_string(), "device".to_string())]);
        for now_mono in [20.0, 30.0, 40.0] {
            tracker.update(&procs, &HashMap::new(), &[], &adb, START as f64 + now_mono, now_mono);
            let rec = &tracker.emulator_records[key];
            assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
        }
    }

    #[test]
    fn test_netsim_tracker_one_tick_miss_is_not_a_disconnect() {
        // The emulator misses both the host scan and adb for one tick, then
        // comes back: it stays connected, with no disconnect and no lost time.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

        emu_tick_with(&mut tracker, &procs, &[], 20.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
        assert_eq!(tracker.get_csv_fields()["netsim_state_phone_emulator-5554"], "CONNECTED");

        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 30.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!(
            (rec.state.as_str(), rec.disconnect_count, rec.connected_start_mono),
            ("CONNECTED", 0, Some(10.0))
        );

        // Seeing it again ended the miss run, so a new miss is held again.
        emu_tick_with(&mut tracker, &procs, &[], 40.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
    }

    #[test]
    fn test_netsim_tracker_miss_run_restarts_when_adb_lists() {
        // Only consecutive ticks missing from both the host scan and adb
        // count, and the exit is dated to the first of them.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        let adb = [("emulator-5554", "device")];
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

        emu_tick_with(&mut tracker, &procs, &[], 20.0);
        emu_tick_adb(&mut tracker, &procs, &[], &adb, 30.0);
        emu_tick_adb(&mut tracker, &procs, &[], &adb, 40.0);
        emu_tick_with(&mut tracker, &procs, &[], 50.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
        // While held, the summary counts connected time only up to the miss.
        let summary = tracker.format_summary_sections(55.0).join("\n");
        assert!(summary.contains("connected_time=40.0s"), "{summary}");

        emu_tick_with(&mut tracker, &procs, &[], 60.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.connected_total_s, 40.0);
    }

    #[test]
    fn test_netsim_tracker_stale_adb_offline_entry_does_not_hold() {
        // adb can keep listing a killed emulator as 'offline'.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        let adb = [("emulator-5554", "offline")];
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

        emu_tick_adb(&mut tracker, &procs, &[], &adb, 20.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        emu_tick_adb(&mut tracker, &procs, &[], &adb, 30.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(rec.connected_total_s, 10.0);
    }

    #[test]
    fn test_netsim_tracker_settled_emulator_stays_settled() {
        // A daemon that later reports a startup timeout does not turn an
        // emulator that already disconnected into a launcher failure.
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
        emu_tick_with(&mut tracker, &procs, &[], 20.0);
        emu_tick_with(&mut tracker, &procs, &[], 30.0);
        let daemon_key = tracker.emulator_records[key].daemon_key.unwrap();
        assert_eq!(tracker.emulator_records[key].state, "DISCONNECTED");

        tracker.daemons.get_mut(&daemon_key).unwrap().exit_reason =
            Some("startup_timeout".to_string());
        emu_tick_with(&mut tracker, &procs, &[], 40.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    }

    #[test]
    fn test_netsim_tracker_replaced_emulator_settles_at_once() {
        // Another AVD takes the serial of a held emulator, so the held record
        // settles at once, still dated to its first missed tick.
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.emulator_records.insert(
            "pixel_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "pixel_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: WORKER_A,
                state: "CONNECTED".to_string(),
                first_transport_ts: Some(START as f64 + 10.0),
                connected_start_mono: Some(10.0),
                ..Default::default()
            },
        );
        emu_tick(&mut tracker, &[], 40.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTED");

        let other = emu_group("emulator-5554", WORKER_B, "other", None);
        emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 45.0);
        let old = &tracker.emulator_records["pixel_emulator-5554"];
        assert_eq!((old.state.as_str(), old.disconnect_count), ("DISCONNECTED", 1));
        assert_eq!(old.connected_total_s, 30.0);
        let new = &tracker.emulator_records["other_emulator-5554"];
        assert_eq!((new.worker_pid, new.state.as_str()), (WORKER_B, "CONNECTING"));
    }

    #[test]
    fn test_poll_sockets_fast_skips_settled_and_non_connecting_records() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
            .unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.socket_os = HostOs::MacOs;
        tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
        let procs = [netsimd(DAEMON_A, START, dir.path())];
        emu_tick_with(&mut tracker, &procs, &[], 1.0);
        for (key, state, no_netsim) in [
            ("failed_emulator-5554", "FAILED(worker_exit_no_connect)", false),
            ("disc_emulator-5554", "DISCONNECTED", false),
            ("disabled_emulator-5554", "CONNECTING", true),
            ("connected_emulator-5554", "CONNECTED", false),
            ("live_emulator-5554", "CONNECTING", false),
        ] {
            tracker.emulator_records.insert(
                key.to_string(),
                EmulatorNetsimRecord {
                    storage_key: key.to_string(),
                    serial: "emulator-5554".to_string(),
                    worker_pid: 4001,
                    worker_start_ts: 100.0,
                    state: state.to_string(),
                    no_netsim,
                    ..Default::default()
                },
            );
        }

        tracker.poll_sockets_fast(&emu_groups_with(&[4001]), 105.0);
        for skipped in [
            "failed_emulator-5554",
            "disc_emulator-5554",
            "disabled_emulator-5554",
            "connected_emulator-5554",
        ] {
            let rec = &tracker.emulator_records[skipped];
            assert_eq!((rec.first_transport_ts, rec.netsim_connect_s), (None, None), "{skipped}");
        }
        let live = &tracker.emulator_records["live_emulator-5554"];
        assert_eq!((live.first_transport_ts, live.netsim_connect_s), (Some(105.0), Some(5.0)));
    }

    #[test]
    fn test_netsim_tracker_worker_exit_with_first_transport_settles_to_failed() {
        let dir = tempfile::tempdir().unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        // Unjoined path: worker opened a socket (`first_transport_ts` set)
        // while still CONNECTING, then exited before reaching CONNECTED.
        tracker.emulator_records.insert(
            "pixel_emulator-5554".to_string(),
            EmulatorNetsimRecord {
                storage_key: "pixel_emulator-5554".to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: WORKER_A,
                worker_start_ts: START as f64 + 1.0,
                first_seen_mono: 1.0,
                state: "CONNECTING".to_string(),
                first_transport_ts: Some(START as f64 + 1.5),
                netsim_connect_s: Some(0.5),
                ..Default::default()
            },
        );
        emu_tick(&mut tracker, &[], 10.0);
        assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");
        emu_tick(&mut tracker, &[], 20.0);
        assert_eq!(
            tracker.emulator_records["pixel_emulator-5554"].state,
            "FAILED(worker_exit_no_connect)"
        );

        // Joined path: UT-5 (live emulator with first_transport_ts set and no
        // active sockets or chips transitions to STALLED after 50s), then
        // settles to FAILED(worker_exit_no_connect) when `emu_groups` no longer
        // contains the worker.
        let conn_only_log = "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n";
        let dir2 = tempfile::tempdir().unwrap();
        let (mut tracker2, procs2) = tracker_with_daemon_log(dir2.path(), conn_only_log);
        let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";
        let summary = vec![(key.to_string(), "emulator-5554".to_string(), WORKER_A, 1.0)];
        let mut groups = HashMap::new();
        groups.insert("emulator-5554".to_string(), phone);
        tracker2.update(&procs2, &groups, &summary, &HashMap::new(), START as f64 + 10.0, 10.0);
        assert!(tracker2.emulator_records[key].first_transport_ts.is_some());
        assert_eq!(tracker2.emulator_records[key].state, "CONNECTING");

        // UT-5: Still live at t=52.0 (>= 50s since first_seen_mono=1.0) -> STALLED.
        tracker2.update(&procs2, &groups, &summary, &HashMap::new(), START as f64 + 52.0, 52.0);
        assert_eq!(tracker2.emulator_records[key].state, "STALLED");

        // Worker exits from `emu_groups` while still listed in `summary` -> FAILED.
        tracker2.update(
            &procs2,
            &HashMap::new(),
            &summary,
            &HashMap::new(),
            START as f64 + 55.0,
            55.0,
        );
        let rec = &tracker2.emulator_records[key];
        assert!(rec.first_transport_ts.is_some());
        assert_eq!(rec.state, "FAILED(worker_exit_no_connect)");

        // Subsequent ticks do not overwrite the settled FAILED state back to CONNECTING.
        tracker2.update(
            &procs2,
            &HashMap::new(),
            &summary,
            &HashMap::new(),
            START as f64 + 60.0,
            60.0,
        );
        assert_eq!(tracker2.emulator_records[key].state, "FAILED(worker_exit_no_connect)");
    }

    #[test]
    fn test_netsim_tracker_cli_socket_failure_retains_cache_until_clear() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
            .unwrap();
        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.socket_os = HostOs::MacOs;
        tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
        let procs = [netsimd(DAEMON_A, START, dir.path())];
        let mut phone = emu_group("emulator-5554", 4001, "phone", Some("Phone"));
        phone.pids = vec![4001];
        let key = "phone_emulator-5554";

        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

        // Transient CLI timeout/failure (None) keeps the cached connection for
        // live PID 4001 so the emulator does not flip to DISCONNECTED.
        tracker.socket_cli_runner = |_, _| None;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 20.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

        // A successful empty CLI scan (Some("")) clears the cache and settles
        // the socket-only connection to DISCONNECTED.
        tracker.socket_cli_runner = |_, _| Some(String::new());
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 30.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));

        // UT-7: Reconnect PID 4001 at t=40, then replace with PID 4002 at t=50
        // while lsof times out (None) -> stale cached socket for exited PID
        // 4001 is pruned rather than retained.
        tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
        emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 40.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        let mut phone_b = emu_group("emulator-5554", 4002, "phone", Some("Phone"));
        phone_b.pids = vec![4002];
        tracker.socket_cli_runner = |_, _| None;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 50.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTING");
    }

    #[test]
    fn test_netsim_tracker_same_avd_restart_resets_state_and_merges_bare_serial() {
        let phone_log = concat!(
            "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n",
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
        );
        let dir = tempfile::tempdir().unwrap();
        let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), phone_log);
        let phone_a = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
        let key = "phone_emulator-5554";

        // Run 1 (WORKER_A) connects at t=10.
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_a)], 10.0);
        assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
        assert_eq!(chip_ids(&tracker, key), ["1"]);
        let run1_log_ts = tracker.emulator_records[key].first_transport_ts.unwrap() as u64;

        // Run 2 (WORKER_B) of the same AVD restarts on the same port at t=60
        // (>=50s after Run 1): first seen under bare serial "emulator-5554"
        // before AVD id resolves, stamps `first_transport_ts`, then promoted to
        // "phone_emulator-5554" at t=61.
        let mut phone_b_unnamed = emu_group("emulator-5554", WORKER_B, "phone", None);
        phone_b_unnamed.avd_id = None;
        phone_b_unnamed.primary_start_time = run1_log_ts + 60;
        emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &phone_b_unnamed)], 60.0);
        assert!(tracker.emulator_records.contains_key("emulator-5554"));
        tracker.emulator_records.get_mut("emulator-5554").unwrap().first_transport_ts =
            Some(run1_log_ts as f64 + 61.0);

        let mut phone_b = emu_group("emulator-5554", WORKER_B, "phone", Some("Phone"));
        phone_b.primary_start_time = run1_log_ts + 60;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 61.0);
        assert!(!tracker.emulator_records.contains_key("emulator-5554"));
        let rec = &tracker.emulator_records[key];
        assert_eq!(rec.worker_pid, WORKER_B);
        assert_eq!(rec.disconnect_count, 1);
        assert_eq!(rec.connected_total_s, 50.0);
        // Pre-restart log entries from Run 1 are before WORKER_B's start time
        // and are ignored, while Tick-1's transport timestamp on the bare
        // record is preserved.
        assert_eq!(rec.state, "CONNECTING");
        assert!(rec.chip_attach_s.is_empty());
        assert!(rec.chip_ids.is_empty());
        assert_eq!(rec.first_transport_ts, Some(run1_log_ts as f64 + 61.0));
        assert_eq!(rec.netsim_connect_s, Some(1.0));

        // Direct same-key restart (WORKER_B -> 4003) at t=70 without bare-serial
        // stage also resets per-run state and `first_seen_mono` so it starts in
        // CONNECTING (not STALLED) while preserving cumulative history.
        let mut phone_c = emu_group("emulator-5554", 4003, "phone", Some("Phone"));
        phone_c.primary_start_time = run1_log_ts + 70;
        emu_tick_with(&mut tracker, &procs, &[(key, &phone_c)], 70.0);
        let rec = &tracker.emulator_records[key];
        assert_eq!(rec.worker_pid, 4003);
        assert_eq!(rec.disconnect_count, 1);
        assert_eq!(rec.connected_total_s, 50.0);
        assert_eq!(rec.state, "CONNECTING");
        assert_eq!(rec.first_transport_ts, None);
    }

    #[cfg(unix)]
    #[test]
    fn test_run_cli_output_with_timeout_captures_stderr_and_kills_slow_process() {
        // Verifies stdout+stderr capture and timeout termination for default_cli_runner's helper.
        let ok_res = run_cli_output_with_timeout(
            "sh",
            &["-c", "printf 'out'; printf 'err' >&2"],
            Duration::from_secs(2),
        );
        assert_eq!(ok_res, Some(("out".to_string(), "err".to_string())));

        let timed_out =
            run_cli_output_with_timeout("sh", &["-c", "sleep 30"], Duration::from_millis(50));
        assert_eq!(timed_out, None);
    }

    #[test]
    fn test_log_tailer_bounds_accumulated_event_lists() {
        // Verifies NetsimLogTailer caps accumulated event/connection/chip lists
        // to MAX_TAILER_EVENTS while retaining the most recent entries.
        let mut tailer = NetsimLogTailer::new(PathBuf::new());
        let limit = MAX_TAILER_EVENTS;
        for i in 0..(limit + 25) {
            let now = 1000.0 + i as f64;
            tailer.feed_line(
                &format!("I0330 00:00:01.000Z src/lib.rs:1] Handling new connection for BLUETOOTH on device dev-{i}"),
                now,
            );
            tailer.feed_line(
                &format!("I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Create device dev-{i}"),
                now,
            );
            tailer.feed_line(
                &format!(r#"I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: AddChip {i} (kind BLUETOOTH, name "bt-{i}") to device {i} (dev-{i})"#),
                now,
            );
            tailer.feed_line(
                &format!("I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Removed chip {i} (kind BLUETOOTH) from device {i}"),
                now,
            );
            tailer.feed_line(
                &format!("I0330 00:00:01.000Z src/lib.rs:1] DeviceActor: Device dev-{i} is empty, auto-deleting"),
                now,
            );
            tailer.feed_line(
                "I0330 00:00:01.000Z src/lib.rs:1] Wrote to INI file /tmp/netsim.ini",
                now,
            );
        }

        let last_name = format!("dev-{}", limit + 24);
        assert_eq!(tailer.connections.len(), limit);
        assert_eq!(tailer.connections.last().unwrap().2, last_name);
        assert_eq!(tailer.created_devices.len(), limit);
        assert_eq!(tailer.created_devices.last().unwrap().1, last_name);
        assert_eq!(tailer.chip_adds.len(), limit);
        assert_eq!(tailer.chip_adds.last().unwrap().device_name, last_name);
        assert_eq!(tailer.chip_removals.len(), limit);
        assert_eq!(tailer.chip_removals.last().unwrap().chip_id, (limit + 24).to_string());
        assert_eq!(tailer.empty_devices.len(), limit);
        assert_eq!(tailer.empty_devices.last().unwrap().1, last_name);
        assert_eq!(tailer.events.len(), limit);
    }
}
