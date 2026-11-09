mod cmdline;
mod grouping;
mod metrics;

use sysinfo::System;

pub use cmdline::*;
pub use grouping::*;
pub use metrics::*;

pub(super) const BYTES_PER_MIB: f64 = 1024.0 * 1024.0;
pub(super) const BYTES_PER_GIB: f64 = 1024.0 * 1024.0 * 1024.0;
pub(super) const MIB_PER_GIB: f32 = 1024.0;

/// Wall-clock bound for each `nvidia-smi` or host CLI call; a wedged utility
/// must not stall host sampling.
const NVIDIA_SMI_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(2);
pub(super) const HOST_CMD_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(2);
const FIRST_RUN_BASELINE_DELAY: std::time::Duration = std::time::Duration::from_millis(200);
const MAC_GPU_FETCH_INTERVAL: std::time::Duration = std::time::Duration::from_secs(10);
const WIN_GPU_FETCH_INTERVAL: std::time::Duration = std::time::Duration::from_secs(5);
const WIN_GPU_CMD_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(3);

#[derive(Clone)]
pub struct EmulatorProcess {
    pub pid: u32,
    pub port: Option<u16>,
    pub avd_name: Option<String>,
    pub cpu_usage: f32,
    pub ram_mb: f64,
    pub threads: u64,
    pub context_switches: u32,
    pub gpu_load: Option<f32>,
}

pub struct HostStats {
    pub cpu_usage: f32,
    pub ram_used_gb: f64,
    pub ram_total_gb: f64,
    pub ram_percent: f32,
    pub swap_used_gb: f64,
    pub swap_total_gb: f64,
    pub swap_percent: f32,
    pub disk_read_mbs: f64,
    pub disk_write_mbs: f64,
    pub emulators: Vec<EmulatorProcess>,
    pub netsim_cpu: f32,
    pub netsim_ram_mb: f64,
    pub netsim_count: usize,
    pub netsim_daemons: Vec<crate::netsim::NetsimDaemonProc>,
    pub emu_groups: std::collections::HashMap<String, crate::netsim::EmuGroupNetsimInfo>,
    pub gpu_percent: Option<f32>,
    pub gpu_memory_mb: Option<f32>,
    pub thermal_temp: Option<f32>,
    pub paging_str: String,
    pub paging_in: f64,
    pub paging_out: f64,
    pub battery_str: String,
    pub battery_percent: Option<f32>,
}

pub struct RealHostProvider {
    sys: System,
    last_pgin: u64,
    last_pgout: u64,
    last_paging_at: Option<std::time::Instant>,
    last_disk_read: u64,
    last_disk_write: u64,
    last_disk_at: Option<std::time::Instant>,
    intel_gpu_failed: bool,
    mac_gpu_failed: bool,
    windows_gpu_failed: bool,
    nvidia_smi_failed: bool,
    nvidia_pmon_failed: bool,
    pid_port_cache: PortCache,
    is_first_run: bool,
    mac_gpu_cache: std::sync::Arc<std::sync::Mutex<Option<f32>>>,
    mac_gpu_fetching: std::sync::Arc<std::sync::atomic::AtomicBool>,
    mac_gpu_last_fetch: Option<std::time::Instant>,
    windows_gpu_cache: std::sync::Arc<std::sync::Mutex<Option<f32>>>,
    windows_gpu_fetching: std::sync::Arc<std::sync::atomic::AtomicBool>,
    windows_gpu_last_fetch: Option<std::time::Instant>,
    #[cfg(target_os = "windows")]
    disks: sysinfo::Disks,
    /// Accumulated CPU time (ms) of each candidate process, keyed by
    /// `(pid, start time)`, at the previous sample.
    cpu_time_ms: std::collections::HashMap<(u32, u64), u64>,
    /// When the previous process sample was taken.
    last_sample_at: Option<std::time::Instant>,
}

impl Default for RealHostProvider {
    fn default() -> Self {
        Self::new(static_hardware_system())
    }
}

