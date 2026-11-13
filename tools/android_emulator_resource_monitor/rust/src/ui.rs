use crate::host::HostStats;
use crate::netsim::NetsimDashboardSnapshot;
use crate::provider::GuestStats;
use regex::Regex;
use std::collections::HashMap;
use tokio::time::Instant;

const MIN_DASHBOARD_WIDTH: u16 = 76;
const MAX_DASHBOARD_WIDTH: u16 = 120;
const BAR_WIDTH: usize = 20;
const LABEL_COLUMN_WIDTH: usize = 30;
const LEFT_BORDER_SEGMENT_WIDTH: usize = 32;
const RIGHT_BORDER_OFFSET: usize = 35;
const BAR_ROW_OVERHEAD: usize = 60;
const FULL_ROW_OVERHEAD: usize = 37;
const TREND_THRESHOLD: f32 = 0.5;
const MAX_UNMAPPED_PIDS_SHOWN: usize = 4;
const ALERT_RED_BOLD: &str = "\x1b[91m\x1b[1m";
const BORDER_STYLE: &str = "\x1b[94m\x1b[1m";

/// Per-device display state rendered in a guest section of the terminal dashboard.
#[derive(Debug, Clone, PartialEq)]
pub struct GuestUiStats {
    pub model: String,
    pub avd_name: Option<String>,
    pub cpu_total: String,
    pub cpu_adjusted: String,
    pub ram: String,
    pub data_space: Option<String>,
    pub swap: Option<String>,
    pub err_count: u32,
    pub is_disconnected: bool,
    pub recording_boot_stats: bool,
}

impl GuestUiStats {
    pub fn from_guest_stats(stats: &GuestStats, recording_boot_stats: bool) -> Self {
        Self {
            model: stats.model.clone(),
            avd_name: None,
            cpu_total: stats.cpu_total.clone(),
            cpu_adjusted: stats.cpu_adjusted.clone(),
            ram: stats.ram.clone(),
            data_space: stats.data_space.clone(),
            swap: stats.swap.clone(),
            err_count: stats.err_count,
            is_disconnected: false,
            recording_boot_stats,
        }
    }

    pub fn with_avd_name(mut self, avd_name: Option<String>) -> Self {
        self.avd_name = avd_name;
        self
    }

    pub fn calculating(
        model: String,
        avd_name: Option<String>,
        recording_boot_stats: bool,
    ) -> Self {
        Self {
            model,
            avd_name,
            cpu_total: "Calculating...".to_string(),
            cpu_adjusted: "Calculating...".to_string(),
            ram: "Calculating...".to_string(),
            data_space: None,
            swap: Some("Calculating...".to_string()),
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats,
        }
    }

    /// Marks a result shown again because this tick's query is still running.
    pub fn mark_timed_out(&mut self) {
        if !self.cpu_adjusted.contains("Timeout") {
            self.cpu_adjusted.push_str(" (Timeout)");
        }
    }

    pub fn disconnected(model: String, avd_name: Option<String>) -> Self {
        Self {
            model,
            avd_name,
            cpu_total: "N/A".to_string(),
            cpu_adjusted: "N/A".to_string(),
            ram: "N/A".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: true,
            recording_boot_stats: false,
        }
    }
}

/// Inputs required to render one frame of the terminal dashboard.
pub struct DashboardInput<'a> {
    pub host_stats: &'a HostStats,
    pub guest_results: &'a HashMap<String, GuestUiStats>,
    pub prev_stats: &'a mut HashMap<String, f32>,
    pub iter_count: u64,
    pub term_width: u16,
    pub is_once: bool,
    pub hardware_specs: &'a str,
    pub force_clear: bool,
    pub online_indicator_until: &'a HashMap<String, Instant>,
    pub netsim_snapshot: Option<&'a NetsimDashboardSnapshot>,
    /// The host sample did not finish this tick; its last values are shown again.
    pub host_timed_out: bool,
}

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

pub(crate) fn ansi_regex() -> &'static Regex {
    static RE_ANSI: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE_ANSI.get_or_init(|| Regex::new(r"(?i)\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])").unwrap())
}

fn model_clean_regex() -> &'static Regex {
    static RE_MODEL_CLEAN: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE_MODEL_CLEAN
        .get_or_init(|| Regex::new(r" \| Android [^|]+ \(API ([^|]+)\) \| Build: ([^|]+)").unwrap())
}

/// Terminal columns taken by `s`: ANSI escapes take none, and each char one
/// (unlike `str::len`, which counts UTF-8 bytes, e.g. 3 for '…').
fn visible_width(re_ansi: &Regex, s: &str) -> usize {
    re_ansi.replace_all(s, "").chars().count()
}

fn truncate_or_pad(s: &str, target_width: usize) -> String {
    let char_len = s.chars().count();
    if char_len > target_width {
        let mut truncated: String = s.chars().take(target_width.saturating_sub(1)).collect();
        truncated.push('…');
        truncated
    } else {
        let spaces = " ".repeat(target_width - char_len);
        format!("{s}{spaces}")
    }
}

fn format_row_value(value_str: &str, is_alert: bool, target_width: usize) -> String {
    let clean = ansi_regex().replace_all(value_str, "");
    if is_alert {
        const ALERT_SUFFIX: &str = " [!!]";
        let max_base_len = target_width.saturating_sub(ALERT_SUFFIX.len());
        let char_len = clean.chars().count();
        let with_alert = if char_len > max_base_len {
            let mut truncated: String =
                clean.chars().take(max_base_len.saturating_sub(1)).collect();
            truncated.push('…');
            format!("{truncated}{ALERT_SUFFIX}")
        } else {
            format!("{clean}{ALERT_SUFFIX}")
        };
        truncate_or_pad(&with_alert, target_width)
    } else {
        truncate_or_pad(&clean, target_width)
    }
}

