use chrono::TimeZone;
use std::collections::{BTreeMap, HashMap};

use super::*;

impl NetsimTracker {
    pub fn format_chip_summary(rec: &EmulatorNetsimRecord) -> String {
        if !rec.chip_attach_s.is_empty() {
            let parts: Vec<String> = rec
                .chip_attach_s
                .iter()
                .map(|(k, &(val_s, is_approx))| {
                    let prefix = if is_approx { "~" } else { "" };
                    format!("{}:{}{:.1}s", k, prefix, val_s)
                })
                .collect();
            return parts.join(",");
        }
        if rec.unknown_chip_count > 0 {
            return format!("chips={}", rec.unknown_chip_count);
        }
        "no-chips".to_string()
    }

    pub fn get_dashboard_snapshot(&self, now_wall: f64) -> NetsimDashboardSnapshot {
        let mut daemon_rows = Vec::new();
        let active_daemons: Vec<&DaemonRecord> =
            self.daemons.values().filter(|d| !d.exited && !d.client_mode_recorded).collect();
        for (idx, d) in active_daemons.iter().enumerate() {
            let uptime_s = (now_wall - d.start_wall).max(0.0) as u64;
            let ver = d.version.as_deref().unwrap_or("?");
            let ready_str = d
                .netsim_daemon_ready_s
                .map(|r| format!("{:.2}s", r))
                .unwrap_or_else(|| "N/A".to_string());
            let ui_str = if d.frontend_disabled { "off" } else { "on" };
            let log_str = if d.log_available { "ok" } else { "unavail" };
            let pcap_mode = if self.pcap_mgr.enabled {
                "auto"
            } else if d.pcap_flag {
                "on"
            } else {
                "off"
            };
            let pcap_str = format!("{} ({:.1}KB)", pcap_mode, d.pcap_live_bytes as f64 / 1024.0);
            let label = if active_daemons.len() == 1 {
                "Netsim Daemon".to_string()
            } else {
                format!("Netsim Daemon #{}", idx + 1)
            };
            let val = format!(
                "pid={} | v={} | up={}s | restarts={} | ready={} | conns={} | ui={} | log={} | pcap={}",
                d.pid,
                ver,
                uptime_s,
                self.restart_count,
                ready_str,
                d.active_socket_conns,
                ui_str,
                log_str,
                pcap_str
            );
            daemon_rows.push((label, val));
        }

        let daemon_status_row = if active_daemons.is_empty() {
            if !self.stale_inis.is_empty() {
                let names: Vec<String> = self
                    .stale_inis
                    .keys()
                    .filter_map(|p| p.file_name().map(|s| s.to_string_lossy().to_string()))
                    .collect();
                Some(format!("STALE_INI ({})", names.join(", ")))
            } else {
                Some("N/A".to_string())
            }
        } else {
            None
        };

        let mut best_by_serial: HashMap<&str, &EmulatorNetsimRecord> = HashMap::new();
        for rec in self.emulator_records.values() {
            let replace = match best_by_serial.get(rec.serial.as_str()) {
                None => true,
                Some(prev) => match (prev.is_inactive(), rec.is_inactive()) {
                    (true, false) => true,
                    (false, true) => false,
                    _ => rec.first_seen_mono >= prev.first_seen_mono,
                },
            };
            if replace {
                best_by_serial.insert(rec.serial.as_str(), rec);
            }
        }
        let mut emulator_rows = HashMap::new();
        for (serial, rec) in best_by_serial {
            let amb = if rec.ambiguous { " [ambiguous]" } else { "" };
            let conn_s = rec.netsim_connect_s.map(|c| format!(" {:.2}s", c)).unwrap_or_default();
            let chips = Self::format_chip_summary(rec);
            emulator_rows.insert(
                serial.to_string(),
                format!(
                    "netsim: {}{} ({}) | disc={}{}",
                    rec.state, conn_s, chips, rec.disconnect_count, amb
                ),
            );
        }

        NetsimDashboardSnapshot { daemon_rows, daemon_status_row, emulator_rows }
    }

