  function togglePgFileDiffBlock(event, clNum, patchSet, filePath) {
    if (event && typeof event.stopPropagation === "function") event.stopPropagation();
    const ps = Number(patchSet || 1);
    const isNowCollapsed = !isPgFileCollapsed(clNum, ps, filePath);
    setPgFileCollapsed(clNum, ps, filePath, isNowCollapsed);
  }

  function onPgFileHeaderClick(event, clNum, patchSet, filePath) {
    if (event && event.target && event.target.closest("a, input, button, label, select")) {
      return;
    }
    togglePgFileDiffBlock(event, clNum, patchSet, filePath);
  }

  function syncPgReviewedFilesFromServer(clNum, patchSet, serverFiles) {
    if (!clNum || !Array.isArray(serverFiles)) return;
    const ps = Number(patchSet || 1);
    const revSet = getPgReviewedSet(clNum, ps);
    let changed = false;
    serverFiles.forEach(f => {
      const fStr = String(f || "").trim();
      if (fStr && !revSet.has(fStr)) {
        revSet.add(fStr);
        changed = true;
      }
    });
    if (changed) {
      savePgReviewedSet(clNum, ps, revSet);
      syncPgFileListReviewedDom(clNum, ps);
    }
  }

  function syncPgFileListReviewedDom(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const ps = Number(patchSet || 1);
    if (!num) return;
    const revSet = getPgReviewedSet(num, ps);

    const cbs = document.querySelectorAll(`input.pg-file-reviewed-cb[data-cl="${num}"], input.pg-diff-header-reviewed-cb[data-cl="${num}"]`);
    cbs.forEach(cb => {
      const file = cb.getAttribute("data-file");
      if (file) {
        cb.checked = revSet.has(file);
      }
    });

    const rows = document.querySelectorAll(`tr.pg-file-row[data-cl="${num}"]`);
    rows.forEach(tr => {
      const file = tr.getAttribute("data-file");
      if (file) {
        tr.classList.toggle("is-reviewed", revSet.has(file));
      }
    });

    syncPgMasterReviewedCheckbox(num, ps);
    syncPgMarkAllReviewedButton(num, ps);
    syncPgReviewedSummary(num, ps);

    if (typeof isPgHideReviewedActive === "function" && isPgHideReviewedActive(num) && typeof applyPgFileFiltersToDom === "function") {
      applyPgFileFiltersToDom(num);
    }
  }

  async function syncPgFileReviewedToServer(clNum, patchSet, filePath, isReviewed) {
    if (!clNum || !filePath) return;
    try {
      if (typeof sidecarFetch === "function") {
        await sidecarFetch("/api/gerrit/file-reviewed", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            cl: Number(clNum),
            ps: Number(patchSet || 1),
            file: String(filePath),
            reviewed: Boolean(isReviewed),
          }),
        });
      }
    } catch (_err) {
      // Non-blocking background sync failure; local state preserved
    }
  }

  async function syncPgBatchFilesReviewedToServer(clNum, patchSet, filePaths, isReviewed) {
    if (!clNum || !Array.isArray(filePaths) || !filePaths.length) return;
    try {
      if (typeof sidecarFetch === "function") {
        await sidecarFetch("/api/gerrit/batch-files-reviewed", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            cl: Number(clNum),
            ps: Number(patchSet || 1),
            files: filePaths.map(String),
            reviewed: Boolean(isReviewed),
          }),
        });
      }
    } catch (_err) {
      // Non-blocking background sync failure
    }
  }

  async function fetchPgReviewedFilesFromServer(clNum, patchSet) {
    if (!clNum) return [];
    const ps = Number(patchSet || 1);
    try {
      if (typeof sidecarFetch === "function") {
        const resp = await sidecarFetch(`/api/gerrit/reviewed-files?cl=${encodeURIComponent(clNum)}&ps=${encodeURIComponent(ps)}`);
        if (resp && resp.ok) {
          const data = await resp.json();
          if (data && Array.isArray(data.reviewed_files)) {
            syncPgReviewedFilesFromServer(clNum, ps, data.reviewed_files);
            return data.reviewed_files;
          }
        }
      }
    } catch (_err) {}
    return [];
  }

  function setPgFileReviewed(clNum, patchSet, filePath, isReviewed) {
    const opts = (arguments.length > 4 && arguments[4]) ? arguments[4] : {};
    const ps = Number(patchSet || 1);
    const pathStr = String(filePath || "");
    const revSet = getPgReviewedSet(clNum, ps);
    if (isReviewed) {
      revSet.add(pathStr);
    } else {
      revSet.delete(pathStr);
    }
    savePgReviewedSet(clNum, ps, revSet);

    const escaped = safeEscapeCss(pathStr);
    const cbs = document.querySelectorAll(`input[data-cl="${clNum}"][data-file="${escaped}"]`);
    cbs.forEach(cb => {
      cb.checked = Boolean(isReviewed);
    });

    const trEls = document.querySelectorAll(`.pg-file-row[data-file="${escaped}"]`);
    trEls.forEach(tr => {
      if (isReviewed) {
        tr.classList.add("is-reviewed");
      } else {
        tr.classList.remove("is-reviewed");
      }
    });

    const keepFocusedOpen = Boolean(
      state.codeViewerMode &&
      state.codeViewerDisplayMode === "single" &&
      state.codeViewerSelectedFileByCl &&
      state.codeViewerSelectedFileByCl[Number(clNum)] === pathStr
    );
    setPgFileCollapsed(clNum, ps, pathStr, keepFocusedOpen ? false : Boolean(isReviewed));
    syncPgReviewedSummary(clNum, ps);
    syncPgMasterReviewedCheckbox(clNum, ps);
    syncPgMarkAllReviewedButton(clNum, ps);
    if (state.codeViewerMode) {
      syncCodeViewerReviewedDom(clNum, ps, pathStr, Boolean(isReviewed));
    }

    const lastSlash = pathStr.lastIndexOf("/");
    if (lastSlash !== -1) {
      const dir = pathStr.substring(0, lastSlash + 1);
      syncPgDirReviewedCheckbox(clNum, dir);
    }

    if (typeof isPgHideReviewedActive === "function" && isPgHideReviewedActive(clNum) && typeof applyPgFileFiltersToDom === "function") {
      applyPgFileFiltersToDom(clNum);
    }

    if (!opts.skipServerSync) {
      syncPgFileReviewedToServer(clNum, ps, pathStr, Boolean(isReviewed));
    }
  }

  function onPgFileReviewedCheckboxClick(event, cbEl) {
    if (state.suppressFileRowClick) {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      return;
    }
    if (event && event.shiftKey && cbEl) {
      cbEl.__shiftClicked = true;
    }
  }

  function onPgFileReviewedLabelClick(event, labelEl) {
    if (state.suppressFileRowClick) {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      return;
    }
    if (!event || !labelEl) return;
    const cb = labelEl.querySelector('input[type="checkbox"]');
    if (cb && event.shiftKey) {
      cb.__shiftClicked = true;
    }
  }

  function clearPgFileListRangeHover() {
    document.querySelectorAll(".pg-file-row.is-range-hover, .pg-tree-file-row.is-range-hover").forEach(r => {
      r.classList.remove("is-range-hover");
    });
  }

  function onPgFileReviewedLabelHover(event, labelEl, isHover) {
    if (!isHover || !event || !event.shiftKey) {
      clearPgFileListRangeHover();
      return;
    }
    const cb = labelEl ? labelEl.querySelector('input[type="checkbox"]') : null;
    if (!cb) {
      clearPgFileListRangeHover();
      return;
    }
    const clNum = Number(cb.getAttribute("data-cl") || state.selectedClNumber || 0);
    const targetFile = cb.getAttribute("data-file") || "";
    if (!clNum || !targetFile || !state.lastReviewedCheckbox || !state.lastReviewedCheckbox.file) {
      clearPgFileListRangeHover();
      return;
    }
    if (state.lastReviewedCheckbox.file === targetFile) {
      clearPgFileListRangeHover();
      return;
    }
    const visibleFiles = (typeof getPgAllVisibleFiles === "function")
      ? getPgAllVisibleFiles(clNum)
      : getPgVisibleFileList(clNum);
    const idxA = visibleFiles.indexOf(state.lastReviewedCheckbox.file);
    const idxB = visibleFiles.indexOf(targetFile);
    if (idxA === -1 || idxB === -1) {
      clearPgFileListRangeHover();
      return;
    }
    const start = Math.min(idxA, idxB);
    const end = Math.max(idxA, idxB);
    const range = new Set(visibleFiles.slice(start, end + 1));

    const rows = document.querySelectorAll(`.pg-file-row[data-cl="${clNum}"], .pg-tree-file-row[data-cl="${clNum}"]`);
    rows.forEach(r => {
      const f = r.getAttribute("data-file");
      r.classList.toggle("is-range-hover", Boolean(f && range.has(f)));
    });
  }

  function clearPgFileListRangeSelection(showToastMsg = false) {
    const prevCl = (state.fileListRangeSelection && state.fileListRangeSelection.cl) || state.selectedClNumber;
    state.fileListRangeSelection = null;
    document.querySelectorAll(".pg-file-row.is-range-selected, .pg-tree-file-row.is-range-selected").forEach(r => {
      r.classList.remove("is-range-selected");
    });
    if (prevCl) {
      syncPgFileListRangeBar(prevCl);
    }
    if (showToastMsg && typeof showToast === "function") {
      showToast("Cleared file selection range (Esc)");
    }
  }

  function syncPgFileListRangeBar(clNum) {
    const num = Number(clNum || (state.fileListRangeSelection && state.fileListRangeSelection.cl) || state.selectedClNumber || 0);
    if (!num) return;
    const barEl = document.getElementById(`pgFileRangeBar-${num}`);
    if (!barEl) return;
    const sel = state.fileListRangeSelection;
    if (sel && sel.cl === num && Array.isArray(sel.files) && sel.files.length > 0) {
      barEl.style.display = "flex";
      const countEl = document.getElementById(`pgRangeCountLabel-${num}`);
      if (countEl) {
        countEl.textContent = `${sel.files.length} file${sel.files.length === 1 ? "" : "s"} selected`;
      }
      const hintEl = document.getElementById(`pgRangeAnchorHint-${num}`);
      if (hintEl) {
        const aBase = (sel.anchor || "").split("/").pop() || sel.anchor || "";
        const cBase = (sel.current || "").split("/").pop() || sel.current || "";
        hintEl.textContent = (aBase === cBase) ? `(${aBase})` : `(${aBase} … ${cBase})`;
      }
    } else {
      barEl.style.display = "none";
    }
  }

  function renderPgFileListRangeBarHtml(clNum) {
    const num = Number(clNum || 0);
    const sel = state.fileListRangeSelection;
    const hasRange = Boolean(sel && sel.cl === num && Array.isArray(sel.files) && sel.files.length > 0);
    const files = hasRange ? sel.files : [];
    const count = files.length;
    const aBase = hasRange ? ((sel.anchor || "").split("/").pop() || sel.anchor || "") : "";
    const cBase = hasRange ? ((sel.current || "").split("/").pop() || sel.current || "") : "";
    const hint = (aBase && cBase) ? ((aBase === cBase) ? `(${aBase})` : `(${aBase} … ${cBase})`) : "";

    return `<div class="pg-file-range-bar" id="pgFileRangeBar-${num}" style="${hasRange ? "display:flex;" : "display:none;"}" role="toolbar" aria-label="Selected file range actions">
      <div class="pg-range-bar-left">
        <span class="pg-range-icon" aria-hidden="true">☑</span>
        <span class="pg-range-count-label" id="pgRangeCountLabel-${num}">${count} file${count === 1 ? "" : "s"} selected</span>
        <span class="pg-range-anchor-hint" id="pgRangeAnchorHint-${num}">${escapeHtml(hint)}</span>
      </div>
      <div class="pg-range-bar-actions">
        <button type="button" class="pg-range-btn pg-btn-range-reviewed" onclick="executePgRangeAction(${num}, 'reviewed')" title="Mark all selected files as reviewed (r or x)">
          <span class="pg-range-btn-icon">✓</span> Mark Reviewed <span class="pg-shortcut-hint">(r)</span>
        </button>
        <button type="button" class="pg-range-btn pg-btn-range-unreviewed" onclick="executePgRangeAction(${num}, 'unreviewed')" title="Mark all selected files as unreviewed (u)">
          <span class="pg-range-btn-icon">✗</span> Mark Unreviewed <span class="pg-shortcut-hint">(u)</span>
        </button>
        <button type="button" class="pg-range-btn pg-btn-range-expand" onclick="executePgRangeAction(${num}, 'expand')" title="Expand diffs for all selected files (i)">
          <span class="pg-range-btn-icon">☷</span> Expand Diffs <span class="pg-shortcut-hint">(i)</span>
        </button>
        <button type="button" class="pg-range-btn pg-btn-range-collapse" onclick="executePgRangeAction(${num}, 'collapse')" title="Collapse diffs for all selected files (e)">
          <span class="pg-range-btn-icon">☰</span> Collapse Diffs <span class="pg-shortcut-hint">(e)</span>
        </button>
        <button type="button" class="pg-range-btn pg-btn-range-clear" onclick="clearPgFileListRangeSelection(true)" title="Clear range selection (Esc)">
          ✕ Clear <span class="pg-shortcut-hint">(Esc)</span>
        </button>
      </div>
    </div>`;
  }

  function executePgRangeAction(clNum, action, opts = {}) {
    const num = Number(clNum || (state.fileListRangeSelection && state.fileListRangeSelection.cl) || state.selectedClNumber || 0);
    if (!num) return;
    const sel = state.fileListRangeSelection;
    if (!sel || !Array.isArray(sel.files) || sel.files.length === 0) return;
    const files = sel.files;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const c = (state.changes || []).find(ch => ch.number === num);
    const ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);

    if (action === "reviewed" || action === "unreviewed" || action === "toggle-reviewed") {
      const revSet = getPgReviewedSet(num, ps);
      let targetReviewed = true;
      if (action === "reviewed") {
        targetReviewed = true;
      } else if (action === "unreviewed") {
        targetReviewed = false;
      } else {
        const allRev = files.every(f => revSet.has(f));
        targetReviewed = !allRev;
      }
      setPgFilesReviewedRange(num, ps, files, targetReviewed);
      state.lastReviewedCheckbox = {
        cl: num,
        ps,
        file: sel.current || files[files.length - 1],
        checked: targetReviewed,
      };
      if (typeof showToast === "function") {
        showToast(targetReviewed ? `Marked ${files.length} files as reviewed ✓` : `Marked ${files.length} files as unreviewed`);
      }
      if (opts.clearSelection) {
        clearPgFileListRangeSelection();
        return;
      }
    } else if (action === "expand") {
      files.forEach(f => {
        setPgFileCollapsed(num, ps, f, false);
      });
      if (typeof renderDiffPane === "function") {
        renderDiffPane({ preserveScroll: true });
      }
      if (typeof showToast === "function") {
        showToast(`Expanded diffs for ${files.length} files (i)`);
      }
      if (opts.clearSelection) {
        clearPgFileListRangeSelection();
        return;
      }
    } else if (action === "collapse") {
      files.forEach(f => {
        setPgFileCollapsed(num, ps, f, true);
      });
      if (typeof renderDiffPane === "function") {
        renderDiffPane({ preserveScroll: true });
      }
      if (typeof showToast === "function") {
        showToast(`Collapsed diffs for ${files.length} files (e)`);
      }
      if (opts.clearSelection) {
        clearPgFileListRangeSelection();
        return;
      }
    }
    syncPgFileListRangeBar(num);
  }

  function updatePgFileListRangeSelection(clNum, anchorFile, currentFile) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num || !anchorFile || !currentFile) {
      clearPgFileListRangeSelection();
      return;
    }
    const visibleFiles = (typeof getPgAllVisibleFiles === "function")
      ? getPgAllVisibleFiles(num)
      : getPgVisibleFileList(num);
    const idxA = visibleFiles.indexOf(anchorFile);
    const idxB = visibleFiles.indexOf(currentFile);
    if (idxA === -1 || idxB === -1) {
      clearPgFileListRangeSelection();
      return;
    }
    const start = Math.min(idxA, idxB);
    const end = Math.max(idxA, idxB);
    const range = visibleFiles.slice(start, end + 1);
    state.fileListRangeSelection = {
      cl: num,
      anchor: anchorFile,
      current: currentFile,
      files: range,
    };
    const rangeSet = new Set(range);
    const rows = document.querySelectorAll(`.pg-file-row[data-cl="${num}"], .pg-tree-file-row[data-cl="${num}"]`);
    rows.forEach(r => {
      const f = r.getAttribute("data-file");
      r.classList.toggle("is-range-selected", Boolean(f && rangeSet.has(f)));
    });
    syncPgFileListRangeBar(num);
  }

  function updatePgFileListDragPill(event, drag, count, startFile, currentFile) {
    if (!drag || typeof document === "undefined") return;
    let pill = document.getElementById("pgFileListDragPill");
    if (!pill) {
      pill = document.createElement("div");
      pill.id = "pgFileListDragPill";
      pill.className = "pg-file-drag-pill";
      if (document.body && typeof document.body.appendChild === "function") {
        document.body.appendChild(pill);
      }
    }
    const countLabel = count === 1 ? "1 file" : `${count} files`;
    const aBase = (startFile || "").split("/").pop() || startFile || "";
    const cBase = (currentFile || "").split("/").pop() || currentFile || "";
    const fileHint = (aBase === cBase) ? aBase : `${aBase} … ${cBase}`;
    let icon = "☑";
    let actionHint = "Release to select range";
    if (drag.fromReviewed) {
      icon = drag.initialChecked ? "☐" : "☑";
      actionHint = drag.initialChecked ? "Release to unmark reviewed" : "Release to mark reviewed";
    }
    pill.innerHTML = `<span class="pg-file-drag-icon" aria-hidden="true">${icon}</span> <strong class="pg-file-drag-count">${countLabel}</strong> <span class="pg-file-drag-files">(${escapeHtml(fileHint)})</span> <span class="pg-file-drag-hint">· ${actionHint}</span>`;
    const clientX = (event && event.clientX != null) ? event.clientX : 0;
    const clientY = (event && event.clientY != null) ? event.clientY : 0;
    if (pill.style) {
      pill.style.left = `${Math.max(12, clientX + 16)}px`;
      pill.style.top = `${Math.max(12, clientY - 14)}px`;
      pill.style.display = "inline-flex";
    }
  }

  function removePgFileListDragPill() {
    if (typeof document === "undefined") return;
    const pill = document.getElementById("pgFileListDragPill");
    if (pill) {
      if (pill.style) pill.style.display = "none";
      if (typeof pill.remove === "function") {
        pill.remove();
      } else if (pill.parentNode && typeof pill.parentNode.removeChild === "function") {
        pill.parentNode.removeChild(pill);
      }
    }
  }

  function cancelPgFileListDrag() {
    if (!state.fileListDrag) return;
    state.fileListDrag = null;
    state.suppressFileRowClick = true;
    setTimeout(() => { state.suppressFileRowClick = false; }, 150);
    if (typeof document !== "undefined" && document.body && document.body.classList) {
      document.body.classList.remove("is-file-list-dragging");
    }
    removePgFileListDragPill();
    clearPgFileListRangeHover();
    if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") {
      document.querySelectorAll(".pg-file-row.is-drag-preview, .pg-tree-file-row.is-drag-preview").forEach(r => {
        if (r && r.classList) r.classList.remove("is-drag-preview");
      });
    }
    if (typeof deselectBrowserSelection === "function") {
      deselectBrowserSelection();
    }
  }

  function initPgFileListRangeDragListeners() {
    if (typeof document === "undefined") return;
    if (document._hasFileListRangeDragListeners) return;
    document._hasFileListRangeDragListeners = true;

    if (typeof document.addEventListener !== "function") return;

    document.addEventListener("mousedown", (event) => {
      if (!event || event.button !== 0) return;
      const target = event.target;
      if (!target || typeof target.closest !== "function") return;
      const tableWrap = target.closest(".pg-file-list-table-wrap, .pg-file-list-table, .pg-tree-rows");
      if (!tableWrap) return;
      if (target.closest("button:not(.pg-btn-file-row), a, .pg-file-copy-btn, .pg-file-edit-btn, .pg-btn-subtle")) {
        return;
      }
      const rowEl = target.closest(".pg-file-row");
      if (!rowEl || (rowEl.classList && rowEl.classList.contains("pg-file-row-empty"))) return;
      const safeFile = (typeof rowEl.getAttribute === "function") ? rowEl.getAttribute("data-file") : null;
      if (!safeFile) return;
      const clNum = Number(((typeof rowEl.getAttribute === "function") && rowEl.getAttribute("data-cl")) || state.selectedClNumber || 0);
      if (!clNum) return;

      const fromReviewed = Boolean(target.closest(".col-reviewed, .pg-file-reviewed-label, .pg-file-reviewed-cb"));
      const curPs = Number(((typeof rowEl.getAttribute === "function") && rowEl.getAttribute("data-ps")) || 1);
      const initialChecked = (typeof isPgFileReviewed === "function") ? isPgFileReviewed(clNum, curPs, safeFile) : false;

      state.fileListDrag = {
        active: false,
        isMouseDown: true,
        clNum,
        startFile: safeFile,
        currentFile: safeFile,
        startX: event.clientX || 0,
        startY: event.clientY || 0,
        fromReviewed,
        initialChecked,
        patchSet: curPs,
      };
    });

    document.addEventListener("mousemove", (event) => {
      if (!state.fileListDrag || !state.fileListDrag.isMouseDown) return;
      const drag = state.fileListDrag;
      const deltaY = Math.abs((event.clientY || 0) - drag.startY);
      const deltaX = Math.abs((event.clientX || 0) - drag.startX);
      if (!drag.active && (deltaY >= 5 || deltaX >= 5)) {
        drag.active = true;
        if (typeof document !== "undefined" && document.body && document.body.classList) {
          document.body.classList.add("is-file-list-dragging");
        }
      }
      if (!drag.active) return;
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      if (typeof deselectBrowserSelection === "function") {
        deselectBrowserSelection();
      }

      let hoverRowEl = (event.target && typeof event.target.closest === "function") ? event.target.closest(".pg-file-row") : null;
      if (!hoverRowEl && typeof document !== "undefined" && typeof document.elementFromPoint === "function") {
        const elAtPoint = document.elementFromPoint(event.clientX, event.clientY);
        hoverRowEl = (elAtPoint && typeof elAtPoint.closest === "function") ? elAtPoint.closest(".pg-file-row") : null;
      }

      if (hoverRowEl && typeof hoverRowEl.getAttribute === "function") {
        const hoverFile = hoverRowEl.getAttribute("data-file");
        if (hoverFile) {
          drag.currentFile = hoverFile;
        }
      }

      const visibleFiles = (typeof getPgAllVisibleFiles === "function")
        ? getPgAllVisibleFiles(drag.clNum)
        : (typeof getPgVisibleFileList === "function" ? getPgVisibleFileList(drag.clNum) : []);
      const idxA = visibleFiles.indexOf(drag.startFile);
      const idxB = visibleFiles.indexOf(drag.currentFile);
      if (idxA !== -1 && idxB !== -1) {
        const start = Math.min(idxA, idxB);
        const end = Math.max(idxA, idxB);
        const range = visibleFiles.slice(start, end + 1);
        const rangeSet = new Set(range);

        if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") {
          const rows = document.querySelectorAll(`.pg-file-row[data-cl="${drag.clNum}"], .pg-tree-file-row[data-cl="${drag.clNum}"]`);
          rows.forEach(r => {
            if (!r || typeof r.getAttribute !== "function" || !r.classList) return;
            const f = r.getAttribute("data-file");
            const inRange = Boolean(f && rangeSet.has(f));
            r.classList.toggle("is-range-hover", inRange);
            r.classList.toggle("is-drag-preview", inRange);
          });
        }

        updatePgFileListDragPill(event, drag, range.length, drag.startFile, drag.currentFile);
      }
    });

    document.addEventListener("mouseup", (event) => {
      if (!state.fileListDrag) return;
      const drag = state.fileListDrag;
      state.fileListDrag = null;
      if (typeof document !== "undefined" && document.body && document.body.classList) {
        document.body.classList.remove("is-file-list-dragging");
      }
      removePgFileListDragPill();
      clearPgFileListRangeHover();
      if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") {
        document.querySelectorAll(".pg-file-row.is-drag-preview, .pg-tree-file-row.is-drag-preview").forEach(r => {
          if (r && r.classList) r.classList.remove("is-drag-preview");
        });
      }

      if (drag.active) {
        state.suppressFileRowClick = true;
        setTimeout(() => { state.suppressFileRowClick = false; }, 150);

        const visibleFiles = (typeof getPgAllVisibleFiles === "function")
          ? getPgAllVisibleFiles(drag.clNum)
          : (typeof getPgVisibleFileList === "function" ? getPgVisibleFileList(drag.clNum) : []);
        const idxA = visibleFiles.indexOf(drag.startFile);
        const idxB = visibleFiles.indexOf(drag.currentFile);
        if (idxA !== -1 && idxB !== -1) {
          const start = Math.min(idxA, idxB);
          const end = Math.max(idxA, idxB);
          const range = visibleFiles.slice(start, end + 1);

          if (drag.fromReviewed && range.length > 1) {
            const targetReviewed = !drag.initialChecked;
            if (typeof setPgFilesReviewedRange === "function") {
              setPgFilesReviewedRange(drag.clNum, drag.patchSet, range, targetReviewed);
            }
            state.lastReviewedCheckbox = {
              cl: drag.clNum,
              ps: drag.patchSet,
              file: drag.currentFile,
              checked: targetReviewed,
            };
            clearPgFileListRangeSelection();
            if (typeof showToast === "function") {
              showToast(targetReviewed ? `Marked ${range.length} files as reviewed ✓` : `Marked ${range.length} files as unreviewed`);
            }
          } else {
            updatePgFileListRangeSelection(drag.clNum, drag.startFile, drag.currentFile);
            if (typeof showToast === "function") {
              showToast(`Selected ${range.length} files · Press 'r' for reviewed, 'u' for unreviewed, 'i' to expand (Esc to clear)`);
            }
          }
        }
      }
    });

    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
      window.addEventListener("blur", () => {
        cancelPgFileListDrag();
      });
    }
  }

  function getPgFilteredAndSortedFiles(c, rawFileList) {
    if (!Array.isArray(rawFileList) || rawFileList.length === 0) return [];
    const num = Number((c && c.number) || state.selectedClNumber || 0);
    const sortedFileList = getPgSortedFileList(c, rawFileList);
    let fileList = sortedFileList;

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const curPs = Number(selPsMeta.ps || (state.diffsByCl && state.diffsByCl[num] && state.diffsByCl[num].patch_set) || (c && c.patch_set) || 1);
    const revSet = getPgReviewedSet(num, curPs);

    const isHideRev = (typeof isPgHideReviewedActive === "function") && isPgHideReviewedActive(num);
    const isFilterMissingActive = (typeof isPgOwnerFilterActive === "function") && isPgOwnerFilterActive(num);
    const isFilterCommentsActive = (typeof isPgCommentsFilterActive === "function") && isPgCommentsFilterActive(num);
    const commentsMode = (typeof getPgCommentsFilterMode === "function") ? getPgCommentsFilterMode(num) : (isFilterCommentsActive ? "unresolved" : "");
    const activeStatus = (typeof getPgFileStatusFilter === "function") ? getPgFileStatusFilter(num) : "";
    const activeExt = (typeof getPgFileExtensionFilter === "function") ? getPgFileExtensionFilter(num) : "";
    const activeSize = (typeof getPgFileSizeFilter === "function") ? getPgFileSizeFilter(num) : "";
    const query = getPgFileQueryFilter(num);

    if (activeStatus) {
      fileList = fileList.filter(f => getPgFileStatus(f) === activeStatus);
    }
    if (activeExt) {
      fileList = fileList.filter(f => (typeof getPgFileExtension === "function") && getPgFileExtension(f.file) === activeExt);
    }
    if (activeSize) {
      fileList = fileList.filter(f => (typeof getPgFileSizeInfo === "function") && getPgFileSizeInfo(f).category === activeSize);
    }
    if (isHideRev) {
      fileList = fileList.filter(f => !revSet.has(f.file));
    }
    if (isFilterMissingActive) {
      fileList = fileList.filter(f => getPgFileOwnerStatus(c, f).status === "missing");
    }
    if (isFilterCommentsActive) {
      fileList = fileList.filter(f => {
        const stats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, f.file) : {};
        if (commentsMode === "drafts") {
          return Boolean(stats.hasDrafts);
        } else if (commentsMode === "comments") {
          return Boolean(stats.hasComments || stats.total > 0 || stats.hasDrafts);
        }
        return Boolean(stats.hasUnresolved || stats.hasDrafts);
      });
    }
    if (query) {
      fileList = fileList.filter(f => {
        const stats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, f.file) : {};
        const ownerInfo = getPgFileOwnerStatus(c, f);
        return matchesPgFileFilter(f.file, query, {
          status: getPgFileStatus(f),
          sizeCategory: (typeof getPgFileSizeInfo === "function") ? getPgFileSizeInfo(f).category : "",
          isReviewed: revSet.has(f.file),
          hasComments: Boolean(stats.hasComments || stats.total > 0 || stats.commentCount > 0),
          hasDrafts: Boolean(stats.hasDrafts),
          hasUnresolved: Boolean(stats.hasUnresolved),
          hasResolved: Boolean(stats.hasResolved),
          hasRobot: Boolean(stats.hasRobot),
          isOwnerMissing: ownerInfo.status === "missing",
          isImage: isPgImageFile(f.file) || Boolean(f.is_image),
          isBinary: Boolean(f.is_binary) || isPgBinaryFile(f.file),
        });
      });
    }
    return fileList;
  }

  function getPgVisibleFileList(clNum, opts = {}) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return [];
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return [];
    const c = (state.changes || []).find(ch => ch.number === num) || { number: num };
    const filtered = getPgFilteredAndSortedFiles(c, rawFileList);

    const includeMagic = Boolean(opts && (opts.includeMagic || opts === true));
    if (!includeMagic) {
      return filtered.map(f => f.file);
    }

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const curPs = Number(selPsMeta.ps || (bundle && bundle.patch_set) || (c && c.patch_set) || 1);
    const revSet = getPgReviewedSet(num, curPs);
    const query = getPgFileQueryFilter(num);
    const isHideReviewed = (typeof isPgHideReviewedActive === "function") && isPgHideReviewedActive(num);
    const isFilterMissingActive = (typeof isPgOwnerFilterActive === "function") && isPgOwnerFilterActive(num);
    const isFilterCommentsActive = (typeof isPgCommentsFilterActive === "function") && isPgCommentsFilterActive(num);
    const activeStatus = (typeof getPgFileStatusFilter === "function") ? getPgFileStatusFilter(num) : "";
    const activeSize = (typeof getPgFileSizeFilter === "function") ? getPgFileSizeFilter(num) : "";

    const visiblePaths = [];

    // Magic file 1: /COMMIT_MSG
    const commitMsgStats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, "/COMMIT_MSG") : {};
    const isCommitMsgRev = revSet.has("/COMMIT_MSG");
    const showCommitMsg = !isFilterMissingActive &&
      (!isFilterCommentsActive || commitMsgStats.hasUnresolved || commitMsgStats.hasDrafts) &&
      (!query || matchesPgFileFilter("/COMMIT_MSG", query, {
        status: "M",
        isReviewed: isCommitMsgRev,
        hasComments: Boolean(commitMsgStats.commentCount > 0),
        hasDrafts: Boolean(commitMsgStats.hasDrafts),
        hasUnresolved: Boolean(commitMsgStats.hasUnresolved),
      })) &&
      (!isHideReviewed || !isCommitMsgRev) &&
      (!activeStatus || activeStatus === "M") &&
      (!activeSize);
    if (showCommitMsg) {
      visiblePaths.push("/COMMIT_MSG");
    }

    // Magic file 2: /MERGE_LIST (if merge commit)
    const isMerge = (typeof isPgMergeCommit === "function") && isPgMergeCommit(c, bundle);
    if (isMerge) {
      const mergeListStats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, "/MERGE_LIST") : {};
      const isMergeListRev = revSet.has("/MERGE_LIST");
      const showMergeList = !isFilterMissingActive &&
        (!isFilterCommentsActive || mergeListStats.hasUnresolved || mergeListStats.hasDrafts) &&
        (!query || matchesPgFileFilter("/MERGE_LIST", query, {
          status: "M",
          isReviewed: isMergeListRev,
          hasComments: Boolean(mergeListStats.commentCount > 0),
          hasDrafts: Boolean(mergeListStats.hasDrafts),
          hasUnresolved: Boolean(mergeListStats.hasUnresolved),
        })) &&
        (!isHideReviewed || !isMergeListRev) &&
        (!activeStatus || activeStatus === "M") &&
        (!activeSize);
      if (showMergeList) {
        visiblePaths.push("/MERGE_LIST");
      }
    }

    filtered.forEach(f => {
      if (f.file !== "/COMMIT_MSG" && f.file !== "/MERGE_LIST") {
        visiblePaths.push(f.file);
      }
    });

    return visiblePaths;
  }

  function getPgAllVisibleFiles(clNum) {
    return getPgVisibleFileList(clNum, { includeMagic: true });
  }

  function focusPgFileRow(clNum, filePath, opts = {}) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num || !filePath) return;
    state.focusedFileByCl = state.focusedFileByCl || {};
    state.focusedFileByCl[num] = filePath;

    const rows = document.querySelectorAll(`.pg-file-row[data-cl="${num}"], .pg-tree-file-row[data-cl="${num}"]`);
    let focusedEl = null;
    rows.forEach(r => {
      const isTarget = r.getAttribute("data-file") === filePath;
      r.classList.toggle("is-focused", isTarget);
      if (isTarget) focusedEl = r;
    });

    if (focusedEl) {
      const parentDir = focusedEl.getAttribute("data-dir");
      if (parentDir) {
        const collapsedSet = getPgCollapsedDirs(num);
        if (collapsedSet.has(parentDir)) {
          togglePgDirCollapse(num, parentDir);
        }
      }
    }

    if (focusedEl && opts.scrollIntoView !== false) {
      if (typeof focusedEl.scrollIntoViewIfNeeded === "function") {
        focusedEl.scrollIntoViewIfNeeded(false);
      } else if (typeof focusedEl.scrollIntoView === "function") {
        focusedEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
    if (focusedEl && opts.focusElement) {
      try {
        focusedEl.focus();
      } catch (_) {}
    }
  }

  function navigatePgFileListCursor(clNum, dir = "next") {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const isTree = (typeof getPgFileTreeMode === "function") && getPgFileTreeMode(num);
    const collapsedDirs = isTree ? getPgCollapsedDirs(num) : null;
    let visibleFiles = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : getPgVisibleFileList(num);
    if (isTree && collapsedDirs && collapsedDirs.size > 0) {
      visibleFiles = visibleFiles.filter(f => {
        for (const d of collapsedDirs) {
          if (d && f.startsWith(d)) return false;
        }
        return true;
      });
    }
    if (!visibleFiles.length) return;

    const curFocused = (state.focusedFileByCl && state.focusedFileByCl[num]) ||
                       (state.activeFileByCl && state.activeFileByCl[num]) ||
                       visibleFiles[0];
    let curIdx = visibleFiles.indexOf(curFocused);
    if (curIdx === -1) curIdx = 0;

    let targetIdx = curIdx;
    if (dir === "next-unreviewed" || dir === "prev-unreviewed") {
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
      const c = (state.changes || []).find(ch => ch.number === num);
      const ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);
      const revSet = getPgReviewedSet(num, ps);

      if (dir === "next-unreviewed") {
        let found = -1;
        for (let i = curIdx + 1; i < visibleFiles.length; i++) {
          if (!revSet.has(visibleFiles[i])) {
            found = i;
            break;
          }
        }
        if (found !== -1) {
          targetIdx = found;
        } else {
          const hasAnyUnreviewed = visibleFiles.some(f => !revSet.has(f));
          if (!hasAnyUnreviewed) {
            if (typeof showToast === "function") showToast("All files in this change are marked as reviewed! ✓");
          } else {
            if (typeof showToast === "function") showToast("At last unreviewed file in file list (Alt+K for previous)");
          }
          return;
        }
      } else { // prev-unreviewed
        let found = -1;
        for (let i = curIdx - 1; i >= 0; i--) {
          if (!revSet.has(visibleFiles[i])) {
            found = i;
            break;
          }
        }
        if (found !== -1) {
          targetIdx = found;
        } else {
          const hasAnyUnreviewed = visibleFiles.some(f => !revSet.has(f));
          if (!hasAnyUnreviewed) {
            if (typeof showToast === "function") showToast("All files in this change are marked as reviewed! ✓");
          } else {
            if (typeof showToast === "function") showToast("At first unreviewed file in file list (Alt+J for next)");
          }
          return;
        }
      }
    } else if (dir === "next") {
      targetIdx = Math.min(visibleFiles.length - 1, curIdx + 1);
    } else if (dir === "prev") {
      targetIdx = Math.max(0, curIdx - 1);
    }

    const targetFile = visibleFiles[targetIdx];
    if (targetFile) {
      focusPgFileRow(num, targetFile, { scrollIntoView: true });
      updatePgActiveFile(num, targetFile);
      if (dir === "next-unreviewed" || dir === "prev-unreviewed") {
        const baseName = targetFile.split("/").pop() || targetFile;
        if (typeof showToast === "function") showToast(`Focused unreviewed file: ${baseName} (Alt+J / Alt+K)`);
      }
    }
  }

  function togglePgFocusedFileReviewed(clNum, opts = {}) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const visibleFiles = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : getPgVisibleFileList(num);
    const curFile = (state.focusedFileByCl && state.focusedFileByCl[num]) ||
                    (state.activeFileByCl && state.activeFileByCl[num]) ||
                    visibleFiles[0];
    if (!curFile) return;

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const c = (state.changes || []).find(ch => ch.number === num);
    const ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);

    const isRev = isPgFileReviewed(num, ps, curFile);
    const nextRev = !isRev;
    setPgFileReviewed(num, ps, curFile, nextRev);
    state.lastReviewedCheckbox = { cl: num, ps, file: curFile, checked: nextRev };

    if (nextRev && typeof isPgHideReviewedActive === "function" && isPgHideReviewedActive(num)) {
      const idx = visibleFiles.indexOf(curFile);
      const remaining = visibleFiles.filter(f => f !== curFile);
      const nextFile = remaining[idx] || remaining[idx - 1] || remaining[0] || "";
      if (nextFile) {
        focusPgFileRow(num, nextFile, { scrollIntoView: true, focusElement: true });
      }
    } else {
      focusPgFileRow(num, curFile, { scrollIntoView: true, focusElement: true });
    }

    syncPgMasterReviewedCheckbox(num, ps);
    syncPgMarkAllReviewedButton(num, ps);

    if (opts.advance && nextRev && typeof navigatePgFileListCursor === "function") {
      navigatePgFileListCursor(num, "next");
    }

    if (typeof showToast === "function") {
      const shortName = curFile.split("/").pop() || curFile;
      const keyLabel = opts.keyLabel || "r";
      showToast(nextRev ? `Marked ${shortName} as reviewed ✓ (${keyLabel})` : `Marked ${shortName} as unreviewed (${keyLabel})`);
    }
  }

  function openFocusedPgFileDiff(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const visibleFiles = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : getPgVisibleFileList(num);
    const curFile = (state.focusedFileByCl && state.focusedFileByCl[num]) ||
                    (state.activeFileByCl && state.activeFileByCl[num]) ||
                    visibleFiles[0];
    if (!curFile) return;

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const c = (state.changes || []).find(ch => ch.number === num);
    const ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);

    setPgFileCollapsed(num, ps, curFile, false);
    scrollToFileDiff(num, ps, curFile);
  }

  function expandPgUnreviewedFileDiffs(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const bundle = state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return;

    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || (bundle && bundle.patch_set) || 1);
    const revSet = getPgReviewedSet(num, ps);

    let unreviewedCount = 0;
    rawFileList.forEach(f => {
      const isRev = revSet.has(f.file);
      if (!isRev) {
        setPgFileCollapsed(num, ps, f.file, false);
        unreviewedCount++;
      } else {
        setPgFileCollapsed(num, ps, f.file, true);
      }
    });

    syncPgExpandAllButtonState(num, ps);
    if (typeof showToast === "function") {
      showToast(`Expanded ${unreviewedCount} unreviewed diffs (${rawFileList.length - unreviewedCount} reviewed collapsed)`);
    }
  }

  function expandPgCommentsFileDiffs(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return;

    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || (bundle && bundle.patch_set) || 1);

    let expandedCount = 0;
    rawFileList.forEach(f => {
      const stats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, f.file) : {};
      const hasComments = Boolean(stats.hasComments || stats.hasDrafts || stats.hasUnresolved || (stats.total > 0));
      if (hasComments) {
        setPgFileCollapsed(num, ps, f.file, false);
        expandedCount++;
      } else {
        setPgFileCollapsed(num, ps, f.file, true);
      }
    });

    syncPgExpandAllButtonState(num, ps);
    if (typeof showToast === "function") {
      showToast(`Expanded ${expandedCount} commented file diff${expandedCount === 1 ? "" : "s"} (${rawFileList.length - expandedCount} without comments collapsed)`);
    }
  }

  function getPgUnreviewedFiles(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return [];
    const bundle = state.diffsByCl && state.diffsByCl[num];
    let fileList = [];
    if (bundle && Array.isArray(bundle.files) && bundle.files.length > 0) {
      fileList = bundle.files.map(f => (typeof f === "object" && f.file) ? f.file : String(f));
    } else if (typeof getPgAllVisibleFiles === "function") {
      fileList = getPgAllVisibleFiles(num);
    } else if (typeof getPgVisibleFileList === "function") {
      fileList = getPgVisibleFileList(num);
    }
    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || (bundle && bundle.patch_set) || 1);
    const revSet = getPgReviewedSet(num, ps);
    return fileList.filter(filePath => !revSet.has(filePath));
  }

  function executePgUnreviewedFileJump(num, ps, targetFile, unreviewed) {
    if (!targetFile) return false;
    if (typeof scrollToFileDiff === "function") {
      scrollToFileDiff(num, ps, targetFile);
    }
    if (typeof focusPgFileRow === "function") {
      focusPgFileRow(num, targetFile, { scrollIntoView: true });
    }

    const baseName = targetFile.split("/").pop() || targetFile;
    const targetIdx = unreviewed.indexOf(targetFile);
    const remaining = (targetIdx !== -1) ? (unreviewed.length - targetIdx) : unreviewed.length;
    if (typeof showToast === "function") {
      showToast(`Jumped to unreviewed file: ${baseName} (${remaining} unreviewed remaining) (Alt+])`);
    }
    return true;
  }

  function jumpToFirstUnreviewedFile(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return false;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const c = (state.changes || []).find(ch => ch.number === num);
    const ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);
    const unreviewed = getPgUnreviewedFiles(num, ps);

    if (!unreviewed.length) {
      if (typeof showToast === "function") showToast("All files in this change are marked as reviewed! ✓");
      return false;
    }
    return executePgUnreviewedFileJump(num, ps, unreviewed[0], unreviewed);
  }

  function navigatePgUnreviewedFileDiff(dir = "next", fromFile = null, fromCl = null, options = {}) {
    const num = Number(fromCl || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return false;
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const c = (state.changes || []).find(ch => ch.number === num);
    const ps = Number(selPsMeta.ps || (bundle && bundle.patch_set) || (c && c.patch_set) || 1);
    const unreviewed = getPgUnreviewedFiles(num, ps);

    if (!unreviewed.length) {
      if (typeof showToast === "function") {
        showToast("All files in this change are marked as reviewed! ✓");
      }
      return false;
    }

    if (options && options.forceTargetFile && unreviewed.includes(options.forceTargetFile)) {
      return executePgUnreviewedFileJump(num, ps, options.forceTargetFile, unreviewed);
    }

    let curFile = fromFile;
    if (!curFile) {
      const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
      if (activeBlock) {
        curFile = activeBlock.getAttribute("data-file");
      }
    }
    if (!curFile) {
      curFile = (state.activeFileByCl && state.activeFileByCl[num]) ||
                (state.focusedFileByCl && state.focusedFileByCl[num]) ||
                null;
    }

    let allFiles = [];
    if (bundle && Array.isArray(bundle.files) && bundle.files.length > 0) {
      allFiles = bundle.files.map(f => (typeof f === "object" && f.file) ? f.file : String(f));
    } else if (typeof getPgAllVisibleFiles === "function") {
      allFiles = getPgAllVisibleFiles(num);
    } else if (typeof getPgVisibleFileList === "function") {
      allFiles = getPgVisibleFileList(num);
    }

    let targetFile = null;
    let targetIdxInUnreviewed = -1;

    if (!curFile) {
      targetFile = unreviewed[0];
      targetIdxInUnreviewed = 0;
    } else {
      const curIdxInAll = allFiles.indexOf(curFile);
      const curIdxInUnrev = unreviewed.indexOf(curFile);

      if (dir === "next") {
        if (curIdxInUnrev !== -1) {
          if (curIdxInUnrev >= unreviewed.length - 1) {
            const base = curFile.split("/").pop() || curFile;
            if (typeof showToast === "function") {
              showToast(`At last unreviewed file: ${base} (Alt+[ for previous unreviewed)`);
            }
            return false;
          }
          targetIdxInUnreviewed = curIdxInUnrev + 1;
          targetFile = unreviewed[targetIdxInUnreviewed];
        } else {
          for (let i = curIdxInAll + 1; i < allFiles.length; i++) {
            if (unreviewed.includes(allFiles[i])) {
              targetFile = allFiles[i];
              targetIdxInUnreviewed = unreviewed.indexOf(targetFile);
              break;
            }
          }
          if (!targetFile) {
            const base = curFile.split("/").pop() || curFile;
            if (typeof showToast === "function") {
              showToast(`No more unreviewed files after ${base} (Alt+[ for previous)`);
            }
            return false;
          }
        }
      } else { // dir === "prev"
        if (curIdxInUnrev !== -1) {
          if (curIdxInUnrev <= 0) {
            const base = curFile.split("/").pop() || curFile;
            if (typeof showToast === "function") {
              showToast(`At first unreviewed file: ${base} (Alt+] for next unreviewed)`);
            }
            return false;
          }
          targetIdxInUnreviewed = curIdxInUnrev - 1;
          targetFile = unreviewed[targetIdxInUnreviewed];
        } else {
          for (let i = curIdxInAll - 1; i >= 0; i--) {
            if (unreviewed.includes(allFiles[i])) {
              targetFile = allFiles[i];
              targetIdxInUnreviewed = unreviewed.indexOf(targetFile);
              break;
            }
          }
          if (!targetFile) {
            const base = curFile.split("/").pop() || curFile;
            if (typeof showToast === "function") {
              showToast(`No unreviewed files before ${base} (Alt+] for next)`);
            }
            return false;
          }
        }
      }
    }

    if (targetFile) {
      return executePgUnreviewedFileJump(num, ps, targetFile, unreviewed);
    }
    return false;
  }

  function setPgFilesReviewedRange(clNum, patchSet, files, isReviewed) {
    const opts = (arguments.length > 4 && arguments[4]) ? arguments[4] : {};
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num || !Array.isArray(files) || files.length === 0) return;
    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || 1);
    const revSet = getPgReviewedSet(num, ps);

    const touchedDirs = new Set();
    files.forEach(filePath => {
      const pathStr = String(filePath || "");
      if (!pathStr) return;
      if (isReviewed) {
        revSet.add(pathStr);
      } else {
        revSet.delete(pathStr);
      }
      setPgFileCollapsed(num, ps, pathStr, Boolean(isReviewed));

      const escaped = safeEscapeCss(pathStr);
      const cbs = document.querySelectorAll(`input[data-cl="${num}"][data-file="${escaped}"]`);
      cbs.forEach(cb => {
        cb.checked = Boolean(isReviewed);
      });

      const trEls = document.querySelectorAll(`.pg-file-row[data-file="${escaped}"], .pg-tree-file-row[data-file="${escaped}"]`);
      trEls.forEach(tr => {
        if (isReviewed) {
          tr.classList.add("is-reviewed");
        } else {
          tr.classList.remove("is-reviewed");
        }
      });

      const diffBlocks = document.querySelectorAll(`.pg-file-diff-block[data-cl="${num}"][data-file="${escaped}"]`);
      diffBlocks.forEach(block => {
        const bodyEl = block.querySelector(".pg-file-diff-body") || block.querySelector(".code-table");
        if (isReviewed) {
          block.classList.add("is-collapsed");
          if (bodyEl) bodyEl.style.display = "none";
        } else {
          block.classList.remove("is-collapsed");
          if (bodyEl) bodyEl.style.display = "";
        }
      });

      const jumpItems = document.querySelectorAll(`.pg-file-jump-item[data-file="${escaped}"]`);
      jumpItems.forEach(item => {
        if (isReviewed) {
          item.classList.add("is-reviewed");
          if (!item.querySelector(".pg-file-jump-reviewed")) {
            const checkEl = document.createElement("span");
            checkEl.className = "pg-file-jump-reviewed";
            checkEl.title = "Reviewed";
            checkEl.textContent = "✓";
            const deltaEl = item.querySelector(".pg-file-jump-delta");
            if (deltaEl) item.insertBefore(checkEl, deltaEl);
            else item.appendChild(checkEl);
          }
        } else {
          item.classList.remove("is-reviewed");
          const checkEl = item.querySelector(".pg-file-jump-reviewed");
          if (checkEl) checkEl.remove();
        }
      });

      const lastSlash = pathStr.lastIndexOf("/");
      if (lastSlash !== -1) {
        touchedDirs.add(pathStr.substring(0, lastSlash + 1));
      }
    });

    savePgReviewedSet(num, ps, revSet);
    syncPgReviewedSummary(num, ps);
    syncPgMasterReviewedCheckbox(num, ps);
    syncPgMarkAllReviewedButton(num, ps);
    syncPgExpandAllButtonState(num, ps);

    if (!opts.skipServerSync && files.length > 0) {
      syncPgBatchFilesReviewedToServer(num, ps, files, Boolean(isReviewed));
    }

    touchedDirs.forEach(dir => {
      syncPgDirReviewedCheckbox(num, dir);
    });

    if (typeof isPgHideReviewedActive === "function" && isPgHideReviewedActive(num) && typeof applyPgFileFiltersToDom === "function") {
      applyPgFileFiltersToDom(num);
    }
  }

  function onPgFileReviewedCheckboxChanged(cbEl, event) {
    if (state.suppressFileRowClick) {
      return;
    }
    const clNum = Number(cbEl.getAttribute("data-cl"));
    const patchSet = Number(cbEl.getAttribute("data-ps") || 1);
    const filePath = cbEl.getAttribute("data-file") || "";
    const isReviewed = Boolean(cbEl.checked);
    const evt = event || window.event;
    const isShift = Boolean((evt && evt.shiftKey) || (cbEl && cbEl.__shiftClicked));
    if (cbEl) delete cbEl.__shiftClicked;

    focusPgFileRow(clNum, filePath, { scrollIntoView: false });

    if (isShift && state.lastReviewedCheckbox &&
        Number(state.lastReviewedCheckbox.cl) === clNum &&
        Number(state.lastReviewedCheckbox.ps) === patchSet &&
        state.lastReviewedCheckbox.file &&
        state.lastReviewedCheckbox.file !== filePath) {

      const visibleFiles = (typeof getPgAllVisibleFiles === "function")
        ? getPgAllVisibleFiles(clNum)
        : getPgVisibleFileList(clNum);
      const idxPrev = visibleFiles.indexOf(state.lastReviewedCheckbox.file);
      const idxCur = visibleFiles.indexOf(filePath);

      if (idxPrev !== -1 && idxCur !== -1) {
        const start = Math.min(idxPrev, idxCur);
        const end = Math.max(idxPrev, idxCur);
        const range = visibleFiles.slice(start, end + 1);

        setPgFilesReviewedRange(clNum, patchSet, range, isReviewed);

        state.lastReviewedCheckbox = {
          cl: clNum,
          ps: patchSet,
          file: filePath,
          checked: isReviewed,
        };

        clearPgFileListRangeSelection();

        if (typeof showToast === "function") {
          showToast(
            isReviewed
              ? `Marked ${range.length} files as reviewed ✓`
              : `Marked ${range.length} files as unreviewed`
          );
        }
        return;
      }
    }

    setPgFileReviewed(clNum, patchSet, filePath, isReviewed);
    state.lastReviewedCheckbox = {
      cl: clNum,
      ps: patchSet,
      file: filePath,
      checked: isReviewed,
    };
    syncPgMasterReviewedCheckbox(clNum, patchSet);
    syncPgMarkAllReviewedButton(clNum, patchSet);
    const dir = cbEl.getAttribute("data-dir");
    if (dir) {
      syncPgDirReviewedCheckbox(clNum, dir);
    }
  }

  function markAllPgFilesReviewed(clNum, patchSet, shouldReview, filteredOnly = true) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const bundle = state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return;

    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || (bundle && bundle.patch_set) || 1);
    const revSet = getPgReviewedSet(num, ps);
    const query = getPgFileQueryFilter(num);
    const isOwnerFilter = isPgOwnerFilterActive(num);
    const c = (state.changes || []).find(ch => ch.number === num);

    let targetFiles = rawFileList;
    if (filteredOnly && (query || isOwnerFilter)) {
      if (isOwnerFilter && c) {
        targetFiles = targetFiles.filter(f => getPgFileOwnerStatus(c, f).status === "missing");
      }
      if (query) {
        targetFiles = targetFiles.filter(f => matchesPgFileFilter(f.file, query));
      }
    }
    if (!targetFiles.length) return;

    const targetPaths = targetFiles.map(f => f.file);
    const isFiltered = Boolean(query || isOwnerFilter);

    let nextReviewState = shouldReview;
    if (typeof nextReviewState !== "boolean") {
      const allTargetReviewed = targetPaths.every(p => revSet.has(p));
      nextReviewState = !allTargetReviewed;
    }

    setPgFilesReviewedRange(num, ps, targetPaths, nextReviewState);

    if (typeof showToast === "function") {
      const count = targetPaths.length;
      if (nextReviewState) {
        showToast(isFiltered ? `✓ Marked ${count} filtered files as reviewed` : `✓ Marked all ${count} files as reviewed`);
      } else {
        showToast(isFiltered ? `Marked ${count} filtered files as unreviewed` : "Marked all files as unreviewed");
      }
    }
  }

  function togglePgMarkAllReviewed(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const curPs = Number(selPsMeta.ps || (state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    markAllPgFilesReviewed(num, curPs);
  }

  function onPgMasterReviewedCheckboxChanged(cbEl) {
    if (!cbEl) return;
    const clNum = Number(cbEl.getAttribute("data-cl"));
    const patchSet = Number(cbEl.getAttribute("data-ps") || 1);
    const shouldReview = Boolean(cbEl.checked);
    markAllPgFilesReviewed(clNum, patchSet, shouldReview);
  }

  function onPgReviewedHeaderClick(event, clNum) {
    if (event && event.target && event.target.closest(".pg-master-reviewed-label")) {
      return;
    }
    togglePgFileSort(clNum, "reviewed");
  }

  function syncPgMasterReviewedCheckbox(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const ps = Number(patchSet || 1);
    const cb = document.getElementById(`pgMasterReviewedCb-${num}`);
    if (!cb) return;

    const bundle = state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) {
      const mSub = cb._lastMasterRevState;
      if (mSub && mSub.num === num && mSub.ps === ps && mSub.total === 0 && mSub.revCount === -1) {
        return;
      }
      cb._lastMasterRevState = { num, ps, total: 0, revCount: -1 };
      cb.checked = false;
      cb.indeterminate = false;
      return;
    }
    const revSet = getPgReviewedSet(num, ps);
    const query = getPgFileQueryFilter(num);
    const isOwnerFilter = isPgOwnerFilterActive(num);
    const c = isOwnerFilter ? findPgChangeByNumber(num) : null;

    let activeFiles = rawFileList;
    if (isOwnerFilter && c) {
      activeFiles = activeFiles.filter(f => getPgFileOwnerStatus(c, f).status === "missing");
    }
    if (query) {
      activeFiles = activeFiles.filter(f => matchesPgFileFilter(f.file, query));
    }

    const total = activeFiles.length;
    let reviewedCount = 0;
    if (revSet.size > 0) {
      for (let i = 0; i < total; i++) {
        if (revSet.has(activeFiles[i].file)) reviewedCount++;
      }
    }

    const mSub = cb._lastMasterRevState;
    if (mSub && mSub.num === num && mSub.ps === ps && mSub.total === total && mSub.revCount === reviewedCount) {
      return;
    }
    cb._lastMasterRevState = { num, ps, total, revCount: reviewedCount };

    if (total === 0) {
      cb.checked = false;
      cb.indeterminate = false;
      cb.title = "No files to mark as reviewed";
    } else if (reviewedCount === total) {
      cb.checked = true;
      cb.indeterminate = false;
      cb.title = `Mark all ${total} files as unreviewed (Shift+V)`;
    } else if (reviewedCount > 0) {
      cb.checked = false;
      cb.indeterminate = true;
      cb.title = `Mark all ${total} files as reviewed (${reviewedCount} already reviewed) (Shift+V)`;
    } else {
      cb.checked = false;
      cb.indeterminate = false;
      cb.title = `Mark all ${total} files as reviewed (Shift+V)`;
    }
  }

  function syncPgMarkAllReviewedButton(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const ps = Number(patchSet || 1);
    const btn = document.getElementById(`btnMarkAllReviewed-${num}`);
    if (!btn) return;

    const bundle = state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    const revSet = getPgReviewedSet(num, ps);
    const query = getPgFileQueryFilter(num);
    const isOwnerFilter = isPgOwnerFilterActive(num);
    const c = isOwnerFilter ? findPgChangeByNumber(num) : null;

    let activeFiles = rawFileList;
    if (isOwnerFilter && c) {
      activeFiles = activeFiles.filter(f => getPgFileOwnerStatus(c, f).status === "missing");
    }
    if (query) {
      activeFiles = activeFiles.filter(f => matchesPgFileFilter(f.file, query));
    }

    const isFiltered = Boolean(query || isOwnerFilter);
    const total = activeFiles.length;
    let reviewedCount = 0;
    if (revSet.size > 0) {
      for (let i = 0; i < total; i++) {
        if (revSet.has(activeFiles[i].file)) reviewedCount++;
      }
    }
    const isAll = reviewedCount === total && total > 0;
    const bSub = btn._lastMarkAllState;
    if (bSub && bSub.num === num && bSub.ps === ps && bSub.total === total && bSub.isAll === isAll && bSub.isFiltered === isFiltered) {
      return;
    }
    btn._lastMarkAllState = { num, ps, total, isAll, isFiltered };

    if (isAll) {
      btn.classList.add("is-all-reviewed");
      btn.textContent = isFiltered ? `✕ Unmark filtered (${total})` : "✕ Mark all unreviewed";
      btn.title = `Unmark all ${total} files as reviewed (Shift+V)`;
    } else {
      btn.classList.remove("is-all-reviewed");
      btn.textContent = isFiltered ? `✓ Mark filtered reviewed (${total})` : "✓ Mark all reviewed";
      btn.title = `Mark all ${total} files as reviewed (Shift+V)`;
    }
  }

  function syncPgReviewedSummary(clNum, patchSet) {
    const ps = Number(patchSet || 1);
    const bundle = state.diffsByCl[clNum];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return;
    const revSet = getPgReviewedSet(clNum, ps);
    const query = getPgFileQueryFilter(clNum);
    const isOwnerFilter = isPgOwnerFilterActive(clNum);
    const c = (state.changes || []).find(ch => ch.number === clNum);

    let activeFiles = rawFileList;
    if (isOwnerFilter && c) {
      activeFiles = activeFiles.filter(f => getPgFileOwnerStatus(c, f).status === "missing");
    }
    if (query) {
      activeFiles = activeFiles.filter(f => matchesPgFileFilter(f.file, query));
    }

    const isFiltered = Boolean(query || isOwnerFilter);
    const reviewedCount = activeFiles.filter(f => revSet.has(f.file)).length;
    const total = activeFiles.length;
    const summaryEl = document.getElementById(`pgReviewedSummary-${clNum}`);
    if (summaryEl) {
      const isAll = reviewedCount === total && total > 0;
      summaryEl.textContent = isFiltered
        ? `${reviewedCount} / ${total} filtered reviewed`
        : (isAll ? `All ${total} files reviewed ✓` : `${reviewedCount} / ${total} reviewed`);
      if (isAll) {
        summaryEl.classList.add("all-reviewed");
      } else {
        summaryEl.classList.remove("all-reviewed");
      }
    }
    const unreviewedCount = total - reviewedCount;
    const btnNext = document.getElementById(`btnNextUnreviewed-${clNum}`);
    const badgeAll = document.getElementById(`badgeAllReviewed-${clNum}`);
    const btnNextHdr = document.getElementById(`btnNextUnreviewedHeader-${clNum}`);
    const badgeAllHdr = document.getElementById(`badgeAllReviewedHeader-${clNum}`);

    if (unreviewedCount > 0) {
      if (btnNext) {
        btnNext.style.display = "";
        btnNext.innerHTML = `⚡ Next unreviewed <span class="pg-unreviewed-pill-count">${unreviewedCount}</span>`;
      }
      if (badgeAll) badgeAll.style.display = "none";
      if (btnNextHdr) {
        btnNextHdr.style.display = "";
        btnNextHdr.innerHTML = `⚡ Next unreviewed (${unreviewedCount})`;
      }
      if (badgeAllHdr) badgeAllHdr.style.display = "none";
    } else {
      if (btnNext) btnNext.style.display = "none";
      if (badgeAll) badgeAll.style.display = "";
      if (btnNextHdr) btnNextHdr.style.display = "none";
      if (badgeAllHdr) badgeAllHdr.style.display = "";
    }
    syncPgMasterReviewedCheckbox(clNum, ps);
    syncPgMarkAllReviewedButton(clNum, ps);
  }

  function syncPgExpandAllButtonState(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || 1);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return;
    const collapsedSet = getPgCollapsedSet(num, ps);
    let targetFiles = (typeof getPgVisibleFileList === "function") ? getPgVisibleFileList(num) : rawFileList.map(f => f.file);
    if (!targetFiles || !targetFiles.length) {
      targetFiles = rawFileList.map(f => (typeof f === "object" && f.file ? f.file : String(f)));
    }
    const totalCount = targetFiles.length;
    let collapsedCount = 0;
    targetFiles.forEach(f => {
      if (collapsedSet.has(f)) collapsedCount++;
    });
    const expandedCount = totalCount - collapsedCount;
    const allCollapsed = totalCount > 0 && collapsedCount === totalCount;
    const allExpanded = totalCount > 0 && expandedCount === totalCount;

    // 1. Sync legacy/tab subcontrol toggle button
    const btn = document.getElementById(`btnToggleAllDiffs-${num}`);
    if (btn) {
      btn.textContent = allCollapsed ? "Expand All (Shift+I)" : "Collapse All (Shift+I)";
      btn.title = allCollapsed ? "Expand all file diffs (Shortcut: Shift+I)" : "Collapse all file diffs (Shortcut: Shift+I)";
    }

    // 2. Sync dedicated Expand All buttons
    const expandBtns = document.querySelectorAll ? document.querySelectorAll(`#btnExpandAllFiles-${num}, #btnExpandAllHeader-${num}, .pg-btn-expand-all-files[data-cl="${num}"]`) : [];
    if (expandBtns && expandBtns.forEach) {
      expandBtns.forEach(b => {
        if (!b) return;
        b.disabled = allExpanded;
        if (allExpanded) {
          b.classList.add("is-disabled");
          b.title = "All file diffs are already expanded (Shift+I)";
        } else {
          b.classList.remove("is-disabled");
          b.title = `Expand all ${collapsedCount} collapsed file diffs (Shortcut: Shift+I)`;
        }
      });
    }

    // 3. Sync dedicated Collapse All buttons
    const collapseBtns = document.querySelectorAll ? document.querySelectorAll(`#btnCollapseAllFiles-${num}, #btnCollapseAllHeader-${num}, .pg-btn-collapse-all-files[data-cl="${num}"]`) : [];
    if (collapseBtns && collapseBtns.forEach) {
      collapseBtns.forEach(b => {
        if (!b) return;
        b.disabled = allCollapsed;
        if (allCollapsed) {
          b.classList.add("is-disabled");
          b.title = "All file diffs are already collapsed (Shift+E)";
        } else {
          b.classList.remove("is-disabled");
          b.title = `Collapse all ${expandedCount} expanded file diffs (Shortcut: Shift+E)`;
        }
      });
    }
  }

  function expandAllPgFileDiffs(clNum, patchSet, showNotification = true) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return 0;
    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || 1);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return 0;

    let targetFiles = (typeof getPgVisibleFileList === "function") ? getPgVisibleFileList(num) : rawFileList.map(f => f.file);
    if (!targetFiles || !targetFiles.length) {
      targetFiles = rawFileList.map(f => (typeof f === "object" && f.file ? f.file : String(f)));
    }
    const collapsedSet = getPgCollapsedSet(num, ps);
    let newlyExpanded = 0;
    targetFiles.forEach(filePath => {
      if (collapsedSet.has(filePath)) {
        setPgFileCollapsed(num, ps, filePath, false);
        newlyExpanded++;
      }
    });
    setPgFileCollapsed(num, ps, "/COMMIT_MSG", false);
    setPgFileCollapsed(num, ps, "/MERGE_LIST", false);
    syncPgExpandAllButtonState(num, ps);
    if (showNotification && typeof showToast === "function") {
      showToast(`Expanded all ${targetFiles.length} file diffs (Shift+I)`);
    }
    return newlyExpanded;
  }

  function collapseAllPgFileDiffs(clNum, patchSet, showNotification = true) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return 0;
    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || 1);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return 0;

    let targetFiles = (typeof getPgVisibleFileList === "function") ? getPgVisibleFileList(num) : rawFileList.map(f => f.file);
    if (!targetFiles || !targetFiles.length) {
      targetFiles = rawFileList.map(f => (typeof f === "object" && f.file ? f.file : String(f)));
    }
    const collapsedSet = getPgCollapsedSet(num, ps);
    let newlyCollapsed = 0;
    targetFiles.forEach(filePath => {
      if (!collapsedSet.has(filePath)) {
        setPgFileCollapsed(num, ps, filePath, true);
        newlyCollapsed++;
      }
    });
    setPgFileCollapsed(num, ps, "/COMMIT_MSG", true);
    setPgFileCollapsed(num, ps, "/MERGE_LIST", true);
    syncPgExpandAllButtonState(num, ps);
    if (showNotification && typeof showToast === "function") {
      showToast(`Collapsed all ${targetFiles.length} file diffs (Shift+E)`);
    }
    return newlyCollapsed;
  }

  function toggleAllPgFileDiffs(clNum, patchSet) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const ps = Number(patchSet || ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {}).ps || 1);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFileList.length) return;
    const collapsedSet = getPgCollapsedSet(num, ps);
    let fileList = (typeof getPgVisibleFileList === "function") ? getPgVisibleFileList(num) : rawFileList.map(f => f.file);
    if (!fileList || !fileList.length) fileList = rawFileList.map(f => (typeof f === "object" && f.file ? f.file : String(f)));
    const anyCollapsed = fileList.some(f => collapsedSet.has(f));
    if (anyCollapsed) {
      expandAllPgFileDiffs(num, ps, true);
    } else {
      collapseAllPgFileDiffs(num, ps, true);
    }
  }

  function getPgFileOldPath(f) {
    if (!f) return "";
    return f.old_path || f.oldPath || (f.old_file && f.old_file !== f.file ? f.old_file : "") || (f.status === "R" && f.backup_path ? f.backup_path : "") || (f.copied_from && f.copied_from !== f.file ? f.copied_from : "") || "";
  }

  function getPgFileSimilarity(f, precomputedStatus = "") {
    if (!f) return null;
    if (typeof f.similarity === "number" && !isNaN(f.similarity)) return f.similarity;
    if (typeof f.similarity === "string" && f.similarity.trim() !== "") {
      const parsed = parseInt(f.similarity, 10);
      if (!isNaN(parsed)) return parsed;
    }
    const status = precomputedStatus || getPgFileStatus(f);
    if (status === "R" || status === "C") {
      const adds = Number(f.additions || 0);
      const dels = Number(f.deletions || 0);
      if (adds === 0 && dels === 0) return 100;
    }
    return null;
  }

  function renderPgFileRenamePillHtml(f, fallbackCl) {
    const oldPath = getPgFileOldPath(f);
    if (!oldPath) return "";
    const status = getPgFileStatus(f);
    const isCopy = status === "C" || Boolean(f && (f.is_copy || f.copied_from));
    const actionText = isCopy ? "copied from" : "renamed from";
    const sim = getPgFileSimilarity(f);
    const simPillHtml = sim !== null
      ? ` · <span class="pg-file-similarity-pill ${sim === 100 ? "is-pure" : ""}" title="Similarity score: ${sim}%">${sim}% match</span>`
      : "";
    const safeOldPath = escapeHtml(oldPath);
    return `<span class="pg-file-renamed-tag" title="${isCopy ? "Copied" : "Renamed"} from ${safeOldPath}${sim !== null ? ` (${sim}% similarity)` : ""}">${actionText} <code>${renderCodeSearchFileLinkHtml(fallbackCl, oldPath)}</code>${simPillHtml}</span>`;
  }

  function copyPgFilePath(event, filePath) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const pathStr = String(filePath || "").trim();
    if (!pathStr) return;
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(pathStr).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(`Copied "${pathStr}" to clipboard ✓`);
    }
  }

  function closePgCopyFilePathsMenu(clNum) {
    const activeState = (typeof window !== "undefined" && window.state) ? window.state : ((typeof state !== "undefined") ? state : {});
    const targetCl = clNum || activeState.copyFilePathsMenuCl;
    activeState.copyFilePathsMenuCl = null;
    if (typeof document === "undefined") return;
    if (targetCl) {
      const menu = document.getElementById(`pgCopyPathsMenu-${targetCl}`);
      if (menu) {
        menu.classList.remove("is-open");
        menu.style.display = "none";
      }
      const btn = document.getElementById(`btnCopyFilePaths-${targetCl}`);
      if (btn) {
        btn.classList.remove("is-active");
        btn.setAttribute("aria-expanded", "false");
      }
    }
    if (typeof document.querySelectorAll === "function") {
      document.querySelectorAll(".pg-copy-paths-menu.is-open").forEach(menu => {
        menu.classList.remove("is-open");
        menu.style.display = "none";
      });
      document.querySelectorAll(".pg-btn-copy-paths.is-active").forEach(btn => {
        btn.classList.remove("is-active");
        btn.setAttribute("aria-expanded", "false");
      });
    }
  }

  function togglePgCopyFilePathsMenu(clNum) {
    const activeState = (typeof window !== "undefined" && window.state) ? window.state : ((typeof state !== "undefined") ? state : {});
    const num = Number(clNum || activeState.selectedClNumber || 0);
    if (!num) return;
    const menu = document.getElementById(`pgCopyPathsMenu-${num}`);
    const btn = document.getElementById(`btnCopyFilePaths-${num}`);
    const isOpen = Boolean(menu && (menu.classList.contains("is-open") || menu.style.display === "flex"));
    if (isOpen) {
      closePgCopyFilePathsMenu();
    } else {
      closePgCopyFilePathsMenu();
      activeState.copyFilePathsMenuCl = num;
      if (menu) {
        menu.classList.add("is-open");
        menu.style.display = "flex";
      }
      if (btn) {
        btn.classList.add("is-active");
        btn.setAttribute("aria-expanded", "true");
      }
    }
  }

  function copyPgFilePaths(clNum, mode = "all", btnEl = null, dirPrefix = null) {
    const activeState = (typeof window !== "undefined" && window.state) ? window.state : ((typeof state !== "undefined") ? state : {});
    const num = Number(clNum || activeState.selectedClNumber || (activeState.changes && activeState.changes[0] && activeState.changes[0].number));
    const c = (typeof resolvePgChangeForAction === "function") ? resolvePgChangeForAction(num) : ((activeState.changes || []).find(x => Number(x.number) === num) || null);
    const bundle = (num && activeState.diffsByCl && activeState.diffsByCl[num]) || null;
    const rawFiles = (bundle && Array.isArray(bundle.files) && bundle.files.length > 0)
      ? bundle.files
      : ((c && Array.isArray(c.files)) ? c.files.map(f => (typeof f === "string" ? { file: f } : f)) : []);

    const curPs = Number((activeState.selectedPatchsetByCl && activeState.selectedPatchsetByCl[num] && activeState.selectedPatchsetByCl[num].ps) || (bundle && bundle.patch_set) || (c && c.patch_set) || 1);
    const revSet = (typeof getPgReviewedSet === "function") ? getPgReviewedSet(num, curPs) : new Set();

    let candidateFiles = rawFiles.map(f => f && f.file).filter(Boolean);
    candidateFiles = candidateFiles.filter(f => {
      const raw = String(f || "").trim();
      if (!raw) return false;
      if (typeof GERRIT_MAGIC_FILES !== "undefined" && GERRIT_MAGIC_FILES.has(raw)) return false;
      const rel = raw.replace(/^\/+/, "");
      if (!rel || rel === "dev/null") return false;
      if (typeof GERRIT_MAGIC_FILES !== "undefined" && (GERRIT_MAGIC_FILES.has(rel) || GERRIT_MAGIC_FILES.has("/" + rel))) return false;
      return true;
    });

    if (dirPrefix) {
      const cleanPrefix = String(dirPrefix).replace(/^\/+|\/+$/g, "") + "/";
      candidateFiles = candidateFiles.filter(f => {
        const rel = String(f || "").replace(/^\/+/, "");
        return rel.startsWith(cleanPrefix);
      });
    }

    const seen = new Set();
    const uniqueFiles = [];
    for (const f of candidateFiles) {
      if (!seen.has(f)) {
        seen.add(f);
        uniqueFiles.push(f);
      }
    }

    let targetFiles = uniqueFiles;
    let formatDesc = "all files";

    if (mode === "unreviewed") {
      targetFiles = uniqueFiles.filter(f => !revSet.has(f));
      formatDesc = "unreviewed files";
    } else if (mode === "visible" || mode === "filtered") {
      const tableRows = typeof document !== "undefined" && document.querySelectorAll
        ? Array.from(document.querySelectorAll(`#pgFilesTabPane-${num} .pg-file-row:not(.pg-magic-file-row):not(.pg-file-row-empty)`))
        : [];
      if (tableRows.length > 0) {
        const visiblePaths = [];
        tableRows.forEach(r => {
          if (r.style && r.style.display === "none") return;
          const p = (typeof r.getAttribute === "function") ? r.getAttribute("data-file") : null;
          if (p && !visiblePaths.includes(p) && seen.has(p)) {
            visiblePaths.push(p);
          }
        });
        if (visiblePaths.length > 0) {
          targetFiles = visiblePaths;
          formatDesc = "filtered files";
        }
      }
    }

    let outputText = "";
    if (mode === "space") {
      outputText = targetFiles.join(" ");
      formatDesc = "space-separated";
    } else if (mode === "markdown") {
      outputText = targetFiles.map(p => `- \`${p}\``).join("\n");
      formatDesc = "markdown list";
    } else {
      outputText = targetFiles.join("\n");
    }

    if (!outputText) {
      if (typeof showToast === "function") {
        showToast("No matching file paths to copy");
      }
      closePgCopyFilePathsMenu();
      return { count: 0, text: "", paths: [] };
    }

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(outputText).catch(() => {});
    }

    if (typeof showToast === "function") {
      showToast(`Copied ${targetFiles.length} file path${targetFiles.length === 1 ? "" : "s"} (${formatDesc}) ✓`);
    }

    if (btnEl && btnEl.classList) {
      btnEl.classList.add("is-copied");
      setTimeout(() => {
        btnEl.classList.remove("is-copied");
      }, 1500);
    }

    closePgCopyFilePathsMenu();
    return { count: targetFiles.length, text: outputText, paths: targetFiles };
  }

  function renderPgCopyFilePathsDropdownHtml(c, fileList, rawFileList, revSet) {
    if (!c || !c.number) return "";
    const num = Number(c.number);
    const nonMagicRaw = (rawFileList || []).filter(f => {
      const raw = String((f && f.file) || f || "").trim();
      if (!raw || (typeof GERRIT_MAGIC_FILES !== "undefined" && GERRIT_MAGIC_FILES.has(raw))) return false;
      const rel = raw.replace(/^\/+/, "");
      return rel && rel !== "dev/null" && !(typeof GERRIT_MAGIC_FILES !== "undefined" && (GERRIT_MAGIC_FILES.has(rel) || GERRIT_MAGIC_FILES.has("/" + rel)));
    });
    const nonMagicFiltered = (fileList || []).filter(f => {
      const raw = String((f && f.file) || f || "").trim();
      if (!raw || (typeof GERRIT_MAGIC_FILES !== "undefined" && GERRIT_MAGIC_FILES.has(raw))) return false;
      const rel = raw.replace(/^\/+/, "");
      return rel && rel !== "dev/null" && !(typeof GERRIT_MAGIC_FILES !== "undefined" && (GERRIT_MAGIC_FILES.has(rel) || GERRIT_MAGIC_FILES.has("/" + rel)));
    });

    const activeState = (typeof window !== "undefined" && window.state) ? window.state : ((typeof state !== "undefined") ? state : {});
    const rev = revSet || (typeof getPgReviewedSet === "function" ? getPgReviewedSet(num) : new Set());
    const unreviewedCount = nonMagicRaw.filter(f => !rev.has(f.file)).length;
    const isFiltered = nonMagicFiltered.length !== nonMagicRaw.length;
    const isMenuOpen = Boolean(activeState.copyFilePathsMenuCl && Number(activeState.copyFilePathsMenuCl) === num);

    return `<div class="pg-copy-file-paths-wrap" id="pgCopyFilePathsWrap-${num}">
      <button
        type="button"
        class="pg-btn-subtle pg-btn-copy-paths ${isMenuOpen ? "is-active" : ""}"
        id="btnCopyFilePaths-${num}"
        onclick="event.stopPropagation(); togglePgCopyFilePathsMenu(${num})"
        title="Copy file paths to clipboard (Shortcut: Shift+P on row)"
        aria-expanded="${isMenuOpen ? "true" : "false"}"
        aria-haspopup="true"
      ><span class="pg-btn-icon" aria-hidden="true">📋</span> Copy Paths ▾</button>
      <div
        class="pg-copy-paths-menu ${isMenuOpen ? "is-open" : ""}"
        id="pgCopyPathsMenu-${num}"
        style="${isMenuOpen ? "display:flex;" : "display:none;"}"
        onclick="event.stopPropagation()"
        role="menu"
        aria-label="Copy file paths options"
      >
        <div class="pg-copy-paths-menu-header">📋 Copy File Paths</div>
        <button type="button" class="pg-copy-paths-item" onclick="copyPgFilePaths(${num}, 'all', this)">
          <span>All files (${nonMagicRaw.length})</span>
          <span class="pg-copy-paths-hint">newline</span>
        </button>
        ${isFiltered ? `<button type="button" class="pg-copy-paths-item" onclick="copyPgFilePaths(${num}, 'visible', this)">
          <span>Filtered files (${nonMagicFiltered.length})</span>
          <span class="pg-copy-paths-hint">newline</span>
        </button>` : ""}
        ${unreviewedCount > 0 ? `<button type="button" class="pg-copy-paths-item" onclick="copyPgFilePaths(${num}, 'unreviewed', this)">
          <span>Unreviewed files (${unreviewedCount})</span>
          <span class="pg-copy-paths-hint">newline</span>
        </button>` : ""}
        <button type="button" class="pg-copy-paths-item" onclick="copyPgFilePaths(${num}, 'space', this)">
          <span>Space-separated</span>
          <span class="pg-copy-paths-hint">CLI / bash</span>
        </button>
        <button type="button" class="pg-copy-paths-item" onclick="copyPgFilePaths(${num}, 'markdown', this)">
          <span>Markdown list</span>
          <span class="pg-copy-paths-hint">- \`path\`</span>
        </button>
      </div>
    </div>`;
  }

  function getPgFileStatus(f) {
    if (f) {
      const s = f.status;
      if (s === "M" || s === "A" || s === "D" || s === "R" || s === "C") return s;
      if (s) return String(s).toUpperCase();
    }
    if (f && (f.file === "/COMMIT_MSG" || f.file === "/MERGE_LIST" || (typeof GERRIT_MAGIC_FILES !== "undefined" && GERRIT_MAGIC_FILES.has(f.file)))) return "M";
    const oldP = getPgFileOldPath(f);
    if (oldP && oldP !== f.file) {
      return (f && (f.is_copy || f.copied_from)) ? "C" : "R";
    }
    const adds = Number((f && f.additions) || 0);
    const dels = Number((f && f.deletions) || 0);
    const lines = (f && Array.isArray(f.lines)) ? f.lines : [];
    if (dels > 0 && adds === 0 && lines.length > 0 && lines.every(l => l.type === "del" || l.type === "hunk")) {
      return "D";
    }
    if (adds > 0 && dels === 0 && lines.length > 0 && lines.every(l => l.type === "add" || l.type === "hunk")) {
      return "A";
    }
    return "M";
  }

  function getPgFileStatusFilter(clNum) {
    if (!state.fileStatusFilterByCl) {
      state.fileStatusFilterByCl = {};
      return "";
    }
    const raw = state.fileStatusFilterByCl[clNum];
    if (!raw) return "";
    if (raw === "M" || raw === "A" || raw === "D" || raw === "R" || raw === "C") return raw;
    return String(raw).toUpperCase().trim();
  }

  function setPgFileStatusFilter(clNum, status, showNotification = true) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.fileStatusFilterByCl) state.fileStatusFilterByCl = {};
    const norm = String(status || "").toUpperCase().trim();
    state.fileStatusFilterByCl[num] = norm;
    if (showNotification && typeof showToast === "function") {
      let label = "All file statuses";
      if (norm === "A") label = "Added files (+)";
      else if (norm === "M") label = "Modified files (M)";
      else if (norm === "D") label = "Deleted files (-)";
      else if (norm === "R") label = "Renamed files (→)";
      else if (norm === "C") label = "Copied files (📋)";
      showToast(`Filter files: ${label} (Shortcut: Alt+S)`);
    }
    if (typeof reanchorPgFocusedFile === "function") reanchorPgFocusedFile(num);
    applyPgFileFiltersToDom(num);
  }

  function togglePgFileStatusFilter(clNum, status) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const cur = getPgFileStatusFilter(num);
    const target = String(status || "").toUpperCase().trim();
    if (cur === target) {
      setPgFileStatusFilter(num, "");
    } else {
      setPgFileStatusFilter(num, target);
    }
  }

  function cyclePgFileStatusFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    const available = new Set();
    rawFileList.forEach(f => {
      const st = getPgFileStatus(f);
      if (st) available.add(st);
    });
    const order = [""];
    if (available.has("M")) order.push("M");
    if (available.has("A")) order.push("A");
    if (available.has("D")) order.push("D");
    if (available.has("R")) order.push("R");
    if (available.has("C")) order.push("C");
    if (order.length <= 1) {
      if (typeof showToast === "function") showToast("No additional file statuses to filter in change");
      return;
    }
    const cur = getPgFileStatusFilter(num);
    let idx = order.indexOf(cur);
    if (idx === -1) idx = 0;
    const nextStatus = order[(idx + 1) % order.length];
    setPgFileStatusFilter(num, nextStatus);
  }

  function onPgFileStatusBadgeClick(event, clNum, status) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    togglePgFileStatusFilter(num, status);
  }

  function onPgFileStatusBadgeKeyDown(event, clNum, status) {
    if (!event) return;
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      const num = Number(clNum || state.selectedClNumber || 0);
      if (num) togglePgFileStatusFilter(num, status);
    }
  }

  const _PG_STATUS_LABELS = Object.freeze({
    M: "Modified",
    A: "Added",
    D: "Deleted",
    R: "Renamed",
    C: "Copied",
  });
  let _lastStatusBadgeCl = -1;
  let _lastStatusBadgeMap = null;

  function renderPgFileStatusBadgeHtml(status, clNum) {
    const s = (status === "M" || status === "A" || status === "D" || status === "R" || status === "C")
      ? status
      : String(status || "M").toUpperCase();
    const num = Number(clNum || state.selectedClNumber || 0);
    const activeStatus = (num && typeof getPgFileStatusFilter === "function") ? getPgFileStatusFilter(num) : "";
    const isCanon = _PG_STATUS_LABELS[s] !== undefined;
    if (isCanon && !activeStatus && num > 0) {
      if (_lastStatusBadgeCl !== num || !_lastStatusBadgeMap) {
        _lastStatusBadgeCl = num;
        _lastStatusBadgeMap = Object.create(null);
      }
      const cached = _lastStatusBadgeMap[s];
      if (cached !== undefined) return cached;
      const label = _PG_STATUS_LABELS[s];
      const html = `<span class="pg-file-status-badge status-${s} is-clickable" role="button" tabindex="0" onclick="onPgFileStatusBadgeClick(event, ${num}, '${s}')" onkeydown="onPgFileStatusBadgeKeyDown(event, ${num}, '${s}')" title="Filter by status: ${label} (${s}) · Click to toggle" aria-label="Status: ${label}">${s}</span>`;
      _lastStatusBadgeMap[s] = html;
      return html;
    }
    const label = _PG_STATUS_LABELS[s] || "Modified";
    const safeS = isCanon ? s : escapeHtml(s);
    const isActive = Boolean(activeStatus && activeStatus === s);
    const clickAttr = num
      ? ` onclick="onPgFileStatusBadgeClick(event, ${num}, '${safeS}')" onkeydown="onPgFileStatusBadgeKeyDown(event, ${num}, '${safeS}')"`
      : "";
    return `<span class="pg-file-status-badge status-${safeS} is-clickable${isActive ? " is-active-filter" : ""}" role="button" tabindex="0"${clickAttr} title="Filter by status: ${label} (${safeS}) · Click to toggle" aria-label="Status: ${label}">${safeS}</span>`;
  }

  const _pgFileExtCache = new Map();

  function getPgFileExtension(filePath) {
    if (typeof filePath === "string") {
      const cached = _pgFileExtCache.get(filePath);
      if (cached !== undefined) return cached;
    }
    const p = String(filePath || "").trim();
    if (!p || p.charCodeAt(0) === 47) return "";
    const slashIdx = p.lastIndexOf("/");
    const basename = slashIdx >= 0 ? p.slice(slashIdx + 1) : p;
    let res = basename;
    if (basename === "BUILD" || basename === "BUILD.bazel") res = "BUILD";
    else if (basename === "WORKSPACE" || basename === "WORKSPACE.bazel") res = "WORKSPACE";
    else if (basename.charCodeAt(0) === 46 && basename.indexOf(".", 1) === -1) res = basename.toLowerCase();
    else {
      const dotIdx = basename.lastIndexOf(".");
      if (dotIdx > 0) {
        res = basename.slice(dotIdx).toLowerCase();
      }
    }
    if (typeof filePath === "string") {
      if (_pgFileExtCache.size >= 4096) _pgFileExtCache.clear();
      _pgFileExtCache.set(filePath, res);
    }
    return res;
  }

  function getPgFileExtensionFilter(clNum) {
    if (!state.fileExtensionFilterByCl) state.fileExtensionFilterByCl = {};
    return String(state.fileExtensionFilterByCl[clNum] || "").trim();
  }

  function setPgFileExtensionFilter(clNum, ext, showNotification = true) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.fileExtensionFilterByCl) state.fileExtensionFilterByCl = {};
    const norm = String(ext || "").trim();
    state.fileExtensionFilterByCl[num] = norm;
    if (showNotification && typeof showToast === "function") {
      const label = norm ? `${norm} files` : "All file extensions";
      showToast(`Filter files: ${label} (Shortcut: Alt+E)`);
    }
    if (typeof reanchorPgFocusedFile === "function") reanchorPgFocusedFile(num);
    applyPgFileFiltersToDom(num);
  }

  function togglePgFileExtensionFilter(clNum, ext) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const cur = getPgFileExtensionFilter(num);
    const target = String(ext || "").trim();
    if (cur === target) {
      setPgFileExtensionFilter(num, "");
    } else {
      setPgFileExtensionFilter(num, target);
    }
  }

  function cyclePgFileExtensionFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    const extCounts = {};
    rawFileList.forEach(f => {
      const ext = getPgFileExtension(f.file);
      if (ext) extCounts[ext] = (extCounts[ext] || 0) + 1;
    });
    const distinctExts = Object.keys(extCounts).sort((a, b) => extCounts[b] - extCounts[a] || a.localeCompare(b));
    if (distinctExts.length === 0) {
      if (typeof showToast === "function") showToast("No file extensions to filter in change");
      return;
    }
    const order = ["", ...distinctExts];
    const cur = getPgFileExtensionFilter(num);
    let idx = order.indexOf(cur);
    if (idx === -1) idx = 0;
    const nextExt = order[(idx + 1) % order.length];
    setPgFileExtensionFilter(num, nextExt);
  }

  function renderPgFileExtBadgeHtml(ext, clNum) {
    if (!ext) return "";
    const safeExt = escapeHtml(ext);
    const num = Number(clNum || state.selectedClNumber || 0);
    const cur = (num && typeof getPgFileExtensionFilter === "function") ? getPgFileExtensionFilter(num) : "";
    const isActive = Boolean(cur && cur === ext);
    const clickAttr = num
      ? ` onclick="onPgFileExtBadgeClick(event, ${num}, '${safeExt}')" onkeydown="onPgFileExtBadgeKeyDown(event, ${num}, '${safeExt}')"`
      : "";
    return `<span class="pg-file-ext-badge is-clickable${isActive ? " is-active-filter" : ""}" role="button" tabindex="0"${clickAttr} title="Filter by extension: ${safeExt} · Click to toggle (Alt+E)" aria-label="Extension: ${safeExt}">${safeExt}</span>`;
  }

  function onPgFileExtBadgeClick(event, clNum, ext) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    togglePgFileExtensionFilter(num, ext);
  }

  function onPgFileExtBadgeKeyDown(event, clNum, ext) {
    if (!event) return;
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      const num = Number(clNum || state.selectedClNumber || 0);
      if (!num) return;
      togglePgFileExtensionFilter(num, ext);
    }
  }

  function getPgFileSizeFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return "";
    if (!state.fileSizeFilterByCl) state.fileSizeFilterByCl = {};
    return String(state.fileSizeFilterByCl[num] || "").toUpperCase().trim();
  }

  function setPgFileSizeFilter(clNum, sizeCategory, showNotification = true) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    if (!state.fileSizeFilterByCl) state.fileSizeFilterByCl = {};
    const norm = String(sizeCategory || "").toUpperCase().trim();
    state.fileSizeFilterByCl[num] = norm;
    if (showNotification && typeof showToast === "function") {
      let label = "All file sizes";
      if (norm === "XS") label = "Extra small files (XS: 1–9 lines)";
      else if (norm === "S") label = "Small files (S: 10–49 lines)";
      else if (norm === "M") label = "Medium files (M: 50–249 lines)";
      else if (norm === "L") label = "Large files (L: 250–999 lines)";
      else if (norm === "XL") label = "Extra large files (XL: 1000+ lines)";
      showToast(`Filter files: ${label} (Shortcut: Alt+B)`);
    }
    if (typeof reanchorPgFocusedFile === "function") reanchorPgFocusedFile(num);
    applyPgFileFiltersToDom(num);
  }

  function togglePgFileSizeFilter(clNum, sizeCategory) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const cur = getPgFileSizeFilter(num);
    const target = String(sizeCategory || "").toUpperCase().trim();
    if (cur === target) {
      setPgFileSizeFilter(num, "");
    } else {
      setPgFileSizeFilter(num, target);
    }
  }

  function cyclePgFileSizeFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    const available = new Set();
    rawFileList.forEach(f => {
      const info = (typeof getPgFileSizeInfo === "function") ? getPgFileSizeInfo(f) : null;
      if (info && info.category) available.add(info.category);
    });
    const order = [""];
    ["XS", "S", "M", "L", "XL"].forEach(cat => {
      if (available.has(cat)) order.push(cat);
    });
    if (order.length <= 1) {
      if (typeof showToast === "function") showToast("No distinct file sizes to filter in change");
      return;
    }
    const cur = getPgFileSizeFilter(num);
    let idx = order.indexOf(cur);
    if (idx === -1) idx = 0;
    const nextSize = order[(idx + 1) % order.length];
    setPgFileSizeFilter(num, nextSize);
  }

  function onPgFileSizeBadgeClick(event, clNum, sizeCategory) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    togglePgFileSizeFilter(num, sizeCategory);
  }

  function onPgFileSizeBadgeKeyDown(event, clNum, sizeCategory) {
    if (!event) return;
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      const num = Number(clNum || state.selectedClNumber || 0);
      if (!num) return;
      togglePgFileSizeFilter(num, sizeCategory);
    }
  }

  function scrollToFileDiff(clNum, patchSet, filePath) {
    const ps = Number(patchSet || 1);
    const pathStr = String(filePath || "");
    if (pathStr === "/COMMIT_MSG") {
      scrollToCommitMessageBox(clNum);
      return;
    }
    if (pathStr === "/MERGE_LIST") {
      scrollToMergeListBox(clNum);
      return;
    }
    updatePgActiveFile(clNum, pathStr);
    setPgFileCollapsed(clNum, ps, pathStr, false);
    const safeFileId = pathStr.replace(/[^a-zA-Z0-9_-]/g, "_");
    const targetEl = document.getElementById(`file-diff-${clNum}-${safeFileId}`);
    if (targetEl && typeof targetEl.scrollIntoView === "function") {
      targetEl.style.display = "";
      targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
      targetEl.classList.remove("pg-diff-focused-flash");
      void targetEl.offsetWidth;
      targetEl.classList.add("pg-diff-focused-flash");
      setTimeout(() => {
        targetEl.classList.remove("pg-diff-focused-flash");
      }, 1400);
    }
  }

  function scrollToCommitMessageBox(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    updatePgActiveFile(num, "/COMMIT_MSG");
    uncollapseCommitMessage(num, false);
    const box = document.getElementById(`commitMsgPanel-${num}`) ||
                document.getElementById(`commitMsgBox-${num}`) ||
                document.querySelector(".pg-commit-container");
    if (box && typeof box.scrollIntoView === "function") {
      box.scrollIntoView({ behavior: "smooth", block: "start" });
      box.classList.remove("pg-diff-focused-flash");
      void box.offsetWidth;
      box.classList.add("pg-diff-focused-flash");
      setTimeout(() => {
        box.classList.remove("pg-diff-focused-flash");
      }, 1400);
    }
  }

  function jumpToPgCommitMessage(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return false;
    updatePgActiveFile(num, "/COMMIT_MSG");
    focusPgFileRow(num, "/COMMIT_MSG", { scrollIntoView: true, focusElement: true });
    scrollToCommitMessageBox(num);
    if (typeof showToast === "function") {
      showToast(`Jumped to Commit Message (/COMMIT_MSG) (ag/${num}) ✓`, 1600);
    }
    return true;
  }

  function jumpToPgMergeList(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return false;
    const c = (state.changes || []).find(ch => Number(ch.number) === num);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    if (!isPgMergeCommit(c, bundle)) {
      if (typeof showToast === "function") showToast("Change is not a merge commit (no /MERGE_LIST)");
      return false;
    }
    updatePgActiveFile(num, "/MERGE_LIST");
    focusPgFileRow(num, "/MERGE_LIST", { scrollIntoView: true, focusElement: true });
    scrollToMergeListBox(num);
    if (typeof showToast === "function") {
      showToast(`Jumped to Merge List (/MERGE_LIST) (ag/${num}) ✓`, 1600);
    }
    return true;
  }

  function scrollToPgFileList(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const wrap = document.getElementById(`clRow-${num}`) || document.querySelector(".pg-file-list-table-wrap") || document.querySelector(".pg-tabs-bar");
    if (wrap && typeof wrap.scrollIntoView === "function") {
      wrap.scrollIntoView({ behavior: "smooth", block: "start" });
      const tableWrap = document.querySelector(".pg-file-list-table-wrap");
      if (tableWrap) {
        tableWrap.classList.remove("flash-highlight");
        void tableWrap.offsetWidth;
        tableWrap.classList.add("flash-highlight");
        setTimeout(() => tableWrap.classList.remove("flash-highlight"), 1400);
      }
    }
  }

  function onPgFileRowClick(event, clNum, patchSet, filePath) {
    if (state.suppressFileRowClick) {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      return;
    }
    if (event) {
      if (event.target && event.target.closest("button, input, label")) {
        return;
      }
      if (event.shiftKey) {
        event.preventDefault();
        const num = Number(clNum || state.selectedClNumber || 0);
        const prevAnchor = (state.fileListRangeSelection && state.fileListRangeSelection.anchor) ||
                           (state.focusedFileByCl && state.focusedFileByCl[num]) ||
                           (state.activeFileByCl && state.activeFileByCl[num]) ||
                           filePath;
        focusPgFileRow(num, filePath, { scrollIntoView: false, focusElement: true });
        updatePgFileListRangeSelection(num, prevAnchor, filePath);
        return;
      }
      event.preventDefault();
    }
    clearPgFileListRangeSelection();
    focusPgFileRow(clNum, filePath, { scrollIntoView: false, focusElement: true });
    updatePgActiveFile(clNum, filePath);
    scrollToFileDiff(clNum, patchSet, filePath);
  }

  function onPgFileRowKeyDown(event, clNum, patchSet, filePath) {
    if (!event) return;
    const key = event.key;
    const num = Number(clNum || 0);
    const ps = Number(patchSet || 1);
    const safeFile = String(filePath || "");

    if (event.ctrlKey || event.metaKey) return;

    if (event.altKey) {
      if (key === "s" || key === "S") {
        event.preventDefault();
        cyclePgFileStatusFilter(num);
        return;
      }
      if (key === "e" || key === "E") {
        event.preventDefault();
        cyclePgFileExtensionFilter(num);
        return;
      }
      if (key === "b" || key === "B") {
        event.preventDefault();
        cyclePgFileSizeFilter(num);
        return;
      }
      if (key === "o" || key === "O") {
        event.preventDefault();
        openPgCodeOwnersDialog(num, safeFile, false);
        return;
      }
      if (key === "j" || key === "J") {
        event.preventDefault();
        navigatePgFileListCursor(num, "next-unreviewed");
        const nextFile = (state.focusedFileByCl && state.focusedFileByCl[num]) || "";
        if (nextFile) {
          focusPgFileRow(num, nextFile, { focusElement: true });
        }
        return;
      }
      if (key === "k" || key === "K") {
        event.preventDefault();
        navigatePgFileListCursor(num, "prev-unreviewed");
        const prevFile = (state.focusedFileByCl && state.focusedFileByCl[num]) || "";
        if (prevFile) {
          focusPgFileRow(num, prevFile, { focusElement: true });
        }
        return;
      }
      if (key === "]") {
        event.preventDefault();
        navigatePgUnreviewedFileDiff("next", safeFile, num);
        return;
      }
      if (key === "[") {
        event.preventDefault();
        navigatePgUnreviewedFileDiff("prev", safeFile, num);
        return;
      }
      return;
    }

    if (key === "Escape") {
      event.preventDefault();
      if (state.fileListRangeSelection && Array.isArray(state.fileListRangeSelection.files) && state.fileListRangeSelection.files.length > 0) {
        clearPgFileListRangeSelection(true);
        return;
      }
      const hasFilter = Boolean(
        getPgFileQueryFilter(num) ||
        getPgFileStatusFilter(num) ||
        getPgFileExtensionFilter(num) ||
        getPgFileSizeFilter(num) ||
        isPgOwnerFilterActive(num) ||
        isPgCommentsFilterActive(num) ||
        isPgHideReviewedActive(num)
      );
      if (hasFilter) {
        clearAllPgFileFilters(num, true);
        return;
      }
      return;
    }

    if (key === "/" || (key === "F" && event.shiftKey)) {
      event.preventDefault();
      const input = document.getElementById(`pgFileFilterInput-${num}`);
      if (input) {
        input.focus();
        if (typeof input.select === "function") input.select();
      }
      return;
    }

    if (key === "Enter" || key === "o") {
      event.preventDefault();
      onPgFileRowClick(event, num, ps, safeFile);
      return;
    }
    if (key === " " || key === "i" || key === "I") {
      if (state.fileListRangeSelection && Array.isArray(state.fileListRangeSelection.files) && state.fileListRangeSelection.files.length > 1) {
        event.preventDefault();
        executePgRangeAction(num, "expand");
        return;
      }
      if (key === "I" || (key === "i" && event.shiftKey)) {
        event.preventDefault();
        expandAllPgFileDiffs(num, ps);
        return;
      }
      event.preventDefault();
      togglePgFileDiffBlock(event, num, ps, safeFile);
      return;
    }
    if (key === "e" || key === "E") {
      if (state.fileListRangeSelection && Array.isArray(state.fileListRangeSelection.files) && state.fileListRangeSelection.files.length > 1) {
        event.preventDefault();
        executePgRangeAction(num, "collapse");
        return;
      }
      if (key === "E" || (key === "e" && event.shiftKey)) {
        event.preventDefault();
        collapseAllPgFileDiffs(num, ps);
        return;
      }
      if (!isPgFileCollapsed(num, ps, safeFile)) {
        event.preventDefault();
        setPgFileCollapsed(num, ps, safeFile, true);
        if (typeof showToast === "function") {
          showToast(`Collapsed diff: ${safeFile} (e)`);
        }
        return;
      }
    }
    if (key === "u" || key === "U") {
      if (state.fileListRangeSelection && Array.isArray(state.fileListRangeSelection.files) && state.fileListRangeSelection.files.length > 0) {
        event.preventDefault();
        executePgRangeAction(num, "unreviewed");
        return;
      }
    }
    if (key === "ArrowDown" || key === "j" || key === "J") {
      event.preventDefault();
      const prevFocused = (state.focusedFileByCl && state.focusedFileByCl[num]) || safeFile;
      navigatePgFileListCursor(num, "next");
      const nextFile = (state.focusedFileByCl && state.focusedFileByCl[num]) || "";
      if (nextFile) {
        focusPgFileRow(num, nextFile, { focusElement: true });
        if (event.shiftKey) {
          const anchor = (state.fileListRangeSelection && state.fileListRangeSelection.anchor) || prevFocused;
          updatePgFileListRangeSelection(num, anchor, nextFile);
        } else {
          clearPgFileListRangeSelection();
        }
      }
      return;
    }
    if (key === "ArrowUp" || key === "k" || key === "K") {
      event.preventDefault();
      const prevFocused = (state.focusedFileByCl && state.focusedFileByCl[num]) || safeFile;
      navigatePgFileListCursor(num, "prev");
      const prevFile = (state.focusedFileByCl && state.focusedFileByCl[num]) || "";
      if (prevFile) {
        focusPgFileRow(num, prevFile, { focusElement: true });
        if (event.shiftKey) {
          const anchor = (state.fileListRangeSelection && state.fileListRangeSelection.anchor) || prevFocused;
          updatePgFileListRangeSelection(num, anchor, prevFile);
        } else {
          clearPgFileListRangeSelection();
        }
      }
      return;
    }
    if (key === "ArrowLeft") {
      const targetRow = (event.currentTarget && event.currentTarget.getAttribute) ? event.currentTarget : (event.target && event.target.closest && event.target.closest(".pg-file-row"));
      const parentDir = targetRow ? targetRow.getAttribute("data-dir") : "";
      if (parentDir) {
        event.preventDefault();
        const dirRows = document.querySelectorAll(`.pg-dir-row[data-cl="${num}"]`);
        let dirRow = null;
        for (let i = 0; i < dirRows.length; i++) {
          if (dirRows[i].getAttribute("data-dir") === parentDir) {
            dirRow = dirRows[i];
            break;
          }
        }
        if (!dirRow && typeof getPgDirRowEl === "function") {
          dirRow = getPgDirRowEl(document, parentDir);
        }
        if (dirRow && typeof dirRow.focus === "function") {
          dirRow.focus();
        }
      }
      return;
    }
    if (key === "x" || key === "X" || key === "v" || key === "V" || key === "r" || key === "R") {
      event.preventDefault();
      if (state.fileListRangeSelection && Array.isArray(state.fileListRangeSelection.files) && state.fileListRangeSelection.files.length > 0) {
        executePgRangeAction(num, "toggle-reviewed", { clearSelection: true });
        return;
      }
      if (event.shiftKey && state.lastReviewedCheckbox && state.lastReviewedCheckbox.file && state.lastReviewedCheckbox.file !== safeFile) {
        const visibleFiles = (typeof getPgAllVisibleFiles === "function")
          ? getPgAllVisibleFiles(num)
          : getPgVisibleFileList(num);
        const idxA = visibleFiles.indexOf(state.lastReviewedCheckbox.file);
        const idxB = visibleFiles.indexOf(safeFile);
        if (idxA !== -1 && idxB !== -1) {
          const start = Math.min(idxA, idxB);
          const end = Math.max(idxA, idxB);
          const rangeFiles = visibleFiles.slice(start, end + 1);
          const revSet = getPgReviewedSet(num, ps);
          const allRev = rangeFiles.every(f => revSet.has(f));
          const nextRev = !allRev;
          setPgFilesReviewedRange(num, ps, rangeFiles, nextRev);
          state.lastReviewedCheckbox = {
            cl: num,
            ps,
            file: safeFile,
            checked: nextRev,
          };
          if (typeof showToast === "function") {
            showToast(
              nextRev
                ? `Marked ${rangeFiles.length} files as reviewed ✓ (Shift+${key.toUpperCase()})`
                : `Marked ${rangeFiles.length} files as unreviewed (Shift+${key.toUpperCase()})`
            );
          }
          return;
        }
      }
      const isRev = isPgFileReviewed(num, ps, safeFile);
      const nextRev = !isRev;
      setPgFileReviewed(num, ps, safeFile, nextRev);
      state.lastReviewedCheckbox = { cl: num, ps, file: safeFile, checked: nextRev };
      const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
      if (changeView) {
        const cb = changeView.querySelector(`.pg-file-row[data-file="${safeFile}"] .pg-file-reviewed-cb`);
        if (cb) cb.checked = nextRev;
        const row = changeView.querySelector(`.pg-file-row[data-file="${safeFile}"]`);
        if (row) {
          if (nextRev) row.classList.add("is-reviewed");
          else row.classList.remove("is-reviewed");
        }
        syncPgMasterReviewedCheckbox(num, ps);
        syncPgMarkAllReviewedButton(num, ps);
        const targetRow = (event.currentTarget && event.currentTarget.getAttribute) ? event.currentTarget : (event.target && event.target.closest && event.target.closest(".pg-file-row"));
        const parentDir = targetRow ? targetRow.getAttribute("data-dir") : "";
        if (parentDir) syncPgDirReviewedCheckbox(num, parentDir);
      }
      if (typeof renderDiffPane === "function") renderDiffPane();
      if (typeof showToast === "function") {
        const shortName = safeFile.split("/").pop() || safeFile;
        showToast(nextRev ? `Marked ${shortName} as reviewed ✓ (${key})` : `Marked ${shortName} as unreviewed (${key})`);
      }
      return;
    }
    if (key === "f") {
      event.preventDefault();
      togglePgActiveFileReviewed();
      return;
    }
    if (key === "M" || (key === "m" && event.shiftKey)) {
      event.preventDefault();
      markActiveFileReviewedAndGoToNextUnreviewed(num, safeFile, ps);
      return;
    }
    if (key === "m") {
      event.preventDefault();
      const c = (state.changes || []).find(ch => Number(ch.number) === num);
      const bundle = state.diffsByCl && state.diffsByCl[num];
      const isMerge = (typeof isPgMergeCommit === "function") && isPgMergeCommit(c, bundle);
      if (safeFile === "/COMMIT_MSG" && isMerge) {
        jumpToPgMergeList(num);
      } else {
        jumpToPgCommitMessage(num);
      }
      return;
    }
    if (key === "e") {
      event.preventDefault();
      if (safeFile === "/COMMIT_MSG") {
        openPgCommitMsgEditor(num);
      } else {
        openPgFileEditor(num, safeFile, ps);
      }
      return;
    }
    if (key === "c") {
      event.preventDefault();
      triggerPgAddComment(num, safeFile);
      return;
    }
    if (key === "p" || key === "P") {
      event.preventDefault();
      if (event.shiftKey || key === "P") {
        copyPgFilePaths(num, "visible");
      } else {
        copyPgFilePath(event, safeFile);
      }
      return;
    }
  }

  function navigatePgFileDiff(dir, fromFile = null, fromCl = null) {
    if (state.codeViewerMode) {
      stepCodeViewerFile(dir, fromFile, fromCl);
      return;
    }
    const changeView = document.querySelector(".pg-change-view");
    if (!changeView) return;
    const curCl = Number(fromCl || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const c = (state.changes || []).find(ch => Number(ch.number) === curCl);
    const bundle = state.diffsByCl && state.diffsByCl[curCl];
    const isMerge = (typeof isPgMergeCommit === "function") && isPgMergeCommit(c, bundle);
    const blocks = Array.from(changeView.querySelectorAll(".pg-file-diff-block")).filter(b => b.style.display !== "none");

    if (!blocks.length) {
      if (fromFile === "/COMMIT_MSG" && dir === "next" && isMerge) {
        updatePgActiveFile(curCl, "/MERGE_LIST");
        focusPgFileRow(curCl, "/MERGE_LIST", { scrollIntoView: true });
        scrollToMergeListBox(curCl);
        if (typeof showToast === "function") showToast("At /MERGE_LIST (Merge List)");
        return;
      }
      if (fromFile === "/MERGE_LIST" && dir === "prev") {
        updatePgActiveFile(curCl, "/COMMIT_MSG");
        focusPgFileRow(curCl, "/COMMIT_MSG", { scrollIntoView: true });
        scrollToCommitMessageBox(curCl);
        if (typeof showToast === "function") showToast("At /COMMIT_MSG (Commit Message)");
        return;
      }
      const activeCl = curCl || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (activeCl) {
        navigatePgFileListCursor(activeCl, dir);
      }
      return;
    }

    if (!fromFile && curCl) {
      const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
      if (activeBlock && typeof activeBlock.getAttribute === "function") {
        fromFile = activeBlock.getAttribute("data-file");
      }
      if (!fromFile) {
        fromFile = (state.activeFileByCl && state.activeFileByCl[curCl]) ||
                   (state.focusedFileByCl && state.focusedFileByCl[curCl]) || null;
      }
    }

    if (fromFile === "/COMMIT_MSG") {
      if (dir === "next") {
        if (isMerge) {
          updatePgActiveFile(curCl, "/MERGE_LIST");
          focusPgFileRow(curCl, "/MERGE_LIST", { scrollIntoView: true });
          scrollToMergeListBox(curCl);
          if (typeof showToast === "function") showToast("At /MERGE_LIST (Merge List)");
          return;
        } else if (blocks.length > 0) {
          const firstTarget = blocks[0];
          const clNum = Number(firstTarget.getAttribute("data-cl") || curCl);
          const ps = Number(firstTarget.getAttribute("data-ps") || 1);
          const fPath = firstTarget.getAttribute("data-file") || "";
          updatePgActiveFile(clNum, fPath);
          focusPgFileRow(clNum, fPath, { scrollIntoView: true });
          scrollToFileDiff(clNum, ps, fPath);
          return;
        } else {
          if (typeof showToast === "function") showToast("At last file diff");
          return;
        }
      } else if (dir === "prev") {
        scrollToPgFileList(curCl);
        if (typeof showToast === "function") showToast("At top of change (/COMMIT_MSG) — jumped to File List");
        return;
      }
    }

    if (fromFile === "/MERGE_LIST") {
      if (dir === "next") {
        if (blocks.length > 0) {
          const firstTarget = blocks[0];
          const clNum = Number(firstTarget.getAttribute("data-cl") || curCl);
          const ps = Number(firstTarget.getAttribute("data-ps") || 1);
          const fPath = firstTarget.getAttribute("data-file") || "";
          updatePgActiveFile(clNum, fPath);
          focusPgFileRow(clNum, fPath, { scrollIntoView: true });
          scrollToFileDiff(clNum, ps, fPath);
          return;
        } else {
          if (typeof showToast === "function") showToast("At last file diff");
          return;
        }
      } else if (dir === "prev") {
        updatePgActiveFile(curCl, "/COMMIT_MSG");
        focusPgFileRow(curCl, "/COMMIT_MSG", { scrollIntoView: true });
        scrollToCommitMessageBox(curCl);
        if (typeof showToast === "function") showToast("At /COMMIT_MSG (Commit Message)");
        return;
      }
    }

    let targetIndex = -1;
    if (fromFile) {
      const targetClNum = Number(fromCl || curCl || 0);
      let explicitIdx = -1;
      if (targetClNum > 0) {
        explicitIdx = blocks.findIndex(b => Number(b.getAttribute("data-cl")) === targetClNum && b.getAttribute("data-file") === fromFile);
      }
      if (explicitIdx === -1) {
        explicitIdx = blocks.findIndex(b => b.getAttribute("data-file") === fromFile);
      }
      if (explicitIdx !== -1) {
        if (dir === "next") {
          if (explicitIdx >= blocks.length - 1) {
            const isCont = typeof isContinuousStackDiffActive === "function" && isContinuousStackDiffActive();
            if (isCont) {
              if (typeof showToast === "function") showToast("At last file diff in stack");
            } else {
              const curClNum = targetClNum || state.selectedClNumber;
              const st = typeof findPgStackForClOrId === "function" ? findPgStackForClOrId(curClNum) : null;
              if (st && Array.isArray(st.commits) && st.commits.length > 1) {
                const curCommitIdx = st.commits.findIndex(commit => Number(commit.number) === Number(curClNum));
                if (curCommitIdx >= 0 && curCommitIdx < st.commits.length - 1) {
                  const nextCl = st.commits[curCommitIdx + 1].number;
                  if (typeof showToast === "function") showToast(`At last file in ag/${curClNum} (Press Shift+] for next commit ag/${nextCl})`, 3000);
                  return;
                }
              }
              if (typeof showToast === "function") showToast("At last file diff");
            }
            return;
          }
          targetIndex = explicitIdx + 1;
        } else if (dir === "prev") {
          if (explicitIdx <= 0) {
            const firstCl = Number(blocks[0].getAttribute("data-cl") || state.selectedClNumber || 0);
            scrollToPgFileList(firstCl);
            if (typeof showToast === "function") showToast("At first file diff — jumped to File List");
            return;
          }
          targetIndex = explicitIdx - 1;
        }
      }
    }
    if (targetIndex === -1) {
      const container = document.getElementById("diffPane") || document.getElementById("diffScroll") || window;
      const viewTop = container.scrollTop !== undefined ? container.scrollTop : 0;
      let currentIndex = -1;
      for (let i = 0; i < blocks.length; i++) {
        const top = blocks[i].offsetTop || 0;
        if (top >= viewTop - 40) {
          currentIndex = i;
          break;
        }
      }
      if (currentIndex === -1) currentIndex = 0;
      targetIndex = currentIndex;
      if (dir === "next") {
        const currTop = blocks[currentIndex].offsetTop || 0;
        targetIndex = Math.min(blocks.length - 1, currentIndex + (currTop <= viewTop + 20 ? 1 : 0));
      } else if (dir === "prev") {
        if (currentIndex === 0) {
          const firstCl = Number(blocks[0].getAttribute("data-cl") || state.selectedClNumber || 0);
          scrollToPgFileList(firstCl);
          if (typeof showToast === "function") showToast("At first file diff — jumped to File List");
          return;
        }
        targetIndex = Math.max(0, currentIndex - 1);
      }
    }
    const targetBlock = blocks[targetIndex];
    if (targetBlock) {
      const clNum = Number(targetBlock.getAttribute("data-cl"));
      const ps = Number(targetBlock.getAttribute("data-ps") || 1);
      const filePath = targetBlock.getAttribute("data-file") || "";
      const currentCl = Number(state.selectedClNumber || 0);
      if (clNum && currentCl && clNum !== currentCl) {
        state.selectedClNumber = clNum;
        if (typeof syncPgStackUiState === "function") {
          syncPgStackUiState(clNum);
        }
        if (typeof showToast === "function") {
          showToast(`Jumped to ${dir === "next" ? "next" : "previous"} commit in stack: ag/${clNum} — ${filePath}`, 2500);
        }
      }
      updatePgActiveFile(clNum, filePath);
      focusPgFileRow(clNum, filePath, { scrollIntoView: true });
      scrollToFileDiff(clNum, ps, filePath);
    }
  }

  function getPgDiffChunks(options = {}) {
    const changeView = document.querySelector(".pg-change-view");
    if (!changeView) return [];
    const chunksOnly = !!(options && options.chunksOnly);
    const targetCl = options && options.clNum ? Number(options.clNum) : null;

    const fileBlocks = Array.from(changeView.querySelectorAll(
      ".pg-file-diff-block:not([style*='display: none'])"
    ));
    if (!fileBlocks.length) return [];

    const allChunks = [];

    for (let bIdx = 0; bIdx < fileBlocks.length; bIdx++) {
      const block = fileBlocks[bIdx];
      const clNum = Number(block.getAttribute("data-cl") || state.selectedClNumber || 0);
      if (targetCl && clNum !== targetCl) continue;
      const file = block.getAttribute("data-file") || "";
      const ps = Number(block.getAttribute("data-ps") || 1);

      const isSbs = !!block.querySelector(".code-table.diff-mode-side-by-side, .code-line-sbs");
      const blockChunks = [];

      if (isSbs) {
        const sbsRows = Array.from(block.querySelectorAll(".code-line-sbs:not(.code-hunk)"));
        let currentChunkRows = [];

        const flushSbsChunk = () => {
          if (!currentChunkRows.length) return;
          const firstRow = currentChunkRows[0];
          const lastRow = currentChunkRows[currentChunkRows.length - 1];
          const hasAdd = currentChunkRows.some(r => (r.querySelector && r.querySelector(".side-add")) || (r.classList && r.classList.contains("side-add")));
          const hasDel = currentChunkRows.some(r => (r.querySelector && r.querySelector(".side-del")) || (r.classList && r.classList.contains("side-del")));
          const changeType = (hasAdd && hasDel) ? "edit" : (hasAdd ? "add" : "del");
          blockChunks.push({
            type: "chunk",
            element: firstRow,
            rows: currentChunkRows,
            file,
            clNum,
            ps,
            lineStart: (firstRow.getAttribute && (firstRow.getAttribute("data-new-ln") || firstRow.getAttribute("data-old-ln"))) || "1",
            lineEnd: (lastRow.getAttribute && (lastRow.getAttribute("data-new-ln") || lastRow.getAttribute("data-old-ln"))) || "1",
            changeType,
            lineCount: currentChunkRows.length,
          });
          currentChunkRows = [];
        };

        for (let rIdx = 0; rIdx < sbsRows.length; rIdx++) {
          const row = sbsRows[rIdx];
          const isModified = (row.getAttribute && row.getAttribute("data-type") === "diff") ||
            (row.querySelector && row.querySelector(".side-add, .side-del") !== null) ||
            (row.classList && (row.classList.contains("side-add") || row.classList.contains("side-del")));

          if (isModified) {
            currentChunkRows.push(row);
          } else {
            flushSbsChunk();
          }
        }
        flushSbsChunk();
      } else {
        const unifiedRows = Array.from(block.querySelectorAll(".code-line:not(.code-hunk):not(.commit-msg-line)"));
        let currentChunkRows = [];

        const flushUnifiedChunk = () => {
          if (!currentChunkRows.length) return;
          const firstRow = currentChunkRows[0];
          const lastRow = currentChunkRows[currentChunkRows.length - 1];
          const hasAdd = currentChunkRows.some(r => (r.classList && r.classList.contains("code-add")) || (r.getAttribute && r.getAttribute("data-type") === "add"));
          const hasDel = currentChunkRows.some(r => (r.classList && r.classList.contains("code-del")) || (r.getAttribute && r.getAttribute("data-type") === "del"));
          const changeType = (hasAdd && hasDel) ? "edit" : (hasAdd ? "add" : "del");
          blockChunks.push({
            type: "chunk",
            element: firstRow,
            rows: currentChunkRows,
            file,
            clNum,
            ps,
            lineStart: (firstRow.getAttribute && (firstRow.getAttribute("data-new-ln") || firstRow.getAttribute("data-old-ln"))) || "1",
            lineEnd: (lastRow.getAttribute && (lastRow.getAttribute("data-new-ln") || lastRow.getAttribute("data-old-ln"))) || "1",
            changeType,
            lineCount: currentChunkRows.length,
          });
          currentChunkRows = [];
        };

        for (let rIdx = 0; rIdx < unifiedRows.length; rIdx++) {
          const row = unifiedRows[rIdx];
          const isModified = (row.classList && (row.classList.contains("code-add") || row.classList.contains("code-del"))) ||
            (row.getAttribute && (row.getAttribute("data-type") === "add" || row.getAttribute("data-type") === "del"));

          if (isModified) {
            currentChunkRows.push(row);
          } else {
            flushUnifiedChunk();
          }
        }
        flushUnifiedChunk();
      }

      // If no code rows were found, include hunk headers as fallback anchors
      if (blockChunks.length === 0) {
        const hunks = Array.from(block.querySelectorAll(".code-line.code-hunk, .code-line[data-type='hunk']"));
        for (let hIdx = 0; hIdx < hunks.length; hIdx++) {
          const hEl = hunks[hIdx];
          blockChunks.push({
            type: "chunk",
            element: hEl,
            rows: [hEl],
            file,
            clNum,
            ps,
            lineStart: (hEl.getAttribute && hEl.getAttribute("data-idx")) || "1",
            lineEnd: (hEl.getAttribute && hEl.getAttribute("data-idx")) || "1",
            changeType: "edit",
            lineCount: 1,
          });
        }
      }

      // Inline comments / threads unless chunksOnly
      if (!chunksOnly) {
        const commentEls = Array.from(block.querySelectorAll(
          ".diff-inline-comment:not(.collapsed):not(.pg-comment-hidden):not(.pg-author-hidden), " +
          ".swarm-finding[data-thread-ui-id], " +
          ".code-line.has-thread"
        ));
        for (let cIdx = 0; cIdx < commentEls.length; cIdx++) {
          const el = commentEls[cIdx];
          if (el.style && el.style.display === "none") continue;
          const lineVal = (el.getAttribute && el.getAttribute("data-line")) ||
            (el.closest && el.closest(".code-line") && el.closest(".code-line").getAttribute("data-new-ln")) ||
            "1";
          blockChunks.push({
            type: "comment",
            element: el,
            rows: [el],
            file,
            clNum,
            ps,
            line: lineVal,
            changeType: "comment",
            lineCount: 1,
          });
        }
      }

      // Sort items in block by DOM appearance order
      blockChunks.sort((a, b) => {
        if (a.element === b.element) return 0;
        if (typeof a.element.compareDocumentPosition === "function") {
          const pos = a.element.compareDocumentPosition(b.element);
          if (pos & 4 /* Node.DOCUMENT_POSITION_FOLLOWING */) return -1;
          if (pos & 2 /* Node.DOCUMENT_POSITION_PRECEDING */) return 1;
        }
        return 0;
      });

      allChunks.push(...blockChunks);
    }

    return allChunks;
  }

  function navigatePgDiffChunk(dir, options = {}) {
    const changeView = document.querySelector(".pg-change-view");
    if (!changeView) return;

    const allChunks = getPgDiffChunks(options);
    if (!allChunks.length) {
      if (typeof showToast === "function") {
        showToast("No diff chunks to navigate in current change view", 2000);
      }
      return;
    }

    const container = document.getElementById("diffPane") || document.getElementById("diffScroll") || window;
    let cTop = 0;
    let cHeight = window.innerHeight || 800;
    if (container !== window && typeof container.getBoundingClientRect === "function") {
      const cRect = container.getBoundingClientRect();
      cTop = cRect.top;
      cHeight = cRect.height || window.innerHeight || 800;
    }

    const activeEl = document.querySelector(".pg-chunk-active");
    let targetIdx = null;
    if (activeEl) {
      const activeIdx = allChunks.findIndex(c => c.element === activeEl || (c.element.contains && c.element.contains(activeEl)) || (activeEl.contains && activeEl.contains(c.element)));
      if (activeIdx !== -1) {
        targetIdx = (dir === "next" ? activeIdx + 1 : activeIdx - 1);
      }
    }

    if (targetIdx === null) {
      let visibleIdx = -1;
      for (let i = 0; i < allChunks.length; i++) {
        const el = allChunks[i].element;
        if (typeof el.getBoundingClientRect === "function") {
          const rect = el.getBoundingClientRect();
          const relTop = rect.top - cTop;
          if (relTop >= 40) {
            visibleIdx = i;
            break;
          }
        }
      }
      if (visibleIdx === -1) {
        targetIdx = (dir === "next" ? 0 : allChunks.length - 1);
      } else if (dir === "next") {
        targetIdx = visibleIdx;
      } else {
        targetIdx = Math.max(0, visibleIdx - 1);
      }
    }

    if (dir === "next" && targetIdx >= allChunks.length) {
      if (typeof showToast === "function") {
        showToast("At last diff chunk across change (Press 'p' for previous chunk)", 2500);
      }
      return;
    }
    if (dir === "prev" && targetIdx < 0) {
      if (typeof showToast === "function") {
        showToast("At first diff chunk across change (Press 'n' for next chunk)", 2500);
      }
      return;
    }

    const target = allChunks[targetIdx];
    if (!target || !target.element) return;

    // Auto-expand parent file block if collapsed
    const parentBlock = typeof target.element.closest === "function" ? target.element.closest(".pg-file-diff-block") : null;
    if (parentBlock && parentBlock.classList && parentBlock.classList.contains("is-collapsed")) {
      const blockCl = Number(parentBlock.getAttribute("data-cl") || target.clNum);
      const blockPs = Number(parentBlock.getAttribute("data-ps") || target.ps || 1);
      const blockFile = parentBlock.getAttribute("data-file") || target.file;
      if (typeof setPgFileCollapsed === "function") {
        setPgFileCollapsed(blockCl, blockPs, blockFile, false);
      }
      parentBlock.classList.remove("is-collapsed");
      const codeTbl = parentBlock.querySelector(".code-table");
      if (codeTbl && codeTbl.style) codeTbl.style.display = "";
      const toggleBtn = parentBlock.querySelector(".pg-file-chevron, .file-toggle-btn");
      if (toggleBtn) toggleBtn.textContent = "▼";
    }

    // Scroll to target element
    if (typeof target.element.scrollIntoView === "function") {
      target.element.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    // Update active highlight classes
    const existingActives = document.querySelectorAll(".pg-chunk-active");
    for (let a = 0; a < existingActives.length; a++) {
      existingActives[a].classList.remove("pg-chunk-active");
    }
    if (target.element.classList) {
      target.element.classList.remove("pg-chunk-focused-flash");
      void (target.element.offsetWidth || 0);
      target.element.classList.add("pg-chunk-active", "pg-chunk-focused-flash");
      setTimeout(() => {
        if (target.element && target.element.classList) {
          target.element.classList.remove("pg-chunk-focused-flash");
        }
      }, 1400);
    }

    // Update active file in state & file list
    if (target.file) {
      if (typeof updatePgActiveFile === "function") {
        updatePgActiveFile(target.clNum, target.file);
      }
      if (typeof focusPgFileRow === "function") {
        focusPgFileRow(target.clNum, target.file, { scrollIntoView: false });
      }
    }

    // Compute progress toast
    if (typeof showToast === "function") {
      const fileChunks = allChunks.filter(c => c.file === target.file);
      const fileIdx = fileChunks.indexOf(target);
      const shortFile = (target.file || "").split("/").pop() || target.file || "file";
      const totalFileCount = fileChunks.length || 1;
      const displayIdx = fileIdx >= 0 ? fileIdx + 1 : 1;

      if (target.type === "chunk") {
        const deltaSummary = target.changeType === "add"
          ? `+${target.lineCount}L`
          : (target.changeType === "del" ? `-${target.lineCount}L` : `modified ${target.lineCount}L`);
        showToast(`Diff chunk ${displayIdx}/${totalFileCount} in ${shortFile} (${deltaSummary}) · (n/p)`, 2200);
      } else {
        showToast(`Comment thread #${displayIdx}/${totalFileCount} on ${shortFile}:${target.line || 1} · (n/p)`, 2200);
      }
    }
  }

  function navigatePgUnresolvedThread(dir, clNum) {
    const changeView = document.querySelector(".pg-change-view");
    if (!changeView) return;

    // Find all unresolved comment elements in DOM
    const rawItems = Array.from(changeView.querySelectorAll(
      ".diff-inline-comment:not(.resolved):not(.agent-question):not(.draft-item), " +
      ".swarm-finding[data-thread-ui-id]"
    ));

    const targetElements = rawItems.filter(el => {
      if (el.classList.contains("agent-question") || el.classList.contains("draft-item") || el.classList.contains("resolved") || el.classList.contains("pg-author-hidden")) {
        return false;
      }
      return true;
    });

    if (targetElements.length === 0) {
      showToast("No unresolved comment threads on this change");
      return;
    }

    const container = document.getElementById("diffPane") || document.getElementById("diffScroll") || window;
    const viewTop = container.scrollTop !== undefined ? container.scrollTop : 0;
    const offsetThreshold = 50;

    let currentIndex = -1;
    for (let i = 0; i < targetElements.length; i++) {
      const top = targetElements[i].offsetTop || 0;
      if (top >= viewTop - offsetThreshold) {
        currentIndex = i;
        break;
      }
    }
    if (currentIndex === -1) currentIndex = (dir === "prev" ? targetElements.length - 1 : 0);

    let targetIndex = currentIndex;
    if (dir === "next") {
      const currTop = targetElements[currentIndex].offsetTop || 0;
      targetIndex = Math.min(
        targetElements.length - 1,
        currentIndex + (currTop <= viewTop + 20 ? 1 : 0)
      );
    } else if (dir === "prev") {
      targetIndex = Math.max(0, currentIndex - 1);
    }

    const targetEl = targetElements[targetIndex];
    if (targetEl) {
      // Auto-expand parent file diff block if collapsed
      const parentBlock = targetEl.closest(".pg-file-diff-block");
      const blockCl = Number((parentBlock && parentBlock.getAttribute("data-cl")) || state.selectedClNumber || 0);
      if (parentBlock && parentBlock.classList.contains("is-collapsed")) {
        const blockPs = Number(parentBlock.getAttribute("data-ps") || 1);
        const blockFile = parentBlock.getAttribute("data-file") || "";
        if (blockCl && blockFile && typeof setPgFileCollapsed === "function") {
          setPgFileCollapsed(blockCl, blockPs, blockFile, false);
        }
        parentBlock.classList.remove("is-collapsed");
        const codeTbl = parentBlock.querySelector(".code-table");
        if (codeTbl && codeTbl.style) codeTbl.style.display = "";
        const toggleBtn = parentBlock.querySelector(".pg-file-chevron, .file-toggle-btn");
        if (toggleBtn) toggleBtn.textContent = "▼";
      }

      // Auto-expand comment if it was collapsed
      if (targetEl.classList.contains("collapsed")) {
        const inlineId = targetEl.getAttribute("data-inline-id") || (targetEl.id || "").replace(/^inlineItem-/, "");
        if (blockCl && inlineId && typeof toggleInlineItemCollapse === "function") {
          toggleInlineItemCollapse(blockCl, inlineId);
        }
        targetEl.classList.remove("collapsed");
        const collapseBtn = targetEl.querySelector(".inline-collapse-btn");
        if (collapseBtn) collapseBtn.textContent = "Collapse";
      }

      if (typeof targetEl.scrollIntoView === "function") {
        targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
        targetEl.classList.remove("pg-unresolved-focused-flash");
        void targetEl.offsetWidth;
        targetEl.classList.add("pg-unresolved-focused-flash");
        setTimeout(() => {
          targetEl.classList.remove("pg-unresolved-focused-flash");
        }, 1400);
      }

      const authorSpan = targetEl.querySelector(".diff-inline-head-title");
      const authorText = authorSpan ? authorSpan.textContent.trim() : "";
      showToast(`Unresolved thread ${targetIndex + 1} of ${targetElements.length}${authorText ? ` · ${authorText}` : ""}`);
    }
  }

  function isPgShowResolvedCommentsEnabled() {
    try {
      const val = localStorage.getItem("gerrit_ui_pg_show_resolved_comments");
      return val !== "false";
    } catch (_) {
      return true;
    }
  }

  function setPgShowResolvedCommentsEnabled(enabled) {
    try {
      localStorage.setItem("gerrit_ui_pg_show_resolved_comments", enabled ? "true" : "false");
    } catch (_) {}
    state.showResolvedComments = enabled;
  }

  function togglePgShowResolvedComments(clNum) {
    const targetCl = clNum || state.selectedClNumber;
    const current = isPgShowResolvedCommentsEnabled();
    const next = !current;
    setPgShowResolvedCommentsEnabled(next);
    applyPgShowResolvedCommentsToDom(targetCl, next);
    showToast(next ? "Showing resolved comments" : "Hiding resolved comments");
  }

  function applyPgShowResolvedCommentsToDom(clNum, isShown) {
    const changeView = document.querySelector(".pg-change-view");
    if (!changeView) return;
    if (isShown) {
      changeView.classList.remove("pg-hide-resolved");
    } else {
      changeView.classList.add("pg-hide-resolved");
    }
    const targetId = clNum || state.selectedClNumber;
    const btn = document.getElementById(`btnToggleResolvedComments-${targetId}`);
    if (btn) {
      btn.classList.toggle("is-hiding-resolved", !isShown);
      const countAttr = btn.getAttribute("data-resolved-count") || "";
      const countSuffix = countAttr ? ` (${countAttr})` : "";
      btn.textContent = (isShown ? "Hide resolved" : "Show resolved") + countSuffix;
    }
  }

  function isPgShowRobotCommentsEnabled() {
    try {
      const val = localStorage.getItem("gerrit_ui_pg_show_robot_comments");
      return val !== "false";
    } catch (_) {
      return true;
    }
  }

  function setPgShowRobotCommentsEnabled(enabled) {
    try {
      localStorage.setItem("gerrit_ui_pg_show_robot_comments", enabled ? "true" : "false");
    } catch (_) {}
    state.showRobotComments = enabled;
  }

  function togglePgShowRobotComments(clNum) {
    const targetCl = clNum || state.selectedClNumber;
    const current = isPgShowRobotCommentsEnabled();
    const next = !current;
    setPgShowRobotCommentsEnabled(next);
    applyPgShowRobotCommentsToDom(targetCl, next);
    showToast(next ? "Showing robot comments (Shift+W)" : "Hiding robot comments (Shift+W)");
  }

  function applyPgShowRobotCommentsToDom(clNum, isShown) {
    const changeView = document.querySelector(".pg-change-view");
    if (!changeView) return;
    if (isShown) {
      changeView.classList.remove("pg-hide-robot-comments");
    } else {
      changeView.classList.add("pg-hide-robot-comments");
    }
    const targetId = clNum || state.selectedClNumber;
    const btn = document.getElementById(`btnToggleRobotComments-${targetId}`);
    if (btn) {
      btn.classList.toggle("is-hiding-robot", !isShown);
      const countAttr = btn.getAttribute("data-robot-count") || "";
      const countSuffix = countAttr ? ` (${countAttr})` : "";
      btn.textContent = (isShown ? "Hide robot" : "Show robot") + countSuffix;
    }
  }

  function syncPgRobotCommentsToggleBtn(clNum) {
    const targetId = clNum || state.selectedClNumber;
    const isShown = isPgShowRobotCommentsEnabled();
    applyPgShowRobotCommentsToDom(targetId, isShown);
  }

  function countPgRobotComments(clNum, bundle) {
    const num = Number(clNum || 0);
    const b = bundle || state.diffsByCl[num];
    const srvThreads = (b && Array.isArray(b.unresolved_threads)) ? b.unresolved_threads : [];
    let count = srvThreads.filter(t => isPgRobotComment(t)).length;
    const localComments = state.inlineCommentsByCl[num] || [];
    count += localComments.filter(c => isPgRobotComment(c)).length;
    return count;
  }

  function renderPgRobotCommentsChipHtml(clNum, bundle) {
    const count = countPgRobotComments(clNum, bundle);
    if (count === 0) return "";
    return `<button type="button" id="pgRobotCommentsChip-${clNum}" class="pg-robot-comments-chip" onclick="navigatePgRobotComment('next', ${clNum})" title="Jump to next robot comment (${count} automated finding(s)) · Shortcut: Shift+W">
      <span class="pg-robot-chip-icon" aria-hidden="true">🤖</span>
      <span class="pg-robot-chip-count">${count} robot</span>
    </button>`;
  }

  function navigatePgRobotComment(dir, clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;

    if (!isPgShowRobotCommentsEnabled()) {
      setPgShowRobotCommentsEnabled(true);
      applyPgShowRobotCommentsToDom(num, true);
    }

    const cards = Array.from(changeView.querySelectorAll(".diff-inline-comment.is-robot, .diff-inline-comment[data-is-robot='1']"));
    if (cards.length === 0) {
      showToast("No robot comments on this change");
      return;
    }

    let activeIdx = -1;
    const viewportCenter = window.innerHeight / 2;
    let closestDist = Infinity;
    cards.forEach((card, idx) => {
      const rect = card.getBoundingClientRect();
      const dist = Math.abs((rect.top + rect.height / 2) - viewportCenter);
      if (dist < closestDist) {
        closestDist = dist;
        activeIdx = idx;
      }
    });

    let targetIdx = 0;
    if (dir === "prev") {
      targetIdx = activeIdx > 0 ? activeIdx - 1 : cards.length - 1;
    } else {
      targetIdx = activeIdx >= 0 && activeIdx < cards.length - 1 ? activeIdx + 1 : 0;
    }

    const targetCard = cards[targetIdx];
    if (!targetCard) return;

    const parentDiff = targetCard.closest(".pg-file-diff-block");
    if (parentDiff && parentDiff.classList.contains("is-collapsed")) {
      const file = parentDiff.getAttribute("data-file");
      if (file && typeof uncollapsePgFileDiffBlock === "function") {
        uncollapsePgFileDiffBlock(num, file);
      }
    }

    targetCard.classList.remove("collapsed");
    targetCard.scrollIntoView({ behavior: "smooth", block: "center" });
    targetCard.classList.add("pg-robot-focused-flash");
    setTimeout(() => targetCard.classList.remove("pg-robot-focused-flash"), 1600);

    const robotName = targetCard.getAttribute("data-robot-id") || "Robot";
    showToast(`Robot comment ${targetIdx + 1} of ${cards.length}: ${robotName}`);
  }

  function onPgFileRobotCommentsClick(event, clNum, filePath) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!isPgShowRobotCommentsEnabled()) {
      setPgShowRobotCommentsEnabled(true);
      applyPgShowRobotCommentsToDom(num, true);
    }
    if (filePath === "/COMMIT_MSG") {
      if (typeof scrollToCommitMessageBox === "function") {
        scrollToCommitMessageBox(num);
      }
      return;
    }
    if (typeof uncollapsePgFileDiffBlock === "function") {
      uncollapsePgFileDiffBlock(num, filePath);
    }
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;
    const fileBlock = changeView.querySelector(`.pg-file-diff-block[data-file="${safeEscapeCss(filePath)}"]`);
    const robotEl = fileBlock
      ? (fileBlock.querySelector(".diff-inline-comment.is-robot") || fileBlock.querySelector(".diff-inline-comment[data-is-robot='1']"))
      : null;
    if (robotEl) {
      robotEl.scrollIntoView({ behavior: "smooth", block: "center" });
      robotEl.classList.remove("collapsed");
      robotEl.classList.add("pg-robot-focused-flash");
      setTimeout(() => robotEl.classList.remove("pg-robot-focused-flash"), 1600);
      showToast(`Jumped to robot comment in ${filePath}`);
    }
  }

  function getPgCommentsAuthorFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    return (state.commentsAuthorFilterByCl && state.commentsAuthorFilterByCl[num]) || "all";
  }

  function getPgCommentAuthors(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const comments = (typeof getAllPgCommentsForCl === "function") ? getAllPgCommentsForCl(num) : [];
    const map = new Map();
    comments.forEach(t => {
      const name = String(t.author_name || (t.is_robot ? (t.robot_id || "Robot") : "Reviewer")).trim();
      if (!name) return;
      if (!map.has(name)) {
        map.set(name, {
          name: name,
          email: t.author_email || "",
          isRobot: Boolean(t.is_robot || t.robot_id),
          total: 0,
          unresolvedCount: 0,
          draftCount: 0,
        });
      }
      const entry = map.get(name);
      entry.total++;
      if (t.is_draft) entry.draftCount++;
      if (t.unresolved && !t.is_draft) entry.unresolvedCount++;
    });
    return Array.from(map.values()).sort((a, b) => b.total - a.total);
  }

  function setPgCommentsAuthorFilter(clNum, authorName, showNotification = true) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return "all";
    state.commentsAuthorFilterByCl = state.commentsAuthorFilterByCl || {};
    const norm = (!authorName || authorName === "all") ? "all" : authorName;
    state.commentsAuthorFilterByCl[num] = norm;

    applyPgCommentsAuthorFilterToDom(num, norm);

    const commentsPane = document.getElementById(`pgCommentsTabPane-${num}`);
    if (commentsPane) {
      const c = (state.changes || []).find(x => Number(x.number) === num);
      const bundle = state.diffsByCl && state.diffsByCl[num];
      if (c && typeof renderPgCommentsViewHtml === "function") {
        commentsPane.innerHTML = renderPgCommentsViewHtml(c, bundle);
      }
    }

    if (showNotification && typeof showToast === "function") {
      if (norm === "all") {
        showToast("Showing comments from all authors");
      } else {
        const authors = getPgCommentAuthors(num);
        const match = authors.find(a => a.name === norm);
        const countStr = match ? ` (${match.total} comment${match.total === 1 ? "" : "s"})` : "";
        showToast(`Filtered comments: ${norm}${countStr} (Shift+Alt+C to cycle)`);
      }
    }
    return norm;
  }

  function clearPgCommentsAuthorFilter(clNum) {
    return setPgCommentsAuthorFilter(clNum, "all");
  }

  function cyclePgCommentsAuthorFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const authors = getPgCommentAuthors(num);
    if (authors.length <= 1) {
      if (typeof showToast === "function") {
        showToast(authors.length === 1 ? `Only one author (${authors[0].name}) commented on this change` : "No comments to filter on this change");
      }
      return "all";
    }
    const cur = getPgCommentsAuthorFilter(num);
    const list = ["all", ...authors.map(a => a.name)];
    const curIdx = list.indexOf(cur);
    const nextIdx = (curIdx + 1) % list.length;
    return setPgCommentsAuthorFilter(num, list[nextIdx]);
  }

  function togglePgDiffAuthorFilter(clNum, authorName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const cur = getPgCommentsAuthorFilter(num);
    if (cur === authorName) {
      return setPgCommentsAuthorFilter(num, "all");
    } else {
      return setPgCommentsAuthorFilter(num, authorName);
    }
  }

  function applyPgCommentsAuthorFilterToDom(clNum, authorName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const targetAuthor = authorName || getPgCommentsAuthorFilter(num);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (changeView && changeView.classList) {
      if (!targetAuthor || targetAuthor === "all") {
        changeView.classList.remove("pg-filter-author-active");
      } else {
        changeView.classList.add("pg-filter-author-active");
      }
    }
    const allCards = document.querySelectorAll ? document.querySelectorAll(`.diff-inline-comment[data-cl="${num}"], .diff-inline-comment`) : [];
    if (allCards && allCards.forEach) {
      allCards.forEach(card => {
        if (!card) return;
        const author = card.getAttribute("data-author") || "";
        const isMatch = (!targetAuthor || targetAuthor === "all") || (author === targetAuthor);
        if (card.classList) {
          card.classList.toggle("pg-author-hidden", !isMatch);
        }
      });
    }
    const btn = document.getElementById(`btnToggleAuthorComments-${num}`);
    if (btn) {
      const isFiltered = (targetAuthor && targetAuthor !== "all");
      if (btn.classList) btn.classList.toggle("is-filtering-author", isFiltered);
      btn.title = `Cycle comment author filter: ${isFiltered ? targetAuthor : "All authors"} (Shortcut: Shift+Alt+C)`;
      const label = btn.querySelector ? btn.querySelector(".pg-btn-author-label") : null;
      if (label) {
        label.textContent = isFiltered ? targetAuthor : "All authors";
      } else {
        btn.innerHTML = `<span class="pg-btn-icon">👤</span> <span class="pg-btn-author-label">${isFiltered ? escapeHtml(targetAuthor) : "All authors"}</span>`;
      }
    }
    const select = document.getElementById(`pgCommentsAuthorSelect-${num}`);
    if (select && select.value !== targetAuthor) {
      select.value = targetAuthor || "all";
    }
  }

  function areAllPgCommentsCollapsed(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    const cards = changeView ? Array.from(changeView.querySelectorAll(".diff-inline-comment")) : [];
    if (cards.length > 0) {
      return cards.every(el => el.classList.contains("collapsed"));
    }
    const bundle = state.diffsByCl[num];
    const srvThreads = (bundle && Array.isArray(bundle.unresolved_threads)) ? bundle.unresolved_threads : [];
    const localComments = state.inlineCommentsByCl[num] || [];
    const totalCount = srvThreads.length + localComments.length;
    if (totalCount === 0) return false;
    const srvCollapsedCount = srvThreads.filter((t, idx) => state.collapsedInlineIds.has(t._ui_item_id || `srv-thread-${num}-${idx}`)).length;
    const localCollapsedCount = localComments.filter(c => Boolean(c.collapsed)).length;
    return (srvCollapsedCount + localCollapsedCount) === totalCount;
  }

  function togglePgAllCommentsCollapse(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;

    const cards = Array.from(changeView.querySelectorAll(".diff-inline-comment"));
    if (cards.length === 0) {
      if (typeof showToast === "function") showToast("No comment threads on this change");
      return;
    }

    const anyCollapsed = cards.some(el => el.classList.contains("collapsed"));
    const shouldExpand = anyCollapsed;

    cards.forEach(card => {
      const idAttr = card.getAttribute("data-inline-id") || "";
      if (shouldExpand) {
        card.classList.remove("collapsed");
        const btn = card.querySelector(".inline-collapse-btn");
        if (btn) btn.textContent = "Hide ▴";
        if (idAttr) {
          if (idAttr.startsWith("srv-thread-")) {
            state.collapsedInlineIds.delete(idAttr);
          } else {
            const it = findInlineItemById(num, idAttr);
            if (it) it.collapsed = false;
          }
        }
      } else {
        card.classList.add("collapsed");
        const btn = card.querySelector(".inline-collapse-btn");
        if (btn) btn.textContent = "Show ▾";
        if (idAttr) {
          if (idAttr.startsWith("srv-thread-")) {
            state.collapsedInlineIds.add(idAttr);
          } else {
            const it = findInlineItemById(num, idAttr);
            if (it) it.collapsed = true;
          }
        }
      }
    });

    syncPgAllCommentsCollapseButton(num);

    const actionWord = shouldExpand ? "Expanded" : "Collapsed";
    if (typeof showToast === "function") {
      showToast(`✓ ${actionWord} all ${cards.length} comments (Shift+E)`);
    }
  }

  function syncPgAllCommentsCollapseButton(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const btn = document.getElementById(`btnToggleAllCommentsCollapse-${num}`);
    if (!btn) return;

    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    const cards = changeView ? Array.from(changeView.querySelectorAll(".diff-inline-comment")) : [];
    if (cards.length === 0) {
      btn.style.display = "none";
      return;
    }
    btn.style.display = "";

    const allCollapsed = cards.length > 0 && cards.every(el => el.classList.contains("collapsed"));
    if (allCollapsed) {
      btn.innerHTML = `<span class="pg-btn-icon">⤢</span> Expand all`;
      btn.title = "Expand all comment threads across change (Shortcut: Shift+E)";
      btn.classList.add("is-all-collapsed");
      btn.classList.remove("is-all-expanded");
    } else {
      btn.innerHTML = `<span class="pg-btn-icon">⤡</span> Collapse all`;
      btn.title = "Collapse all comment threads across change (Shortcut: Shift+E)";
      btn.classList.remove("is-all-collapsed");
      btn.classList.add("is-all-expanded");
    }
  }

  function onPgFileCommentsClick(event, clNum, filePath) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num || !filePath) return;
    const baseName = filePath.split("/").pop() || filePath;

    if (filePath === "/COMMIT_MSG") {
      uncollapseCommitMessage(num, true);
      const msgBox = document.getElementById(`commitMsgPanel-${num}`) || document.querySelector(`.commit-msg-table[data-cl="${num}"]`);
      if (msgBox) {
        if (typeof msgBox.scrollIntoView === "function") {
          msgBox.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        const commentEls = Array.from(msgBox.querySelectorAll(".diff-inline-comment"));
        if (commentEls.length > 0) {
          state.activeCommentIndexByFile = state.activeCommentIndexByFile || {};
          const key = `${num}:/COMMIT_MSG:all`;
          const lastIdx = state.activeCommentIndexByFile[key] != null ? state.activeCommentIndexByFile[key] : -1;
          const targetIdx = (lastIdx + 1) % commentEls.length;
          state.activeCommentIndexByFile[key] = targetIdx;
          const commentEl = commentEls[targetIdx];
          if (commentEl.classList.contains("collapsed")) {
            const idAttr = commentEl.getAttribute("data-inline-id");
            if (idAttr) toggleInlineItemCollapse(num, idAttr);
          }
          commentEl.classList.remove("pg-unresolved-focused");
          void commentEl.offsetWidth;
          commentEl.classList.add("pg-unresolved-focused");
          setTimeout(() => commentEl.classList.remove("pg-unresolved-focused"), 2200);
          if (typeof showToast === "function") {
            const countMsg = commentEls.length > 1 ? ` (${targetIdx + 1} of ${commentEls.length})` : "";
            showToast(`Jumped to comment in Commit Message${countMsg}`);
          }
          return;
        }
      }
      if (typeof showToast === "function") showToast("Jumped to Commit Message comments");
      return;
    }

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const curPs = Number(selPsMeta.ps || 1);
    if (typeof setPgFileCollapsed === "function") {
      setPgFileCollapsed(num, curPs, filePath, false);
    }

    const safeFile = filePath.replace(/["\\]/g, "\\$&");
    const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${safeFile}"]`) ||
                      document.querySelector(`.pg-file-diff-block[data-file="${safeFile}"]`);
    if (fileBlock) {
      fileBlock.classList.remove("is-collapsed");
      const codeTbl = fileBlock.querySelector(".code-table");
      if (codeTbl && codeTbl.style) codeTbl.style.display = "";
      const chevron = fileBlock.querySelector(".pg-file-chevron, .file-toggle-btn");
      if (chevron) chevron.textContent = "▼";
    }

    let commentEls = fileBlock
      ? Array.from(fileBlock.querySelectorAll(".diff-inline-comment"))
      : [];

    if (commentEls.length === 0) {
      const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
      if (changeView) {
        commentEls = Array.from(changeView.querySelectorAll(`.diff-inline-comment[data-file="${safeFile}"]`));
        if (commentEls.length === 0) {
          commentEls = Array.from(changeView.querySelectorAll(".diff-inline-comment"));
        }
      }
    }

    if (commentEls.length === 0) {
      if (fileBlock && typeof fileBlock.scrollIntoView === "function") {
        fileBlock.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      return;
    }

    state.activeCommentIndexByFile = state.activeCommentIndexByFile || {};
    const key = `${num}:${filePath}:all`;
    const lastIdx = state.activeCommentIndexByFile[key] != null ? state.activeCommentIndexByFile[key] : -1;
    const targetIdx = (lastIdx + 1) % commentEls.length;
    state.activeCommentIndexByFile[key] = targetIdx;

    const targetComment = commentEls[targetIdx];
    if (targetComment.classList.contains("collapsed")) {
      targetComment.classList.remove("collapsed");
      const btn = targetComment.querySelector(".inline-collapse-btn");
      if (btn) btn.textContent = "Hide ▴";
      const idAttr = targetComment.getAttribute("data-inline-id");
      if (idAttr) {
        if (idAttr.startsWith("srv-thread-")) {
          state.collapsedInlineIds.delete(idAttr);
        } else if (typeof findInlineItemById === "function") {
          const it = findInlineItemById(num, idAttr);
          if (it) it.collapsed = false;
        }
        if (typeof syncPgAllCommentsCollapseButton === "function") {
          syncPgAllCommentsCollapseButton(num);
        }
      }
    }

    if (typeof targetComment.scrollIntoView === "function") {
      targetComment.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    targetComment.classList.remove("pg-unresolved-focused");
    void targetComment.offsetWidth;
    targetComment.classList.add("pg-unresolved-focused");
    setTimeout(() => targetComment.classList.remove("pg-unresolved-focused"), 2200);

    if (typeof showToast === "function") {
      const countMsg = commentEls.length > 1 ? ` (${targetIdx + 1} of ${commentEls.length})` : "";
      showToast(`Jumped to comment in ${baseName}${countMsg}`);
    }
  }

  function onPgFileUnresolvedCommentsClick(event, clNum, filePath) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num || !filePath) return;
    const baseName = filePath.split("/").pop() || filePath;

    if (filePath === "/COMMIT_MSG") {
      uncollapseCommitMessage(num, true);
      const msgBox = document.getElementById(`commitMsgPanel-${num}`) || document.querySelector(`.commit-msg-table[data-cl="${num}"]`);
      if (msgBox) {
        if (typeof msgBox.scrollIntoView === "function") {
          msgBox.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        const commentEls = Array.from(msgBox.querySelectorAll(".diff-inline-comment:not(.resolved)") || msgBox.querySelectorAll(".diff-inline-comment"));
        if (commentEls.length > 0) {
          state.activeCommentIndexByFile = state.activeCommentIndexByFile || {};
          const key = `${num}:/COMMIT_MSG:unresolved`;
          const lastIdx = state.activeCommentIndexByFile[key] != null ? state.activeCommentIndexByFile[key] : -1;
          const targetIdx = (lastIdx + 1) % commentEls.length;
          state.activeCommentIndexByFile[key] = targetIdx;
          const commentEl = commentEls[targetIdx];
          if (commentEl.classList.contains("collapsed")) {
            const idAttr = commentEl.getAttribute("data-inline-id");
            if (idAttr) toggleInlineItemCollapse(num, idAttr);
          }
          commentEl.classList.remove("pg-unresolved-focused");
          void commentEl.offsetWidth;
          commentEl.classList.add("pg-unresolved-focused");
          setTimeout(() => commentEl.classList.remove("pg-unresolved-focused"), 2200);
          if (typeof showToast === "function") {
            const countMsg = commentEls.length > 1 ? ` (${targetIdx + 1} of ${commentEls.length})` : "";
            showToast(`Jumped to unresolved comment in Commit Message${countMsg}`);
          }
          return;
        }
      }
      if (typeof showToast === "function") showToast("Jumped to unresolved comment in Commit Message");
      return;
    }

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const curPs = Number(selPsMeta.ps || 1);
    if (typeof setPgFileCollapsed === "function") {
      setPgFileCollapsed(num, curPs, filePath, false);
    }

    const safeFile = filePath.replace(/["\\]/g, "\\$&");
    const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${safeFile}"]`) ||
                      document.querySelector(`.pg-file-diff-block[data-file="${safeFile}"]`);
    if (fileBlock) {
      fileBlock.classList.remove("is-collapsed");
      const codeTbl = fileBlock.querySelector(".code-table");
      if (codeTbl && codeTbl.style) codeTbl.style.display = "";
      const chevron = fileBlock.querySelector(".pg-file-chevron, .file-toggle-btn");
      if (chevron) chevron.textContent = "▼";
    }

    let commentEls = fileBlock
      ? Array.from(fileBlock.querySelectorAll(".diff-inline-comment:not(.resolved):not(.agent-question)"))
      : [];
    if (commentEls.length === 0 && fileBlock) {
      commentEls = Array.from(fileBlock.querySelectorAll(".diff-inline-comment:not(.resolved)"));
    }
    if (commentEls.length === 0 && fileBlock) {
      commentEls = Array.from(fileBlock.querySelectorAll(".diff-inline-comment"));
    }
    if (commentEls.length === 0) {
      const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
      if (changeView) {
        commentEls = Array.from(changeView.querySelectorAll(`.diff-inline-comment[data-file="${safeFile}"]:not(.resolved)`));
        if (commentEls.length === 0) {
          commentEls = Array.from(changeView.querySelectorAll(".diff-inline-comment:not(.resolved)"));
        }
      }
    }

    if (commentEls.length === 0) {
      if (fileBlock && typeof fileBlock.scrollIntoView === "function") {
        fileBlock.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      return;
    }

    state.activeCommentIndexByFile = state.activeCommentIndexByFile || {};
    const key = `${num}:${filePath}:unresolved`;
    const lastIdx = state.activeCommentIndexByFile[key] != null ? state.activeCommentIndexByFile[key] : -1;
    const targetIdx = (lastIdx + 1) % commentEls.length;
    state.activeCommentIndexByFile[key] = targetIdx;

    const targetComment = commentEls[targetIdx];
    if (targetComment.classList.contains("collapsed")) {
      targetComment.classList.remove("collapsed");
      const btn = targetComment.querySelector(".inline-collapse-btn");
      if (btn) btn.textContent = "Hide ▴";
      const idAttr = targetComment.getAttribute("data-inline-id");
      if (idAttr) {
        if (idAttr.startsWith("srv-thread-")) {
          state.collapsedInlineIds.delete(idAttr);
        } else if (typeof findInlineItemById === "function") {
          const it = findInlineItemById(num, idAttr);
          if (it) it.collapsed = false;
        }
        if (typeof syncPgAllCommentsCollapseButton === "function") {
          syncPgAllCommentsCollapseButton(num);
        }
      }
    }

    if (typeof targetComment.scrollIntoView === "function") {
      targetComment.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    targetComment.classList.remove("pg-unresolved-focused");
    void targetComment.offsetWidth;
    targetComment.classList.add("pg-unresolved-focused");
    setTimeout(() => targetComment.classList.remove("pg-unresolved-focused"), 2200);

    if (typeof showToast === "function") {
      const countMsg = commentEls.length > 1 ? ` (${targetIdx + 1} of ${commentEls.length})` : "";
      showToast(`Jumped to unresolved comment in ${baseName}${countMsg}`);
    }
  }

  function onPgFileDraftCommentsClick(event, clNum, filePath) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num || !filePath) return;
    const baseName = filePath.split("/").pop() || filePath;

    if (filePath === "/COMMIT_MSG") {
      uncollapseCommitMessage(num, true);
    } else {
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
      const curPs = Number(selPsMeta.ps || 1);
      if (typeof setPgFileCollapsed === "function") {
        setPgFileCollapsed(num, curPs, filePath, false);
      }
      const safeFile = filePath.replace(/["\\]/g, "\\$&");
      const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${safeFile}"]`) ||
                        document.querySelector(`.pg-file-diff-block[data-file="${safeFile}"]`);
      if (fileBlock) {
        fileBlock.classList.remove("is-collapsed");
        const codeTbl = fileBlock.querySelector(".code-table");
        if (codeTbl && codeTbl.style) codeTbl.style.display = "";
        const chevron = fileBlock.querySelector(".pg-file-chevron, .file-toggle-btn");
        if (chevron) chevron.textContent = "▼";
      }
    }

    const fileDrafts = (typeof getPendingDraftsForFile === "function") ? getPendingDraftsForFile(num, filePath) : [];
    if (fileDrafts.length > 0) {
      state.activeCommentIndexByFile = state.activeCommentIndexByFile || {};
      const key = `${num}:${filePath}:draft`;
      const lastIdx = state.activeCommentIndexByFile[key] != null ? state.activeCommentIndexByFile[key] : -1;
      const targetIdx = (lastIdx + 1) % fileDrafts.length;
      state.activeCommentIndexByFile[key] = targetIdx;

      const targetDraft = fileDrafts[targetIdx];
      if (targetDraft.kind === "draft") {
        if (typeof reopenInlineBlockInComposer === "function") {
          reopenInlineBlockInComposer(num, targetDraft.id, targetDraft.draft_mode === "ask" ? "ask" : "comment", true);
        }
      } else if (targetDraft.kind === "reply") {
        if (typeof openInlineReplyComposer === "function") {
          openInlineReplyComposer(num, targetDraft.id);
        }
      }
      const el = document.getElementById(`inlineItem-${targetDraft.id}`) || document.getElementById(`inlineDraft-${targetDraft.id}`);
      if (el) {
        if (typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        el.classList.remove("pg-chunk-focused-flash");
        void el.offsetWidth;
        el.classList.add("pg-chunk-focused-flash");
        setTimeout(() => el.classList.remove("pg-chunk-focused-flash"), 1800);
      }
      if (typeof showToast === "function") {
        const countMsg = fileDrafts.length > 1 ? ` (${targetIdx + 1} of ${fileDrafts.length})` : "";
        showToast(`Jumped to draft in ${baseName}${countMsg} (Shift+D)`);
      }
    } else {
      onPgFileCommentsClick(event, num, filePath);
    }
  }

  const _PG_EMPTY_FILE_COMMENT_STATS = Object.freeze({
    total: 0,
    commentCount: 0,
    humanCount: 0,
    unresolvedCount: 0,
    resolvedCount: 0,
    draftCount: 0,
    robotCount: 0,
    qaCount: 0,
    hasComments: false,
    hasUnresolved: false,
    hasDrafts: false,
    hasResolved: false,
    hasRobot: false,
    hasQa: false,
    unresolvedComments: _PG_EMPTY_ARRAY,
    resolvedComments: _PG_EMPTY_ARRAY,
    drafts: _PG_EMPTY_ARRAY,
    robotComments: _PG_EMPTY_ARRAY,
    agentQuestions: _PG_EMPTY_ARRAY,
  });

  function getPgFileCommentStats(clNum, filePath) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const targetPath = String(filePath || "");
    const allComments = (typeof getInlineCommentsForFile === "function")
      ? getInlineCommentsForFile(num, targetPath)
      : _PG_EMPTY_ARRAY;
    if (allComments.length === 0) {
      const draftsOnly = (typeof getPendingDraftsForFile === "function")
        ? getPendingDraftsForFile(num, targetPath)
        : _PG_EMPTY_ARRAY;
      if (draftsOnly.length === 0) {
        return _PG_EMPTY_FILE_COMMENT_STATS;
      }
    }
    const robotComments = allComments.filter(isPgRobotComment);
    const nonRobot = allComments.filter(cm => !isPgRobotComment(cm));

    // Staged drafts (saved inline drafts or open reply composer drafts)
    const drafts = (typeof getPendingDraftsForFile === "function")
      ? getPendingDraftsForFile(num, targetPath)
      : nonRobot.filter(cm => cm.kind === "draft");

    // Agent Q&A threads
    const agentQuestions = nonRobot.filter(cm => cm.kind === "agent_question");

    // Published human comments (exclude drafts and agent Q&A so counts are distinct)
    const publishedHuman = nonRobot.filter(cm => cm.kind !== "draft" && cm.kind !== "agent_question");
    const unresolvedComments = publishedHuman.filter(cm => cm.unresolved !== false);
    const resolvedComments = publishedHuman.filter(cm => cm.unresolved === false);
    const commentCount = publishedHuman.length + drafts.length + robotComments.length + agentQuestions.length;

    return {
      total: allComments.length,
      commentCount,
      humanCount: publishedHuman.length,
      unresolvedCount: unresolvedComments.length,
      resolvedCount: resolvedComments.length,
      draftCount: drafts.length,
      robotCount: robotComments.length,
      qaCount: agentQuestions.length,
      hasComments: commentCount > 0 || allComments.length > 0 || drafts.length > 0,
      hasUnresolved: unresolvedComments.length > 0,
      hasDrafts: drafts.length > 0,
      hasResolved: resolvedComments.length > 0,
      hasRobot: robotComments.length > 0,
      hasQa: agentQuestions.length > 0,
      unresolvedComments,
      resolvedComments,
      drafts,
      robotComments,
      agentQuestions,
    };
  }

  function renderPgFileCommentsBadgesHtml(c, filePath, precomputedStats = null) {
    const num = Number(c && c.number ? c.number : (state.selectedClNumber || 0));
    const stats = precomputedStats || getPgFileCommentStats(num, filePath);
    if (!stats || !stats.hasComments) return "";
    const safeFile = escapeHtml(filePath).replace(/'/g, "&#39;");
    const badges = [];

    // 1. Pending drafts badge
    if (stats.draftCount > 0) {
      const draftLabel = stats.draftCount === 1 ? "1 draft" : `${stats.draftCount} drafts`;
      badges.push(`<span class="pg-file-draft-badge pg-file-drafts-badge is-clickable" onclick="onPgFileDraftCommentsClick(event, ${num}, '${safeFile}')" title="${stats.draftCount} draft comment(s) in this file — click to jump to draft (Shift+D)" role="button" tabindex="0">📝 ${draftLabel}</span>`);
    }

    // 2. Unresolved comments badge
    if (stats.unresolvedCount > 0) {
      badges.push(`<span class="pg-file-comments-badge is-clickable pg-file-unresolved-badge has-unresolved" onclick="onPgFileUnresolvedCommentsClick(event, ${num}, '${safeFile}')" title="${stats.unresolvedCount} unresolved comment(s) in this file — click to jump to first unresolved (Shift+E to toggle all)" role="button" tabindex="0">⚠️ ${stats.unresolvedCount} unresolved</span>`);
      if (stats.resolvedCount > 0) {
        badges.push(`<span class="pg-file-comments-badge is-clickable pg-file-resolved-pill" onclick="onPgFileCommentsClick(event, ${num}, '${safeFile}')" title="${stats.resolvedCount} resolved comment(s) in this file — click to jump" role="button" tabindex="0">✓ ${stats.resolvedCount}</span>`);
      }
    } else if (stats.resolvedCount > 0) {
      // 3. All human comments are resolved
      badges.push(`<span class="pg-file-comments-badge is-clickable pg-file-resolved-badge all-resolved" onclick="onPgFileCommentsClick(event, ${num}, '${safeFile}')" title="${stats.resolvedCount} comment(s) in this file (all resolved) — click to jump" role="button" tabindex="0">✓ ${stats.resolvedCount} resolved</span>`);
    }

    // 4. Robot comments badge
    if (stats.robotCount > 0) {
      badges.push(`<span class="pg-file-robot-badge is-clickable" onclick="onPgFileRobotCommentsClick(event, ${num}, '${safeFile}')" title="${stats.robotCount} robot comment(s) in this file — click to jump (Shift+W to toggle)" role="button" tabindex="0">🤖 ${stats.robotCount}</span>`);
    }

    // 5. Shared Agent Q&A threads badge
    if (stats.qaCount > 0) {
      badges.push(`<span class="pg-file-comments-badge is-clickable pg-file-qa-badge" onclick="onPgFileCommentsClick(event, ${num}, '${safeFile}')" title="${stats.qaCount} Agent Q&amp;A thread(s) in this file — click to jump" role="button" tabindex="0">🤖 ${stats.qaCount} Q&amp;A</span>`);
    }

    if (badges.length === 0) return "";
    return `<div class="pg-file-comments-cell">${badges.join("")}</div>`;
  }

  function getPgFileChecksStatus(c, fileObjOrPath) {
    const num = Number(c && c.number ? c.number : (state.selectedClNumber || 0));
    const filePath = typeof fileObjOrPath === "string" ? fileObjOrPath : (fileObjOrPath && fileObjOrPath.file ? fileObjOrPath.file : "");
    if (!filePath || filePath === "/COMMIT_MSG" || filePath === "/MERGE_LIST") {
      return {
        hasChecks: false,
        status: "",
        totalCount: 0,
        failedCount: 0,
        warningCount: 0,
        runningCount: 0,
        passedCount: 0,
        targetName: "",
        primaryTarget: null,
        targets: [],
        filePath,
      };
    }

    const matched = [];

    // 1. Check explicit checks on the file object or change bundle findings
    const fileObj = typeof fileObjOrPath === "object" ? fileObjOrPath : null;
    if (fileObj && Array.isArray(fileObj.checks)) {
      fileObj.checks.forEach(chk => {
        matched.push({
          name: chk.name || chk.target || chk.check_name || "Check",
          status: String(chk.status || (chk.category === "ERROR" ? "failed" : (chk.category === "WARNING" ? "warning" : "passed"))).toLowerCase(),
          kind: chk.kind || "CHECK",
          subtitle: chk.message || chk.summary || "",
        });
      });
    }

    // 2. Query targets from TreeHugger presubmit runs cache for this CL
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && Array.isArray(entry.data.targets)) ? entry.data.targets : [];

    if (targets.length > 0) {
      const lowerPath = filePath.toLowerCase();
      const slashParts = lowerPath.split("/").filter(Boolean);
      const baseName = slashParts[slashParts.length - 1] || "";
      const dotIdx = baseName.lastIndexOf(".");
      const stem = dotIdx > 0 ? baseName.substring(0, dotIdx) : baseName;

      targets.forEach(t => {
        if (!t || !t.name) return;
        const tName = String(t.name);
        const lowerTName = tName.toLowerCase();

        // Exact file or affected_files list match
        if (t.file && String(t.file).toLowerCase() === lowerPath) {
          matched.push(t);
          return;
        }
        if (Array.isArray(t.affected_files) && t.affected_files.some(af => String(af).toLowerCase() === lowerPath)) {
          matched.push(t);
          return;
        }

        // Stem matching (e.g. "gerrit_ui_test" matches "tools/emu-dev-cli/tests/gerrit_ui_test.py")
        if (stem.length >= 3 && (lowerTName.includes(stem) || stem.includes(lowerTName.replace(/^[^:]*:/, "")))) {
          matched.push(t);
          return;
        }

        // Target package path matching (e.g. "//emulator/libs/file:..." matches "emulator/libs/file/...")
        const pkgMatch = lowerTName.match(/^\/{0,2}([\w\/\-\.]+):/);
        if (pkgMatch && pkgMatch[1]) {
          const pkg = pkgMatch[1].replace(/^@[\w\-\.]+\/\//, "");
          if (pkg.length >= 4 && lowerPath.includes(pkg)) {
            matched.push(t);
            return;
          }
        }

        // Direct directory prefix matching (e.g. "goldfish_presubmit" matches files starting with "goldfish/")
        if (slashParts.length > 1 && slashParts[0].length >= 3 && lowerTName.includes(slashParts[0])) {
          matched.push(t);
          return;
        }
      });
    }

    if (matched.length === 0) {
      return {
        hasChecks: false,
        status: "",
        totalCount: 0,
        failedCount: 0,
        warningCount: 0,
        runningCount: 0,
        passedCount: 0,
        targetName: "",
        primaryTarget: null,
        targets: [],
        filePath,
      };
    }

    let failedCount = 0;
    let warningCount = 0;
    let runningCount = 0;
    let passedCount = 0;
    let worstTarget = null;

    matched.forEach(t => {
      const st = String(t.status || "").toLowerCase();
      if (st === "failed") {
        failedCount++;
        if (!worstTarget || worstTarget.status !== "failed") worstTarget = t;
      } else if (st === "warning") {
        warningCount++;
        if (!worstTarget || (worstTarget.status !== "failed" && worstTarget.status !== "warning")) worstTarget = t;
      } else if (st === "running" || st === "queued") {
        runningCount++;
        if (!worstTarget || (worstTarget.status !== "failed" && worstTarget.status !== "warning" && worstTarget.status !== "running")) worstTarget = t;
      } else {
        passedCount++;
        if (!worstTarget) worstTarget = t;
      }
    });

    const status = failedCount > 0 ? "failed" : (warningCount > 0 ? "warning" : (runningCount > 0 ? "running" : "passed"));
    const targetName = worstTarget ? worstTarget.name : "";

    return {
      hasChecks: true,
      status,
      totalCount: matched.length,
      failedCount,
      warningCount,
      runningCount,
      passedCount,
      targetName,
      primaryTarget: worstTarget,
      targets: matched,
      filePath,
    };
  }

  function formatPgTargetShortName(name) {
    if (!name) return "";
    let str = String(name).trim();
    if (str.includes(":")) {
      const colonIdx = str.lastIndexOf(":");
      str = str.substring(colonIdx + 1);
    }
    if (str.length > 20) {
      str = str.substring(0, 18) + "…";
    }
    return str;
  }

  function renderPgFileChecksChipHtml(c, fileObjOrPath) {
    const num = Number(c && c.number ? c.number : (state.selectedClNumber || 0));
    const info = getPgFileChecksStatus(c, fileObjOrPath);
    if (!info || !info.hasChecks) return "";
    const safeFile = escapeHtml(info.filePath).replace(/'/g, "&#39;");
    const targetName = info.targetName || "";
    const safeTarget = escapeHtml(targetName).replace(/'/g, "&#39;");
    const status = info.status || "passed";

    let icon = "✓";
    let label = "pass";
    if (status === "failed") {
      icon = "✕";
      label = info.failedCount > 1 ? `${info.failedCount} fail` : (targetName ? formatPgTargetShortName(targetName) : "fail");
    } else if (status === "warning") {
      icon = "⚠";
      label = info.warningCount > 1 ? `${info.warningCount} warn` : (targetName ? formatPgTargetShortName(targetName) : "warn");
    } else if (status === "running") {
      icon = "◷";
      label = targetName ? formatPgTargetShortName(targetName) : "running";
    } else if (status === "passed") {
      icon = "✓";
      label = targetName ? formatPgTargetShortName(targetName) : "pass";
    }

    const tooltip = `TreeHugger presubmit: ${escapeHtml(targetName || "Target")} (${status}) — click to view in Checks tab`;
    const spinCls = status === "running" ? " pg-checks-spin" : "";

    return `<button type="button" class="pg-file-checks-chip st-${status}" onclick="onPgFileChecksChipClick(event, ${num}, '${safeFile}', '${safeTarget}')" title="${tooltip}"><span class="pg-checks-chip-icon${spinCls}">${icon}</span><span class="pg-checks-chip-text">${escapeHtml(label)}</span></button>`;
  }

  function onPgFileChecksChipClick(event, clNum, filePath, targetName) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;

    if (typeof selectPgChangeTab === "function") {
      selectPgChangeTab(num, "checks");
    }

    if (targetName) {
      state.selectedCheckRunByCl = state.selectedCheckRunByCl || {};
      state.selectedCheckRunByCl[num] = targetName;
      if (typeof rerenderPgChecksViewDom === "function") {
        rerenderPgChecksViewDom(num);
      }
      setTimeout(() => {
        const itemEl = document.querySelector(`.pg-checks-run-item.active[data-run-name="${escapeHtml(targetName)}"]`) ||
                       document.querySelector(`.pg-checks-run-item[data-run-name="${escapeHtml(targetName)}"]`);
        if (itemEl && typeof itemEl.scrollIntoView === "function") {
          itemEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }, 80);
    }

    const displayTarget = targetName ? `"${targetName}"` : "presubmit";
    if (typeof showToast === "function") {
      showToast(`Jumped to TreeHugger check ${displayTarget} in Checks tab ✓`);
    }
  }

  function selectPgChecksStatusFilter(clNum, statusFilter) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (typeof selectPgChangeTab === "function") {
      selectPgChangeTab(num, "checks");
    }
    if (statusFilter && typeof setPresubmitTargetFilter === "function") {
      setPresubmitTargetFilter(num, statusFilter);
    }
    const pane = document.getElementById(`pgChecksTabPane-${num}`);
    if (pane && typeof pane.scrollIntoView === "function") {
      pane.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (typeof showToast === "function") {
      showToast(`Filtered Checks tab to ${statusFilter} runs ✓`);
    }
  }

  function buildPgPatchsetRangeContext(c, bundle) {
    const clNum = Number(c.number);
    const sel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[clNum]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(clNum) : null) || {};
    const currentTarget = Number(sel.ps || (bundle && bundle.patch_set) || c.patch_set || 1);
    const currentBase = String(sel.base || (bundle && bundle.base_patch_set) || "base");
    const rawList = Array.isArray(c.patch_sets) && c.patch_sets.length > 0
      ? c.patch_sets
      : Array.from({ length: Math.max(1, Number(c.patch_set || 1)) }, (_, i) => i + 1);
    const allPs = Array.from(new Set(rawList.map(Number).filter(n => n > 0))).sort((a, b) => a - b);
    const maxPs = allPs.length ? allPs[allPs.length - 1] : 1;

    const psDetailsMap = {};
    if (Array.isArray(c.patchset_details)) {
      c.patchset_details.forEach(d => {
        if (d && d.number) psDetailsMap[Number(d.number)] = d;
      });
    }

    function getPsMeta(p) {
      const detail = psDetailsMap[p] || {};
      const desc = (detail.description || "").trim();
      const uploader = (detail.uploader || "").trim();
      const created = (detail.created || "").trim();

      let tooltip = `Patchset ${p}`;
      if (desc) tooltip += `: ${desc}`;
      if (uploader) tooltip += ` · by ${uploader}`;
      if (created) tooltip += ` · ${created}`;

      let label = `Patchset ${p}`;
      if (desc) {
        const shortDesc = desc.length > 28 ? desc.slice(0, 26) + "…" : desc;
        label += `: ${shortDesc}`;
      }
      return { tooltip, label, desc, uploader, created };
    }

    return { clNum, currentTarget, currentBase, allPs, maxPs, psDetailsMap, getPsMeta };
  }

  function renderPgPatchsetSelectorHtml(c, bundle) {
    if (!c || !c.number) return "";
    const clNum = Number(c.number);
    const isMenuOpen = state.patchRangeMenuCl === clNum;
    const selEntry = state.selectedPatchsetByCl && state.selectedPatchsetByCl[clNum];
    const selPsVal = selEntry ? (selEntry.ps || 0) : 0;
    const selBaseVal = selEntry ? (selEntry.base || "") : "";
    const bPs = bundle ? (bundle.patch_set || 0) : 0;
    const bBase = bundle ? (bundle.base_patch_set || "") : "";
    const psDetails = Array.isArray(c.patchset_details) ? c.patchset_details : null;
    const psDetailsLen = psDetails ? psDetails.length : 0;
    const lastDetailDesc = (psDetails && psDetailsLen > 0 && psDetails[psDetailsLen - 1])
      ? (psDetails[psDetailsLen - 1].description || "")
      : "";
    const psSelSub = c._psSelSub;
    if (
      psSelSub &&
      typeof c._cachedPatchsetSelectorHtml === "string" &&
      psSelSub.clNum === clNum &&
      psSelSub.cPs === c.patch_set &&
      psSelSub.branch === c.branch &&
      psSelSub.bPs === bPs &&
      psSelSub.bBase === bBase &&
      psSelSub.selPs === selPsVal &&
      psSelSub.selBase === selBaseVal &&
      psSelSub.menuOpen === isMenuOpen &&
      psSelSub.detailsRef === psDetails &&
      psSelSub.detailsLen === psDetailsLen &&
      psSelSub.lastDesc === lastDetailDesc
    ) {
      return c._cachedPatchsetSelectorHtml;
    }
    const rangeCtx = buildPgPatchsetRangeContext(c, bundle);
    const { currentTarget, currentBase, allPs, maxPs, getPsMeta } = rangeCtx;
    const detailsSig = psDetails
      ? psDetails.map(d => d ? `${d.number}:${d.description || ""}:${d.uploader || ""}:${d.created || ""}` : "").join(";")
      : "";
    const cacheKey = `${clNum}|${c.branch || ""}|${currentTarget}|${currentBase}|${allPs.join(",")}|${isMenuOpen ? 1 : 0}|${detailsSig}`;
    if (c._cachedPatchsetSelectorKey === cacheKey && typeof c._cachedPatchsetSelectorHtml === "string") {
      const wSub = c._psSelSub || (c._psSelSub = {});
      wSub.clNum = clNum;
      wSub.cPs = c.patch_set;
      wSub.branch = c.branch;
      wSub.bPs = bPs;
      wSub.bBase = bBase;
      wSub.selPs = selPsVal;
      wSub.selBase = selBaseVal;
      wSub.menuOpen = isMenuOpen;
      wSub.detailsRef = psDetails;
      wSub.detailsLen = psDetailsLen;
      wSub.lastDesc = lastDetailDesc;
      return c._cachedPatchsetSelectorHtml;
    }

    const branchName = c.branch || "";
    const baseTitle = branchName ? `Base of change on branch ${branchName}` : "Base of change";
    const baseLabel = branchName ? `Base (${branchName})` : "Base";

    const baseOptionsHtml = allPs
      .filter(p => p < currentTarget)
      .map(p => {
        const meta = getPsMeta(p);
        return `<option value="${p}" title="${escapeHtml(meta.tooltip)}" ${currentBase === String(p) ? "selected" : ""}>${escapeHtml(meta.label)}</option>`;
      })
      .join("");

    const targetOptionsHtml = allPs
      .map(p => {
        const meta = getPsMeta(p);
        const latestSuffix = p === maxPs ? " (latest)" : "";
        return `<option value="${p}" title="${escapeHtml(meta.tooltip)}" ${p === currentTarget ? "selected" : ""}>${escapeHtml(meta.label)}${latestSuffix}</option>`;
      })
      .join("");

    const isInterdiff = currentBase !== "base" && currentBase !== "0";

    const curTargetIdx = allPs.indexOf(currentTarget);
    const hasPrevPs = curTargetIdx > 0;
    const hasNextPs = curTargetIdx >= 0 && curTargetIdx < allPs.length - 1;

    const prevPsBtnHtml = `<button id="btnPrevPs-${clNum}" class="pg-btn-ps-step" title="Previous patchset (Shift+Up or ‹)" aria-label="Previous patchset" onclick="stepPgPatchset(${clNum}, -1)" ${hasPrevPs ? "" : "disabled"}>‹</button>`;
    const nextPsBtnHtml = `<button id="btnNextPs-${clNum}" class="pg-btn-ps-step" title="Next patchset (Shift+Down or ›)" aria-label="Next patchset" onclick="stepPgPatchset(${clNum}, 1)" ${hasNextPs ? "" : "disabled"}>›</button>`;

    let interdiffActionBtnHtml = "";
    if (isInterdiff) {
      interdiffActionBtnHtml = `<button type="button" id="btnResetInterdiff-${clNum}" class="pg-btn-reset-interdiff is-active" title="Reset comparison to Base (Shortcut: b)" onclick="resetPgPatchsetComparisonToBase(${clNum})">✕ vs Base</button>`;
    } else if (currentTarget > 1) {
      const prevPsNum = curTargetIdx > 0 ? allPs[curTargetIdx - 1] : (currentTarget - 1);
      interdiffActionBtnHtml = `<button type="button" id="btnToggleInterdiffPrev-${clNum}" class="pg-btn-toggle-interdiff-prev" title="Compare with previous patchset PS ${prevPsNum} (Shortcut: b)" onclick="togglePgDiffPreviousPatchset(${clNum})">⚡ vs PS ${prevPsNum}</button>`;
    }

    const revisionsMenuBtnHtml = `<button type="button" id="btnPatchRangeMenu-${clNum}" class="pg-btn-patch-range-toggle" title="Compare revisions &amp; patchsets popover" onclick="togglePgPatchRangeMenu(${clNum})" aria-haspopup="true" aria-expanded="${isMenuOpen ? 'true' : 'false'}">▾ Revisions</button>`;

    const curTargetMeta = getPsMeta(currentTarget);
    const targetDescText = curTargetMeta.desc || "";
    const targetChipHtml = targetDescText
      ? `<span id="pgPsDescChip-${clNum}" class="pg-ps-desc-chip" title="${escapeHtml(curTargetMeta.tooltip)}">📝 ${escapeHtml(targetDescText.length > 36 ? targetDescText.slice(0, 34) + "…" : targetDescText)}</span>`
      : "";

    const html = `<div class="pg-patchset-selector-wrap" data-cl="${clNum}" style="position:relative;">
      <span class="pg-ps-label">Diff:</span>
      <div class="pg-ps-select-group">
        <select id="pgBasePsSelect-${clNum}" class="pg-ps-select" aria-label="Base patchset" title="${escapeHtml(baseTitle)}" onchange="onPgPatchsetComparisonChanged(${clNum})">
          <option value="base" title="${escapeHtml(baseTitle)}" ${currentBase === "base" ? "selected" : ""}>${escapeHtml(baseLabel)}</option>
          ${baseOptionsHtml}
        </select>
        <span class="pg-ps-separator">vs</span>
        <select id="pgTargetPsSelect-${clNum}" class="pg-ps-select" aria-label="Target patchset" onchange="onPgPatchsetComparisonChanged(${clNum})">
          ${targetOptionsHtml}
        </select>
        <div class="pg-ps-steppers">
          ${prevPsBtnHtml}
          ${nextPsBtnHtml}
        </div>
      </div>
      ${isInterdiff ? `<span class="pg-interdiff-badge" title="Comparing patchset ${currentBase} to ${currentTarget}">Interdiff (PS ${currentBase} → PS ${currentTarget})</span>` : ""}
      ${interdiffActionBtnHtml}
      ${revisionsMenuBtnHtml}
      ${targetChipHtml}
      ${renderPgPatchRangePopoverHtml(c, bundle, rangeCtx)}
    </div>`;
    c._cachedPatchsetSelectorKey = cacheKey;
    c._cachedPatchsetSelectorHtml = html;
    const wSub = c._psSelSub || (c._psSelSub = {});
    wSub.clNum = clNum;
    wSub.cPs = c.patch_set;
    wSub.branch = c.branch;
    wSub.bPs = bPs;
    wSub.bBase = bBase;
    wSub.selPs = selPsVal;
    wSub.selBase = selBaseVal;
    wSub.menuOpen = isMenuOpen;
    wSub.detailsRef = psDetails;
    wSub.detailsLen = psDetailsLen;
    wSub.lastDesc = lastDetailDesc;
    return html;
  }

  function renderPgPatchRangePopoverHtml(c, bundle, precomputedCtx = null) {
    if (!c || !c.number) return "";
    const { clNum, currentTarget, currentBase, allPs, maxPs, getPsMeta } = precomputedCtx || buildPgPatchsetRangeContext(c, bundle);

    const branchName = c.branch || "";
    const baseLabel = branchName ? `Base (${branchName})` : "Base";
    const isOpen = state.patchRangeMenuCl === clNum;

    return `<div id="pgPatchRangePopover-${clNum}" class="pg-patch-range-popover" style="${isOpen ? "display:flex;" : "display:none;"}" role="dialog" aria-label="Patchset comparison revisions menu">
      <div class="pg-patch-range-header">
        <span>⇄ Patchset Revisions &amp; Interdiff Comparison</span>
        <button type="button" class="close-btn" style="background:transparent;border:none;color:var(--g-muted);cursor:pointer;font-size:14px;padding:2px 6px;" onclick="closePgPatchRangeMenu()" title="Close menu (Esc)">✕</button>
      </div>
      <div class="pg-patch-range-presets">
        <span style="font-size:11px;color:var(--g-muted);font-weight:600;">Presets:</span>
        <button type="button" class="pg-patch-range-preset-chip" onclick="selectPgPatchsetComparison(${clNum}, ${maxPs}, 'base'); closePgPatchRangeMenu();" title="Compare latest patchset vs Base">Base → Latest (PS ${maxPs})</button>
        ${maxPs > 1 ? `<button type="button" class="pg-patch-range-preset-chip" onclick="selectPgPatchsetComparison(${clNum}, ${maxPs}, ${maxPs - 1}); closePgPatchRangeMenu();" title="Compare latest patchset with its immediate predecessor">PS ${maxPs - 1} → PS ${maxPs} (Latest Interdiff)</button>` : ""}
        ${currentTarget > 1 && currentTarget !== maxPs ? `<button type="button" class="pg-patch-range-preset-chip" onclick="selectPgPatchsetComparison(${clNum}, ${currentTarget}, ${currentTarget - 1}); closePgPatchRangeMenu();" title="Compare current patchset with previous">PS ${currentTarget - 1} → PS ${currentTarget}</button>` : ""}
      </div>
      <div class="pg-patch-range-table-wrap" style="max-height:240px;overflow-y:auto;">
        <table class="pg-patch-range-table">
          <thead>
            <tr>
              <th style="width:45px;">Base</th>
              <th style="width:50px;">Target</th>
              <th style="width:75px;">Patchset</th>
              <th>Description</th>
              <th>Uploader</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody>
            <tr class="${currentBase === "base" ? "is-base" : ""}">
              <td>
                <button type="button" class="pg-patch-range-radio-btn ${currentBase === "base" ? "is-active" : ""}" onclick="selectPgPatchsetComparison(${clNum}, ${currentTarget}, 'base'); closePgPatchRangeMenu();">Base</button>
              </td>
              <td>-</td>
              <td><strong>Base</strong></td>
              <td><em>${escapeHtml(baseLabel)}</em></td>
              <td>—</td>
              <td>—</td>
            </tr>
            ${allPs.map(p => {
              const meta = getPsMeta(p);
              const isBase = currentBase === String(p);
              const isTarget = currentTarget === p;
              const isBaseDisabled = p >= currentTarget;
              return `<tr class="${isTarget ? "is-target" : (isBase ? "is-base" : "")}">
                <td>
                  <button type="button" class="pg-patch-range-radio-btn ${isBase ? "is-active" : ""}" ${isBaseDisabled ? "disabled title='Base must be earlier than target'" : `onclick="selectPgPatchsetComparison(${clNum}, ${currentTarget}, ${p}); closePgPatchRangeMenu();"`}>Base</button>
                </td>
                <td>
                  <button type="button" class="pg-patch-range-radio-btn is-target-btn ${isTarget ? "is-target-active" : ""}" onclick="selectPgPatchsetComparison(${clNum}, ${p}, '${isBase ? "base" : (currentBase !== "base" && Number(currentBase) >= p ? "base" : currentBase)}'); closePgPatchRangeMenu();">Target</button>
                </td>
                <td><strong>PS ${p}</strong>${p === maxPs ? ` <span style="font-size:10px;color:var(--g-green);font-weight:600;">(latest)</span>` : ""}</td>
                <td title="${escapeHtml(meta.desc || "")}">${escapeHtml(meta.desc ? (meta.desc.length > 30 ? meta.desc.slice(0, 28) + "…" : meta.desc) : "—")}</td>
                <td>${escapeHtml(meta.uploader || "—")}</td>
                <td style="color:var(--g-muted);font-size:10.5px;">${escapeHtml(meta.created ? meta.created.split("T")[0] : "—")}</td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;padding-top:6px;border-top:1px solid var(--g-border);font-size:11.5px;">
        <span>Comparing: <strong>${currentBase === "base" ? "Base" : `PS ${currentBase}`}</strong> → <strong>PS ${currentTarget}</strong></span>
        <button type="button" class="btn" style="height:24px;padding:0 8px;font-size:11px;" onclick="closePgPatchRangeMenu()">Close</button>
      </div>
    </div>`;
  }

  function togglePgPatchRangeMenu(clNum) {
    const num = Number(clNum);
    if (state.patchRangeMenuCl === num) {
      closePgPatchRangeMenu();
    } else {
      state.patchRangeMenuCl = num;
      const popover = document.getElementById(`pgPatchRangePopover-${num}`);
      if (popover) {
        popover.style.display = "flex";
      }
      const btn = document.getElementById(`btnPatchRangeMenu-${num}`);
      if (btn) btn.setAttribute("aria-expanded", "true");
    }
  }

  function closePgPatchRangeMenu() {
    if (state.patchRangeMenuCl) {
      const popover = document.getElementById(`pgPatchRangePopover-${state.patchRangeMenuCl}`);
      if (popover) {
        popover.style.display = "none";
      }
      const btn = document.getElementById(`btnPatchRangeMenu-${state.patchRangeMenuCl}`);
      if (btn) btn.setAttribute("aria-expanded", "false");
      state.patchRangeMenuCl = null;
    }
  }

  async function togglePgDiffPreviousPatchset(clNum, showToastMsg = true) {
    const num = Number(clNum);
    const c = (state.changes || []).find(ch => Number(ch.number) === num);
    const sel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(num) : null) || {};
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const currentTarget = Number(sel.ps || (bundle && bundle.patch_set) || (c && c.patch_set) || 1);
    const rawList = (c && Array.isArray(c.patch_sets) && c.patch_sets.length > 0)
      ? c.patch_sets
      : Array.from({ length: Math.max(1, Number(currentTarget || (c && c.patch_set) || 1)) }, (_, i) => i + 1);
    const allPs = Array.from(new Set(rawList.map(Number).filter(n => n > 0))).sort((a, b) => a - b);
    const curIdx = allPs.indexOf(currentTarget);
    if (currentTarget <= 1 || curIdx <= 0) {
      if (showToastMsg && typeof showToast === "function") {
        showToast(`ag/${num}: Patchset 1 has no previous patchset to compare against`);
      }
      return;
    }
    const prevPs = allPs[curIdx - 1];
    if (showToastMsg && typeof showToast === "function") {
      showToast(`ag/${num}: Switched to Interdiff (PS ${prevPs} → PS ${currentTarget})`);
    }
    await selectPgPatchsetComparison(num, currentTarget, prevPs);
  }

  function renderPgInterdiffBannerHtml(c, bundle) {
    if (!c || !c.number) return "";
    const clNum = Number(c.number);
    const sel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[clNum]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(clNum) : null) || {};
    const currentTarget = Number(sel.ps || (bundle && bundle.patch_set) || c.patch_set || 1);
    const currentBase = String(sel.base || (bundle && bundle.base_patch_set) || "base");
    const isInterdiff = currentBase !== "base" && currentBase !== "0";
    if (!isInterdiff) return "";

    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    const filesCount = rawFileList.length;
    const filesText = filesCount === 1 ? "1 file modified" : `${filesCount} files modified`;

    return `<div id="pgInterdiffBanner-${clNum}" class="pg-interdiff-banner" role="status" aria-label="Interdiff comparison mode">
      <div class="pg-interdiff-banner-left">
        <span class="pg-interdiff-banner-tag">⚡ Interdiff</span>
        <span>Comparing <strong>Patchset ${escapeHtml(currentBase)}</strong> → <strong>Patchset ${currentTarget}</strong></span>
        <span class="pg-interdiff-banner-stats">(${filesText} between patchsets)</span>
      </div>
      <button type="button" class="pg-btn-interdiff-banner-reset" onclick="resetPgPatchsetComparisonToBase(${clNum})" title="Reset diff to Base (Shortcut: b)">↩ View full change vs Base (b)</button>
    </div>`;
  }

  async function stepPgPatchset(clNum, delta) {
    const num = Number(clNum);
    const c = (state.changes || []).find(ch => Number(ch.number) === num);
    const sel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(num) : null) || {};
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const currentTarget = Number(sel.ps || (bundle && bundle.patch_set) || (c && c.patch_set) || 1);
    const currentBase = String(sel.base || (bundle && bundle.base_patch_set) || "base");

    const rawList = (c && Array.isArray(c.patch_sets) && c.patch_sets.length > 0)
      ? c.patch_sets
      : Array.from({ length: Math.max(1, Number(currentTarget || (c && c.patch_set) || 1)) }, (_, i) => i + 1);
    const allPs = Array.from(new Set(rawList.map(Number).filter(n => n > 0))).sort((a, b) => a - b);
    const curIdx = allPs.indexOf(currentTarget);
    const targetIdx = curIdx >= 0 ? curIdx + delta : (delta > 0 ? 0 : allPs.length - 1);

    if (targetIdx < 0 || targetIdx >= allPs.length) return;
    const newTarget = allPs[targetIdx];
    let newBase = currentBase;
    if (newBase !== "base" && Number(newBase) >= newTarget) {
      newBase = "base";
    }
    showToast(`ag/${num}: Switched to Patchset ${newTarget}`);
    await selectPgPatchsetComparison(num, newTarget, newBase);
  }

  async function resetPgPatchsetComparisonToBase(clNum, showToastMsg = true) {
    const num = Number(clNum);
    const c = (state.changes || []).find(ch => Number(ch.number) === num);
    const sel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(num) : null) || {};
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const currentTarget = Number(sel.ps || (bundle && bundle.patch_set) || (c && c.patch_set) || 1);
    if (showToastMsg && typeof showToast === "function") {
      showToast(`ag/${num}: Reset diff comparison to Base`);
    }
    await selectPgPatchsetComparison(num, currentTarget, "base");
  }

  async function onPgPatchsetComparisonChanged(clNum) {
    const baseSel = document.getElementById(`pgBasePsSelect-${clNum}`);
    const targetSel = document.getElementById(`pgTargetPsSelect-${clNum}`);
    if (!targetSel) return;
    const targetPs = Number(targetSel.value);
    let basePs = baseSel ? baseSel.value : "base";
    if (basePs !== "base" && Number(basePs) >= targetPs) {
      basePs = "base";
    }
    await selectPgPatchsetComparison(clNum, targetPs, basePs);
  }

  async function selectPgPatchsetComparison(clNum, targetPs, basePs, opts = {}) {
    const num = Number(clNum);
    state.selectedPatchsetByCl = state.selectedPatchsetByCl || {};
    state.selectedPatchsetByCl[num] = { ps: Number(targetPs), base: String(basePs) };
    if (!opts.skipPersistence && typeof setPgPersistedPatchsetComparison === "function") {
      const c = (state.changes || []).find(ch => Number(ch.number) === num);
      const maxPs = c ? Number(c.patch_set || 1) : null;
      setPgPersistedPatchsetComparison(num, targetPs, basePs, maxPs);
    }
    state.diffsByClPs = state.diffsByClPs || {};
    const cacheKey = `${num}:${targetPs}:${basePs}`;

    if (state.diffsByClPs[cacheKey]) {
      state.diffsByCl[num] = state.diffsByClPs[cacheKey];
      renderDiffPane();
      if (typeof syncPgRelationSlots === "function") syncPgRelationSlots();
      return;
    }

    state.diffLoadingCount += 1;
    state.diffLoadingLabel = `Loading diff for ag/${num} (PS ${targetPs} vs ${basePs})…`;
    syncGlobalLoadingBar();

    try {
      const resp = await sidecarFetch(`/api/gerrit/diffs?cls=${num}&ps=${targetPs}&base=${basePs}`);
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (data && (data.status === "error" || data.error)) {
        throw new Error(data.error || "Unknown diff error");
      }
      if (data && Array.isArray(data.diffs) && data.diffs[0]) {
        const bundle = data.diffs[0];
        state.diffsByClPs[cacheKey] = bundle;
        state.diffsByCl[num] = bundle;
        const targetCl = (state.changes || []).find(x => Number(x && x.number) === num) ||
          (state.queue || []).find(x => Number(x && x.number) === num);
        if (targetCl) {
          if (typeof bundle.mergeable === "boolean") targetCl.mergeable = bundle.mergeable;
          if (bundle.contains_git_conflicts) targetCl.contains_git_conflicts = true;
          if (Array.isArray(bundle.conflicts)) targetCl.conflicts = bundle.conflicts;
        }
        if (typeof syncPgClMergeability === "function") {
          syncPgClMergeability(num, bundle);
        }
        if (Array.isArray(bundle.code_questions)) {
          mergeTranscriptCodeQuestions(bundle.code_questions, { silentDom: true });
        }
        if (Array.isArray(bundle.reviewed_files)) {
          syncPgReviewedFilesFromServer(num, bundle.patch_set, bundle.reviewed_files);
        }
      }
    } catch (err) {
      const reason = (err && err.message) || err;
      console.error(`Failed to load patchset diff for ag/${num} (PS ${targetPs} vs ${basePs}):`, err);
      showToast(`Failed to load diff for ag/${num} (PS ${targetPs} vs ${basePs}): ${reason}. Run 'gcert' or retry patchset selection.`);
    } finally {
      state.diffLoadingCount = Math.max(0, state.diffLoadingCount - 1);
      syncGlobalLoadingBar();
      renderDiffPane();
      if (typeof syncPgRelationSlots === "function") syncPgRelationSlots();
    }
  }

  // --- PolyGerrit Diff File Jump Menu & Stepper Controls ---

  const _pgFileJumpMenuHtmlCache = new Map();

  function getJumpMenuFilesSig(c, rawFileList) {
    const revs = c.reviewers_detail;
    const revsLen = Array.isArray(revs) ? revs.length : 0;
    const f0 = rawFileList[0] ? rawFileList[0].file : "";
    const fLast = rawFileList[rawFileList.length - 1] ? rawFileList[rawFileList.length - 1].file : "";
    const r0Vote = revsLen > 0 && revs[0] ? (revs[0].cr_vote || 0) : 0;
    const rLastVote = revsLen > 1 && revs[revsLen - 1] ? (revs[revsLen - 1].cr_vote || 0) : 0;
    const jSub = rawFileList._jumpSub || (rawFileList._jumpSub = {});
    if (
      jSub.sig !== undefined &&
      jSub.len === rawFileList.length &&
      jSub.f0 === f0 &&
      jSub.fLast === fLast &&
      jSub.revsLen === revsLen &&
      jSub.r0Vote === r0Vote &&
      jSub.rLastVote === rLastVote
    ) {
      return jSub.sig;
    }
    let computedSig = `${rawFileList.length}:`;
    const clOwnerPrefix = typeof computePgClOwnerStatusPrefix === "function" ? computePgClOwnerStatusPrefix(c) : "";
    for (let i = 0; i < rawFileList.length; i++) {
      const f = rawFileList[i];
      const ownInfo = (
        f._loopOwnerInfo &&
        f._loopOwnerClPrefix === clOwnerPrefix &&
        f._loopOwnerRawSt === f.owner_status &&
        f._loopOwnerRawApp === f.owner_approved &&
        (!Array.isArray(f.owners) || f.owners.length === 0)
      )
        ? f._loopOwnerInfo
        : (typeof getPgFileOwnerStatus === "function" ? getPgFileOwnerStatus(c, f, clOwnerPrefix) : { status: "approved" });
      f._jumpOwnerInfo = ownInfo;
      f._jumpOwnerRawSt = f.owner_status;
      f._jumpOwnerRawApp = f.owner_approved;
      computedSig += `${f.file}|${f.status || ""}|${f.additions || 0}|${f.deletions || 0}|${ownInfo.status};`;
    }
    jSub.len = rawFileList.length;
    jSub.f0 = f0;
    jSub.fLast = fLast;
    jSub.revsLen = revsLen;
    jSub.r0Vote = r0Vote;
    jSub.rLastVote = rLastVote;
    jSub.sig = computedSig;
    rawFileList._cachedJumpFilesSig = computedSig;
    return computedSig;
  }

  function renderPgFileJumpMenuHtml(c, bundle) {
    if (!c || !c.number) return "";
    const clNum = Number(c.number);
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (rawFileList.length === 0) return "";

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[clNum]) || {};
    const curPs = Number(selPsMeta.ps || (bundle && bundle.patch_set) || c.patch_set || 1);
    const revSet = getPgReviewedSet(clNum, curPs);

    const activeFile = (state.activeFileByCl && state.activeFileByCl[clNum]) || rawFileList[0].file;
    const isMenuOpen = Boolean(state.fileJump && state.fileJump.isOpen && Number(state.fileJump.cl) === clNum);
    const query = (state.fileJump && state.fileJump.cl === clNum && state.fileJump.filter) ? state.fileJump.filter.trim().toLowerCase() : "";
    const fileSort = (typeof getPgFileSort === "function") ? getPgFileSort(clNum) : { col: "path", dir: "asc" };
    const inlineCommentsLen = (state.inlineCommentsByCl && Array.isArray(state.inlineCommentsByCl[clNum])) ? state.inlineCommentsByCl[clNum].length : 0;
    const unresThreadsLen = (bundle && Array.isArray(bundle.unresolved_threads)) ? bundle.unresolved_threads.length : 0;
    const filesSig = getJumpMenuFilesSig(c, rawFileList);
    const unresCnt = c.unresolved_comment_count || 0;
    const showRes = state.showResolvedComments ? 1 : 0;
    const showRob = state.showRobotComments ? 1 : 0;
    if (bundle && revSet.size === 0) {
      const fjSub = bundle._fjSub;
      if (
        fjSub &&
        fjSub.clNum === clNum &&
        fjSub.curPs === curPs &&
        fjSub.activeFile === activeFile &&
        fjSub.isMenuOpen === isMenuOpen &&
        fjSub.query === query &&
        fjSub.sortCol === fileSort.col &&
        fjSub.sortDir === fileSort.dir &&
        fjSub.inlineLen === inlineCommentsLen &&
        fjSub.unresLen === unresThreadsLen &&
        fjSub.unresCnt === unresCnt &&
        fjSub.showRes === showRes &&
        fjSub.showRob === showRob &&
        fjSub.filesSig === filesSig &&
        bundle._cachedJumpMenuKey === fjSub.key &&
        bundle._cachedJumpMenuHtml !== undefined
      ) {
        return bundle._cachedJumpMenuHtml;
      }
    }
    const revSig = revSet.size > 0 ? Array.from(revSet).join(",") : "";
    const jumpCacheKey = `${clNum}|${curPs}|${activeFile}|${isMenuOpen ? 1 : 0}|${query}|${fileSort.col}:${fileSort.dir}|${revSig}|${inlineCommentsLen}|${unresThreadsLen}|${unresCnt}|${showRes}|${showRob}|${filesSig}`;
    if (bundle && bundle._cachedJumpMenuKey === jumpCacheKey && bundle._cachedJumpMenuHtml !== undefined) {
      return bundle._cachedJumpMenuHtml;
    }
    const cachedJumpHtml = _pgFileJumpMenuHtmlCache.get(jumpCacheKey);
    if (cachedJumpHtml !== undefined) {
      if (bundle) {
        bundle._cachedJumpMenuKey = jumpCacheKey;
        bundle._cachedJumpMenuHtml = cachedJumpHtml;
        if (revSet.size === 0) {
          bundle._fjSub = {
            clNum, curPs, activeFile, isMenuOpen, query, sortCol: fileSort.col, sortDir: fileSort.dir,
            inlineLen: inlineCommentsLen, unresLen: unresThreadsLen, unresCnt, showRes, showRob, filesSig, key: jumpCacheKey
          };
        }
      }
      return cachedJumpHtml;
    }

    let activeIdx = -1;
    if (activeFile === "/COMMIT_MSG") {
      activeIdx = -1;
    } else {
      activeIdx = rawFileList.findIndex(f => f.file === activeFile);
    }

    let buttonLabel = `Jump to file… (${rawFileList.length})`;
    let buttonTitle = "Jump to file in diff (Shortcut: Shift+J)";
    if (activeFile === "/COMMIT_MSG") {
      buttonLabel = "📄 /COMMIT_MSG";
      buttonTitle = "Active file: /COMMIT_MSG — Click to jump (Shift+J)";
    } else if (activeIdx >= 0) {
      const f = rawFileList[activeIdx];
      const status = (f._cachedFilePath === f.file && f._cachedMetaStatusRaw === f.status && f._cachedStatus)
        ? f._cachedStatus
        : getPgFileStatus(f);
      const shortName = f.file.split("/").pop() || f.file;
      buttonLabel = `[${status}] ${shortName} (${activeIdx + 1} of ${rawFileList.length})`;
      buttonTitle = `Active file: ${f.file} (${activeIdx + 1} of ${rawFileList.length}) — Click to jump (Shift+J)`;
    }

    const hasAnyClComments = Boolean(
      inlineCommentsLen > 0 ||
      unresThreadsLen > 0 ||
      c.unresolved_comment_count > 0 ||
      (state.pendingDraftsByCl && state.pendingDraftsByCl[clNum] && state.pendingDraftsByCl[clNum].length > 0) ||
      hasAnyOwnProp(state.openReplyByItemId)
    );
    const commitMsgBadgeHtml = (hasAnyClComments && typeof renderPgFileCommentsBadgesHtml === "function")
      ? renderPgFileCommentsBadgesHtml(c, "/COMMIT_MSG")
      : "";

    const commitMsgMatches = !query || "/commit_msg".includes(query) || "commit".includes(query) || "msg".includes(query);
    const commitMsgItemHtml = commitMsgMatches ? `<div
        class="pg-file-jump-item pg-file-jump-magic ${activeFile === "/COMMIT_MSG" ? "is-active" : ""}"
        data-file="/COMMIT_MSG"
        onclick="onPgFileJumpItemClick(event, ${clNum}, ${curPs})"
        title="Commit Message"
      >
        <span class="pg-file-status-badge status-M" title="Commit Message">M</span>
        <span class="pg-file-jump-path">/COMMIT_MSG <span class="pg-magic-file-desc">Commit Message</span></span>
        ${commitMsgBadgeHtml}
        <span class="pg-file-jump-delta"><span class="pg-delta-add">Message</span></span>
      </div>` : "";

    let matchedFilesCount = 0;
    const sortedJumpFiles = (typeof getPgSortedFileList === "function") ? getPgSortedFileList(c, rawFileList) : rawFileList;
    let fileItemsHtml = "";
    for (let idx = 0; idx < sortedJumpFiles.length; idx++) {
      const f = sortedJumpFiles[idx];
      const matches = !query || f.file.toLowerCase().includes(query) || (typeof matchesPgFileFilter === "function" && matchesPgFileFilter(f.file, query));
      if (matches) matchedFilesCount++;
      const isRev = revSet.has(f.file);
      if (f._cachedFilePath !== f.file) {
        f._cachedFilePath = f.file;
        f._cachedSafeFile = escapeHtml(f.file);
        f._cachedSafeFileId = escapeHtml(String(f.file).replace(/[^a-zA-Z0-9_-]/g, "_"));
        f._cachedLangInfo = detectDiffFileLanguage(f.file);
        const ext = (typeof getPgFileExtension === "function") ? getPgFileExtension(f.file) : "";
        f._cachedExt = ext;
        const cleanExt = ext && ext.charCodeAt(0) === 46 ? ext.slice(1) : ext;
        const isImgExt = Boolean(cleanExt && PG_IMAGE_EXTENSIONS.has(cleanExt));
        f._cachedIsImg = isImgExt;
        f._cachedIsBin = Boolean(isImgExt || (cleanExt && PG_BINARY_EXTENSIONS.has(cleanExt)));
        f._cachedIsMd = Boolean(cleanExt && PG_MARKDOWN_EXTENSIONS.has(cleanExt));
        f._cachedIsHtml = Boolean(cleanExt && PG_HTML_EXTENSIONS.has(cleanExt));
      }
      const safeFile = f._cachedSafeFile;
      const commentsBadgeHtml = (hasAnyClComments && typeof renderPgFileCommentsBadgesHtml === "function")
        ? renderPgFileCommentsBadgesHtml(c, f.file)
        : "";
      const status = (f._cachedMetaStatusRaw === f.status && f._cachedStatus)
        ? f._cachedStatus
        : (f._cachedMetaStatusRaw = f.status, f._cachedStatus = (f._cachedFilePath === f.file && f._loopStatus) ? f._loopStatus : getPgFileStatus(f));
      const statusBadge = renderPgFileStatusBadgeHtml(status, clNum);
      const isActive = activeFile === f.file;
      const ownerInfo = (f._jumpOwnerInfo && f._jumpOwnerRawSt === f.owner_status && f._jumpOwnerRawApp === f.owner_approved)
        ? f._jumpOwnerInfo
        : (typeof getPgFileOwnerStatus === "function" ? getPgFileOwnerStatus(c, f) : { status: "approved" });
      const ownerBadgeHtml = (
        f._cachedOwnerBadgeCl === clNum &&
        f._cachedOwnerBadgeInfo === ownerInfo &&
        f._cachedOwnerBadgeTarget === safeFile &&
        typeof f._cachedOwnerBadgeHtml === "string"
      )
        ? f._cachedOwnerBadgeHtml
        : (typeof renderPgFileOwnerBadgeHtml === "function" ? renderPgFileOwnerBadgeHtml(ownerInfo, clNum, safeFile, false, "", true) : "");
      fileItemsHtml += `<div
        class="pg-file-jump-item ${isActive ? "is-active" : ""} ${isRev ? "is-reviewed" : ""}"
        data-file="${safeFile}"
        data-index="${idx}"
        style="${matches ? "" : "display:none;"}"
        onclick="onPgFileJumpItemClick(event, ${clNum}, ${curPs})"
        title="${safeFile}"
      >
        ${statusBadge}
        <span class="pg-file-jump-path">${safeFile}</span>
        ${ownerBadgeHtml}
        ${commentsBadgeHtml}
        ${isRev ? `<span class="pg-file-jump-reviewed" title="Reviewed">✓</span>` : ""}
        <span class="pg-file-jump-delta"><span class="pg-delta-add">+${f.additions || 0}</span> <span class="pg-delta-del">-${f.deletions || 0}</span></span>
      </div>`;
    }

    const totalCount = commitMsgMatches ? matchedFilesCount + 1 : matchedFilesCount;

    const jumpOutHtml = `<div class="pg-file-jump-group" id="pgFileJumpGroup-${clNum}" data-cl="${clNum}">
      <button
        type="button"
        id="btnPrevFile-${clNum}"
        class="pg-btn-subtle pg-btn-file-step pg-btn-file-prev"
        onclick="navigatePgFileDiff('prev')"
        title="Previous file in change (Shortcut: [)"
      ><span aria-hidden="true">‹</span> <span class="pg-file-step-kbd">[</span></button>
      <button
        type="button"
        id="btnFileJump-${clNum}"
        class="pg-btn-subtle pg-btn-file-jump ${isMenuOpen ? "is-active" : ""}"
        onclick="togglePgFileJumpMenu(${clNum})"
        title="${escapeHtml(buttonTitle)}"
        aria-haspopup="true"
        aria-expanded="${isMenuOpen ? "true" : "false"}"
      >
        <span class="pg-file-jump-icon" aria-hidden="true">📄</span>
        <span id="pgFileJumpLabel-${clNum}" class="pg-file-jump-btn-label">${escapeHtml(buttonLabel)}</span>
        <span class="pg-file-jump-caret" aria-hidden="true">▾</span>
      </button>
      <button
        type="button"
        id="btnNextFile-${clNum}"
        class="pg-btn-subtle pg-btn-file-step pg-btn-file-next"
        onclick="navigatePgFileDiff('next')"
        title="Next file in change (Shortcut: ])"
      ><span class="pg-file-step-kbd">]</span> <span aria-hidden="true">›</span></button>
      <button
        type="button"
        id="btnUpToFileList-${clNum}"
        class="pg-btn-subtle pg-btn-file-up"
        onclick="scrollToPgFileList(${clNum})"
        title="Scroll up to file list (Shortcut: u)"
      ><span>↑ Files</span></button>
      <div id="pgFileJumpDropdown-${clNum}" class="pg-file-jump-dropdown" style="${isMenuOpen ? "" : "display:none;"}" onclick="event.stopPropagation()">
        <div class="pg-file-jump-search-wrap">
          <span class="pg-file-jump-search-icon" aria-hidden="true">🔍</span>
          <input
            type="text"
            id="pgFileJumpFilter-${clNum}"
            class="pg-file-jump-filter"
            placeholder="Filter files… (Esc to close)"
            value="${escapeHtml(query)}"
            oninput="onPgFileJumpFilterInput(${clNum}, this.value)"
            onkeydown="onPgFileJumpFilterKeyDown(event, ${clNum})"
            autocomplete="off"
            spellcheck="false"
          />
          <button type="button" class="pg-file-jump-clear-btn" id="pgFileJumpClearBtn-${clNum}" style="${query ? "" : "display:none;"}" onclick="clearPgFileJumpFilter(${clNum})" title="Clear filter (Esc)">✕</button>
        </div>
        <div class="pg-file-jump-list-wrap" id="pgFileJumpList-${clNum}">
          ${commitMsgItemHtml}
          ${fileItemsHtml}
        </div>
        <div class="pg-file-jump-footer">
          <span class="pg-file-jump-footer-tip">↑/↓: navigate · Enter: jump · [ / ]: step</span>
          <span id="pgFileJumpCount-${clNum}" class="pg-file-jump-footer-count">${totalCount} files</span>
        </div>
      </div>
    </div>`;
    setPgBoundedMapEntry(_pgFileJumpMenuHtmlCache, jumpCacheKey, jumpOutHtml, 500);
    if (bundle) {
      bundle._cachedJumpMenuKey = jumpCacheKey;
      bundle._cachedJumpMenuHtml = jumpOutHtml;
      if (revSet.size === 0) {
        bundle._fjSub = {
          clNum, curPs, activeFile, isMenuOpen, query, sortCol: fileSort.col, sortDir: fileSort.dir,
          inlineLen: inlineCommentsLen, unresLen: unresThreadsLen, unresCnt, showRes, showRob, filesSig, key: jumpCacheKey
        };
      }
    }
    return jumpOutHtml;
  }

  function openPgFileJumpMenu(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    state.fileJump = state.fileJump || {};
    state.fileJump.isOpen = true;
    state.fileJump.cl = num;
    const dropdown = document.getElementById(`pgFileJumpDropdown-${num}`);
    const btn = document.getElementById(`btnFileJump-${num}`);
    if (dropdown) dropdown.style.display = "";
    if (btn) {
      btn.classList.add("is-active");
      btn.setAttribute("aria-expanded", "true");
    }
    const input = document.getElementById(`pgFileJumpFilter-${num}`);
    if (input) {
      input.focus();
      input.select();
    }
  }

  function closePgFileJumpMenu(clNum) {
    const num = Number(clNum || (state.fileJump && state.fileJump.cl) || state.selectedClNumber || 0);
    if (state.fileJump) {
      state.fileJump.isOpen = false;
    }
    const dropdown = document.getElementById(`pgFileJumpDropdown-${num}`);
    const btn = document.getElementById(`btnFileJump-${num}`);
    if (dropdown) dropdown.style.display = "none";
    if (btn) {
      btn.classList.remove("is-active");
      btn.setAttribute("aria-expanded", "false");
    }
  }

  function togglePgFileJumpMenu(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (state.fileJump && state.fileJump.isOpen && Number(state.fileJump.cl) === num) {
      closePgFileJumpMenu(num);
    } else {
      openPgFileJumpMenu(num);
    }
  }

  function selectPgFileJumpItem(clNum, patchSet, filePath) {
    closePgFileJumpMenu(clNum);
    updatePgActiveFile(clNum, filePath);
    scrollToFileDiff(clNum, patchSet, filePath);
  }

  function selectPgFileJumpCommitMsg(clNum) {
    closePgFileJumpMenu(clNum);
    updatePgActiveFile(clNum, "/COMMIT_MSG");
    scrollToCommitMessageBox(clNum);
  }

  function onPgFileJumpItemClick(event, clNum, patchSet) {
    if (event) event.stopPropagation();
    const item = (event && event.currentTarget) || (event && event.target && event.target.closest(".pg-file-jump-item"));
    if (!item) return;
    const filePath = item.getAttribute("data-file") || "";
    if (filePath === "/COMMIT_MSG") {
      selectPgFileJumpCommitMsg(clNum);
    } else {
      selectPgFileJumpItem(clNum, patchSet, filePath);
    }
  }

  function onPgFileJumpFilterInput(clNum, val) {
    const num = Number(clNum);
    state.fileJump = state.fileJump || {};
    state.fileJump.filter = val || "";
    const q = String(val || "").trim().toLowerCase();
    const dropdown = document.getElementById(`pgFileJumpDropdown-${num}`);
    const clearBtn = document.getElementById(`pgFileJumpClearBtn-${num}`);
    const countEl = document.getElementById(`pgFileJumpCount-${num}`);
    if (clearBtn) clearBtn.style.display = q ? "" : "none";
    if (!dropdown) return;

    const items = Array.from(dropdown.querySelectorAll(".pg-file-jump-item"));
    let visibleCount = 0;
    items.forEach(it => {
      const file = (it.getAttribute("data-file") || "").toLowerCase();
      let matches = false;
      if (!q) {
        matches = true;
      } else if (file === "/commit_msg") {
        matches = "/commit_msg".includes(q) || "commit".includes(q) || "msg".includes(q);
      } else {
        matches = file.includes(q) || (typeof matchesPgFileFilter === "function" && matchesPgFileFilter(file, q));
      }
      it.style.display = matches ? "" : "none";
      if (matches) visibleCount++;
    });
    if (countEl) {
      countEl.textContent = `${visibleCount} files`;
    }
  }

  function clearPgFileJumpFilter(clNum) {
    const num = Number(clNum);
    const input = document.getElementById(`pgFileJumpFilter-${num}`);
    if (input) {
      input.value = "";
      input.focus();
    }
    onPgFileJumpFilterInput(num, "");
  }

  function onPgFileJumpFilterKeyDown(event, clNum) {
    if (!event) return;
    const num = Number(clNum);
    if (event.key === "Escape") {
      event.preventDefault();
      closePgFileJumpMenu(num);
      return;
    }
    const dropdown = document.getElementById(`pgFileJumpDropdown-${num}`);
    if (!dropdown) return;
    const visibleItems = Array.from(dropdown.querySelectorAll(".pg-file-jump-item")).filter(it => it.style.display !== "none");
    if (!visibleItems.length) return;

    let focusedIdx = visibleItems.findIndex(it => it.classList.contains("is-focused"));

    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (focusedIdx >= 0) visibleItems[focusedIdx].classList.remove("is-focused");
      focusedIdx = (focusedIdx + 1) % visibleItems.length;
      visibleItems[focusedIdx].classList.add("is-focused");
      if (typeof visibleItems[focusedIdx].scrollIntoView === "function") {
        visibleItems[focusedIdx].scrollIntoView({ block: "nearest" });
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (focusedIdx >= 0) visibleItems[focusedIdx].classList.remove("is-focused");
      focusedIdx = focusedIdx <= 0 ? visibleItems.length - 1 : focusedIdx - 1;
      visibleItems[focusedIdx].classList.add("is-focused");
      if (typeof visibleItems[focusedIdx].scrollIntoView === "function") {
        visibleItems[focusedIdx].scrollIntoView({ block: "nearest" });
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      const targetItem = focusedIdx >= 0 ? visibleItems[focusedIdx] : visibleItems[0];
      if (targetItem) {
        targetItem.click();
      }
    }
  }

  function updatePgActiveFile(clNum, filePath) {
    const num = Number(clNum || state.selectedClNumber || 0);
    state.activeFileByCl = state.activeFileByCl || {};
    state.activeFileByCl[num] = filePath;
    state.activeFile = filePath;

    const labelEl = document.getElementById(`pgFileJumpLabel-${num}`);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (labelEl && (labelEl._lastActiveFile !== filePath || labelEl._lastFilesLen !== rawFiles.length)) {
      labelEl._lastActiveFile = filePath;
      labelEl._lastFilesLen = rawFiles.length;
      if (filePath === "/COMMIT_MSG") {
        labelEl.textContent = "📄 /COMMIT_MSG";
      } else {
        const idx = rawFiles.findIndex(f => f.file === filePath);
        if (idx !== -1) {
          const f = rawFiles[idx];
          const status = getPgFileStatus(f);
          const shortName = f.file.split("/").pop() || f.file;
          labelEl.textContent = `[${status}] ${shortName} (${idx + 1} of ${rawFiles.length})`;
        } else if (filePath) {
          const shortName = filePath.split("/").pop() || filePath;
          labelEl.textContent = shortName;
        } else {
          labelEl.textContent = `Jump to file… (${rawFiles.length})`;
        }
      }
    }

    const dropdown = document.getElementById(`pgFileJumpDropdown-${num}`);
    if (dropdown && typeof dropdown.querySelectorAll === "function" && dropdown._lastActiveFile !== filePath) {
      dropdown._lastActiveFile = filePath;
      const items = dropdown.querySelectorAll(".pg-file-jump-item");
      items.forEach(it => {
        const f = it.getAttribute("data-file");
        it.classList.toggle("is-active", f === filePath);
      });
    }
  }

  function openPgHeaderReply(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (num && Number(state.selectedClNumber) !== num && typeof selectCl === "function") {
      selectCl(num, { keepChecked: true, skipRender: true });
    }
    openReplyDialog({ focusComment: true });
  }

  function scrollPgChangeToTop(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const scrollEl = document.getElementById("diffScroll") || document.getElementById("diffPane");
    const cv = (num && document.getElementById(`pgStackCommit-${num}`)) ||
               document.querySelector(`.pg-change-view[data-cl="${num}"]`) ||
               document.querySelector(".pg-change-view");
    if (scrollEl) {
      const targetTop = (cv && state.continuousStackReview && Number(cv.offsetTop) > 0)
        ? Math.max(0, Number(cv.offsetTop) - 56)
        : 0;
      if (typeof scrollEl.scrollTo === "function") {
        scrollEl.scrollTo({ top: targetTop, behavior: "smooth" });
      }
      scrollEl.scrollTop = targetTop;
    }
    syncPgStickyFileHeaders();
  }

  function setPgStylePropertyIfChanged(el, propName, nextValue) {
    if (!el || !el.style || typeof el.style.setProperty !== "function") return;
    if (!el._pgCssVarCache || el._pgLastSetPropertyFn !== el.style.setProperty) {
      el._pgCssVarCache = {};
      el._pgLastSetPropertyFn = el.style.setProperty;
    }
    if (el._pgCssVarCache[propName] === nextValue) return;
    el._pgCssVarCache[propName] = nextValue;
    el.style.setProperty(propName, nextValue);
  }

  function syncPgStickyFileHeaders() {
    const changeViewsNodeList = document.querySelectorAll(".pg-change-view");
    if (!changeViewsNodeList || changeViewsNodeList.length === 0) return;
    const changeViews = Array.from(changeViewsNodeList);
    const scrollEl = document.getElementById("diffScroll") || document.getElementById("diffPane");
    const stackBanner = document.getElementById("pgStackReviewBanner");
    const stackBannerHeight = (stackBanner && (!stackBanner.style || stackBanner.style.display !== "none") && stackBanner.offsetHeight)
      ? Number(stackBanner.offsetHeight)
      : 0;
    const scrollRect = (scrollEl && typeof scrollEl.getBoundingClientRect === "function")
      ? scrollEl.getBoundingClientRect()
      : null;
    const scrollTopVal = scrollEl && scrollEl.scrollTop !== undefined ? Number(scrollEl.scrollTop || 0) : 0;

    // Phase 1: Batch all DOM geometry reads before mutating classes or CSS variables.
    const viewSnapshots = changeViews.map(cv => {
      const changeHeader = cv.querySelector(".pg-change-header");
      const changeHeaderHeight = (changeHeader && changeHeader.offsetHeight) ? Number(changeHeader.offsetHeight) : 41;
      const findBar = cv.querySelector(".pg-diff-find-bar");
      const findBarVisible = Boolean(findBar && (!findBar.style || findBar.style.display !== "none"));
      const findBarHeight = findBarVisible ? (Number(findBar.offsetHeight) || 36) : 0;
      const changeHeaderTop = stackBannerHeight;
      const findBarTop = changeHeaderTop + changeHeaderHeight;
      const fileHeaderTop = findBarTop + findBarHeight;

      const blocks = Array.from(cv.querySelectorAll(".pg-file-diff-block"));
      const visibleBlocks = blocks.filter(b => !b.style || b.style.display !== "none");
      let isScrollingFiles = false;
      if (visibleBlocks.length > 0) {
        const firstBlock = visibleBlocks[0];
        const lastBlock = visibleBlocks[visibleBlocks.length - 1];
        if (scrollRect && typeof firstBlock.getBoundingClientRect === "function" && typeof lastBlock.getBoundingClientRect === "function") {
          const fRect = firstBlock.getBoundingClientRect();
          const lRect = lastBlock.getBoundingClientRect();
          if (fRect && lRect && (fRect.height > 0 || lRect.bottom > fRect.top)) {
            const fTop = fRect.top - scrollRect.top;
            const lBottom = lRect.bottom - scrollRect.top;
            isScrollingFiles = fTop <= stackBannerHeight + 96 && lBottom > stackBannerHeight + 48;
          }
        }
        if (!isScrollingFiles && firstBlock.offsetHeight > 0) {
          const firstTop = Number(firstBlock.offsetTop || 0);
          const lastBottom = Number(lastBlock.offsetTop || 0) + Number(lastBlock.offsetHeight || 0);
          isScrollingFiles = (scrollTopVal + stackBannerHeight + 60 >= firstTop) && (scrollTopVal + stackBannerHeight + 40 < lastBottom);
        }
      }

      let activeStuckHeaderHeight = null;
      const blockMutations = blocks.map(block => {
        const hdr = block.querySelector(".file-header");
        if (!hdr) return null;
        const isHiddenOrCollapsed = block.classList.contains("is-collapsed") || (block.style && block.style.display === "none");
        if (isHiddenOrCollapsed) {
          return { block, hdr, isStuck: false };
        }
        const hdrHeight = Number(hdr.offsetHeight) || 34;
        let isStuck = false;
        if (scrollRect && typeof block.getBoundingClientRect === "function") {
          const bRect = block.getBoundingClientRect();
          if (bRect && (bRect.height > 0 || bRect.bottom > bRect.top)) {
            const relTop = bRect.top - scrollRect.top;
            const relBottom = bRect.bottom - scrollRect.top;
            isStuck = relTop <= fileHeaderTop + 2 && relBottom > fileHeaderTop + hdrHeight;
          }
        }
        if (!isStuck && block.offsetHeight > 0) {
          const bTop = Number(block.offsetTop || 0);
          const bBottom = bTop + Number(block.offsetHeight || 0);
          isStuck = (scrollTopVal + fileHeaderTop >= bTop - 2) && (scrollTopVal + fileHeaderTop + hdrHeight < bBottom);
        }
        if (isStuck) {
          activeStuckHeaderHeight = hdrHeight;
        }
        return { block, hdr, isStuck };
      });

      return {
        cv,
        changeHeader,
        isScrollingFiles,
        changeHeaderTop,
        findBarTop,
        fileHeaderTop,
        activeStuckHeaderHeight,
        blockMutations,
      };
    });

    // Phase 2: Apply all DOM style/class writes in a single non-reflowing pass.
    viewSnapshots.forEach(snap => {
      const {
        cv,
        changeHeader,
        isScrollingFiles,
        changeHeaderTop,
        findBarTop,
        fileHeaderTop,
        activeStuckHeaderHeight,
        blockMutations,
      } = snap;
      if (cv.classList) cv.classList.toggle("is-scrolling-files", isScrollingFiles);
      if (changeHeader && changeHeader.classList) changeHeader.classList.toggle("is-scrolling-files", isScrollingFiles);
      setPgStylePropertyIfChanged(cv, "--pg-sticky-change-header-top", `${changeHeaderTop}px`);
      setPgStylePropertyIfChanged(cv, "--pg-sticky-find-bar-top", `${findBarTop}px`);
      setPgStylePropertyIfChanged(cv, "--pg-sticky-file-header-top", `${fileHeaderTop}px`);
      if (scrollEl) {
        setPgStylePropertyIfChanged(scrollEl, "--pg-sticky-change-header-top", `${changeHeaderTop}px`);
        setPgStylePropertyIfChanged(scrollEl, "--pg-sticky-find-bar-top", `${findBarTop}px`);
        setPgStylePropertyIfChanged(scrollEl, "--pg-sticky-file-header-top", `${fileHeaderTop}px`);
      }
      if (activeStuckHeaderHeight !== null) {
        setPgStylePropertyIfChanged(cv, "--pg-sticky-active-file-header-height", `${activeStuckHeaderHeight}px`);
      }
      blockMutations.forEach(item => {
        if (!item) return;
        item.hdr.classList.toggle("is-stuck", item.isStuck);
        item.block.classList.toggle("has-stuck-header", item.isStuck);
      });
    });
  }

  function syncPgActiveFileFromScroll(clNum) {
    syncPgStickyFileHeaders();
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const block = typeof getActivePgFileDiffBlock === "function" ? getActivePgFileDiffBlock() : null;
    if (!block) return;
    const blockCl = Number(block.getAttribute("data-cl"));
    if (blockCl !== num) return;
    const filePath = block.getAttribute("data-file") || "";
    if (filePath && state.activeFileByCl && state.activeFileByCl[num] !== filePath) {
      updatePgActiveFile(num, filePath);
    }
  }