fn trend_arrow_for_delta(previous: f32, current: f32) -> &'static str {
    if current > previous + TREND_THRESHOLD {
        "↑"
    } else if current < previous - TREND_THRESHOLD {
        "↓"
    } else {
        "→"
    }
}

fn record_trend_arrow(
    prev_stats: &mut HashMap<String, f32>,
    key: &str,
    current: f32,
) -> &'static str {
    let previous = prev_stats.get(key).copied().unwrap_or(current);
    prev_stats.insert(key.to_string(), current);
    trend_arrow_for_delta(previous, current)
}

fn is_leading_na_placeholder(value_str: &str) -> bool {
    let clean = ansi_regex().replace_all(value_str, "");
    let head = clean.split('(').next().unwrap_or(&clean).trim_start();
    head.starts_with("N/A")
}

fn is_placeholder_value(value_str: &str, ts_val: f32) -> bool {
    is_leading_na_placeholder(value_str)
        || value_str.contains("Unsupported")
        || value_str.contains("No ADB")
        || value_str.contains("Error")
        || value_str.contains("Timeout")
        || value_str.contains("Hang")
        || value_str.contains("Calculating")
        || value_str.contains("NaN")
        || ts_val.is_nan()
}

fn is_row_alert(label: &str, value_str: &str, val_for_alert: f32) -> bool {
    if label.contains("Battery") {
        val_for_alert <= 10.0
    } else if value_str.contains("°C") {
        val_for_alert > 85.0
    } else if value_str.contains('%') {
        let threshold = if is_multi_core_cpu_label(label) { 750.0 } else { 90.0 };
        val_for_alert > threshold
    } else {
        false
    }
}

const BATTERY_COLOR_TIERS: &[(f32, &str)] = &[(20.0, Colors::GREEN), (10.0, Colors::YELLOW)];
const TEMP_COLOR_TIERS: &[(f32, &str)] = &[(85.0, ALERT_RED_BOLD), (70.0, Colors::YELLOW)];
const PAGING_COLOR_TIERS: &[(f32, &str)] = &[(5000.0, ALERT_RED_BOLD), (1000.0, Colors::YELLOW)];
const SPECIAL_CPU_COLOR_TIERS: &[(f32, &str)] =
    &[(750.0, ALERT_RED_BOLD), (350.0, Colors::RED), (150.0, Colors::YELLOW)];
const PERCENT_COLOR_TIERS: &[(f32, &str)] =
    &[(90.0, ALERT_RED_BOLD), (70.0, Colors::RED), (40.0, Colors::YELLOW)];

fn threshold_color(val: f32, tiers: &[(f32, &'static str)], default: &'static str) -> &'static str {
    for &(threshold, color) in tiers {
        if val > threshold {
            return color;
        }
    }
    default
}

fn is_multi_core_cpu_label(label: &str) -> bool {
    label.contains("Total Device CPU")
        || label.contains("Host QEMU CPU")
        || label.contains("Unmapped Emulator CPU")
        || label.contains("Network Simulator")
}

fn row_value_color(
    label: &str,
    value_str: &str,
    val_for_alert: f32,
    is_placeholder: bool,
    is_alert: bool,
) -> &'static str {
    if is_alert {
        return ALERT_RED_BOLD;
    }
    let base_color = if is_placeholder {
        Colors::CYAN
    } else if label.contains("Battery") {
        threshold_color(val_for_alert, BATTERY_COLOR_TIERS, ALERT_RED_BOLD)
    } else if value_str.contains("°C") {
        threshold_color(val_for_alert, TEMP_COLOR_TIERS, Colors::GREEN)
    } else if value_str.contains("pages/s")
        || value_str.contains("Out:")
        || value_str.contains("KB/s")
    {
        threshold_color(val_for_alert, PAGING_COLOR_TIERS, Colors::GREEN)
    } else if !value_str.contains('%') {
        Colors::CYAN
    } else if is_multi_core_cpu_label(label) {
        threshold_color(val_for_alert, SPECIAL_CPU_COLOR_TIERS, Colors::GREEN)
    } else {
        threshold_color(val_for_alert, PERCENT_COLOR_TIERS, Colors::GREEN)
    };

    if label.contains("Host QEMU")
        && !is_placeholder
        && matches!(base_color, Colors::GREEN | Colors::CYAN)
    {
        Colors::BLUE
    } else {
        base_color
    }
}

fn format_progress_bar(val_to_clamp: f32, bar_width: usize) -> String {
    let clamped_val = val_to_clamp.clamp(0.0, 100.0);
    let filled = ((clamped_val / 100.0) * bar_width as f32) as usize;
    let half = usize::from(((clamped_val / 100.0) * bar_width as f32 - filled as f32) >= 0.5);
    format!(
        "[{}{}{}]",
        "█".repeat(filled),
        if half == 1 { "▌" } else { "" },
        " ".repeat(bar_width.saturating_sub(filled + half))
    )
}

