import os
import sys
from commands.source_directory import get_source_directory
from lib.output import print_result

RELATIVE_DOC_PATH = "third_party/adt-infra/goldfish_test/xts/verifier/README.md"


def register_parser(subparsers):
    docs_parser = subparsers.add_parser(
        "docs",
        help="Access and query documentation for Android Emulator tools and frameworks"
    )
    docs_parser.set_defaults(func=lambda args: docs_parser.print_help() or sys.exit(0))

    docs_subparsers = docs_parser.add_subparsers(dest="docs_cmd", help="Available doc targets")

    # cts-verifier-automation subcommand
    verifier_parser = docs_subparsers.add_parser(
        "cts-verifier-automation",
        help="Outputs the full path to CtsVerifier.md documentation"
    )
    verifier_parser.set_defaults(func=run_cts_verifier_automation_docs)


def run_cts_verifier_automation_docs(args):
    json_mode = getattr(args, "json", False)

    # 1. Look up source-directory for emu-main-next
    source_dir = get_source_directory("emu-main-next")

    # Fallback to current working directory or /work/emu-main-next if not set in config
    if not source_dir:
        pwd = os.getcwd()
        if os.path.exists(os.path.join(pwd, RELATIVE_DOC_PATH)):
            source_dir = pwd
        elif os.path.exists(os.path.join("/work/emu-main-next", RELATIVE_DOC_PATH)):
            source_dir = "/work/emu-main-next"

    if not source_dir:
        print_result({
            "status": "error",
            "action": "docs cts-verifier-automation",
            "error_message": "No local source directory configured for branch 'emu-main-next'. Use 'emu-dev-cli source-directory set emu-main-next <path>' first.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    full_doc_path = os.path.abspath(os.path.join(source_dir, RELATIVE_DOC_PATH))

    if not os.path.exists(full_doc_path):
        print_result({
            "status": "error",
            "action": "docs cts-verifier-automation",
            "source_directory": source_dir,
            "doc_path": full_doc_path,
            "error_message": f"Documentation file not found at: {full_doc_path}",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    if json_mode:
        print_result({
            "status": "success",
            "action": "docs cts-verifier-automation",
            "source_directory": source_dir,
            "doc_path": full_doc_path,
            "exists": True
        }, json_mode=True)
    else:
        print(full_doc_path)
