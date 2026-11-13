use sysinfo::System;

use super::cmdline::is_candidate_process;
use super::{BYTES_PER_GIB, BYTES_PER_MIB, HOST_CMD_TIMEOUT, MIB_PER_GIB};

#[cfg(any(target_os = "linux", test))]
const DISKSTATS_SECTOR_BYTES: u64 = 512;
const MIN_RATE_INTERVAL_SECS: f64 = 0.001;
/// 1000 ms per second / 100% per core = 10 ms per (core-% * second).
const MS_PER_CORE_PERCENT_SEC: f64 = 10.0;
const MILLIDEGREES_PER_DEGREE_C: f32 = 1000.0;
const MAX_VALID_THERMAL_C: f32 = 150.0;

pub fn parse_nvidia_smi(stdout: &str) -> (Option<f32>, Option<f32>) {
    if let Some(line) = stdout.lines().next() {
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() >= 2 {
            let gpu = parts[0].trim().parse::<f32>().ok();
            let mem = parts[1].trim().parse::<f32>().ok();
            return (gpu, mem);
        }
    }
    (None, None)
}

pub fn parse_vmstat(stdout: &str) -> (u64, u64) {
    let mut pgin = 0;
    let mut pgout = 0;
    for line in stdout.lines() {
        if line.starts_with("pgpgin ") {
            pgin = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
        } else if line.starts_with("pgpgout ") {
            pgout = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
        }
    }
    (pgin, pgout)
}

pub fn parse_vm_stat(stdout: &str) -> (u64, u64) {
    let mut pgin = 0;
    let mut pgout = 0;
    for line in stdout.lines() {
        if line.contains("Pages paged in:") || line.starts_with("Pageins:") {
            pgin = line
                .split_whitespace()
                .last()
                .and_then(|s| s.trim_end_matches('.').parse::<u64>().ok())
                .unwrap_or(0);
        } else if line.contains("Pages paged out:") || line.starts_with("Pageouts:") {
            pgout = line
                .split_whitespace()
                .last()
                .and_then(|s| s.trim_end_matches('.').parse::<u64>().ok())
                .unwrap_or(0);
        }
    }
    (pgin, pgout)
}

/// CPU use of a process over the last interval in % of one core, from its
/// accumulated CPU time. A process (pid + start time) without a previous
/// sample reports 0, like psutil's first `cpu_percent()` call.
pub(super) fn cpu_percent(prev_ms: Option<u64>, now_ms: u64, interval_secs: Option<f64>) -> f32 {
    match (prev_ms, interval_secs) {
        (Some(prev), Some(dt)) if dt > 0.0 => {
            (now_ms.saturating_sub(prev) as f64 / (dt * MS_PER_CORE_PERCENT_SEC)) as f32
        }
        _ => 0.0,
    }
}

pub(super) fn process_refresh_kind_for_os(
    is_linux: bool,
    is_windows: bool,
) -> sysinfo::ProcessRefreshKind {
    let kind = sysinfo::ProcessRefreshKind::nothing()
        .with_cpu()
        .with_memory()
        .with_cmd(sysinfo::UpdateKind::Always)
        .with_exe(sysinfo::UpdateKind::Always)
        // Owners decide whose netsimd artifacts and CLI are trusted.
        .with_user(sysinfo::UpdateKind::OnlyIfNotSet)
        // Linux reads the few environments it needs from /proc.
        .with_environ(if is_linux {
            sysinfo::UpdateKind::Never
        } else {
            sysinfo::UpdateKind::OnlyIfNotSet
        });
    if !is_linux && !is_windows { kind.with_disk_usage() } else { kind }
}

pub(super) fn refresh_processes(sys: &mut System) {
    sys.refresh_processes_specifics(
        sysinfo::ProcessesToUpdate::All,
        true,
        process_refresh_kind_for_os(cfg!(target_os = "linux"), cfg!(target_os = "windows")),
    );
}

/// Running (non-thread, non-zombie) processes that may belong to an emulator or netsimd. An
/// exiting process stays in the table as a zombie with no memory for a moment; like psutil in
/// the Python monitor, it no longer counts.
pub(super) fn candidate_processes(
    sys: &System,
) -> impl Iterator<Item = (&sysinfo::Pid, &sysinfo::Process)> {
    sys.processes().iter().filter(|(_, p)| {
        p.thread_kind().is_none()
            && p.status() != sysinfo::ProcessStatus::Zombie
            && is_candidate_process(&p.name().to_string_lossy(), p.exe(), p.cmd())
    })
}