fn push_metric_row(
    buf: &mut Vec<String>,
    label: &str,
    value_str: &str,
    value_pct: f32,
    width: usize,
) {
    let clean = ansi_regex().replace_all(value_str, "");
    let head = clean.split('(').next().unwrap_or(&clean).trim_start();
    let parse_src =
        if !head.starts_with("N/A") && clean.contains("N/A") { head } else { value_str };
    let ts_val = crate::summary::get_val(parse_src, true);
    let raw_val = if ts_val.is_nan() { 0.0 } else { ts_val };
    let is_placeholder = is_placeholder_value(value_str, ts_val);
    let val_for_alert = if raw_val == 0.0 && value_pct > 0.0 { value_pct } else { raw_val };
    let is_alert = !is_placeholder && is_row_alert(label, value_str, val_for_alert);
    let color = row_value_color(label, value_str, val_for_alert, is_placeholder, is_alert);

    let has_bar = value_str.contains('%') || value_str.contains("°C");

    let mut line = String::new();
    line.push_str(BORDER_STYLE);
    line.push('│');
    line.push_str(Colors::RESET);
    line.push(' ');
    line.push_str(Colors::BOLD);
    line.push_str(&format!("{label:<LABEL_COLUMN_WIDTH$}"));
    line.push_str(Colors::RESET);
    line.push(' ');
    line.push_str(BORDER_STYLE);
    line.push('│');
    line.push_str(Colors::RESET);
    line.push(' ');
    line.push_str(color);

    if has_bar {
        let val_to_clamp = if is_placeholder { 0.0 } else { val_for_alert };
        let bar = format_progress_bar(val_to_clamp, BAR_WIDTH);
        let val_display = format_row_value(value_str, is_alert, width - BAR_ROW_OVERHEAD);
        line.push_str(&bar);
        line.push(' ');
        line.push_str(color);
        line.push_str(&val_display);
    } else {
        let val_display_full = format_row_value(value_str, is_alert, width - FULL_ROW_OVERHEAD);
        line.push_str(&val_display_full);
    }

    line.push(' ');
    line.push_str(Colors::RESET);
    line.push_str(BORDER_STYLE);
    line.push('│');
    line.push_str(Colors::RESET);
    buf.push(line);
}

fn render_header_section(buf: &mut Vec<String>, input: &DashboardInput<'_>, width: usize) {
    let re_ansi = ansi_regex();
    let c1 = BORDER_STYLE;
    let c2 = Colors::RESET;

    if !input.is_once {
        let esc = if input.force_clear { "\x1b[2J\x1b[H" } else { "\x1b[H\x1b[J" };
        buf.push(format!("{esc}\n"));
    }
    buf.push(format!("{c1}┌{}┐{c2}", "─".repeat(width - 2)));

    let logo = format!(
        "{}G{}o{}o{}g{}l{}e{}",
        Colors::BLUE,
        Colors::RED,
        Colors::YELLOW,
        Colors::BLUE,
        Colors::GREEN,
        Colors::RED,
        Colors::RESET
    );
    let title_text = "Android Emulator & Device Resource Monitor";
    let line_content = format!(" {logo}   {title_text}");
    let visible_len = visible_width(re_ansi, &line_content);
    let padding = (width - 2).saturating_sub(visible_len);
    buf.push(format!(
        "{c1}│{c2}{}{line_content}{}{c2}{c1}│{c2}",
        Colors::BOLD,
        " ".repeat(padding)
    ));
    buf.push(format!("{c1}├{}┤{c2}", "─".repeat(width - 2)));

    // Long specs are cut two columns short of the border, as in the Python dashboard.
    let specs_text = truncate_or_pad(&format!(" Host Specs: {}", input.hardware_specs), width - 4);
    buf.push(format!("{c1}│{c2}{}{specs_text}  {c2}{c1}│{c2}", Colors::DIM));
    buf.push(format!("{c1}├{}┤{c2}", "─".repeat(width - 2)));
}

fn push_section_banner(buf: &mut Vec<String>, title: &str, color: &str, width: usize) {
    let c1 = BORDER_STYLE;
    let c2 = Colors::RESET;
    let visible_len = visible_width(ansi_regex(), title);
    let padding = (width - 2).saturating_sub(visible_len);
    buf.push(format!(
        "{c1}│{c2}{color}{}{title}{}{c2}{c1}│{c2}",
        Colors::BOLD,
        " ".repeat(padding)
    ));
    buf.push(format!(
        "{c1}├{}┬{}┤{c2}",
        "─".repeat(LEFT_BORDER_SEGMENT_WIDTH),
        "─".repeat(width - RIGHT_BORDER_OFFSET)
    ));
}

fn render_host_system_rows(
    buf: &mut Vec<String>,
    host_stats: &HostStats,
    prev_stats: &mut HashMap<String, f32>,
    host_timed_out: bool,
    width: usize,
) {
    let cpu_str = if host_timed_out {
        format!("{:.1}% (Timeout)", host_stats.cpu_usage)
    } else {
        format!(
            "{:.1}% {}",
            host_stats.cpu_usage,
            record_trend_arrow(prev_stats, "host_cpu", host_stats.cpu_usage)
        )
    };
    push_metric_row(buf, "Complete System CPU", &cpu_str, host_stats.cpu_usage, width);

    let ram_str = format!(
        "{:.1}% ({:.1}GB/{:.1}GB) {}",
        host_stats.ram_percent,
        host_stats.ram_used_gb,
        host_stats.ram_total_gb,
        record_trend_arrow(prev_stats, "host_ram", host_stats.ram_percent)
    );
    push_metric_row(buf, "Complete System RAM", &ram_str, host_stats.ram_percent, width);

    let swap_str = format!(
        "{:.1}% ({:.1}GB/{:.1}GB) {}",
        host_stats.swap_percent,
        host_stats.swap_used_gb,
        host_stats.swap_total_gb,
        record_trend_arrow(prev_stats, "host_swap", host_stats.swap_percent)
    );
    push_metric_row(buf, "Backup Memory (Swap) Usage", &swap_str, host_stats.swap_percent, width);
    push_metric_row(buf, "Memory Swapping Activity", &host_stats.paging_str, 0.0, width);

    let gpu_str = host_stats
        .gpu_percent
        .map(|g| format!("{g:.1}%"))
        .unwrap_or_else(|| "N/A (Requires sudo or nvidia-smi)".to_string());
    push_metric_row(buf, "Host GPU Load", &gpu_str, host_stats.gpu_percent.unwrap_or(0.0), width);

    let gpu_mem_str =
        host_stats.gpu_memory_mb.map(|m| format!("{m:.1}MB")).unwrap_or_else(|| "N/A".to_string());
    push_metric_row(buf, "Host GPU Memory", &gpu_mem_str, 0.0, width);

    let temp_str = host_stats
        .thermal_temp
        .filter(|&t| t > 0.0)
        .map(|t| format!("{t:.1}°C"))
        .unwrap_or_else(|| "N/A".to_string());
    push_metric_row(buf, "Host Thermal Temperature", &temp_str, 0.0, width);

    let disk_str = format!(
        "Read: {:.1} MB/s | Write: {:.1} MB/s",
        host_stats.disk_read_mbs, host_stats.disk_write_mbs
    );
    push_metric_row(buf, "Host Disk I/O Rate", &disk_str, 0.0, width);

    if host_stats.battery_str != "N/A" {
        push_metric_row(buf, "Laptop Battery Status", &host_stats.battery_str, 0.0, width);
    }
}

