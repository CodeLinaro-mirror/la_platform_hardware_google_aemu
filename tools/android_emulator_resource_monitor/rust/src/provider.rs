use std::collections::HashMap;
use crate::host::HostStats;

pub trait HostProvider: Send {
    fn get_host_stats(&mut self, elapsed_secs: f64) -> HostStats;
    fn get_cpu_cores(&self) -> usize;
}

use std::future::Future;

pub trait GuestProvider: Send + Sync {
    fn get_devices(&self) -> impl Future<Output = HashMap<String, String>> + Send;
    fn get_guest_stats(&self, serial: &str, known_model: &str, is_booting: bool, status: &str) -> impl Future<Output = (String, bool, String, String, String, Option<String>, Option<String>, u32, Option<f32>)> + Send;
}
