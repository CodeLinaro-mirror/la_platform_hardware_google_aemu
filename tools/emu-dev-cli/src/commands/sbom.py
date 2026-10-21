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

"""SBOM (Software Bill of Materials) generation, validation, inspection, and diffing."""

import argparse
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Set

from commands.source_directory import get_source_directory
from lib.bazel import find_bazel_cmd
from lib.output import format_markdown_table, print_result

SPDX_VALIDATOR_BIN = "/google/bin/releases/opensource/tools/spdx_validator/validate"
DEFAULT_SBOM_TARGET = "@goldfish//emulator:emulator_sbom_spdx"
# Locations of DEFAULT_SBOM_TARGET's output relative to the workspace root, checked in order.
DEFAULT_BUILTIN_SBOM_CANDIDATES = (
    "bazel-bin/external/goldfish+/emulator/sbom.spdx.json",
    "bazel-bin/external/goldfish/emulator/sbom.spdx.json",
    "bazel-bin/emulator/sbom.spdx.json",
)
# Files that identify a source workspace, strongest group first, so that an emu-main-next
# checkout wins over a nested Bazel module such as hardware/generic/goldfish.
_WORKSPACE_MARKERS = (
    (
        os.path.join("build", "bazel", "toplevel.MODULE.bazel"),
        os.path.join("hardware", "generic", "goldfish", "emulator", "BUILD.bazel"),
    ),
    (os.path.join("emulator", "BUILD.bazel"), "MODULE.bazel", "WORKSPACE"),
)
_MAIN_PACKAGE_ID = "SPDXRef-Package-main"
_SPDX_ID_RE = re.compile(r"SPDXRef-[A-Za-z0-9.\-]+")
_LICENSE_REF_RE = re.compile(r"LicenseRef-[A-Za-z0-9.\-]+")
# Values that a relationship may use instead of an element identifier (SPDX 2.3, clause 11.1).
_SPDX_NO_ELEMENT_VALUES = frozenset({"NONE", "NOASSERTION"})


def _abspath(path: str) -> Optional[str]:
    """Returns the absolute form of a user-supplied path, or None if it cannot be resolved."""
    try:
        return os.path.abspath(os.path.expanduser(path))
    except OSError:  # Relative paths need os.getcwd(), which fails if the cwd was deleted.
        return None


def _workspace_error(source_dir: Optional[str], branch: str) -> str:
    """Explains why no workspace was found, naming the invalid --source-dir if there is one."""
    if source_dir:
        return f"--source-dir '{source_dir}' is not a directory"
    return (
        f"Could not find a source workspace for branch '{branch}': pass --source-dir or run "
        f"'emu-dev-cli source-directory set {branch} <path>'"
    )


def _resolve_workspace_dir(branch: str = "emu-main-next", source_dir: Optional[str] = None) -> Optional[str]:
    """Returns --source-dir, the directory registered for `branch`, or a workspace above the cwd."""
    if source_dir:
        expanded = _abspath(source_dir)
        return expanded if expanded and os.path.isdir(expanded) else None
    configured = get_source_directory(branch)
    if isinstance(configured, str) and configured and os.path.isdir(configured):
        return os.path.abspath(configured)
    try:
        cwd = os.path.realpath(os.getcwd())
    except OSError:  # The current working directory was deleted.
        return None
    ancestors = [cwd]
    while os.path.dirname(ancestors[-1]) != ancestors[-1]:
        ancestors.append(os.path.dirname(ancestors[-1]))
    for markers in _WORKSPACE_MARKERS:
        for directory in ancestors:
            if any(os.path.exists(os.path.join(directory, marker)) for marker in markers):
                return directory
    return None


def _resolve_sbom_file(sbom_path: Optional[str], branch: str = "emu-main-next", source_dir: Optional[str] = None) -> Optional[str]:
    if sbom_path:
        expanded = _abspath(sbom_path)
        if expanded and os.path.isfile(expanded):
            return expanded
        return None

    ws = _resolve_workspace_dir(branch=branch, source_dir=source_dir)
    if ws:
        for rel in DEFAULT_BUILTIN_SBOM_CANDIDATES:
            candidate = os.path.join(ws, rel)
            if os.path.isfile(candidate):
                return candidate
    return None