fn render_unmapped_emulator_rows(
    buf: &mut Vec<String>,
    emulators: &[crate::host::EmulatorProcess],
    width: usize,
) {
    let unmapped: Vec<&crate::host::EmulatorProcess> =
        emulators.iter().filter(|e| e.port.is_none()).collect();
    if unmapped.is_empty() {
        return;
    }
    let sum_cpu: f32 = unmapped.iter().map(|e| e.cpu_usage).sum();
    let sum_ram: f64 = unmapped.iter().map(|e| e.ram_mb).sum();
    let pids: Vec<String> = unmapped.iter().map(|e| e.pid.to_string()).collect();
    let pid_str = if pids.len() > MAX_UNMAPPED_PIDS_SHOWN {
        format!(
            "{} ... (+{} more)",
            pids[..MAX_UNMAPPED_PIDS_SHOWN].join(", "),
            pids.len() - MAX_UNMAPPED_PIDS_SHOWN
        )
    } else {
        pids.join(", ")
    };
    push_metric_row(
        buf,
        "Unmapped Emulator CPU",
        &format!("{sum_cpu:.1}% (PIDs: {pid_str})"),
        sum_cpu,
        width,
    );
    push_metric_row(
        buf,
        "Unmapped Emulator RAM",
        &format!("{sum_ram:.1}MB (PIDs: {pid_str})"),
        0.0,
        width,
    );
}

fn render_host_netsim_rows(
    buf: &mut Vec<String>,
    host_stats: &HostStats,
    prev_stats: &mut HashMap<String, f32>,
    netsim_snapshot: Option<&NetsimDashboardSnapshot>,
    width: usize,
) {
    if host_stats.netsim_count > 0 {
        let netsim_cpu_str = format!(
            "{:.1}% ({} processes) {}",
            host_stats.netsim_cpu,
            host_stats.netsim_count,
            record_trend_arrow(prev_stats, "netsim_cpu", host_stats.netsim_cpu)
        );
        push_metric_row(
            buf,
            "Network Simulator CPU",
            &netsim_cpu_str,
            host_stats.netsim_cpu,
            width,
        );

        let netsim_ram_str =
            format!("{:.1}MB ({} processes)", host_stats.netsim_ram_mb, host_stats.netsim_count);
        push_metric_row(buf, "Network Simulator RAM", &netsim_ram_str, 0.0, width);
    } else {
        push_metric_row(buf, "Network Simulator CPU", "N/A", 0.0, width);
        push_metric_row(buf, "Network Simulator RAM", "N/A", 0.0, width);
    }

    if let Some(snap) = netsim_snapshot {
        if !snap.daemon_rows.is_empty() {
            for (lbl, val) in &snap.daemon_rows {
                push_metric_row(buf, lbl, val, 0.0, width);
            }
        } else if let Some(ref status_str) = snap.daemon_status_row {
            push_metric_row(buf, "Netsim Daemon", status_str, 0.0, width);
        }
    }
}

fn render_host_section(buf: &mut Vec<String>, input: &mut DashboardInput<'_>, width: usize) {
    let host_stats = input.host_stats;
    let prev_stats = &mut *input.prev_stats;
    let netsim_snapshot = input.netsim_snapshot;
    let section_title = format!(
        " HOST SYSTEM ({})",
        sysinfo::System::name().unwrap_or_else(|| "Unknown OS".to_string())
    );
    push_section_banner(buf, &section_title, Colors::YELLOW, width);
    render_host_system_rows(buf, host_stats, prev_stats, input.host_timed_out, width);
    render_unmapped_emulator_rows(buf, &host_stats.emulators, width);
    render_host_netsim_rows(buf, host_stats, prev_stats, netsim_snapshot, width);
}

fn render_empty_guest_section(buf: &mut Vec<String>, width: usize) {
    let c1 = BORDER_STYLE;
    let c2 = Colors::RESET;

    buf.push(format!(
        "{c1}├{}┴{}┤{c2}",
        "─".repeat(LEFT_BORDER_SEGMENT_WIDTH),
        "─".repeat(width - RIGHT_BORDER_OFFSET)
    ));
    push_section_banner(buf, " GUEST SYSTEM (ADB)", Colors::HEADER, width);
    push_metric_row(buf, "Guest CPU", "N/A (No devices connected)", 0.0, width);
    push_metric_row(buf, "Guest RAM Usage", "N/A", 0.0, width);
    push_metric_row(buf, "Guest Swap Memory", "N/A", 0.0, width);
}

fn format_guest_status_badge(guest: &GuestUiStats, is_online_flash: bool) -> (String, usize) {
    let status_tag = if guest.is_disconnected {
        "[DISCONNECTED]"
    } else if guest.recording_boot_stats {
        "[RECORDING BOOT STATS]"
    } else if is_online_flash {
        "[ONLINE]"
    } else {
        return (String::new(), 0);
    };
    let badge_color = if guest.is_disconnected {
        Colors::RED
    } else if guest.recording_boot_stats {
        Colors::YELLOW
    } else {
        Colors::GREEN
    };
    (format!(" {badge_color}{status_tag}{}", Colors::RESET), status_tag.len() + 1)
}

