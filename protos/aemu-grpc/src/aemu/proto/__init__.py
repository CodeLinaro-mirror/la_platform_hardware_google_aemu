# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bridges Bazel `py_proto_library` and `py_grpc_library` modules into `aemu.proto`."""

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

_PREFIX = __name__ + "."


class _AemuProtoAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Maps `aemu.proto.<name>_pb2[_grpc]` imports to top-level Bazel proto modules."""

    def find_spec(self, fullname: str, path=None, target=None):
        if not fullname.startswith(_PREFIX):
            return None
        short_name = fullname[len(_PREFIX) :]
        if "." in short_name or not (
            short_name.endswith("_pb2") or short_name.endswith("_pb2_grpc")
        ):
            return None
        if (
            short_name not in sys.modules
            and importlib.machinery.PathFinder.find_spec(short_name) is None
        ):
            return None
        return importlib.machinery.ModuleSpec(fullname, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module: ModuleType) -> None:
        short_name = module.__name__[len(_PREFIX) :]
        sys.modules[module.__name__] = importlib.import_module(short_name)


if not any(isinstance(f, _AemuProtoAliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AemuProtoAliasFinder())


def __getattr__(name: str) -> ModuleType:
    if name.startswith("_"):
        raise AttributeError(name)
    try:
        mod = importlib.import_module(_PREFIX + name)
        globals()[name] = mod
        return mod
    except ModuleNotFoundError as exc:
        if exc.name in (_PREFIX + name, name):
            raise AttributeError(name) from exc
        raise