def _missing_sbom_message(branch: str, source_dir: Optional[str]) -> str:
    """Explains why no SBOM was found when --sbom was not given."""
    ws = _resolve_workspace_dir(branch=branch, source_dir=source_dir)
    if not ws:
        return _workspace_error(source_dir, branch)
    return (
        f"No built SBOM found under {ws} (checked: {', '.join(DEFAULT_BUILTIN_SBOM_CANDIDATES)}). "
        "Run 'emu-dev-cli sbom generate' first or pass --sbom <path>."
    )


def _as_str(val: Any) -> str:
    """Returns a JSON value without surrounding whitespace if it is a string, otherwise ''."""
    return val.strip() if isinstance(val, str) else ""


def _extract_purl(pkg: Dict[str, Any]) -> str:
    if not isinstance(pkg, dict):
        return ""
    refs = pkg.get("externalRefs")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict) and ref.get("referenceType") == "purl":
                loc = ref.get("referenceLocator")
                if isinstance(loc, str):
                    return loc.strip()
    return ""


def _object_list(doc: Dict[str, Any], key: str, errors: List[str]) -> List[Dict[str, Any]]:
    """Returns the JSON objects of an optional list field, recording errors for malformed values."""
    raw = doc.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"Top-level '{key}' field must be a list")
        return []
    objects = [item for item in raw if isinstance(item, dict)]
    if len(objects) != len(raw):
        errors.append(
            f"Top-level '{key}' field contains {len(raw) - len(objects)} element(s) "
            "that are not JSON objects"
        )
    return objects


