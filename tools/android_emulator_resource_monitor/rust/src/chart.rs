const DOTS_PER_CELL_X: usize = 2;
const DOTS_PER_CELL_Y: usize = 4;
const BRAILLE_UNICODE_BASE: u32 = 0x2800;

/// Unicode Braille dot bitmasks indexed by `[dot_row (0..4)][dot_col (0..2)]`.
const BRAILLE_DOT_BITS: [[u8; DOTS_PER_CELL_X]; DOTS_PER_CELL_Y] =
    [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]];

const MIN_POINTS_FOR_CHART: usize = 2;
const FLAT_ZERO_Y_HALF_SPAN: f32 = 0.5;
const FLAT_NONZERO_Y_MARGIN_RATIO: f32 = 0.1;
const X_AXIS_SEGMENT_WIDTH: usize = 10;
const MAX_X_TICK_LABEL_LEN: usize = 9;
const X_TICK_ZERO_EPSILON: f32 = 0.001;
const X_AXIS_LABEL: &str = "Time (s)";

const USAGE_PCT_KEYWORDS: &[&str] = &["cpu", "ram", "gpu", "swap", "fullness"];

/// A 2D monochrome canvas that renders `2x4` sub-cell dot grids as Unicode Braille characters.
struct BrailleCanvas {
    width: usize,
    height: usize,
    cells: Vec<u8>,
}

impl BrailleCanvas {
    fn new(width: usize, height: usize) -> Self {
        Self { width, height, cells: vec![0; width * height] }
    }

    fn dot_width(&self) -> usize {
        self.width * DOTS_PER_CELL_X
    }

    fn dot_height(&self) -> usize {
        self.height * DOTS_PER_CELL_Y
    }

    fn set(&mut self, x: usize, y: usize) {
        if x < self.dot_width() && y < self.dot_height() {
            let cell_x = x / DOTS_PER_CELL_X;
            let cell_y = y / DOTS_PER_CELL_Y;
            let dot_col = x % DOTS_PER_CELL_X;
            let dot_row = y % DOTS_PER_CELL_Y;
            self.cells[cell_y * self.width + cell_x] |= BRAILLE_DOT_BITS[dot_row][dot_col];
        }
    }

