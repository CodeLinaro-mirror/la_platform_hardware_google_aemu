use regex::Regex;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{Duration, UNIX_EPOCH};

use super::*;

const CLI_TIMEOUT: Duration = Duration::from_secs(5);
const BYTES_PER_MIB: u64 = 1024 * 1024;
const HARVEST_INTERVAL_S: f64 = 10.0;

pub type CliRunnerFn =
    dyn Fn(&NetsimCliTarget, &[String]) -> Option<String> + Send + Sync + 'static;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetsimCliTarget {
    pub netsim_bin: String,
    pub grpc_port: u16,
    pub ini_dir: Option<PathBuf>,
    pub instance_num: Option<u32>,
}

pub fn build_netsim_cli_args(
    grpc_port: u16,
    instance_num: Option<u32>,
    args: &[String],
) -> Vec<String> {
    let mut full_args = Vec::with_capacity(5 + args.len());
    full_args.push("-v".to_string());
    if let Some(inst) = instance_num.filter(|&i| i > DEFAULT_NETSIM_INSTANCE) {
        full_args.push("-i".to_string());
        full_args.push(inst.to_string());
    }
    full_args.push("-p".to_string());
    full_args.push(grpc_port.to_string());
    full_args.extend(args.iter().cloned());
    full_args
}

/// Runs the `netsim` CLI against one daemon.
///
/// `netsim capture ...` finds its daemon through `<TMPDIR>/netsim[_N].ini` and ignores `-p`,
/// so without a verified INI directory the call could reach another daemon and is skipped.
/// With one, `TMPDIR` and `NETSIM_INSTANCE` are both set, because an ambient
/// `NETSIM_INSTANCE` takes precedence over `-i`.
fn default_cli_runner(target: &NetsimCliTarget, args: &[String]) -> Option<String> {
    let mut env_vars: Vec<(&str, &std::ffi::OsStr)> = Vec::new();
    let inst_str =
        target.instance_num.filter(|&i| i > 0).unwrap_or(DEFAULT_NETSIM_INSTANCE).to_string();
    if args.first().map(String::as_str) == Some("capture")
        && (args.get(1).map(String::as_str) == Some("get") || target.ini_dir.is_none())
    {
        return None;
    }
    if let Some(ref dir) = target.ini_dir {
        env_vars.push(("TMPDIR", dir.as_os_str()));
        env_vars.push(("NETSIM_INSTANCE", std::ffi::OsStr::new(&inst_str)));
    }
    let bin = if target.netsim_bin.is_empty() { "netsim" } else { &target.netsim_bin };
    let full_args = build_netsim_cli_args(target.grpc_port, target.instance_num, args);
    let arg_refs: Vec<&str> = full_args.iter().map(String::as_str).collect();
    let (stdout, stderr) =
        run_cli_output_with_timeout_and_env(bin, &arg_refs, &env_vars, CLI_TIMEOUT)?;
    if stderr.is_empty() { Some(stdout) } else { Some(format!("{}\n{}", stdout, stderr)) }
}

#[derive(Clone)]
pub struct EnabledChipRecord {
    pub target: NetsimCliTarget,
    pub ini_path: Option<PathBuf>,
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
    pub enabled_by_monitor: HashMap<EnabledChipKey, EnabledChipRecord>,
    pub checked_emulators: HashSet<(DaemonKey, String)>,
    pub checked_chip_counts: HashMap<(DaemonKey, String, String), (usize, BTreeSet<String>)>,
    pub cli_error_counts: HashMap<(DaemonKey, String), u8>,
    pub skipped_notes: Vec<String>,
    pub last_harvest_by_daemon: HashMap<DaemonKey, f64>,
    pub cap_reached: bool,
    pub cli_runner: std::sync::Arc<CliRunnerFn>,
}

fn expand_tilde_path_with(path: &Path, home: Option<&Path>) -> PathBuf {
    match path.to_str() {
        Some(raw) if !raw.is_empty() => crate::summary::expand_tilde_with(raw, home),
        _ => path.to_path_buf(),
    }
}

