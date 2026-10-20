mod host;
mod guest;
mod ui;
mod chart;
mod summary;
mod provider;

use clap::Parser;
use std::path::PathBuf;
use provider::{HostProvider, GuestProvider};

#[derive(Parser, Debug, Clone)]
#[command(author, version, about = "Cross-platform Android Emulator & Device Resource Monitor", long_about = None)]
pub struct Args {
    #[arg(short = '1', long)]
    pub once: bool,

    #[arg(long)]
    pub no_summary: bool,

    #[arg(long)]
    pub csv: bool,

    #[arg(short, long, default_value = ".")]
    pub out_dir: PathBuf,

    #[arg(short, long, default_value_t = 1.0)]
    pub interval: f64,

    #[arg(long)]
    pub headless: bool,

    #[arg(long, default_value_t = 5)]
    pub auto_save_interval: u32,
}

pub fn update_device_cache(
    device_names: &mut std::collections::HashMap<String, String>,
    disconnect_times: &mut std::collections::HashMap<String, tokio::time::Instant>,
    serial_pids: &mut std::collections::HashMap<String, u32>,
    connected_serials: &std::collections::HashSet<String>,
    qemu_pids: &std::collections::HashMap<String, u32>,
    current_time: tokio::time::Instant,
    timeout_secs: u64,
) {
    for s in device_names.keys().cloned().collect::<Vec<_>>() {
        if !connected_serials.contains(&s) {
            if !disconnect_times.contains_key(&s) {
                disconnect_times.insert(s.clone(), current_time);
            } else if current_time.duration_since(disconnect_times[&s]).as_secs() > timeout_secs {
                device_names.remove(&s);
                disconnect_times.remove(&s);
                serial_pids.remove(&s);
            }
        } else {
            disconnect_times.remove(&s);

            let new_pid = qemu_pids.get(&s).cloned();
            let old_pid = serial_pids.get(&s).cloned();

            if let (Some(old), Some(new)) = (old_pid, new_pid) {
                if old != new {
                    device_names.remove(&s);
                }
            }

            if let Some(new) = new_pid {
                serial_pids.insert(s.clone(), new);
            }
        }
    }
}

pub fn calculate_sleep_duration(interval: f64, loop_elapsed: f64) -> f64 {
    (interval - loop_elapsed).max(0.1)
}

