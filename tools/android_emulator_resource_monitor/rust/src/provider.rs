use crate::host::HostStats;
use std::collections::HashMap;
use std::future::Future;

/// Per-device metrics sampled via `adb` for a single connected Android guest.
#[derive(Debug, Clone, PartialEq)]
pub struct GuestStats {
    pub model: String,
    pub is_booting: bool,
    pub boot_state_known: bool,
    pub cpu_total: String,
    pub cpu_adjusted: String,
    pub ram: String,
    pub data_space: Option<String>,
    pub swap: Option<String>,
    pub err_count: u32,
    pub data_pct: Option<f32>,
}

impl GuestStats {
    /// Placeholder stats when the device's `adb` state is not `"device"` (e.g. `"offline"`).
    pub fn unavailable(known_model: &str, is_booting: bool, status: &str) -> Self {
        let na_status = format!("N/A ({status})");
        Self {
            model: if known_model.is_empty() {
                format!("Unknown ({status})")
            } else {
                known_model.to_string()
            },
            is_booting,
            boot_state_known: false,
            cpu_total: na_status.clone(),
            cpu_adjusted: na_status.clone(),
            ram: na_status,
            data_space: None,
            swap: None,
            err_count: 0,
            data_pct: None,
        }
    }

    /// Fallback stats when `adb shell` fails or times out on an online `"device"`.
    pub fn shell_failed(model: String, is_booting: bool, boot_state_known: bool) -> Self {
        Self {
            model,
            is_booting,
            boot_state_known,
            cpu_total: "N/A".to_string(),
            cpu_adjusted: "N/A".to_string(),
            ram: "N/A".to_string(),
            data_space: None,
            swap: None,
            err_count: 0,
            data_pct: None,
        }
    }
}

pub trait HostProvider: Send {
    fn get_host_stats(&mut self, elapsed_secs: f64) -> HostStats;
    fn get_cpu_cores(&self) -> usize;
}

pub trait GuestProvider: Send + Sync {
    fn get_devices(&self) -> impl Future<Output = HashMap<String, String>> + Send;
    fn get_guest_stats(
        &self,
        serial: &str,
        known_model: &str,
        is_booting: bool,
        status: &str,
    ) -> impl Future<Output = GuestStats> + Send;
}