fn render_guest_header(
    buf: &mut Vec<String>,
    serial: &str,
    guest: &GuestUiStats,
    matched_emu: Option<&crate::host::EmulatorProcess>,
    online_indicator_until: &HashMap<String, Instant>,
    width: usize,
) {
    let re_ansi = ansi_regex();
    let re_model_clean = model_clean_regex();
    let c1 = BORDER_STYLE;
    let c2 = Colors::RESET;

    buf.push(format!(
        "{c1}├{}┴{}┤{c2}",
        "─".repeat(LEFT_BORDER_SEGMENT_WIDTH),
        "─".repeat(width - RIGHT_BORDER_OFFSET)
    ));

    let clean_model = re_model_clean.replace(&guest.model, " | API $1 | $2");
    let avd_name_str = matched_emu
        .and_then(|emu| emu.avd_name.as_deref())
        .or(guest.avd_name.as_deref())
        .unwrap_or(serial);
    let display_name = if clean_model.is_empty() {
        avd_name_str.to_string()
    } else {
        format!("{avd_name_str} | {clean_model}")
    };

    let is_online_flash =
        online_indicator_until.get(serial).map(|&t| Instant::now() < t).unwrap_or(false);
    let (badge_formatted, v_badge_len) = format_guest_status_badge(guest, is_online_flash);

    let mut inside_text = format!(" GUEST SYSTEM ({display_name})");
    let mut v_title_len_final = visible_width(re_ansi, &inside_text);
    if v_title_len_final + v_badge_len > width - 4 {
        let max_title_len = (width - 4).saturating_sub(v_badge_len + 1);
        inside_text = inside_text.chars().take(max_title_len).collect::<String>();
        inside_text.push('…');
        v_title_len_final = visible_width(re_ansi, &inside_text);
    }

    let padding = (width - 2).saturating_sub(v_title_len_final + v_badge_len);
    buf.push(format!(
        "{c1}│{c2}{}{}{inside_text}{badge_formatted}{}{c1}│{c2}",
        Colors::GREEN,
        Colors::BOLD,
        " ".repeat(padding)
    ));
    buf.push(format!(
        "{c1}├{}┬{}┤{c2}",
        "─".repeat(LEFT_BORDER_SEGMENT_WIDTH),
        "─".repeat(width - RIGHT_BORDER_OFFSET)
    ));
}

fn render_single_guest_section(
    buf: &mut Vec<String>,
    serial: &str,
    guest: &GuestUiStats,
    input: &mut DashboardInput<'_>,
    width: usize,
) {
    let port_emu = input
        .host_stats
        .emulators
        .iter()
        .find(|emu| crate::serial_matches_emu_port(serial, emu.port));
    let matched_emu = if guest.is_disconnected { None } else { port_emu };
    render_guest_header(buf, serial, guest, matched_emu, input.online_indicator_until, width);

    push_metric_row(buf, "Total Device CPU Usage", &guest.cpu_total, 0.0, width);

    let guest_real_trend = if guest.cpu_adjusted.contains('%')
        && !guest.cpu_adjusted.contains("Timeout")
    {
        match guest.cpu_adjusted.split('%').next().unwrap_or("").parse::<f32>() {
            Ok(val) if val.is_finite() => {
                let arrow = record_trend_arrow(input.prev_stats, &format!("{serial}_real"), val);
                format!("{} {arrow}", guest.cpu_adjusted)
            }
            _ => guest.cpu_adjusted.clone(),
        }
    } else {
        guest.cpu_adjusted.clone()
    };

    push_metric_row(buf, "Adjusted CPU Load", &guest_real_trend, 0.0, width);
    push_metric_row(buf, "Guest RAM Usage", &guest.ram, 0.0, width);

    let disk_str = guest.data_space.as_deref().unwrap_or("N/A");
    push_metric_row(buf, "Guest Disk Space", disk_str, 0.0, width);

    if let Some(emu) = matched_emu {
        let qemu_ram_str =
            format!("{:.1} MB ({} thr, {} cs)", emu.ram_mb, emu.threads, emu.context_switches);
        push_metric_row(
            buf,
            "Host QEMU CPU",
            &format!("{:.1}%", emu.cpu_usage),
            emu.cpu_usage,
            width,
        );
        push_metric_row(buf, "Host QEMU RAM", &qemu_ram_str, 0.0, width);
    }

    let swap_str = guest.swap.as_deref().unwrap_or("N/A");
    push_metric_row(buf, "Guest Swap Memory", swap_str, 0.0, width);

    if let Some(gpu) = matched_emu.and_then(|emu| emu.gpu_load) {
        push_metric_row(buf, "Host QEMU GPU Load", &format!("{gpu:.1}%"), gpu, width);
    }

    let errs_str = if guest.is_disconnected
        || guest.ram.starts_with("N/A")
        || guest.ram.starts_with("Calculating")
    {
        "N/A".to_string()
    } else {
        format!("{} errs", guest.err_count)
    };
    push_metric_row(buf, "Guest Logcat Errors", &errs_str, 0.0, width);
    if !guest.is_disconnected || port_emu.is_none() {
        if let Some(ns_row) = input
            .netsim_snapshot
            .and_then(|snap| snap.emulator_rows.get(serial))
            .filter(|row| !guest.is_disconnected || !row.starts_with("netsim: CONNECT"))
        {
            push_metric_row(buf, "Netsim Status", ns_row, 0.0, width);
        }
    }
}

fn render_guests_section(buf: &mut Vec<String>, input: &mut DashboardInput<'_>, width: usize) {
    if input.guest_results.is_empty() {
        render_empty_guest_section(buf, width);
        return;
    }

    let mut guest_keys: Vec<&String> = input.guest_results.keys().collect();
    guest_keys.sort();

    for serial in guest_keys {
        let guest = &input.guest_results[serial];
        render_single_guest_section(buf, serial, guest, input, width);
    }
}

