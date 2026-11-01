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

import argparse
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)

from commands import sbom


def _sample_valid_spdx():
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "emulator-37.1.0-12345",
        "documentNamespace": "https://spdx.google/emulator-37.1.0-12345-abcd",
        "creationInfo": {
            "creators": [
                "Organization: Google LLC",
                "Tool: android-emulator-sbom-generator-1.0",
            ],
            "created": "2026-10-17T00:00:00Z",
        },
        "packages": [
            {
                "name": "emulator",
                "SPDXID": "SPDXRef-Package-main",
                "versionInfo": "37.1.0-12345",
                "supplier": "Organization: Google LLC",
                "downloadLocation": "https://android.googlesource.com/platform/hardware/generic/goldfish",
                "filesAnalyzed": False,
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "Copyright (C) The Android Open Source Project",
            },
            {
                "name": "zlib",
                "SPDXID": "SPDXRef-Package-zlib-12345678",
                "versionInfo": "1.3.1",
                "supplier": "NOASSERTION",
                "downloadLocation": "https://zlib.net",
                "filesAnalyzed": False,
                "licenseConcluded": "LicenseRef-zlib-12345678",
                "licenseDeclared": "Zlib",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": "pkg:generic/zlib@1.3.1",
                    }
                ],
            },
        ],
        "hasExtractedLicensingInfos": [
            {
                "licenseId": "LicenseRef-zlib-12345678",
                "name": "Zlib",
                "extractedText": "Zlib license text",
                "seeAlsos": ["https://zlib.net"],
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": "SPDXRef-Package-main",
                "relationshipType": "DESCRIBES",
            },
            {
                "spdxElementId": "SPDXRef-Package-main",
                "relatedSpdxElement": "SPDXRef-Package-zlib-12345678",
                "relationshipType": "CONTAINS",
            },
        ],
    }


