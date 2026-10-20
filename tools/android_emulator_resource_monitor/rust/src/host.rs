use sysinfo::System;
use std::ffi::OsString;

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
    pub gpu_percent: Option<f32>,
    pub gpu_memory_mb: Option<f32>,
    pub thermal_temp: Option<f32>,
    pub paging_str: String,
    pub paging_in: f64,
    pub paging_out: f64,
    pub battery_str: String,
    pub battery_percent: Option<f32>,
}


fn extract_device_prop(s: &str, key: &str) -> Option<String> {
    if let Some(pos) = s.find(key) {
        let rest = &s[pos + key.len()..];
        let val = if let Some(comma_pos) = rest.find(',') {
            &rest[..comma_pos]
        } else {
            rest
        };
        let cleaned = val.trim_matches(|c| c == '\'' || c == '"');
        if !cleaned.is_empty() {
            return Some(cleaned.to_string());
        }
    }
    None
}

pub fn parse_cmdline(cmdline: &[OsString]) -> (Option<u16>, Option<String>) {
    let mut port = None;
    let mut avd_name = None;

    for i in 0..cmdline.len() {
        let arg = cmdline[i].to_str().unwrap_or("");
        if (arg == "-port" || arg == "-ports" || arg == "-fishtank") && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("");
            let val = next_arg.split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                port = Some(p);
            }
        } else if arg.starts_with("-port=") || arg.starts_with("-ports=") {
            let val = arg.split('=').nth(1).unwrap_or("").split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                port = Some(p);
            }
        } else if arg.starts_with("-fishtank=") {
            let val = arg.split('=').nth(1).unwrap_or("").split(',').next().unwrap_or("");
            if let Ok(p) = val.parse::<u16>() {
                port = Some(p);
            }
        } else if (arg == "-avd" || arg == "-name") && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("").trim_matches(|c| c == '\'' || c == '"');
            let next_clean = if let Some(stripped) = next_arg.strip_prefix("guest=") {
                stripped
            } else {
                next_arg
            };
            let val = next_clean.split(',').next().unwrap_or("");
            if !val.is_empty() {
                avd_name = Some(val.to_string());
            }
        } else if let Some(stripped) = arg.strip_prefix("-name=") {
            let next_clean = stripped.trim_matches(|c| c == '\'' || c == '"');
            let next_clean = if let Some(s) = next_clean.strip_prefix("guest=") {
                s
            } else {
                next_clean
            };
            let val = next_clean.split(',').next().unwrap_or("");
            if !val.is_empty() {
                avd_name = Some(val.to_string());
            }
        } else if let Some(idx) = arg.find("-avd") {
            if !arg.starts_with("-avd_name=") {
                let rest = arg[idx + 4..].trim_matches(|c| c == '\'' || c == '"');
                if let Some(end_idx) = rest.find('-') {
                    avd_name = Some(rest[..end_idx].to_string());
                } else if !rest.is_empty() {
                    avd_name = Some(rest.to_string());
                }
            }
        } else if arg.starts_with('@') && arg.len() > 1 {
            let val = arg[1..].trim_matches(|c| c == '\'' || c == '"');
            if !val.is_empty() {
                avd_name = Some(val.to_string());
            }
        }

        if let Some(val) = extract_device_prop(arg, "serial_number=") {
            if let Ok(p) = val.parse::<u16>() {
                port = Some(p);
            }
        }
        if let Some(val) = extract_device_prop(arg, "host_port=") {
            if let Ok(hp) = val.parse::<u16>() {
                if hp > 0 {
                    port = Some(hp - 1);
                }
            }
        }
        if let Some(val) = extract_device_prop(arg, "avd_name=") {
            avd_name = Some(val);
        }

        if arg == "-device" && i + 1 < cmdline.len() {
            let next_arg = cmdline[i + 1].to_str().unwrap_or("");
            if let Some(val) = extract_device_prop(next_arg, "serial_number=") {
                if let Ok(p) = val.parse::<u16>() {
                    port = Some(p);
                }
            }
            if let Some(val) = extract_device_prop(next_arg, "host_port=") {
                if let Ok(hp) = val.parse::<u16>() {
                    if hp > 0 {
                        port = Some(hp - 1);
                    }
                }
            }
            if let Some(val) = extract_device_prop(next_arg, "avd_name=") {
                avd_name = Some(val);
            }
        }
    }

    (port, avd_name)
}

