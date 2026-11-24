/// Runs `launch` until it stops failing with `ETXTBSY`.
///
/// A child that another test thread forks while a freshly written executable is still
/// open for writing keeps that handle until it calls `exec`; launching the file before
/// then fails with `ETXTBSY`.
pub(crate) fn retry_while_text_busy<T>(
    mut launch: impl FnMut() -> std::io::Result<T>,
) -> std::io::Result<T> {
    for _ in 0..1000 {
        match launch() {
            Err(e) if e.kind() == std::io::ErrorKind::ExecutableFileBusy => {
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
            res => return res,
        }
    }
    launch()
}
