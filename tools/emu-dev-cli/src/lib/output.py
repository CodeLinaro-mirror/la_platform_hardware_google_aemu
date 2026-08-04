import json
import sys


def print_result(data: dict, json_mode: bool = False, is_error: bool = False):
    """Prints output in either JSON format or human-readable format."""
    if json_mode:
        output_str = json.dumps(data, indent=2)
        if is_error:
            print(output_str, file=sys.stderr)
        else:
            print(output_str)
    else:
        status_symbol = "❌ ERROR" if is_error else "✅ SUCCESS"
        print(f"\n{status_symbol}: {data.get('summary', data.get('action', 'done'))}")
        for key, val in data.items():
            if key in ("summary", "status"):
                continue
            print(f"  • {key}: {val}")
        print()

    if is_error:
        sys.exit(data.get("exit_code", 1))
    else:
        sys.exit(0)
