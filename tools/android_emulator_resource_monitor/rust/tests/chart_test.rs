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
