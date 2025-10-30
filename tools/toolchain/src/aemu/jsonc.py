# -*- coding: utf-8 -*-
# Copyright 2023 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import re


def load(fp):
    """
    Loads a JSON with comments (JSONC) file, stripping comments before parsing.
    """
    content = fp.read()

    # Remove multi-line comments /* ... */
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)

    # Remove single-line comments // ...
    lines = content.splitlines()
    stripped_lines = [re.sub(r"\s+//.*", "", line) for line in lines]

    return json.loads("\n".join(stripped_lines))
