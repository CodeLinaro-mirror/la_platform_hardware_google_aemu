import os
import re
import sys
import glob
import platform
import subprocess
import shutil
import time
from lib.output import print_result

NON_EXEC_EXTENSIONS = {
    ".so", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".xml", ".json",
    ".img", ".dat", ".pak", ".pyc", ".py", ".md", ".ini", ".properties",
    ".pem", ".crt", ".key", ".cer", ".icns", ".ico", ".svg"
}


def ensure_executable_permissions(emu_dir, emu_bin):
    if platform.system().lower() == "windows":
        return

    for root, _, files in os.walk(emu_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in NON_EXEC_EXTENSIONS:
                continue
            full_p = os.path.join(root, f)
            if not os.access(full_p, os.X_OK):
                try:
                    os.chmod(full_p, 0o755)
                except Exception:
                    pass


def resolve_emulator_executable(emulator_dir_arg):
    if not emulator_dir_arg:
        raise ValueError(
            "Emulator directory not specified. Please provide --emulator-dir=<path> "
            "(e.g. --emulator-dir=/tmp/emulator-linux-x64-15942201/extracted/emulator)."
        )

    is_windows = platform.system().lower() == "windows"
    exe_name = "emulator.exe" if is_windows else "emulator"

    abs_dir = os.path.abspath(os.path.expanduser(emulator_dir_arg))
    if not os.path.isdir(abs_dir):
        raise FileNotFoundError(f"Specified emulator directory does not exist: {abs_dir}")

    direct_bin = os.path.join(abs_dir, exe_name)
    if os.path.exists(direct_bin):
        ensure_executable_permissions(abs_dir, direct_bin)
        return direct_bin, abs_dir

    sub_bin = os.path.join(abs_dir, "emulator", exe_name)
    if os.path.exists(sub_bin):
        sub_dir = os.path.join(abs_dir, "emulator")
        ensure_executable_permissions(sub_dir, sub_bin)
        return sub_bin, sub_dir

    raise FileNotFoundError(f"Could not locate '{exe_name}' binary inside directory: {abs_dir}")


def prepare_environment(emu_dir):
    env = os.environ.copy()
    system = platform.system().lower()

    if system == "linux":
        lib64 = os.path.join(emu_dir, "lib64")
        qt_lib = os.path.join(lib64, "qt", "lib")
        gles_lib = os.path.join(lib64, "gles_swiftshader")
        vulkan_lib = os.path.join(lib64, "vulkan")
        existing_ld = env.get("LD_LIBRARY_PATH", "")

        ld_paths = [p for p in [lib64, qt_lib, gles_lib, vulkan_lib] if os.path.exists(p)]
        if existing_ld:
            ld_paths.append(existing_ld)
        env["LD_LIBRARY_PATH"] = ":".join(ld_paths)

        qt_plugins = os.path.join(lib64, "qt", "plugins")
        if os.path.exists(qt_plugins):
            env["QT_PLUGIN_PATH"] = qt_plugins

    elif system == "darwin":
        lib64 = os.path.join(emu_dir, "lib64")
        existing_dyld = env.get("DYLD_LIBRARY_PATH", "")
        if os.path.exists(lib64):
            env["DYLD_LIBRARY_PATH"] = f"{lib64}:{existing_dyld}" if existing_dyld else lib64

    return env


def is_headless_launch(emu_args):
    headless_flags = {"-no-window", "-no-gui", "-headless"}
    return any(flag in emu_args for flag in headless_flags)


def get_display_owner_label(disp_num):
    sock_path = f"/tmp/.X11-unix/X{disp_num}"
    try:
        res = subprocess.run(["fuser", sock_path], capture_output=True, text=True, check=False)
        pids = res.stdout.strip().split()
        if pids:
            pid = pids[0]
            comm_file = f"/proc/{pid}/comm"
            if os.path.exists(comm_file):
                with open(comm_file, "r", encoding="utf-8") as f:
                    proc_name = f.read().strip()
                if "Xorg" in proc_name:
                    return f"{proc_name} (Chrome Remote Desktop / Desktop Session)"
                elif "nxnode" in proc_name or "nx" in proc_name.lower():
                    return f"{proc_name} (NoMachine)"
                elif "vnc" in proc_name.lower():
                    return f"{proc_name} (VNC)"
                return f"{proc_name} (PID {pid})"
    except Exception:
        pass
    return "Active X11 Display Server"


def check_display_health(env, emu_args, json_mode=False):
    """
    Check beforehand if DISPLAY points to an active socket (unless -no-window is given).
    If a display issue is detected, print a high-signal warning with owner labels,
    but always proceed with launching the emulator.
    """
    if platform.system().lower() != "linux":
        return

    if is_headless_launch(emu_args):
        return

    current_disp = env.get("DISPLAY", "")
    disp_num = None
    if current_disp:
        m = re.match(r"^:?(\d+)(?:\.\d+)?$", current_disp.strip())
        if m:
            disp_num = m.group(1)

    socket_path = f"/tmp/.X11-unix/X{disp_num}" if disp_num is not None else None
    if socket_path and os.path.exists(socket_path):
        return  # Active display socket exists, good to go!

    # Active socket not found for current DISPLAY. Scan available /tmp/.X11-unix/X* sockets.
    active_displays = []
    if os.path.isdir("/tmp/.X11-unix"):
        try:
            for fname in os.listdir("/tmp/.X11-unix"):
                if fname.startswith("X") and fname[1:].isdigit():
                    num = int(fname[1:])
                    label = get_display_owner_label(num)
                    active_displays.append((num, f":{num}", label))
        except Exception:
            pass

    active_displays.sort(key=lambda item: item[0])
    disp_str = current_disp if current_disp else "(not set)"

    if not json_mode:
        print("⚠️  DISPLAY WARNING:")
        print(f"   Current DISPLAY='{disp_str}' has no active socket file at /tmp/.X11-unix/X{disp_num or 0}.")
        if active_displays:
            print("   Detected active X11 display socket(s) on machine:")
            for _, d_str, owner_desc in active_displays:
                print(f"     • DISPLAY={d_str:<6} [{owner_desc}]")
            suggested_disp = active_displays[0][1]
            for num, d_str, owner_desc in active_displays:
                if "chrome" in owner_desc.lower() or "xorg" in owner_desc.lower() or num == 20:
                    suggested_disp = d_str
                    break
            print(f"   If Qt XCB fails to connect to display, try running: export DISPLAY={suggested_disp}")
        else:
            print("   No active X11 display sockets found in /tmp/.X11-unix/.")
            print("   If launch fails with Qt XCB error, ensure your display server is running or pass '-no-window'.")
        print("   Proceeding to launch emulator...")
        print("-" * 50)
        sys.stdout.flush()


def register_parser(subparsers):
    launch_parser = subparsers.add_parser(
        "launch",
        help="Launch developer tools (emulator, cts-verifier, etc.)"
    )
    launch_parser.set_defaults(func=lambda args: launch_parser.print_help() or sys.exit(0))
    launch_subparsers = launch_parser.add_subparsers(dest="launch_cmd", help="Resource type to launch")

    # emu-dev-cli launch emulator ...
    emulator_parser = launch_subparsers.add_parser(
        "emulator",
        help="Launch an Android Emulator using prebuilt emulator binaries and forwarding arguments"
    )
    emulator_parser.add_argument(
        "--emulator-dir",
        type=str,
        default=None,
        help="Directory containing extracted prebuilt emulator (required)"
    )
    emulator_parser.add_argument(
        "--detached",
        action="store_true",
        help="Launch emulator as an independent daemon background process and exit immediately"
    )
    emulator_parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Custom log file path when running with --detached (defaults to /tmp/emulator_<pid>.log)"
    )
    emulator_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved executable, library paths, and command without executing"
    )
    emulator_parser.add_argument(
        "emulator_args",
        nargs="*",
        help="Arguments passed directly to emulator (use '--' before options, e.g. -- -avd my-avd)"
    )
    emulator_parser.set_defaults(parser=emulator_parser, func=run_launch_emulator)


