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
import tempfile
import os
import logging


class TemporaryBuildBotDirectory(tempfile.TemporaryDirectory):
    """
    A temporary directory manager that conditionally skips cleanup
    if the BUILD_CONTEXT environment variable is set. This is mainly
    to make sure we do not get into issues with Windows where files
    might be locked due to bazel, or meson.

    If BUILD_CONTEXT is set:
    - And `dir` constructor argument is None, it will prioritize using the
      directory specified by the TMPDIR environment variable. If TMPDIR is
      not set, a warning is issued, and standard temp directory selection proceeds.
    - The created directory is NOT removed when the context is exited or when the
      object is garbage collected.

    If BUILD_CONTEXT is NOT set:
    - Behaves like tempfile.TemporaryDirectory, creating the directory
      (respecting TMPDIR, TEMP, etc., if `dir` is None) and cleaning it up.
    """

    def __init__(
        self, suffix=None, prefix="tmp_buildbot_", dir=None, ignore_cleanup_errors=False
    ):
        self._build_context_active = os.environ.get("BUILD_CONTEXT") is not None
        self._cleanup_deliberately_skipped = False

        # Determine the actual directory to pass to the parent constructor.
        actual_dir_for_parent = dir

        if self._build_context_active and dir is None:
            # BUILD_CONTEXT is set and no specific 'dir' was provided.
            # Prioritize TMPDIR as per the requirement.
            tmpdir_env_path = os.environ.get("TMPDIR")
            if tmpdir_env_path:
                actual_dir_for_parent = tmpdir_env_path
                logging.info(
                    f"BUILD_CONTEXT is set and 'dir' argument was None. Using TMPDIR: '{tmpdir_env_path}' as base for the temporary directory."
                )
            else:
                # TMPDIR is not set, even though BUILD_CONTEXT is active.
                # Log a warning as this might be contrary to expectations.
                # actual_dir_for_parent remains None, so parent class will use its
                # full default search (TEMP, TMP, system defaults).
                logging.warning(
                    "BUILD_CONTEXT is set and 'dir' argument was None, but the TMPDIR environment variable is not set. "
                    "Falling back to standard temporary directory selection process (TEMP, TMP, etc.)."
                )
        elif dir is None:
            # BUILD_CONTEXT is NOT set, and dir is None.
            # Log that we're relying on default temp dir selection.
            logging.debug(
                "Using default temporary directory selection process (TMPDIR, TEMP, TMP, etc.)."
            )
            # actual_dir_for_parent is already None.

        # `super().__init__` creates the directory and registers the finalizer.
        super().__init__(suffix, prefix, actual_dir_for_parent, ignore_cleanup_errors)

        # Post-super().__init__() actions based on BUILD_CONTEXT
        if self._build_context_active:
            logging.info(
                f"BUILD_CONTEXT is set. Directory '{self.name}' will NOT be automatically cleaned up by this Python process."
            )
            # Detach the finalizer registered by the parent to prevent cleanup on GC.
            if hasattr(self, "_finalizer") and self._finalizer.alive:
                self._finalizer.detach()
                logging.debug(
                    f"Detached internal finalizer for '{self.name}' due to BUILD_CONTEXT."
                )
            self._cleanup_deliberately_skipped = True

    def cleanup(self):
        """
        Performs cleanup of the temporary directory.
        If BUILD_CONTEXT was set at initialization, this method does nothing.
        Otherwise, it calls the cleanup method of the parent class.
        """
        if self._cleanup_deliberately_skipped:
            logging.info(
                f"Cleanup for '{self.name}' skipped as per BUILD_CONTEXT setting."
            )
            return
        else:
            super().cleanup()
