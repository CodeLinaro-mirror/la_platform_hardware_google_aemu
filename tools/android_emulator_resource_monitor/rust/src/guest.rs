use tokio::process::Command;
// Deleted unused import

pub fn parse_model_info(stdout: &str) -> Option<(String, bool)> {
    let parts: Vec<&str> = stdout.split("|!|").collect();
    if parts.len() >= 6 {
        let model = parts[0].trim().to_string();
        let version = parts[1].trim().to_string();
        let api = parts[2].trim().to_string();
        let build = parts[3].trim().to_string();
        let size_lower = parts[4].to_lowercase();
        let size = if size_lower.contains("size:") {
            size_lower.split("size:").last().unwrap_or("N/A").trim().to_string()
        } else {
            "N/A".to_string()
        };
        let is_booting = parts[5].trim() != "1";

        let display_name = format!("{} | Android {} (API {}) | Build: {} | Res: {}", model, version, api, build, size);
        Some((display_name, is_booting))
    } else {
        None
    }
}

pub fn parse_batch_output<'a>(stdout: &'a str) -> (String, bool, Option<String>, u32, Option<f32>, &'a str, Option<String>) {
    let parts: Vec<&str> = stdout.split("===ADB_MON_SPLIT===").collect();
    let mut mem_total = 0;
    let mut mem_avail = 0;
    let mut used = 0;
    let mut err_count = 0;
    let mut is_booting = true;
// Deleted obsolete variable
    let mut data_space: Option<String> = None;
    let mut data_pct: Option<f32> = None;
    let mut guest_ram = "N/A".to_string();

    if parts.len() >= 1 {
        for line in parts[0].lines() {
            let lp: Vec<&str> = line.split_whitespace().collect();
            if lp.len() >= 2 {
                if lp[0].starts_with("MemTotal") {
                    mem_total = lp[1].parse::<u64>().unwrap_or(0);
                } else if lp[0].starts_with("MemAvailable") {
                    mem_avail = lp[1].parse::<u64>().unwrap_or(0);
                }
            }
        }
        if mem_total > 0 {
            used = mem_total - mem_avail;
        }
    }

    if parts.len() >= 2 {
        for line in parts[1].lines() {
            if line.contains("/data") {
                let dp: Vec<&str> = line.split_whitespace().collect();
                let pct_idx = dp.iter().position(|s| s.contains('%'));
                if let Some(idx) = pct_idx {
                    data_pct = dp[idx].split('%').next().and_then(|s| s.parse::<f32>().ok());
                    if idx >= 2 {
                        let used_s = dp[idx - 2];
                        let avail_s = dp[idx - 1];
                        let pct = dp[idx];
                        data_space = Some(format!("{} ({} used/{} avail)", pct, used_s, avail_s));
                    } else {
                        let used_s = if dp.len() > 2 { dp[2] } else { "N/A" };
                        let avail_s = if dp.len() > 3 { dp[3] } else { "N/A" };
                        let pct = if idx < dp.len() { dp[idx] } else { "N/A%" };
                        data_space = Some(format!("{} ({} used/{} avail)", pct, used_s, avail_s));
                    }
                }
                break;
            }
        }
    }

    if parts.len() >= 3 {
        err_count = parts[2].trim().parse::<u32>().unwrap_or(0);
    }

    if parts.len() >= 4 {
        is_booting = parts[3].trim() != "1";
    }

    let mut top_output = "";
    if parts.len() >= 5 {
        top_output = parts[4];
    }

    let mut guest_swap = None;
    if parts.len() >= 6 {
        if let Some(last_line) = parts[5].trim().lines().last() {
            let cols: Vec<&str> = last_line.split_whitespace().collect();
            if cols.len() >= 3 {
                if let Ok(swap_kb) = cols[2].parse::<u64>() {
                    guest_swap = Some(format!("{:.1} MB", swap_kb as f32 / 1024.0));
                }
            }
        }
    }

    if mem_total > 0 {
        guest_ram = format!("{:.1}% ({:.1}GB/{:.1}GB)",
            (used as f32 / mem_total as f32) * 100.0,
            used as f32 / 1048576.0,
            mem_total as f32 / 1048576.0
        );
    }

    (guest_ram, is_booting, data_space, err_count, data_pct, top_output, guest_swap)
}