impl RealHostProvider {
    pub fn new(sys: System) -> Self {
        Self {
            sys,
            last_pgin: 0,
            last_pgout: 0,
            last_paging_at: None,
            last_disk_read: 0,
            last_disk_write: 0,
            last_disk_at: None,
            intel_gpu_failed: false,
            mac_gpu_failed: false,
            windows_gpu_failed: false,
            nvidia_smi_failed: false,
            nvidia_pmon_failed: false,
            pid_port_cache: std::collections::HashMap::new(),
            is_first_run: true,
            mac_gpu_cache: std::sync::Arc::new(std::sync::Mutex::new(None)),
            mac_gpu_fetching: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            mac_gpu_last_fetch: None,
            windows_gpu_cache: std::sync::Arc::new(std::sync::Mutex::new(None)),
            windows_gpu_fetching: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            windows_gpu_last_fetch: None,
            #[cfg(target_os = "windows")]
            disks: sysinfo::Disks::new(),
            cpu_time_ms: std::collections::HashMap::new(),
            last_sample_at: None,
        }
    }

    /// Refreshes CPU, memory, and process tables, taking a short baseline
    /// window on the very first run so the first reported tick already has
    /// non-zero CPU and disk rates.
    fn refresh_system(&mut self) -> Option<f64> {
        if self.is_first_run {
            self.sys.refresh_cpu_all();
            self.sys.refresh_memory();
            refresh_processes(&mut self.sys);
            self.cpu_time_ms = candidate_processes(&self.sys)
                .map(|(pid, p)| ((pid.as_u32(), p.start_time()), p.accumulated_cpu_time()))
                .collect();
            if let Some((r, w)) = self.read_cumulative_disk_counters() {
                self.last_disk_read = r;
                self.last_disk_write = w;
                self.last_disk_at = Some(std::time::Instant::now());
            }
            if let Some((pgin, pgout)) = Self::read_paging_counters() {
                self.last_pgin = pgin;
                self.last_pgout = pgout;
                self.last_paging_at = Some(std::time::Instant::now());
            }
            self.last_sample_at = Some(std::time::Instant::now());
            std::thread::sleep(FIRST_RUN_BASELINE_DELAY);
            self.is_first_run = false;
        }

        self.sys.refresh_cpu_all();
        self.sys.refresh_memory();
        refresh_processes(&mut self.sys);
        let sampled_at = std::time::Instant::now();
        let sample_interval =
            self.last_sample_at.map(|prev| sampled_at.duration_since(prev).as_secs_f64());
        self.last_sample_at = Some(sampled_at);

        let sys = &self.sys;
        self.pid_port_cache.retain(|&(pid, start_time), _| {
            sys.process(sysinfo::Pid::from_u32(pid)).is_some_and(|p| p.start_time() == start_time)
        });

        sample_interval
    }

    fn sample_memory(&self) -> MemoryStats {
        compute_memory_stats(
            self.sys.used_memory(),
            self.sys.total_memory(),
            self.sys.used_swap(),
            self.sys.total_swap(),
        )
    }

    fn collect_process_samples(&mut self, sample_interval: Option<f64>) -> Vec<ProcSample> {
        // sysinfo's cpu_usage() over-reports a process the first time it sees
        // it, so CPU% comes from our own accumulated-CPU-time deltas.
        let prev_cpu_time_ms = std::mem::take(&mut self.cpu_time_ms);
        let mut next_cpu_time_ms = std::collections::HashMap::new();
        let self_pid = std::process::id();
        let sys = &self.sys;
        let parent_of = |pid: u32| {
            sys.process(sysinfo::Pid::from_u32(pid))
                .filter(|p| p.thread_kind().is_none())
                .and_then(|p| p.parent())
                .map(sysinfo::Pid::as_u32)
        };
        let samples = candidate_processes(sys)
            .filter_map(|(pid, p)| {
                let pid_u32 = pid.as_u32();
                let parent = p.parent().map(|pp| pp.as_u32());
                if is_descendant_of_self(pid_u32, parent, self_pid, &parent_of) {
                    return None;
                }
                let key = (pid_u32, p.start_time());
                let cpu_ms = p.accumulated_cpu_time();
                next_cpu_time_ms.insert(key, cpu_ms);
                Some(ProcSample {
                    pid: pid_u32,
                    parent,
                    name: p.name().to_string_lossy().into_owned(),
                    exe: p.exe().map(|e| e.to_path_buf()),
                    cmd: p.cmd().to_vec(),
                    start_time: p.start_time(),
                    cpu: cpu_percent(prev_cpu_time_ms.get(&key).copied(), cpu_ms, sample_interval),
                    ram_mb: p.memory() as f64 / BYTES_PER_MIB,
                    uid: process_uid(p),
                })
            })
            .collect();
        self.cpu_time_ms = next_cpu_time_ms;
        samples
    }

