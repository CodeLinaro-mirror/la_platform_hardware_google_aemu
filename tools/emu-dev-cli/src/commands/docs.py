import os
import sys
import argparse
from commands.source_directory import get_source_directory, find_file_in_source_directories, build_codesearch_url
from lib.output import print_result

# Add your doc targets here
DOC_TARGETS = {
    "cts-verifier-automation": ("emu-main-next", "third_party/adt-infra/goldfish_test/xts/verifier/README.md"),
    "cts-verifier-automation-development": ("emu-main-next", "third_party/adt-infra/goldfish_test/xts/verifier/DEVELOPING_CTS_VERIFIER_AUTOMATION.md"),
    "emu-dev-cli-development": ("emu-main-next", "hardware/google/aemu/tools/emu-dev-cli/docs/DEVELOPMENT.md"),
}


def resolve_doc_file_path(branch, relative_path):
    """
    Dynamically resolves the absolute path to a documentation file across:
    1. The target's specific branch source directory in ~/.android/emu-dev-cli.json
    2. All registered source directories and workspace roots
    3. Relative path traversal from __file__ location
    """
    # 1. Primary lookup: Check target branch's registered source directory
    src_dir = get_source_directory(branch)
    if src_dir:
        candidate = os.path.join(src_dir, relative_path)
        if os.path.exists(candidate):
            return candidate

    # 2. Search across all registered source directories and workspace roots
    found_path = find_file_in_source_directories(relative_path)
    if found_path:
        return found_path

    # 3. Fallback: Check relative directory structure from this script file location (__file__)
    curr = os.path.abspath(__file__)
    for _ in range(7):
        curr = os.path.dirname(curr)
        candidate = os.path.join(curr, relative_path)
        if os.path.exists(candidate):
            return candidate

    return None


def register_parser(subparsers):
    docs_parser = subparsers.add_parser(
        "docs",
        help="Access and query documentation for Android Emulator tools and frameworks"
    )
    docs_parser.add_argument(
        "--codesearch-url", "--cs-url", "--url",
        dest="codesearch_url",
        action="store_true",
        help="Outputs the Code Search web URL instead of local file path"
    )
    docs_parser.set_defaults(func=lambda args: docs_parser.print_help() or sys.exit(0))

    docs_subparsers = docs_parser.add_subparsers(dest="docs_cmd", help="Available doc targets")

    for target_name in DOC_TARGETS.keys():
        sub_parser = docs_subparsers.add_parser(target_name)
        sub_parser.add_argument(
            "--codesearch-url", "--cs-url", "--url",
            dest="codesearch_url",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Outputs the Code Search web URL instead of local file path"
        )
        sub_parser.set_defaults(func=lambda args, name=target_name: run_doc_target(name, args))


def run_doc_target(target_name, args):
    target_info = DOC_TARGETS.get(target_name)
    if not target_info:
        print_result({
            "status": "error",
            "action": f"docs {target_name}",
            "error_message": f"Unknown doc target '{target_name}'",
            "exit_code": 1
        }, is_error=True)
        sys.exit(1)

    branch, rel_path = target_info
    json_mode = getattr(args, "json", False)
    use_cs_url = getattr(args, "codesearch_url", False)

    full_doc_path = resolve_doc_file_path(branch, rel_path)
    cs_url = build_codesearch_url(rel_path, branch_name=branch)

    if json_mode:
        print_result({
            "status": "success",
            "action": f"docs {target_name}",
            "branch": branch,
            "doc_path": full_doc_path,
            "codesearch_url": cs_url,
            "exists_locally": bool(full_doc_path)
        }, json_mode=True)
    else:
        if use_cs_url or not full_doc_path:
            if not full_doc_path and not use_cs_url:
                sys.stderr.write(f"⚠️ Local file not found ('{rel_path}'). Falling back to Code Search URL:\n")
            print(cs_url)
        else:
            print(full_doc_path)


def run_cts_verifier_automation_docs(args):
    run_doc_target("cts-verifier-automation", args)


def run_emu_dev_cli_development_docs(args):
    run_doc_target("emu-dev-cli-development", args)