pub fn parse_top_output(stdout: &str) -> (String, String) {
    let mut guest_diff = "N/A".to_string();
    let mut guest_real = "N/A".to_string();

    let target_block = stdout.split("Tasks:").last().unwrap_or(
        stdout.split("User ").last().unwrap_or(stdout)
    );

    static RE_ANSI: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_ansi = RE_ANSI.get_or_init(|| regex::Regex::new(r"(?i)\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])").unwrap());

    static RE_USAGE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_usage = RE_USAGE.get_or_init(|| regex::Regex::new(r"(?i)(\d+)\s*%?\s*cpu").unwrap());

    static RE_IDLE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_idle = RE_IDLE.get_or_init(|| regex::Regex::new(r"(?i)(\d+)\s*%?\s*idle").unwrap());

    static RE_ID: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re_id = RE_ID.get_or_init(|| regex::Regex::new(r"(?i)(\d+(?:[.,]\d+)?)\s*%?\s*id").unwrap());

    for line in target_block.lines().take(10) {
        let clean_line = re_ansi.replace_all(line, "");
        let line_trimmed = clean_line.trim();

        let t_match = re_usage.captures(line_trimmed);
        let i_match = re_idle.captures(line_trimmed);

        if let (Some(t), Some(i)) = (t_match, i_match) {
            if let (Ok(t_val), Ok(i_val)) = (t[1].parse::<f32>(), i[1].parse::<f32>()) {
                if t_val >= 90.0 {
                    let diff = (t_val - i_val).max(0.0);
                    let cores = (t_val / 100.0).round().max(1.0);
                    guest_diff = format!("{:.0}%", diff);
                    guest_real = format!("{:.1}% (over {} cores)", diff / cores, cores);
                    break;
                }
            }
        } else if let Some(id_match) = re_id.captures(line_trimmed) {
            let id_str = id_match[1].replace(',', ".");
            if let Ok(id_val) = id_str.parse::<f32>() {
                let real_pct = (100.0 - id_val).max(0.0);
                guest_diff = format!("{:.1}%", real_pct);
                guest_real = format!("{:.1}% (Cores unknown)", real_pct);
                break;
            }
        }
    }

    (guest_diff, guest_real)
}

pub struct RealGuestProvider;

impl crate::provider::GuestProvider for RealGuestProvider {
    async fn get_devices(&self) -> std::collections::HashMap<String, String> {
    let mut devices = std::collections::HashMap::new();
        let output = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            Command::new("adb").arg("devices").output()
        ).await;

        let output = match output {
            Ok(Ok(out)) => out,
            Ok(Err(e)) => {
                println!("Failed to run adb devices: {}", e);
                return devices;
            }
            Err(_) => {
                println!("adb devices timed out");
                return devices;
            }
        };