    fn sample_processes(
        &mut self,
        samples: &[ProcSample],
        gpu_by_pid: &std::collections::HashMap<u32, f32>,
    ) -> ProcessAggregate {
        let sys = &self.sys;
        let parent_of = |pid: u32| {
            sys.process(sysinfo::Pid::from_u32(pid))
                .filter(|p| p.thread_kind().is_none())
                .and_then(|p| p.parent())
                .map(sysinfo::Pid::as_u32)
        };
        let self_uid =
            sys.process(sysinfo::Pid::from_u32(std::process::id())).and_then(process_uid);
        let environ_of = |pid: u32| {
            if cfg!(target_os = "linux") {
                crate::netsim::read_proc_environ(pid)
            } else {
                sys.process(sysinfo::Pid::from_u32(pid))
                    .map(|p| {
                        crate::netsim::parse_env_entries(
                            p.environ().iter().map(|e| e.to_string_lossy()),
                        )
                    })
                    .unwrap_or_default()
            }
        };
        let name_of = |pid: u32| {
            sys.process(sysinfo::Pid::from_u32(pid))
                .map(|p| p.name().to_string_lossy().into_owned())
        };
        let lookups = ProcessLookups {
            self_pid: std::process::id(),
            self_uid,
            gpu_by_pid,
            parent_of: &parent_of,
            listening_port: &get_listening_port,
            proc_status: &read_proc_status,
            environ_of: &environ_of,
            name_of: &name_of,
        };
        aggregate_processes(samples, &lookups, &mut self.pid_port_cache)
    }

    #[cfg(target_os = "linux")]
    fn read_cumulative_disk_counters(&mut self) -> Option<(u64, u64)> {
        let sys_block = std::path::Path::new("/sys/block");
        let counters = std::fs::read_to_string("/proc/diskstats")
            .map(|text| parse_diskstats(&text, |dev| is_physical_disk(sys_block, dev)))
            .unwrap_or_default();
        Some(counters)
    }

    #[cfg(target_os = "windows")]
    fn read_cumulative_disk_counters(&mut self) -> Option<(u64, u64)> {
        Some(read_sysinfo_disk_counters(&mut self.disks))
    }

    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    fn read_cumulative_disk_counters(&mut self) -> Option<(u64, u64)> {
        None
    }

    fn record_disk_counters(
        &mut self,
        curr_read: u64,
        curr_write: u64,
        sampled_at: std::time::Instant,
    ) -> (f64, f64) {
        let dt =
            self.last_disk_at.map(|prev| sampled_at.saturating_duration_since(prev).as_secs_f64());
        let rates = (
            disk_counter_rate_mbs(curr_read, self.last_disk_read, dt),
            disk_counter_rate_mbs(curr_write, self.last_disk_write, dt),
        );
        self.last_disk_read = curr_read;
        self.last_disk_write = curr_write;
        self.last_disk_at = Some(sampled_at);
        rates
    }

    fn sample_disk_rates(&mut self, sample_interval: Option<f64>, elapsed_secs: f64) -> (f64, f64) {
        if let Some((curr_read, curr_write)) = self.read_cumulative_disk_counters() {
            return self.record_disk_counters(curr_read, curr_write, std::time::Instant::now());
        }
        let (total_read_bytes, total_write_bytes) = self
            .sys
            .processes()
            .values()
            .map(|p| p.disk_usage())
            .fold((0u64, 0u64), |(r, w), u| (r + u.read_bytes, w + u.written_bytes));
        process_disk_delta_rate_mbs(
            total_read_bytes,
            total_write_bytes,
            sample_interval,
            elapsed_secs,
        )
    }

