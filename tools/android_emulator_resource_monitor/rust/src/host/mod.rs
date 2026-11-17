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
const MAC_PROC_STATUS_INTERVAL: std::time::Duration = std::time::Duration::from_secs(2);
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
    #[cfg(target_os = "macos")]
    mac_disk_ioreg_failed: bool,
    nvidia_smi_failed: bool,
    nvidia_pmon_failed: bool,
    pid_port_cache: PortCache,
    is_first_run: bool,
    mac_gpu: TimedProbeCache<(Option<f32>, Option<f32>)>,
    mac_proc_status: TimedProbeCache<std::collections::HashMap<u32, (u64, u32)>>,
    windows_gpu: TimedProbeCache<f32>,
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
            #[cfg(target_os = "macos")]
            mac_disk_ioreg_failed: false,
            nvidia_smi_failed: false,
            nvidia_pmon_failed: false,
            pid_port_cache: std::collections::HashMap::new(),
            is_first_run: true,
            mac_gpu: TimedProbeCache::default(),
            mac_proc_status: TimedProbeCache::default(),
            windows_gpu: TimedProbeCache::default(),
            #[cfg(target_os = "windows")]
            disks: sysinfo::Disks::new(),
            cpu_time_ms: std::collections::HashMap::new(),
            last_sample_at: None,
        }
    }

    /// Refreshes CPU, memory, and process tables, taking a short baseline
    /// window on the very first run so the first reported tick already has
    /// non-zero CPU and disk rates.
    fn refresh_system(&mut self) -> (Option<f64>, bool) {
        let was_first_run = self.is_first_run;
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
            // Pre-warm async macOS ioreg and `top` probes before the 200ms baseline
            // sleep so `--once` observes live GPU and process status on the first tick.
            if cfg!(target_os = "macos") {
                let _ = self.sample_mac_gpu();
                let emu_pids = self.current_emulator_pids();
                let _ = self.poll_mac_proc_status(emu_pids, false);
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

        (sample_interval, was_first_run)
    }

    fn parent_pid(sys: &System, pid: u32) -> Option<u32> {
        sys.process(sysinfo::Pid::from_u32(pid))
            .filter(|p| p.thread_kind().is_none())
            .and_then(|p| p.parent())
            .map(sysinfo::Pid::as_u32)
    }

    fn non_self_candidates(
        sys: &System,
    ) -> impl Iterator<Item = (u32, Option<u32>, &sysinfo::Process)> {
        let self_pid = std::process::id();
        candidate_processes(sys).filter_map(move |(pid, p)| {
            let pid_u32 = pid.as_u32();
            let parent = p.parent().map(sysinfo::Pid::as_u32);
            (!is_descendant_of_self(pid_u32, parent, self_pid, &|id| Self::parent_pid(sys, id)))
                .then_some((pid_u32, parent, p))
        })
    }

    fn current_emulator_pids(&self) -> Vec<u32> {
        Self::non_self_candidates(&self.sys)
            .filter(|(_, _, p)| is_emulator_process(&p.name().to_string_lossy(), p.exe(), p.cmd()))
            .map(|(pid, _, _)| pid)
            .collect()
    }

    fn poll_mac_proc_status(
        &mut self,
        mut emu_pids: Vec<u32>,
        wait_initial: bool,
    ) -> std::collections::HashMap<u32, (u64, u32)> {
        if emu_pids.is_empty() {
            self.mac_proc_status.clear();
            return std::collections::HashMap::new();
        }
        emu_pids.sort_unstable();
        emu_pids.dedup();
        let mut status = self
            .mac_proc_status
            .poll(MAC_PROC_STATUS_INTERVAL, move || read_mac_top_proc_status(&emu_pids));
        if wait_initial && status.is_none() {
            status = self.mac_proc_status.wait_initial();
        }
        status.unwrap_or_default()
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
        let samples = Self::non_self_candidates(&self.sys)
            .map(|(pid_u32, parent, p)| {
                let key = (pid_u32, p.start_time());
                let cpu_ms = p.accumulated_cpu_time();
                next_cpu_time_ms.insert(key, cpu_ms);
                ProcSample {
                    pid: pid_u32,
                    parent,
                    name: p.name().to_string_lossy().into_owned(),
                    exe: p.exe().map(|e| e.to_path_buf()),
                    cmd: p.cmd().to_vec(),
                    start_time: p.start_time(),
                    cpu: cpu_percent(prev_cpu_time_ms.get(&key).copied(), cpu_ms, sample_interval),
                    ram_mb: p.memory() as f64 / BYTES_PER_MIB,
                    uid: process_uid(p),
                }
            })
            .collect();
        self.cpu_time_ms = next_cpu_time_ms;
        samples
    }

    fn sample_processes(
        &mut self,
        samples: &[ProcSample],
        gpu_by_pid: &std::collections::HashMap<u32, f32>,
        wait_initial_proc_status: bool,
    ) -> ProcessAggregate {
        let mac_statuses = if cfg!(target_os = "macos") {
            let emu_pids = samples
                .iter()
                .filter(|s| is_emulator_process(&s.name, s.exe.as_deref(), &s.cmd))
                .map(|s| s.pid)
                .collect();
            self.poll_mac_proc_status(emu_pids, wait_initial_proc_status)
        } else {
            std::collections::HashMap::new()
        };
        let mac_proc_status = |pid: u32| mac_statuses.get(&pid).copied().unwrap_or((0, 0));
        let proc_status: &dyn Fn(u32) -> (u64, u32) =
            if cfg!(target_os = "macos") { &mac_proc_status } else { &read_proc_status };

        let sys = &self.sys;
        let parent_of = |pid: u32| Self::parent_pid(sys, pid);
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
            proc_status,
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

    #[cfg(target_os = "macos")]
    fn read_cumulative_disk_counters(&mut self) -> Option<(u64, u64)> {
        if self.mac_disk_ioreg_failed {
            return None;
        }
        let counters = ioreg_class("IOBlockStorageDriver")
            .and_then(|stdout| parse_ioreg_disk_counters(&stdout));
        self.mac_disk_ioreg_failed = counters.is_none();
        counters
    }

    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
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
        self.windows_gpu.poll(WIN_GPU_FETCH_INTERVAL, || {
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
        })
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

    /// GPU load in percent and GPU memory in MiB. `ioreg` reports both to any
    /// user; `powermetrics`, which needs root, is the fallback for the load.
    fn sample_mac_gpu(&mut self) -> (Option<f32>, Option<f32>) {
        self.mac_gpu
            .poll(MAC_GPU_FETCH_INTERVAL, || {
                ioreg_class("IOAccelerator")
                    .map(|stdout| parse_mac_ioreg_gpu(&stdout))
                    .filter(|(percent, memory_mb)| percent.is_some() || memory_mb.is_some())
                    .or_else(|| Some((Some(powermetrics_gpu_percent()?), None)))
            })
            .unwrap_or((None, None))
    }

    fn sample_gpu(&mut self, has_emulators: bool) -> GpuStats {
        let (mut percent, mut memory_mb) =
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
            let (mac_percent, mac_memory_mb) = self.sample_mac_gpu();
            percent = mac_percent;
            memory_mb = memory_mb.or(mac_memory_mb);
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
        let (sample_interval, was_first_run) = self.refresh_system();
        let cpu_usage = self.sys.global_cpu_usage();
        let mem = self.sample_memory();
        let proc_samples = self.collect_process_samples(sample_interval);
        let has_emulators =
            proc_samples.iter().any(|s| is_emulator_process(&s.name, s.exe.as_deref(), &s.cmd));
        let gpu = self.sample_gpu(has_emulators);
        let proc_agg = self.sample_processes(&proc_samples, &gpu.by_pid, was_first_run);
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

/// Stdout of `ioreg` listing the registry entries of `class` with their properties.
fn ioreg_class(class: &str) -> Option<String> {
    crate::netsim::run_with_timeout(
        "ioreg",
        &["-r", "-d", "1", "-w", "0", "-c", class],
        HOST_CMD_TIMEOUT,
    )
    .filter(|out| out.status.success())
    .map(|out| String::from_utf8_lossy(&out.stdout).into_owned())
}

fn powermetrics_gpu_percent() -> Option<f32> {
    crate::netsim::run_with_timeout(
        "powermetrics",
        &["-i", "500", "-n", "1", "--samplers", "gpu_power"],
        HOST_CMD_TIMEOUT,
    )
    .filter(|out| out.status.success())
    .and_then(|out| parse_mac_powermetrics_gpu(&String::from_utf8_lossy(&out.stdout)))
}

/// GPU models of a Mac, such as `Apple M4 Pro (20-core)`.
fn mac_gpu_models() -> Option<String> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    let models = parse_mac_ioreg_gpu_models(&ioreg_class("IOAccelerator")?);
    (!models.is_empty()).then(|| models.join(", "))
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

/// The OS name of the hardware line. macOS reads `Darwin <kernel version>`, as
/// in the Python monitor.
fn format_static_os_name(name: Option<&str>, kernel_version: Option<&str>) -> String {
    match (name, kernel_version) {
        (Some("Darwin"), Some(kernel)) => format!("Darwin {kernel}"),
        (Some(name), _) => name.to_string(),
        (None, _) => "Unknown OS".to_string(),
    }
}

pub fn get_static_hardware_info() -> String {
    let sys = static_hardware_system();

    let os = format_static_os_name(System::name().as_deref(), System::kernel_version().as_deref());
    let cpu = format_cpu_brand(sys.cpus().first().map(|c| c.brand()));
    let ram = sys.total_memory() as f64 / BYTES_PER_GIB;

    let gpu_str = crate::netsim::run_with_timeout(
        "nvidia-smi",
        &["--query-gpu=gpu_name,memory.total", "--format=csv,noheader,nounits"],
        NVIDIA_SMI_TIMEOUT,
    )
    .as_ref()
    .and_then(parse_nvidia_gpu_hardware_info)
    .or_else(mac_gpu_models)
    .unwrap_or_else(|| "N/A".to_string());

    format!("{} | {} | {:.1} GB RAM | GPU: {}", os, cpu, ram, gpu_str)
}

#[cfg(test)]
#[path = "../../tests/host/host_test.rs"]
mod tests;