        let stdout = String::from_utf8_lossy(&output.stdout);
        for line in stdout.lines().skip(1) {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 2 {
                devices.insert(parts[0].to_string(), parts[1].to_string());
            }
        }
    devices
}

    async fn get_guest_stats(&self, serial: &str, known_model: &str, mut is_booting: bool, status: &str) -> (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>) {
    if status != "device" {
        return (
            if known_model.is_empty() { format!("Unknown ({})", status) } else { known_model.to_string() },
            true,
            format!("N/A ({})", status),
            format!("N/A ({})", status),
            format!("N/A ({})", status),
            None,
            None,
            0,
            None,
        );
    }

    let mut model = known_model.to_string();
    let model_lower = model.to_lowercase();
    let should_retry = model.is_empty() ||
                       !model_lower.contains("android") ||
                       model_lower.contains("unknown model") ||
                       model_lower.contains("error:") ||
                       model_lower.contains("offline") ||
                       (model_lower.contains("res: n/a") && is_booting);

    if should_retry {
        let script = "m=$(getprop ro.product.model); v=$(getprop ro.build.version.release); a=$(getprop ro.build.version.sdk); b=$(getprop ro.build.id); w=$(wm size 2>/dev/null); c=$(getprop sys.boot_completed); echo \"$m|!|$v|!|$a|!|$b|!|$w|!|$c\"";
        let output = Command::new("adb")
            .arg("-s")
            .arg(serial)
            .arg("shell")
            .arg(script)
            .output()
            .await;

        if let Ok(output) = output {
            let stdout = String::from_utf8_lossy(&output.stdout);
            if let Some((m, b)) = parse_model_info(&stdout) {
                model = m;
                is_booting = b;
            }
        }
    }

    let mut guest_ram = "N/A".to_string();
    let mut data_space = None;
    let mut data_pct = None;
    let mut err_count = 0;
    let mut guest_swap = None;
    let batch_script = r#"cat /proc/meminfo; echo '===ADB_'"MON"'_SPLIT==='; df -h /data 2>/dev/null; echo '===ADB_'"MON"'_SPLIT==='; logcat -d -t 500 -b main,crash *:E 2>/dev/null | grep -v "^---------" | wc -l; echo '===ADB_'"MON"'_SPLIT==='; getprop sys.boot_completed; echo '===ADB_'"MON"'_SPLIT==='; (top -b -n 2 -d 0.2 || top -n 2 -d 1); echo '===ADB_'"MON"'_SPLIT==='; vmstat"#;

    let output = Command::new("adb")
        .arg("-s")
        .arg(serial)
        .arg("shell")
        .arg(batch_script)
        .output()
        .await;

    let mut guest_diff = "N/A".to_string();
    let mut guest_real = "N/A".to_string();

    if let Ok(output) = output {
        let stdout = String::from_utf8_lossy(&output.stdout);
        let (ram, booting, space, errs, pct, top_out, swap) = parse_batch_output(&stdout);
        guest_ram = ram;
        is_booting = booting;
        data_space = space;
        err_count = errs;
        data_pct = pct;
        guest_swap = swap;

        let (diff, real) = parse_top_output(&top_out);
        guest_diff = diff;
        guest_real = real;
    }

    (model, is_booting, guest_diff, guest_real, guest_ram, data_space, guest_swap, err_count, data_pct)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::provider::GuestProvider;

    #[test]
    fn test_parse_model_info() {
        let stdout = "Mock Phone|!|14|!|34|!|BuildID|!|Size: 1080x2400|!|1";
        let res = parse_model_info(stdout);
        assert!(res.is_some());
        let (model, is_booting) = res.unwrap();
        assert!(model.contains("Mock Phone"));
        assert!(!is_booting);
    }

    #[test]
    fn test_parse_model_info_robust_resolution() {
        let stdout = "Mock Phone|!|14|!|34|!|BuildID|!|Physical size: 450x450\nOverride size: 450x450|!|1";
        let res = parse_model_info(stdout);
        assert!(res.is_some());
        let (model, _) = res.unwrap();
        assert!(model.contains("Res: 450x450"), "Expected Res: 450x450, got {}", model);
    }

    #[test]
    fn test_parse_batch_output() {
        let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/dev/block/dm-5 10G 2G 8G 20% /data\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
        let (ram, is_booting, space, errs, pct, _, _swap) = parse_batch_output(stdout);
        assert!(ram.contains("50.0%"));
        assert!(!is_booting);
        assert_eq!(space, Some("20% (2G used/8G avail)".to_string()));
        assert_eq!(errs, 5);
        assert_eq!(pct, Some(20.0));
    }

    #[test]
    fn test_parse_batch_output_with_swap() {
        let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/dev/block/dm-5 10G 2G 8G 20% /data\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1\n===ADB_MON_SPLIT===\n(top out)\n===ADB_MON_SPLIT===\nprocs ------------memory------------ ---swap-- -----io---- --system- ----cpu----\n r  b    swpd    free   buff   cache   si   so    bi    bo   in   cs us sy id wa\n 0  0   93308 1191956 307452 1768432    2   31   515   300  132  236  1  2 97  0";
        let (ram, is_booting, space, errs, pct, _, swap) = parse_batch_output(stdout);
        assert!(ram.contains("50.0%"));
        assert!(!is_booting);
        assert_eq!(space, Some("20% (2G used/8G avail)".to_string()));
        assert_eq!(errs, 5);
        assert_eq!(pct, Some(20.0));
        assert_eq!(swap, Some("91.1 MB".to_string()));
    }

    #[test]
    fn test_parse_top_output() {
        let stdout = "Tasks: 100\n400% cpu 200% idle";
        let (diff, real) = parse_top_output(stdout);
        assert_eq!(diff, "200%");
        assert!(real.contains("50.0%"));
    }

    #[test]
    fn test_parse_batch_output_boot_correction() {
        let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/data space\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n0";
        let (_, is_booting, _, _, _, _, _) = parse_batch_output(stdout);
        assert!(is_booting);
    }

    #[test]
    fn test_parse_batch_output_safe_separator() {
        let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n/data space with random text\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
        let (ram, is_booting, _, _, _, _, _) = parse_batch_output(stdout);
        assert!(ram.contains("50.0%"));
        assert!(!is_booting);
    }

    #[test]
    fn test_parse_batch_output_df_variation() {
        let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n10G   2G   8G  20% /data\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
        let (ram, _, space, _, pct, _, _) = parse_batch_output(stdout);
        assert!(ram.contains("50.0%"));
        assert_eq!(space, Some("20% (2G used/8G avail)".to_string()));
        assert_eq!(pct, Some(20.0));
    }

    #[test]
    fn test_guest_cpu_cores_calculation() {
        let stdout = "Tasks: 100\n400% cpu 100% idle";
        let (diff, real) = parse_top_output(stdout);
        assert_eq!(diff, "300%");
        assert!(real.contains("over 4 cores"));
    }

    #[test]
    fn test_parse_batch_output_missing_data_partition() {
        let stdout = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n===ADB_MON_SPLIT===\n===ADB_MON_SPLIT===\n5\n===ADB_MON_SPLIT===\n1";
        let (ram, _, space, _, pct, _, _) = parse_batch_output(stdout);
        assert!(ram.contains("50.0%"));
        assert_eq!(space, None);
        assert_eq!(pct, None);
    }

    #[test]
    fn test_parse_top_output_no_cpu_line() {
        let stdout = "Tasks: 100\nNo CPU line here";
        let (diff, real) = parse_top_output(stdout);
        assert_eq!(diff, "N/A");
        assert_eq!(real, "N/A");
    }
    #[test]
    fn test_parse_top_output_with_io_wait() {
        let stdout = "400%cpu  5%user  1%nice  7%sys 215%idle 167%iow   5%irq   0%sirq   0%host";
        let (diff, real) = parse_top_output(stdout);
        assert_eq!(diff, "185%");
        assert!(real.contains("over 4 cores"));
    }

    #[test]
    fn test_retry_conditions() {
        let retry_cases = vec![
            ("", true),
            ("Unknown", true),
            ("offline", true),
            ("Error: some error", true),
            ("Android 12 | Res: N/A", true),
            ("Just some string", true),
        ];

        for (model, is_booting) in retry_cases {
            let model_lower = model.to_lowercase();
            let should_retry = model.is_empty() ||
                               !model_lower.contains("android") ||
                               model_lower.contains("unknown model") ||
                               model_lower.contains("error:") ||
                               model_lower.contains("offline") ||
                               (model_lower.contains("res: n/a") && is_booting);
            assert!(should_retry, "Failed for case: model='{}', is_booting={}", model, is_booting);
        }

        let no_retry_cases = vec![
            ("Android 12 | Res: 1080x2400", true),
            ("Android 12 | Res: N/A", false),
        ];

        for (model, is_booting) in no_retry_cases {
            let model_lower = model.to_lowercase();
            let should_retry = model.is_empty() ||
                               !model_lower.contains("android") ||
                               model_lower.contains("unknown model") ||
                               model_lower.contains("error:") ||
                               model_lower.contains("offline") ||
                               (model_lower.contains("res: n/a") && is_booting);
            assert!(!should_retry, "Failed for case: model='{}', is_booting={}", model, is_booting);
        }
    }

    #[tokio::test]
    async fn test_get_guest_stats_offline_returns_booting() {
        let provider = RealGuestProvider;
        let (model, is_booting, diff, _real, _ram, _space, _swap, _errs, _pct) = provider.get_guest_stats("emulator-5554", "Android 12", false, "offline").await;
        assert_eq!(model, "Android 12");
        assert!(is_booting);
        assert_eq!(diff, "N/A (offline)");
    }
}