#[cfg(target_os = "linux")]
fn get_socket_inodes(pid: u32) -> Vec<u64> {
    let mut inodes = Vec::new();
    if let Ok(entries) = std::fs::read_dir(format!("/proc/{}/fd", pid)) {
        for entry in entries {
            if let Ok(entry) = entry {
                if let Ok(link) = std::fs::read_link(entry.path()) {
                    let link_str = link.to_string_lossy();
                    if link_str.starts_with("socket:[") && link_str.ends_with("]") {
                        let inode_str = &link_str[8..link_str.len() - 1];
                        if let Ok(inode) = inode_str.parse::<u64>() {
                            inodes.push(inode);
                        }
                    }
                }
            }
        }
    }
    inodes
}

#[cfg(target_os = "linux")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let inodes = get_socket_inodes(pid);
    if let Ok(tcp) = std::fs::read_to_string("/proc/net/tcp") {
        for line in tcp.lines().skip(1) {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 10 && parts[3] == "0A" {
                let inode = parts[9].parse::<u64>().unwrap_or(0);
                if inodes.contains(&inode) {
                    let local = parts[1];
                    if let Some(port_hex) = local.split(':').nth(1) {
                        if let Ok(port) = u16::from_str_radix(port_hex, 16) {
                            if port >= 5554 && port <= 5584 && port % 2 == 0 {
                                return Some(port);
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

#[cfg(target_os = "macos")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let output = std::process::Command::new("lsof")
        .args(&["-nP", "-iTCP", "-sTCP:LISTEN", "-p", &pid.to_string()])
        .output()
        .ok()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if line.contains("LISTEN") {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 9 {
                let addr = parts[8];
                if let Some(port_str) = addr.split(':').last() {
                    if let Ok(port) = port_str.parse::<u16>() {
                        if port >= 5554 && port <= 5584 && port % 2 == 0 {
                            return Some(port);
                        }
                    }
                }
            }
        }
    }
    None
}

#[cfg(target_os = "windows")]
pub fn get_listening_port(pid: u32) -> Option<u16> {
    let output = std::process::Command::new("netstat")
        .args(&["-ano"])
        .output()
        .ok()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if line.contains("LISTENING") && line.contains(&pid.to_string()) {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 5 {
                let addr = parts[1];
                if let Some(port_str) = addr.split(':').last() {
                    if let Ok(port) = port_str.parse::<u16>() {
                        if port >= 5554 && port <= 5584 && port % 2 == 0 {
                            return Some(port);
                        }
                    }
                }
            }
        }
    }
    None
}
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
            pgin = line.split_whitespace().last().and_then(|s| s.trim_end_matches('.').parse::<u64>().ok()).unwrap_or(0);
        } else if line.contains("Pages paged out:") || line.starts_with("Pageouts:") {
            pgout = line.split_whitespace().last().and_then(|s| s.trim_end_matches('.').parse::<u64>().ok()).unwrap_or(0);
        }
    }
    (pgin, pgout)
}


pub struct RealHostProvider {
    pub sys: System,
    pub last_pgin: u64,
    pub last_pgout: u64,
    pub last_disk_read: u64,
    pub last_disk_write: u64,
    pub intel_gpu_failed: bool,
    pub mac_gpu_failed: bool,
    pub pid_port_cache: std::collections::HashMap<(u32, u64), (Option<u16>, Option<String>)>,
    pub is_first_run: bool,
    pub mac_gpu_cache: std::sync::Arc<std::sync::Mutex<Option<f32>>>,
    pub mac_gpu_fetching: std::sync::Arc<std::sync::atomic::AtomicBool>,
    pub mac_gpu_last_fetch: Option<std::time::Instant>,
}


impl crate::provider::HostProvider for RealHostProvider {
    fn get_cpu_cores(&self) -> usize {
        self.sys.cpus().len()
    }

    fn get_host_stats(&mut self, elapsed_secs: f64) -> HostStats {
        let sys = &mut self.sys;

        if self.is_first_run {
            sys.refresh_cpu_all();
            sys.refresh_memory();
            std::thread::sleep(std::time::Duration::from_millis(200));
            self.is_first_run = false;
        }

        sys.refresh_cpu_all();
        sys.refresh_memory();

        sys.refresh_processes_specifics(
            sysinfo::ProcessesToUpdate::All,
            true,
            sysinfo::ProcessRefreshKind::nothing()
                .with_cpu()
                .with_memory()
                .with_cmd(sysinfo::UpdateKind::Always)
                .with_exe(sysinfo::UpdateKind::Always),
        );

    let current_keys: std::collections::HashSet<(u32, u64)> = sys.processes().iter().map(|(p, proc)| (p.as_u32(), proc.start_time())).collect();
    self.pid_port_cache.retain(|k, _| current_keys.contains(k));

    let cpu_usage = sys.global_cpu_usage();

    let total_mem = sys.total_memory();
    let used_mem = sys.used_memory();

    let ram_used_gb = used_mem as f64 / (1024.0 * 1024.0 * 1024.0);
    let ram_total_gb = total_mem as f64 / (1024.0 * 1024.0 * 1024.0);
    let ram_percent = (used_mem as f32 / total_mem as f32) * 100.0;

    let total_swap = sys.total_swap();
    let used_swap = sys.used_swap();

    let swap_used_gb = used_swap as f64 / (1024.0 * 1024.0 * 1024.0);
    let swap_total_gb = total_swap as f64 / (1024.0 * 1024.0 * 1024.0);
    let swap_percent = if total_swap > 0 { (used_swap as f32 / total_swap as f32) * 100.0 } else { 0.0 };

    #[allow(unused_variables, unused_mut)]
    let mut total_read_bytes = 0;
    #[allow(unused_variables, unused_mut)]
    let mut total_write_bytes = 0;

    let mut emu_gpu_stats = std::collections::HashMap::new();
    if let Ok(output) = std::process::Command::new("nvidia-smi")
        .args(&["pmon", "-c", "1"])
        .output() {
        let stdout = String::from_utf8_lossy(&output.stdout);
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
                    *emu_gpu_stats.entry(pid).or_insert(0.0) += sm_val;
                }
            }
        }
    }

    let mut accumulated_emus: std::collections::HashMap<u16, EmulatorProcess> = std::collections::HashMap::new();
    let mut max_metrics_per_port: std::collections::HashMap<u16, (f32, f64)> = std::collections::HashMap::new();
    let mut unmapped_emus: Vec<EmulatorProcess> = Vec::new();
    let mut netsim_cpu = 0.0;
    let mut netsim_ram_mb = 0.0;
    let mut netsim_count = 0;

    for (pid, process) in sys.processes() {
        #[cfg(not(any(target_os = "linux", target_os = "windows")))]
        {
            let usage = process.disk_usage();
            total_read_bytes += usage.read_bytes;
            total_write_bytes += usage.written_bytes;
        }

        let name = process.name().to_string_lossy().to_lowercase();
        let is_emu = name.contains("qemu") || name.contains("emulator") || name.contains("crosvm") || name.contains("fishtank");
        let is_netsim = name.contains("netsim");
        let is_excluded = name.contains("terminal") || name.contains("crash");

        if (is_emu || is_netsim) && !is_excluded {
            let cpu = process.cpu_usage();
            let ram = process.memory() as f64 / (1024.0 * 1024.0);

            if is_netsim {
                netsim_cpu += cpu;
                netsim_ram_mb += ram;
                netsim_count += 1;
            } else {
                let cache_key = (pid.as_u32(), process.start_time());
                let (port, avd_name) = if let Some(cached) = self.pid_port_cache.get(&cache_key) {
                    cached.clone()
                } else {
                    let cmdline = process.cmd();
                    let (mut p, avd) = parse_cmdline(cmdline);
                    if p.is_none() {
                        p = get_listening_port(pid.as_u32());
                    }
                    if p.is_some() {
                        self.pid_port_cache.insert(cache_key, (p, avd.clone()));
                    }
                    (p, avd)
                };

                let (threads, context_switches) = {
                    #[cfg(target_os = "linux")]
                    {
                        let mut vol = 0;
                        let mut nonvol = 0;
                        let mut threads = 0;

                        if let Ok(status) = std::fs::read_to_string(format!("/proc/{}/status", pid.as_u32())) {
                            for line in status.lines() {
                                if line.starts_with("Threads:") {
                                    threads = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
                                } else if line.starts_with("voluntary_ctxt_switches:") {
                                    vol = line.split_whitespace().nth(1).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                                } else if line.starts_with("nonvoluntary_ctxt_switches:") {
                                    nonvol = line.split_whitespace().nth(1).and_then(|s| s.parse::<u32>().ok()).unwrap_or(0);
                                }
                            }
                        }

                        (threads, vol + nonvol)
                    }
                    #[cfg(not(target_os = "linux"))]
                    {
                        (0, 0)
                    }
                };

                let emu = EmulatorProcess {
                    pid: pid.as_u32(),
                    port,
                    avd_name: avd_name.clone(),
                    cpu_usage: cpu,
                    ram_mb: ram,
                    threads,
                    context_switches,
                    gpu_load: emu_gpu_stats.get(&pid.as_u32()).cloned(),
                };

                if let Some(p) = port {
                    let (prev_max_cpu, prev_max_ram) = *max_metrics_per_port.get(&p).unwrap_or(&(-1.0, -1.0));
                    let is_new_max = cpu > prev_max_cpu || (cpu == prev_max_cpu && emu.ram_mb > prev_max_ram);
                    if is_new_max {
                        max_metrics_per_port.insert(p, (cpu, emu.ram_mb));
                    }

                    accumulated_emus.entry(p)
                        .and_modify(|e| {
                            e.cpu_usage += emu.cpu_usage;
                            e.ram_mb += emu.ram_mb;
                            e.threads += emu.threads;
                            e.context_switches += emu.context_switches;
                            if is_new_max {
                                e.pid = emu.pid;
                                if emu.avd_name.is_some() {
                                    e.avd_name = emu.avd_name.clone();
                                }
                            } else if e.avd_name.is_none() && emu.avd_name.is_some() {
                                e.avd_name = emu.avd_name.clone();
                            }
                            if let Some(g) = emu.gpu_load {
                                e.gpu_load = Some(e.gpu_load.unwrap_or(0.0) + g);
                            }
                        })
                        .or_insert_with(|| {
                            max_metrics_per_port.insert(p, (cpu, emu.ram_mb));
                            emu.clone()
                        });
                } else {
                    unmapped_emus.push(emu);
                }
            }
        }
    }

    let num_cores = sys.cpus().len().max(1) as f32;
    let mut emulators: Vec<EmulatorProcess> = accumulated_emus.into_values()
        .map(|mut e| {
            e.cpu_usage /= num_cores;
            e
        })
        .collect();

    emulators.extend(unmapped_emus.into_iter().map(|mut e| {
        e.cpu_usage /= num_cores;
        e
    }));

    #[cfg(target_os = "linux")]
    let (disk_read_mbs, disk_write_mbs) = {
        let mut curr_read = 0;
        let mut curr_write = 0;
        if let Ok(diskstats) = std::fs::read_to_string("/proc/diskstats") {
            for line in diskstats.lines() {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 10 {
                    let dev = parts[2];
                    let is_disk = dev.starts_with("sd") && dev.len() == 3 ||
                                  dev.starts_with("nvme") && !dev.contains('p') ||
                                  dev.starts_with("vd") && dev.len() == 3 ||
                                  dev.starts_with("hd") && dev.len() == 3 ||
                                  dev.starts_with("mmcblk") && !dev.contains('p');

                    if is_disk {
                        let sectors_read = parts[5].parse::<u64>().unwrap_or(0);
                        let sectors_written = parts[9].parse::<u64>().unwrap_or(0);
                        curr_read += sectors_read * 512;
                        curr_write += sectors_written * 512;
                    }
                }
            }
        }

        let read_mb = if self.last_disk_read > 0 && elapsed_secs > 0.001 {
            (curr_read.saturating_sub(self.last_disk_read) as f64 / (1024.0 * 1024.0)) / elapsed_secs
        } else {
            0.0
        };
        let write_mb = if self.last_disk_write > 0 && elapsed_secs > 0.001 {
            (curr_write.saturating_sub(self.last_disk_write) as f64 / (1024.0 * 1024.0)) / elapsed_secs
        } else {
            0.0
        };

        self.last_disk_read = curr_read;
        self.last_disk_write = curr_write;

        (read_mb, write_mb)
    };

    #[cfg(target_os = "windows")]
    let (disk_read_mbs, disk_write_mbs) = {
        let mut read_mb = 0.0;
        let mut write_mb = 0.0;
        if let Ok(output) = std::process::Command::new("powershell")
            .args(&["-NoProfile", "-NonInteractive", "-Command", "Get-Counter '\\PhysicalDisk(_Total)\\Disk Read Bytes/sec', '\\PhysicalDisk(_Total)\\Disk Write Bytes/sec' | Select-Object -ExpandProperty CounterSamples | ForEach-Object { $_.CookedValue }"])
            .output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let lines: Vec<&str> = stdout.lines().filter(|l| !l.trim().is_empty()).collect();
            if lines.len() >= 2 {
                read_mb = lines[0].trim().parse::<f64>().unwrap_or(0.0) / (1024.0 * 1024.0);
                write_mb = lines[1].trim().parse::<f64>().unwrap_or(0.0) / (1024.0 * 1024.0);
            }
        }
        (read_mb, write_mb)
    };

    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    let (disk_read_mbs, disk_write_mbs) = {
        let read_mb = if elapsed_secs > 0.001 { (total_read_bytes as f64 / (1024.0 * 1024.0)) / elapsed_secs } else { 0.0 };
        let write_mb = if elapsed_secs > 0.001 { (total_write_bytes as f64 / (1024.0 * 1024.0)) / elapsed_secs } else { 0.0 };
        (read_mb, write_mb)
    };

    let mut gpu_percent = if std::path::Path::new("/sys/class/drm/card0/device/gpu_busy_percent").exists() {
        std::fs::read_to_string("/sys/class/drm/card0/device/gpu_busy_percent")
            .ok()
            .and_then(|s| s.trim().parse::<f32>().ok())
    } else {
        None
    };

    let mut gpu_memory_mb = None;

    if gpu_percent.is_none() {
        if let Ok(output) = std::process::Command::new("nvidia-smi")
            .args(&["--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"])
            .output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let (g, m) = parse_nvidia_smi(&stdout);
            gpu_percent = g;
            gpu_memory_mb = m;
        }

    }

    if gpu_percent.is_none() && cfg!(target_os = "windows") {
        if let Ok(output) = std::process::Command::new("powershell")
            .args(&["-NoProfile", "-NonInteractive", "-Command", "Get-Counter '\\GPU Engine(*)\\Utilization Percentage' | Select-Object -ExpandProperty CounterSamples | Measure-Object -Property CookedValue -Maximum | Select-Object -ExpandProperty Maximum"])
            .output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            if let Ok(val) = stdout.trim().replace(',', ".").parse::<f32>() {
                gpu_percent = Some(val);
            }
        }
    }

    if gpu_percent.is_none() && cfg!(target_os = "linux") && !self.intel_gpu_failed {
        if let Ok(output) = std::process::Command::new("intel_gpu_top")
            .args(&["-s", "1", "-n", "1"])
            .output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            static RE_INTEL: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
            let re_intel = RE_INTEL.get_or_init(|| regex::Regex::new(r"(?i)Render/3D:\s+(\d+)%").unwrap());
            if let Some(caps) = re_intel.captures(&stdout) {
                if let Ok(val) = caps[1].parse::<f32>() {
                    gpu_percent = Some(val);
                }
            } else {
                self.intel_gpu_failed = true;
            }
        } else {
            self.intel_gpu_failed = true;
        }
    }

    if gpu_percent.is_none() && cfg!(target_os = "macos") && !self.mac_gpu_failed {
        let now = std::time::Instant::now();
        let should_fetch = match self.mac_gpu_last_fetch {
            Some(last) => now.duration_since(last).as_secs() >= 10,
            None => true,
        };

        if should_fetch && !self.mac_gpu_fetching.load(std::sync::atomic::Ordering::SeqCst) {
            self.mac_gpu_fetching.store(true, std::sync::atomic::Ordering::SeqCst);
            self.mac_gpu_last_fetch = Some(now);

            let cache_clone = self.mac_gpu_cache.clone();
            let fetching_clone = self.mac_gpu_fetching.clone();

            std::thread::spawn(move || {
                if let Ok(output) = std::process::Command::new("powermetrics")
                    .args(&["-i", "500", "-n", "1", "--samplers", "gpu_power"])
                    .output() {
                    let stdout = String::from_utf8_lossy(&output.stdout);
                    static RE_MAC: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
                    let re_mac = RE_MAC.get_or_init(|| regex::Regex::new(r"(?i)GPU (?:HW )?active residency:\s+([\d.]+)%").unwrap());
                    if let Some(caps) = re_mac.captures(&stdout) {
                        if let Ok(val) = caps[1].parse::<f32>() {
                            let mut cache = cache_clone.lock().unwrap();
                            *cache = Some(val);
                        }
                    }
                }
                fetching_clone.store(false, std::sync::atomic::Ordering::SeqCst);
            });
        }

        let cache = self.mac_gpu_cache.lock().unwrap();
        gpu_percent = *cache;
    }


    let thermal_temp = {
        let mut max_t: Option<f32> = None;
        if cfg!(target_os = "linux") {
            if let Ok(entries) = std::fs::read_dir("/sys/class/thermal") {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name.starts_with("thermal_zone") {
                        let temp_path = entry.path().join("temp");
                        if let Ok(temp_str) = std::fs::read_to_string(temp_path) {
                            if let Ok(t) = temp_str.trim().parse::<f32>() {
                                let t_c = t / 1000.0;
                                if t_c < 150.0 {
                                    max_t = Some(max_t.map_or(t_c, |curr| curr.max(t_c)));
                                }
                            }
                        }
                    }
                }
            }
        }
        max_t
    };

    let mut pgin = 0;
    let mut pgout = 0;

    if cfg!(target_os = "linux") {
        if let Ok(vmstat) = std::fs::read_to_string("/proc/vmstat") {
            let (i, o) = parse_vmstat(&vmstat);
            pgin = i;
            pgout = o;
        }
    } else if cfg!(target_os = "macos") {
        if let Ok(output) = std::process::Command::new("vm_stat").output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let (i, o) = parse_vm_stat(&stdout);
            pgin = i;
            pgout = o;
        }
    }


    let mut in_rate = 0.0;
    let mut out_rate = 0.0;
    let mut paging_str = "Calculating...".to_string();
    if self.last_pgin > 0 || self.last_pgout > 0 {
        in_rate = (pgin.saturating_sub(self.last_pgin)) as f64 / elapsed_secs;
        out_rate = (pgout.saturating_sub(self.last_pgout)) as f64 / elapsed_secs;
        let unit = if cfg!(target_os = "macos") { "pages/s" } else { "KB/s" };
        paging_str = format!("In: {:.0} / Out: {:.0} ({})", in_rate, out_rate, unit);
    }
    self.last_pgin = pgin;
    self.last_pgout = pgout;

    let mut battery_str = "N/A".to_string();
    let mut battery_percent = None;
    if cfg!(target_os = "linux") {
        if let Ok(entries) = std::fs::read_dir("/sys/class/power_supply") {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.starts_with("BAT") {
                    let cap_path = entry.path().join("capacity");
                    let stat_path = entry.path().join("status");
                    if let (Ok(cap), Ok(stat)) = (std::fs::read_to_string(cap_path), std::fs::read_to_string(stat_path)) {
                        battery_str = format!("{}% ({})", cap.trim(), stat.trim());
                        battery_percent = cap.trim().parse::<f32>().ok();
                        break;
                    }
                }
            }
        }
    } else if cfg!(target_os = "macos") {
        if let Ok(output) = std::process::Command::new("pmset").args(&["-g", "batt"]).output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            if let Some(line) = stdout.lines().nth(1) {
                if let Some(idx) = line.find('%') {
                    let pct = &line[..idx];
                    let pct = pct.split_whitespace().last().unwrap_or("N/A");
                    battery_percent = pct.parse::<f32>().ok();
                    let status = if line.contains("discharging") { "Discharging" } else { "Plugged" };
                    battery_str = format!("{}% ({})", pct, status);
                }
            }
        }
    }

    HostStats {
        cpu_usage,
        ram_used_gb,
        ram_total_gb,
        ram_percent,
        swap_used_gb,
        swap_total_gb,
        swap_percent,
        disk_read_mbs,
        disk_write_mbs,
        emulators,
        netsim_cpu,
        netsim_ram_mb,
        netsim_count,
        gpu_percent,
        gpu_memory_mb,
        thermal_temp,
        paging_str,
        paging_in: in_rate,
        paging_out: out_rate,
        battery_str,
        battery_percent,
    }

    }
}

