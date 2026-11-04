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

"""Hermetic unit tests for clients.f1_sql_client (zero @patch decorators)."""

import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from clients.f1_sql_client import (
    CapacitorTableBinding,
    FakeF1SqlClient,
    ProdF1SqlClient,
    format_sql_in_list,
    normalize_sql_query,
    parse_f1_csv_output,
    parse_f1_jsonl_output,
    quote_sql_string,
    validate_sql_identifier,
    validate_sql_string,
)
from core.errors import ExecutionError, SubprocessError, UsageError
from core.process import FakeProcessRunner


class F1SqlClientTest(unittest.TestCase):
    """Tests SQL parameter validation, Capacitor table bindings, ProdF1SqlClient, and FakeF1SqlClient."""

    def test_sql_parameter_validation_and_capacitor_bindings(self) -> None:
        self.assertEqual(
            quote_sql_string("branch", "emu-main-next_测试_🚀"),
            "'emu-main-next_测试_🚀'",
        )
        self.assertEqual(
            format_sql_in_list("targets", ["target_a", "target_b"]),
            "'target_a', 'target_b'",
        )
        self.assertEqual(validate_sql_identifier("table_alias", "test_runs_1"), "test_runs_1")

        for unsafe in ("' OR 1=1", 'bad"quote', "semi;colon", "back\\slash", "line\nbreak"):
            with self.assertRaises(UsageError):
                validate_sql_string("param", unsafe)

        with self.assertRaises(UsageError):
            validate_sql_identifier("alias", "123-invalid")

        binding = CapacitorTableBinding(
            alias="test_runs",
            cns_path="/cns/iq-d/home/sponge-eng/test_runs.capacitor*",
        )
        full_sql = normalize_sql_query(
            "SELECT target, status FROM test_runs",
            capacitor_tables=[binding],
        )
        self.assertIn(
            "DEFINE TABLE test_runs (format = 'capacitor', path = '/cns/iq-d/home/sponge-eng/test_runs.capacitor*');",
            full_sql,
        )
        self.assertTrue(full_sql.endswith("SELECT target, status FROM test_runs;"))

    def test_parse_f1_csv_output_strips_banners_and_normalizes_null(self) -> None:
        raw_stdout = (
            "Connecting to F1 server...\n"
            "I0428 12:00:00.000000 12345 f1_client.cc:99] Connected\n"
            "Profile: https://f1.corp.google.com/profile/abc\n"
            '"report_id","signature","bug_id"\n'
            '"rep_001","gfxstream::VkDecoder_测试_🚀","NULL"\n'
            '"rep_002","qemu_mutex_lock","485291270"\n'
        )
        rows = parse_f1_csv_output(
            raw_stdout, expected_header_columns=["report_id"]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["report_id"], "rep_001")
        self.assertEqual(rows[0]["signature"], "gfxstream::VkDecoder_测试_🚀")
        self.assertEqual(rows[0]["bug_id"], "")
        self.assertEqual(rows[1]["bug_id"], "485291270")

    def test_prod_f1_sql_client_csv_jsonl_and_scalar(self) -> None:
        runner = FakeProcessRunner()
        client = ProdF1SqlClient(
            runner,
            f1_sql_bin="/custom/f1-sql",
            default_accounting_group="android-emulator-f1",
        )

        # 1. query_csv + query_scalar
        runner.queue_response(
            stdout=(
                "Hint: Read the F1 skill first\n"
                '"bug_id"\n'
                '"485291270"\n'
            )
        )
        scalar = client.query_scalar("SELECT bug_id FROM crash.prod.latest LIMIT 1")
        self.assertEqual(scalar, "485291270")
        self.assertEqual(runner.calls[0].args[0], "/custom/f1-sql")
        self.assertIn("--csv_output=true", runner.calls[0].args)
        self.assertIn(
            "--f1_sql_accounting_group=android-emulator-f1",
            runner.calls[0].args,
        )
        self.assertEqual(
            runner.calls[0].input_text,
            "SELECT bug_id FROM crash.prod.latest LIMIT 1;",
        )

        # 2. query_jsonl with Capacitor table binding
        runner.queue_response(
            stdout=(
                '{"target": "//emulator:test_测试", "total_runs": 12}\n'
                '{"target": "//emulator:other_test", "total_runs": 4}\n'
            )
        )
        with tempfile.TemporaryDirectory(prefix="emu_f1_test_") as tmp_dir:
            jsonl_client = ProdF1SqlClient(
                runner,
                f1_sql_bin="/custom/f1-sql",
                scratch_dir=Path(tmp_dir),
            )
            rows = jsonl_client.query_jsonl(
                "SELECT target, total_runs FROM runs",
                capacitor_tables=[
                    CapacitorTableBinding("runs", "/cns/path/runs.capacitor*")
                ],
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["target"], "//emulator:test_测试")
            self.assertEqual(rows[0]["total_runs"], 12)

        # 3. Malformed JSONL raises ExecutionError
        with self.assertRaises(ExecutionError):
            parse_f1_jsonl_output("{invalid json line\n")

        # 4. Non-zero subprocess exit raises SubprocessError (not swallowed)
        runner.queue_response(returncode=1, stderr="NOT_AUTHORIZED: missing group")
        with self.assertRaises(SubprocessError) as err_ctx:
            client.query_csv("SELECT 1")
        self.assertIn("NOT_AUTHORIZED", err_ctx.exception.message)

    def test_fake_f1_sql_client_routing_and_queueing(self) -> None:
        fake = FakeF1SqlClient()
        fake.register_csv_query(
            "crash.prod.latest",
            [{"report_id": "r1", "signature": "sig_测试_🚀"}],
        )
        fake.register_jsonl_query(
            "FROM test_runs",
            [{"target_label": "//foo:bar", "failed_runs": 3}],
        )

        csv_rows = fake.query_csv("SELECT * FROM crash.prod.latest")
        self.assertEqual(len(csv_rows), 1)
        self.assertEqual(csv_rows[0]["signature"], "sig_测试_🚀")

        jsonl_rows = fake.query_jsonl(
            "SELECT * FROM test_runs",
            capacitor_tables=[
                CapacitorTableBinding("test_runs", "/cns/test.capacitor*")
            ],
        )
        self.assertEqual(len(jsonl_rows), 1)
        self.assertEqual(jsonl_rows[0]["failed_runs"], 3)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(len(fake.calls[1].capacitor_tables), 1)

        fake.queue_csv_rows([{"count": "42"}])
        self.assertEqual(fake.query_scalar("SELECT COUNT(*)"), "42")


if __name__ == "__main__":
    unittest.main()