fn render_footer_section(buf: &mut Vec<String>, iter_count: u64, is_once: bool, width: usize) {
    let c1 = BORDER_STYLE;
    let c2 = Colors::RESET;

    buf.push(format!(
        "{c1}└{}┴{}┘{c2}",
        "─".repeat(LEFT_BORDER_SEGMENT_WIDTH),
        "─".repeat(width - RIGHT_BORDER_OFFSET)
    ));

    let dots = ".".repeat((iter_count % 4) as usize);
    let msg = if is_once {
        format!("Last Updated: {}", chrono::Local::now().format("%H:%M:%S"))
    } else {
        format!(
            "Press Ctrl+C to exit. Last Updated: {} | Refreshing{dots:<3}",
            chrono::Local::now().format("%H:%M:%S"),
        )
    };
    buf.push(format!("\n{}{msg}{}", Colors::YELLOW, Colors::RESET));
}

pub fn build_dashboard(mut input: DashboardInput<'_>) -> String {
    let mut buf = Vec::new();
    let width =
        input.term_width.saturating_sub(1).clamp(MIN_DASHBOARD_WIDTH, MAX_DASHBOARD_WIDTH) as usize;

    render_header_section(&mut buf, &input, width);
    render_host_section(&mut buf, &mut input, width);
    render_guests_section(&mut buf, &mut input, width);
    render_footer_section(&mut buf, input.iter_count, input.is_once, width);

    buf.join("\n")
}