/// Cumulative `(bytes read, bytes written)` over the devices accepted by
/// `is_disk`, from `/proc/diskstats` text.
#[cfg(any(target_os = "linux", test))]
pub(super) fn parse_diskstats(text: &str, is_disk: impl Fn(&str) -> bool) -> (u64, u64) {
    text.lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            let (dev, sectors_read, sectors_written) =
                (parts.get(2)?, parts.get(5)?, parts.get(9)?);
            is_disk(dev).then(|| {
                Some((
                    sectors_read.parse::<u64>().ok()? * DISKSTATS_SECTOR_BYTES,
                    sectors_written.parse::<u64>().ok()? * DISKSTATS_SECTOR_BYTES,
                ))
            })?
        })
        .fold((0, 0), |(r, w), (dr, dw)| (r + dr, w + dw))
}

/// Whole physical disks are the `sys_block` entries backed by a device.
/// Partitions are not listed there, and device-mapper, loop, md, zram and ram
/// devices have no `device` link, so I/O already counted on the underlying
/// disk is not counted again.
#[cfg(any(target_os = "linux", test))]
pub(super) fn is_physical_disk(sys_block: &std::path::Path, dev: &str) -> bool {
    sys_block.join(dev).join("device").exists()
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct MemoryStats {
    pub(super) ram_used_gb: f64,
    pub(super) ram_total_gb: f64,
    pub(super) ram_percent: f32,
    pub(super) swap_used_gb: f64,
    pub(super) swap_total_gb: f64,
    pub(super) swap_percent: f32,
}

pub(super) fn compute_memory_stats(
    used_mem: u64,
    total_mem: u64,
    used_swap: u64,
    total_swap: u64,
) -> MemoryStats {
    let ram_used_gb = used_mem as f64 / BYTES_PER_GIB;
    let ram_total_gb = total_mem as f64 / BYTES_PER_GIB;
    let ram_percent =
        if total_mem > 0 { (used_mem as f32 / total_mem as f32) * 100.0 } else { 0.0 };

    let swap_used_gb = used_swap as f64 / BYTES_PER_GIB;
    let swap_total_gb = total_swap as f64 / BYTES_PER_GIB;
    let swap_percent =
        if total_swap > 0 { (used_swap as f32 / total_swap as f32) * 100.0 } else { 0.0 };

    MemoryStats {
        ram_used_gb,
        ram_total_gb,
        ram_percent,
        swap_used_gb,
        swap_total_gb,
        swap_percent,
    }
}

/// Rate in MiB/s from cumulative byte counters; counters that went backwards
/// (e.g. a removed disk) or have no prior baseline yield 0.
pub(super) fn disk_counter_rate_mbs(curr: u64, last: u64, sample_interval: Option<f64>) -> f64 {
    match sample_interval {
        Some(dt) if last > 0 && dt > MIN_RATE_INTERVAL_SECS => {
            curr.saturating_sub(last) as f64 / BYTES_PER_MIB / dt
        }
        _ => 0.0,
    }
}

pub(super) fn process_disk_delta_rate_mbs(
    total_read_bytes: u64,
    total_write_bytes: u64,
    sample_interval: Option<f64>,
    elapsed_secs: f64,
) -> (f64, f64) {
    let dt = sample_interval.unwrap_or(elapsed_secs);
    if dt <= MIN_RATE_INTERVAL_SECS {
        return (0.0, 0.0);
    }
    (
        (total_read_bytes as f64 / BYTES_PER_MIB) / dt,
        (total_write_bytes as f64 / BYTES_PER_MIB) / dt,
    )
}

#[cfg(any(target_os = "windows", test))]
pub(super) fn sum_disk_usage_counters(
    usages: impl IntoIterator<Item = sysinfo::DiskUsage>,
) -> (u64, u64) {
    usages.into_iter().fold((0u64, 0u64), |(r, w), u| {
        (r.saturating_add(u.total_read_bytes), w.saturating_add(u.total_written_bytes))
    })
}

#[cfg(any(target_os = "windows", test))]
pub(super) fn read_sysinfo_disk_counters(disks: &mut sysinfo::Disks) -> (u64, u64) {
    disks.refresh_specifics(true, sysinfo::DiskRefreshKind::nothing().with_io_usage());
    sum_disk_usage_counters(disks.list().iter().map(sysinfo::Disk::usage))
}

pub(super) struct GpuStats {
    pub(super) percent: Option<f32>,
    pub(super) memory_mb: Option<f32>,
    pub(super) by_pid: std::collections::HashMap<u32, f32>,
}

/// Busy % of the first DRM card (`card0`, `card1`, ...) that reports one; amdgpu does, while
/// i915 and the NVIDIA driver do not, so the busy GPU is not always `card0`.
pub(super) fn read_drm_gpu_busy(drm_class_dir: &std::path::Path) -> Option<f32> {
    let mut cards: Vec<std::path::PathBuf> = std::fs::read_dir(drm_class_dir)
        .ok()?
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_name()
                .to_str()
                .and_then(|name| name.strip_prefix("card"))
                .is_some_and(|id| !id.is_empty() && id.bytes().all(|b| b.is_ascii_digit()))
        })
        .map(|entry| entry.path())
        .collect();
    cards.sort();
    cards.iter().find_map(|card| {
        std::fs::read_to_string(card.join("device/gpu_busy_percent")).ok()?.trim().parse().ok()
    })
}

