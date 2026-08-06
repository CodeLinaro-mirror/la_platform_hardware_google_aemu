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

"""Markdown structure and code block extraction utilities for emu-dev-cli."""

import re
from typing import Dict, List, Optional


def extract_yaml_block(content: str, block_header: str) -> Dict[str, str]:
    """Extracts key-value dictionary from a structured YAML or actionability block in Markdown.

    Args:
        content: Markdown string containing the block.
        block_header: Header line prefix to identify block start (e.g. 'actionability:').

    Returns:
        Dictionary mapping field keys to string values.
    """
    if block_header not in content:
        return {}

    raw_block = content.split(block_header, 1)[1].split("```", 1)[0].strip()
    lines = raw_block.split("\n")
    data: Dict[str, str] = {}

    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            clean_val = v.strip().split("#")[0].strip().strip('"').strip("'")
            data[k.strip()] = clean_val

    return data


def extract_fenced_code_blocks(content: str, lang: Optional[str] = None) -> List[str]:
    """Extracts code blocks delimited by triple backticks.

    Args:
        content: Markdown document string.
        lang: Optional language identifier filter (e.g. 'python', 'bash').

    Returns:
        List of code block contents.
    """
    if lang:
        pattern = rf"```{re.escape(lang)}\s*\n(.*?)```"
    else:
        pattern = r"```(?:\w+)?\s*\n(.*?)```"

    matches = re.findall(pattern, content, flags=re.DOTALL)
    return matches
