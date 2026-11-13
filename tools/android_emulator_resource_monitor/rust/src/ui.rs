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
#[path = "../tests/ui_test.rs"]
mod tests;