    /// This tick's CSV columns: daemon totals and every emulator's state. Covers
    /// emulators adb does not list yet, since netsim can connect first, and ones
    /// that have exited.
    pub fn get_csv_fields(&self) -> BTreeMap<String, String> {
        let active_daemons: Vec<&DaemonRecord> =
            self.daemons.values().filter(|d| !d.exited && !d.client_mode_recorded).collect();
        let mut fields = BTreeMap::new();
        if let Some(d0) = active_daemons.first() {
            let conns: usize = active_daemons.iter().map(|d| d.active_socket_conns).sum();
            let devs: usize = active_daemons.iter().map(|d| d.joined_emulator_count).sum();
            fields.insert("netsim_up".to_string(), "1".to_string());
            fields.insert("netsim_pid".to_string(), d0.pid.to_string());
            fields.insert("netsim_restarts".to_string(), self.restart_count.to_string());
            fields.insert("netsim_conns".to_string(), conns.to_string());
            fields.insert("netsim_devices".to_string(), devs.to_string());
        } else {
            fields.insert("netsim_up".to_string(), "0".to_string());
            fields.insert("netsim_pid".to_string(), String::new());
            fields.insert("netsim_restarts".to_string(), self.restart_count.to_string());
            fields.insert("netsim_conns".to_string(), "0".to_string());
            fields.insert("netsim_devices".to_string(), "0".to_string());
        }
        for (k, rec) in &self.emulator_records {
            fields.insert(format!("netsim_state_{}", k), rec.state.clone());
        }
        fields
    }

    pub fn format_summary_sections(&self, now_mono: f64) -> Vec<String> {
        let mut lines = vec!["Netsim daemon lifecycle:".to_string()];
        if self.timeline.is_empty() && self.daemons.is_empty() {
            lines.push("  No netsimd daemon activity observed.".to_string());
        } else {
            for d in self.daemons.values() {
                let status = if d.exited {
                    format!("EXITED({})", d.exit_reason.as_deref().unwrap_or("unknown"))
                } else if d.orphaned {
                    "ORPHANED".to_string()
                } else {
                    "RUNNING".to_string()
                };
                let ready_s = d
                    .netsim_daemon_ready_s
                    .map(|r| format!("{:.2}s", r))
                    .unwrap_or_else(|| "N/A".to_string());
                lines.push(format!(
                    "  Daemon pid={} spawner={} version={} port={} ready_s={} status={} restarts={}",
                    d.pid,
                    d.spawner,
                    d.version.as_deref().unwrap_or("N/A"),
                    d.grpc_port
                        .map(|p| p.to_string())
                        .unwrap_or_else(|| "N/A".to_string()),
                    ready_s,
                    status,
                    self.restart_count
                ));
            }
            for (ts, ev) in &self.timeline {
                let dt = chrono::Local
                    .timestamp_opt(*ts as i64, 0)
                    .single()
                    .unwrap_or_else(chrono::Local::now);
                lines.push(format!("  [{}] {}", dt.format("%H:%M:%S"), ev));
            }
        }
        lines.push(String::new());

        lines.push("Netsim attach:".to_string());
        if self.emulator_records.is_empty() {
            lines.push("  No emulator netsim sessions recorded.".to_string());
        } else {
            for (key, rec) in &self.emulator_records {
                let ready_s = rec
                    .netsim_daemon_ready_s
                    .map(|r| format!("{:.2}s", r))
                    .unwrap_or_else(|| "N/A".to_string());
                let conn_s = rec
                    .netsim_connect_s
                    .map(|c| format!("{:.2}s", c))
                    .unwrap_or_else(|| "N/A".to_string());
                let chips = Self::format_chip_summary(rec);
                let wifi_s = rec
                    .wifi_associated_s
                    .map(|w| format!("{:.2}s", w))
                    .unwrap_or_else(|| "N/A".to_string());
                let mut tot_conn = rec.connected_total_s;
                if let Some(st) = rec.connected_start_mono {
                    let end = rec.missing_since_mono.unwrap_or(now_mono);
                    tot_conn += (end - st).max(0.0);
                }
                let amb = if rec.ambiguous { " (ambiguous)" } else { "" };
                let dev_label = rec.device_name.as_deref().unwrap_or(&rec.serial);
                lines.push(format!(
                    "  {} ({}){}: state={} ready_s={} connect_s={} chips=[{}] wifi_assoc_s={} disconnects={} connected_time={:.1}s",
                    key,
                    dev_label,
                    amb,
                    rec.state,
                    ready_s,
                    conn_s,
                    chips,
                    wifi_s,
                    rec.disconnect_count,
                    tot_conn
                ));
            }
        }
        lines.push(String::new());

        lines.push("Netsim pcap manifest:".to_string());
        if !self.pcap_mgr.enabled {
            lines.push("  Pcap collection disabled (pass --netsim-pcap).".to_string());
        } else {
            if self.pcap_mgr.copied_files.is_empty() {
                lines.push("  No pcap files copied.".to_string());
            } else {
                for (path, &(sz, _)) in &self.pcap_mgr.copied_files {
                    lines.push(format!("  {} ({} bytes)", path, sz));
                }
            }
            for note in &self.pcap_mgr.skipped_notes {
                lines.push(format!("  Note: {}", note));
            }
        }
        lines.push(String::new());
        lines
    }
}