pub fn get_static_hardware_info() -> String {
    let mut sys = System::new_all();
    sys.refresh_all();

    let os = System::name().unwrap_or_else(|| "Unknown OS".to_string());
    let cpu = sys.cpus().first().map(|c| c.brand()).unwrap_or("Unknown CPU").trim().to_string();
    let ram = sys.total_memory() as f64 / (1024.0 * 1024.0 * 1024.0);

    let mut gpu_str = "N/A".to_string();
    if let Ok(output) = std::process::Command::new("nvidia-smi")
        .args(&["--query-gpu=gpu_name,memory.total", "--format=csv,noheader,nounits"])
        .output() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        if let Some(line) = stdout.lines().next() {
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() >= 2 {
                let name = parts[0].trim();
                let mem = parts[1].trim().parse::<f32>().unwrap_or(0.0) / 1024.0;
                gpu_str = format!("{} ({:.1}GB)", name, mem);
            }
        }
    }

    format!("{} | {} | {:.1} GB RAM | GPU: {}", os, cpu, ram, gpu_str)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    #[test]
    fn test_parse_cmdline() {
        let cmdline = vec![
            OsString::from("emulator"),
            OsString::from("-avd"),
            OsString::from("Pixel_6_API_31"),
            OsString::from("-port"),
            OsString::from("5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
    }

    #[test]
    fn test_parse_cmdline_concatenated_avd() {
        let cmdline = vec![
            OsString::from("emulator"),
            OsString::from("-avdPixel_6_API_31"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, None);
        assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
    }

    #[test]
    fn test_parse_cmdline_concatenated_full() {
        let cmdline = vec![
            OsString::from("emulator-netdelaynone-netspeedfull-avdMedium_Phone_37_Pro-qt-hide-window"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, None);
        assert_eq!(avd, Some("Medium_Phone_37_Pro".to_string()));
    }

    #[test]
    fn test_parse_cmdline_at_avd() {
        let cmdline = vec![
            OsString::from("emulator"),
            OsString::from("@Pixel_6_API_31"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, None);
        assert_eq!(avd, Some("Pixel_6_API_31".to_string()));
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
    fn test_parse_cmdline_qemu_next_device_args() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-device"),
            OsString::from("avdstart,serial_number=5554,avd_name=Pixel_7"),
            OsString::from("-device"),
            OsString::from("virtio-goldfish-adb,host_port=5555"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7".to_string()));
    }

    #[test]
    fn test_parse_cmdline_fishtank() {
        let cmdline = vec![
            OsString::from("fishtank"),
            OsString::from("@Pixel_7"),
            OsString::from("-fishtank"),
            OsString::from("5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7".to_string()));

        let cmdline_no_avd = vec![
            OsString::from("fishtank"),
            OsString::from("-fishtank"),
            OsString::from("5556"),
        ];
        let (port, avd) = parse_cmdline(&cmdline_no_avd);
        assert_eq!(port, Some(5556));
        assert_eq!(avd, None);
    }

    #[test]
    fn test_parse_cmdline_name_flag() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-name"),
            OsString::from("Pixel_Tablet"),
            OsString::from("-device"),
            OsString::from("virtio-goldfish-adb,host_port=5557"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5556));
        assert_eq!(avd, Some("Pixel_Tablet".to_string()));
    }

    #[test]
    fn test_parse_cmdline_name_flag_guest_prefix() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-name"),
            OsString::from("guest=Pixel_7_API_36,debug-threads=on"),
            OsString::from("-port"),
            OsString::from("5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7_API_36".to_string()));
    }

    #[test]
    fn test_parse_cmdline_equal_flags() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-name=guest=Pixel_Tablet,process=qemu"),
            OsString::from("-port=5556"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5556));
        assert_eq!(avd, Some("Pixel_Tablet".to_string()));

        let cmdline_fish = vec![
            OsString::from("fishtank"),
            OsString::from("@Pixel_7"),
            OsString::from("-fishtank=5554"),
        ];
        let (port, avd) = parse_cmdline(&cmdline_fish);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel_7".to_string()));
    }

    #[test]
    fn test_parse_cmdline_quoted_args() {
        let cmdline = vec![
            OsString::from("qemu-system-x86_64"),
            OsString::from("-device"),
            OsString::from("avdstart,serial_number=5554,avd_name=\"Pixel 7\""),
            OsString::from("-device=virtio-goldfish-adb,host_port=5555"),
        ];
        let (port, avd) = parse_cmdline(&cmdline);
        assert_eq!(port, Some(5554));
        assert_eq!(avd, Some("Pixel 7".to_string()));
    }
}