    /// Draws a straight line between `(x0, y0)` and `(x1, y1)` in dot coordinates using Bresenham's algorithm.
    fn line(&mut self, x0: i32, y0: i32, x1: i32, y1: i32) {
        let dx = (x1 - x0).abs();
        let dy = -(y1 - y0).abs();
        let sx = if x0 < x1 { 1 } else { -1 };
        let sy = if y0 < y1 { 1 } else { -1 };
        let mut err = dx + dy;

        let mut x = x0;
        let mut y = y0;

        loop {
            if x >= 0 && y >= 0 {
                self.set(x as usize, y as usize);
            }
            if x == x1 && y == y1 {
                break;
            }
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

    fn row_string(&self, cell_y: usize) -> String {
        let row_start = cell_y * self.width;
        let row_end = row_start + self.width;
        self.cells[row_start..row_end]
            .iter()
            .map(|&mask| char::from_u32(BRAILLE_UNICODE_BASE + u32::from(mask)).unwrap_or(' '))
            .collect()
    }
}

#[derive(Debug, Clone, Copy)]
struct ChartBounds {
    min_x: f32,
    max_x: f32,
    min_y: f32,
    max_y: f32,
}

impl ChartBounds {
    fn from_points(points: &[(f32, f32)]) -> Self {
        let mut min_x = f32::INFINITY;
        let mut max_x = f32::NEG_INFINITY;
        let mut raw_min_y = f32::INFINITY;
        let mut raw_max_y = f32::NEG_INFINITY;
        for &(x, y) in points.iter().filter(|&&(x, y)| x.is_finite() && y.is_finite()) {
            min_x = min_x.min(x);
            max_x = max_x.max(x);
            raw_min_y = raw_min_y.min(y);
            raw_max_y = raw_max_y.max(y);
        }

        let (min_y, max_y) = if raw_min_y == raw_max_y {
            if raw_min_y == 0.0 {
                (-FLAT_ZERO_Y_HALF_SPAN, FLAT_ZERO_Y_HALF_SPAN)
            } else {
                let margin = raw_min_y.abs() * FLAT_NONZERO_Y_MARGIN_RATIO;
                (raw_min_y - margin, raw_max_y + margin)
            }
        } else {
            (raw_min_y, raw_max_y)
        };

        Self { min_x, max_x, min_y, max_y }
    }

    fn span_x(&self) -> f32 {
        self.max_x - self.min_x
    }

    fn span_y(&self) -> f32 {
        self.max_y - self.min_y
    }
}

fn y_axis_label(title: &str) -> &'static str {
    let base_title = title.split(" (").next().unwrap_or(title);
    let lower_title = base_title.to_lowercase();
    if lower_title.contains("gpu memory") {
        "Usage/MB"
    } else if lower_title.contains("netsim ram")
        || lower_title.contains("qemu ram")
        || lower_title.contains("guest swap")
    {
        "MB"
    } else if USAGE_PCT_KEYWORDS.iter().any(|k| lower_title.contains(k)) {
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
    }
}

/// Buckets `(x, y)` samples into `dot_width` horizontal columns and averages `y` per non-empty bucket.
fn aggregate_points(
    points: &[(f32, f32)],
    bounds: ChartBounds,
    dot_width: usize,
) -> Vec<(f32, f32)> {
    let finite_points = points.iter().copied().filter(|&(x, y)| x.is_finite() && y.is_finite());
    let span_x = bounds.span_x();
    if span_x <= 0.0 || dot_width < 2 {
        return finite_points.collect();
    }

    let max_bin = dot_width - 1;
    let bin_scale = max_bin as f32 / span_x;
    let mut bins = vec![(0.0_f32, 0_usize); dot_width];

    for (x, y) in finite_points {
        let bin_idx = (((x - bounds.min_x) * bin_scale) as usize).min(max_bin);
        bins[bin_idx].0 += y;
        bins[bin_idx].1 += 1;
    }

    bins.into_iter()
        .enumerate()
        .filter_map(|(i, (sum_y, count))| {
            if count == 0 {
                None
            } else {
                let avg_y = sum_y / count as f32;
                let x_val = bounds.min_x + (i as f32 / max_bin as f32) * span_x;
                Some((x_val, avg_y))
            }
        })
        .collect()
}

fn format_x_axis_border(segments: usize) -> String {
    let mut border = "----------------|-|".to_string();
    for _ in 0..segments {
        border.push_str(&"-".repeat(X_AXIS_SEGMENT_WIDTH - 1));
        border.push('|');
    }
    border.push_str("-> (");
    border.push_str(X_AXIS_LABEL);
    border.push(')');
    border
}

fn format_x_tick_value(raw_val: f32) -> String {
    let val = if raw_val.abs() < X_TICK_ZERO_EPSILON { 0.0 } else { raw_val };
    let formatted = format!("{val:.7}");
    let mut clean = formatted.trim_end_matches('0').trim_end_matches('.');
    if clean.is_empty() {
        clean = "0";
    }
    if clean.len() > MAX_X_TICK_LABEL_LEN {
        clean = &clean[..MAX_X_TICK_LABEL_LEN];
        clean = clean.trim_end_matches('.');
    }
    format!("{clean:<X_AXIS_SEGMENT_WIDTH$}")
}

fn format_x_axis_labels(bounds: ChartBounds, segments: usize) -> String {
    let mut labels = "                | ".to_string();
    let x_step = if segments > 0 { bounds.span_x() / segments as f32 } else { 0.0 };
    for i in 0..=segments {
        let val = bounds.min_x + i as f32 * x_step;
        labels.push_str(&format_x_tick_value(val));
    }
    labels
}

pub fn render_chart(points: &[(f32, f32)], width: usize, height: usize, title: &str) -> String {
    let finite_count = points.iter().filter(|&&(x, y)| x.is_finite() && y.is_finite()).count();
    if finite_count < MIN_POINTS_FOR_CHART || width == 0 || height == 0 {
        return format!("\n{title}:\nNot enough data points for chart.");
    }

    let bounds = ChartBounds::from_points(points);
    let mut canvas = BrailleCanvas::new(width, height);
    let w_dots = canvas.dot_width();
    let h_dots = canvas.dot_height();

    let aggregated_points = aggregate_points(points, bounds, w_dots);
    if aggregated_points.len() < MIN_POINTS_FOR_CHART {
        return format!("\n{title}:\nNot enough aggregated data points for chart.");
    }

    let x_scale = if bounds.span_x() > 0.0 { (w_dots - 1) as f32 / bounds.span_x() } else { 1.0 };
    let y_scale = if bounds.span_y() > 0.0 { (h_dots - 1) as f32 / bounds.span_y() } else { 1.0 };

    for window in aggregated_points.windows(2) {
        let (p0, p1) = (window[0], window[1]);
        let x0 = ((p0.0 - bounds.min_x) * x_scale) as i32;
        let y0 = (h_dots as i32 - 1) - ((p0.1 - bounds.min_y) * y_scale) as i32;
        let x1 = ((p1.0 - bounds.min_x) * x_scale) as i32;
        let y1 = (h_dots as i32 - 1) - ((p1.1 - bounds.min_y) * y_scale) as i32;
        canvas.line(x0, y0, x1, y1);
    }

    let mut result = Vec::with_capacity(height + 4);
    result.push(format!("{title}:"));
    result.push(format!("({}) ^", y_axis_label(title)));

    let y_step = bounds.span_y() / height as f32;
    for cy in 0..height {
        let y_val = bounds.max_y - (cy as f32 * y_step);
        let row_str = canvas.row_string(cy);
        result.push(format!("{y_val:>15.7} | ⡇{row_str}"));
    }

    let segments = width / X_AXIS_SEGMENT_WIDTH;
    result.push(format_x_axis_border(segments));
    result.push(format_x_axis_labels(bounds, segments));

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

    #[test]
    fn test_braille_canvas_dot_bitmask() {
        let mut canvas = BrailleCanvas::new(1, 1);
        assert_eq!(canvas.row_string(0), "\u{2800}");

        for y in 0..DOTS_PER_CELL_Y {
            for x in 0..DOTS_PER_CELL_X {
                canvas.set(x, y);
            }
        }
        assert_eq!(canvas.row_string(0), "\u{28ff}");
    }

    #[test]
    fn test_chart_bounds_flat_zero_and_nonzero() {
        let zero_bounds = ChartBounds::from_points(&[(1.0, 0.0), (2.0, 0.0)]);
        assert_eq!(zero_bounds.min_y, -0.5);
        assert_eq!(zero_bounds.max_y, 0.5);

        let nonzero_bounds = ChartBounds::from_points(&[(1.0, 10.0), (2.0, 10.0)]);
        assert_eq!(nonzero_bounds.min_y, 9.0);
        assert_eq!(nonzero_bounds.max_y, 11.0);
    }

    #[test]
    fn test_y_axis_label_categories() {
        assert_eq!(y_axis_label("HOST CPU HISTORY CHART"), "Usage (%)");
        assert_eq!(y_axis_label("HOST THERMAL HISTORY CHART"), "Temp (°C)");
        assert_eq!(y_axis_label("GUEST LOGCAT ERRORS"), "Errors");
        assert_eq!(y_axis_label("QEMU CONTEXT SWITCHES"), "Switches");
        assert_eq!(y_axis_label("UNKNOWN METRIC"), "");
    }

    #[test]
    fn test_aggregate_points_buckets_samples() {
        let points = vec![(0.0, 10.0), (0.0, 20.0), (10.0, 30.0)];
        let bounds = ChartBounds::from_points(&points);
        let aggregated = aggregate_points(&points, bounds, 11);
        assert_eq!(aggregated, vec![(0.0, 15.0), (10.0, 30.0)]);
    }

    #[test]
    fn test_format_x_tick_value_trims_and_pads() {
        assert_eq!(format_x_tick_value(0.0002), "0         ");
        assert_eq!(format_x_tick_value(12.5), "12.5      ");
        assert_eq!(format_x_tick_value(123_456.79), "123456.78 ");
    }

    #[test]
    fn test_render_chart_and_aggregate_points_ignore_non_finite_samples() {
        let points = vec![(0.0, f32::NAN), (0.0, 20.0), (10.0, 30.0)];
        let bounds = ChartBounds::from_points(&points);
        let aggregated = aggregate_points(&points, bounds, 11);
        assert_eq!(aggregated, vec![(0.0, 20.0), (10.0, 30.0)]);

        let all_nan = vec![(0.0, f32::NAN), (1.0, f32::NAN)];
        let out = render_chart(&all_nan, 10, 5, "NaN Chart");
        assert!(out.contains("Not enough data points"));
    }

    #[test]
    fn test_y_axis_label_mb_charts_and_zero_dimensions() {
        assert_eq!(y_axis_label("HOST GPU MEMORY HISTORY CHART"), "Usage/MB");
        assert_eq!(y_axis_label("NETSIM RAM HISTORY CHART"), "MB");
        assert_eq!(y_axis_label("GUEST SWAP MEMORY HISTORY (emulator-5554)"), "MB");
        assert_eq!(y_axis_label("HOST QEMU RAM HISTORY (emulator-5554)"), "MB");
        assert_eq!(y_axis_label("HOST RAM HISTORY CHART"), "Usage (%)");
        assert_eq!(y_axis_label("HOST SWAP HISTORY CHART"), "Usage (%)");

        let points = vec![(0.0, 1.0), (1.0, 2.0)];
        assert!(render_chart(&points, 0, 5, "Zero Width").contains("Not enough"));
        assert!(render_chart(&points, 10, 0, "Zero Height").contains("Not enough"));
    }

    #[test]
    fn test_y_axis_label_ignores_parenthesized_avd_name_keywords() {
        assert_eq!(y_axis_label("GUEST LOGCAT ERRORS (Pixel_8_Low_RAM_emulator-5554)"), "Errors");
        assert_eq!(y_axis_label("QEMU CONTEXT SWITCHES (GPU_Bench_emulator-5554)"), "Switches");
        assert_eq!(y_axis_label("GUEST CPU HISTORY (qemu ram avd_emulator-5554)"), "Usage (%)");
    }
}
