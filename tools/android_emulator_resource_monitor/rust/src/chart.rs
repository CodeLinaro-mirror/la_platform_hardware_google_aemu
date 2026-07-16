struct BrailleCanvas {
    width: usize,
    height: usize,
    grid: Vec<Vec<bool>>,
}

impl BrailleCanvas {
    fn new(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            grid: vec![vec![false; width * 2]; height * 4],
        }
    }

    fn set(&mut self, x: usize, y: usize) {
        if x < self.width * 2 && y < self.height * 4 {
            self.grid[y][x] = true;
        }
    }

    fn line(&mut self, x0: i32, y0: i32, x1: i32, y1: i32) {
        let dx = (x1 - x0).abs();
        let dy = -(y1 - y0).abs();
        let sx = if x0 < x1 { 1 } else { -1 };
        let sy = if y0 < y1 { 1 } else { -1 };
        let mut err = dx + dy;

        let mut x = x0;
        let mut y = y0;

        loop {
            self.set(x as usize, y as usize);
            if x == x1 && y == y1 { break; }
            let e2 = 2 * err;
            if e2 >= dy {
                err += dy;
                x += sx;
            }
            if e2 <= dx {
                err += dx;
                y += sy;
            }
        }
    }

    fn get_row(&self, cy: usize) -> String {
        let mut result = String::new();
        for cx in 0..self.width {
            let mut offset = 0;
            let y_base = cy * 4;
            let x_base = cx * 2;

            if self.grid[y_base][x_base] { offset |= 1; }
            if self.grid[y_base + 1][x_base] { offset |= 2; }
            if self.grid[y_base + 2][x_base] { offset |= 4; }
            if self.grid[y_base][x_base + 1] { offset |= 8; }
            if self.grid[y_base + 1][x_base + 1] { offset |= 16; }
            if self.grid[y_base + 2][x_base + 1] { offset |= 32; }
            if self.grid[y_base + 3][x_base] { offset |= 64; }
            if self.grid[y_base + 3][x_base + 1] { offset |= 128; }

            let c = std::char::from_u32(0x2800 + offset).unwrap_or(' ');
            result.push(c);
        }
        result
    }
}

