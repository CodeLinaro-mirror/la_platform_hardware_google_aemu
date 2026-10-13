# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI entrypoint and interactive control REPL for Clownfish."""

from __future__ import annotations

import argparse
import cmd
import logging
import signal
import sys

import grpc

from clownfish.companions import (
    resolve_aquarium_static_dir,
    resolve_companion_binary,
)
from clownfish.presets import DEVICE_PRESETS
from clownfish.server import ClownfishServer

_logger = logging.getLogger(__name__)


class ClownfishShell(cmd.Cmd):
    """Interactive control shell for mutating Clownfish state at runtime."""

    intro = "Clownfish interactive shell. Type 'help' or '?' to list commands.\n"
    prompt = "clownfish> "

    def __init__(self, server: ClownfishServer) -> None:
        super().__init__()
        self.server = server

    def do_status(self, arg: str) -> None:
        """Prints current server and device status."""
        del arg
        st = self.server.state
        w, h = st.get_effective_display_dims(0)
        with st._lock:
            dpi = st.displays[0].dpi
            rot = st.displays[0].rotation_deg
            posture = st.posture
            bat = st.battery_charge_level
            seq = st.frame_seq
        print(f"Address:  {self.server.address}")
        print(f"AVD:      {st.preset.avd_name} ({st.preset.name})")
        print(f"Display:  {w}x{h} @{dpi}dpi (rot={rot}deg)")
        print(f"Posture:  {posture}")
        print(f"Battery:  {bat}%")
        print(f"Frames:   {seq}")
        if self.server.videobridge_address:
            print(
                f"WebRTC:   {self.server.videobridge_address} (proxied via RtcService)"
            )
        if self.server.grpc_web_url:
            print(f"gRPC-Web: {self.server.grpc_web_url}")
        if self.server.aquarium_url:
            print(f"Aquarium: {self.server.aquarium_url}")

    def do_rotate(self, arg: str) -> None:
        """Rotates the device by 90 degrees around Z axis."""
        del arg
        with self.server.state._lock:
            px, py, pz = self.server.state.pose_rotation_deg
        next_z = ((round(pz) + 90 + 180) % 360) - 180
        self.server.state.set_pose_rotation_deg(px, py, float(next_z))
        print(f"Rotated device to {next_z} degrees.")

    def do_posture(self, arg: str) -> None:
        """Sets the foldable posture: posture <int> (1=CLOSED, 2=HALF_OPENED, 3=OPENED)."""
        try:
            posture = int(arg.strip())
        except ValueError:
            print("Usage: posture <int> (1=CLOSED, 2=HALF_OPENED, 3=OPENED)")
            return
        self.server.state.set_posture(posture)
        print(f"Set posture to {posture}.")

    def do_battery(self, arg: str) -> None:
        """Sets battery charge level: battery <0-100>."""
        try:
            level = int(arg.strip())
        except ValueError:
            print("Usage: battery <0-100>")
            return
        self.server.state.update_battery(charge_level=max(0, min(100, level)))
        print(f"Set battery level to {level}%.")

    def do_sms(self, arg: str) -> None:
        """Simulates incoming SMS: sms <sender> <message>."""
        parts = arg.strip().split(maxsplit=1)
        if len(parts) < 2:
            print("Usage: sms <sender> <message>")
            return
        with self.server.state._lock:
            self.server.state.sms_messages.append((parts[0], parts[1]))
        print(f"Queued SMS from {parts[0]}.")

    def do_inject_error(self, arg: str) -> None:
        """Injects a gRPC UNAVAILABLE error for RPCs matching substring: inject_error <method>."""
        method = arg.strip()
        if not method:
            print("Usage: inject_error <method_substr>")
            return
        self.server.inject_error(method, grpc.StatusCode.UNAVAILABLE)
        print(f"Injecting UNAVAILABLE for RPCs matching '{method}'.")

    def do_clear_errors(self, arg: str) -> None:
        """Clears all injected gRPC errors."""
        del arg
        self.server.clear_errors()
        print("Cleared all injected errors.")

    def do_quit(self, arg: str) -> bool:
        """Stops the server and exits."""
        del arg
        return True

    def do_exit(self, arg: str) -> bool:
        """Stops the server and exits."""
        return self.do_quit(arg)

    def do_EOF(self, arg: str) -> bool:
        """Exits on Ctrl-D."""
        print()
        return self.do_quit(arg)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clownfish",
        description="Clownfish: Deterministic fake Android Emulator gRPC server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8554,
        help="gRPC listening port (0 for ephemeral port, default: 8554).",
    )
    parser.add_argument(
        "--serial",
        type=str,
        default="5554",
        help="Emulator console serial port string (default: 5554).",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(DEVICE_PRESETS.keys()),
        default="phone",
        help="Device hardware preset profile (default: phone).",
    )
    parser.add_argument(
        "--avd-name",
        type=str,
        default=None,
        help="Override the AVD name advertised in status and discovery.",
    )
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="Disable writing pid_<PID>.ini to the emulator discovery directory.",
    )
    parser.add_argument(
        "--discovery-dir",
        type=str,
        default=None,
        help="Custom directory path for writing pid_<PID>.ini.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=15.0,
        help="Target frame rate for the animated synthetic display (default: 15).",
    )
    parser.add_argument(
        "--webrtc",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable real WebRTC v2 media bridge via C++ videobridge (default: auto).",
    )
    parser.add_argument(
        "--webrtc-max-dim",
        type=int,
        default=720,
        help="Max display dimension for WebRTC shared memory capture (default: 720).",
    )
    parser.add_argument(
        "--videobridge-port",
        type=int,
        default=0,
        help="Port for internal videobridge gRPC server (0 for ephemeral).",
    )
    parser.add_argument(
        "--videobridge-bin",
        type=str,
        default=None,
        help="Explicit path to the C++ videobridge binary.",
    )
    parser.add_argument(
        "--grpc-web",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable standalone grpc-web-proxy server (default: auto).",
    )
    parser.add_argument(
        "--grpc-web-port",
        type=int,
        default=8888,
        help="HTTP listening port for standalone grpc-web-proxy (default: 8888).",
    )
    parser.add_argument(
        "--grpc-web-bin",
        type=str,
        default=None,
        help="Explicit path to the grpc-web-proxy binary.",
    )
    parser.add_argument(
        "--aquarium",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable Aquarium web UI server (default: auto).",
    )
    parser.add_argument(
        "--aquarium-port",
        type=int,
        default=8080,
        help="HTTP listening port for Aquarium UI server (default: 8080).",
    )
    parser.add_argument(
        "--aquarium-bin",
        type=str,
        default=None,
        help="Explicit path to the aquarium-ui binary.",
    )
    parser.add_argument(
        "--aquarium-static-dir",
        type=str,
        default=None,
        help="Explicit path to compiled Aquarium UI dist/ directory.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch an interactive control prompt to mutate state at runtime.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args(argv)

    enable_webrtc = (
        args.webrtc
        if args.webrtc is not None
        else resolve_companion_binary("videobridge", args.videobridge_bin) is not None
    )
    enable_grpc_web = (
        args.grpc_web
        if args.grpc_web is not None
        else resolve_companion_binary("grpc-web-proxy", args.grpc_web_bin) is not None
    )
    enable_aquarium = (
        args.aquarium
        if args.aquarium is not None
        else (
            resolve_companion_binary("aquarium-ui", args.aquarium_bin) is not None
            and resolve_aquarium_static_dir(args.aquarium_static_dir) is not None
        )
    )

    server = ClownfishServer(
        preset=args.preset,
        port=args.port,
        serial=args.serial,
        avd_name=args.avd_name,
        enable_discovery=not args.no_discovery,
        discovery_dir=args.discovery_dir,
        fps=args.fps,
        enable_webrtc=enable_webrtc,
        videobridge_port=args.videobridge_port,
        webrtc_max_dim=args.webrtc_max_dim,
        videobridge_bin=args.videobridge_bin,
        enable_grpc_web=enable_grpc_web,
        grpc_web_port=args.grpc_web_port,
        grpc_web_bin=args.grpc_web_bin,
        enable_aquarium=enable_aquarium,
        aquarium_port=args.aquarium_port,
        aquarium_bin=args.aquarium_bin,
        aquarium_static_dir=args.aquarium_static_dir,
    )
    server.start()
    _logger.info(
        "Clownfish (%s) listening on %s (AVD: %s)",
        args.preset,
        server.address,
        server.state.preset.avd_name,
    )
    if server.videobridge_address:
        _logger.info(
            "WebRTC v2 RtcService active via videobridge (%s)",
            server.videobridge_address,
        )
    if server.grpc_web_url:
        _logger.info("gRPC-Web proxy listening on %s", server.grpc_web_url)
    if server.aquarium_url:
        _logger.info("Aquarium Web UI available at %s", server.aquarium_url)

    def _sig_handler(signum, frame):
        del signum, frame
        _logger.info("Shutting down Clownfish...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        if args.interactive:
            ClownfishShell(server).cmdloop()
        else:
            server.wait_for_termination()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
