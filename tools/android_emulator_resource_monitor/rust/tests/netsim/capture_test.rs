use super::*;

#[test]
fn test_parse_pipe_and_whitespace_capture_rows() {
    let pipe = " 4 | Pixel 8 Pro | BLUETOOTH_LOW_ENERGY | ON | 12:00:00 | 15 | 1024 ";
    let row = parse_pipe_capture_row(pipe).unwrap();
    assert_eq!(row.id, 4);
    assert_eq!(row.device_name, "Pixel 8 Pro");
    assert_eq!(row.chip_kind, "BT");
    assert_eq!(row.state, "on");
    assert_eq!(row.records, 15);
    assert_eq!(row.size_bytes, 1024);

    let ws = "7 Pixel 8 Pro WIFI off 12:00:01 0 512";
    let ws_row = parse_whitespace_capture_row(ws).unwrap();
    assert_eq!(ws_row.id, 7);
    assert_eq!(ws_row.device_name, "Pixel 8 Pro");
    assert_eq!(ws_row.chip_kind, "WIFI");
    assert_eq!(ws_row.state, "off");
    assert_eq!(ws_row.size_bytes, 512);
}
