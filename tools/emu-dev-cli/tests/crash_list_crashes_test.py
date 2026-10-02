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

"""Unit tests for commands.crash.list_crashes and commands.crash.link_bug modules."""

import argparse
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.link_bug import (
    is_crash_report_id,
    normalize_bug_url_and_id,
    parse_search_annotations_output,
    resolve_signature_from_report_id,
    run_link_bug,
)
from commands.crash.list_crashes import (
    build_f1_crashes_query,
    extract_build_ids_from_versions,
    merge_and_normalize_rows,
    parse_f1_csv_output,
    run_list_crashes,
)


class CrashListAndLinkTest(unittest.TestCase):
    """Tests for `crash list-crashes` and `crash link-bug`."""

    def test_extract_build_ids_from_versions(self):
        versions = ["37.1.3-16373346", "37.1.2-16173978", "37.1.3-16373346"]
        self.assertEqual(
            extract_build_ids_from_versions(versions),
            ["16373346", "16173978"],
        )

    def test_build_f1_crashes_query_defaults_and_unassigned(self):
        sql = build_f1_crashes_query(hours=48, unassigned_only=True)
        self.assertIn("FROM crash.prod.latest AS c", sql)
        self.assertIn("LEFT JOIN crash_annotations.prod.latest AS a", sql)
        self.assertIn("Qt6|libandroid-emu|adb|netsimd", sql)
        self.assertIn(r"0\.0\.1-|37", sql)
        self.assertIn("(a.BugInfo IS NULL OR ARRAY_LENGTH(a.BugInfo) = 0)", sql)
        self.assertIn("GROUP BY c.stable_signature", sql)

    def test_build_f1_crashes_query_rejects_single_quotes(self):
        with self.assertRaises(ValueError):
            build_f1_crashes_query(version_regex="0.0.1' OR 1=1")

    def test_parse_f1_csv_output_strips_banner(self):
        raw_out = (
            "Read [f1 skill](google3/learning/gemini/agents/skills/f1/SKILL.md) for usage.\n\n"
            "HINT: No accounting group is configured...\n\n"
            "stable_signature,occurrences,latest_report_id,versions,os_names,crash_location,internal_msg,looper_registrations,list_annotations,bug_urls,bug_ids\n"
            '"virtio_gpu_rutabaga_load-e709a6ba",4,"7ebbb53ff0400000","37.1.2-16173978","Mac OS X","virtio_gpu_rutabaga_load (rutabaga.c:42)",NULL,"1=GlobalLoop;3=QemuMainLoop;",NULL,"https://b.corp.google.com/issues/567125517","567125517"\n'
            '"PthreadWaiter::Wait-a030f826",2,"9dd7b05170400000","37.1.3-16488488","Mac OS X","Wait (waiter.cc:10)","hang","1=GlobalLoop;",NULL,"",""\n'
        )
        rows = parse_f1_csv_output(raw_out)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[0]["stable_signature"], "virtio_gpu_rutabaga_load-e709a6ba"
        )
        self.assertEqual(rows[0]["internal_msg"], "")
        self.assertEqual(rows[1]["stable_signature"], "PthreadWaiter::Wait-a030f826")
        self.assertEqual(rows[1]["internal_msg"], "hang")

    def test_merge_and_normalize_rows_with_live_annotations(self):
        csv_rows = [
            {
                "stable_signature": "PthreadWaiter::Wait-a030f826",
                "occurrences": "4",
                "latest_report_id": "9dd7b05170400000",
                "versions": "37.1.3-16488488",
                "os_names": "Mac OS X",
                "crash_location": "Wait (waiter.cc:10)",
                "internal_msg": "",
                "looper_registrations": "1=GlobalLoop;",
                "list_annotations": "",
                "bug_urls": "",
                "bug_ids": "",
            },
            {
                "stable_signature": "FailWithoutStackTrace-7a37e8e9",
                "occurrences": "1",
                "latest_report_id": "190d58d170400000",
                "versions": "37.1.3-16373346",
                "os_names": "Mac OS X",
                "crash_location": "Flush (log_message.cc:498)",
                "internal_msg": "",
                "looper_registrations": "1=LauncherLoop;",
                "list_annotations": "abort() called",
                "bug_urls": "",
                "bug_ids": "",
            },
        ]
        # Simulate that PthreadWaiter::Wait-a030f826 was just linked via link-bug 10 seconds ago
        live_annotations = {
            "PthreadWaiter::Wait-a030f826": [
                {
                    "bug_id": "599999999",
                    "bug_url": "https://b.corp.google.com/issues/599999999",
                }
            ]
        }

        all_merged = merge_and_normalize_rows(
            csv_rows, live_annotations, raw=False, unassigned_only=False
        )
        self.assertEqual(len(all_merged), 2)
        self.assertEqual(all_merged[0]["bug_ids"], ["599999999"])
        self.assertEqual(all_merged[0]["build_ids"], ["16488488"])

        unassigned_merged = merge_and_normalize_rows(
            csv_rows, live_annotations, raw=False, unassigned_only=True
        )
        self.assertEqual(len(unassigned_merged), 1)
        self.assertEqual(
            unassigned_merged[0]["stable_signature"],
            "FailWithoutStackTrace-7a37e8e9",
        )

    def test_parse_search_annotations_output(self):
        stubby_out = """status {
  status_code: SUCCESS
}
annotations {
  StackSignature: "virtio_gpu_rutabaga_load-e709a6ba"
  BugInfo {
    BugID: 567125517
    BugUrl: "https://b.corp.google.com/issues/567125517"
  }
  Product: "AndroidEmulator"
  SignatureType: "Stable"
}
"""
        parsed = parse_search_annotations_output(stubby_out)
        self.assertIn("virtio_gpu_rutabaga_load-e709a6ba", parsed)
        self.assertEqual(
            parsed["virtio_gpu_rutabaga_load-e709a6ba"][0]["bug_id"], "567125517"
        )

    def test_normalize_bug_url_and_id(self):
        self.assertEqual(
            normalize_bug_url_and_id("567125517"),
            ("567125517", "https://b.corp.google.com/issues/567125517"),
        )
        self.assertEqual(
            normalize_bug_url_and_id("b/567125517"),
            ("567125517", "https://b.corp.google.com/issues/567125517"),
        )
        self.assertEqual(
            normalize_bug_url_and_id("https://b.corp.google.com/issues/567125517"),
            ("567125517", "https://b.corp.google.com/issues/567125517"),
        )
        with self.assertRaises(ValueError):
            normalize_bug_url_and_id("not-a-bug")

    def test_is_crash_report_id(self):
        self.assertTrue(is_crash_report_id("9dd7b05170400000"))
        self.assertTrue(
            is_crash_report_id("https://crash.corp.google.com/9dd7b05170400000")
        )
        self.assertFalse(is_crash_report_id("virtio_gpu_rutabaga_load-e709a6ba"))

    @patch("commands.crash.link_bug.subprocess.run")
    def test_resolve_signature_from_report_id_handles_null_and_quotes(
        self, mock_subproc
    ):
        mock_subproc.return_value = MagicMock(
            returncode=0,
            stdout="HINT: banner\nstable_signature\nNULL\n",
        )
        self.assertIsNone(resolve_signature_from_report_id("9dd7b05170400000"))

        mock_subproc.return_value = MagicMock(
            returncode=0,
            stdout='stable_signature\n"PthreadWaiter::Wait-a030f826"\n',
        )
        self.assertEqual(
            resolve_signature_from_report_id("9dd7b05170400000"),
            "PthreadWaiter::Wait-a030f826",
        )

    @patch("commands.crash.list_crashes.search_crash_annotations")
    @patch("commands.crash.list_crashes.execute_f1_query")
    @patch("commands.crash.list_crashes.print_result")
    def test_run_list_crashes_json(
        self, mock_print_res, mock_exec_f1, mock_search_anno
    ):
        mock_exec_f1.return_value = [
            {
                "stable_signature": "FailWithoutStackTrace-7a37e8e9",
                "occurrences": "3",
                "latest_report_id": "190d58d170400000",
                "versions": "37.1.3-16373346",
                "os_names": "Mac OS X",
                "crash_location": "Flush (log_message.cc:498)",
                "internal_msg": "",
                "looper_registrations": "1=LauncherLoop;",
                "list_annotations": "",
                "bug_urls": "",
                "bug_ids": "",
            }
        ]
        mock_search_anno.return_value = {}

        args = argparse.Namespace(
            hours=48,
            days=7,
            version_regex=r"0\.0\.1-|37",
            exclude_modules=r"Qt6|libandroid-emu|adb|netsimd",
            unassigned_only=True,
            raw=False,
            limit=25,
            accounting_group=None,
            print_sql=False,
            json=True,
        )
        run_list_crashes(args)
        mock_print_res.assert_called_once()
        payload = mock_print_res.call_args[0][0]
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["hours"], 168)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(
            payload["crashes"][0]["stable_signature"],
            "FailWithoutStackTrace-7a37e8e9",
        )

    @patch("commands.crash.link_bug.search_crash_annotations")
    @patch("commands.crash.link_bug.update_crash_annotation")
    @patch("commands.crash.link_bug.print_result")
    def test_run_link_bug_json(
        self, mock_print_res, mock_update_anno, mock_search_anno
    ):
        mock_update_anno.return_value = (True, "status { status_code: SUCCESS }")
        mock_search_anno.return_value = {
            "virtio_gpu_rutabaga_load-e709a6ba": [
                {
                    "bug_id": "567125517",
                    "bug_url": "https://b.corp.google.com/issues/567125517",
                }
            ]
        }

        args = argparse.Namespace(
            target="virtio_gpu_rutabaga_load-e709a6ba",
            bug="b/567125517",
            unlink=False,
            product="AndroidEmulator",
            json=True,
        )
        run_link_bug(args)
        mock_update_anno.assert_called_once_with(
            stable_signature="virtio_gpu_rutabaga_load-e709a6ba",
            bug_url="https://b.corp.google.com/issues/567125517",
            unlink=False,
            product_name="AndroidEmulator",
        )
        mock_print_res.assert_called_once()
        payload = mock_print_res.call_args[0][0]
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["bug_id"], "567125517")


if __name__ == "__main__":
    unittest.main()
