#!/usr/bin/env python3
# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cross-platform hermetic APK builder for PerfProbe.apk (Linux, macOS, Windows).

Packages a compiled Java JAR and AndroidManifest.xml into a signed APK using
tools from @prebuilt_android_sdk (d8.jar, aapt2, apksigner.jar) and Python's
standard zipfile module (zero shell/bash dependency).
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def _find_in_sdk(sdk_files: list[str], basename: str) -> str:
    for path in sdk_files:
        name = os.path.basename(path)
        if name == basename or name == f"{basename}.exe":
            return path
    raise FileNotFoundError(
        f"Required Android SDK tool '{basename}' not found among {len(sdk_files)} SDK inputs."
    )


def build_apk(
    java_bin: str,
    input_jar: str,
    manifest_xml: str,
    android_jar: str,
    d8_jar: str,
    key_pk8: str,
    cert_pem: str,
    output_apk: str,
    sdk_files: list[str],
) -> None:
    aapt2_bin = _find_in_sdk(sdk_files, "aapt2")
    apksigner_jar = _find_in_sdk(sdk_files, "apksigner.jar")

    with tempfile.TemporaryDirectory(prefix="perf_probe_apk_") as tmpdir:
        tmp = Path(tmpdir)
        dex_zip = tmp / "dex_out.zip"
        linked_ap = tmp / "linked.ap_"
        unsigned_apk = tmp / "unsigned.apk"

        # 1. Compile Java bytecode JAR -> classes.dex via d8.jar
        d8_cmd = [
            java_bin,
            "-cp",
            d8_jar,
            "com.android.tools.r8.D8",
            "--min-api",
            "26",
            "--lib",
            android_jar,
            "--output",
            str(dex_zip),
            input_jar,
        ]
        res_d8 = subprocess.run(d8_cmd, capture_output=True, text=True, check=False)
        if res_d8.returncode != 0:
            raise RuntimeError(
                f"D8 dex compilation failed (exit {res_d8.returncode}):\n{res_d8.stderr}"
            )

        # 2. Link AndroidManifest.xml into base APK container via aapt2
        aapt2_cmd = [
            aapt2_bin,
            "link",
            "-o",
            str(linked_ap),
            "--manifest",
            manifest_xml,
            "-I",
            android_jar,
        ]
        res_aapt2 = subprocess.run(aapt2_cmd, capture_output=True, text=True, check=False)
        if res_aapt2.returncode != 0:
            raise RuntimeError(
                f"aapt2 link failed (exit {res_aapt2.returncode}):\n{res_aapt2.stderr}"
            )

        # 3. Merge classes.dex into linked APK archive with deterministic timestamps
        # Android R+ (API 30+) requires resources.arsc to be stored uncompressed
        # (ZIP_STORED) and aligned on a 4-byte boundary.
        with zipfile.ZipFile(linked_ap, "r") as z_in, zipfile.ZipFile(
            dex_zip, "r"
        ) as z_dex, zipfile.ZipFile(
            unsigned_apk, "w", compression=zipfile.ZIP_DEFLATED
        ) as z_out:
            for item in z_in.infolist():
                data = z_in.read(item.filename)
                info = zipfile.ZipInfo(
                    filename=item.filename, date_time=(2026, 1, 1, 0, 0, 0)
                )
                if item.filename == "resources.arsc" or item.compress_type == zipfile.ZIP_STORED:
                    info.compress_type = zipfile.ZIP_STORED
                    assert z_out.fp is not None
                    header_end = (
                        z_out.fp.tell() + 30 + len(info.filename.encode("utf-8"))
                    )
                    pad_len = (4 - (header_end % 4)) % 4
                    info.extra = b"\x00" * pad_len
                else:
                    info.compress_type = zipfile.ZIP_DEFLATED
                z_out.writestr(info, data)
            for item in z_dex.infolist():
                if item.filename.endswith(".dex"):
                    data = z_dex.read(item.filename)
                    info = zipfile.ZipInfo(
                        filename=item.filename, date_time=(2026, 1, 1, 0, 0, 0)
                    )
                    info.compress_type = zipfile.ZIP_DEFLATED
                    z_out.writestr(info, data)

        # 4. Sign APK with apksigner.jar (v1 + v2 signing)
        os.makedirs(os.path.dirname(os.path.abspath(output_apk)), exist_ok=True)
        sign_cmd = [
            java_bin,
            "-jar",
            apksigner_jar,
            "sign",
            "--key",
            key_pk8,
            "--cert",
            cert_pem,
            "--v1-signing-enabled",
            "true",
            "--v2-signing-enabled",
            "true",
            "--v3-signing-enabled",
            "false",
            "--out",
            output_apk,
            str(unsigned_apk),
        ]
        res_sign = subprocess.run(sign_cmd, capture_output=True, text=True, check=False)
        if res_sign.returncode != 0:
            raise RuntimeError(
                f"apksigner failed (exit {res_sign.returncode}):\n{res_sign.stderr}"
            )

        if not os.path.isfile(output_apk):
            shutil.copyfile(unsigned_apk, output_apk)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build signed PerfProbe APK.")
    parser.add_argument("--java", required=True, help="Path to java binary.")
    parser.add_argument("--input-jar", required=True, help="Compiled classes JAR.")
    parser.add_argument("--manifest", required=True, help="AndroidManifest.xml path.")
    parser.add_argument("--android-jar", required=True, help="android.jar path.")
    parser.add_argument("--d8-jar", required=True, help="d8.jar path.")
    parser.add_argument("--key-pk8", required=True, help="PKCS#8 signing key.")
    parser.add_argument("--cert-pem", required=True, help="X.509 certificate PEM.")
    parser.add_argument("--output", required=True, help="Output signed APK path.")
    parser.add_argument("sdk_files", nargs="+", help="SDK files from @prebuilt_android_sdk.")
    args = parser.parse_args()

    build_apk(
        java_bin=args.java,
        input_jar=args.input_jar,
        manifest_xml=args.manifest,
        android_jar=args.android_jar,
        d8_jar=args.d8_jar,
        key_pk8=args.key_pk8,
        cert_pem=args.cert_pem,
        output_apk=args.output,
        sdk_files=args.sdk_files,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