/// GPU load and memory from `nvidia-smi`, else the load from a DRM card's sysfs node.
pub(super) fn resolve_nvidia_or_drm_gpu(
    drm_class_dir: &std::path::Path,
    sample_nvidia: impl FnOnce() -> (Option<f32>, Option<f32>),
) -> (Option<f32>, Option<f32>) {
    let (percent, memory_mb) = sample_nvidia();
    (percent.or_else(|| read_drm_gpu_busy(drm_class_dir)), memory_mb)
}

/// Parses `nvidia-smi pmon -c 1` output into per-PID SM utilization %.
pub(super) fn parse_nvidia_pmon(stdout: &str) -> std::collections::HashMap<u32, f32> {
    let mut gpu_by_pid = std::collections::HashMap::new();
    for line in stdout.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 4 {
            if let Ok(pid) = parts[1].parse::<u32>() {
                let sm_str = parts[3];
                let sm_val = if sm_str != "-" && sm_str != "?" {
                    sm_str.parse::<f32>().unwrap_or(0.0)
                } else {
                    0.0
                };
                *gpu_by_pid.entry(pid).or_insert(0.0) += sm_val;
            }
        }
    }
    gpu_by_pid
}

pub(super) fn parse_intel_gpu_top(stdout: &str) -> Option<f32> {
    static RE_INTEL: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_intel =
        RE_INTEL.get_or_init(|| regex::Regex::new(r"(?i)Render/3D(?:/\d+)?:\s+([\d.]+)%").unwrap());
    let caps = re_intel.captures(stdout)?;
    caps[1].parse::<f32>().ok()
}

pub(super) fn parse_mac_powermetrics_gpu(stdout: &str) -> Option<f32> {
    static RE_MAC: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_mac = RE_MAC.get_or_init(|| {
        regex::Regex::new(r"(?i)GPU (?:HW )?active residency:\s+([\d.]+)%").unwrap()
    });
    let caps = re_mac.captures(stdout)?;
    caps[1].parse::<f32>().ok()
}

pub(super) fn parse_windows_gpu(stdout: &str) -> Option<f32> {
    stdout.trim().replace(',', ".").parse::<f32>().ok().filter(|v| v.is_finite())
}

pub(super) fn poll_cached_gpu(
    failed: &mut bool,
    cache: &std::sync::Arc<std::sync::Mutex<Option<f32>>>,
    fetching: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    last_fetch: &mut Option<std::time::Instant>,
    interval: std::time::Duration,
    fetch: impl FnOnce() -> Option<f32> + Send + 'static,
) -> Option<f32> {
    if *failed {
        return None;
    }
    {
        let mut guard = cache.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if guard.is_some_and(f32::is_nan) {
            *guard = None;
            *failed = true;
            return None;
        }
    }
    let now = std::time::Instant::now();
    let should_fetch = match *last_fetch {
        Some(last) => now.duration_since(last) >= interval,
        None => true,
    };

    if should_fetch && !fetching.load(std::sync::atomic::Ordering::SeqCst) {
        fetching.store(true, std::sync::atomic::Ordering::SeqCst);
        *last_fetch = Some(now);

        let cache_clone = cache.clone();
        let fetching_clone = fetching.clone();

        std::thread::spawn(move || {
            struct ResetOnDrop(std::sync::Arc<std::sync::atomic::AtomicBool>);
            impl Drop for ResetOnDrop {
                fn drop(&mut self) {
                    self.0.store(false, std::sync::atomic::Ordering::SeqCst);
                }
            }
            // Declared before `fetch()` runs so the flag also clears when the probe panics.
            let _reset = ResetOnDrop(fetching_clone);
            let parsed = fetch();
            let mut guard = cache_clone.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            *guard = Some(parsed.unwrap_or(f32::NAN));
        });
    }

    cache.lock().unwrap_or_else(std::sync::PoisonError::into_inner).filter(|v| !v.is_nan())
}