    fn sample_nvidia_pmon(&mut self) -> std::collections::HashMap<u32, f32> {
        if self.nvidia_smi_failed || self.nvidia_pmon_failed {
            return std::collections::HashMap::new();
        }
        let output =
            crate::netsim::run_with_timeout("nvidia-smi", &["pmon", "-c", "1"], NVIDIA_SMI_TIMEOUT);
        self.record_nvidia_pmon(output)
    }

    /// Parses `nvidia-smi pmon` output; a failed run disables only pmon.
    fn record_nvidia_pmon(
        &mut self,
        output: Option<std::process::Output>,
    ) -> std::collections::HashMap<u32, f32> {
        let Some(output) = output.filter(|out| out.status.success()) else {
            self.nvidia_pmon_failed = true;
            return std::collections::HashMap::new();
        };
        parse_nvidia_pmon(&String::from_utf8_lossy(&output.stdout))
    }

    fn sample_nvidia_gpu(&mut self) -> (Option<f32>, Option<f32>) {
        if self.nvidia_smi_failed {
            return (None, None);
        }
        let output = crate::netsim::run_with_timeout(
            "nvidia-smi",
            &["--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
            NVIDIA_SMI_TIMEOUT,
        );
        self.record_nvidia_gpu(output)
    }

    /// Parses `nvidia-smi --query-gpu` output; a failed run disables nvidia-smi entirely.
    fn record_nvidia_gpu(
        &mut self,
        output: Option<std::process::Output>,
    ) -> (Option<f32>, Option<f32>) {
        let Some(output) = output.filter(|out| out.status.success()) else {
            self.nvidia_smi_failed = true;
            self.nvidia_pmon_failed = true;
            return (None, None);
        };
        parse_nvidia_smi(&String::from_utf8_lossy(&output.stdout))
    }

    fn sample_windows_gpu(&mut self) -> Option<f32> {
        poll_cached_gpu(
            &mut self.windows_gpu_failed,
            &self.windows_gpu_cache,
            &self.windows_gpu_fetching,
            &mut self.windows_gpu_last_fetch,
            WIN_GPU_FETCH_INTERVAL,
            || {
                crate::netsim::run_with_timeout(
                    "powershell",
                    &[
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "Get-Counter '\\GPU Engine(*)\\Utilization Percentage' | Select-Object -ExpandProperty CounterSamples | Measure-Object -Property CookedValue -Maximum | Select-Object -ExpandProperty Maximum",
                    ],
                    WIN_GPU_CMD_TIMEOUT,
                )
                .filter(|out| out.status.success())
                .and_then(|out| parse_windows_gpu(&String::from_utf8_lossy(&out.stdout)))
            },
        )
    }

    fn sample_intel_gpu(&mut self) -> Option<f32> {
        if self.intel_gpu_failed {
            return None;
        }
        let Some(output) = crate::netsim::run_with_timeout(
            "intel_gpu_top",
            &["-s", "1", "-n", "1"],
            HOST_CMD_TIMEOUT,
        )
        .filter(|out| out.status.success()) else {
            self.intel_gpu_failed = true;
            return None;
        };
        let parsed = parse_intel_gpu_top(&String::from_utf8_lossy(&output.stdout));
        if parsed.is_none() {
            self.intel_gpu_failed = true;
        }
        parsed
    }

    fn sample_mac_gpu(&mut self) -> Option<f32> {
        poll_cached_gpu(
            &mut self.mac_gpu_failed,
            &self.mac_gpu_cache,
            &self.mac_gpu_fetching,
            &mut self.mac_gpu_last_fetch,
            MAC_GPU_FETCH_INTERVAL,
            || {
                crate::netsim::run_with_timeout(
                    "powermetrics",
                    &["-i", "500", "-n", "1", "--samplers", "gpu_power"],
                    HOST_CMD_TIMEOUT,
                )
                .filter(|out| out.status.success())
                .and_then(|out| parse_mac_powermetrics_gpu(&String::from_utf8_lossy(&out.stdout)))
            },
        )
    }

    fn sample_gpu(&mut self, has_emulators: bool) -> GpuStats {
        let (mut percent, memory_mb) =
            resolve_nvidia_or_drm_gpu(std::path::Path::new("/sys/class/drm"), || {
                self.sample_nvidia_gpu()
            });
        let by_pid = if has_emulators {
            self.sample_nvidia_pmon()
        } else {
            std::collections::HashMap::new()
        };

        if percent.is_none() && cfg!(target_os = "windows") {
            percent = self.sample_windows_gpu();
        }
        if percent.is_none() && cfg!(target_os = "linux") {
            percent = self.sample_intel_gpu();
        }
        if percent.is_none() && cfg!(target_os = "macos") {
            percent = self.sample_mac_gpu();
        }

        GpuStats { percent, memory_mb, by_pid }
    }

    /// Cumulative (pgin, pgout) since boot, or `None` where unsupported. The
    /// counters are never both zero on a platform that provides them.
    fn read_paging_counters() -> Option<(u64, u64)> {
        let counters = if cfg!(target_os = "linux") {
            std::fs::read_to_string("/proc/vmstat").ok().map(|s| parse_vmstat(&s))
        } else if cfg!(target_os = "macos") {
            crate::netsim::run_with_timeout("vm_stat", &[], HOST_CMD_TIMEOUT)
                .filter(|out| out.status.success())
                .map(|out| parse_vm_stat(&String::from_utf8_lossy(&out.stdout)))
        } else {
            None
        };
        counters.filter(|&(pgin, pgout)| pgin > 0 || pgout > 0)
    }

    fn record_paging_counters(
        &mut self,
        counters: Option<(u64, u64)>,
        sampled_at: std::time::Instant,
        elapsed_secs: f64,
        unit: &str,
    ) -> PagingStats {
        let dt = self
            .last_paging_at
            .map(|prev| sampled_at.saturating_duration_since(prev).as_secs_f64())
            .unwrap_or(elapsed_secs);
        let stats = compute_paging_stats(counters, self.last_pgin, self.last_pgout, dt, unit);
        if let Some((pgin, pgout)) = counters {
            self.last_pgin = pgin;
            self.last_pgout = pgout;
            self.last_paging_at = Some(sampled_at);
        }
        stats
    }

    fn sample_paging(&mut self, elapsed_secs: f64) -> PagingStats {
        let counters = Self::read_paging_counters();
        let sampled_at = std::time::Instant::now();
        let unit = if cfg!(target_os = "macos") { "pages/s" } else { "KB/s" };
        self.record_paging_counters(counters, sampled_at, elapsed_secs, unit)
    }
}

impl crate::provider::HostProvider for RealHostProvider {
    fn get_cpu_cores(&self) -> usize {
        self.sys.cpus().len()
    }