class SbomCommandTest(unittest.TestCase):

    def test_validate_spdx_structure_valid(self):
        doc = _sample_valid_spdx()
        res = sbom.validate_spdx_structure(doc)
        self.assertTrue(res["valid"])
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["total_packages"], 2)

    def test_validate_spdx_structure_detects_missing_fields(self):
        doc = _sample_valid_spdx()
        doc["spdxVersion"] = "SPDX-2.2"
        doc["packages"] = [doc["packages"][1]]  # remove SPDXRef-Package-main
        res = sbom.validate_spdx_structure(doc)
        self.assertFalse(res["valid"])
        self.assertTrue(any("SPDX-2.3" in e for e in res["errors"]))
        self.assertTrue(any("SPDXRef-Package-main" in e for e in res["errors"]))

    def test_validate_spdx_structure_checks_root_package_and_null_external_refs(self):
        doc = _sample_valid_spdx()
        doc["packages"][0]["licenseConcluded"] = ""
        doc["packages"][0]["supplier"] = ""
        doc["packages"][1]["externalRefs"] = None
        doc["packages"][1]["licenseConcluded"] = "Apache-2.0 OR LicenseRef-MissingCustom"
        res = sbom.validate_spdx_structure(doc)
        self.assertFalse(res["valid"])
        self.assertTrue(any("emulator" in e and "licenseConcluded" in e for e in res["errors"]))
        self.assertTrue(any("emulator" in e and "supplier" in e for e in res["errors"]))
        self.assertTrue(any("LicenseRef-MissingCustom" in e for e in res["errors"]))
        self.assertEqual(res["missing_purls"], ["zlib"])

    def test_validate_and_diff_handle_malformed_json_structures(self):
        res_list = sbom.validate_spdx_structure([])  # type: ignore[arg-type]
        self.assertFalse(res_list["valid"])
        self.assertEqual(res_list["total_packages"], 0)

        doc = _sample_valid_spdx()
        doc["packages"].append("not-a-dict")
        doc["packages"].append({"name": 12345, "SPDXID": None, "licenseConcluded": ["bad"]})
        res_malformed = sbom.validate_spdx_structure(doc)
        self.assertFalse(res_malformed["valid"])

        diff_res = sbom.diff_spdx_documents(doc, [])  # type: ignore[arg-type]
        self.assertEqual(diff_res["added"], [])
        self.assertEqual(len(diff_res["removed"]), 1)

    def test_validate_reports_non_object_list_elements(self):
        doc = _sample_valid_spdx()
        doc["packages"].append("not-a-package")
        doc["hasExtractedLicensingInfos"].append(None)
        doc["relationships"].append(42)
        doc["creationInfo"]["creators"].append(7)
        res = sbom.validate_spdx_structure(doc)
        self.assertFalse(res["valid"])
        for key in ("packages", "hasExtractedLicensingInfos", "relationships"):
            self.assertTrue(
                any(f"'{key}'" in e and "not JSON objects" in e for e in res["errors"]), key
            )
        self.assertTrue(any("creators" in e for e in res["errors"]))
        self.assertEqual(res["total_packages"], 2)

    def test_validate_checks_identifiers_references_and_required_fields(self):
        doc = _sample_valid_spdx()
        zlib = doc["packages"][1]
        doc["packages"].append(dict(zlib))  # Duplicate SPDXID.
        doc["packages"].append(
            {
                "SPDXID": "not-an-spdx-id",
                "supplier": "NOASSERTION",
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "LicenseRef-Undeclared",
            }
        )
        doc["hasExtractedLicensingInfos"].append({"licenseId": "Custom", "extractedText": "x"})
        doc["hasExtractedLicensingInfos"].append({"licenseId": "LicenseRef-empty"})
        doc["relationships"].append(
            {
                "spdxElementId": "SPDXRef-Package-main",
                "relatedSpdxElement": "SPDXRef-Package-missing",
                "relationshipType": "CONTAINS",
            }
        )
        res = sbom.validate_spdx_structure(doc)
        errors = res["errors"]
        self.assertFalse(res["valid"])
        self.assertIn("Duplicate SPDXID SPDXRef-Package-zlib-12345678", errors)
        self.assertTrue(any("invalid SPDXID 'not-an-spdx-id'" in e for e in errors))
        self.assertTrue(any("is missing name" in e for e in errors))
        self.assertTrue(any("missing versionInfo" in e for e in errors))
        self.assertTrue(any("missing downloadLocation" in e for e in errors))
        self.assertTrue(any("licenseDeclared references LicenseRef-Undeclared" in e for e in errors))
        self.assertTrue(any("invalid licenseId 'Custom'" in e for e in errors))
        self.assertIn("Extracted license LicenseRef-empty has no extractedText", errors)
        self.assertTrue(any("SPDXRef-Package-missing has an unknown relatedSpdxElement" in e for e in errors))

    def test_validate_accepts_external_and_no_element_relationship_targets(self):
        doc = _sample_valid_spdx()
        doc["relationships"].append(
            {
                "spdxElementId": "SPDXRef-Package-zlib-12345678",
                "relatedSpdxElement": "NOASSERTION",
                "relationshipType": "DEPENDS_ON",
            }
        )
        doc["relationships"].append(
            {
                "spdxElementId": "SPDXRef-Package-main",
                "relatedSpdxElement": "DocumentRef-other:SPDXRef-Package-x",
                "relationshipType": "DEPENDS_ON",
            }
        )
        res = sbom.validate_spdx_structure(doc)
        self.assertEqual(res["errors"], [])

    def test_resolve_workspace_dir_handles_deleted_cwd(self):
        with mock.patch.object(sbom, "get_source_directory", return_value=None), mock.patch.object(
            sbom.os, "getcwd", side_effect=FileNotFoundError("cwd was deleted")
        ):
            self.assertIsNone(sbom._resolve_workspace_dir())

    def test_resolve_workspace_dir_prefers_the_workspace_over_nested_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = os.path.realpath(tmpdir)
            for rel in (
                ("build", "bazel", "toplevel.MODULE.bazel"),
                ("hardware", "generic", "goldfish", "MODULE.bazel"),
                ("hardware", "generic", "goldfish", "emulator", "BUILD.bazel"),
            ):
                path = os.path.join(root, *rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n")
            nested = os.path.join(root, "hardware", "generic", "goldfish", "emulator")
            with mock.patch.object(sbom, "get_source_directory", return_value=None), mock.patch.object(
                sbom.os, "getcwd", return_value=nested
            ):
                self.assertEqual(sbom._resolve_workspace_dir(), root)

    def test_abspath_handles_deleted_cwd(self):
        with mock.patch.object(sbom.os, "getcwd", side_effect=FileNotFoundError("cwd was deleted")):
            self.assertIsNone(sbom._abspath("relative.spdx.json"))
            self.assertEqual(sbom._abspath("/abs/file.spdx.json"), "/abs/file.spdx.json")

    def test_missing_sbom_message_names_invalid_source_dir_and_checked_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = os.path.join(tmpdir, "missing")
            self.assertEqual(
                sbom._missing_sbom_message("emu-main-next", missing),
                f"--source-dir '{missing}' is not a directory",
            )
            message = sbom._missing_sbom_message("emu-main-next", tmpdir)
            self.assertIn(f"No built SBOM found under {tmpdir}", message)
            self.assertIn(sbom.DEFAULT_BUILTIN_SBOM_CANDIDATES[0], message)

    def test_run_diff_names_the_file_that_failed_to_parse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            good = os.path.join(tmpdir, "good.spdx.json")
            bad = os.path.join(tmpdir, "bad.spdx.json")
            with open(good, "w", encoding="utf-8", newline="\n") as f:
                json.dump(_sample_valid_spdx(), f)
            with open(bad, "w", encoding="utf-8", newline="\n") as f:
                f.write("{not json")
            args = argparse.Namespace(old_sbom=good, new_sbom=bad, json=True)
            with mock.patch.object(sbom, "print_result") as print_result:
                sbom.run_diff(args)
            payload = print_result.call_args[0][0]
            self.assertEqual(payload["status"], "error")
            self.assertIn(f"Failed to parse SBOM JSON in {bad}", payload["summary"])

    def test_diff_keys_duplicate_package_names_by_spdx_id(self):
        doc_a = _sample_valid_spdx()
        second = dict(doc_a["packages"][1], SPDXID="SPDXRef-Package-zlib-other")
        doc_a["packages"].append(second)
        doc_b = copy.deepcopy(doc_a)
        doc_b["packages"][2]["versionInfo"] = "2.0"
        diff = sbom.diff_spdx_documents(doc_a, doc_b)
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        self.assertEqual([c["name"] for c in diff["changed"]], ["zlib (SPDXRef-Package-zlib-other)"])

    def test_diff_spdx_documents(self):
        doc_a = _sample_valid_spdx()
        doc_b = _sample_valid_spdx()
        # Upgrade zlib and add libpng in doc_b
        doc_b["packages"][1]["versionInfo"] = "1.3.2"
        doc_b["packages"].append(
            {
                "name": "libpng",
                "SPDXID": "SPDXRef-Package-libpng-abcdef12",
                "versionInfo": "1.6.40",
                "licenseDeclared": "Libpng",
            }
        )
        diff = sbom.diff_spdx_documents(doc_a, doc_b)
        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(diff["added"][0]["name"], "libpng")
        self.assertEqual(len(diff["removed"]), 0)
        self.assertEqual(len(diff["changed"]), 1)
        self.assertEqual(diff["changed"][0]["name"], "zlib")
        self.assertEqual(diff["changed"][0]["old_version"], "1.3.1")
        self.assertEqual(diff["changed"][0]["new_version"], "1.3.2")

    def test_parser_registers_sbom_subcommands(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        sbom.register_parser(subparsers)

        args_gen = parser.parse_args(["sbom", "generate", "--aosp", "--build-id", "999"])
        self.assertEqual(args_gen.sbom_cmd, "generate")
        self.assertIsInstance(args_gen._command_instance, sbom.SbomGenerateCommand)
        self.assertTrue(args_gen.aosp)
        self.assertEqual(args_gen.build_id, "999")
        self.assertIsNone(args_gen.config)

        args_cfg = parser.parse_args(
            ["sbom", "generate", "--config", "release", "--config", "hermetic"]
        )
        self.assertEqual(args_cfg.config, ["release", "hermetic"])

        args_insp = parser.parse_args(["sbom", "inspect", "--filter", "qemu"])
        self.assertEqual(args_insp.sbom_cmd, "inspect")
        self.assertEqual(args_insp.filter, "qemu")

        args_audit = parser.parse_args(["sbom", "audit", "--strict"])
        self.assertEqual(args_audit.sbom_cmd, "audit")
        self.assertTrue(args_audit.strict)

        args_diff = parser.parse_args(["sbom", "diff", "old.json", "new.json"])
        self.assertEqual(args_diff.sbom_cmd, "diff")
        self.assertIsInstance(args_diff._command_instance, sbom.SbomDiffCommand)
        self.assertIsInstance(args_insp._command_instance, sbom.SbomInspectCommand)
        self.assertIsInstance(args_audit._command_instance, sbom.SbomAuditCommand)
        self.assertEqual(args_diff.old_sbom, "old.json")
        self.assertEqual(args_diff.new_sbom, "new.json")


if __name__ == "__main__":
    unittest.main()
