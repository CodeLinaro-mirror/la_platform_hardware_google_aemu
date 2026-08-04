import os
import glob
import importlib
import sys


def get_target_modules():
    """
    Dynamically discover all target plugins in the src/targets/ directory.
    Excludes __init__.py and non-python files.
    """
    targets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "targets")
    modules = []
    for filepath in sorted(glob.glob(os.path.join(targets_dir, "*.py"))):
        filename = os.path.basename(filepath)
        if filename.startswith("_") or filename == "__init__.py":
            continue
        mod_name = f"targets.{filename[:-3]}"
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "register_parser"):
                modules.append((filename[:-3], mod))
        except Exception as e:
            print(f"Warning: Failed to load target plugin '{mod_name}': {e}", file=sys.stderr)
    return modules


def register_parser(subparsers, default_host):
    """
    Register the 'fetch-build' subcommand and dynamically populate
    sub-targets from all modules found in src/targets/.
    """
    fetch_parser = subparsers.add_parser(
        "fetch-build",
        help="Download and extract prebuilt artifacts from Android Build (go/ab)"
    )
    target_subparsers = fetch_parser.add_subparsers(
        dest="target",
        help="Available build target plugins in src/targets/"
    )

    target_modules = get_target_modules()
    for name, module in target_modules:
        module.register_parser(target_subparsers, default_host)

    def handle_fetch(args):
        if not getattr(args, "target", None):
            fetch_parser.print_help()
            sys.exit(0)

    fetch_parser.set_defaults(func=handle_fetch)
