use super::*;
use std::collections::HashSet;

pub fn parse_netsim_capture_list(output: &str) -> Result<Vec<CaptureRow>, String> {
    if output.contains("UNIMPLEMENTED")
        || output.contains("gRPC error")
        || output.contains("Connection refused")
        || output.contains("transport error")
        || output.contains("Unable to find netsim ini file")
        || output.contains("Failed to connect")
        || output.contains("error=")
    {
        return Err(output.trim().to_string());
    }
    let mut rows = Vec::new();
    for raw_line in output.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with("---") || line.starts_with("ID") {
            continue;
        }
        if let Some(row) =
            parse_pipe_capture_row(line).or_else(|| parse_whitespace_capture_row(line))
        {
            rows.push(row);
        }
    }
    Ok(rows)
}

fn parse_pipe_capture_row(line: &str) -> Option<CaptureRow> {
    if !line.contains('|') {
        return None;
    }
    let cols: Vec<&str> = line.split('|').map(str::trim).collect();
    let [id_str, dev, raw_kind, state, ts, rec_str, size_str, ..] = cols[..] else {
        return None;
    };
    let id = id_str.parse::<u32>().ok()?;
    Some(CaptureRow {
        id,
        device_name: dev.to_string(),
        chip_kind: normalize_chip_kind(raw_kind),
        raw_chip_kind: raw_kind.to_string(),
        state: state.to_lowercase(),
        timestamp: ts.to_string(),
        records: rec_str.parse().unwrap_or(0),
        size_bytes: size_str.parse().unwrap_or(0),
    })
}

fn parse_whitespace_capture_row(line: &str) -> Option<CaptureRow> {
    let parts: Vec<&str> = line.split_whitespace().collect();
    if parts.len() < 6 {
        return None;
    }
    let id = parts[0].parse::<u32>().ok()?;
    let size_bytes = parts[parts.len() - 1].parse::<u64>().ok()?;
    let records = parts[parts.len() - 2].parse::<u64>().unwrap_or(0);
    let timestamp = parts[parts.len() - 3].to_string();
    let state = parts[parts.len() - 4].to_lowercase();
    let raw_chip_kind = parts[parts.len() - 5].to_string();
    let device_name = parts[1..parts.len() - 5].join(" ");
    if (state != "on" && state != "off") || device_name.is_empty() {
        return None;
    }
    Some(CaptureRow {
        id,
        device_name,
        chip_kind: normalize_chip_kind(&raw_chip_kind),
        raw_chip_kind,
        state,
        timestamp,
        records,
        size_bytes,
    })
}

pub fn simulate_netsim_capture_filter(rows: &[CaptureRow], patterns: &[String]) -> Vec<CaptureRow> {
    if patterns.is_empty() {
        return rows.to_vec();
    }
    let up_pats: Vec<String> = patterns.iter().map(|p| p.to_uppercase()).collect();
    rows.iter()
        .filter(|r| {
            let hay_id = r.id.to_string().to_uppercase();
            let hay_dev = r.device_name.to_uppercase();
            let hay_kind = r.raw_chip_kind.to_uppercase();
            up_pats
                .iter()
                .all(|p| hay_id.contains(p) || hay_dev.contains(p) || hay_kind.contains(p))
        })
        .cloned()
        .collect()
}

pub fn select_safe_capture_patterns(
    rows: &[CaptureRow],
    target_device_name: &str,
    target_row: Option<&CaptureRow>,
) -> Option<Vec<String>> {
    if rows.is_empty() || target_device_name.is_empty() {
        return None;
    }
    if let Some(tr) = target_row {
        let pats =
            vec![tr.id.to_string(), target_device_name.to_string(), tr.raw_chip_kind.clone()];
        let matched = simulate_netsim_capture_filter(rows, &pats);
        if !matched.is_empty()
            && matched.iter().all(|m| m.device_name == target_device_name && m.id == tr.id)
        {
            return Some(pats);
        }
        return None;
    }
    let pats = vec![target_device_name.to_string()];
    let matched = simulate_netsim_capture_filter(rows, &pats);
    if !matched.is_empty() && matched.iter().all(|m| m.device_name == target_device_name) {
        return Some(pats);
    }
    None
}

/// Selects CLI filter patterns to disable `target_row` during monitor shutdown.
///
/// Unlike `select_safe_capture_patterns`, collateral substring matches are safe
/// when every extra matched row is already `"off"` or was also enabled by this
/// monitor (`monitor_enabled_ids`), so prefix collisions never leave a
/// monitor-enabled chip on while user-enabled captures remain untouched.
pub fn select_safe_disable_patterns(
    rows: &[CaptureRow],
    target_device_name: &str,
    target_row: &CaptureRow,
    monitor_enabled_ids: &HashSet<u32>,
) -> Option<Vec<String>> {
    if rows.is_empty() || target_device_name.is_empty() {
        return None;
    }
    let pats = vec![
        target_row.id.to_string(),
        target_device_name.to_string(),
        target_row.raw_chip_kind.clone(),
    ];
    let matched = simulate_netsim_capture_filter(rows, &pats);
    let hits_target =
        matched.iter().any(|m| m.device_name == target_device_name && m.id == target_row.id);
    let safe_collateral = matched.iter().all(|m| {
        (m.device_name == target_device_name && m.id == target_row.id)
            || m.state == "off"
            || monitor_enabled_ids.contains(&m.id)
    });
    if hits_target && safe_collateral { Some(pats) } else { None }
}

pub fn sanitize_netsim_filename(name: &str) -> String {
    name.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect()
}

#[cfg(test)]
#[path = "../../tests/netsim/capture_test.rs"]
mod tests;
