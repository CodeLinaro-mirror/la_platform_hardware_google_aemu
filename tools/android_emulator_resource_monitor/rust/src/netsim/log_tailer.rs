use chrono::{TimeZone, Utc};
use regex::Regex;
use serde_json::Value;
use std::collections::{BTreeMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::UNIX_EPOCH;

use super::*;

const SECS_PER_DAY: f64 = 86_400.0;
const GUID_PORT_WINDOW_S: f64 = 2.0;
const CHIP_MATCH_SLACK_S: f64 = 2.0;

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
    if ts - now_utc > SECS_PER_DAY {
        ts = Utc
            .with_ymd_and_hms(year - 1, month, day, hh, mm, ss)
            .single()
            .map_or(ts, |dt_prev| dt_prev.timestamp() as f64 + ms / 1000.0);
    }
    (level, Some(ts), msg)
}

#[derive(Debug, Clone)]
pub struct NetsimLogTailer {
    pub log_path: PathBuf,
    pub min_ts: Option<f64>,
    pub offset: u64,
    pub inode: Option<u64>,
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
    pub empty_devices: Vec<(f64, String)>,
    pub pending_guid_port: Option<(f64, u16)>,
    pub wifi_associations: Vec<f64>,
    pub last_timestamp: Option<f64>,
    pub truncation_count: u32,
    pub frozen: bool,
    pub require_self_owner: bool,
    pub expected_uid: Option<u32>,
    #[cfg(test)]
    pub owner_uid_override: Option<Option<u32>>,
    head_prefix: Vec<u8>,
    last_mtime: Option<std::time::SystemTime>,
    skipping_oversize_line: bool,
}

pub const MAX_TAILER_EVENTS: usize = 512;
pub const MAX_LOG_POLL_BYTES: usize = 16 * 1024 * 1024;
const MAX_LOG_LINE_BYTES: usize = 64 * 1024;
const HEAD_PREFIX_MAX_BYTES: usize = 128;