impl NetsimPcapManager {
    pub fn new(pcap_dir_opt: Option<&str>, max_mb: u64, out_dir: &Path) -> Self {
        let home = crate::summary::resolve_home_dir();
        let (enabled, pcap_dir) = match pcap_dir_opt {
            None => (false, None),
            Some("") => {
                (true, Some(expand_tilde_path_with(out_dir, home.as_deref()).join("netsim_pcaps")))
            }
            Some(p) => (true, Some(expand_tilde_path_with(Path::new(p), home.as_deref()))),
        };
        Self {
            enabled,
            pcap_dir,
            max_bytes: max_mb.max(1).saturating_mul(BYTES_PER_MIB),
            total_copied_bytes: 0,
            copied_files: BTreeMap::new(),
            rescued_zips: HashSet::new(),
            enabled_by_monitor: HashMap::new(),
            checked_emulators: HashSet::new(),
            checked_chip_counts: HashMap::new(),
            cli_error_counts: HashMap::new(),
            skipped_notes: Vec::new(),
            last_harvest_by_daemon: HashMap::new(),
            cap_reached: false,
            cli_runner: std::sync::Arc::new(default_cli_runner),
        }
    }

    /// Runs the CLI for `target`, or returns `None` without running it.
    ///
    /// A sibling of the daemon's executable was vetted when the daemon was first seen. It is
    /// vetted again before every run: below a sticky directory such as `/tmp`, its directory
    /// can be removed by its owner and recreated by anyone.
    pub fn run_cli(&self, target: &NetsimCliTarget, args: &[String]) -> Option<String> {
        let bin = Path::new(&target.netsim_bin);
        if bin.parent().is_some_and(|dir| !dir.as_os_str().is_empty())
            && !is_trusted_executable(bin)
        {
            return None;
        }
        (self.cli_runner)(target, args)
    }

    fn is_manageable_emulator(emu: &EmulatorNetsimRecord) -> bool {
        !emu.is_settled()
            && !emu.no_netsim
            && emu.state != "DISABLED"
            && emu.device_name.as_deref().is_some_and(|dn| !dn.is_empty())
    }

    fn emulator_chip_signature(emu: &EmulatorNetsimRecord) -> (usize, BTreeSet<String>) {
        (emu.chip_attach_s.len() + emu.unknown_chip_count, emu.chip_ids.clone())
    }