def run_launch_emulator(args):
    json_mode = getattr(args, "json", False)
    emu_args = getattr(args, "emulator_args", []) or []

    if not args.emulator_dir:
        if not json_mode and hasattr(args, "parser"):
            args.parser.print_help()
            sys.exit(0)
        print_result({
            "status": "error",
            "action": "launch emulator",
            "error_message": "--emulator-dir is required when launching emulator.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    if emu_args and emu_args[0] == "--":
        emu_args = emu_args[1:]

    try:
        emu_bin, emu_dir = resolve_emulator_executable(args.emulator_dir)
    except (ValueError, FileNotFoundError) as e:
        print_result({
            "status": "error",
            "action": "launch emulator",
            "error_message": str(e),
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    env = prepare_environment(emu_dir)
    check_display_health(env, emu_args, json_mode=json_mode)

    full_cmd = [emu_bin] + emu_args

    if args.dry_run:
        print_result({
            "status": "dry_run",
            "action": "launch emulator",
            "emulator_executable": emu_bin,
            "emulator_dir": emu_dir,
            "detached_mode": args.detached,
            "display": env.get("DISPLAY", ""),
            "ld_library_path": env.get("LD_LIBRARY_PATH", ""),
            "full_command": full_cmd,
        }, json_mode=json_mode)
        return

    # Detached daemon launch mode
    if args.detached:
        log_file_path = args.log_file
        if not log_file_path:
            ts = int(time.time())
            log_file_path = f"/tmp/emulator_{ts}.log"
        else:
            log_file_path = os.path.abspath(os.path.expanduser(log_file_path))

        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        log_fd = open(log_file_path, "a", encoding="utf-8")

        system = platform.system().lower()
        popen_kwargs = {
            "env": env,
            "cwd": emu_dir,
            "stdout": log_fd,
            "stderr": log_fd,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if system == "windows":
            popen_kwargs["creationflags"] = 0x00000008
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(full_cmd, **popen_kwargs)

        print_result({
            "status": "success",
            "action": "launch emulator (detached)",
            "summary": f"Started emulator daemon (PID {proc.pid}) on DISPLAY={env.get('DISPLAY')}",
            "pid": proc.pid,
            "display": env.get("DISPLAY", ""),
            "log_file": log_file_path,
            "emulator_executable": emu_bin,
            "emulator_dir": emu_dir,
            "full_command": full_cmd,
        }, json_mode=json_mode)
        return

    # Foreground interactive launch mode
    if not json_mode:
        print(f"🚀 Launching Emulator: {emu_bin}")
        print(f"   Using prebuilt directory: {emu_dir}")
        print(f"   Display: DISPLAY={env.get('DISPLAY', '(none)')}")
        if emu_args:
            print(f"   Arguments: {' '.join(emu_args)}")
        print("-" * 50)
        sys.stdout.flush()

    try:
        res = subprocess.run(full_cmd, env=env, cwd=emu_dir, check=False)
        sys.exit(res.returncode)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as ex:
        print_result({
            "status": "error",
            "action": "launch emulator",
            "error_message": f"Failed to execute emulator: {ex}",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)