pub async fn run<H: HostProvider + 'static, G: GuestProvider + 'static>(
    host_prov: H,
    guest_prov: G,
    args: Args,
    hardware_specs: String,
) {
    let interval = args.interval.clamp(1.0, 10.0);
    let mut prev_stats = std::collections::HashMap::new();
    let mut summary = summary::SessionSummary::new();
    let mut elapsed_total = 0.0;

    let mut device_names: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let mut disconnect_times: std::collections::HashMap<String, tokio::time::Instant> = std::collections::HashMap::new();
    let mut serial_pids: std::collections::HashMap<String, u32> = std::collections::HashMap::new();
    let mut online_indicator_until: std::collections::HashMap<String, tokio::time::Instant> = std::collections::HashMap::new();
    let mut last_status: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let mut prev_term_width = 0;
    let mut boot_completed: std::collections::HashMap<String, bool> = std::collections::HashMap::new();
    let mut boot_finished_time: std::collections::HashMap<String, tokio::time::Instant> = std::collections::HashMap::new();
    let mut is_booting_state: std::collections::HashMap<String, bool> = std::collections::HashMap::new();
    let mut last_seen_time: std::collections::HashMap<String, tokio::time::Instant> = std::collections::HashMap::new();

    let mut stats = crate::host::HostStats {
        cpu_usage: 0.0,
        ram_used_gb: 0.0,
        ram_total_gb: 0.0,
        ram_percent: 0.0,
        swap_used_gb: 0.0,
        swap_total_gb: 0.0,
        swap_percent: 0.0,
        disk_read_mbs: 0.0,
        disk_write_mbs: 0.0,
        emulators: vec![],
        netsim_cpu: 0.0,
        netsim_ram_mb: 0.0,
        netsim_count: 0,
        gpu_percent: None,
        gpu_memory_mb: None,
        thermal_temp: None,
        paging_str: "Calculating...".to_string(),
        paging_in: 0.0,
        paging_out: 0.0,
        battery_str: "Calculating...".to_string(),
        battery_percent: None,
    };
    let mut devices = std::collections::HashMap::new();

    if !args.once && !args.headless {
        let _ = crossterm::execute!(
            std::io::stdout(),
            crossterm::terminal::EnterAlternateScreen,
            crossterm::cursor::Hide
        );
    }

    let cpu_cores = host_prov.get_cpu_cores();
    let host_prov = std::sync::Arc::new(tokio::sync::Mutex::new(host_prov));
    let guest_prov = std::sync::Arc::new(guest_prov);

    let mut active_host_task: Option<tokio::task::JoinHandle<crate::host::HostStats>> = None;
    let mut active_devices_task: Option<tokio::task::JoinHandle<std::collections::HashMap<String, String>>> = None;
    let mut active_guest_tasks: std::collections::HashMap<String, tokio::task::JoinHandle<(String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>)>> = std::collections::HashMap::new();

    let mut last_guest_res: std::collections::HashMap<String, (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>)> = std::collections::HashMap::new();
    let mut first_seen_times: std::collections::HashMap<String, tokio::time::Instant> = std::collections::HashMap::new();
    let mut last_boot_log_times: std::collections::HashMap<String, tokio::time::Instant> = std::collections::HashMap::new();
    let mut last_known_pids: std::collections::HashMap<String, u32> = std::collections::HashMap::new();
    let mut current_storage_keys: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let mut last_host_task_start = tokio::time::Instant::now();
    let loop_start_time = tokio::time::Instant::now();
    let mut last_auto_save = tokio::time::Instant::now();

    tokio::select! {
        _ = async {
            loop {
                let now = tokio::time::Instant::now();
                elapsed_total = now.duration_since(loop_start_time).as_secs_f64();

                // Poll Host Task
                if let Some(ref h) = active_host_task {
                    if h.is_finished() {
                        if let Ok(res) = active_host_task.take().unwrap().await {
                            stats = res;
                        }
                    }
                }
                if active_host_task.is_none() {
                    let host_clone = host_prov.clone();
                    let elapsed = last_host_task_start.elapsed().as_secs_f64();
                    last_host_task_start = tokio::time::Instant::now();
                    active_host_task = Some(tokio::spawn(async move {
                        let mut lock = host_clone.lock().await;
                        lock.get_host_stats(elapsed)
                    }));
                }

                // Poll Devices Task
                if let Some(ref d) = active_devices_task {
                    if d.is_finished() {
                        if let Ok(res) = active_devices_task.take().unwrap().await {
                            devices = res;
                        }
                    }
                }
                if active_devices_task.is_none() {
                    let guest_clone = guest_prov.clone();
                    active_devices_task = Some(tokio::spawn(async move {
                        guest_clone.get_devices().await
                    }));
                }

                let connected: std::collections::HashSet<String> = devices.keys().cloned().collect();
                let mut qemu_pids = std::collections::HashMap::new();
                for emu in &stats.emulators {
                    for (serial, _) in &devices {
                        let port = serial.split('-').nth(1).and_then(|p| p.parse::<u16>().ok());
                        if port == emu.port {
                            qemu_pids.insert(serial.clone(), emu.pid);
                            break;
                        }
                    }
                }

                update_device_cache(
                    &mut device_names,
                    &mut disconnect_times,
                    &mut serial_pids,
                    &connected,
                    &qemu_pids,
                    tokio::time::Instant::now(),
                    5,
                );

                for (serial, status) in &devices {
                    let current_status = status.clone();
                    let old_status = last_status.get(serial).cloned().unwrap_or_else(|| "offline".to_string());
                    if old_status == "offline" && current_status == "device" {
                        online_indicator_until.insert(serial.clone(), tokio::time::Instant::now() + std::time::Duration::from_secs(10));
                    }
                    last_status.insert(serial.clone(), current_status);
                }

                let mut ts_entry = std::collections::HashMap::new();
                ts_entry.insert("time".to_string(), elapsed_total as f32);

                let mut guest_results = std::collections::HashMap::new();

                // Poll & Spawn Guest Tasks
                for (serial, status) in &devices {
                    if let Some(h) = active_guest_tasks.get(serial) {
                        if h.is_finished() {
                            if let Ok(res) = active_guest_tasks.remove(serial).unwrap().await {
                                last_guest_res.insert(serial.clone(), res);
                            }
                        }
                    }

                    if !active_guest_tasks.contains_key(serial) {
                        let current_pid = qemu_pids.get(serial).cloned();
                        let last_pid = last_known_pids.get(serial).cloned();
                        let pid_changed = match (current_pid, last_pid) {
                            (Some(c), Some(l)) => c != l,
                            (Some(_), None) => true,
                            _ => false,
                        };
                        if let Some(c) = current_pid {
                            last_known_pids.insert(serial.clone(), c);
                        }

                        if pid_changed {
                            boot_completed.remove(serial);
                            boot_finished_time.remove(serial);
                            is_booting_state.insert(serial.clone(), true);
                            first_seen_times.remove(serial);
                            last_boot_log_times.remove(serial);
                            current_storage_keys.remove(serial);
                        }

                        let known_model = device_names.get(serial).cloned().unwrap_or_default();
                        let current_is_booting = *is_booting_state.get(serial).unwrap_or(&true);
                        let serial_clone = serial.clone();
                        let status_clone = status.clone();
                        let guest_prov_ref = guest_prov.clone();

                        active_guest_tasks.insert(serial.clone(), tokio::spawn(async move {
                            guest_prov_ref.get_guest_stats(&serial_clone, &known_model, current_is_booting, &status_clone).await
                        }));
                    }
                }

                // Process Guest Results
                for (serial, _) in &devices {
                    if let Some(result) = last_guest_res.get(serial) {
                        let model = result.0.clone();
                        let is_booting = result.1;
                        is_booting_state.insert(serial.clone(), is_booting);

                        if !model.is_empty() && !model.contains("Unknown") {
                            device_names.insert(serial.clone(), model.clone());
                        }

                        last_seen_time.insert(serial.clone(), tokio::time::Instant::now());

                        let (model, is_booting, guest_diff, guest_real, guest_ram, _data_space, guest_swap, err_count, data_pct) = result.clone();

                        let mut avd_name_str = serial.clone();
                        let port = serial.split('-').nth(1).and_then(|s| s.parse::<u16>().ok());
                        for emu in &stats.emulators {
                            if port == emu.port {
                                if let Some(ref avd) = emu.avd_name {
                                    avd_name_str = avd.clone();
                                }
                                break;
                            }
                        }

                        let mut storage_key = if avd_name_str != *serial {
                            format!("{}_{}", avd_name_str, serial)
                        } else {
                            serial.clone()
                        };

                        let old_storage_key = current_storage_keys.get(serial).cloned();
                        if let Some(old_key) = old_storage_key {
                            if storage_key == *serial && old_key != *serial {
                                storage_key = old_key.clone();
                            } else if old_key != storage_key {
                                summary.migrate_keys(&old_key, &storage_key);
                                summary.device_names.remove(&old_key);
                            }
                        }
                        current_storage_keys.insert(serial.clone(), storage_key.clone());

                        let display_name = if model.is_empty() { avd_name_str } else { format!("{} | {}", avd_name_str, model) };

                        summary.device_names.insert(storage_key.clone(), display_name);
                        let cpu_tot = guest_diff.split('%').next().unwrap_or("").parse::<f32>().unwrap_or(0.0);
                        let cpu_adj = guest_real.split('%').next().unwrap_or("").parse::<f32>().unwrap_or(0.0);
                        let ram_pct = guest_ram.split('%').next().unwrap_or("").parse::<f32>().unwrap_or(0.0);
                        let swap_val = guest_swap.as_deref().map(|s| crate::summary::get_val(s, true)).unwrap_or(f32::NAN);

                        ts_entry.insert(format!("emu_cpu_tot_{}", storage_key), cpu_tot);
                        ts_entry.insert(format!("emu_cpu_adj_{}", storage_key), cpu_adj);
                        ts_entry.insert(format!("emu_ram_{}", storage_key), ram_pct);
                        ts_entry.insert(format!("emu_swap_{}", storage_key), swap_val);
                        ts_entry.insert(format!("emu_disk_pct_{}", storage_key), data_pct.unwrap_or(0.0));
                        ts_entry.insert(format!("emu_errs_{}", storage_key), err_count as f32);

                        let was_booting = is_booting; // approximation
                        if was_booting && !is_booting {
                            boot_completed.insert(serial.clone(), true);
                            boot_finished_time.insert(serial.clone(), tokio::time::Instant::now());
                        }

                        let status = devices.get(serial).map(|s| s.as_str()).unwrap_or("offline");
                        let is_booting_or_offline = is_booting || status == "offline";

                        let should_track = if is_booting_or_offline {
                            true
                        } else if *boot_completed.get(serial).unwrap_or(&false) {
                            if let Some(finished_time) = boot_finished_time.get(serial) {
                                tokio::time::Instant::now().duration_since(*finished_time).as_secs() < 300
                            } else {
                                false
                            }
                        } else {
                            false
                        };

                        if should_track {
                            let first_seen = *first_seen_times.entry(serial.clone()).or_insert_with(tokio::time::Instant::now);
                            let last_log = last_boot_log_times.get(serial).cloned();

                            let current_time = tokio::time::Instant::now();
                            let elapsed_boot = current_time.duration_since(first_seen).as_secs();

                            let pacing_ok = match last_log {
                                Some(t) => current_time.duration_since(t).as_secs() >= 5,
                                None => true,
                            };

                            if elapsed_boot < 900 && pacing_ok {
                                let delta_str = format!("+{}s", elapsed_boot);
                                let stats_str = format!("CPU: {} | RAM: {}, {} errs, {} data, {} swap)",
                                    guest_real,
                                    guest_ram.trim_end_matches(')'),
                                    err_count,
                                    _data_space.as_deref().unwrap_or("N/A"),
                                    guest_swap.as_deref().unwrap_or("N/A")
                                );

                                summary.boot_logs.entry(serial.clone())
                                    .or_insert_with(Vec::new)
                                    .push((delta_str, stats_str));

                                last_boot_log_times.insert(serial.clone(), current_time);
                            }
                        }

                        let ui_result = (model.clone(), is_booting, guest_diff.clone(), guest_real.clone(), guest_ram.clone(), _data_space.clone(), guest_swap.clone(), err_count, data_pct, false, should_track);
                        guest_results.insert(serial.clone(), ui_result);
                    }
                }

                let check_time = tokio::time::Instant::now();
                for (serial, name) in &device_names {
                    if !devices.contains_key(serial) {
                        let is_booting = *is_booting_state.get(serial).unwrap_or(&false);
                        let last_seen = last_seen_time.get(serial).cloned();

                        let should_show = is_booting || match last_seen {
                            Some(t) => check_time.duration_since(t).as_secs() < 120,
                            None => false,
                        };

                        if should_show {
                            let ui_result = (name.clone(), is_booting, "N/A".to_string(), "N/A".to_string(), "N/A".to_string(), None, None, 0, None, true, false);
                            guest_results.insert(serial.clone(), ui_result);
                        }
                    }
                }

                for emu in &stats.emulators {
                    for (serial, _) in &devices {
                        let port = serial.split('-').nth(1).and_then(|s| s.parse::<u16>().ok());
                        if port == emu.port {
                            let storage_key = if let Some(ref avd) = emu.avd_name {
                                format!("{}_{}", avd, serial)
                            } else {
                                serial.clone()
                            };
                            ts_entry.insert(format!("qemu_cpu_{}", storage_key), emu.cpu_usage);
                            ts_entry.insert(format!("qemu_ram_{}", storage_key), emu.ram_mb as f32);
                            ts_entry.insert(format!("qemu_cs_{}", storage_key), emu.context_switches as f32);
                            if let Some(g) = emu.gpu_load { ts_entry.insert(format!("qemu_gpu_{}", storage_key), g); }
                            break;
                        }
                    }
                }

                summary.iterations += 1;
                summary.host_cpu_sum += stats.cpu_usage;
                summary.host_cpu_max = summary.host_cpu_max.max(stats.cpu_usage);
                summary.host_ram_sum += stats.ram_percent;
                summary.host_ram_max = summary.host_ram_max.max(stats.ram_percent);

                ts_entry.insert("host_cpu".to_string(), stats.cpu_usage);
                ts_entry.insert("host_ram".to_string(), stats.ram_percent);
                ts_entry.insert("host_swap".to_string(), stats.swap_percent);
                ts_entry.insert("host_disk_read".to_string(), stats.disk_read_mbs as f32);
                ts_entry.insert("host_disk_write".to_string(), stats.disk_write_mbs as f32);
                ts_entry.insert("netsim_cpu".to_string(), stats.netsim_cpu);
                ts_entry.insert("netsim_ram".to_string(), stats.netsim_ram_mb as f32);
                ts_entry.insert("host_paging_in".to_string(), stats.paging_in as f32);
                ts_entry.insert("host_paging_out".to_string(), stats.paging_out as f32);
                if let Some(b) = stats.battery_percent { ts_entry.insert("host_battery".to_string(), b); }

                if let Some(t) = stats.thermal_temp { ts_entry.insert("host_thermal".to_string(), t); }
                if let Some(g) = stats.gpu_percent { ts_entry.insert("host_gpu".to_string(), g); }
                if let Some(g) = stats.gpu_memory_mb { ts_entry.insert("host_gpu_mem".to_string(), g); }

                if !args.headless && (!args.once || summary.iterations > 1) {
                    let term_width = crossterm::terminal::size().map(|(w, _)| w).unwrap_or(80);
                    let force_clear = summary.iterations == 1 || term_width != prev_term_width;
                    prev_term_width = term_width;
                    ui::print_dashboard(&stats, &guest_results, &mut prev_stats, summary.iterations, term_width, args.once, &hardware_specs, force_clear, &online_indicator_until);
                }

                summary.time_series.push(ts_entry);

                if args.auto_save_interval > 0 && last_auto_save.elapsed().as_secs() >= (args.auto_save_interval as u64 * 60) {
                    crate::summary::save_summary(&summary, &args.out_dir.to_string_lossy(), args.csv, !args.no_summary, &hardware_specs, cpu_cores, true);
                    last_auto_save = tokio::time::Instant::now();
                }

                if args.once && summary.iterations >= 2 {
                    break;
                }

                tokio::time::sleep(std::time::Duration::from_secs_f64(interval)).await;
            }
        } => {}
        _ = tokio::signal::ctrl_c() => {
            println!("\nCtrl+C received, exiting...");
        }
    }

    if !args.once && !args.headless {
        let _ = crossterm::execute!(
            std::io::stdout(),
            crossterm::terminal::LeaveAlternateScreen,
            crossterm::cursor::Show
        );
    }


    summary::save_summary(&summary, args.out_dir.to_str().unwrap_or("."), args.csv, !args.no_summary, &hardware_specs, cpu_cores, false);
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let args_clone = args.clone();
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        if !args_clone.once && !args_clone.headless {
            let _ = crossterm::execute!(
                std::io::stdout(),
                crossterm::terminal::LeaveAlternateScreen,
                crossterm::cursor::Show
            );
        }
        default_hook(panic_info);
    }));

    let mut sys = sysinfo::System::new_all();
    sys.refresh_cpu_all();
    sys.refresh_memory();
    sys.refresh_processes_specifics(
        sysinfo::ProcessesToUpdate::All,
        true,
        sysinfo::ProcessRefreshKind::everything(),
    );
    tokio::time::sleep(std::time::Duration::from_millis(500)).await;

    let host_prov = host::RealHostProvider {
        sys,
        last_pgin: 0,
        last_pgout: 0,
        last_disk_read: 0,
        last_disk_write: 0,
        intel_gpu_failed: false,
        mac_gpu_failed: false,
        pid_port_cache: std::collections::HashMap::new(),
        is_first_run: true,
        mac_gpu_cache: std::sync::Arc::new(std::sync::Mutex::new(None)),
        mac_gpu_fetching: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
        mac_gpu_last_fetch: None,
    };

    let guest_prov = guest::RealGuestProvider;

    let hardware_specs = host::get_static_hardware_info();
    run(host_prov, guest_prov, args, hardware_specs).await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use crate::host::{HostStats, EmulatorProcess};
    #[test]
    fn test_calculate_sleep_duration() {
        assert_eq!(calculate_sleep_duration(2.0, 0.5), 1.5);
        assert_eq!(calculate_sleep_duration(2.0, 2.5), 0.1);
        assert_eq!(calculate_sleep_duration(2.0, 5.0), 0.1);
    }

    struct MockHostProvider;
    impl provider::HostProvider for MockHostProvider {
        fn get_cpu_cores(&self) -> usize { 4 }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            HostStats {
                cpu_usage: 10.0,
                ram_used_gb: 1.0,
                ram_total_gb: 4.0,
                ram_percent: 25.0,
                swap_used_gb: 0.0,
                swap_total_gb: 0.0,
                swap_percent: 0.0,
                disk_read_mbs: 0.0,
                disk_write_mbs: 0.0,
                emulators: vec![],
                netsim_cpu: 0.0,
                netsim_ram_mb: 0.0,
                netsim_count: 0,
                gpu_percent: None,
                gpu_memory_mb: None,
                thermal_temp: None,
                paging_str: "N/A".to_string(),
                paging_in: 0.0,
                paging_out: 0.0,
                battery_str: "N/A".to_string(),
                battery_percent: None,
            }

        }
    }

    struct MockGuestProvider;
    impl provider::GuestProvider for MockGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
            ("Mock Model".to_string(), false, "50%".to_string(), "12.5% (over 4 cores)".to_string(), "50%".to_string(), Some("20%".to_string()), Some("0.0 MB".to_string()), 0, Some(20.0))
        }
    }

    #[tokio::test]
    async fn test_main_run_mocked() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };

        // If this finishes without hanging, dependency injection is working properly!
        run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
    }

    #[tokio::test]
    async fn test_main_headless_flag() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: false,
            auto_save_interval: 5,
        };
        run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
    }

    #[tokio::test]
    async fn test_main_no_summary_flag() {
        let args = Args {
            once: true,
            no_summary: false,
            csv: false,
            out_dir: std::path::PathBuf::from("/tmp"),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };
        run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
    }

    struct MockBootingGuestProvider;
    impl provider::GuestProvider for MockBootingGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
            ("Mock Model".to_string(), true, "50%".to_string(), "12.5% (over 4 cores)".to_string(), "50%".to_string(), Some("20%".to_string()), Some("0.0 MB".to_string()), 0, Some(20.0))
        }
    }

    #[tokio::test]
    async fn test_main_loop_booting_device() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };
        run(MockHostProvider, MockBootingGuestProvider, args, "Mock Specs".to_string()).await;
    }

    struct MockOfflineGuestProvider;
    impl provider::GuestProvider for MockOfflineGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "offline".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
            ("Mock Model".to_string(), true, "N/A".to_string(), "N/A".to_string(), "N/A".to_string(), None, None, 0, None)
        }
    }

    #[tokio::test]
    async fn test_main_loop_offline_device() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };
        run(MockHostProvider, MockOfflineGuestProvider, args, "Mock Specs".to_string()).await;
    }

    #[tokio::test]
    async fn test_main_csv_flag() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: true,
            out_dir: std::path::PathBuf::from("/tmp"),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };
        run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
    }

    #[tokio::test]
    async fn test_main_out_dir_flag() {
        let args = Args {
            once: true,
            no_summary: false,
            csv: false,
            out_dir: std::path::PathBuf::from("/tmp/out_rust"),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };
        run(MockHostProvider, MockGuestProvider, args, "Mock Specs".to_string()).await;
    }

    #[tokio::test]
    async fn test_main_loop_serial_migration() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };

        struct MigratingGuestProvider;
        impl provider::GuestProvider for MigratingGuestProvider {
            async fn get_devices(&self) -> std::collections::HashMap<String, String> {
                let mut map = std::collections::HashMap::new();
                map.insert("emulator-5556".to_string(), "device".to_string());
                map
            }
            async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
                ("Mock Model".to_string(), false, "50%".to_string(), "12.5% (over 4 cores)".to_string(), "50%".to_string(), Some("20%".to_string()), Some("0.0 MB".to_string()), 0, Some(20.0))
            }
        }

        run(MockHostProvider, MigratingGuestProvider, args, "Mock Specs".to_string()).await;
    }

    struct MockPhysicalDeviceGuestProvider;
    impl provider::GuestProvider for MockPhysicalDeviceGuestProvider {
        async fn get_devices(&self) -> std::collections::HashMap<String, String> {
            let mut map = std::collections::HashMap::new();
            map.insert("HT4A12345678".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
            ("Pixel 6".to_string(), false, "10%".to_string(), "2.5% (over 4 cores)".to_string(), "20%".to_string(), Some("15%".to_string()), Some("0.0 MB".to_string()), 0, Some(15.0))
        }
    }

    #[tokio::test]
    async fn test_main_loop_physical_device() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };
        run(MockHostProvider, MockPhysicalDeviceGuestProvider, args, "Mock Specs".to_string()).await;
    }

    #[test]
    fn test_update_device_cache_normal_operation() {
        let mut device_names = HashMap::new();
        device_names.insert("emulator-5554".to_string(), "Model A".to_string());
        let mut disconnect_times = HashMap::new();
        let mut serial_pids = HashMap::new();
        serial_pids.insert("emulator-5554".to_string(), 100);

        let mut connected_serials = std::collections::HashSet::new();
        connected_serials.insert("emulator-5554".to_string());

        let mut qemu_pids = HashMap::new();
        qemu_pids.insert("emulator-5554".to_string(), 100);

        let now = tokio::time::Instant::now();

        update_device_cache(
            &mut device_names,
            &mut disconnect_times,
            &mut serial_pids,
            &connected_serials,
            &qemu_pids,
            now,
            5,
        );

        assert_eq!(device_names.get("emulator-5554").map(|s| s.as_str()), Some("Model A"));
        assert_eq!(serial_pids.get("emulator-5554"), Some(&100));
        assert!(disconnect_times.is_empty());
    }

    #[test]
    fn test_update_device_cache_pid_changed() {
        let mut device_names = HashMap::new();
        device_names.insert("emulator-5554".to_string(), "Model A".to_string());
        let mut disconnect_times = HashMap::new();
        let mut serial_pids = HashMap::new();
        serial_pids.insert("emulator-5554".to_string(), 100);

        let mut connected_serials = std::collections::HashSet::new();
        connected_serials.insert("emulator-5554".to_string());

        let mut qemu_pids = HashMap::new();
        qemu_pids.insert("emulator-5554".to_string(), 101); // New PID

        let now = tokio::time::Instant::now();

        update_device_cache(
            &mut device_names,
            &mut disconnect_times,
            &mut serial_pids,
            &connected_serials,
            &qemu_pids,
            now,
            5,
        );

        assert!(device_names.is_empty()); // Invalidated
        assert_eq!(serial_pids.get("emulator-5554"), Some(&101)); // Updated
    }

    #[test]
    fn test_update_device_cache_disconnect_starts_timer() {
        let mut device_names = HashMap::new();
        device_names.insert("emulator-5554".to_string(), "Model A".to_string());
        let mut disconnect_times = HashMap::new();
        let mut serial_pids = HashMap::new();
        serial_pids.insert("emulator-5554".to_string(), 100);

        let connected_serials = std::collections::HashSet::new(); // Disconnected
        let qemu_pids = HashMap::new();

        let now = tokio::time::Instant::now();

        update_device_cache(
            &mut device_names,
            &mut disconnect_times,
            &mut serial_pids,
            &connected_serials,
            &qemu_pids,
            now,
            5,
        );

        assert_eq!(device_names.get("emulator-5554").map(|s| s.as_str()), Some("Model A"));
        assert_eq!(disconnect_times.get("emulator-5554"), Some(&now));
    }

    #[test]
    fn test_update_device_cache_disconnect_timeout_expires() {
        let mut device_names = HashMap::new();
        device_names.insert("emulator-5554".to_string(), "Model A".to_string());
        let mut disconnect_times = HashMap::new();
        let mut serial_pids = HashMap::new();
        serial_pids.insert("emulator-5554".to_string(), 100);

        let connected_serials = std::collections::HashSet::new();
        let qemu_pids = HashMap::new();

        let now = tokio::time::Instant::now();
        let past = now - std::time::Duration::from_secs(6);
        disconnect_times.insert("emulator-5554".to_string(), past);

        update_device_cache(
            &mut device_names,
            &mut disconnect_times,
            &mut serial_pids,
            &connected_serials,
            &qemu_pids,
            now,
            5,
        );

        assert!(device_names.is_empty());
        assert!(disconnect_times.is_empty());
        assert!(serial_pids.is_empty());
    }

    #[test]
    fn test_update_device_cache_disconnect_waiting() {
        let mut device_names = HashMap::new();
        device_names.insert("emulator-5554".to_string(), "Model A".to_string());
        let mut disconnect_times = HashMap::new();
        let mut serial_pids = HashMap::new();
        serial_pids.insert("emulator-5554".to_string(), 100);

        let connected_serials = std::collections::HashSet::new();
        let qemu_pids = HashMap::new();

        let now = tokio::time::Instant::now();
        let past = now - std::time::Duration::from_secs(4);
        disconnect_times.insert("emulator-5554".to_string(), past);

        update_device_cache(
            &mut device_names,
            &mut disconnect_times,
            &mut serial_pids,
            &connected_serials,
            &qemu_pids,
            now,
            5,
        );

        assert_eq!(device_names.get("emulator-5554").map(|s| s.as_str()), Some("Model A"));
        assert_eq!(disconnect_times.get("emulator-5554"), Some(&past));
    }

    #[test]
    fn test_update_device_cache_reconnect_before_timeout() {
        let mut device_names = HashMap::new();
        device_names.insert("emulator-5554".to_string(), "Model A".to_string());
        let mut disconnect_times = HashMap::new();
        let mut serial_pids = HashMap::new();
        serial_pids.insert("emulator-5554".to_string(), 100);

        let now = tokio::time::Instant::now();
        let past = now - std::time::Duration::from_secs(4);
        disconnect_times.insert("emulator-5554".to_string(), past);

        let mut connected_serials = std::collections::HashSet::new();
        connected_serials.insert("emulator-5554".to_string());

        let mut qemu_pids = HashMap::new();
        qemu_pids.insert("emulator-5554".to_string(), 100);

        update_device_cache(
            &mut device_names,
            &mut disconnect_times,
            &mut serial_pids,
            &connected_serials,
            &qemu_pids,
            now,
            5,
        );

        assert!(disconnect_times.is_empty());
        assert_eq!(device_names.get("emulator-5554").map(|s| s.as_str()), Some("Model A"));
    }

    struct MockCollisionHostProvider;
    impl provider::HostProvider for MockCollisionHostProvider {
        fn get_cpu_cores(&self) -> usize { 4 }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            HostStats {
                cpu_usage: 10.0,
                ram_used_gb: 1.0,
                ram_total_gb: 4.0,
                ram_percent: 25.0,
                swap_used_gb: 0.0,
                swap_total_gb: 0.0,
                swap_percent: 0.0,
                disk_read_mbs: 0.0,
                disk_write_mbs: 0.0,
                emulators: vec![
                    EmulatorProcess {
                        pid: 1234,
                        port: Some(5554),
                        avd_name: Some("Pixel_6".to_string()),
                        cpu_usage: 10.0,
                        ram_mb: 100.0,
                        threads: 4,
                        context_switches: 100,
                        gpu_load: None,
                    },
                    EmulatorProcess {
                        pid: 5678,
                        port: Some(5556),
                        avd_name: Some("Pixel_6".to_string()),
                        cpu_usage: 20.0,
                        ram_mb: 200.0,
                        threads: 4,
                        context_switches: 100,
                        gpu_load: None,
                    },
                ],
                netsim_cpu: 0.0,
                netsim_ram_mb: 0.0,
                netsim_count: 0,
                gpu_percent: None,
                gpu_memory_mb: None,
                thermal_temp: None,
                paging_str: "N/A".to_string(),
                paging_in: 0.0,
                paging_out: 0.0,
                battery_str: "N/A".to_string(),
                battery_percent: None,
            }
        }
    }

    struct MockCollisionGuestProvider;
    impl provider::GuestProvider for MockCollisionGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map.insert("emulator-5556".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, serial: &str, _: &str, _: bool, _: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
            if serial == "emulator-5554" {
                ("Pixel_6".to_string(), false, "50%".to_string(), "25%".to_string(), "50%".to_string(), None, Some("0.0 MB".to_string()), 0, None)
            } else {
                ("Pixel_6".to_string(), false, "60%".to_string(), "30%".to_string(), "60%".to_string(), None, Some("0.0 MB".to_string()), 0, None)
            }
        }
    }

    #[tokio::test]
    async fn test_avd_key_collision() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };

        run(MockCollisionHostProvider, MockCollisionGuestProvider, args, "Mock Specs".to_string()).await;
    }

    struct MockDynamicHostProvider {
        iteration: std::sync::Mutex<usize>,
    }

    impl provider::HostProvider for MockDynamicHostProvider {
        fn get_cpu_cores(&self) -> usize { 4 }
        fn get_host_stats(&mut self, _: f64) -> HostStats {
            let mut iter = self.iteration.lock().unwrap();
            *iter += 1;

            let pid = if *iter == 1 { 1234 } else { 5678 };

            HostStats {
                cpu_usage: 10.0,
                ram_used_gb: 1.0,
                ram_total_gb: 4.0,
                ram_percent: 25.0,
                swap_used_gb: 0.0,
                swap_total_gb: 0.0,
                swap_percent: 0.0,
                disk_read_mbs: 0.0,
                disk_write_mbs: 0.0,
                emulators: vec![
                    EmulatorProcess {
                        pid,
                        port: Some(5554),
                        avd_name: Some("Pixel_6".to_string()),
                        cpu_usage: 10.0,
                        ram_mb: 100.0,
                        threads: 4,
                        context_switches: 100,
                        gpu_load: None,
                    }
                ],
                netsim_cpu: 0.0,
                netsim_ram_mb: 0.0,
                netsim_count: 0,
                gpu_percent: None,
                gpu_memory_mb: None,
                thermal_temp: None,
                paging_str: "N/A".to_string(),
                paging_in: 0.0,
                paging_out: 0.0,
                battery_str: "N/A".to_string(),
                battery_percent: None,
            }
        }
    }

    struct MockDynamicGuestProvider {
        iteration: std::sync::Mutex<usize>,
    }

    impl provider::GuestProvider for MockDynamicGuestProvider {
        async fn get_devices(&self) -> HashMap<String, String> {
            let mut map = HashMap::new();
            map.insert("emulator-5554".to_string(), "device".to_string());
            map
        }
        async fn get_guest_stats(&self, _: &str, _: &str, _: bool, _: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
            let mut iter = self.iteration.lock().unwrap();
            *iter += 1;

            if *iter == 1 {
                ("Pixel_6".to_string(), false, "50%".to_string(), "25%".to_string(), "50%".to_string(), None, Some("0.0 MB".to_string()), 0, None)
            } else {
                ("Pixel_6".to_string(), true, "60%".to_string(), "30%".to_string(), "60%".to_string(), None, Some("0.0 MB".to_string()), 0, None)
            }
        }
    }

    #[tokio::test]
    async fn test_boot_tracking_restart_detected() {
        let args = Args {
            once: true,
            no_summary: true,
            csv: false,
            out_dir: std::path::PathBuf::from("."),
            interval: 1.0,
            headless: true,
            auto_save_interval: 5,
        };

        let host_prov = MockDynamicHostProvider { iteration: std::sync::Mutex::new(0) };
        let guest_prov = MockDynamicGuestProvider { iteration: std::sync::Mutex::new(0) };

        run(host_prov, guest_prov, args, "Mock Specs".to_string()).await;
    }
}
