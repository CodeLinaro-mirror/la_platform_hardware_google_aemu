use crate::host::HostStats;
// Deleted unused import
use regex::Regex;

pub struct Colors;
impl Colors {
    pub const HEADER: &'static str = "\x1b[95m";
    pub const CYAN: &'static str = "\x1b[96m";
    pub const GREEN: &'static str = "\x1b[92m";
    pub const YELLOW: &'static str = "\x1b[93m";
    pub const RED: &'static str = "\x1b[91m";
    pub const BLUE: &'static str = "\x1b[94m";
    pub const RESET: &'static str = "\x1b[0m";
    pub const BOLD: &'static str = "\x1b[1m";
    pub const DIM: &'static str = "\x1b[2m";
}

pub fn build_dashboard(
    host_stats: &HostStats,
    guest_results: &std::collections::HashMap<String, (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>, bool, bool)>,
    prev_stats: &mut std::collections::HashMap<String, f32>,
    iter_count: u64,
    term_width: u16,
    is_once: bool,
    hardware_specs: &str,
    _force_clear: bool,
    online_indicator_until: &std::collections::HashMap<String, tokio::time::Instant>,
) -> String {

    let mut buf = Vec::new();

    let width = term_width.saturating_sub(1).clamp(76, 120) as usize;
    let bar_width: usize = 20;
    let val_width = width - 60;

    let c1 = format!("{}{}", Colors::BLUE, Colors::BOLD);
    let c2 = Colors::RESET;

    let re_ansi = Regex::new(r"(?i)\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])").unwrap();

    if !is_once {
        let esc = if _force_clear { "\x1b[2J\x1b[H" } else { "\x1b[H\x1b[J" };
        buf.push(format!("{}\n", esc));
    }
    buf.push(format!("{}┌{}┐{}", c1, "─".repeat(width - 2), c2));

    let logo = format!("{}G{}o{}o{}g{}l{}e{}", Colors::BLUE, Colors::RED, Colors::YELLOW, Colors::BLUE, Colors::GREEN, Colors::RED, Colors::RESET);
    let title_text = "Android Emulator & Device Resource Monitor";
    let line_content = format!(" {}   {}", logo, title_text);

    let visible_len = re_ansi.replace_all(&line_content, "").len();
    let padding = (width - 2).saturating_sub(visible_len);
    buf.push(format!("{}│{}{}{}{}{}{}│{}", c1, c2, Colors::BOLD, line_content, " ".repeat(padding), c2, c1, c2));
    buf.push(format!("{}├{}┤{}", c1, "─".repeat(width - 2), c2));

    let specs_text = format!(" Host Specs: {}", hardware_specs);
    let visible_specs_len = re_ansi.replace_all(&specs_text, "").len();
    let padding_specs = (width - 2).saturating_sub(visible_specs_len);
    buf.push(format!("{}│{}{}{}{}{}│{}", c1, c2, Colors::DIM, specs_text, " ".repeat(padding_specs), c1, c2));
    buf.push(format!("{}├{}┤{}", c1, "─".repeat(width - 2), c2));

    // Print Host Section
    let section_title = format!(" HOST SYSTEM ({})", sysinfo::System::name().unwrap_or_else(|| "Unknown OS".to_string()));
    let visible_len = re_ansi.replace_all(&section_title, "").len();
    let padding = (width - 2).saturating_sub(visible_len);
    buf.push(format!("{}│{}{}{}{}{}{}{}│{}", c1, c2, Colors::YELLOW, Colors::BOLD, section_title, " ".repeat(padding), c2, c1, c2));
    buf.push(format!("{}├{}┬{}┤{}", c1, "─".repeat(32), "─".repeat(width - 35), c2));

    // Helper to get trend arrow
    let mut get_trend_arrow = |key: &str, current: f32| {
        let previous = prev_stats.get(key).cloned().unwrap_or(current);
        prev_stats.insert(key.to_string(), current);

        if current > previous + 0.5 {
            "↑"
        } else if current < previous - 0.5 {
            "↓"
        } else {
            "→"
        }
    };

    // Helper to print a row
    let print_row = |label: &str, value_str: &str, value_pct: f32, buf: &mut Vec<String>| {
        let raw_val = crate::summary::get_val(value_str, false);

        let is_placeholder = value_str.contains("Unsupported") ||
                             value_str.contains("No ADB") ||
                             value_str.contains("Error") ||
                             value_str.contains("Timeout") ||
                             value_str.contains("Hang") ||
                             value_str.contains("Calculating") ||
                             raw_val.is_nan();

        let mut is_alert = false;
        let val_for_alert = if raw_val == 0.0 && value_pct > 0.0 { value_pct } else { raw_val };

        if label.contains("Battery") {
            if val_for_alert <= 10.0 { is_alert = true; }
        } else if value_str.contains("°C") {
            if val_for_alert > 85.0 { is_alert = true; }
        } else if value_str.contains('%') {
            let threshold = if label == "Total Device CPU Usage" ||
                               label == "Host QEMU CPU" ||
                               label == "Unmapped Emulator CPU" ||
                               label == "Network Simulator CPU" { 750.0 } else { 90.0 };
            if val_for_alert > threshold { is_alert = true; }
        }

        let color = if is_placeholder {
            Colors::CYAN
        } else if label.contains("Battery") {
            if val_for_alert > 20.0 {
                Colors::GREEN
            } else if val_for_alert > 10.0 {
                Colors::YELLOW
            } else {
                "\x1b[91m\x1b[1m"
            }
        } else if value_str.contains("°C") {
            if val_for_alert > 85.0 {
                "\x1b[91m\x1b[1m"
            } else if val_for_alert > 70.0 {
                Colors::YELLOW
            } else {
                Colors::GREEN
            }
        } else if value_str.contains("pages/s") || value_str.contains("Out:") || value_str.contains("KB/s") {
            if val_for_alert > 5000.0 {
                "\x1b[91m\x1b[1m"
            } else if val_for_alert > 1000.0 {
                Colors::YELLOW
            } else {
                Colors::GREEN
            }
        } else if !value_str.contains('%') {
            Colors::CYAN
        } else {
            let is_special_cpu = label.contains("Total Device CPU") ||
                                 label.contains("Host QEMU CPU") ||
                                 label.contains("Unmapped Emulator CPU") ||
                                 label.contains("Network Simulator");

            if is_special_cpu {
                if val_for_alert > 750.0 {
                    "\x1b[91m\x1b[1m"
                } else if val_for_alert > 350.0 {
                    Colors::RED
                } else if val_for_alert > 150.0 {
                    Colors::YELLOW
                } else {
                    Colors::GREEN
                }
            } else {
                if val_for_alert > 90.0 {
                    "\x1b[91m\x1b[1m"
                } else if val_for_alert > 70.0 {
                    Colors::RED
                } else if val_for_alert > 40.0 {
                    Colors::YELLOW
                } else {
                    Colors::GREEN
                }
            }
        };

        let color = if label.contains("Host QEMU") && color == Colors::GREEN {
            Colors::BLUE
        } else {
            color
        };

        let color = if is_alert { "\x1b[91m\x1b[1m" } else { color };

        let has_bar = value_str.contains('%') || value_str.contains("°C");

        let bar = if has_bar {
            let val_to_clamp = if is_placeholder { 0.0 } else { if raw_val == 0.0 && value_pct > 0.0 { value_pct } else { raw_val } };
            let clamped_val = val_to_clamp.clamp(0.0, 100.0);
            let filled = ((clamped_val / 100.0) * bar_width as f32) as usize;
            let half = if ((clamped_val / 100.0) * bar_width as f32 - filled as f32) >= 0.5 { 1 } else { 0 };
            format!("[{}{}{}]", "█".repeat(filled), if half == 1 { "▌" } else { "" }, " ".repeat(bar_width.saturating_sub(filled + half)))
        } else {
            "".to_string()
        };

        let value_str_with_alert = if is_alert {
            format!("{} [!!]", value_str)
        } else {
            value_str.to_string()
        };

        let visible_val_len = value_str_with_alert.chars().count();
        let val_display = if visible_val_len > val_width {
            let mut truncated: String = value_str_with_alert.chars().take(val_width - 1).collect();
            truncated.push('…');
            truncated
        } else {
            let spaces = " ".repeat(val_width - visible_val_len);
            format!("{}{}", value_str_with_alert, spaces)
        };

        let mut line = String::new();
        line.push_str(&c1);
        line.push('│');
        line.push_str(c2);
        line.push(' ');
        line.push_str(Colors::BOLD);
        line.push_str(&format!("{:<30}", label));
        line.push_str(c2);
        line.push(' ');
        line.push_str(&c1);
        line.push('│');
        line.push_str(c2);
        line.push(' ');
        line.push_str(color);

        if has_bar {
            line.push_str(&bar);
            line.push(' ');
            line.push_str(color);
            line.push_str(&val_display);
        } else {
            let full_w = width - 37;
            let visible_val_len = value_str_with_alert.chars().count();
            let val_display_full = if visible_val_len > full_w {
                let mut truncated: String = value_str_with_alert.chars().take(full_w - 1).collect();
                truncated.push('…');
                truncated
            } else {
                let spaces = " ".repeat(full_w - visible_val_len);
                format!("{}{}", value_str_with_alert, spaces)
            };
            line.push_str(&val_display_full);
        }

        line.push(' ');
        line.push_str(c2);
        line.push_str(&c1);
        line.push('│');
        line.push_str(c2);
        buf.push(line);
    };

    let cpu_str = format!("{:.1}% {}", host_stats.cpu_usage, get_trend_arrow("host_cpu", host_stats.cpu_usage));
    print_row("Complete System CPU", &cpu_str, host_stats.cpu_usage, &mut buf);
    let ram_str = format!("{:.1}% ({:.1}GB/{:.1}GB) {}", host_stats.ram_percent, host_stats.ram_used_gb, host_stats.ram_total_gb, get_trend_arrow("host_ram", host_stats.ram_percent));
    print_row("Complete System RAM", &ram_str, host_stats.ram_percent, &mut buf);
    let swap_str = format!("{:.1}% ({:.1}GB/{:.1}GB) {}", host_stats.swap_percent, host_stats.swap_used_gb, host_stats.swap_total_gb, get_trend_arrow("host_swap", host_stats.swap_percent));
    print_row("Backup Memory (Swap) Usage", &swap_str, host_stats.swap_percent, &mut buf);
    print_row("Memory Swapping Activity", &host_stats.paging_str, 0.0, &mut buf);

    let gpu_str = host_stats.gpu_percent.map(|g| format!("{:.1}%", g)).unwrap_or_else(|| "N/A (Requires sudo or nvidia-smi)".to_string());
    print_row("Host GPU Load", &gpu_str, host_stats.gpu_percent.unwrap_or(0.0), &mut buf);

    let gpu_mem_str = host_stats.gpu_memory_mb.map(|m| format!("{:.1}MB", m)).unwrap_or_else(|| "N/A".to_string());
    print_row("Host GPU Memory", &gpu_mem_str, 0.0, &mut buf);

    let temp_str = host_stats.thermal_temp.map(|t| format!("{:.1}°C", t)).unwrap_or_else(|| "N/A".to_string());
    print_row("Host Thermal Temperature", &temp_str, 0.0, &mut buf);

    let disk_str = format!("Read: {:.1} MB/s | Write: {:.1} MB/s", host_stats.disk_read_mbs, host_stats.disk_write_mbs);
    print_row("Host Disk I/O Rate", &disk_str, 0.0, &mut buf);

    if host_stats.battery_str != "N/A" {
        print_row("Laptop Battery Status", &host_stats.battery_str, 0.0, &mut buf);
    }

    let unmapped: Vec<&crate::host::EmulatorProcess> = host_stats.emulators.iter().filter(|e| e.port.is_none()).collect();
    if !unmapped.is_empty() {
        let sum_cpu: f32 = unmapped.iter().map(|e| e.cpu_usage).sum();
        let sum_ram: f64 = unmapped.iter().map(|e| e.ram_mb).sum();
        let pids: Vec<String> = unmapped.iter().map(|e| e.pid.to_string()).collect();
        let pid_str = if pids.len() > 4 {
            format!("{} ... (+{} more)", pids[..4].join(", "), pids.len() - 4)
        } else {
            pids.join(", ")
        };
        print_row("Unmapped Emulator CPU", &format!("{:.1}% (PIDs: {})", sum_cpu, pid_str), sum_cpu, &mut buf);
        print_row("Unmapped Emulator RAM", &format!("{:.1}MB (PIDs: {})", sum_ram, pid_str), 0.0, &mut buf);
    }

    let netsim_cpu_str = format!("{:.1}% ({} processes) {}", host_stats.netsim_cpu, host_stats.netsim_count, get_trend_arrow("netsim_cpu", host_stats.netsim_cpu));
    print_row("Network Simulator CPU", &netsim_cpu_str, host_stats.netsim_cpu, &mut buf);

    let netsim_ram_str = format!("{:.1}MB ({} processes)", host_stats.netsim_ram_mb, host_stats.netsim_count);
    print_row("Network Simulator RAM", &netsim_ram_str, 0.0, &mut buf);


    // Print Guest Section
    if guest_results.is_empty() {
        buf.push(format!("{}├{}┴{}┤{}", c1, "─".repeat(32), "─".repeat(width - 35), c2));
        let section_title = " GUEST SYSTEM (ADB)";
        let visible_len = re_ansi.replace_all(&section_title, "").len();
        let padding = (width - 2).saturating_sub(visible_len);
        buf.push(format!("{}│{}{}{}{}{}{}{}│{}", c1, c2, Colors::HEADER, Colors::BOLD, section_title, " ".repeat(padding), c2, c1, c2));
        buf.push(format!("{}├{}┬{}┤{}", c1, "─".repeat(32), "─".repeat(width - 35), c2));
        print_row("Guest CPU", "N/A (No devices connected)", 0.0, &mut buf);
        print_row("Guest RAM Usage", "N/A", 0.0, &mut buf);
        print_row("Guest Swap Memory", "N/A", 0.0, &mut buf);
    } else {
        let re_model_clean = Regex::new(r" \| Android [^|]+ \(API ([^|]+)\) \| Build: ([^|]+)").unwrap();

        let mut guest_keys: Vec<&String> = guest_results.keys().collect();
        guest_keys.sort();

        for serial in guest_keys {
            let (model, _is_booting, guest_diff, guest_real, guest_ram, data_space, guest_swap, err_count, _data_pct, is_disconnected, recording_boot_stats) = &guest_results[serial];
            buf.push(format!("{}├{}┴{}┤{}", c1, "─".repeat(32), "─".repeat(width - 35), c2));

            let clean_model = re_model_clean.replace(model, " | API $1 | $2");

            let mut avd_name_str = serial.clone();
            let port = serial.split('-').nth(1).and_then(|s| s.parse::<u16>().ok());
            for emu in &host_stats.emulators {
                if port == emu.port {
                    if let Some(ref avd) = emu.avd_name {
                        avd_name_str = avd.clone();
                    }
                    break;
                }
            }

            let display_name = if clean_model.is_empty() { avd_name_str } else { format!("{} | {}", avd_name_str, clean_model) };

            let is_online_flash = online_indicator_until.get(serial)
                .map(|&t| tokio::time::Instant::now() < t)
                .unwrap_or(false);

            let status_tag = if *is_disconnected {
                format!("[DISCONNECTED]")
            } else if *recording_boot_stats {
                format!("[RECORDING BOOT STATS]")
            } else if is_online_flash {
                format!("[ONLINE]")
            } else {
                "".to_string()
            };

            let badge_color = if *is_disconnected {
                Colors::RED
            } else if *recording_boot_stats {
                Colors::YELLOW
            } else {
                Colors::GREEN
            };

            let mut inside_text = format!(" GUEST SYSTEM ({})", display_name);
            let v_title_len = re_ansi.replace_all(&inside_text, "").len();

            let (badge_formatted, v_badge_len) = if status_tag.is_empty() {
                ("".to_string(), 0)
            } else {
                (format!(" {}{}{}", badge_color, status_tag, Colors::RESET), status_tag.len() + 1)
            };

            let mut v_title_len_final = v_title_len;
            if v_title_len + v_badge_len > width - 4 {
                let max_title_len = (width - 4).saturating_sub(v_badge_len + 1);
                inside_text = inside_text.chars().take(max_title_len).collect::<String>();
                inside_text.push('…');
                v_title_len_final = re_ansi.replace_all(&inside_text, "").len();
            }

            let padding = (width - 2).saturating_sub(v_title_len_final + v_badge_len);

            buf.push(format!("{}│{}{}{}{}{}{}{}│{}", c1, c2, Colors::GREEN, Colors::BOLD, inside_text, badge_formatted, " ".repeat(padding), c1, c2));
            buf.push(format!("{}├{}┬{}┤{}", c1, "─".repeat(32), "─".repeat(width - 35), c2));

            print_row("Total Device CPU Usage", guest_diff, 0.0, &mut buf);

            // Apply trend to guest adjusted CPU if it's a percentage
            let guest_real_trend = if guest_real.contains("%") {
                if let Ok(val) = guest_real.split('%').next().unwrap_or("").parse::<f32>() {
                    format!("{} {}", guest_real, if val > prev_stats.get(&format!("{}_real", serial)).cloned().unwrap_or(val) + 0.5 { "↑" } else if val < prev_stats.get(&format!("{}_real", serial)).cloned().unwrap_or(val) - 0.5 { "↓" } else { "→" })
                } else {
                    guest_real.clone()
                }
            } else {
                guest_real.clone()
            };

            print_row("Adjusted CPU Load", &guest_real_trend, 0.0, &mut buf);
            print_row("Guest RAM Usage", guest_ram, 0.0, &mut buf);

            let disk_str = data_space.as_deref().unwrap_or("N/A");
            print_row("Guest Disk Space", disk_str, 0.0, &mut buf);

            let mut qemu_cpu = 0.0;
            let mut qemu_ram = 0.0;
            let mut qemu_threads = 0;
            let mut qemu_cs = 0;
            let mut qemu_gpu: Option<f32> = None;
            let mut found_qemu = false;

            let port = serial.split('-').nth(1).and_then(|s| s.parse::<u16>().ok());
            for emu in &host_stats.emulators {
                if port == emu.port {
                    qemu_cpu = emu.cpu_usage;
                    qemu_ram = emu.ram_mb;
                    qemu_threads = emu.threads;
                    qemu_cs = emu.context_switches;
                    qemu_gpu = emu.gpu_load;
                    found_qemu = true;
                    break;
                }
            }

            let qemu_cpu_str = if found_qemu { format!("{:.1}%", qemu_cpu) } else { "N/A".to_string() };
            let qemu_ram_str = if found_qemu { format!("{:.1} MB ({} thr, {} cs)", qemu_ram, qemu_threads, qemu_cs) } else { "N/A".to_string() };
            print_row("Host QEMU CPU", &qemu_cpu_str, if found_qemu { qemu_cpu } else { 0.0 }, &mut buf);
            print_row("Host QEMU RAM", &qemu_ram_str, 0.0, &mut buf);

            let swap_str = guest_swap.as_deref().unwrap_or("N/A");
            print_row("Guest Swap Memory", swap_str, 0.0, &mut buf);

            if found_qemu {
                if let Some(gpu) = qemu_gpu {
                    print_row("Host QEMU GPU Load", &format!("{:.1}%", gpu), gpu, &mut buf);
                }
            }

            print_row("Guest Logcat Errors", &format!("{} errs", err_count), 0.0, &mut buf);
        }
    }

    buf.push(format!("{}└{}┴{}┘{}", c1, "─".repeat(32), "─".repeat(width - 35), c2));

    let dots = ".".repeat((iter_count % 4) as usize);
    let msg = if is_once {
        format!("Last Updated: {}", chrono::Local::now().format("%H:%M:%S"))
    } else {
        format!("Press Ctrl+C to exit. Last Updated: {} | Refreshing{:<3}", chrono::Local::now().format("%H:%M:%S"), dots)
    };
    buf.push(format!("\n{}{}{}", Colors::YELLOW, msg, Colors::RESET));

    buf.join("\n")
}