    fn get_host_stats(&mut self, elapsed_secs: f64) -> HostStats {
        let sample_interval = self.refresh_system();
        let cpu_usage = self.sys.global_cpu_usage();
        let mem = self.sample_memory();
        let proc_samples = self.collect_process_samples(sample_interval);
        let has_emulators =
            proc_samples.iter().any(|s| is_emulator_process(&s.name, s.exe.as_deref(), &s.cmd));
        let gpu = self.sample_gpu(has_emulators);
        let proc_agg = self.sample_processes(&proc_samples, &gpu.by_pid);
        let (disk_read_mbs, disk_write_mbs) = self.sample_disk_rates(sample_interval, elapsed_secs);
        let thermal_temp = sample_thermal_temp();
        let paging = self.sample_paging(elapsed_secs);
        let battery = sample_battery();

        HostStats {
            cpu_usage,
            ram_used_gb: mem.ram_used_gb,
            ram_total_gb: mem.ram_total_gb,
            ram_percent: mem.ram_percent,
            swap_used_gb: mem.swap_used_gb,
            swap_total_gb: mem.swap_total_gb,
            swap_percent: mem.swap_percent,
            disk_read_mbs,
            disk_write_mbs,
            emulators: proc_agg.emulators,
            netsim_cpu: proc_agg.netsim_cpu,
            netsim_ram_mb: proc_agg.netsim_ram_mb,
            netsim_count: proc_agg.netsim_count,
            netsim_daemons: proc_agg.netsim_daemons,
            emu_groups: proc_agg.emu_groups,
            gpu_percent: gpu.percent,
            gpu_memory_mb: gpu.memory_mb,
            thermal_temp,
            paging_str: paging.display,
            paging_in: paging.in_rate,
            paging_out: paging.out_rate,
            battery_str: battery.display,
            battery_percent: battery.percent,
        }
    }
}

fn static_hardware_system() -> System {
    System::new_with_specifics(
        sysinfo::RefreshKind::nothing()
            .with_cpu(sysinfo::CpuRefreshKind::nothing())
            .with_memory(sysinfo::MemoryRefreshKind::nothing().with_ram()),
    )
}

fn format_cpu_brand(brand: Option<&str>) -> &str {
    brand.map(str::trim).filter(|b| !b.is_empty()).unwrap_or("Unknown CPU")
}

pub fn get_static_hardware_info() -> String {
    let sys = static_hardware_system();

    let os = System::name().unwrap_or_else(|| "Unknown OS".to_string());
    let cpu = format_cpu_brand(sys.cpus().first().map(|c| c.brand()));
    let ram = sys.total_memory() as f64 / BYTES_PER_GIB;

    let gpu_str = crate::netsim::run_with_timeout(
        "nvidia-smi",
        &["--query-gpu=gpu_name,memory.total", "--format=csv,noheader,nounits"],
        NVIDIA_SMI_TIMEOUT,
    )
    .as_ref()
    .and_then(parse_nvidia_gpu_hardware_info)
    .unwrap_or_else(|| "N/A".to_string());

    format!("{} | {} | {:.1} GB RAM | GPU: {}", os, cpu, ram, gpu_str)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_real_host_provider_new_defaults() {
        let prov = RealHostProvider::new(System::new());
        assert_eq!(prov.last_pgin, 0);
        assert_eq!(prov.last_pgout, 0);
        assert!(prov.last_paging_at.is_none());
        assert_eq!(prov.last_disk_read, 0);
        assert_eq!(prov.last_disk_write, 0);
        assert!(prov.last_disk_at.is_none());
        assert!(!prov.intel_gpu_failed);
        assert!(!prov.mac_gpu_failed);
        assert!(!prov.windows_gpu_failed);
        assert!(!prov.nvidia_smi_failed);
        assert!(!prov.nvidia_pmon_failed);
        assert!(prov.pid_port_cache.is_empty());
        assert!(prov.is_first_run);
        assert!(prov.mac_gpu_cache.lock().unwrap().is_none());
        assert!(!prov.mac_gpu_fetching.load(std::sync::atomic::Ordering::SeqCst));
        assert!(prov.mac_gpu_last_fetch.is_none());
        assert!(prov.windows_gpu_cache.lock().unwrap().is_none());
        assert!(!prov.windows_gpu_fetching.load(std::sync::atomic::Ordering::SeqCst));
        assert!(prov.windows_gpu_last_fetch.is_none());
        assert!(prov.cpu_time_ms.is_empty());
        assert!(prov.last_sample_at.is_none());
    }

    #[test]
    fn test_windows_gpu_background_cache_throttles_and_latches_failure() {
        assert_eq!(parse_windows_gpu(" 37.5 \r\n"), Some(37.5));
        assert_eq!(parse_windows_gpu("12,5\n"), Some(12.5));
        assert_eq!(parse_windows_gpu("NaN\n"), None);
        assert_eq!(parse_windows_gpu(""), None);

        let mut failed = false;
        let cache = std::sync::Arc::new(std::sync::Mutex::new(None));
        let fetching = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut last_fetch = None;
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));

