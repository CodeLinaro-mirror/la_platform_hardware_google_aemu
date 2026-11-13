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
mod tests {
    use super::*;

    /// A daemon record for harvest tests: the artifact dir holds one real pcap,
    /// one real zip and (on Unix) symlinked look-alikes pointing at a secret file.
    fn harvest_fixture(tmp: &Path, same_user: bool) -> (DaemonRecord, PathBuf, PathBuf) {
        let art_dir = tmp.join("art");
        let pcaps = art_dir.join("pcaps");
        std::fs::create_dir_all(&pcaps).unwrap();
        let secret = tmp.join("secret.txt");
        std::fs::write(&secret, b"SECRET-DATA").unwrap();
        std::fs::write(pcaps.join("netsim-1-Pixel_8-WIFI.pcap"), [0xd4u8; 20]).unwrap();
        std::fs::write(art_dir.join("netsim_artifacts_1.zip"), b"PK\x03\x04zip").unwrap();
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&secret, pcaps.join("netsim-2-Evil-BT.pcap")).unwrap();
            std::os::unix::fs::symlink(&secret, art_dir.join("netsim_artifacts_2.zip")).unwrap();
        }
        let ini_path = same_user.then(|| {
            let ini = tmp.join("netsim.ini");
            std::fs::write(&ini, "grpc.port=43929\npid=5001\n").unwrap();
            ini
        });
        let daemon = DaemonRecord {
            daemon_key: (5001, 100),
            pid: 5001,
            create_time: 100,
            start_wall: 100.0,
            spawner: "emulator".to_string(),
            instance_num: None,
            artifact_dir: art_dir,
            log_path: PathBuf::new(),
            tailer: None,
            netsim_cli_bin: "netsim".to_string(),
            same_user,
            expected_uid: same_user.then(owner_uid).flatten(),
            version: None,
            ini_path,
            grpc_port: None,
            ready_ts: None,
            ready_recorded: false,
            netsim_daemon_ready_s: None,
            frontend_disabled: false,
            pcap_flag: false,
            log_available: false,
            log_unavail_recorded: false,
            client_mode_recorded: false,
            exited: false,
            exit_reason_hint: None,
            exit_reason: None,
            orphaned: false,
            zero_devices_since_mono: None,
            stats_cache: None,
            pcap_live_bytes: 0,
            pcap_live_files: 0,
            active_socket_conns: 0,
            joined_emulator_count: 0,
        };
        (daemon, tmp.join("out"), secret)
    }

    #[cfg(unix)]
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

    /// Writes an executable script and returns once it can be run.
    #[cfg(unix)]
    fn write_script(path: &Path, body: &str) {
        use std::os::unix::fs::PermissionsExt;

        std::fs::write(path, body).unwrap();
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
        crate::test_support::retry_while_text_busy(|| std::process::Command::new(path).output())
            .unwrap();
    }

    #[test]
    #[cfg(unix)]
    fn test_harvest_copies_same_named_zip_from_restarted_daemon() {
        // A restarted daemon reuses netsim_artifacts_1.zip; the new file belongs
        // to the new daemon and is harvested into its directory too.
        let tmp = tempfile::tempdir().unwrap();
        let (mut first, out, _) = harvest_fixture(tmp.path(), true);
        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        mgr.harvest_daemon_files(&mut first, &[]);
        std::fs::write(first.artifact_dir.join("netsim_artifacts_1.zip"), b"PK\x03\x04new")
            .unwrap();
        let mut second = DaemonRecord {
            daemon_key: (5002, 200),
            pid: 5002,
            create_time: 200,
            start_wall: 200.0,
            ..first.clone()
        };
        mgr.harvest_daemon_files(&mut second, &[]);
        let zips = copied_names(&out).iter().filter(|n| *n == "netsim_artifacts_1.zip").count();
        assert_eq!(zips, 2);
    }

    #[test]
    #[cfg(unix)]
    fn test_netsim_tracker_cleanup_runs_once() {
        let tmp = tempfile::tempdir().unwrap();
        let (daemon, out, _) = harvest_fixture(tmp.path(), true);
        let pcaps = daemon.artifact_dir.join("pcaps");
        let mut tracker = NetsimTracker::new(Some(""), 500, false, &out);
        tracker.custom_ini_dirs = vec![out.clone()];
        tracker.daemons.insert(daemon.daemon_key, daemon);
        tracker.cleanup();
        assert_eq!(copied_names(&out).len(), 2);

        // A pcap that shows up later is not harvested by a second cleanup or by Drop.
        std::fs::write(pcaps.join("netsim-3-Late-WIFI.pcap"), [0xd4u8; 20]).unwrap();
        tracker.cleanup();
        drop(tracker);
        assert_eq!(copied_names(&out).len(), 2);
    }

    #[test]
    #[cfg(unix)]
    fn test_netsim_tracker_cleanup_without_pcap_collection_copies_nothing() {
        let tmp = tempfile::tempdir().unwrap();
        let (daemon, out, _) = harvest_fixture(tmp.path(), true);
        let mut tracker = NetsimTracker::new(None, 500, false, &out);
        tracker.custom_ini_dirs = vec![out.clone()];
        tracker.daemons.insert(daemon.daemon_key, daemon);
        tracker.cleanup();
        drop(tracker);
        assert!(copied_names(&out).is_empty());
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
        // The live counters still describe what is on disk: the planted link counts as
        // itself, not as the file it points at.
        assert_eq!(daemon.pcap_live_files, 2);
        let link = daemon.artifact_dir.join("pcaps").join("netsim-2-Evil-BT.pcap");
        assert_eq!(daemon.pcap_live_bytes, 20 + std::fs::symlink_metadata(link).unwrap().len());
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
        mgr.cli_runner = std::sync::Arc::new(move |_target, args| {
            assert!(
                !(args.len() >= 2 && args[0] == "capture" && args[1] == "get"),
                "capture get is forbidden"
            );
            calls_clone.lock().unwrap().push(args.to_vec());
            Some(list_str.clone())
        });

        let ini = dir.path().join("netsim.ini");
        std::fs::write(&ini, "grpc.port=43929\npid=5000\n").unwrap();
        let mut daemon = DaemonRecord {
            daemon_key: (5000, 100),
            pid: 5000,
            create_time: 100,
            start_wall: 100.0,
            spawner: "emulator".to_string(),
            instance_num: None,
            artifact_dir: art_dir,
            log_path: PathBuf::new(),
            tailer: None,
            netsim_cli_bin: "netsim".to_string(),
            same_user: true,
            expected_uid: owner_uid(),
            version: Some("1.0.24".to_string()),
            ini_path: Some(ini),
            grpc_port: Some(43929),
            ready_ts: Some(100.1),
            ready_recorded: true,
            netsim_daemon_ready_s: Some(0.1),
            frontend_disabled: false,
            pcap_flag: false,
            log_available: true,
            log_unavail_recorded: false,
            client_mode_recorded: false,
            exited: false,
            exit_reason_hint: None,
            exit_reason: None,
            orphaned: false,
            zero_devices_since_mono: None,
            stats_cache: None,
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
                worker_create_time: 0,
                group_pids: Vec::new(),
                group_procs: BTreeMap::new(),
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
                prior_bare_snapshot: None,
            },
            EmulatorNetsimRecord {
                storage_key: "ERM_emulator-5556".to_string(),
                serial: "emulator-5556".to_string(),
                avd_id: Some("erm_qn_phone37".to_string()),
                device_name: Some("ERM QN Phone 37".to_string()),
                worker_pid: 2,
                worker_create_time: 0,
                group_pids: Vec::new(),
                group_procs: BTreeMap::new(),
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
                prior_bare_snapshot: None,
            },
        ];

        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 20.0, false);
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
    #[cfg(unix)]
    fn test_pcap_manager_throttles_per_daemon_and_after_ambiguous_skip() {
        let tmp_a = tempfile::tempdir().unwrap();
        let tmp_b = tempfile::tempdir().unwrap();
        let (mut first, out, _) = harvest_fixture(tmp_a.path(), true);
        let (mut second, _, _) = harvest_fixture(tmp_b.path(), true);
        second.daemon_key = (5002, 200);
        second.pid = 5002;
        second.create_time = 200;
        second.start_wall = 200.0;

        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
        let calls_clone = calls.clone();
        let list_out = "\
ID | Device Name | Chip Kind | State | Timestamp | Records | Size (bytes)
---|-------------|-----------|-------|-----------|---------|-------------
1  | Pixel 8     | WIFI      | off   | 22:37:06  | 0       | 0
10 | Pixel 8 Pro | WIFI      | off   | 22:37:06  | 0       | 0
";
        mgr.cli_runner = std::sync::Arc::new(move |_target, _args| {
            *calls_clone.lock().unwrap() += 1;
            Some(list_out.to_string())
        });
        first.grpc_port = Some(43929);
        second.grpc_port = Some(43930);

        let joined_a = [EmulatorNetsimRecord {
            storage_key: "Pixel_8_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("Pixel 8".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(first.daemon_key),
            ..Default::default()
        }];

        // Both daemons are polled on the same tick (now_mono = 20.0): neither
        // daemon may starve the other.
        let joined_a_refs: Vec<&EmulatorNetsimRecord> = joined_a.iter().collect();
        mgr.poll_and_manage_daemon(&mut first, &joined_a_refs, 20.0, false);
        mgr.poll_and_manage_daemon(&mut second, &[], 20.0, false);
        assert!(first.pcap_live_bytes > 0);
        assert!(second.pcap_live_bytes > 0);

        // A follow-up tick 2s later (< HARVEST_INTERVAL_S) with the same
        // ambiguous device must be throttled rather than re-running CLI.
        let calls_after_first_tick = *calls.lock().unwrap();
        mgr.poll_and_manage_daemon(&mut first, &joined_a_refs, 22.0, false);
        assert_eq!(*calls.lock().unwrap(), calls_after_first_tick);
    }

    #[test]
    fn test_pcap_manager_passes_ini_tmpdir_and_instance_and_records_cli_errors() {
        let missing_ini_out = concat!(
            "netsim E 11-09 08:06:41.842 ini_file.rs:139 - Unable to find netsim ini file: \"/tmp/netsim_2.ini\"\n",
            "netsim E 11-09 08:06:41.842 main.rs:349 - error=Failed to connect to : invalid socket address\n",
        );
        assert!(parse_netsim_capture_list(missing_ini_out).is_err());
        assert!(parse_netsim_capture_list("Failed to connect to server").is_err());
        assert!(parse_netsim_capture_list("error=connection failed").is_err());

        assert_eq!(
            build_netsim_cli_args(44709, Some(1), &["capture".to_string(), "list".to_string()]),
            vec!["-v", "-p", "44709", "capture", "list"]
        );
        assert_eq!(
            build_netsim_cli_args(42747, Some(2), &["capture".to_string(), "list".to_string()]),
            vec!["-v", "-i", "2", "-p", "42747", "capture", "list"]
        );

        let tmp = tempfile::tempdir().unwrap();
        let ini_dir = tmp.path().join("emu_tmp");
        std::fs::create_dir_all(&ini_dir).unwrap();
        let ini_path = ini_dir.join("netsim_2.ini");
        std::fs::write(&ini_path, "grpc.port=42747\npid=5001\n").unwrap();

        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        daemon.ini_path = Some(ini_path);
        daemon.grpc_port = Some(42747);

        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let calls_clone = calls.clone();
        let fail_first = std::sync::Arc::new(std::sync::Mutex::new(true));
        let fail_first_clone = fail_first.clone();
        let list_ok = "\
ID | Device Name | Chip Kind | State | Timestamp | Records | Size (bytes)
---|-------------|-----------|-------|-----------|---------|-------------
0  | erm_live37  | WIFI      | off   | 00:00:00  | 0       | 0
";
        mgr.cli_runner = std::sync::Arc::new(move |target, args| {
            calls_clone.lock().unwrap().push((target.clone(), args.to_vec()));
            let mut fail = fail_first_clone.lock().unwrap();
            if *fail {
                *fail = false;
                return Some(missing_ini_out.to_string());
            }
            Some(list_ok.to_string())
        });

        let joined = [EmulatorNetsimRecord {
            storage_key: "erm_live37_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("erm_live37".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        }];

        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        // First poll gets CLI error -> surfaced in skipped_notes, emulator stays unchecked.
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
        assert!(mgr.skipped_notes.iter().any(|n| n.contains("Unable to find netsim ini file")));
        assert!(mgr.enabled_by_monitor.is_empty());

        // Second poll 1s later retries immediately because capture list had failed,
        // passes ini_dir and instance 2, and enables capture.
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 11.0, false);
        assert!(mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 0)));

        let mut daemons = BTreeMap::new();
        daemons.insert(daemon.daemon_key, daemon);
        let mut emu_recs = BTreeMap::new();
        for e in joined {
            emu_recs.insert(e.storage_key.clone(), e);
        }
        mgr.cleanup(&mut daemons, &emu_recs);

        let recorded = calls.lock().unwrap();
        let expected_target = NetsimCliTarget {
            netsim_bin: "netsim".to_string(),
            grpc_port: 42747,
            ini_dir: Some(ini_dir),
            instance_num: Some(2),
        };
        assert!(recorded.iter().all(|(t, _)| t == &expected_target));
        assert!(
            recorded
                .iter()
                .any(|(_, a)| a == &["capture", "patch", "on", "0", "erm_live37", "WIFI"])
        );
        assert!(
            recorded
                .iter()
                .any(|(_, a)| a == &["capture", "patch", "off", "0", "erm_live37", "WIFI"])
        );
    }

    #[test]
    #[cfg(unix)]
    fn test_harvest_counts_current_bytes_of_a_pcap_that_shrank() {
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        let pcap = daemon.artifact_dir.join("pcaps").join("netsim-1-Pixel_8-WIFI.pcap");
        let set = |len: usize, mtime: u64| {
            std::fs::write(&pcap, vec![0xd4u8; len]).unwrap();
            std::fs::File::options()
                .write(true)
                .open(&pcap)
                .unwrap()
                .set_modified(UNIX_EPOCH + std::time::Duration::from_secs(mtime))
                .unwrap();
        };
        let mut mgr = NetsimPcapManager::new(Some(""), 1, &out);
        let zip_bytes = 7;
        set(800 * 1024, 1000);
        mgr.harvest_daemon_files(&mut daemon, &[]);
        // The capture restarts: the file shrinks, then grows again.
        set(100 * 1024, 1010);
        mgr.harvest_daemon_files(&mut daemon, &[]);
        set(500 * 1024, 1020);
        mgr.harvest_daemon_files(&mut daemon, &[]);
        assert!(!mgr.cap_reached);
        assert_eq!(mgr.total_copied_bytes, 500 * 1024 + zip_bytes);
    }

    #[test]
    #[cfg(unix)]
    fn test_harvest_sends_pcap_of_a_shared_device_name_to_shared() {
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        let emu = |serial: &str| EmulatorNetsimRecord {
            storage_key: format!("pixel_{serial}"),
            serial: serial.to_string(),
            device_name: Some("Pixel 8".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        };
        let joined = [emu("emulator-5554"), emu("emulator-5556")];
        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        mgr.harvest_daemon_files(&mut daemon, &joined_refs);
        let keys: Vec<&String> = mgr.copied_files.keys().filter(|k| k.ends_with(".pcap")).collect();
        assert_eq!(keys.len(), 1);
        assert!(keys[0].contains("/shared/"), "{keys:?}");
    }

    #[test]
    #[cfg(unix)]
    fn test_pcap_manager_skips_cli_when_disabled_and_lists_once_per_daemon_on_cleanup() {
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        daemon.grpc_port = Some(43929);

        let mut mgr_disabled = NetsimPcapManager::new(None, 500, &out);
        let disabled_calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
        let disabled_calls_clone = disabled_calls.clone();
        mgr_disabled.cli_runner = std::sync::Arc::new(move |_target, _args| {
            *disabled_calls_clone.lock().unwrap() += 1;
            Some(String::new())
        });
        mgr_disabled.poll_and_manage_daemon(&mut daemon, &[], 10.0, false);
        assert_eq!(*disabled_calls.lock().unwrap(), 0);

        let list_out = "\
ID | Device Name | Chip Kind            | State | Timestamp | Records | Size (bytes)
---|-------------|----------------------|-------|-----------|---------|-------------
1  | erm_live37  | WIFI                 | off   | 00:00:00  | 0       | 0
2  | erm_live37  | BLUETOOTH_LOW_ENERGY | off   | 00:00:00  | 0       | 0
";
        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Vec<String>>::new()));
        let calls_clone = calls.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_target, args| {
            calls_clone.lock().unwrap().push(args.to_vec());
            Some(list_out.to_string())
        });
        let joined = [EmulatorNetsimRecord {
            storage_key: "erm_live37_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("erm_live37".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        }];
        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
        assert_eq!(mgr.enabled_by_monitor.len(), 2);

        calls.lock().unwrap().clear();
        let mut daemons = BTreeMap::from([(daemon.daemon_key, daemon)]);
        let emu_recs = BTreeMap::new();
        mgr.cleanup(&mut daemons, &emu_recs);

        let cleanup_calls = calls.lock().unwrap();
        let list_count =
            cleanup_calls.iter().filter(|a| a.as_slice() == ["capture", "list"]).count();
        let off_count = cleanup_calls
            .iter()
            .filter(|a| a.len() >= 3 && a[0] == "capture" && a[1] == "patch" && a[2] == "off")
            .count();
        assert_eq!(list_count, 1);
        assert_eq!(off_count, 2);
    }

    #[test]
    #[cfg(unix)]
    fn test_cleanup_restores_prefix_colliding_captures_and_skips_exited_daemons() {
        let user_rows = parse_netsim_capture_list(
            "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
             1  | erm_live37  | WIFI      | on    | 00:00:00  | 0       | 0\n\
             10 | erm_live37b | WIFI      | on    | 00:00:05  | 0       | 0\n",
        )
        .unwrap();
        let only_1 = HashSet::from([1u32]);
        assert_eq!(
            select_safe_disable_patterns(&user_rows, "erm_live37", &user_rows[0], &only_1),
            None
        );

        let tmp = tempfile::tempdir().unwrap();
        let (mut d_old, out, _) = harvest_fixture(tmp.path(), true);
        d_old.exited = true;
        d_old.grpc_port = Some(43929);

        let mut d_live = d_old.clone();
        d_live.daemon_key = (5002, 200);
        d_live.pid = 5002;
        d_live.create_time = 200;
        d_live.start_wall = 200.0;
        d_live.exited = false;
        d_live.grpc_port = Some(43930);

        let mut mgr = NetsimPcapManager::new(Some(""), 1, &out);
        let state = std::sync::Arc::new(std::sync::Mutex::new((false, false, 0usize)));
        let state_clone = state.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_target, args| {
            let mut st = state_clone.lock().unwrap();
            st.2 += 1;
            let call_no = st.2;
            if args == ["capture", "list"] {
                let s1 = if st.0 { "on" } else { "off" };
                let s10 = if st.1 { "on" } else { "off" };
                let mut text = format!(
                    "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                     1  | erm_live37  | WIFI      | {s1}  | 00:00:00  | 0       | 0\n"
                );
                if call_no >= 3 {
                    text.push_str(&format!(
                        "10 | erm_live37b | WIFI      | {s10} | 00:00:05  | 0       | 0\n"
                    ));
                }
                return Some(text);
            }
            if args.len() >= 3 && args[0] == "capture" && args[1] == "patch" {
                let turn_on = args[2] == "on";
                let pats: Vec<String> = args[3..].iter().map(|p| p.to_uppercase()).collect();
                if pats
                    .iter()
                    .all(|p| "1".contains(p) || "ERM_LIVE37".contains(p) || "WIFI".contains(p))
                {
                    st.0 = turn_on;
                }
                if call_no >= 3
                    && pats.iter().all(|p| {
                        "10".contains(p) || "ERM_LIVE37B".contains(p) || "WIFI".contains(p)
                    })
                {
                    st.1 = turn_on;
                }
                return Some(
                    "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n"
                        .to_string(),
                );
            }
            None
        });

        let emu1 = EmulatorNetsimRecord {
            storage_key: "erm_live37_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("erm_live37".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(d_live.daemon_key),
            chip_ids: BTreeSet::from(["1".to_string()]),
            ..Default::default()
        };
        let emu2 = EmulatorNetsimRecord {
            storage_key: "erm_live37b_emulator-5556".to_string(),
            serial: "emulator-5556".to_string(),
            device_name: Some("erm_live37b".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(d_live.daemon_key),
            chip_ids: BTreeSet::from(["10".to_string()]),
            ..Default::default()
        };
        let old_emu = EmulatorNetsimRecord {
            storage_key: "old_emulator-5558".to_string(),
            serial: "emulator-5558".to_string(),
            device_name: Some("OldPhone".to_string()),
            state: "DISCONNECTED".to_string(),
            daemon_key: Some(d_old.daemon_key),
            ..Default::default()
        };

        mgr.poll_and_manage_daemon(&mut d_live, &[&emu1], 10.0, false);
        assert!(state.lock().unwrap().0);
        mgr.poll_and_manage_daemon(&mut d_live, &[&emu1, &emu2], 25.0, false);
        assert!(state.lock().unwrap().1);

        // forget_daemon drops enabled_by_monitor entries for an exited daemon.
        mgr.enabled_by_monitor.insert(
            (d_old.daemon_key, 99),
            EnabledChipRecord {
                target: NetsimCliTarget {
                    netsim_bin: "netsim".to_string(),
                    grpc_port: 43929,
                    ini_dir: None,
                    instance_num: None,
                },
                ini_path: None,
                device_name: "OldPhone".to_string(),
                row: CaptureRow {
                    id: 99,
                    device_name: "OldPhone".to_string(),
                    chip_kind: "WIFI".to_string(),
                    raw_chip_kind: "WIFI".to_string(),
                    state: "off".to_string(),
                    timestamp: String::new(),
                    records: 0,
                    size_bytes: 0,
                },
            },
        );
        mgr.forget_daemon(d_old.daemon_key);
        assert!(!mgr.enabled_by_monitor.contains_key(&(d_old.daemon_key, 99)));

        let mut daemons = BTreeMap::from([(d_old.daemon_key, d_old), (d_live.daemon_key, d_live)]);
        let emu_recs = BTreeMap::from([
            (old_emu.storage_key.clone(), old_emu),
            (emu1.storage_key.clone(), emu1),
            (emu2.storage_key.clone(), emu2),
        ]);
        mgr.cleanup(&mut daemons, &emu_recs);

        let st = state.lock().unwrap();
        assert!(!st.0, "chip 1 remained on after cleanup");
        assert!(!st.1, "chip 10 remained on after cleanup");
        assert!(!out.join("netsim_pcaps").join("5001-100").exists());
        assert!(out.join("netsim_pcaps").join("5002-200").exists());
    }

    #[test]
    #[cfg(unix)]
    fn test_pcap_manager_skips_foreign_daemons_caps_cli_retries_and_sanitizes_notes() {
        let tmp = tempfile::tempdir().unwrap();
        let (mut foreign_daemon, out, _) = harvest_fixture(tmp.path(), false);
        let foreign_ini_dir = tmp.path().join("foreign_tmp");
        std::fs::create_dir_all(&foreign_ini_dir).unwrap();
        foreign_daemon.ini_path = Some(foreign_ini_dir.join("netsim.ini"));
        foreign_daemon.grpc_port = Some(43929);

        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
        let calls_clone = calls.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_target, _args| {
            let mut c = calls_clone.lock().unwrap();
            *c += 1;
            Some(format!(
                "netsim E 11-09 08:24:10.{:03} main.rs:78 - gRPC error UNIMPLEMENTED\nError: unsupported",
                *c
            ))
        });
        let joined = [EmulatorNetsimRecord {
            storage_key: "Phone_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("Phone".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(foreign_daemon.daemon_key),
            ..Default::default()
        }];

        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        // Foreign-user daemon never invokes the CLI.
        mgr.poll_and_manage_daemon(&mut foreign_daemon, &joined_refs, 10.0, false);
        assert_eq!(*calls.lock().unwrap(), 0, "foreign daemon invoked CLI");

        // Disabled-only or empty emulator lists on a same-user daemon also skip CLI.
        let tmp2 = tempfile::tempdir().unwrap();
        let (mut own_daemon, _, _) = harvest_fixture(tmp2.path(), true);
        own_daemon.grpc_port = Some(43930);
        let disabled = [EmulatorNetsimRecord {
            storage_key: "NoNet_emulator-5556".to_string(),
            serial: "emulator-5556".to_string(),
            device_name: Some("NoNet".to_string()),
            state: "DISABLED".to_string(),
            no_netsim: true,
            daemon_key: Some(own_daemon.daemon_key),
            ..Default::default()
        }];
        let disabled_refs: Vec<&EmulatorNetsimRecord> = disabled.iter().collect();
        mgr.poll_and_manage_daemon(&mut own_daemon, &disabled_refs, 10.0, false);
        assert_eq!(*calls.lock().unwrap(), 0, "DISABLED emulator invoked CLI");

        // Persistent CLI errors allow at most 1 immediate retry within the 10s window,
        // and strip timestamps + newlines so skipped_notes stays single-line and bounded.
        for t in [20.0, 22.0, 24.0, 26.0, 28.0] {
            mgr.poll_and_manage_daemon(&mut own_daemon, &joined_refs, t, false);
        }
        assert_eq!(*calls.lock().unwrap(), 2);
        assert_eq!(mgr.skipped_notes.len(), 1);
        assert!(!mgr.skipped_notes[0].contains('\n'));
        assert!(mgr.skipped_notes[0].contains("gRPC error UNIMPLEMENTED; Error: unsupported"));

        // Missing/timed-out CLI (`None`) records a diagnostic skipped_note.
        let mut mgr_none = NetsimPcapManager::new(Some(""), 500, &out);
        mgr_none.cli_runner = std::sync::Arc::new(|_, _| None);
        mgr_none.poll_and_manage_daemon(&mut own_daemon, &joined_refs, 40.0, false);
        assert_eq!(mgr_none.skipped_notes.len(), 1);
        assert!(mgr_none.skipped_notes[0].contains("unavailable or timed out"));
    }

    #[test]
    #[cfg(unix)]
    fn test_pcap_manager_allows_cli_for_same_user_daemon_with_system_tmp_ini() {
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        let tmp_ini =
            tempfile::Builder::new().prefix("netsim_").suffix(".ini").tempfile_in("/tmp").unwrap();
        std::fs::write(tmp_ini.path(), "grpc.port=43929\npid=5001\n").unwrap();
        daemon.ini_path = Some(tmp_ini.path().to_path_buf());
        daemon.grpc_port = Some(43929);

        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
        let calls_clone = calls.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_target, _args| {
            *calls_clone.lock().unwrap() += 1;
            Some(
                "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                 1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                    .to_string(),
            )
        });
        let joined = [EmulatorNetsimRecord {
            storage_key: "Phone_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("Phone".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        }];
        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
        assert!(*calls.lock().unwrap() > 0, "same-user daemon with /tmp INI never invoked CLI");
        assert!(mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 1)));
    }

    #[test]
    fn test_pcap_manager_new_expands_tilde_and_saturates_max_mb() {
        if let Some(home) = std::env::var_os("HOME")
            .filter(|v| !v.is_empty())
            .or_else(|| std::env::var_os("USERPROFILE").filter(|v| !v.is_empty()))
            .map(PathBuf::from)
        {
            let mgr_custom = NetsimPcapManager::new(Some("~/custom_pcaps"), 500, Path::new("."));
            assert_eq!(mgr_custom.pcap_dir, Some(home.join("custom_pcaps")));

            let mgr_out = NetsimPcapManager::new(Some(""), 500, Path::new("~/erm_out"));
            assert_eq!(mgr_out.pcap_dir, Some(home.join("erm_out").join("netsim_pcaps")));
        }

        let fake_home = Path::new("/fake/home");
        assert_eq!(expand_tilde_path_with(Path::new("~"), Some(fake_home)), fake_home);
        assert_eq!(expand_tilde_path_with(Path::new("~/"), Some(fake_home)), fake_home);
        assert_eq!(
            expand_tilde_path_with(Path::new("~\\sub"), Some(fake_home)),
            fake_home.join("sub")
        );
        assert_eq!(
            expand_tilde_path_with(Path::new("~other/sub"), Some(fake_home)),
            Path::new("~other/sub")
        );
        assert_eq!(expand_tilde_path_with(Path::new("~/sub"), None), Path::new("~/sub"));

        let mgr_max = NetsimPcapManager::new(Some(""), u64::MAX, Path::new("/tmp"));
        assert_eq!(mgr_max.max_bytes, u64::MAX);
    }

    #[test]
    #[cfg(unix)]
    fn test_pcap_manager_rejects_cli_when_artifact_dir_is_symlink_or_unowned() {
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        let real_art = daemon.artifact_dir.clone();
        let ini = tmp.path().join("netsim.ini");
        std::fs::write(&ini, "grpc.port=43929\npid=5001\n").unwrap();
        daemon.ini_path = Some(ini);
        daemon.grpc_port = Some(43929);

        let sym_art = tmp.path().join("symlink_art");
        std::os::unix::fs::symlink(&real_art, &sym_art).unwrap();
        daemon.artifact_dir = sym_art;

        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
        let calls_clone = calls.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_, _| {
            *calls_clone.lock().unwrap() += 1;
            Some(
                "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                 1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                    .to_string(),
            )
        });
        let joined = [EmulatorNetsimRecord {
            storage_key: "Phone_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("Phone".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        }];
        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
        assert_eq!(*calls.lock().unwrap(), 0, "symlinked artifact_dir must block netsim CLI");
        assert!(mgr.copied_files.is_empty(), "symlinked artifact_dir must not harvest files");
        assert_eq!(daemon.pcap_live_files, 0);

        // Real artifact_dir with a symlinked `pcaps` subdirectory must not harvest pcaps.
        let sym_pcaps_art = tmp.path().join("real_art_sym_pcaps");
        std::fs::create_dir_all(&sym_pcaps_art).unwrap();
        std::os::unix::fs::symlink(real_art.join("pcaps"), sym_pcaps_art.join("pcaps")).unwrap();
        daemon.artifact_dir = sym_pcaps_art;
        mgr.harvest_daemon_files(&mut daemon, &joined_refs);
        assert!(mgr.copied_files.is_empty(), "symlinked pcaps subdir must not harvest pcaps");
        assert_eq!(daemon.pcap_live_files, 0);

        // Dangling symlink artifact_dir must also block CLI execution.
        let dangling_art = tmp.path().join("dangling_art");
        std::os::unix::fs::symlink(tmp.path().join("missing_target"), &dangling_art).unwrap();
        daemon.artifact_dir = dangling_art;
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 20.0, false);
        assert_eq!(*calls.lock().unwrap(), 0, "dangling symlink artifact_dir must block CLI");
        assert!(mgr.copied_files.is_empty());
    }

    #[test]
    #[cfg(unix)]
    fn test_pcap_manager_rechecks_on_chip_attach_and_routes_duplicate_chip_ids_to_shared() {
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        daemon.grpc_port = Some(43929);

        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let state = std::sync::Arc::new(std::sync::Mutex::new((false, 0usize)));
        let state_clone = state.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_, args| {
            let mut st = state_clone.lock().unwrap();
            st.1 += 1;
            if args == ["capture", "list"] {
                if !st.0 {
                    return Some(
                        "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n"
                            .to_string(),
                    );
                }
                return Some(
                    "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                     1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                        .to_string(),
                );
            }
            Some("ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n".to_string())
        });

        let mut emu = EmulatorNetsimRecord {
            storage_key: "Phone_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("Phone".to_string()),
            state: "CONNECTING".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        };

        // Tick at t=10.0: 0 chips attached -> lists once, records 0-chip baseline.
        mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 10.0, false);
        assert_eq!(state.lock().unwrap().1, 1);

        // Tick at t=12.0 (< HARVEST_INTERVAL_S, still 0 chips): throttled, no CLI spam.
        mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 12.0, false);
        assert_eq!(state.lock().unwrap().1, 1);

        // Tick at t=14.0 (< HARVEST_INTERVAL_S): WIFI chip attaches -> must recheck immediately.
        state.lock().unwrap().0 = true;
        emu.state = "CONNECTED".to_string();
        emu.chip_ids.insert("1".to_string());
        emu.chip_attach_s.insert("WIFI".to_string(), (1.0, false));
        mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 14.0, false);
        assert!(
            mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 1)),
            "newly attached chip should be auto-enabled without waiting 10s"
        );
        let calls_after_enable = state.lock().unwrap().1;

        // Tick at t=16.0 (< 24.0, chip count unchanged at 1): throttled again.
        mgr.poll_and_manage_daemon(&mut daemon, &[&emu], 16.0, false);
        assert_eq!(state.lock().unwrap().1, calls_after_enable);

        // Duplicate chip_id claim across two emulators must route pcap to `shared/`.
        let pcaps_dir = daemon.artifact_dir.join("pcaps");
        std::fs::write(pcaps_dir.join("netsim-7-Pixel_8-WIFI.pcap"), b"shared-bytes").unwrap();
        let make_dup = |serial: &str| EmulatorNetsimRecord {
            storage_key: format!("Pixel_8_{serial}"),
            serial: serial.to_string(),
            device_name: Some("Pixel 8".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            chip_ids: BTreeSet::from(["7".to_string()]),
            ..Default::default()
        };
        let dup_a = make_dup("emulator-5554");
        let dup_b = make_dup("emulator-5556");
        let mut mgr2 = NetsimPcapManager::new(Some(""), 500, &out);
        mgr2.harvest_daemon_files(&mut daemon, &[&dup_a, &dup_b]);
        let shared_hits: Vec<&String> = mgr2
            .copied_files
            .keys()
            .filter(|k| k.ends_with("netsim-7-Pixel_8-WIFI.pcap"))
            .collect();
        assert_eq!(shared_hits.len(), 1);
        assert!(shared_hits[0].contains("/shared/"), "{shared_hits:?}");
    }

    #[cfg(unix)]
    #[test]
    fn test_pcap_cli_skips_missing_ini_dir_and_sets_netsim_instance_env() {
        let tmp = tempfile::tempdir().unwrap();
        let fake_cli = tmp.path().join("fake_netsim");
        write_script(
            &fake_cli,
            "#!/bin/sh\nprintf 'INST=%s\\nTMP=%s\\nARGS=%s\\n' \"$NETSIM_INSTANCE\" \"$TMPDIR\" \"$*\"\n",
        );

        // `capture list`/`capture patch` must not execute when `ini_dir` is `None`.
        let no_ini_target = NetsimCliTarget {
            netsim_bin: fake_cli.to_string_lossy().to_string(),
            grpc_port: 43929,
            ini_dir: None,
            instance_num: Some(2),
        };
        assert_eq!(
            default_cli_runner(&no_ini_target, &["capture".to_string(), "list".to_string()]),
            None
        );
        assert_eq!(
            default_cli_runner(
                &no_ini_target,
                &["capture".to_string(), "patch".to_string(), "on".to_string(), "1".to_string()],
            ),
            None
        );

        // With `ini_dir` present, `default_cli_runner` passes both `TMPDIR` and `NETSIM_INSTANCE`
        // (defaulting `None` / `0` to `DEFAULT_NETSIM_INSTANCE` = `1`).
        let ini_dir = tmp.path().join("ini_dir");
        std::fs::create_dir_all(&ini_dir).unwrap();
        let default_inst_target = NetsimCliTarget {
            netsim_bin: fake_cli.to_string_lossy().to_string(),
            grpc_port: 43929,
            ini_dir: Some(ini_dir.clone()),
            instance_num: None,
        };
        let out1 =
            default_cli_runner(&default_inst_target, &["capture".to_string(), "list".to_string()])
                .expect("capture list with ini_dir must run");
        assert!(out1.contains("INST=1\n"), "out1={out1}");
        assert!(out1.contains(&format!("TMP={}\n", ini_dir.display())), "out1={out1}");

        let inst2_target = NetsimCliTarget { instance_num: Some(2), ..default_inst_target };
        let out2 = default_cli_runner(&inst2_target, &["capture".to_string(), "list".to_string()])
            .expect("capture list for instance 2 must run");
        assert!(out2.contains("INST=2\n"), "out2={out2}");
        assert!(out2.contains("ARGS=-v -i 2 -p 43929 capture list"), "out2={out2}");

        // `poll_and_manage_daemon` skips CLI without false error notes when `ini_path` is `None`,
        // while still harvesting on-disk PCAP/ZIP artifacts.
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        daemon.ini_path = None;
        daemon.grpc_port = Some(43929);
        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(0usize));
        let calls_clone = calls.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_, _| {
            *calls_clone.lock().unwrap() += 1;
            None
        });
        let joined = [EmulatorNetsimRecord {
            storage_key: "Phone_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("Phone".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        }];
        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
        assert_eq!(*calls.lock().unwrap(), 0, "daemon with ini_path=None must not invoke CLI");
        assert!(mgr.skipped_notes.is_empty(), "notes={:?}", mgr.skipped_notes);
        assert!(mgr.cli_error_counts.is_empty());
        assert!(!mgr.copied_files.is_empty(), "on-disk artifacts must still be harvested");
    }

    #[cfg(unix)]
    #[test]
    fn test_run_cli_vets_an_adopted_sibling_before_every_run() {
        use std::os::unix::fs::PermissionsExt;

        let tmp = tempfile::tempdir().unwrap();
        let sdk = tmp.path().join("sdk");
        std::fs::create_dir(&sdk).unwrap();
        // Both directories would otherwise take their mode from the umask.
        for dir in [tmp.path(), sdk.as_path()] {
            std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let cli = sdk.join("netsim");
        std::fs::write(&cli, b"#!/bin/sh\n").unwrap();
        std::fs::set_permissions(&cli, std::fs::Permissions::from_mode(0o755)).unwrap();

        let mut mgr = NetsimPcapManager::new(Some(""), 500, tmp.path());
        let runs = std::sync::Arc::new(std::sync::Mutex::new(0usize));
        let runs_clone = runs.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_, _| {
            *runs_clone.lock().unwrap() += 1;
            Some("ran".to_string())
        });
        let args = ["capture".to_string(), "list".to_string()];
        let sibling = NetsimCliTarget {
            netsim_bin: cli.to_string_lossy().to_string(),
            grpc_port: 43929,
            ini_dir: None,
            instance_num: None,
        };
        assert_eq!(mgr.run_cli(&sibling, &args).as_deref(), Some("ran"));
        assert_eq!(*runs.lock().unwrap(), 1);

        // Once others can write to its directory, the sibling is no longer run.
        std::fs::set_permissions(&sdk, std::fs::Permissions::from_mode(0o777)).unwrap();
        assert_eq!(mgr.run_cli(&sibling, &args), None);
        std::fs::set_permissions(&sdk, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(mgr.run_cli(&sibling, &args).as_deref(), Some("ran"));
        std::fs::remove_file(&cli).unwrap();
        assert_eq!(mgr.run_cli(&sibling, &args), None);
        assert_eq!(*runs.lock().unwrap(), 2);

        // A bare name is found through the monitor's own PATH and is not vetted.
        let bare = NetsimCliTarget { netsim_bin: "netsim".to_string(), ..sibling };
        assert_eq!(mgr.run_cli(&bare, &args).as_deref(), Some("ran"));
        assert_eq!(*runs.lock().unwrap(), 3);
    }

    #[cfg(unix)]
    #[test]
    fn test_pcap_cli_overrides_ambient_netsim_instance_and_cleanup_revalidates_ini() {
        // Changing this process's environment is `unsafe` with other test threads running, so a
        // child running only this test gets `NETSIM_INSTANCE=7`: `default_cli_runner` must still
        // pass `NETSIM_INSTANCE=1` for `instance_num` `None` and `Some(1)`.
        if std::env::var_os("ERM_SUBTEST_NETSIM_INST_OVERRIDE").is_some() {
            assert_eq!(std::env::var("NETSIM_INSTANCE").as_deref(), Ok("7"));
            let tmp = tempfile::tempdir().unwrap();
            let fake_cli = tmp.path().join("fake_netsim");
            write_script(&fake_cli, "#!/bin/sh\nprintf 'INST=%s\\n' \"$NETSIM_INSTANCE\"\n");
            let ini_dir = tmp.path().join("ini_dir");
            std::fs::create_dir_all(&ini_dir).unwrap();
            for inst in [None, Some(1)] {
                let target = NetsimCliTarget {
                    netsim_bin: fake_cli.to_string_lossy().to_string(),
                    grpc_port: 43929,
                    ini_dir: Some(ini_dir.clone()),
                    instance_num: inst,
                };
                let out = default_cli_runner(&target, &["capture".to_string(), "list".to_string()])
                    .expect("default_cli_runner must run");
                assert_eq!(out, "INST=1\n", "inst={inst:?} leaked ambient NETSIM_INSTANCE=7");
            }
            return;
        }

        let child = std::process::Command::new(std::env::current_exe().unwrap())
            .env("NETSIM_INSTANCE", "7")
            .env("ERM_SUBTEST_NETSIM_INST_OVERRIDE", "1")
            .args([
                "--exact",
                "netsim::pcap::tests::test_pcap_cli_overrides_ambient_netsim_instance_and_cleanup_revalidates_ini",
                "--nocapture",
            ])
            .output()
            .expect("must run child subtest");
        let child_stdout = String::from_utf8_lossy(&child.stdout);
        assert!(
            child.status.success(),
            "ambient NETSIM_INSTANCE subtest failed: stdout={child_stdout} stderr={}",
            String::from_utf8_lossy(&child.stderr)
        );
        // A name that matches no test also exits successfully.
        assert!(child_stdout.contains("test result: ok. 1 passed"), "{child_stdout}");

        // Shutdown `cleanup` (`restore_captures_on_exit`) must re-verify `is_trusted_ini_path`
        // and skip `capture list` / `capture patch off` if `/tmp/netsim_*.ini` was unlinked or
        // replaced by a symlink before shutdown.
        let tmp = tempfile::tempdir().unwrap();
        let (mut daemon, out, _) = harvest_fixture(tmp.path(), true);
        let sys_ini =
            tempfile::Builder::new().prefix("netsim_").suffix(".ini").tempfile_in("/tmp").unwrap();
        let sys_ini_path = sys_ini.path().to_path_buf();
        std::fs::write(&sys_ini_path, "grpc.port=43929\npid=5001\n").unwrap();
        daemon.ini_path = Some(sys_ini_path.clone());
        daemon.grpc_port = Some(43929);

        let mut mgr = NetsimPcapManager::new(Some(""), 500, &out);
        let calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Vec<String>>::new()));
        let calls_clone = calls.clone();
        mgr.cli_runner = std::sync::Arc::new(move |_, args| {
            calls_clone.lock().unwrap().push(args.to_vec());
            Some(
                "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
                 1  | Phone       | WIFI      | off   | 00:00:00  | 0       | 0\n"
                    .to_string(),
            )
        });
        let joined = [EmulatorNetsimRecord {
            storage_key: "Phone_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            device_name: Some("Phone".to_string()),
            state: "CONNECTED".to_string(),
            daemon_key: Some(daemon.daemon_key),
            ..Default::default()
        }];
        let joined_refs: Vec<&EmulatorNetsimRecord> = joined.iter().collect();
        mgr.poll_and_manage_daemon(&mut daemon, &joined_refs, 10.0, false);
        assert!(mgr.enabled_by_monitor.contains_key(&(daemon.daemon_key, 1)));
        calls.lock().unwrap().clear();

        // Unlink INI in sticky `/tmp`: `NotFound` inside root-owned `/tmp` fails
        // `is_trusted_ini_path`, so `cleanup` must skip CLI invocations completely.
        drop(sys_ini);
        assert!(!sys_ini_path.exists());
        let mut daemons = BTreeMap::from([(daemon.daemon_key, daemon)]);
        let emu_recs = BTreeMap::from([(joined[0].storage_key.clone(), joined[0].clone())]);
        mgr.cleanup(&mut daemons, &emu_recs);
        assert!(
            calls.lock().unwrap().is_empty(),
            "cleanup invoked CLI after /tmp INI was deleted: {:?}",
            *calls.lock().unwrap()
        );
        // The summary says which capture the monitor could not turn off again.
        assert_eq!(
            mgr.skipped_notes,
            ["Left capture on for 'Phone' (chip 1): netsim INI is missing or no longer trusted"]
        );
    }
}