pub fn render_chart(points: &[(f32, f32)], width: usize, height: usize, title: &str) -> String {
    if points.len() < 2 {
        return format!("\n{}:\nNot enough data points for chart.", title);
    }

    let x_label = "Time (s)".to_string();
    let lower_title = title.to_lowercase();
    let y_label = if lower_title.contains("cpu") {
        "Usage (%)"
    } else if lower_title.contains("ram") || lower_title.contains("gpu") || lower_title.contains("swap") || lower_title.contains("fullness") {
        "Usage (%)"
    } else if lower_title.contains("disk read") {
        "Read (MB/s)"
    } else if lower_title.contains("disk write") {
        "Write (MB/s)"
    } else if lower_title.contains("thermal") {
        "Temp (°C)"
    } else if lower_title.contains("error") {
        "Errors"
    } else if lower_title.contains("switch") {
        "Switches"
    } else {
        ""
    }.to_string();

    let min_x = points.iter().map(|p| p.0).fold(f32::INFINITY, f32::min);
    let max_x = points.iter().map(|p| p.0).fold(f32::NEG_INFINITY, f32::max);
    let min_y = points.iter().map(|p| p.1).fold(f32::INFINITY, f32::min);
    let max_y = points.iter().map(|p| p.1).fold(f32::NEG_INFINITY, f32::max);

    let (min_y, max_y) = if min_y == max_y {
        if min_y == 0.0 {
            (-0.5, 0.5)
        } else {
            let margin = min_y.abs() * 0.1;
            (min_y - margin, max_y + margin)
        }
    } else {
        (min_y, max_y)
    };

    let w_dots = width * 2;
    let h_dots = height * 4;
    let mut aggregated_points = Vec::new();
    let span_x = max_x - min_x;

    if span_x > 0.0 {
        let mut bins = vec![Vec::new(); w_dots];
        let bin_scale = (w_dots - 1) as f32 / span_x;

        for &(x, y) in points {
            let bin_idx = (((x - min_x) * bin_scale) as usize).min(w_dots - 1);
            bins[bin_idx].push(y);
        }

        for (i, bin) in bins.iter().enumerate() {
            if !bin.is_empty() {
                let avg_y = bin.iter().sum::<f32>() / bin.len() as f32;
                let x_val = min_x + (i as f32 / (w_dots - 1) as f32) * span_x;
                aggregated_points.push((x_val, avg_y));
            }
        }
    } else {
        aggregated_points = points.to_vec();
    }

    if aggregated_points.len() < 2 {
        return format!("\n{}:\nNot enough aggregated data points for chart.", title);
    }

    let mut canvas = BrailleCanvas::new(width, height);

    let x_scale = if max_x > min_x { (w_dots - 1) as f32 / (max_x - min_x) } else { 1.0 };
    let y_scale = if max_y > min_y { (h_dots - 1) as f32 / (max_y - min_y) } else { 1.0 };

    for i in 0..aggregated_points.len() - 1 {
        let p0 = aggregated_points[i];
        let p1 = aggregated_points[i + 1];

        let x0 = ((p0.0 - min_x) * x_scale) as i32;
        let y0 = (h_dots as i32 - 1) - ((p0.1 - min_y) * y_scale) as i32;

        let x1 = ((p1.0 - min_x) * x_scale) as i32;
        let y1 = (h_dots as i32 - 1) - ((p1.1 - min_y) * y_scale) as i32;

        canvas.line(x0, y0, x1, y1);
    }

    let mut result = Vec::new();
    result.push(format!("{}:", title));
    result.push(format!("({}) ^", y_label));

    let y_step = (max_y - min_y) / height as f32;

    for cy in 0..height {
        let y_val = max_y - (cy as f32 * y_step);
        let row_str = canvas.get_row(cy);
        result.push(format!("{:>15.7} | ⡇{}", y_val, row_str));
    }

    let mut bottom_border = "----------------|-|".to_string();
    let segment_width = 10;
    let segments = width / segment_width;
    for _ in 0..segments {
        bottom_border.push_str(&"-".repeat(segment_width - 1));
        bottom_border.push('|');
    }
    bottom_border.push_str("-> (");
    bottom_border.push_str(&x_label);
    bottom_border.push(')');
    result.push(bottom_border);

    let mut x_vals = "                | ".to_string();
    let x_step = (max_x - min_x) / segments as f32;
    for i in 0..=segments {
        let val = min_x + i as f32 * x_step;
        let val = if val.abs() < 0.001 { 0.0 } else { val };
        let val_str = format!("{:.7}", val);
        let mut val_str_clean = val_str.trim_end_matches('0').trim_end_matches('.').to_string();
        if val_str_clean.is_empty() { val_str_clean = "0".to_string(); }
        if val_str_clean.len() > 9 {
            val_str_clean.truncate(9);
            if val_str_clean.ends_with('.') {
                val_str_clean.pop();
            }
        }
        x_vals.push_str(&format!("{:<10}", val_str_clean));
    }
    result.push(x_vals);

    result.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_chart() {
        let points = vec![(0.0, 0.0), (10.0, 10.0)];
        let chart = render_chart(&points, 10, 5, "Test Chart");
        assert!(chart.contains("Test Chart"));
    }

    #[test]
    fn test_render_chart_flat_data() {
        let points = vec![(0.0, 5.0), (10.0, 5.0)];
        let chart = render_chart(&points, 10, 5, "Flat Chart");
        assert!(chart.contains("Flat Chart"));
    }

    #[test]
    fn test_render_chart_empty_data() {
        let points = vec![(0.0, 5.0)];
        let chart = render_chart(&points, 10, 5, "Empty Chart");
        assert!(chart.contains("Not enough data points"));
    }

    #[test]
    fn test_render_chart_y_label_case_insensitivity() {
        let points = vec![(0.0, 0.0), (10.0, 10.0)];

        // Title is all-caps, matching must be case-insensitive
        let chart = render_chart(&points, 10, 5, "HOST DISK WRITE HISTORY CHART");
        assert!(chart.contains("Write (MB/s)"));

        let chart_read = render_chart(&points, 10, 5, "HOST DISK READ HISTORY CHART");
        assert!(chart_read.contains("Read (MB/s)"));
    }
}