pub fn print_dashboard(
    host_stats: &HostStats,
    guest_results: &std::collections::HashMap<String, (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>, bool, bool)>,
    prev_stats: &mut std::collections::HashMap<String, f32>,
    iter_count: u64,
    term_width: u16,
    is_once: bool,
    hardware_specs: &str,
    force_clear: bool,
    online_indicator_until: &std::collections::HashMap<String, tokio::time::Instant>,
) {
    let s = build_dashboard(host_stats, guest_results, prev_stats, iter_count, term_width, is_once, hardware_specs, force_clear, online_indicator_until);
    println!("{}", s);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::host::{HostStats, EmulatorProcess};
    use std::collections::HashMap;

    #[test]
    fn test_unmapped_emulators_ui() {
        let mut emulators = Vec::new();
        emulators.push(EmulatorProcess {
            pid: 1234,
            port: None,
            avd_name: None,
            cpu_usage: 10.0,
            ram_mb: 100.0,
            threads: 4,
            context_switches: 100,
            gpu_load: None,
        });

        let stats = HostStats {
            cpu_usage: 10.0,
            ram_used_gb: 1.0,
            ram_total_gb: 4.0,
            ram_percent: 25.0,
            swap_used_gb: 0.0,
            swap_total_gb: 0.0,
            swap_percent: 0.0,
            disk_read_mbs: 0.0,
            disk_write_mbs: 0.0,
            emulators,
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
        };

        let guest_results = HashMap::new();
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();

        let dashboard = build_dashboard(&stats, &guest_results, &mut prev_stats, 1, 80, true, "Specs", true, &online_indicator_until);

        assert!(dashboard.contains("Unmapped Emulator CPU"));
        assert!(dashboard.contains("10.0% (PIDs: 1234)"));
    }
    #[test]
    fn test_unmapped_emulators_clamping() {
        let mut emulators = Vec::new();
        for i in 0..5 {
            emulators.push(EmulatorProcess {
                pid: 1000 + i,
                port: None,
                avd_name: None,
                cpu_usage: 2.0,
                ram_mb: 20.0,
                threads: 1,
                context_switches: 10,
                gpu_load: None,
            });
        }

        let stats = HostStats {
            cpu_usage: 10.0,
            ram_used_gb: 1.0,
            ram_total_gb: 4.0,
            ram_percent: 25.0,
            swap_used_gb: 0.0,
            swap_total_gb: 0.0,
            swap_percent: 0.0,
            disk_read_mbs: 0.0,
            disk_write_mbs: 0.0,
            emulators,
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
        };

        let guest_results = HashMap::new();
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();

        let dashboard = build_dashboard(&stats, &guest_results, &mut prev_stats, 1, 120, true, "Specs", true, &online_indicator_until);

        assert!(dashboard.contains("Unmapped Emulator CPU"));
        assert!(dashboard.contains("1000, 1001, 1002, 1003 ... (+1 more)"));
    }

    #[test]
    fn test_print_row_nan_placeholder() {
        let stats = HostStats {
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
        };

        let mut guest_results = HashMap::new();
        guest_results.insert(
            "emulator-5554".to_string(),
            ("Mock Device".to_string(), false, "NaN%".to_string(), "NaN%".to_string(), "NaN%".to_string(), None, None, 0, None, false, false)
        );
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();

        let dashboard = build_dashboard(&stats, &guest_results, &mut prev_stats, 1, 80, true, "Specs", true, &online_indicator_until);

        assert!(dashboard.contains("Total Device CPU Usage"));
    }

    #[test]
    fn test_ansi_stripping_truncation() {
        let stats = HostStats {
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
        };

        let mut guest_results = HashMap::new();
        let long_val = "\x1b[91mVery Long String That Will Be Truncated\x1b[0m";
        guest_results.insert(
            "emulator-5554".to_string(),
            ("Mock Device".to_string(), false, "10%".to_string(), "5%".to_string(), long_val.to_string(), None, None, 0, None, false, false)
        );
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();

        let dashboard = build_dashboard(&stats, &guest_results, &mut prev_stats, 1, 80, true, "Specs", true, &online_indicator_until);

        assert!(dashboard.contains("Very Long"));
    }

}