        let c1 = calls.clone();
        let _ = poll_cached_gpu(
            &mut failed,
            &cache,
            &fetching,
            &mut last_fetch,
            WIN_GPU_FETCH_INTERVAL,
            move || {
                c1.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                Some(42.5)
            },
        );
        for _ in 0..200 {
            if !fetching.load(std::sync::atomic::Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert!(!failed);
        assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);

        // Subsequent polls within WIN_GPU_FETCH_INTERVAL return the cached reading without spawning.
        let c2 = calls.clone();
        let cached = poll_cached_gpu(
            &mut failed,
            &cache,
            &fetching,
            &mut last_fetch,
            WIN_GPU_FETCH_INTERVAL,
            move || {
                c2.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                Some(99.0)
            },
        );
        assert_eq!(cached, Some(42.5));
        assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);

        // Once the throttle window elapses, a failing refresh stores NaN and latches failed on the next poll.
        last_fetch = Some(std::time::Instant::now() - WIN_GPU_FETCH_INTERVAL);
        let _ = poll_cached_gpu(
            &mut failed,
            &cache,
            &fetching,
            &mut last_fetch,
            WIN_GPU_FETCH_INTERVAL,
            || None,
        );
        for _ in 0..200 {
            if !fetching.load(std::sync::atomic::Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert_eq!(
            poll_cached_gpu(
                &mut failed,
                &cache,
                &fetching,
                &mut last_fetch,
                WIN_GPU_FETCH_INTERVAL,
                || Some(1.0)
            ),
            None
        );
        assert!(failed);

        let mut prov = RealHostProvider::new(System::new());
        prov.windows_gpu_last_fetch = Some(std::time::Instant::now());
        *prov.windows_gpu_cache.lock().unwrap() = Some(33.5);
        assert_eq!(prov.sample_windows_gpu(), Some(33.5));
        assert!(!prov.windows_gpu_failed);
        *prov.windows_gpu_cache.lock().unwrap() = Some(f32::NAN);
        assert_eq!(prov.sample_windows_gpu(), None);
        assert!(prov.windows_gpu_failed);
    }

    #[test]
    fn test_gpu_and_hardware_cli_failure_handling() {
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            let failed_out = std::process::Output {
                status: std::process::ExitStatus::from_raw(256),
                stdout: b"NVIDIA-SMI has failed, driver not loaded\n".to_vec(),
                stderr: Vec::new(),
            };
            assert_eq!(parse_nvidia_gpu_hardware_info(&failed_out), None);
        }
        assert_eq!(parse_intel_gpu_top("Render/3D/0: 37.5%\n"), Some(37.5));

        let mut prov = RealHostProvider::new(System::new());
        *prov.mac_gpu_cache.lock().unwrap() = Some(f32::NAN);
        assert_eq!(prov.sample_mac_gpu(), None);
        assert!(prov.mac_gpu_failed);
    }