    fn sanitize_cli_error(raw: &str) -> String {
        static RE_LOG_PREFIX: OnceLock<Regex> = OnceLock::new();
        let re = RE_LOG_PREFIX.get_or_init(|| {
            Regex::new(
                r"^netsim\s+[EWIDTV]\s+\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+\S+\s+-\s*",
            )
            .unwrap()
        });
        let parts: Vec<String> = raw
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty())
            .map(|l| re.replace(l, "").trim().to_string())
            .filter(|l| !l.is_empty())
            .collect();
        parts.join("; ")
    }

    fn cli_target_for_daemon(daemon: &DaemonRecord, port: u16) -> NetsimCliTarget {
        let ini_dir = daemon
            .ini_path
            .as_deref()
            .and_then(Path::parent)
            .filter(|p| !p.as_os_str().is_empty())
            .map(Path::to_path_buf);
        let instance_num =
            daemon.ini_path.as_deref().and_then(extract_ini_instance_num).or(daemon.instance_num);
        NetsimCliTarget {
            netsim_bin: daemon.netsim_cli_bin.clone(),
            grpc_port: port,
            ini_dir,
            instance_num,
        }
    }

    /// Drops the per-daemon throttle, checked-device, and enabled-chip state of an exited daemon.
    pub fn forget_daemon(&mut self, d_key: DaemonKey) {
        self.last_harvest_by_daemon.remove(&d_key);
        self.checked_emulators.retain(|(dk, _)| *dk != d_key);
        self.checked_chip_counts.retain(|(dk, _, _), _| *dk != d_key);
        self.cli_error_counts.retain(|(dk, _), _| *dk != d_key);
        self.enabled_by_monitor.retain(|(dk, _), _| *dk != d_key);
    }

    fn record_skipped_note(&mut self, note: String) {
        if !self.skipped_notes.contains(&note) {
            self.skipped_notes.push(note);
        }
    }

    fn note_cli_failure(&mut self, d_key: DaemonKey, joined_emulators: &[&EmulatorNetsimRecord]) {
        for emu in joined_emulators.iter().copied().filter(|e| Self::is_manageable_emulator(e)) {
            if let Some(dn) = emu.device_name.as_deref() {
                let entry = self.cli_error_counts.entry((d_key, dn.to_string())).or_insert(0);
                *entry = entry.saturating_add(1);
            }
        }
    }

    pub fn poll_and_manage_daemon(
        &mut self,
        daemon: &mut DaemonRecord,
        joined_emulators: &[&EmulatorNetsimRecord],
        now_mono: f64,
        shutting_down: bool,
    ) {
        let d_key = daemon.daemon_key;
        let has_manageable_emu = joined_emulators.iter().copied().any(Self::is_manageable_emulator);
        let expected_uid =
            daemon.expected_uid.or_else(|| daemon.same_user.then(owner_uid).flatten());
        let ini_dir_trusted = daemon.ini_path.as_deref().is_some_and(is_trusted_ini_path);
        let art_dir_trusted = match std::fs::symlink_metadata(&daemon.artifact_dir) {
            Ok(_) => is_trusted_owned_real_dir(&daemon.artifact_dir, expected_uid),
            Err(e) => {
                e.kind() == std::io::ErrorKind::NotFound
                    && has_trusted_existing_ancestors(&daemon.artifact_dir, expected_uid, 0)
            }
        };
        let can_manage_cli = self.enabled
            && !daemon.exited
            && daemon.same_user
            && ini_dir_trusted
            && art_dir_trusted
            && has_manageable_emu;
        let has_unmanaged_emu = can_manage_cli
            && joined_emulators
                .iter()
                .copied()
                .filter(|emu| Self::is_manageable_emulator(emu))
                .any(|emu| {
                    emu.device_name.as_deref().is_some_and(|dn| {
                        let key = (d_key, dn.to_string());
                        let chip_key = (d_key, emu.serial.clone(), dn.to_string());
                        let sig = Self::emulator_chip_signature(emu);
                        let already_checked = self.checked_emulators.contains(&key)
                            && self.checked_chip_counts.get(&chip_key) == Some(&sig);
                        !already_checked
                            && self.cli_error_counts.get(&key).copied().unwrap_or(0) < 2
                    })
                });
        let last_harvest = self.last_harvest_by_daemon.get(&d_key).copied().unwrap_or(0.0);
        if last_harvest > 0.0
            && (now_mono - last_harvest) < HARVEST_INTERVAL_S
            && !shutting_down
            && !has_unmanaged_emu
        {
            return;
        }
        self.last_harvest_by_daemon.insert(d_key, now_mono);

        let mut rows = Vec::new();
        let target = daemon
            .grpc_port
            .filter(|_| can_manage_cli)
            .map(|port| Self::cli_target_for_daemon(daemon, port));
        if let Some(ref cli_target) = target {
            let args = vec!["capture".to_string(), "list".to_string()];
            match self.run_cli(cli_target, &args) {
                Some(out) => match parse_netsim_capture_list(&out) {
                    Ok(parsed) => {
                        for emu in joined_emulators
                            .iter()
                            .copied()
                            .filter(|e| Self::is_manageable_emulator(e))
                        {
                            if let Some(dn) = emu.device_name.as_deref() {
                                let key = (d_key, dn.to_string());
                                let chip_key = (d_key, emu.serial.clone(), dn.to_string());
                                self.checked_emulators.insert(key.clone());
                                self.checked_chip_counts
                                    .insert(chip_key, Self::emulator_chip_signature(emu));
                                self.cli_error_counts.remove(&key);
                            }
                        }
                        rows = parsed;
                    }
                    Err(err) => {
                        self.note_cli_failure(d_key, joined_emulators);
                        self.record_skipped_note(format!(
                            "Skipped capture list for port {}: {}",
                            cli_target.grpc_port,
                            Self::sanitize_cli_error(&err)
                        ));
                    }
                },
                None => {
                    self.note_cli_failure(d_key, joined_emulators);
                    self.record_skipped_note(format!(
                        "Skipped capture list for port {}: netsim CLI unavailable or timed out",
                        cli_target.grpc_port
                    ));
                }
            }
        }

        if let Some(ref cli_target) = target.filter(|_| !rows.is_empty()) {
            self.auto_enable_joined_captures(
                d_key,
                cli_target,
                daemon.ini_path.as_deref(),
                &rows,
                joined_emulators,
            );
        }

        self.harvest_daemon_files(daemon, joined_emulators);
    }

    fn auto_enable_joined_captures(
        &mut self,
        d_key: DaemonKey,
        target: &NetsimCliTarget,
        ini_path: Option<&Path>,
        rows: &[CaptureRow],
        joined_emulators: &[&EmulatorNetsimRecord],
    ) {
        for emu in joined_emulators.iter().copied().filter(|emu| Self::is_manageable_emulator(emu))
        {
            let Some(ref dev_name) = emu.device_name else {
                continue;
            };
            for r in rows.iter().filter(|r| &r.device_name == dev_name && r.state == "off") {
                let Some(pats) = select_safe_capture_patterns(rows, dev_name, Some(r)) else {
                    self.record_skipped_note(format!(
                        "Skipped auto-enable for '{}' (chip {}) due to ambiguous CLI substring match",
                        dev_name, r.id
                    ));
                    continue;
                };
                let mut cmd_args =
                    vec!["capture".to_string(), "patch".to_string(), "on".to_string()];
                cmd_args.extend(pats);
                match self.run_cli(target, &cmd_args) {
                    Some(out) => match parse_netsim_capture_list(&out) {
                        Ok(_) => {
                            self.enabled_by_monitor.insert(
                                (d_key, r.id),
                                EnabledChipRecord {
                                    target: target.clone(),
                                    ini_path: ini_path.map(Path::to_path_buf),
                                    device_name: dev_name.clone(),
                                    row: r.clone(),
                                },
                            );
                        }
                        Err(err) => {
                            self.record_skipped_note(format!(
                                "Skipped auto-enable for '{}' (chip {}): {}",
                                dev_name,
                                r.id,
                                Self::sanitize_cli_error(&err)
                            ));
                        }
                    },
                    None => {
                        self.record_skipped_note(format!(
                            "Skipped auto-enable for '{}' (chip {}): netsim CLI unavailable or timed out",
                            dev_name, r.id
                        ));
                    }
                }
            }
        }
    }

    pub fn harvest_daemon_files(
        &mut self,
        daemon: &mut DaemonRecord,
        joined_emulators: &[&EmulatorNetsimRecord],
    ) {
        let art_dir = &daemon.artifact_dir;
        if art_dir.as_os_str().is_empty()
            || !std::fs::symlink_metadata(art_dir).is_ok_and(|m| m.file_type().is_dir())
        {
            return;
        }
        let pcaps_dir = art_dir.join("pcaps");
        let pcaps_is_real_dir =
            std::fs::symlink_metadata(&pcaps_dir).is_ok_and(|m| m.file_type().is_dir());
        let mut live_bytes = 0u64;
        let mut live_files = 0usize;
        if pcaps_is_real_dir {
            if let Ok(entries) = std::fs::read_dir(&pcaps_dir) {
                for entry in entries.flatten() {
                    if entry.path().extension().and_then(|s| s.to_str()) != Some("pcap") {
                        continue;
                    }
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
        // it only when the daemon and the directory (including ancestors) belong
        // to the expected user, and never through symlinks (`copy_owned_regular_file`).
        let expected_uid =
            daemon.expected_uid.or_else(|| daemon.same_user.then(owner_uid).flatten());
        if !daemon.same_user || !is_trusted_owned_real_dir(art_dir, expected_uid) {
            return;
        }

        let daemon_subdir =
            base_pcap_dir.join(format!("{}-{}", daemon.pid, daemon.start_wall as u64));

        self.rescue_artifact_zips(art_dir, &daemon_subdir, expected_uid);

        if !pcaps_is_real_dir || !is_trusted_owned_real_dir(&pcaps_dir, expected_uid) {
            return;
        }

        self.harvest_pcap_files(&pcaps_dir, &daemon_subdir, joined_emulators, expected_uid);
    }

    fn rescue_artifact_zips(
        &mut self,
        art_dir: &Path,
        daemon_subdir: &Path,
        expected_uid: Option<u32>,
    ) {
        let Ok(entries) = std::fs::read_dir(art_dir) else {
            return;
        };
        for entry in entries.flatten() {
            let fname = entry.file_name().to_string_lossy().to_string();
            if !fname.starts_with("netsim_artifacts_") || !fname.ends_with(".zip") {
                continue;
            }
            let zpath = entry.path();
            let dst = daemon_subdir.join(&fname);
            if self.rescued_zips.contains(&dst) {
                continue;
            }
            let Ok(m) = std::fs::symlink_metadata(&zpath) else {
                continue;
            };
            if !m.file_type().is_file() {
                continue;
            }
            if self.total_copied_bytes + m.len() > self.max_bytes {
                self.cap_reached = true;
                continue;
            }
            if std::fs::create_dir_all(daemon_subdir).is_err() {
                continue;
            }
            if let Some(copied) = copy_owned_regular_file(&zpath, &dst, expected_uid) {
                self.rescued_zips.insert(dst.clone());
                self.total_copied_bytes += copied;
                self.copied_files.insert(dst.to_string_lossy().to_string(), (copied, 0));
            }
        }
    }

    fn harvest_pcap_files(
        &mut self,
        pcaps_dir: &Path,
        daemon_subdir: &Path,
        joined_emulators: &[&EmulatorNetsimRecord],
        expected_uid: Option<u32>,
    ) {
        let mut chip_to_serial = HashMap::new();
        let mut name_to_serial = HashMap::new();
        for emu in joined_emulators {
            let serial = emu.serial.clone();
            if let Some(ref dn) = emu.device_name {
                // Two emulators with one device name cannot be told apart by file name.
                name_to_serial
                    .entry(sanitize_netsim_filename(dn))
                    .and_modify(|s: &mut String| {
                        if *s != serial {
                            *s = "shared".to_string();
                        }
                    })
                    .or_insert_with(|| serial.clone());
            }
            for cid in &emu.chip_ids {
                chip_to_serial
                    .entry(cid.clone())
                    .and_modify(|s: &mut String| {
                        if *s != serial {
                            *s = "shared".to_string();
                        }
                    })
                    .or_insert_with(|| serial.clone());
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

        let Ok(entries) = std::fs::read_dir(pcaps_dir) else {
            return;
        };
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
            let mtime_ns = m
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| u64::try_from(d.as_nanos()).unwrap_or(u64::MAX))
                .unwrap_or(0);
            if self
                .copied_files
                .get(&dst_key)
                .is_some_and(|&(prev_sz, prev_mt)| prev_sz == m.len() && prev_mt == mtime_ns)
            {
                continue;
            }
            // A re-copy replaces the earlier copy, which can be larger after
            // the capture restarted.
            let prev_sz = self.copied_files.get(&dst_key).map(|&(s, _)| s).unwrap_or(0);
            let base = self.total_copied_bytes.saturating_sub(prev_sz);
            if base + m.len() > self.max_bytes {
                self.cap_reached = true;
                let note = format!(
                    "Reached --netsim-pcap-max-mb cap ({} MB); skipped {}",
                    self.max_bytes / BYTES_PER_MIB,
                    fname
                );
                if !self.skipped_notes.contains(&note) {
                    self.skipped_notes.push(note);
                }
                continue;
            }
            if std::fs::create_dir_all(&dst_dir).is_err() {
                continue;
            }
            if let Some(copied) = copy_owned_regular_file(&src, &dst, expected_uid) {
                self.copied_files.insert(dst_key, (copied, mtime_ns));
                self.total_copied_bytes = base + copied;
            }
        }
    }

    pub fn cleanup(
        &mut self,
        daemons: &mut BTreeMap<DaemonKey, DaemonRecord>,
        emulator_records: &BTreeMap<String, EmulatorNetsimRecord>,
    ) {
        let mut enabled_list: Vec<(EnabledChipKey, EnabledChipRecord)> =
            self.enabled_by_monitor.iter().map(|(k, v)| (*k, v.clone())).collect();
        enabled_list.sort_by_key(|(k, _)| *k);
        let mut enabled_ids_by_daemon: HashMap<DaemonKey, HashSet<u32>> = HashMap::new();
        for ((dk, cid), _) in &enabled_list {
            enabled_ids_by_daemon.entry(*dk).or_default().insert(*cid);
        }
        let empty_ids = HashSet::new();
        let list_args = vec!["capture".to_string(), "list".to_string()];
        let mut rows_by_daemon: HashMap<DaemonKey, Option<Vec<CaptureRow>>> = HashMap::new();
        for ((d_key, chip_id), info) in enabled_list {
            if daemons.get(&d_key).is_some_and(|d| d.exited) {
                continue;
            }
            if !info.ini_path.as_deref().is_some_and(is_trusted_ini_path) {
                self.record_skipped_note(format!(
                    "Left capture on for '{}' (chip {}): netsim INI is missing or no longer trusted",
                    info.device_name, chip_id
                ));
                continue;
            }
            let maybe_rows = rows_by_daemon.entry(d_key).or_insert_with(|| {
                let out = self.run_cli(&info.target, &list_args)?;
                parse_netsim_capture_list(&out).ok()
            });
            let Some(ref rows) = *maybe_rows else {
                continue;
            };
            let enabled_ids = enabled_ids_by_daemon.get(&d_key).unwrap_or(&empty_ids);
            if let Some(pats) =
                select_safe_disable_patterns(rows, &info.device_name, &info.row, enabled_ids)
            {
                let mut off_args =
                    vec!["capture".to_string(), "patch".to_string(), "off".to_string()];
                off_args.extend(pats);
                let _ = self.run_cli(&info.target, &off_args);
            }
        }
        for d in daemons.values_mut() {
            if d.exited || d.client_mode_recorded {
                continue;
            }
            let joined: Vec<&EmulatorNetsimRecord> =
                emulator_records.values().filter(|e| e.daemon_key == Some(d.daemon_key)).collect();
            self.harvest_daemon_files(d, &joined);
        }
    }
}

#[cfg(test)]
#[path = "../../tests/netsim/pcap_test.rs"]
mod tests;
