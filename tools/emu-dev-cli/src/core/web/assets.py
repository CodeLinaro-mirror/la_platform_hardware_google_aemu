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

"""Static asset resolution, bounded UTF-8 file previews, and sidecar HTML shell rendering."""

from __future__ import annotations

import codecs
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from core.web.security import resolve_within_roots

ASSET_VERSION_HEX_LENGTH: int = 12
DEFAULT_PREVIEW_MAX_BYTES: int = 128 * 1024
SIDECAR_PRELOAD_SCRIPT_TAG: str = '<script src="/preload.js"></script>'

SIDECAR_PRELOAD_JS: str = """(function() {
  const searchStr = (typeof window !== 'undefined' && window.location && window.location.search) || '';
  const params = typeof URLSearchParams !== 'undefined' ? new URLSearchParams(searchStr) : { get: () => '' };
  const token = params.get('token') || '';
  const conversationId = params.get('conversationId') || '';

  let currentWorkspaceUris = [];
  let hasInitialWorkspace = false;
  const workspaceListeners = new Set();
  let resolveInitialWorkspace;
  const initialWorkspacePromise = new Promise((resolve) => {
    resolveInitialWorkspace = resolve;
  });

  window.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'theme-change') {
      const vars = (event.data.payload && event.data.payload.cssVariables) || {};
      for (const [key, value] of Object.entries(vars)) {
        document.documentElement.style.setProperty(key, value);
      }
    } else if (event.data && event.data.type === 'workspace-change') {
      const payload = event.data.payload || {};
      currentWorkspaceUris = Array.isArray(payload.workspaceUris)
        ? payload.workspaceUris.slice()
        : [];
      hasInitialWorkspace = true;
      resolveInitialWorkspace();
      for (const listener of workspaceListeners) {
        try {
          listener(currentWorkspaceUris.slice());
        } catch (err) {
          console.error('workspace-change listener failed:', err);
        }
      }
    }
  });

  function secureFetch(url, options = {}) {
    let targetUrl = url;
    if (token && !/[?&]token=/.test(targetUrl)) {
      const sep = targetUrl.includes('?') ? '&' : '?';
      targetUrl = `${targetUrl}${sep}token=${encodeURIComponent(token)}`;
    }
    const headers = typeof Headers !== 'undefined' ? new Headers(options.headers || {}) : Object.assign({}, options.headers || {});
    if (typeof headers.set === 'function') {
      headers.set('Content-Type', 'application/json');
      if (token) {
        headers.set('X-Sidecar-Token', token);
      }
    }
    return fetch(targetUrl, Object.assign({}, options, { headers }));
  }

  const projectIdPromise = (async () => {
    if (!conversationId) return '';
    try {
      const res = await secureFetch('/_sidecar/get-conversation-metadata', {
        method: 'POST',
        body: JSON.stringify({ conversationId })
      });
      if (!res.ok) return '';
      const data = await res.json();
      const metaEvent = (data && data.response && (data.response.conversationMetadata || data.response.conversation_metadata)) || {};
      const meta = metaEvent.metadata || {};
      return meta.projectId || meta.project_id || '';
    } catch (e) {
      return '';
    }
  })();

  async function getWorkspaceUris() {
    if (window.parent === window) {
      return [];
    }
    await initialWorkspacePromise;
    return currentWorkspaceUris.slice();
  }

  window.sidecar = {
    conversationId,
    fetch: secureFetch,
    getWorkspaceUris,
    onWorkspaceChange(listener) {
      if (typeof listener !== 'function') return () => {};
      workspaceListeners.add(listener);
      if (hasInitialWorkspace) {
        queueMicrotask(() => {
          if (workspaceListeners.has(listener)) {
            listener(currentWorkspaceUris.slice());
          }
        });
      }
      return () => { workspaceListeners.delete(listener); };
    },
    agent: {
      async sendMessage(message, convId) {
        const resolvedProjectId = await projectIdPromise;
        const res = await secureFetch('/_sidecar/send-message', {
          method: 'POST',
          body: JSON.stringify({
            conversationId: convId || conversationId,
            message,
            projectId: resolvedProjectId
          })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      },
      async startConversation(message, title) {
        const resolvedProjectId = await projectIdPromise;
        const res = await secureFetch('/_sidecar/new-conversation', {
          method: 'POST',
          body: JSON.stringify({ message, title, projectId: resolvedProjectId })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      },
      async getConversationMetadata(convId) {
        const res = await secureFetch('/_sidecar/get-conversation-metadata', {
          method: 'POST',
          body: JSON.stringify({ conversationId: convId || conversationId })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      }
    },
    ui: {
      toggleAuxPane(request) {
        window.parent.postMessage({ type: 'toggle-aux-pane', payload: request }, '*');
      },
      toggleConversation(convId) {
        window.parent.postMessage({ type: 'toggle-conversation', payload: { conversationId: convId } }, '*');
      }
    }
  };
})();
"""