    #[test]
    fn test_static_hardware_system_skips_process_table_scan() {
        let sys = static_hardware_system();
        assert!(
            sys.processes().is_empty(),
            "static_hardware_system should not scan all processes (found {})",
            sys.processes().len()
        );
        assert!(sys.total_memory() > 0);
        assert!(!sys.cpus().is_empty());
    }

    #[test]
    fn test_nvidia_pmon_failure_does_not_disable_nvidia_gpu_query() {
        let ok = |stdout: &str| std::process::Output {
            status: std::process::ExitStatus::default(),
            stdout: stdout.as_bytes().to_vec(),
            stderr: Vec::new(),
        };
        let mut prov = RealHostProvider::new(System::new());
        let by_pid = prov.record_nvidia_pmon(Some(ok(
            "    0       1234     C    30    10     0     0   qemu-system-x86\n",
        )));
        assert_eq!(by_pid.get(&1234), Some(&30.0));
        assert!(!prov.nvidia_pmon_failed);

        assert!(prov.record_nvidia_pmon(None).is_empty());
        assert!(prov.nvidia_pmon_failed);
        assert!(
            !prov.nvidia_smi_failed,
            "a failed or skipped pmon call must not latch nvidia_smi_failed and disable --query-gpu"
        );
        assert_eq!(prov.record_nvidia_gpu(Some(ok("30, 1000\n"))), (Some(30.0), Some(1000.0)));
        assert!(!prov.nvidia_smi_failed);

        let mut prov = RealHostProvider::new(System::new());
        assert_eq!(prov.record_nvidia_gpu(None), (None, None));
        assert!(prov.nvidia_smi_failed);
        assert!(prov.nvidia_pmon_failed, "a failed --query-gpu also disables pmon");
    }

