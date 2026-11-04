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

"""Unit tests for Phase 15 Parameter Objects (`CrashQuery`, `CrashMetadata`, `ReviewBatchConfig`, `RefactoringConfig`)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest

from commands.crash.list_crashes import CrashQuery, build_f1_crashes_query
from commands.crash.utils import CrashMetadata
from commands.gerrit.services.vote_submit_service import (
    OWNER_NOTIFY_HANDLING,
    ReviewBatchConfig,
)
from lib.tidy_engine import RefactoringConfig
from testlib.gerrit_fakes import make_fake_change


class ParameterObjectsTest(unittest.TestCase):
    """Tests construction, immutability, and Unicode handling of Phase 15 Parameter Objects."""

    def test_crash_query_from_namespace_and_sql_builder(self) -> None:
        ns = argparse.Namespace(
            days=14,
            limit=50,
            version="36.1.0-1234567",
            include="gfxstream|测试_🚀",
            exclude="Qt6",
            unassigned_only=True,
            raw=False,
            product="AndroidEmulator",
            accounting_group="android-devtools-emulator",
            print_sql=False,
            json=True,
        )
        query = CrashQuery.from_namespace(ns, json_mode=True)
        self.assertEqual(query.hours, 14 * 24)
        self.assertEqual(query.limit, 50)
        self.assertEqual(query.version_regex, "36.1.0-1234567")
        self.assertEqual(query.include_modules, "gfxstream|测试_🚀")
        self.assertEqual(query.exclude_modules, "Qt6")
        self.assertTrue(query.unassigned_only)
        self.assertEqual(query.accounting_group, "android-devtools-emulator")
        self.assertTrue(query.json_mode)

        sql = build_f1_crashes_query(query)
        self.assertIn("336", sql)
        self.assertIn("LIMIT 50", sql)
        self.assertIn("gfxstream|测试_🚀", sql)

    def test_crash_metadata_from_work_dir_and_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "täst_中文_🚀"
            work_dir.mkdir(parents=True, exist_ok=True)
            meta_payload = {
                "report_proto": {
                    "stableSignature": "android::emulation::crash_测试_🚀",
                    "product": {
                        "Version": "36.2.1-99887766",
                        "version": "36.2.1-99887766",
                    },
                    "operatingSystem": {"name": "Linux"},
                    "buildIds": ["99887766"],
                    "crashLocation": "host-common/crash_测试.cpp:42",
                    "customKeys": [{"key": "gpu", "value": "vulkan_🚀"}],
                }
            }
            (work_dir / "metadata.json").write_text(
                json.dumps(meta_payload), encoding="utf-8"
            )
            (work_dir / "crashreport.txt").write_text(
                "Thread 0 (crashed)\n 0  libemulator.so!android::emulation::crash_fn() [crash.cpp : 42 + 0x4]\n",
                encoding="utf-8",
            )

            meta = CrashMetadata.from_work_dir(
                "https://crash.corp.google.com/05d8356e2f800000",
                work_dir,
            )
            self.assertEqual(meta.crash_id, "05d8356e2f800000")
            self.assertEqual(
                meta.stable_signature, "android::emulation::crash_测试_🚀"
            )
            self.assertEqual(meta.build_id, "99887766")
            self.assertEqual(meta.os_names, ("Linux",))
            self.assertEqual(meta.versions, ("36.2.1-99887766",))
            self.assertEqual(meta.build_ids, ("99887766",))
            self.assertEqual(meta.crash_location, "host-common/crash_测试.cpp:42")
            self.assertEqual(meta.custom_keys, {"gpu": "vulkan_🚀"})

            sandbox_dict = meta.to_sandbox_info_dict()
            self.assertEqual(sandbox_dict["os"], ["Linux"])
            self.assertEqual(sandbox_dict["build_ids"], ["99887766"])

            refreshed = meta.refresh(signature_override="override_sig_🚀")
            self.assertEqual(refreshed.stable_signature, "override_sig_🚀")

    def test_review_batch_config_normalizes_and_deduplicates_cls(self) -> None:
        batch = ReviewBatchConfig.from_cls(
            ["ag/43399904", 43399904, "https://android-review.googlesource.com/c/platform/hardware/google/aemu/+/43399905"],
            code_review=1,
            autosubmit=True,
            presubmit_ready=True,
            message="  LGTM 测试_🚀_é  ",
            action="ADD",
            account="  jansene@google.com ",
            state="cc",
        )
        self.assertEqual(batch.cl_numbers, ("43399904", "43399905"))
        self.assertEqual(batch.code_review, 1)
        self.assertTrue(batch.autosubmit)
        self.assertTrue(batch.presubmit_ready)
        self.assertEqual(batch.message, "LGTM 测试_🚀_é")
        self.assertEqual(batch.action, "add")
        self.assertEqual(batch.account, "jansene@google.com")
        self.assertEqual(batch.state, "CC")
        self.assertEqual(batch.notify, OWNER_NOTIFY_HANDLING)

    def test_refactoring_config_from_namespace_and_updates(self) -> None:
        ns = argparse.Namespace(
            target="//emulator/libs/file:file_lib",
            level=2,
            model="pro",
            test="//emulator/libs/file:file_test",
            checks="readability-identifier-naming",
            force=True,
            dry_run=False,
            continue_tx=False,
            bug="579918998",
            upload=True,
            json=True,
        )
        cfg = RefactoringConfig.from_namespace(
            ns,
            report_target="//emulator/libs/file:file_lib_tidy",
            compilation_targets=["//emulator/libs/file:file_lib"],
        )
        self.assertEqual(cfg.target, "//emulator/libs/file:file_lib")
        self.assertEqual(cfg.report_target, "//emulator/libs/file:file_lib_tidy")
        self.assertEqual(cfg.compilation_targets, ("//emulator/libs/file:file_lib",))
        self.assertEqual(cfg.level, 2)
        self.assertEqual(cfg.model, "pro")
        self.assertEqual(cfg.test_target, "//emulator/libs/file:file_test")
        self.assertEqual(cfg.bug_id, "579918998")
        self.assertTrue(cfg.force)
        self.assertTrue(cfg.upload)
        self.assertTrue(cfg.json_mode)

        updated = cfg.with_updates(level=3, compilation_targets=["//a:b", "//c:d"])
        self.assertEqual(updated.level, 3)
        self.assertEqual(updated.compilation_targets, ("//a:b", "//c:d"))

    def test_make_fake_change_helper(self) -> None:
        fake = make_fake_change(43399904, subject="refactor: Phase 15 测试_🚀")
        self.assertEqual(fake["_number"], 43399904)
        self.assertEqual(fake["subject"], "refactor: Phase 15 测试_🚀")
        self.assertIn("Code-Review", fake["labels"])


if __name__ == "__main__":
    unittest.main()