def validate_spdx_structure(doc: Dict[str, Any], spdx_path: Optional[str] = None) -> Dict[str, Any]:
    """Validates an SPDX 2.3 JSON document against go/spdx-style-guide rules and optional Google validator binary."""
    if not isinstance(doc, dict):
        return {
            "valid": False,
            "errors": ["Root SPDX document must be a JSON object (dict)"],
            "warnings": [],
            "missing_versions": [],
            "missing_urls": [],
            "missing_purls": [],
            "validator_binary_used": False,
            "validator_output": "",
            "total_packages": 0,
        }

    errors: List[str] = []
    warnings: List[str] = []

    if doc.get("spdxVersion") != "SPDX-2.3":
        errors.append(f"Expected spdxVersion 'SPDX-2.3', got {doc.get('spdxVersion')!r}")
    if doc.get("dataLicense") != "CC0-1.0":
        errors.append(f"Expected dataLicense 'CC0-1.0', got {doc.get('dataLicense')!r}")
    if doc.get("SPDXID") != "SPDXRef-DOCUMENT":
        errors.append(f"Expected SPDXID 'SPDXRef-DOCUMENT', got {doc.get('SPDXID')!r}")
    if not _as_str(doc.get("name")):
        errors.append("Missing top-level document 'name'")
    if not _as_str(doc.get("documentNamespace")):
        errors.append("Missing top-level 'documentNamespace'")

    creation_info = doc.get("creationInfo")
    if not isinstance(creation_info, dict):
        errors.append("Missing 'creationInfo' object")
        creation_info = {}
    if not _as_str(creation_info.get("created")):
        errors.append("Missing 'creationInfo.created' timestamp")
    raw_creators = creation_info.get("creators")
    if not isinstance(raw_creators, list) or not all(isinstance(c, str) for c in raw_creators):
        errors.append("'creationInfo.creators' must be a list of strings")
    creators = [c for c in raw_creators if isinstance(c, str)] if isinstance(raw_creators, list) else []
    org_creators = [c for c in creators if c.startswith("Organization:")]
    if org_creators != ["Organization: Google LLC"]:
        errors.append(
            f"Creator Organization must be ['Organization: Google LLC'], got {org_creators}"
        )

    packages = _object_list(doc, "packages", errors)
    extracted = _object_list(doc, "hasExtractedLicensingInfos", errors)
    relationships = _object_list(doc, "relationships", errors)

    extracted_ids = set()
    for lic in extracted:
        license_id = _as_str(lic.get("licenseId"))
        if not _LICENSE_REF_RE.fullmatch(license_id):
            errors.append(f"Extracted license has an invalid licenseId {license_id!r}")
            continue
        if license_id in extracted_ids:
            errors.append(f"Duplicate extracted licenseId {license_id}")
        extracted_ids.add(license_id)
        if not _as_str(lic.get("extractedText")):
            errors.append(f"Extracted license {license_id} has no extractedText")

    # Identifiers that relationships may reference: the document, packages, files, snippets.
    element_ids = {"SPDXRef-DOCUMENT"}
    for key in ("files", "snippets"):
        element_ids.update(_as_str(item.get("SPDXID")) for item in _object_list(doc, key, errors))

    has_main_pkg = False
    missing_versions: List[str] = []
    missing_purls: List[str] = []
    missing_urls: List[str] = []

    for pkg in packages:
        spdx_id = _as_str(pkg.get("SPDXID"))
        name = _as_str(pkg.get("name"))
        display = name or spdx_id or "<unnamed>"
        if not name:
            errors.append(f"Package {spdx_id or '<missing SPDXID>'} is missing name")
        if not _SPDX_ID_RE.fullmatch(spdx_id):
            errors.append(f"Package '{display}' has an invalid SPDXID {spdx_id!r}")
        elif spdx_id in element_ids:
            errors.append(f"Duplicate SPDXID {spdx_id}")
        else:
            element_ids.add(spdx_id)
        if spdx_id == _MAIN_PACKAGE_ID:
            has_main_pkg = True

        ver = _as_str(pkg.get("versionInfo"))
        if not ver:
            errors.append(f"Package '{display}' is missing versionInfo")
        if not ver or ver == "unspecified":
            missing_versions.append(display)

        dl = _as_str(pkg.get("downloadLocation"))
        if not dl:
            errors.append(f"Package '{display}' is missing downloadLocation")
        if not dl or dl == "NOASSERTION":
            missing_urls.append(display)

        if spdx_id != _MAIN_PACKAGE_ID and not _extract_purl(pkg):
            missing_purls.append(display)

        if not _as_str(pkg.get("licenseConcluded")):
            errors.append(f"Package '{display}' is missing licenseConcluded")
        for field_name in ("licenseConcluded", "licenseDeclared"):
            for ref in _LICENSE_REF_RE.findall(_as_str(pkg.get(field_name))):
                if ref not in extracted_ids:
                    errors.append(
                        f"Package '{display}' {field_name} references {ref} not present in hasExtractedLicensingInfos"
                    )

        if not _as_str(pkg.get("supplier")):
            errors.append(f"Package '{display}' is missing supplier")

    if not has_main_pkg:
        errors.append("Missing root package with SPDXID 'SPDXRef-Package-main'")

    has_describes = False
    for rel in relationships:
        rel_type = _as_str(rel.get("relationshipType"))
        source = _as_str(rel.get("spdxElementId"))
        target = _as_str(rel.get("relatedSpdxElement"))
        if not (rel_type and source and target):
            errors.append(
                "Relationship is missing spdxElementId, relatedSpdxElement, or relationshipType"
            )
            continue
        if source not in element_ids and not source.startswith("DocumentRef-"):
            errors.append(f"Relationship {source} {rel_type} {target} has an unknown spdxElementId")
        if (
            target not in element_ids
            and target not in _SPDX_NO_ELEMENT_VALUES
            and not target.startswith("DocumentRef-")
        ):
            errors.append(f"Relationship {source} {rel_type} {target} has an unknown relatedSpdxElement")
        if (rel_type, source, target) in (
            ("DESCRIBES", "SPDXRef-DOCUMENT", _MAIN_PACKAGE_ID),
            ("DESCRIBED_BY", _MAIN_PACKAGE_ID, "SPDXRef-DOCUMENT"),
        ):
            has_describes = True
    if not has_describes:
        errors.append(
            "Missing DESCRIBES relationship referencing 'SPDXRef-Package-main'"
        )

    if missing_versions:
        warnings.append(
            f"{len(missing_versions)} package(s) have unspecified version: {', '.join(missing_versions[:10])}"
        )
    if missing_urls:
        warnings.append(
            f"{len(missing_urls)} package(s) have NOASSERTION downloadLocation: {', '.join(missing_urls[:10])}"
        )
    if missing_purls:
        warnings.append(
            f"{len(missing_purls)} package(s) missing purl externalRef: {', '.join(missing_purls[:10])}"
        )

    validator_bin_used = False
    validator_output = ""
    if spdx_path and os.path.isfile(SPDX_VALIDATOR_BIN) and os.access(SPDX_VALIDATOR_BIN, os.X_OK):
        try:
            res = subprocess.run(
                [SPDX_VALIDATOR_BIN, f"-spdx_json={spdx_path}"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"spdx_validator execution failed: {exc}")
        else:
            validator_bin_used = True
            validator_output = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            if res.returncode != 0:
                errors.append(
                    f"spdx_validator exited with code {res.returncode}: {validator_output or '<no output>'}"
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "missing_versions": missing_versions,
        "missing_urls": missing_urls,
        "missing_purls": missing_purls,
        "validator_binary_used": validator_bin_used,
        "validator_output": validator_output,
        "total_packages": len(packages),
    }


def run_generate(args: argparse.Namespace) -> None:
    json_mode = getattr(args, "json", False)
    ws = _resolve_workspace_dir(branch=args.branch, source_dir=args.source_dir)
    if not ws:
        print_result(
            {
                "status": "error",
                "action": "sbom generate",
                "summary": _workspace_error(args.source_dir, args.branch),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    bazel_cmd = find_bazel_cmd(ws)
    cmd = [
        bazel_cmd,
        "build",
        DEFAULT_SBOM_TARGET,
        f"--@goldfish//emulator:build_id={args.build_id}",
    ]
    if args.aosp:
        cmd.extend(["--config=aosp", "--lockfile_mode=off"])
    for cfg in args.config or []:
        cmd.append(f"--config={cfg}")

    try:
        res = subprocess.run(
            cmd,
            cwd=ws,
            capture_output=json_mode,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print_result(
            {
                "status": "error",
                "action": "sbom generate",
                "summary": f"Failed to execute Bazel in {ws}: {exc}",
                "error_message": str(exc),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    if res.returncode != 0:
        err_msg = (res.stderr or res.stdout or "Bazel build failed").strip() if json_mode else "Bazel build failed"
        print_result(
            {
                "status": "error",
                "action": "sbom generate",
                "summary": f"Failed to build SBOM in {ws}",
                "error_message": err_msg,
                "exit_code": res.returncode,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    built_sbom = _resolve_sbom_file(None, branch=args.branch, source_dir=args.source_dir)
    if not built_sbom:
        print_result(
            {
                "status": "error",
                "action": "sbom generate",
                "summary": f"Built SBOM file not found under workspace {ws}",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    final_path = built_sbom
    if args.out:
        out_abs = _abspath(args.out)
        try:
            if out_abs is None:
                raise OSError(f"cannot resolve {args.out!r}: the current directory no longer exists")
            # Like cp, an existing directory or a trailing separator means "copy into this directory".
            if args.out.endswith(("/", os.sep)) or os.path.isdir(out_abs):
                out_abs = os.path.join(out_abs, os.path.basename(built_sbom))
            os.makedirs(os.path.dirname(out_abs), exist_ok=True)
            if os.path.realpath(built_sbom) != os.path.realpath(out_abs):
                shutil.copyfile(built_sbom, out_abs)
        except OSError as e:
            print_result(
                {
                    "status": "error",
                    "action": "sbom generate",
                    "summary": f"Failed to write output SBOM to {out_abs or args.out}: {e}",
                    "exit_code": 1,
                },
                json_mode=json_mode,
                is_error=True,
            )
            return
        final_path = out_abs

    try:
        with open(final_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print_result(
            {
                "status": "error",
                "action": "sbom generate",
                "summary": f"Failed to parse SBOM JSON: {e}",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    validation = validate_spdx_structure(doc, spdx_path=final_path)
    package_count = validation["total_packages"]
    summary = f"Generated SPDX 2.3 SBOM ({package_count} packages) at {final_path}"
    if not validation["valid"]:
        summary += f"; validation FAILED ({len(validation['errors'])} errors)"

    print_result(
        {
            "status": "success" if validation["valid"] else "error",
            "action": "sbom generate",
            "summary": summary,
            "sbom_path": final_path,
            "document_name": doc.get("name") if isinstance(doc, dict) else None,
            "spdx_version": doc.get("spdxVersion") if isinstance(doc, dict) else None,
            "package_count": package_count,
            "valid": validation["valid"],
            "validator_binary_used": validation["validator_binary_used"],
            "errors": (
                validation["errors"]
                if json_mode
                else ("; ".join(validation["errors"]) or "none")
            ),
            "warnings": (
                validation["warnings"]
                if json_mode
                else ("; ".join(validation["warnings"]) or "none")
            ),
        },
        json_mode=json_mode,
        is_error=not validation["valid"],
    )


def run_inspect(args: argparse.Namespace) -> None:
    json_mode = getattr(args, "json", False)
    sbom_file = _resolve_sbom_file(args.sbom, branch=args.branch, source_dir=args.source_dir)
    if not sbom_file:
        msg = (
            f"Specified SBOM file not found: {args.sbom}"
            if args.sbom
            else _missing_sbom_message(args.branch, args.source_dir)
        )
        print_result(
            {
                "status": "error",
                "action": "sbom inspect",
                "summary": msg,
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    try:
        with open(sbom_file, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print_result(
            {
                "status": "error",
                "action": "sbom inspect",
                "summary": f"Failed to parse SBOM JSON: {e}",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    if not isinstance(doc, dict):
        print_result(
            {
                "status": "error",
                "action": "sbom inspect",
                "summary": f"Invalid SBOM JSON in {sbom_file}: root must be a JSON object (dict)",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    raw_pkgs = doc.get("packages")
    pkgs = [p for p in (raw_pkgs if isinstance(raw_pkgs, list) else []) if isinstance(p, dict)]
    filter_q = (args.filter or "").lower().strip()
    rows = []
    filtered_pkgs = []

    for pkg in pkgs:
        name = _as_str(pkg.get("name"))
        ver = _as_str(pkg.get("versionInfo"))
        lic_decl = _as_str(pkg.get("licenseDeclared"))
        supplier = _as_str(pkg.get("supplier")).replace("Organization: ", "")
        purl = _extract_purl(pkg)
        if filter_q and not any(
            filter_q in s.lower() for s in (name, ver, lic_decl, supplier, purl)
        ):
            continue
        filtered_pkgs.append(pkg)
        rows.append([name, ver, lic_decl, supplier, purl])

    table = format_markdown_table(
        ["Package", "Version", "Declared License", "Supplier", "PURL"],
        rows,
    )

    print_result(
        {
            "status": "success",
            "action": "sbom inspect",
            "summary": f"Inspected {len(filtered_pkgs)} of {len(pkgs)} package(s) in {sbom_file}",
            "sbom_path": sbom_file,
            "document_name": doc.get("name"),
            "spdx_version": doc.get("spdxVersion"),
            "package_count": len(filtered_pkgs),
            "markdown_table": table,
            "packages": filtered_pkgs,
        },
        json_mode=json_mode,
    )


def run_audit(args: argparse.Namespace) -> None:
    json_mode = getattr(args, "json", False)
    sbom_file = _resolve_sbom_file(args.sbom, branch=args.branch, source_dir=args.source_dir)
    if not sbom_file:
        msg = (
            f"Specified SBOM file not found: {args.sbom}"
            if args.sbom
            else _missing_sbom_message(args.branch, args.source_dir)
        )
        print_result(
            {
                "status": "error",
                "action": "sbom audit",
                "summary": msg,
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    try:
        with open(sbom_file, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print_result(
            {
                "status": "error",
                "action": "sbom audit",
                "summary": f"Failed to parse SBOM JSON: {e}",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    res = validate_spdx_structure(doc, spdx_path=sbom_file)
    is_err = not res["valid"] or (args.strict and bool(res["warnings"]))

    print_result(
        {
            "status": "error" if is_err else "success",
            "action": "sbom audit",
            "summary": (
                f"SPDX 2.3 audit {'PASSED' if not is_err else 'FAILED'} for {sbom_file} "
                f"({res['total_packages']} packages, {len(res['errors'])} errors, {len(res['warnings'])} warnings)"
            ),
            "sbom_path": sbom_file,
            "valid": res["valid"],
            "validator_binary_used": res["validator_binary_used"],
            "validator_output": res["validator_output"],
            "total_packages": res["total_packages"],
            "errors": (
                res["errors"] if json_mode else ("; ".join(res["errors"]) or "none")
            ),
            "warnings": (
                res["warnings"] if json_mode else ("; ".join(res["warnings"]) or "none")
            ),
            "missing_versions": res["missing_versions"],
            "missing_urls": res["missing_urls"],
            "missing_purls": res["missing_purls"],
            "exit_code": 1 if is_err else 0,
        },
        json_mode=json_mode,
        is_error=is_err,
    )


def _index_packages_for_diff(
    doc: Dict[str, Any], duplicate_names: Set[str]
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    if not isinstance(doc, dict):
        return indexed
    raw_pkgs = doc.get("packages")
    pkgs = raw_pkgs if isinstance(raw_pkgs, list) else []
    for idx, p in enumerate(pkgs):
        if not isinstance(p, dict):
            continue
        name = _as_str(p.get("name"))
        spdx_id = _as_str(p.get("SPDXID")) or f"idx-{idx}"
        if spdx_id == _MAIN_PACKAGE_ID or not name:
            continue
        key = f"{name} ({spdx_id})" if name in duplicate_names else name
        indexed[key] = p
    return indexed


def diff_spdx_documents(doc_a: Dict[str, Any], doc_b: Dict[str, Any]) -> Dict[str, Any]:
    """Computes added, removed, and changed packages between two SPDX JSON documents."""
    duplicate_names: Set[str] = set()
    for doc in (doc_a, doc_b):
        if not isinstance(doc, dict):
            continue
        counts: Dict[str, int] = {}
        raw_pkgs = doc.get("packages")
        pkgs = raw_pkgs if isinstance(raw_pkgs, list) else []
        for p in pkgs:
            if not isinstance(p, dict):
                continue
            spdx_id = _as_str(p.get("SPDXID"))
            name = _as_str(p.get("name"))
            if spdx_id != _MAIN_PACKAGE_ID and name:
                counts[name] = counts.get(name, 0) + 1
        for name, cnt in counts.items():
            if cnt > 1:
                duplicate_names.add(name)

    pkgs_a = _index_packages_for_diff(doc_a, duplicate_names)
    pkgs_b = _index_packages_for_diff(doc_b, duplicate_names)

    names_a = set(pkgs_a.keys())
    names_b = set(pkgs_b.keys())

    added = [
        {
            "name": n,
            "version": _as_str(pkgs_b[n].get("versionInfo")),
            "license": _as_str(pkgs_b[n].get("licenseDeclared")),
        }
        for n in sorted(names_b - names_a)
    ]
    removed = [
        {
            "name": n,
            "version": _as_str(pkgs_a[n].get("versionInfo")),
            "license": _as_str(pkgs_a[n].get("licenseDeclared")),
        }
        for n in sorted(names_a - names_b)
    ]
    changed = []
    for n in sorted(names_a & names_b):
        pa, pb = pkgs_a[n], pkgs_b[n]
        va, vb = _as_str(pa.get("versionInfo")), _as_str(pb.get("versionInfo"))
        la, lb = _as_str(pa.get("licenseDeclared")), _as_str(pb.get("licenseDeclared"))
        if va != vb or la != lb:
            changed.append(
                {
                    "name": n,
                    "old_version": va,
                    "new_version": vb,
                    "old_license": la,
                    "new_license": lb,
                }
            )

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def run_diff(args: argparse.Namespace) -> None:
    json_mode = getattr(args, "json", False)
    docs = []
    paths = []
    for raw_path in (args.old_sbom, args.new_sbom):
        path = _abspath(raw_path) or raw_path
        error = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except OSError as exc:
            error = f"Failed to read SBOM {path}: {exc}"
        except ValueError as exc:  # json.JSONDecodeError and UnicodeDecodeError.
            error = f"Failed to parse SBOM JSON in {path}: {exc}"
        else:
            if not isinstance(doc, dict):
                error = f"Invalid SBOM JSON in {path}: root must be a JSON object (dict)"
        if error:
            print_result(
                {
                    "status": "error",
                    "action": "sbom diff",
                    "summary": error,
                    "exit_code": 1,
                },
                json_mode=json_mode,
                is_error=True,
            )
            return
        docs.append(doc)
        paths.append(path)
    doc_a, doc_b = docs
    path_a, path_b = paths

    diff = diff_spdx_documents(doc_a, doc_b)
    rows = []
    for item in diff["added"]:
        rows.append(["+ ADDED", item["name"], "-", item["version"], item["license"]])
    for item in diff["removed"]:
        rows.append(["- REMOVED", item["name"], item["version"], "-", item["license"]])
    for item in diff["changed"]:
        lic_str = (
            item["new_license"]
            if item["old_license"] == item["new_license"]
            else f"{item['old_license']} -> {item['new_license']}"
        )
        rows.append(
            [
                "~ CHANGED",
                item["name"],
                item["old_version"],
                item["new_version"],
                lic_str,
            ]
        )

    table = format_markdown_table(
        ["Change", "Package", "Old Version", "New Version", "License"],
        rows,
    )

    print_result(
        {
            "status": "success",
            "action": "sbom diff",
            "summary": (
                f"SBOM diff: +{len(diff['added'])} added, "
                f"-{len(diff['removed'])} removed, ~{len(diff['changed'])} changed"
            ),
            "old_sbom": path_a,
            "new_sbom": path_b,
            "added": diff["added"],
            "removed": diff["removed"],
            "changed": diff["changed"],
            "markdown_table": table,
        },
        json_mode=json_mode,
    )


def register_parser(subparsers) -> None:
    sbom_parser = subparsers.add_parser(
        "sbom",
        help="Generate, inspect, audit, and diff SPDX 2.3 Software Bill of Materials (SBOM)",
    )
    sbom_parser.set_defaults(func=lambda args: sbom_parser.print_help())

    sbom_sub = sbom_parser.add_subparsers(dest="sbom_cmd", help="SBOM operations")

    # generate
    gen_p = sbom_sub.add_parser(
        "generate",
        help="Build the emulator SPDX 2.3 SBOM via Bazel (supports release and --aosp)",
    )
    gen_p.add_argument(
        "--branch",
        default="emu-main-next",
        help="Registered source branch to build from (default: emu-main-next)",
    )
    gen_p.add_argument(
        "--source-dir",
        default=None,
        help="Explicit workspace root path (overrides --branch)",
    )
    gen_p.add_argument(
        "--build-id",
        default="developer",
        help="Build ID to stamp into the SBOM (default: developer)",
    )
    gen_p.add_argument(
        "--aosp",
        action="store_true",
        help="Build using AOSP configuration (--config=aosp --lockfile_mode=off)",
    )
    gen_p.add_argument(
        "--config",
        action="append",
        default=None,
        metavar="CONFIG",
        help="Additional Bazel --config value; repeat to pass several (e.g. --config release)",
    )
    gen_p.add_argument(
        "--out",
        "-o",
        default=None,
        help="Copy the generated .spdx.json to this file or directory",
    )
    gen_p.set_defaults(func=run_generate)

    # inspect
    insp_p = sbom_sub.add_parser(
        "inspect",
        help="Display a table of all packages, versions, licenses, suppliers, and PURLs in an SBOM",
    )
    insp_p.add_argument(
        "--sbom",
        default=None,
        help="Path to .spdx.json file (default: the SBOM built by 'sbom generate' in the workspace)",
    )
    insp_p.add_argument(
        "--branch",
        default="emu-main-next",
        help="Registered branch to locate built SBOM (default: emu-main-next)",
    )
    insp_p.add_argument(
        "--source-dir",
        default=None,
        help="Explicit workspace root path",
    )
    insp_p.add_argument(
        "--filter",
        "-f",
        default=None,
        help="Substring filter on package name, version, license, supplier, or PURL",
    )
    insp_p.set_defaults(func=run_inspect)

    # audit
    audit_p = sbom_sub.add_parser(
        "audit",
        help="Validate an SBOM against Google's SPDX 2.3 style guide (go/spdx-style-guide) and spdx_validator",
    )
    audit_p.add_argument(
        "--sbom",
        default=None,
        help="Path to .spdx.json file (default: the SBOM built by 'sbom generate' in the workspace)",
    )
    audit_p.add_argument(
        "--branch",
        default="emu-main-next",
        help="Registered branch to locate built SBOM (default: emu-main-next)",
    )
    audit_p.add_argument(
        "--source-dir",
        default=None,
        help="Explicit workspace root path",
    )
    audit_p.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any warnings are present",
    )
    audit_p.set_defaults(func=run_audit)

    # diff
    diff_p = sbom_sub.add_parser(
        "diff",
        help="Compare two SPDX 2.3 SBOM JSON files and report added, removed, or version/license-changed packages",
    )
    diff_p.add_argument("old_sbom", help="Path to baseline/old .spdx.json file")
    diff_p.add_argument("new_sbom", help="Path to new .spdx.json file")
    diff_p.set_defaults(func=run_diff)
