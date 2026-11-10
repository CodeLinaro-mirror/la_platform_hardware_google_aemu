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

"""Turns a command's argparse arguments into an MCP tool schema, and tool arguments back into argv.

The CLI parser stays the single source of truth: a tool's input schema is read
from it, and the arguments of each call are parsed by it, so MCP calls get the
same defaults, types, choices, and validation as the command line.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import enum
from typing import Any, NoReturn

from core.errors import UsageError

_JSON_TYPES: Mapping[Any, str] = {int: "integer", float: "number"}
_EXPECTED_TYPES: Mapping[str, str] = {
    "boolean": "true or false",
    "integer": "an integer",
    "number": "a number",
    "string": "a string",
}
_SCHEMA_HINT = "tools/list shows the arguments and values each tool accepts."


def json_type_name(value: Any) -> str:
    """Returns the JSON Schema type name of a Python value decoded from JSON."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


class ToolArgumentParser(argparse.ArgumentParser):
    """An ArgumentParser that raises UsageError instead of printing usage and exiting."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message, remediation=_SCHEMA_HINT)


class ParameterKind(enum.Enum):
    """How a tool argument is written on the command line."""

    POSITIONAL = "positional"  # value
    FLAG = "flag"  # --name, for store_true and store_false
    VALUE = "value"  # --name=value
    REPEATED = "repeated"  # --name=a --name=b, for action="append"
    VALUES = "values"  # --name a b, for nargs="*" or "+"
    REMAINDER = "remainder"  # --name ..., placed last because it consumes all remaining words


def _takes_list(nargs: Any) -> bool:
    return nargs in ("*", "+", argparse.REMAINDER) or (isinstance(nargs, int) and nargs > 0)


def _is_json_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def _parameter_kind(action: argparse.Action) -> ParameterKind | None:
    """Returns how `action` is passed, or None when a tool cannot express it."""
    if action.dest == argparse.SUPPRESS or action.nargs == argparse.PARSER:
        return None  # --help and subcommand selectors.
    if not action.option_strings:
        return ParameterKind.POSITIONAL
    if action.nargs == 0:
        # Only true/false flags map to a JSON boolean; count and store_const do not.
        return ParameterKind.FLAG if isinstance(action.const, bool) else None
    if action.nargs == argparse.REMAINDER:
        return ParameterKind.REMAINDER
    # argparse has no public way to recognize an "append" action.
    if isinstance(action, argparse._AppendAction):  # pylint: disable=protected-access
        return ParameterKind.REPEATED
    return ParameterKind.VALUES if _takes_list(action.nargs) else ParameterKind.VALUE


@dataclass(frozen=True)
class ToolParameter:
    """One argparse argument, exposed as a property of a tool's input schema."""

    action: argparse.Action
    kind: ParameterKind

    @property
    def name(self) -> str:
        """The property name: the dest, or the option name for flags like --no-rebase.

        A flag's help text describes what passing it does, so `true` always means
        "pass the flag", even when argparse stores False (store_false).
        """
        if self.kind is ParameterKind.FLAG and self.action.const is False:
            return self.option.lstrip("-").replace("-", "_")
        return self.action.dest

    @property
    def default(self) -> Any:
        return False if self.kind is ParameterKind.FLAG else self.action.default

    @property
    def option(self) -> str:
        """The option string used to pass this argument, preferring the long form."""
        strings = self.action.option_strings
        return next((s for s in strings if s.startswith("--")), strings[0] if strings else "")

    @property
    def is_list(self) -> bool:
        return self.kind is ParameterKind.REPEATED or _takes_list(self.action.nargs)

    @property
    def required(self) -> bool:
        if self.kind is ParameterKind.POSITIONAL:
            return self.action.nargs not in ("?", "*", argparse.REMAINDER)
        return bool(self.action.required)

    @property
    def json_type(self) -> str:
        if self.kind is ParameterKind.FLAG:
            return "boolean"
        return _JSON_TYPES.get(self.action.type, "string")

    def schema(self, default: Any) -> dict[str, Any]:
        """Returns the JSON Schema of this argument, advertising `default` when it is JSON."""
        item: dict[str, Any] = {"type": self.json_type}
        if self.action.choices is not None:
            item["enum"] = [c if _is_json_scalar(c) else str(c) for c in self.action.choices]
        prop = {"type": "array", "items": item} if self.is_list else item
        if isinstance(self.action.help, str) and self.action.help:
            prop["description"] = self.action.help
        if default is argparse.SUPPRESS:
            return prop
        if _is_json_scalar(default):
            prop["default"] = default
        elif isinstance(default, (list, tuple)) and default and all(map(_is_json_scalar, default)):
            prop["default"] = list(default)
        return prop

    def to_argv(self, tool: str, value: Any) -> list[str]:
        """Returns the command-line words that pass `value` for this argument."""
        if self.kind is ParameterKind.FLAG:
            if not isinstance(value, bool):
                raise self._type_error(tool, value)
            return [self.option] if value else []
        words = [self._word(tool, item) for item in self._items(tool, value)]
        if self.kind is ParameterKind.POSITIONAL:
            return words
        if self.kind in (ParameterKind.VALUES, ParameterKind.REMAINDER):
            return [self.option, *words]
        # The `--name=value` form keeps values that start with '-' from reading as options.
        return [f"{self.option}={word}" for word in words]

    def _items(self, tool: str, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return [value]
        if not self.is_list:
            raise UsageError(
                f"Argument '{self.name}' of tool '{tool}' takes one {self.json_type}, "
                f"not an array ({value!r}).",
                remediation=f"Pass a single {self.json_type} value for '{self.name}'.",
            )
        return value

    def _word(self, tool: str, item: Any) -> str:
        if isinstance(item, bool) or not _is_json_scalar(item):
            raise self._type_error(tool, item)
        word = str(int(item)) if isinstance(item, float) and item.is_integer() else str(item)
        if self.kind in (ParameterKind.POSITIONAL, ParameterKind.VALUES) and word.startswith("-"):
            raise UsageError(
                f"Argument '{self.name}' of tool '{tool}' cannot start with '-' (got {word!r}), "
                "because the command line would read it as an option.",
                remediation=(
                    f"Pass a value for '{self.name}' that does not start with '-'; "
                    "options belong in their own tool arguments."
                ),
            )
        return word

    def _type_error(self, tool: str, value: Any) -> UsageError:
        expected = _EXPECTED_TYPES[self.json_type]
        return UsageError(
            f"Argument '{self.name}' of tool '{tool}' must be {expected}, "
            f"not {json_type_name(value)} {value!r}.",
            remediation=_SCHEMA_HINT,
        )


def tool_parameters(
    parser: argparse.ArgumentParser, *, hidden: Iterable[str] = ()
) -> dict[str, ToolParameter]:
    """Returns the arguments of `parser` that a tool exposes, positionals first, by name."""
    skipped = set(hidden)
    parameters: dict[str, ToolParameter] = {}
    # argparse has no public accessor for the arguments a parser has registered.
    actions = parser._actions  # pylint: disable=protected-access
    for action in sorted(actions, key=lambda a: bool(a.option_strings)):
        kind = _parameter_kind(action)
        if kind is None or action.help == argparse.SUPPRESS:
            continue
        param = ToolParameter(action, kind)
        if param.name not in skipped:
            parameters.setdefault(param.name, param)
    return parameters


def build_argv(
    tool: str, parameters: Mapping[str, ToolParameter], arguments: Mapping[str, Any]
) -> list[str]:
    """Turns tool arguments into argv: positionals in declared order, list options last."""
    unknown = sorted(set(arguments) - set(parameters))
    if unknown:
        raise UsageError(
            f"Tool '{tool}' has no argument {', '.join(map(repr, unknown))}.",
            remediation=f"Use only: {', '.join(parameters) or 'no arguments'}.",
        )
    given = {name: value for name, value in arguments.items() if value is not None}
    missing = [p.name for p in parameters.values() if p.required and p.name not in given]
    if missing:
        raise UsageError(
            f"Tool '{tool}' needs argument {', '.join(map(repr, missing))}.",
            remediation=_SCHEMA_HINT,
        )
    positionals: list[str] = []
    options: list[str] = []
    values: list[str] = []
    remainder: list[str] = []  # REMAINDER consumes everything after it, so it goes last.
    for name, param in parameters.items():
        if name not in given:
            continue
        words = param.to_argv(tool, given[name])
        if param.kind is ParameterKind.POSITIONAL:
            positionals.extend(words)
        elif param.kind is ParameterKind.VALUES:
            values.extend(words)
        elif param.kind is ParameterKind.REMAINDER:
            remainder.extend(words)
        else:
            options.extend(words)
    return positionals + options + values + remainder


def parse_tool_arguments(
    tool: str,
    parser: argparse.ArgumentParser,
    parameters: Mapping[str, ToolParameter],
    arguments: Mapping[str, Any],
) -> argparse.Namespace:
    """Parses tool arguments with the command's own parser, raising UsageError when invalid."""
    argv = build_argv(tool, parameters, arguments)
    try:
        return parser.parse_args(argv)
    except UsageError as exc:
        raise UsageError(
            f"Invalid arguments for tool '{tool}': {exc.message}.", remediation=_SCHEMA_HINT
        ) from exc