pub(super) fn push_bounded<T>(items: &mut Vec<T>, item: T) {
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
            min_ts: None,
            offset: 0,
            inode: None,
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
            empty_devices: Vec::new(),
            pending_guid_port: None,
            wifi_associations: Vec::new(),
            last_timestamp: None,
            truncation_count: 0,
            frozen: false,
            require_self_owner: false,
            expected_uid: None,
            #[cfg(test)]
            owner_uid_override: None,
            head_prefix: Vec::new(),
            last_mtime: None,
            skipping_oversize_line: false,
        }
    }

    fn effective_monitor_uid(&self) -> Option<u32> {
        #[cfg(test)]
        if let Some(override_uid) = self.owner_uid_override {
            return override_uid;
        }
        super::owner_uid()
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
        self.empty_devices.clear();
        self.pending_guid_port = None;
        self.wifi_associations.clear();
        self.last_timestamp = None;
        self.frozen = false;
        self.head_prefix.clear();
        self.last_mtime = None;
        self.skipping_oversize_line = false;
    }

    #[cfg(test)]
    pub fn poll(&mut self, now_utc: f64) -> bool {
        self.poll_with_truncation_policy(now_utc, false)
    }

    fn apply_truncation(
        &mut self,
        now_utc: f64,
        current_inode: Option<u64>,
        freeze_on_truncation: bool,
    ) -> bool {
        if freeze_on_truncation {
            self.frozen = true;
            return true;
        }
        self.offset = 0;
        self.truncation_count += 1;
        self.reset_state();
        self.inode = current_inode;
        push_bounded(&mut self.events, (now_utc, "LOG_TRUNCATED".to_string()));
        false
    }

    pub fn poll_with_truncation_policy(
        &mut self,
        now_utc: f64,
        freeze_on_truncation: bool,
    ) -> bool {
        use std::io::{Read, Seek, SeekFrom};
        if self.frozen {
            return true;
        }
        let monitor_uid = self.effective_monitor_uid();
        #[cfg(unix)]
        let eff_expected_uid = match self.expected_uid {
            Some(uid) => Some(uid),
            None if self.require_self_owner => match monitor_uid {
                Some(0) => None,
                Some(uid) => Some(uid),
                None => return false,
            },
            None => None,
        };
        #[cfg(not(unix))]
        let eff_expected_uid: Option<u32> = None;

        if self.log_path.parent().filter(|p| !p.as_os_str().is_empty()).is_some_and(|parent| {
            if eff_expected_uid.is_some() || self.require_self_owner {
                !super::is_trusted_owned_real_dir_with_monitor(
                    parent,
                    eff_expected_uid,
                    monitor_uid,
                    0,
                )
            } else {
                !super::is_real_dir(parent)
            }
        }) {
            return false;
        }
        let Ok(meta) = std::fs::symlink_metadata(&self.log_path) else {
            return false;
        };
        if !meta.file_type().is_file() {
            return false;
        }
        #[cfg(unix)]
        if let Some(uid) = eff_expected_uid {
            use std::os::unix::fs::MetadataExt;
            if meta.uid() != uid {
                return false;
            }
        }
        let st_size = meta.len();
        let cur_mtime = meta.modified().ok();

        #[cfg(unix)]
        let current_inode = {
            use std::os::unix::fs::MetadataExt;
            Some(meta.ino())
        };
        #[cfg(not(unix))]
        let current_inode = None;

        let meta_truncated = st_size < self.offset
            || matches!(
                (self.inode, current_inode),
                (Some(old_ino), Some(new_ino)) if old_ino != new_ino
            );
        if meta_truncated {
            if self.apply_truncation(now_utc, current_inode, freeze_on_truncation) {
                return true;
            }
        } else if self.inode.is_none() {
            self.inode = current_inode;
        }

        if st_size == 0
            || (st_size == self.offset && self.last_mtime.is_some() && self.last_mtime == cur_mtime)
        {
            self.last_mtime = cur_mtime;
            return true;
        }
        let Some((mut f, _)) = super::open_owned_regular_file(&self.log_path, eff_expected_uid)
        else {
            return false;
        };
        if self.offset > 0 && !self.head_prefix.is_empty() {
            let mut cur_head = vec![0u8; self.head_prefix.len()];
            let head_matches = f.read_exact(&mut cur_head).is_ok() && cur_head == self.head_prefix;
            if !head_matches && self.apply_truncation(now_utc, current_inode, freeze_on_truncation)
            {
                return true;
            }
        }
        if st_size == self.offset {
            self.last_mtime = cur_mtime;
            return true;
        }
        if f.seek(SeekFrom::Start(self.offset)).is_err() {
            return false;
        }
        let mut buf = Vec::new();
        if (&mut f).take(MAX_LOG_POLL_BYTES as u64).read_to_end(&mut buf).is_err() {
            return false;
        }
        if buf.is_empty() {
            self.last_mtime = cur_mtime;
            return true;
        }

        let mut slice: &[u8] = &buf;
        if self.skipping_oversize_line {
            if let Some(nl_pos) = slice.iter().position(|&c| c == b'\n') {
                let skip = nl_pos + 1;
                self.offset += skip as u64;
                self.skipping_oversize_line = false;
                slice = &slice[skip..];
                if slice.is_empty() {
                    if self.offset == st_size {
                        self.last_mtime = cur_mtime;
                    } else {
                        self.last_mtime = None;
                    }
                    return true;
                }
            } else {
                self.offset += slice.len() as u64;
                if self.offset == st_size {
                    self.last_mtime = cur_mtime;
                } else {
                    self.last_mtime = None;
                }
                return true;
            }
        }

        // Only a newline ends a line: str::lines() does not split on a bare CR.
        let Some(last_nl) = slice.iter().rposition(|&c| c == b'\n').map(|idx| idx + 1) else {
            if slice.len() >= MAX_LOG_POLL_BYTES {
                let prev_offset = self.offset;
                self.offset += slice.len() as u64;
                if prev_offset == self.head_prefix.len() as u64
                    && self.head_prefix.len() < HEAD_PREFIX_MAX_BYTES
                {
                    let need = HEAD_PREFIX_MAX_BYTES - self.head_prefix.len();
                    self.head_prefix.extend_from_slice(&slice[..slice.len().min(need)]);
                }
                self.skipping_oversize_line = true;
                if self.offset == st_size {
                    self.last_mtime = cur_mtime;
                } else {
                    self.last_mtime = None;
                }
            } else if self.offset != st_size {
                self.last_mtime = None;
            }
            return true;
        };

        let prev_offset = self.offset;
        self.offset += last_nl as u64;
        if prev_offset == self.head_prefix.len() as u64
            && self.head_prefix.len() < HEAD_PREFIX_MAX_BYTES
        {
            let need = HEAD_PREFIX_MAX_BYTES - self.head_prefix.len();
            self.head_prefix.extend_from_slice(&slice[..last_nl.min(need)]);
        }
        if self.offset == st_size {
            self.last_mtime = cur_mtime;
        } else {
            self.last_mtime = None;
        }
        let text = String::from_utf8_lossy(&slice[..last_nl]);
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
        static RE_EMPTY: OnceLock<Regex> = OnceLock::new();
        static RE_WIFI: OnceLock<Regex> = OnceLock::new();

        if raw_line.len() > MAX_LOG_LINE_BYTES {
            return;
        }
        let stripped = raw_line.trim();
        if stripped.is_empty() {
            return;
        }
        let (_level, ts, msg) = parse_netsim_log_timestamp(raw_line, now_utc);
        if let Some(t) = ts {
            self.last_timestamp = Some(t);
        }
        let eff_ts = ts.or(self.last_timestamp).unwrap_or(now_utc);
        if self.min_ts.is_some_and(|fl| eff_ts < fl) {
            return;
        }

        if (stripped == "no_cli_ui: true," || msg.contains("no_cli_ui: true"))
            && !self.frontend_disabled
        {
            self.frontend_disabled = true;
            push_bounded(&mut self.events, (eff_ts, "FRONTEND_DISABLED".to_string()));
        }
        if stripped == "pcap: true," || msg.contains("pcap: true") {
            self.pcap_flag = true;
        }

        let re_art = RE_ART.get_or_init(|| {
            Regex::new(r#"(?:netsim artifacts path|Artifacts):\s*"([^"]+)""#).unwrap()
        });
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
        if let Some(p) =
            re_port.captures(&msg).and_then(|c| c[1].parse::<u16>().ok()).filter(|&p| p > 0)
        {
            self.grpc_port_logged = Some(p);
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
            self.pending_guid_port =
                caps[1].parse::<u16>().ok().filter(|&p| p > 0).map(|p| (eff_ts, p));
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
                .filter(|(gts, _)| (eff_ts - gts).abs() <= GUID_PORT_WINDOW_S)
                .map(|(_, p)| p);
            push_bounded(
                &mut self.chip_adds,
                ChipAddEvent {
                    ts: eff_ts,
                    chip_id: Some(caps[1].to_string()),
                    kind: normalize_chip_kind(&caps[2]),
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
                .filter(|(gts, _)| (eff_ts - gts).abs() <= GUID_PORT_WINDOW_S)
                .map(|(_, p)| p);
            push_bounded(
                &mut self.chip_adds,
                ChipAddEvent {
                    ts: eff_ts,
                    chip_id: None,
                    kind: "?".to_string(),
                    device_name: caps[4].trim().to_string(),
                    peer_port,
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
        if re_wifi.is_match(&msg) {
            push_bounded(&mut self.wifi_associations, eff_ts);
        }
    }
}

/// Maximum size accepted for `netsim_session_stats.json`.
pub const MAX_SESSION_STATS_BYTES: usize = 4 * 1024 * 1024;

#[derive(Clone, Copy)]
pub(super) struct StatsOwnerPolicy {
    pub require_self_owner: bool,
    pub monitor_uid: Option<u32>,
    pub expected_uid: Option<u32>,
}

/// A stats file older than `not_before` (the daemon's start) was left by an
/// earlier daemon in the same artifact dir and is ignored.
pub(super) fn refresh_session_stats_cache_with_uid<'a>(
    stats_json_path: &Path,
    not_before: f64,
    cached: &'a mut Option<StatsCache>,
    owner_policy: StatsOwnerPolicy,
) -> Option<(f64, &'a Value)> {
    let StatsOwnerPolicy { require_self_owner, monitor_uid, expected_uid } = owner_policy;
    if stats_json_path.to_string_lossy().ends_with(".tmp") {
        return None;
    }
    #[cfg(unix)]
    let eff_expected_uid = match expected_uid {
        Some(uid) => Some(uid),
        None if require_self_owner => match monitor_uid {
            Some(0) => None,
            Some(uid) => Some(uid),
            None => return None,
        },
        None => None,
    };
    #[cfg(not(unix))]
    let eff_expected_uid: Option<u32> = None;

    if stats_json_path.parent().filter(|p| !p.as_os_str().is_empty()).is_some_and(|parent| {
        if eff_expected_uid.is_some() || require_self_owner {
            !super::is_trusted_owned_real_dir_with_monitor(parent, eff_expected_uid, monitor_uid, 0)
        } else {
            !super::is_real_dir(parent)
        }
    }) {
        return None;
    }
    let meta = std::fs::symlink_metadata(stats_json_path).ok()?;
    if !meta.file_type().is_file() {
        return None;
    }
    #[cfg(unix)]
    if let Some(uid) = eff_expected_uid {
        use std::os::unix::fs::MetadataExt;
        if meta.uid() != uid {
            return None;
        }
    }
    let stats_mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    if stats_mtime < not_before {
        return None;
    }

    // Re-parse only when the file changed; callers still re-run timestamp matching.
    let cache_miss = cached.as_ref().is_none_or(|c| (c.mtime - stats_mtime).abs() >= 1e-6);
    if cache_miss {
        let text = super::read_owned_regular_file_bounded(
            stats_json_path,
            MAX_SESSION_STATS_BYTES,
            eff_expected_uid,
        )?;
        let parsed = serde_json::from_str::<Value>(&text).ok()?;
        *cached = Some(StatsCache { mtime: stats_mtime, parsed });
    }
    cached.as_ref().map(|c| (stats_mtime, &c.parsed))
}

pub(super) fn refresh_session_stats_cache_for_owner<'a>(
    stats_json_path: &Path,
    not_before: f64,
    cached: &'a mut Option<StatsCache>,
    require_self_owner: bool,
    expected_uid: Option<u32>,
) -> Option<(f64, &'a Value)> {
    refresh_session_stats_cache_with_uid(
        stats_json_path,
        not_before,
        cached,
        StatsOwnerPolicy { require_self_owner, monitor_uid: super::owner_uid(), expected_uid },
    )
}

#[cfg(test)]
pub fn resolve_session_stats_chips(
    stats_json_path: &Path,
    unknown_add_timestamps: &[f64],
    joined_emulator_count: usize,
    not_before: f64,
    cached: &mut Option<StatsCache>,
) -> (usize, Option<ResolvedChipMap>) {
    resolve_session_stats_chips_for_owner(
        stats_json_path,
        unknown_add_timestamps,
        joined_emulator_count,
        not_before,
        cached,
        false,
        None,
    )
}

pub(super) fn resolve_session_stats_chips_for_owner(
    stats_json_path: &Path,
    unknown_add_timestamps: &[f64],
    joined_emulator_count: usize,
    not_before: f64,
    cached: &mut Option<StatsCache>,
    require_self_owner: bool,
    expected_uid: Option<u32>,
) -> (usize, Option<ResolvedChipMap>) {
    resolve_session_stats_chips_with_uid(
        stats_json_path,
        unknown_add_timestamps,
        joined_emulator_count,
        not_before,
        cached,
        StatsOwnerPolicy { require_self_owner, monitor_uid: super::owner_uid(), expected_uid },
    )
}

fn resolve_session_stats_chips_with_uid(
    stats_json_path: &Path,
    unknown_add_timestamps: &[f64],
    joined_emulator_count: usize,
    not_before: f64,
    cached: &mut Option<StatsCache>,
    owner_policy: StatsOwnerPolicy,
) -> (usize, Option<ResolvedChipMap>) {
    let Some((stats_mtime, val)) =
        refresh_session_stats_cache_with_uid(stats_json_path, not_before, cached, owner_policy)
    else {
        return (0, None);
    };

    let device_stats =
        val.get("device_stats").and_then(Value::as_array).map_or(&[][..], Vec::as_slice);
    let radio_stats =
        val.get("radio_stats").and_then(Value::as_array).map_or(&[][..], Vec::as_slice);
    let num_devices = device_stats.len();

    if num_devices != 1 || joined_emulator_count != 1 {
        return (num_devices, None);
    }

    let dev_id = device_stats[0].get("device_id");
    let mut kind_durations: BTreeMap<String, i64> = BTreeMap::new();
    for r in radio_stats {
        if dev_id
            .zip(r.get("device_id"))
            .is_some_and(|(target_id, rid)| !rid.is_null() && rid != target_id)
        {
            continue;
        }
        let Some(raw_kind) = r.get("kind").and_then(|v| v.as_str()) else {
            continue;
        };
        let norm_k = normalize_chip_kind(raw_kind);
        if matches!(norm_k.as_str(), "" | "?" | "UNSPECIFIED") {
            continue;
        }
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
            if (ev_ts - est_k).abs() <= CHIP_MATCH_SLACK_S {
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

    (num_devices, Some(resolved))
}

#[cfg(test)]
#[path = "../../tests/netsim/log_tailer_test.rs"]
mod tests;