pub(super) fn read_max_thermal_temp(thermal_dir: &std::path::Path) -> Option<f32> {
    let entries = std::fs::read_dir(thermal_dir).ok()?;
    let mut max_t: Option<f32> = None;
    for entry in entries.flatten() {
        let name = entry.file_name();
        if name.to_string_lossy().starts_with("thermal_zone") {
            let temp_path = entry.path().join("temp");
            if let Ok(temp_str) = std::fs::read_to_string(temp_path) {
                if let Ok(t) = temp_str.trim().parse::<f32>() {
                    let t_c = t / MILLIDEGREES_PER_DEGREE_C;
                    if t_c > 0.0 && t_c < MAX_VALID_THERMAL_C {
                        max_t = Some(max_t.map_or(t_c, |curr| curr.max(t_c)));
                    }
                }
            }
        }
    }
    max_t
}

pub(super) fn sample_thermal_temp() -> Option<f32> {
    if cfg!(target_os = "linux") {
        read_max_thermal_temp(std::path::Path::new("/sys/class/thermal"))
    } else {
        None
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct PagingStats {
    pub(super) display: String,
    pub(super) in_rate: f64,
    pub(super) out_rate: f64,
}

/// `counters` is `None` where paging counters are unsupported (N/A, NaN rates).
pub(super) fn compute_paging_stats(
    counters: Option<(u64, u64)>,
    last_pgin: u64,
    last_pgout: u64,
    elapsed_secs: f64,
    unit: &str,
) -> PagingStats {
    let Some((pgin, pgout)) = counters else {
        return PagingStats { display: "N/A".to_string(), in_rate: f64::NAN, out_rate: f64::NAN };
    };
    if last_pgin > 0 || last_pgout > 0 {
        let (in_rate, out_rate) = if elapsed_secs > MIN_RATE_INTERVAL_SECS {
            (
                pgin.saturating_sub(last_pgin) as f64 / elapsed_secs,
                pgout.saturating_sub(last_pgout) as f64 / elapsed_secs,
            )
        } else {
            (0.0, 0.0)
        };
        let display = format!("In: {:.0} / Out: {:.0} ({})", in_rate, out_rate, unit);
        PagingStats { display, in_rate, out_rate }
    } else {
        PagingStats { display: "Calculating...".to_string(), in_rate: 0.0, out_rate: 0.0 }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct BatteryStats {
    pub(super) display: String,
    pub(super) percent: Option<f32>,
}

pub(super) fn read_linux_battery(power_supply_dir: &std::path::Path) -> Option<BatteryStats> {
    let entries = std::fs::read_dir(power_supply_dir).ok()?;
    for entry in entries.flatten() {
        if entry.file_name().to_string_lossy().starts_with("BAT") {
            let cap_path = entry.path().join("capacity");
            let stat_path = entry.path().join("status");
            if let (Ok(cap), Ok(stat)) =
                (std::fs::read_to_string(cap_path), std::fs::read_to_string(stat_path))
            {
                let cap_trimmed = cap.trim();
                return Some(BatteryStats {
                    display: format!("{}% ({})", cap_trimmed, stat.trim()),
                    percent: cap_trimmed.parse::<f32>().ok(),
                });
            }
        }
    }
    None
}

pub(super) fn parse_pmset_battery(stdout: &str) -> Option<BatteryStats> {
    let line = stdout.lines().nth(1)?;
    let idx = line.find('%')?;
    let pct = line[..idx].split_whitespace().last().unwrap_or("N/A");
    let percent = pct.parse::<f32>().ok();
    let status = if line.contains("discharging") { "Discharging" } else { "Plugged" };
    Some(BatteryStats { display: format!("{}% ({})", pct, status), percent })
}

pub(super) fn sample_battery() -> BatteryStats {
    let stats = if cfg!(target_os = "linux") {
        read_linux_battery(std::path::Path::new("/sys/class/power_supply"))
    } else if cfg!(target_os = "macos") {
        crate::netsim::run_with_timeout("pmset", &["-g", "batt"], HOST_CMD_TIMEOUT)
            .filter(|out| out.status.success())
            .and_then(|out| parse_pmset_battery(&String::from_utf8_lossy(&out.stdout)))
    } else {
        None
    };
    stats.unwrap_or_else(|| BatteryStats { display: "N/A".to_string(), percent: None })
}

pub(super) fn parse_nvidia_gpu_hardware_info(output: &std::process::Output) -> Option<String> {
    if !output.status.success() {
        return None;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let line = stdout.lines().next()?;
    let parts: Vec<&str> = line.split(',').collect();
    if parts.len() >= 2 {
        let name = parts[0].trim();
        let mem = parts[1].trim().parse::<f32>().unwrap_or(0.0) / MIB_PER_GIB;
        Some(format!("{} ({:.1}GB)", name, mem))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cpu_percent_from_accumulated_cpu_time() {
        // A process seen for the first time (or a reused PID, which has a new
        // start time and so a new key) has no rate yet.
        assert_eq!(cpu_percent(None, 50_000, Some(2.0)), 0.0);
        // 1 s of CPU over 2 s of wall time: half a core.
        assert_eq!(cpu_percent(Some(10_000), 11_000, Some(2.0)), 50.0);
        // 3 cores busy for 1 s.
        assert_eq!(cpu_percent(Some(0), 3_000, Some(1.0)), 300.0);
        // No previous sample time, a zero interval or a counter that went
        // backwards never produce garbage.
        assert_eq!(cpu_percent(Some(0), 1_000, None), 0.0);
        assert_eq!(cpu_percent(Some(0), 1_000, Some(0.0)), 0.0);
        assert_eq!(cpu_percent(Some(2_000), 1_000, Some(1.0)), 0.0);
    }

    #[test]
    fn test_parse_diskstats_counts_whole_physical_disks_only() {
        let sys_block = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(sys_block.path().join("sda/device")).unwrap();
        std::fs::create_dir_all(sys_block.path().join("nvme0n1/device")).unwrap();
        std::fs::create_dir_all(sys_block.path().join("dm-0")).unwrap();
        // Fields: major minor name reads merged sectors_read ms writes merged
        // sectors_written ...; LVM's dm-0 sits on sda1, so only whole disks count.
        let text = "   8       0 sda 100 0 2000 0 50 0 4000 0 0 0 0\n\
                       8       1 sda1 90 0 1800 0 40 0 3000 0 0 0 0\n\
                     259       0 nvme0n1 10 0 100 0 5 0 300 0 0 0 0\n\
                     253       0 dm-0 90 0 1800 0 40 0 3000 0 0 0 0\n\
                       7       0 loop0 1 0 8 0 0 0 0 0 0 0 0\n\
                     garbage\n";
        let (read, written) = parse_diskstats(text, |dev| is_physical_disk(sys_block.path(), dev));
        assert_eq!(read, (2000 + 100) * 512);
        assert_eq!(written, (4000 + 300) * 512);
        assert_eq!(parse_diskstats("", |_| true), (0, 0));
    }

    #[test]
    fn test_parse_nvidia_smi() {
        let stdout = "30.0, 1000.0\n";
        let (gpu, mem) = parse_nvidia_smi(stdout);
        assert_eq!(gpu, Some(30.0));
        assert_eq!(mem, Some(1000.0));

        let stdout_invalid = "invalid\n";
        let (gpu, mem) = parse_nvidia_smi(stdout_invalid);
        assert_eq!(gpu, None);
        assert_eq!(mem, None);
    }

    #[test]
    fn test_parse_vmstat() {
        let stdout = "pgpgin 1000\npgpgout 2000\n";
        let (in_val, out_val) = parse_vmstat(stdout);
        assert_eq!(in_val, 1000);
        assert_eq!(out_val, 2000);
    }

    #[test]
    fn test_parse_vm_stat() {
        let stdout = "Pages paged in: 1000.\nPages paged out: 2000.\n";
        let (in_val, out_val) = parse_vm_stat(stdout);
        assert_eq!(in_val, 1000);
        assert_eq!(out_val, 2000);
    }

    #[test]
    fn test_compute_memory_stats() {
        let gib = 1024 * 1024 * 1024;
        let stats = compute_memory_stats(4 * gib, 16 * gib, gib, 4 * gib);
        assert!((stats.ram_used_gb - 4.0).abs() < 1e-6);
        assert!((stats.ram_total_gb - 16.0).abs() < 1e-6);
        assert!((stats.ram_percent - 25.0).abs() < 1e-4);
        assert!((stats.swap_used_gb - 1.0).abs() < 1e-6);
        assert!((stats.swap_total_gb - 4.0).abs() < 1e-6);
        assert!((stats.swap_percent - 25.0).abs() < 1e-4);

        let no_swap = compute_memory_stats(0, 0, 0, 0);
        assert_eq!(no_swap.ram_percent, 0.0);
        assert_eq!(no_swap.swap_percent, 0.0);
    }

    #[test]
    fn test_disk_counter_rate_and_windows_counters() {
        let mib = 1024 * 1024;
        assert_eq!(disk_counter_rate_mbs(10 * mib, 0, Some(2.0)), 0.0);
        assert!((disk_counter_rate_mbs(10 * mib, 2 * mib, Some(2.0)) - 4.0).abs() < 1e-6);
        assert_eq!(disk_counter_rate_mbs(2 * mib, 10 * mib, Some(2.0)), 0.0);
        assert_eq!(disk_counter_rate_mbs(10 * mib, 2 * mib, Some(0.0)), 0.0);
        assert_eq!(disk_counter_rate_mbs(10 * mib, 2 * mib, None), 0.0);

        let usages = [
            sysinfo::DiskUsage {
                total_read_bytes: 4 * mib,
                read_bytes: mib,
                total_written_bytes: 6 * mib,
                written_bytes: mib,
            },
            sysinfo::DiskUsage {
                total_read_bytes: 6 * mib,
                read_bytes: mib,
                total_written_bytes: 2 * mib,
                written_bytes: mib,
            },
        ];
        let (tot_r, tot_w) = sum_disk_usage_counters(usages);
        assert_eq!(tot_r, 10 * mib);
        assert_eq!(tot_w, 8 * mib);
        assert!((disk_counter_rate_mbs(tot_r, 6 * mib, Some(2.0)) - 2.0).abs() < 1e-6);
        assert!((disk_counter_rate_mbs(tot_w, 0, Some(2.0)) - 0.0).abs() < 1e-6);
        let mut disks = sysinfo::Disks::new();
        let (r1, w1) = read_sysinfo_disk_counters(&mut disks);
        let mounts_1: Vec<_> = disks.list().iter().map(|d| d.mount_point().to_path_buf()).collect();
        let (r2, w2) = read_sysinfo_disk_counters(&mut disks);
        let mounts_2: Vec<_> = disks.list().iter().map(|d| d.mount_point().to_path_buf()).collect();
        if mounts_1 == mounts_2 {
            assert!(
                r2 >= r1 && w2 >= w1,
                "cumulative disk counters regressed on unchanged mounts: ({r1}, {w1}) -> ({r2}, {w2})"
            );
        }
        assert!(disk_counter_rate_mbs(r2, r1, Some(1.0)) >= 0.0);
        assert!(disk_counter_rate_mbs(w2, w1, Some(1.0)) >= 0.0);
    }

    #[test]
    fn test_poll_cached_gpu_resets_fetching_flag_after_panic() {
        let mut failed = false;
        let cache = std::sync::Arc::new(std::sync::Mutex::new(Some(12.5_f32)));
        let fetching = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut last_fetch = None;
        let interval = std::time::Duration::from_secs(5);

        let initial = poll_cached_gpu(
            &mut failed,
            &cache,
            &fetching,
            &mut last_fetch,
            interval,
            || -> Option<f32> { panic!("simulated GPU probe panic") },
        );
        assert_eq!(initial, Some(12.5));
        for _ in 0..200 {
            if !fetching.load(std::sync::atomic::Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert!(
            !fetching.load(std::sync::atomic::Ordering::SeqCst),
            "ResetOnDrop must clear fetching after a panicking background GPU probe"
        );
        assert!(!failed);

        // Next poll after interval must be able to spawn a fresh probe and update the cache.
        last_fetch = Some(std::time::Instant::now() - interval);
        let _ = poll_cached_gpu(&mut failed, &cache, &fetching, &mut last_fetch, interval, || {
            Some(64.0)
        });
        for _ in 0..200 {
            if !fetching.load(std::sync::atomic::Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert_eq!(
            poll_cached_gpu(&mut failed, &cache, &fetching, &mut last_fetch, interval, || {
                Some(0.0)
            }),
            Some(64.0)
        );
    }

    #[test]
    fn test_gpu_output_parsers() {
        let pmon = "# gpu        pid  type    sm   mem   enc   dec   command\n\
                    # Idx          #   C/G     %     %     %     %   name\n\
                        0       1001     G    25    10     -     -   qemu-system-x86_64\n\
                        0       1001     C    15     5     -     -   qemu-system-x86_64\n\
                        0       1002     G     -     -     -     -   fishtank\n";
        let by_pid = parse_nvidia_pmon(pmon);
        assert_eq!(by_pid.get(&1001), Some(&40.0));
        assert_eq!(by_pid.get(&1002), Some(&0.0));

        assert_eq!(parse_intel_gpu_top("Render/3D: 42%\n"), Some(42.0));
        assert_eq!(parse_intel_gpu_top("no match"), None);

        assert_eq!(
            parse_mac_powermetrics_gpu("GPU HW active residency:  37.50% (100 MHz)\n"),
            Some(37.5)
        );
        assert_eq!(parse_mac_powermetrics_gpu("GPU active residency: 12.0%\n"), Some(12.0));
        assert_eq!(parse_mac_powermetrics_gpu("no match"), None);
    }

    #[test]
    fn test_thermal_paging_and_battery_helpers() {
        let dir = tempfile::tempdir().unwrap();
        let z0 = dir.path().join("thermal_zone0");
        let z1 = dir.path().join("thermal_zone1");
        let z2 = dir.path().join("thermal_zone2");
        std::fs::create_dir_all(&z0).unwrap();
        std::fs::create_dir_all(&z1).unwrap();
        std::fs::create_dir_all(&z2).unwrap();
        std::fs::write(z0.join("temp"), "45000\n").unwrap();
        std::fs::write(z1.join("temp"), "62500\n").unwrap();
        std::fs::write(z2.join("temp"), "200000\n").unwrap(); // bogus > 150 C ignored
        assert_eq!(read_max_thermal_temp(dir.path()), Some(62.5));

        let initial_paging = compute_paging_stats(Some((100, 200)), 0, 0, 2.0, "KB/s");
        assert_eq!(initial_paging.display, "Calculating...");
        let next_paging = compute_paging_stats(Some((300, 260)), 100, 200, 2.0, "KB/s");
        assert_eq!(next_paging.display, "In: 100 / Out: 30 (KB/s)");
        assert!((next_paging.in_rate - 100.0).abs() < 1e-6);
        assert!((next_paging.out_rate - 30.0).abs() < 1e-6);

        let pwr = tempfile::tempdir().unwrap();
        let bat0 = pwr.path().join("BAT0");
        std::fs::create_dir_all(&bat0).unwrap();
        std::fs::write(bat0.join("capacity"), "85\n").unwrap();
        std::fs::write(bat0.join("status"), "Discharging\n").unwrap();
        let bat = read_linux_battery(pwr.path()).unwrap();
        assert_eq!(bat.display, "85% (Discharging)");
        assert_eq!(bat.percent, Some(85.0));

        let pmset = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t78%; discharging; 3:12 remaining\n";
        let mac_bat = parse_pmset_battery(pmset).unwrap();
        assert_eq!(mac_bat.display, "78% (Discharging)");
        assert_eq!(mac_bat.percent, Some(78.0));
    }

    #[test]
    fn test_read_max_thermal_temp_ignores_non_positive_dummy_zones() {
        let dir = tempfile::tempdir().unwrap();
        let z0 = dir.path().join("thermal_zone0");
        let z1 = dir.path().join("thermal_zone1");
        std::fs::create_dir_all(&z0).unwrap();
        std::fs::create_dir_all(&z1).unwrap();
        std::fs::write(z0.join("temp"), "0\n").unwrap();
        std::fs::write(z1.join("temp"), "-273200\n").unwrap();
        assert_eq!(read_max_thermal_temp(dir.path()), None);
    }

    /// Writes `<drm>/<card>/device/gpu_busy_percent` holding `busy`.
    fn write_drm_busy(drm: &std::path::Path, card: &str, busy: &str) {
        let device = drm.join(card).join("device");
        std::fs::create_dir_all(&device).unwrap();
        std::fs::write(device.join("gpu_busy_percent"), busy).unwrap();
    }

    #[test]
    fn test_nvidia_gpu_takes_precedence_over_drm_sysfs() {
        let dir = tempfile::tempdir().unwrap();
        write_drm_busy(dir.path(), "card0", "0\n");

        let (pct, mem) = resolve_nvidia_or_drm_gpu(dir.path(), || (Some(65.0), Some(4096.0)));
        assert_eq!((pct, mem), (Some(65.0), Some(4096.0)));

        let (fallback_pct, fallback_mem) = resolve_nvidia_or_drm_gpu(dir.path(), || (None, None));
        assert_eq!((fallback_pct, fallback_mem), (Some(0.0), None));
    }

    #[test]
    fn test_drm_gpu_busy_comes_from_the_first_card_that_reports_it() {
        // card0 (an iGPU without the node) and its connector are skipped; card1 reports.
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("card0").join("device")).unwrap();
        write_drm_busy(dir.path(), "card0-DP-1", "99");
        write_drm_busy(dir.path(), "card1", "42\n");
        write_drm_busy(dir.path(), "card2", "7");
        assert_eq!(read_drm_gpu_busy(dir.path()), Some(42.0));

        // A garbled value is skipped like a missing one.
        write_drm_busy(dir.path(), "card1", "busy");
        assert_eq!(read_drm_gpu_busy(dir.path()), Some(7.0));
        assert_eq!(read_drm_gpu_busy(&dir.path().join("missing")), None);
    }

    #[test]
    fn test_macos_process_refresh_enables_disk_usage_and_uses_sample_interval() {
        let mac_kind = process_refresh_kind_for_os(false, false);
        assert!(mac_kind.disk_usage(), "macOS ProcessRefreshKind must enable disk_usage");
        let linux_kind = process_refresh_kind_for_os(true, false);
        assert!(!linux_kind.disk_usage());

        let mib = 1024 * 1024;
        // First tick: sample_interval is 0.2s (baseline window), while elapsed_secs is ~0.0s.
        let (r, w) = process_disk_delta_rate_mbs(2 * mib, 4 * mib, Some(0.2), 0.0001);
        assert!((r - 10.0).abs() < 1e-6);
        assert!((w - 20.0).abs() < 1e-6);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_zombie_netsimd_is_not_a_candidate() {
        // An exited but unreaped child stays in the process table as a zombie
        // with no memory: it must not count as a running netsimd.
        let dir = tempfile::tempdir().unwrap();
        let fake_netsimd = dir.path().join("netsimd");
        std::fs::copy("/bin/true", &fake_netsimd).unwrap();
        let mut child = crate::test_support::retry_while_text_busy(|| {
            std::process::Command::new(&fake_netsimd).spawn()
        })
        .unwrap();
        let pid = sysinfo::Pid::from_u32(child.id());

        let mut sys = System::new();
        let mut saw_zombie = false;
        for _ in 0..500 {
            refresh_processes(&mut sys);
            if sys.process(pid).is_some_and(|p| p.status() == sysinfo::ProcessStatus::Zombie) {
                saw_zombie = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let listed = candidate_processes(&sys).any(|(p, _)| *p == pid);
        child.wait().unwrap();
        assert!(saw_zombie, "the child never showed up as a zombie");
        assert!(!listed, "a zombie netsimd was counted as running");
    }

    #[test]
    fn test_paging_unsupported_counters_are_na() {
        let unsupported = compute_paging_stats(None, 100, 200, 2.0, "KB/s");
        assert_eq!(unsupported.display, "N/A");
        assert!(unsupported.in_rate.is_nan());
        assert!(unsupported.out_rate.is_nan());

        let first = compute_paging_stats(Some((100, 200)), 0, 0, 2.0, "KB/s");
        assert_eq!(first.display, "Calculating...");
    }
}
