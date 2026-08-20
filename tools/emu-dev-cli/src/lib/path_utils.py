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

import re

def normalize_build_path(raw_path: str) -> str:
    """Strips Bazel virtual roots (execroot, bazel-out, external) from a file path."""
    clean_path = raw_path.replace("\\", "/")
    
    clean_path = re.sub(r"^(?:.*/)?bazel-out/[^/]+/bin/", "", clean_path)
    clean_path = re.sub(r"^(?:.*/)?execroot/[^/]+/(?:_main/)?", "", clean_path)
    clean_path = re.sub(r"^external/[^/]+/", "", clean_path)
    
    return clean_path