    #[test]
    fn test_real_host_provider_default_skips_process_table_scan() {
        let prov = RealHostProvider::default();
        assert!(
            prov.sys.processes().is_empty(),
            "RealHostProvider::default() should not scan all processes before refresh_system (found {})",
            prov.sys.processes().len()
        );
        assert!(!prov.sys.cpus().is_empty());
        assert_eq!(format_cpu_brand(None), "Unknown CPU");
        assert_eq!(format_cpu_brand(Some("   ")), "Unknown CPU");
        assert_eq!(format_cpu_brand(Some(" Apple M3 Max ")), "Apple M3 Max");
    }

    #[test]
    fn test_disk_and_paging_counter_rates_use_counter_timestamps() {
        let mib = 1024 * 1024;
        let t0 = std::time::Instant::now();
        let t1 = t0 + std::time::Duration::from_millis(1200);
        let t2 = t0 + std::time::Duration::from_millis(2000);
        let t3 = t0 + std::time::Duration::from_millis(3000);
        let t4 = t0 + std::time::Duration::from_millis(4000);

        let mut prov = RealHostProvider::new(System::new());
        // Baseline snapshot at t0 (before 200ms baseline sleep + 1.0s GPU CLI).
        assert_eq!(prov.record_disk_counters(10 * mib, 20 * mib, t0), (0.0, 0.0));
        let first_pg = prov.record_paging_counters(Some((1000, 2000)), t0, 0.2, "KB/s");
        assert_eq!(first_pg.display, "Calculating...");

        // Tick 1 counter read at t0 + 1.2s divides by 1.2s (not refresh_system's 0.2s).
        let (r1, w1) = prov.record_disk_counters(22 * mib, 44 * mib, t1);
        assert!((r1 - 10.0).abs() < 1e-6);
        assert!((w1 - 20.0).abs() < 1e-6);
        let pg1 = prov.record_paging_counters(Some((1120, 2240)), t1, 0.2, "KB/s");
        assert!((pg1.in_rate - 100.0).abs() < 1e-6);
        assert!((pg1.out_rate - 200.0).abs() < 1e-6);

        // Tick 2 at t0 + 2.0s (after GPU CLI latches off) divides by the true 0.8s gap
        // rather than refresh_system's 1.8s sample_interval.
        let (r2, w2) = prov.record_disk_counters(30 * mib, 52 * mib, t2);
        assert!((r2 - 10.0).abs() < 1e-6);
        assert!((w2 - 10.0).abs() < 1e-6);
        let pg2 = prov.record_paging_counters(Some((1200, 2320)), t2, 1.8, "KB/s");
        assert!((pg2.in_rate - 100.0).abs() < 1e-6);
        assert!((pg2.out_rate - 100.0).abs() < 1e-6);

        // A transient None paging sample preserves last_paging_at = t2 so recovery at t4
        // divides the 2-tick delta by t4 - t2 = 2.0s.
        let pg_miss = prov.record_paging_counters(None, t3, 1.0, "pages/s");
        assert_eq!(pg_miss.display, "N/A");
        assert_eq!(prov.last_paging_at, Some(t2));
        let pg_rec = prov.record_paging_counters(Some((1400, 2720)), t4, 1.0, "pages/s");
        assert!((pg_rec.in_rate - 100.0).abs() < 1e-6);
        assert!((pg_rec.out_rate - 200.0).abs() < 1e-6);
    }
}