pub fn print_dashboard(input: DashboardInput<'_>) {
    let s = build_dashboard(input);
    println!("{}", s);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::host::{EmulatorProcess, HostStats};
    use std::collections::HashMap;

    #[test]
    fn test_unmapped_emulators_ui() {
        let emulators = vec![EmulatorProcess {
            pid: 1234,
            port: None,
            avd_name: None,
            cpu_usage: 10.0,
            ram_mb: 100.0,
            threads: 4,
            context_switches: 100,
            gpu_load: None,
        }];

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
            netsim_daemons: vec![],
            emu_groups: HashMap::new(),
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

        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 80,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });

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
            netsim_daemons: vec![],
            emu_groups: HashMap::new(),
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

        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 120,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });

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
            netsim_daemons: vec![],
            emu_groups: HashMap::new(),
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
            GuestUiStats {
                model: "Mock Device".to_string(),
                avd_name: None,
                cpu_total: "NaN%".to_string(),
                cpu_adjusted: "NaN%".to_string(),
                ram: "NaN%".to_string(),
                data_space: None,
                swap: None,
                err_count: 0,
                is_disconnected: false,
                recording_boot_stats: false,
            },
        );
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();

        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 80,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });

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
            netsim_daemons: vec![],
            emu_groups: HashMap::new(),
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
            GuestUiStats {
                model: "Mock Device".to_string(),
                avd_name: None,
                cpu_total: "10%".to_string(),
                cpu_adjusted: "5%".to_string(),
                ram: long_val.to_string(),
                data_space: None,
                swap: None,
                err_count: 0,
                is_disconnected: false,
                recording_boot_stats: false,
            },
        );
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();

        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 80,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });

        assert!(dashboard.contains("Very Long"));
    }

    #[test]
    fn test_truncated_guest_title_keeps_border_aligned() {
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
            netsim_daemons: vec![],
            emu_groups: HashMap::new(),
            gpu_percent: None,
            gpu_memory_mb: None,
            thermal_temp: None,
            paging_str: "N/A".to_string(),
            paging_in: 0.0,
            paging_out: 0.0,
            battery_str: "N/A".to_string(),
            battery_percent: None,
        };
        // Long enough that the title is cut and ends in '…' (3 UTF-8 bytes,
        // one column) ahead of the badge.
        let model = "x86_64 glasses emulator | Android 16 (API 36) | Build: BGQ1.260312.001.A2 | Res: 480x480";
        let mut guest_results = HashMap::new();
        guest_results.insert(
            "emulator-5554".to_string(),
            GuestUiStats {
                model: model.to_string(),
                avd_name: None,
                cpu_total: "10%".to_string(),
                cpu_adjusted: "5%".to_string(),
                ram: "26%".to_string(),
                data_space: None,
                swap: None,
                err_count: 0,
                is_disconnected: false,
                recording_boot_stats: true,
            },
        );
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();
        let re_ansi = ansi_regex();

        for term_width in [80u16, 121] {
            let dashboard = build_dashboard(DashboardInput {
                host_stats: &stats,
                guest_results: &guest_results,
                prev_stats: &mut prev_stats,
                iter_count: 1,
                term_width,
                is_once: true,
                hardware_specs: "Specs",
                force_clear: true,
                online_indicator_until: &online_indicator_until,
                netsim_snapshot: None,
                host_timed_out: false,
            });
            let top = dashboard.lines().find(|l| l.contains('┌')).unwrap();
            let header = dashboard.lines().find(|l| l.contains("[RECORDING BOOT STATS]")).unwrap();
            let header_plain = re_ansi.replace_all(header, "");
            assert!(header_plain.contains('…'), "{header_plain}");
            assert!(header_plain.ends_with('│'), "{header_plain}");
            assert_eq!(
                visible_width(re_ansi, header),
                visible_width(re_ansi, top),
                "{header_plain}"
            );
        }
    }

    #[test]
    fn test_long_host_specs_are_cut_to_the_box_width() {
        let specs =
            "Linux | AMD EPYC 7B13 64-Core Processor | 251.8 GB RAM | GPU: NVIDIA L4 (22.5GB)";
        let short_specs = "Linux | 8 GB RAM";
        let re_ansi = ansi_regex();
        for (hardware_specs, term_width) in [(specs, 77u16), (specs, 121), (short_specs, 77)] {
            let mut buf = Vec::new();
            render_header_section(
                &mut buf,
                &DashboardInput {
                    host_stats: &crate::initial_host_stats(),
                    guest_results: &HashMap::new(),
                    prev_stats: &mut HashMap::new(),
                    iter_count: 1,
                    term_width,
                    is_once: true,
                    hardware_specs,
                    force_clear: true,
                    online_indicator_until: &HashMap::new(),
                    netsim_snapshot: None,
                    host_timed_out: false,
                },
                usize::from(term_width - 1),
            );
            let top = buf.iter().find(|l| l.contains('┌')).unwrap();
            let line = buf.iter().find(|l| l.contains("Host Specs:")).unwrap();
            let plain = re_ansi.replace_all(line, "");
            assert_eq!(visible_width(re_ansi, line), visible_width(re_ansi, top), "{plain}");
            assert!(plain.ends_with('│'), "{plain}");
            let fits = visible_width(re_ansi, &format!(" Host Specs: {hardware_specs}"))
                <= usize::from(term_width) - 5;
            assert_eq!(plain.contains(hardware_specs), fits, "{plain}");
            assert_eq!(plain.contains('…'), !fits, "{plain}");
        }
    }

    #[test]
    fn test_ui_row_helpers_and_progress_bar() {
        assert_eq!(format_progress_bar(0.0, 10), "[          ]");
        assert_eq!(format_progress_bar(50.0, 10), "[█████     ]");
        assert_eq!(format_progress_bar(55.0, 10), "[█████▌    ]");
        assert_eq!(format_progress_bar(150.0, 10), "[██████████]");

        assert!(is_row_alert("Laptop Battery Status", "8%", 8.0));
        assert!(!is_row_alert("Laptop Battery Status", "25%", 25.0));
        assert!(is_row_alert("Host Thermal Temperature", "90.0°C", 90.0));
        assert!(is_row_alert("Complete System CPU", "95.0%", 95.0));
        assert!(!is_row_alert("Host QEMU CPU", "200.0%", 200.0));
        assert!(is_row_alert("Host QEMU CPU", "800.0%", 800.0));

        let mut prev = HashMap::new();
        assert_eq!(record_trend_arrow(&mut prev, "host_cpu", 10.0), "→");
        assert_eq!(record_trend_arrow(&mut prev, "host_cpu", 11.0), "↑");
        assert_eq!(record_trend_arrow(&mut prev, "host_cpu", 9.0), "↓");
    }

    #[test]
    fn test_placeholder_no_alert_and_ansi_and_alert_badge_preserved() {
        let mut stats = crate::initial_host_stats();
        stats.ram_percent = 95.0;
        stats.ram_used_gb = 30.4;
        stats.ram_total_gb = 32.0;

        let mut guest_results = HashMap::new();
        guest_results.insert(
            "emulator-5554".to_string(),
            GuestUiStats {
                model: "Mock Device".to_string(),
                avd_name: None,
                cpu_total: "10%".to_string(),
                cpu_adjusted: "5%".to_string(),
                ram: "\x1b[91mVery Long String That Will Be Truncated\x1b[0m".to_string(),
                data_space: None,
                swap: None,
                err_count: 0,
                is_disconnected: false,
                recording_boot_stats: false,
            },
        );
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();
        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 80,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });

        assert!(
            !dashboard.contains("Calculating... [!!]"),
            "Placeholder battery status must not alert:\n{dashboard}"
        );
        let host_ram_line = dashboard.lines().find(|l| l.contains("Complete System RAM")).unwrap();
        assert!(
            host_ram_line.contains("[!!]"),
            "Alerting 95% RAM row at width 80 must preserve [!!] suffix:\n{host_ram_line}"
        );
        let re_ansi = ansi_regex();
        let top = dashboard.lines().find(|l| l.contains('┌')).unwrap();
        let guest_ram_line = dashboard.lines().find(|l| l.contains("Guest RAM Usage")).unwrap();
        assert_eq!(
            visible_width(re_ansi, guest_ram_line),
            visible_width(re_ansi, top),
            "ANSI-colored value row must keep right border aligned:\n{guest_ram_line}"
        );
    }

    #[test]
    fn test_guest_adjusted_cpu_trend_arrow_updates_across_frames() {
        let stats = crate::initial_host_stats();
        let mut prev_stats = HashMap::new();
        let online_indicator_until = HashMap::new();

        let mut guest_results = HashMap::new();
        guest_results.insert(
            "emulator-5554".to_string(),
            GuestUiStats {
                model: "Pixel 6".to_string(),
                avd_name: None,
                cpu_total: "40%".to_string(),
                cpu_adjusted: "10.0% (over 4 cores)".to_string(),
                ram: "50.0% (2.0GB/4.0GB)".to_string(),
                data_space: None,
                swap: None,
                err_count: 0,
                is_disconnected: false,
                recording_boot_stats: false,
            },
        );

        let frame1 = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 120,
            is_once: false,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        assert!(frame1.contains("10.0% (over 4 cores) →"));

        guest_results.get_mut("emulator-5554").unwrap().cpu_adjusted =
            "80.0% (over 4 cores)".to_string();
        let frame2 = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 2,
            term_width: 120,
            is_once: false,
            hardware_specs: "Specs",
            force_clear: false,
            online_indicator_until: &online_indicator_until,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        assert!(
            frame2.contains("80.0% (over 4 cores) ↑"),
            "Expected upward trend arrow on frame 2, got:\n{frame2}"
        );
    }

    #[test]
    fn test_ui_placeholder_color_qemu_ram_blue_and_offline_errs_na() {
        let mut stats = crate::initial_host_stats();
        stats.battery_str = "N/A".to_string();
        stats.emulators = vec![EmulatorProcess {
            pid: 100,
            port: Some(5554),
            avd_name: Some("Pixel_6".to_string()),
            cpu_usage: 50.0,
            ram_mb: 2048.0,
            threads: 40,
            context_switches: 0,
            gpu_load: None,
        }];
        let mut guest_results = HashMap::new();
        guest_results.insert(
            "emulator-5554".to_string(),
            GuestUiStats {
                model: "Pixel 6".to_string(),
                avd_name: None,
                cpu_total: "NaN%".to_string(),
                cpu_adjusted: "N/A (offline)".to_string(),
                ram: "N/A (offline)".to_string(),
                data_space: None,
                swap: None,
                err_count: 0,
                is_disconnected: false,
                recording_boot_stats: true,
            },
        );
        let mut prev_stats = HashMap::new();
        let online = HashMap::new();
        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 1,
            term_width: 100,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &online,
            netsim_snapshot: None,
            host_timed_out: false,
        });
        let qemu_ram_line = dashboard.lines().find(|l| l.contains("Host QEMU RAM")).unwrap();
        let cpu_tot_line =
            dashboard.lines().find(|l| l.contains("Total Device CPU Usage")).unwrap();
        let errs_line = dashboard.lines().find(|l| l.contains("Guest Logcat Errors")).unwrap();
        assert!(
            qemu_ram_line.contains("\x1b[94m2048.0 MB"),
            "Host QEMU RAM should be colored BLUE (\\x1b[94m) like Python, got: {qemu_ram_line:?}"
        );
        assert!(
            cpu_tot_line.contains("\x1b[96m["),
            "NaN% placeholder row should be colored CYAN (\\x1b[96m), got: {cpu_tot_line:?}"
        );
        assert!(
            errs_line.contains("N/A"),
            "Offline guest should show N/A for Guest Logcat Errors, got: {errs_line:?}"
        );
    }

    #[test]
    fn test_timed_out_host_and_guest_rows_show_timeout_without_trend() {
        let mut stats = crate::initial_host_stats();
        stats.cpu_usage = 42.0;
        let mut guest = GuestUiStats {
            model: "Pixel 6".to_string(),
            avd_name: None,
            cpu_total: "40%".to_string(),
            cpu_adjusted: "10.0% (over 4 cores)".to_string(),
            ram: "50.0% (2.0GB/4.0GB)".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: false,
        };
        guest.mark_timed_out();
        guest.mark_timed_out();
        assert_eq!(guest.cpu_adjusted, "10.0% (over 4 cores) (Timeout)");
        let guest_results = HashMap::from([("emulator-5554".to_string(), guest)]);
        let mut prev_stats = HashMap::new();
        let dashboard = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 2,
            term_width: 120,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &HashMap::new(),
            netsim_snapshot: None,
            host_timed_out: true,
        });
        let cpu_line = dashboard.lines().find(|l| l.contains("Complete System CPU")).unwrap();
        assert!(cpu_line.contains("42.0% (Timeout)"), "{cpu_line}");
        assert!(!cpu_line.contains('→'), "{cpu_line}");
        let adj_line = dashboard.lines().find(|l| l.contains("Adjusted CPU Load")).unwrap();
        assert!(adj_line.contains("(over 4 cores) (Timeout)"), "{adj_line}");
        assert!(!adj_line.contains('→'), "{adj_line}");
        assert!(!prev_stats.contains_key("host_cpu"));
    }

    #[test]
    fn test_physical_and_disconnected_cards_omit_absent_qemu_and_replacement_netsim() {
        let mut stats = crate::initial_host_stats();
        stats.emulators = vec![EmulatorProcess {
            pid: 999,
            port: Some(5554),
            avd_name: Some("Pixel_9".to_string()),
            cpu_usage: 80.0,
            ram_mb: 2048.0,
            threads: 16,
            context_switches: 0,
            gpu_load: None,
        }];
        let usb_guest = GuestUiStats {
            model: "Pixel 9 Pro".to_string(),
            avd_name: None,
            cpu_total: "20%".to_string(),
            cpu_adjusted: "5.0% (over 4 cores)".to_string(),
            ram: "40.0% (3.2GB/8.0GB)".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            is_disconnected: false,
            recording_boot_stats: false,
        };
        let replaced_disc =
            GuestUiStats::disconnected("Pixel 6".to_string(), Some("Pixel_6".to_string()));
        let settled_disc =
            GuestUiStats::disconnected("Pixel 7".to_string(), Some("Pixel_7".to_string()));
        let guest_results = HashMap::from([
            ("HT4A12345678".to_string(), usb_guest),
            ("emulator-5554".to_string(), replaced_disc),
            ("emulator-5556".to_string(), settled_disc),
        ]);
        let ns_snap = NetsimDashboardSnapshot {
            daemon_rows: vec![],
            daemon_status_row: None,
            emulator_rows: HashMap::from([
                (
                    "emulator-5554".to_string(),
                    "netsim: CONNECTED 0.40s (wifi:0.5s) | disc=0".to_string(),
                ),
                (
                    "emulator-5556".to_string(),
                    "netsim: DISCONNECTED 0.30s (wifi:0.4s) | disc=1".to_string(),
                ),
            ]),
        };
        let mut prev_stats = HashMap::new();
        let dash = build_dashboard(DashboardInput {
            host_stats: &stats,
            guest_results: &guest_results,
            prev_stats: &mut prev_stats,
            iter_count: 2,
            term_width: 110,
            is_once: true,
            hardware_specs: "Specs",
            force_clear: true,
            online_indicator_until: &HashMap::new(),
            netsim_snapshot: Some(&ns_snap),
            host_timed_out: false,
        });
        assert!(!dash.contains("Host QEMU CPU"), "Spurious Host QEMU CPU row:\n{dash}");
        assert!(!dash.contains("Host QEMU RAM"), "Spurious Host QEMU RAM row:\n{dash}");
        assert!(
            !dash.contains("netsim: CONNECTED"),
            "Replacement emulator's live Netsim Status leaked into [DISCONNECTED] card:\n{dash}"
        );
        assert!(
            dash.contains("netsim: DISCONNECTED"),
            "Settled Netsim Status should remain visible when port is unoccupied:\n{dash}"
        );
    }
}