def compute_asset_version(asset_path: Path, length: int = ASSET_VERSION_HEX_LENGTH) -> str:
    """Computes a short SHA-256 hex digest of `asset_path` (`'0'` if unreadable)."""
    try:
        return hashlib.sha256(asset_path.read_bytes()).hexdigest()[:length]
    except OSError:
        return "0"


def render_html_shell(
    template_path: Path,
    *,
    inline_preload: bool = True,
    version_placeholder: str | None = None,
    version: str | None = None,
    bootstrap_placeholder: str | None = None,
    bootstrap_payload: Mapping[str, Any] | None = None,
) -> str:
    """Renders an HTML template with optional inlined `window.sidecar` JS and JSON bootstrap data."""
    html = template_path.read_text(encoding="utf-8")
    if inline_preload:
        inline_script = f"<script>\n{SIDECAR_PRELOAD_JS}\n</script>"
        html = html.replace(SIDECAR_PRELOAD_SCRIPT_TAG, inline_script)
    if version_placeholder is not None:
        effective_version = version if version is not None else compute_asset_version(template_path)
        html = html.replace(version_placeholder, json.dumps(effective_version))
    if bootstrap_placeholder is not None:
        bootstrap_json = json.dumps(dict(bootstrap_payload or {}), ensure_ascii=False).replace(
            "</", "<\\/"
        )
        html = html.replace(bootstrap_placeholder, bootstrap_json)
    return html


def read_bounded_utf8_preview(
    file_path: str | Path,
    roots: Iterable[str | Path],
    *,
    max_bytes: int = DEFAULT_PREVIEW_MAX_BYTES,
) -> dict[str, Any] | None:
    """Reads up to `max_bytes` of a UTF-8 file inside `roots` without splitting multi-byte chars.

    Returns None when `file_path` resolves outside `roots`.
    Raises `FileNotFoundError` when the resolved path is not a regular file, or `OSError` on read
    failure.
    """
    resolved = resolve_within_roots(file_path, roots)
    if resolved is None:
        return None
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    with resolved.open("rb") as f:
        head = f.read(max_bytes)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    return {
        "path": str(resolved),
        "filename": resolved.name,
        "size": resolved.stat().st_size,
        "content": decoder.decode(head, final=len(head) < max_bytes),
    }


class StaticAssetResolver:
    """Resolves static templates and confined preview files across allowed root directories."""

    def __init__(self, candidate_paths: Sequence[Path]) -> None:
        if not candidate_paths:
            raise ValueError(
                "StaticAssetResolver requires at least one candidate path. "
                "Provide one or more template file paths."
            )
        self._candidates: tuple[Path, ...] = tuple(Path(p) for p in candidate_paths)

    def resolve_first_existing(self) -> Path:
        """Returns the first candidate path that exists as a file, or the primary candidate."""
        for candidate in self._candidates:
            if candidate.is_file():
                return candidate
        return self._candidates[0]

    def version(self) -> str:
        """Returns the short SHA-256 content version of the resolved asset."""
        return compute_asset_version(self.resolve_first_existing())


__all__ = [
    "ASSET_VERSION_HEX_LENGTH",
    "DEFAULT_PREVIEW_MAX_BYTES",
    "SIDECAR_PRELOAD_JS",
    "SIDECAR_PRELOAD_SCRIPT_TAG",
    "StaticAssetResolver",
    "compute_asset_version",
    "read_bounded_utf8_preview",
    "render_html_shell",
]
