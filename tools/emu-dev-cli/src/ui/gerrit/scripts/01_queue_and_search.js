  function clampSidebarWidth(rawPx) {
    const wsEl = document.getElementById("workspaceContainer");
    const wsWidth = wsEl ? wsEl.clientWidth : (window.innerWidth || 1200);
    const padAllowance = 12;
    let maxAllowed = Math.max(
      MIN_SIDEBAR_WIDTH,
      Math.floor(wsWidth - padAllowance - MIN_DIFF_PANE_WIDTH)
    );
    if (wsWidth > 720 && wsWidth <= 920) {
      maxAllowed = Math.min(maxAllowed, Math.max(MIN_SIDEBAR_WIDTH, Math.floor(wsWidth * 0.42)));
    } else if (wsWidth > 920 && wsWidth <= 1150) {
      maxAllowed = Math.min(maxAllowed, Math.max(MIN_SIDEBAR_WIDTH, Math.floor(wsWidth * 0.45)));
    }
    const num = Number(rawPx);
    const desired = Number.isFinite(num) && num > 0 ? num : DEFAULT_SIDEBAR_WIDTH;
    return Math.round(Math.min(maxAllowed, Math.max(MIN_SIDEBAR_WIDTH, desired)));
  }

  function saveSidebarLayoutPrefs() {
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(Math.round(state.sidebarWidth)));
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, state.sidebarCollapsed ? "1" : "0");
    } catch (_e) {}
  }

  function loadSidebarLayoutPrefs() {
    try {
      const savedW = Number(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY));
      if (Number.isFinite(savedW) && savedW >= MIN_SIDEBAR_WIDTH) {
        state.sidebarWidth = savedW;
      }
      const savedCol = window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
      if (savedCol === "1" || savedCol === "true") {
        state.sidebarCollapsed = true;
      }
      const savedScheme = window.localStorage.getItem(DIFF_COLOR_SCHEME_STORAGE_KEY);
      if (savedScheme && DIFF_COLOR_SCHEMES.some(s => s.id === savedScheme)) {
        state.diffColorScheme = savedScheme;
      }
      const savedDiffMode = window.localStorage.getItem(PG_DIFF_VIEW_MODE_STORAGE_KEY);
      if (savedDiffMode === "side-by-side" || savedDiffMode === "unified") {
        state.diffViewMode = savedDiffMode;
      }
      const savedTheme = window.localStorage.getItem(PG_THEME_STORAGE_KEY);
      if (savedTheme === "light" || savedTheme === "dark") {
        applyPgTheme(savedTheme, false);
      } else {
        applyPgTheme("dark", false);
      }
    } catch (_e) {}
    syncSidebarLayoutDom();
  }

  function syncSidebarLayoutDom() {
    const wsEl = document.getElementById("workspaceContainer");
    const queuePaneEl = document.getElementById("queuePane");
    const isStacked = (window.innerWidth || 1200) <= 720;
    const primaryEl = wsEl || queuePaneEl;
    const cvModeFlag = state.codeViewerMode ? 1 : 0;
    const cvActiveCl = cvModeFlag ? Number(state.selectedClNumber || 0) : 0;
    const cvBundle = cvActiveCl && state.diffsByCl ? state.diffsByCl[cvActiveCl] : null;
    const cvFileCount = (cvModeFlag && cvBundle && Array.isArray(cvBundle.files)) ? cvBundle.files.length : 0;
    const layoutSig = `${state.sidebarWidth}|${state.sidebarCollapsed ? 1 : 0}|${isStacked ? 1 : 0}|${isStacked ? (state.selectedClNumber || 0) : 0}|${state.visibleCommitCount || 0}|${cvModeFlag}:${cvFileCount}`;
    if (
      primaryEl &&
      primaryEl._lastSidebarLayoutSig === layoutSig &&
      (!wsEl || wsEl.getAttribute("data-sidebar-density") !== null)
    ) {
      if (state.harnessMenuOpen) {
        positionHarnessDropdownMenu();
      }
      return;
    }
    const collapseBtn = document.getElementById("btnCollapseSidebar");
    const expandBtn = document.getElementById("btnExpandSidebar");
    const expandLbl = document.getElementById("expandSidebarLabel");
    const splitterEl = document.getElementById("workspaceSplitter");
    const clamped = clampSidebarWidth(state.sidebarWidth);
    const density = computePgSidebarDensity(clamped);
    if (wsEl) {
      if (!isStacked) {
        if (wsEl.style && typeof wsEl.style.setProperty === "function") {
          wsEl.style.setProperty("--queue-pane-width", `${clamped}px`);
        } else if (wsEl.style) {
          wsEl.style["--queue-pane-width"] = `${clamped}px`;
        }
      } else {
        if (wsEl.style && typeof wsEl.style.setProperty === "function") {
          wsEl.style.setProperty("--queue-pane-width", "100%");
        } else if (wsEl.style) {
          wsEl.style["--queue-pane-width"] = "100%";
        }
      }
      wsEl.classList.toggle("sidebar-collapsed", Boolean(state.sidebarCollapsed));
      wsEl.classList.toggle("is-stacked", Boolean(isStacked));
      wsEl.classList.toggle("code-viewer-mode", Boolean(state.codeViewerMode));
      wsEl.setAttribute("data-sidebar-density", state.sidebarCollapsed ? "collapsed" : density);
    }
    if (queuePaneEl) {
      queuePaneEl.setAttribute("data-sidebar-density", density);
    }
    if (collapseBtn) {
      collapseBtn.setAttribute("aria-expanded", state.sidebarCollapsed ? "false" : "true");
      if (isStacked && state.selectedClNumber) {
        collapseBtn.title = `View change ag/${state.selectedClNumber} ([)`;
      } else {
        collapseBtn.title = "Collapse dashboard panel ([)";
      }
    }
    if (expandBtn) {
      expandBtn.style.display = (state.sidebarCollapsed || isStacked) ? "inline-flex" : "none";
    }
    if (expandLbl) {
      if (state.codeViewerMode) {
        expandLbl.textContent = cvFileCount > 0 ? `Files (${cvFileCount})` : "Files";
      } else {
        expandLbl.textContent = state.visibleCommitCount > 0
          ? `Queue (${state.visibleCommitCount})`
          : "Queue";
      }
    }
    if (splitterEl) {
      splitterEl.setAttribute("aria-valuenow", String(state.sidebarCollapsed ? 0 : clamped));
      splitterEl.setAttribute("aria-valuemin", String(MIN_SIDEBAR_WIDTH));
      splitterEl.title = state.sidebarCollapsed
        ? "Click or drag right to expand side panel ([)"
        : "Drag to resize panels · Double-click to reset width";
    }
    if (primaryEl) {
      primaryEl._lastSidebarLayoutSig = layoutSig;
    }
    if (state.harnessMenuOpen) {
      positionHarnessDropdownMenu();
    }
  }

  function toggleSidebarCollapsed(forceCollapsed) {
    state.sidebarCollapsed =
      typeof forceCollapsed === "boolean" ? forceCollapsed : !state.sidebarCollapsed;
    syncSidebarLayoutDom();
    saveSidebarLayoutPrefs();
  }

  function setSidebarWidth(nextWidthPx, opts = {}) {
    state.sidebarWidth = clampSidebarWidth(nextWidthPx);
    if (state.sidebarCollapsed && opts.uncollapse !== false) {
      state.sidebarCollapsed = false;
    }
    syncSidebarLayoutDom();
    if (!opts.skipSave) {
      saveSidebarLayoutPrefs();
    }
  }

  function applySplitterDragClientX(wsEl, startClientX, clientX, wasCollapsed, preDragWidth) {
    const wsRect = wsEl.getBoundingClientRect();
    const padLeft = parseFloat(getComputedStyle(wsEl).paddingLeft || "18") || 18;
    const rawWidth = clientX - wsRect.left - padLeft;
    if (wasCollapsed) {
      if (clientX - startClientX > 10 || rawWidth >= MIN_SIDEBAR_WIDTH * 0.5) {
        state.sidebarCollapsed = false;
        state.sidebarWidth = clampSidebarWidth(rawWidth);
        syncSidebarLayoutDom();
      }
      return;
    }
    state.sidebarCollapsed = rawWidth < SNAP_COLLAPSE_SIDEBAR_WIDTH;
    state.sidebarWidth = state.sidebarCollapsed ? preDragWidth : clampSidebarWidth(rawWidth);
    syncSidebarLayoutDom();
  }

  function onWorkspaceSplitterKeydown(e) {
    if (e.key === "ArrowLeft" && !state.sidebarCollapsed) {
      e.preventDefault();
      setSidebarWidth(state.sidebarWidth - 24);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      if (state.sidebarCollapsed) {
        toggleSidebarCollapsed(false);
      } else {
        setSidebarWidth(state.sidebarWidth + 24);
      }
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleSidebarCollapsed();
    }
  }

  function startWorkspaceSplitterDrag(splitterEl, wsEl, startClientX) {
    const wasCollapsed = Boolean(state.sidebarCollapsed);
    const preDragWidth = clampSidebarWidth(state.sidebarWidth);
    let didMove = false;
    splitterEl.classList.add("is-dragging");
    document.body.classList.add("is-resizing-panels");

    const onMouseMove = (ev) => {
      ev.preventDefault();
      if (Math.abs(ev.clientX - startClientX) > 3) didMove = true;
      applySplitterDragClientX(wsEl, startClientX, ev.clientX, wasCollapsed, preDragWidth);
    };
    const onTouchMove = (ev) => {
      if (!ev.touches || !ev.touches.length) return;
      const cx = ev.touches[0].clientX;
      if (Math.abs(cx - startClientX) > 3) didMove = true;
      applySplitterDragClientX(wsEl, startClientX, cx, wasCollapsed, preDragWidth);
    };
    const stopDrag = () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", stopDrag);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("touchend", stopDrag);
      window.removeEventListener("touchcancel", stopDrag);
      splitterEl.classList.remove("is-dragging");
      document.body.classList.remove("is-resizing-panels");
      if (wasCollapsed && !didMove) {
        toggleSidebarCollapsed(false);
      } else {
        saveSidebarLayoutPrefs();
      }
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", stopDrag);
    window.addEventListener("touchmove", onTouchMove, { passive: true });
    window.addEventListener("touchend", stopDrag);
    window.addEventListener("touchcancel", stopDrag);
  }

  function initWorkspaceSplitter() {
    const splitterEl = document.getElementById("workspaceSplitter");
    const wsEl = document.getElementById("workspaceContainer");
    if (!splitterEl || !wsEl) return;

    splitterEl.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      startWorkspaceSplitterDrag(splitterEl, wsEl, e.clientX);
    });
    splitterEl.addEventListener("touchstart", (e) => {
      if (!e.touches || !e.touches.length) return;
      startWorkspaceSplitterDrag(splitterEl, wsEl, e.touches[0].clientX);
    }, { passive: true });
    splitterEl.addEventListener("dblclick", (e) => {
      e.preventDefault();
      state.sidebarCollapsed ? toggleSidebarCollapsed(false) : setSidebarWidth(DEFAULT_SIDEBAR_WIDTH);
    });
    splitterEl.addEventListener("keydown", onWorkspaceSplitterKeydown);
  }

  const QUEUE_CLIENT_CACHE_TTL_MS = 45000;
  const QUEUE_CLIENT_STALE_MAX_MS = 300000;

  function buildQueueCacheKey(viewer, scope, author) {
    const v = String(viewer || "").trim().toLowerCase();
    const sc = String(scope || "reviewer").trim().toLowerCase();
    const a = String(author || "").trim().toLowerCase();
    return `${v}|${sc}|${a}`;
  }

  function cloneQueuePayload(data) {
    if (typeof structuredClone === "function") {
      return structuredClone(data);
    }
    return JSON.parse(JSON.stringify(data));
  }

  function invalidateClientQueueCache() {
    state.queueCacheByKey = {};
  }

  function recomputeApprovalSlotsForViewer(c, viewerNorm) {
    const reqCount = c.is_agent_cl ? 2 : 1;
    const humanCr2 = Array.isArray(c.human_cr2) ? c.human_cr2 : [];
    const slots = [];
    humanCr2.slice(0, reqCount).forEach(voter => {
      const em = String(voter.email || "").trim().toLowerCase();
      const tag = em === viewerNorm ? "You (+2 ✓)" : `✓ ${voter.short || em.split("@")[0]} +2`;
      slots.push({ state: "done", text: tag });
    });
    const remaining = Math.max(0, reqCount - humanCr2.length);
    if (remaining >= 2) {
      slots.push({ state: "needed", text: "Needs two +2s" });
    } else if (remaining === 1) {
      slots.push({ state: "needed", text: "Needs +2" });
    }
    return slots;
  }

  function applyViewerProjectionToCl(c, viewerNorm, viewerShort) {
    const humanCr2 = Array.isArray(c.human_cr2) ? c.human_cr2 : [];
    c.approved_by_viewer = humanCr2.some(v => String(v.email || "").toLowerCase() === viewerNorm);
    if (Array.isArray(c.reviewers_detail)) {
      c.reviewers_detail.forEach(p => {
        p.is_viewer = String(p.email || "").toLowerCase() === viewerNorm;
      });
      const me = c.reviewers_detail.find(p => p.is_viewer);
      c.viewer_cr_vote = me ? Number(me.cr_vote || 0) : (c.approved_by_viewer ? 2 : 0);
      c.is_explicit_reviewer = Boolean(me);
    } else {
      c.viewer_cr_vote = c.approved_by_viewer ? 2 : 0;
    }
    if (Array.isArray(c.ccs_detail)) {
      c.ccs_detail.forEach(p => {
        p.is_viewer = String(p.email || "").toLowerCase() === viewerNorm;
      });
      c.is_cc = c.ccs_detail.some(p => p.is_viewer);
    }
    if (Array.isArray(c.attention_set_emails)) {
      c.viewer_in_attention_set = c.attention_set_emails.some(
        em => String(em || "").toLowerCase() === viewerNorm
      );
    } else if (Array.isArray(c.reviewers_detail)) {
      c.viewer_in_attention_set = c.reviewers_detail.some(
        p => p.is_viewer && p.in_attention_set
      );
    }
    c.viewer_autosubmit = (c.autosubmit_voters || []).some(
      s => String(s || "").toLowerCase() === viewerShort
    );
    c.viewer_presubmit_ready = (c.presubmit_ready_voters || []).some(
      s => String(s || "").toLowerCase() === viewerShort
    );
    c.approval_slots = recomputeApprovalSlotsForViewer(c, viewerNorm);
  }

  function reprojectQueuePayloadForViewer(baseData, targetViewer) {
    const cloned = cloneQueuePayload(baseData);
    const viewerNorm = String(targetViewer || "").trim().toLowerCase();
    const viewerShort = (viewerNorm.split("@")[0]) || "you";
    cloned.viewer = viewerNorm;
    (cloned.changes || []).forEach(c => applyViewerProjectionToCl(c, viewerNorm, viewerShort));
    const byNum = new Map((cloned.changes || []).map(c => [Number(c.number), c]));
    (cloned.stacks || []).forEach(st => {
      st.commits = (st.commits || []).map(sc => byNum.get(Number(sc.number)) || sc);
    });
    return cloned;
  }

  function lookupClientQueueCache(viewer, scope, author) {
    const now = Date.now();
    const exactKey = buildQueueCacheKey(viewer, scope, author);
    const exact = state.queueCacheByKey[exactKey];
    if (exact && (now - exact.fetchedAt) < QUEUE_CLIENT_STALE_MAX_MS) {
      const age = now - exact.fetchedAt;
      return {
        payload: cloneQueuePayload(exact.data),
        isPartial: Boolean(exact.isPartial),
        needsRevalidate: Boolean(exact.isPartial || age >= QUEUE_CLIENT_CACHE_TTL_MS),
      };
    }
    if (String(scope || "").toLowerCase() === "team" && viewer) {
      const suffix = `|team|${String(author || "").trim().toLowerCase()}`;
      const donorEntry = Object.entries(state.queueCacheByKey)
        .map(([k, val]) => ({ k, val }))
        .find(({ k, val }) => k.endsWith(suffix) && val && !val.isPartial && (now - val.fetchedAt) < QUEUE_CLIENT_STALE_MAX_MS);
      if (donorEntry) {
        return {
          payload: reprojectQueuePayloadForViewer(donorEntry.val.data, viewer),
          isPartial: false,
          needsRevalidate: true,
        };
      }
    }
    return null;
  }

  function storeClientQueueCache(viewer, scope, author, data, isPartial) {
    const resolvedViewer = (data && data.viewer) || viewer || state.viewer;
    const resolvedScope = (data && data.scope) || scope || state.scope;
    const key = buildQueueCacheKey(resolvedViewer, resolvedScope, author);
    state.queueCacheByKey[key] = {
      fetchedAt: Date.now(),
      isPartial: Boolean(isPartial),
      data: cloneQueuePayload(data),
    };
  }

  function showToast(msg) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.style.display = "block";
    clearTimeout(window._toastTimer);
    window._toastTimer = setTimeout(() => { el.style.display = "none"; }, 3400);
  }

  const _HTML_ESCAPE_TEST_RE = /[&<>"]/;
  const _HTML_ESCAPE_REPLACE_RE = /[&<>"]/g;
  const _HTML_ESCAPE_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
  };
  function _escapeHtmlChar(ch) {
    return _HTML_ESCAPE_MAP[ch];
  }

  function escapeHtml(str) {
    if (str == null || str === "") return "";
    const s = typeof str === "string" ? str : String(str);
    if (!_HTML_ESCAPE_TEST_RE.test(s)) return s;
    return s.replace(_HTML_ESCAPE_REPLACE_RE, _escapeHtmlChar);
  }

  const _JS_QUOTE_ESCAPE_TEST_RE = /[\\'\"\n\r]/;

  function escapeJsQuote(str) {
    if (str == null || str === "") return "";
    const s = typeof str === "string" ? str : String(str);
    if (!_JS_QUOTE_ESCAPE_TEST_RE.test(s)) return s;
    return s
      .replace(/\\/g, "\\\\")
      .replace(/'/g, "\\'")
      .replace(/"/g, "&quot;")
      .replace(/\n/g, "\\n")
      .replace(/\r/g, "\\r");
  }

  function getScopeDisplayLabel(scope) {
    if (scope === "dashboard") return "Your ▸ Dashboard";
    if (scope === "owner") return "Outgoing reviews";
    if (scope === "wip") return "Work in progress";
    if (scope === "cc") return "CCed on";
    if (scope === "starred") return "Starred changes";
    if (scope === "closed") return "Recently closed";
    if (scope === "team") return "All open (Team)";
    return "Incoming reviews";
  }

  function syncGlobalLoadingBar() {
    const bar = document.getElementById("globalLoadingBar");
    const isActionBusy = Boolean(
      state.votingInFlight || state.submittingInFlight || state.sidecarUpdateInFlight
    );
    const isBusy = Boolean(state.queueLoading || state.diffLoadingCount > 0 || isActionBusy);
    if (bar) {
      bar.classList.toggle("active", isBusy);
    }
    if (!state.queueLoading && !isActionBusy && state.diffLoadingCount > 0) {
      const drawerDot = document.getElementById("drawerDot");
      const drawerStatus = document.getElementById("drawerStatus");
      if (drawerDot) drawerDot.className = "dot dot-blue-pulse";
      if (drawerStatus && state.diffLoadingLabel) {
        drawerStatus.textContent = state.diffLoadingLabel;
      }
    } else if (!state.queueLoading && !isActionBusy && state.diffLoadingCount === 0) {
      syncSwarmIndicatorsOnly();
    }
  }

  function setQueueControlsDisabled(disabled) {
    if (disabled) {
      closeHarnessMenu();
    }
    const controlIds = [
      "viewerSelect",
      "scopeSelect",
      "btnRefreshQueue",
      "queueSearchInput",
      "authorFilterSelect",
      "queueSortSelect",
      "btnVotePlus1",
      "btnVoteCurrent",
      "btnVotePlus2Auto",
      "btnVoteAutosubmit",
      "btnPresubmitReady",
      "btnVoteStack",
      "btnNavPrev",
      "btnNavNext",
      "btnHarnessMenuToggle",
      "btnAiAnalysis",
      "btnRunSwarm",
      "btnAutoFix",
    ];
    controlIds.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.disabled = disabled;
    });
    if (disabled) {
      ["btnSubmitCl", "btnBatchSubmitCls"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = true;
      });
    } else {
      syncToolbarNavButtons();
      if (typeof syncSubmitClButtonsDom === "function") {
        syncSubmitClButtonsDom(getActiveCommitsForDiff());
      }
    }
  }

  function setQueueLoadingState(isLoading, titleText, subText) {
    state.queueLoading = Boolean(isLoading);
    const refreshBtn = document.getElementById("btnRefreshQueue");
    const qOverlay = document.getElementById("queuePaneOverlay");
    const dOverlay = document.getElementById("diffPaneOverlay");
    if (refreshBtn) refreshBtn.classList.toggle("is-refreshing", isLoading);
    if (qOverlay) {
      qOverlay.classList.toggle("active", isLoading);
      qOverlay.setAttribute("aria-hidden", isLoading ? "false" : "true");
    }
    if (dOverlay) {
      dOverlay.classList.toggle("active", isLoading);
      dOverlay.setAttribute("aria-hidden", isLoading ? "false" : "true");
    }
    if (isLoading) {
      const qTitle = document.getElementById("queueOverlayTitle");
      const qSub = document.getElementById("queueOverlaySub");
      const dTitle = document.getElementById("diffOverlayTitle");
      const dSub = document.getElementById("diffOverlaySub");
      if (qTitle && titleText) qTitle.textContent = titleText;
      if (qSub && subText) qSub.textContent = subText;
      if (dTitle) dTitle.textContent = titleText || "Updating Diff Workspace…";
      if (dSub) dSub.textContent = "Refreshing commit selection and unified diffs…";
      const drawerDot = document.getElementById("drawerDot");
      const drawerStatus = document.getElementById("drawerStatus");
      if (drawerDot) drawerDot.className = "dot dot-blue-pulse";
      if (drawerStatus && titleText) drawerStatus.textContent = titleText;
    }
    setQueueControlsDisabled(isLoading);
    syncGlobalLoadingBar();
  }

  function renderDiffLoadingPlaceholderHtml(titleText, subText) {
    const subHtml = subText
      ? `<div style="font-size:11.5px;color:var(--g-muted);">${escapeHtml(subText)}</div>`
      : "";
    return `<div class="diff-loading-card">
      <div class="diff-loading-row">
        <span class="btn-spinner"></span>
        <span>${escapeHtml(titleText || "Loading unified diff…")}</span>
      </div>
      ${subHtml}
      <div class="diff-skeleton-lines">
        <div class="diff-skeleton-bar" style="width:92%;"></div>
        <div class="diff-skeleton-bar" style="width:78%;"></div>
        <div class="diff-skeleton-bar" style="width:64%;"></div>
      </div>
    </div>`;
  }

  async function initApp() {
    if (window.__GERRIT_UI_BOOTSTRAP__ && Array.isArray(window.__GERRIT_UI_BOOTSTRAP__.changes)) {
      applyQueuePayload(window.__GERRIT_UI_BOOTSTRAP__);
      return;
    }
    if (state.viewer || state.roster.length > 0) {
      renderViewerDropdown();
    }
    checkSidecarUpdateStatus({ force: false });
    ensureSidecarUpdatePollingTimer();
    await fetchQueueData(state.viewer, state.scope, {
      loadingTitle: "Loading Gerrit Review Queue…",
      loadingSub: "Fetching open CLs, approval slots, and Buganizer metadata",
    });
  }

  async function manualRefreshQueue() {
    const btn = document.getElementById("btnRefreshQueue");
    if (btn) {
      btn.disabled = true;
      btn.classList.add("is-refreshing");
    }
    try {
      invalidateClientQueueCache();
      checkSidecarUpdateStatus({ force: true });
      await fetchQueueData(state.viewer, state.scope, {
        forceRefresh: true,
        preserveDiffCache: false,
        loadingTitle: "Refreshing Gerrit Queue…",
        loadingSub: `Syncing latest patchsets, labels, and diffs for ${state.viewer}`,
      });
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("is-refreshing");
      }
    }
  }

  function syncReadyDrawerStatus() {
    if (getActiveRunningSwarmClNumbers().length > 0 || state.diffLoadingCount > 0) return;
    const drawerStatus = document.getElementById("drawerStatus");
    if (!drawerStatus) return;
    const suffix = state.sidebarHydrating ? " · loading remaining commits…" : "";
    drawerStatus.textContent =
      `Ready · Loaded ${state.changes.length} open CLs (${getScopeDisplayLabel(state.scope)} · ${state.viewer})${suffix}`;
  }

  function triggerServerPrewarmOnly(viewer) {
    const vParam = viewer ? `&viewer=${encodeURIComponent(viewer)}` : "";
    sidecarFetch(`/api/gerrit/queue?prewarm_only=1${vParam}`).catch(() => {});
  }

  async function hydrateFullQueueInBackground(seq, viewer, scope, authorQ) {
    let qs = `?scope=${encodeURIComponent(scope)}${viewer ? `&viewer=${encodeURIComponent(viewer)}` : ""}&prewarm=1`;
    if (authorQ) {
      qs += `&author=${encodeURIComponent(authorQ)}`;
    }
    try {
      const resp = await sidecarFetch(`/api/gerrit/queue${qs}`);
      const fullData = await resp.json();
      if (!fullData || fullData.error) return;
      storeClientQueueCache(viewer, scope, authorQ, fullData, Boolean(fullData.is_partial));
      if (seq !== state.queueFetchSeq) return;
      state.sidebarHydrating = false;
      applyQueuePayload(fullData, {
        preserveDiffCache: true,
        preserveScroll: true,
        silentRevalidate: true,
      });
      syncReadyDrawerStatus();
    } catch (_err) {
      if (seq === state.queueFetchSeq && state.sidebarHydrating) {
        state.sidebarHydrating = false;
        const summary = getFilteredStacksSummary();
        renderQueueList(summary.visibleStacks, summary);
      }
    }
  }

  function tryServeClientQueueCache(seq, viewer, scope, authorQ, opts) {
    if (opts.forceRefresh || opts.silent) return false;
    const cachedHit = lookupClientQueueCache(viewer, scope, authorQ);
    if (!cachedHit) return false;
    state.sidebarHydrating = Boolean(cachedHit.isPartial);
    if (state.queueLoading) setQueueLoadingState(false);
    applyQueuePayload(cachedHit.payload, opts);
    syncReadyDrawerStatus();
    if (cachedHit.needsRevalidate) {
      hydrateFullQueueInBackground(seq, cachedHit.payload.viewer || viewer, scope, authorQ);
    }
    return true;
  }

  async function fetchSilentQueue(seq, baseQs, viewer, scope, authorQ, opts) {
    const silentQs = `${baseQs}&prewarm=1${opts.forceRefresh ? "&refresh=1" : ""}`;
    const resp = await sidecarFetch(`/api/gerrit/queue${silentQs}`);
    const data = await resp.json();
    if (!data || data.error) return;
    storeClientQueueCache(viewer, scope, authorQ, data, Boolean(data.is_partial));
    if (seq !== state.queueFetchSeq) return;
    state.sidebarHydrating = false;
    applyQueuePayload(data, { ...opts, silentRevalidate: true });
  }

  async function fetchProgressiveQueueStage1(seq, baseQs, viewer, scope, authorQ, opts) {
    const stage1Qs = `${baseQs}&fast=1${opts.forceRefresh ? "&refresh=1" : ""}`;
    const resp = await sidecarFetch(`/api/gerrit/queue${stage1Qs}`);
    const data = await resp.json();
    if (seq !== state.queueFetchSeq) return;
    if (data.error) {
      showToast("Error loading Gerrit queue: " + data.error);
      return;
    }
    if (opts.preserveDiffCache === false) {
      state.diffsByCl = {};
    }
    storeClientQueueCache(viewer, scope, authorQ, data, Boolean(data.is_partial));
    state.sidebarHydrating = Boolean(data.is_partial);
    setQueueLoadingState(false);
    applyQueuePayload(data, opts);
    syncReadyDrawerStatus();
    if (data.is_partial || data.stale) {
      hydrateFullQueueInBackground(seq, data.viewer || viewer, scope, authorQ);
    } else {
      triggerServerPrewarmOnly(data.viewer || viewer);
    }
  }

  async function fetchQueueData(viewerOverride, scopeOverride, opts = {}) {
    const seq = ++state.queueFetchSeq;
    const v = viewerOverride || state.viewer || "";
    const sc = scopeOverride || state.scope || "reviewer";
    const authorQ = opts.author || "";
    if (tryServeClientQueueCache(seq, v, sc, authorQ, opts)) return;

    if (!opts.silent) {
      state.sidebarHydrating = false;
      const defaultTitle = authorQ
        ? `Querying Gerrit for owner:${authorQ}…`
        : `Loading ${getScopeDisplayLabel(sc)}…`;
      const defaultSub = `Viewer: ${v || "auto"} · Fetching top commit & dismissing spinner asynchronously`;
      setQueueLoadingState(true, opts.loadingTitle || defaultTitle, opts.loadingSub || defaultSub);
    }
    const baseQs = `?scope=${encodeURIComponent(sc)}${v ? `&viewer=${encodeURIComponent(v)}` : ""}${authorQ ? `&author=${encodeURIComponent(authorQ)}` : ""}`;
    try {
      if (opts.silent) {
        await fetchSilentQueue(seq, baseQs, v, sc, authorQ, opts);
      } else {
        await fetchProgressiveQueueStage1(seq, baseQs, v, sc, authorQ, opts);
      }
    } catch (err) {
      if (seq === state.queueFetchSeq && !opts.silent) {
        showToast("Could not reach local Gerrit UI server: " + err);
      }
    } finally {
      if (seq === state.queueFetchSeq && state.queueLoading) {
        setQueueLoadingState(false);
        ensureActiveDiffsLoaded({ preserveScroll: Boolean(opts.preserveScroll) });
      }
    }
  }

  function maybeReloadOnUiVersionChange(serverUiVersion) {
    if (
      serverUiVersion &&
      window.__GERRIT_UI_VERSION__ &&
      !window.__GERRIT_UI_VERSION__.startsWith("__") &&
      serverUiVersion !== window.__GERRIT_UI_VERSION__
    ) {
      window.location.reload();
      return true;
    }
    return false;
  }

  function applyQueuePayload(data, opts = {}) {
    if (maybeReloadOnUiVersionChange(data && data.ui_version)) {
      return;
    }
    state.viewer = data.viewer || state.viewer;
    state.authEmail = data.auth_email || state.authEmail || "";
    state.scope = data.scope || state.scope || "reviewer";
    state.roster = data.roster || ["jansene@google.com", "joshuaduong@google.com", "rkir@google.com", "whollins@google.com"];
    state.changes = data.changes || [];
    state.stacks = data.stacks || [];
    if (state.remoteSearchChangesByCl && typeof mergeRemoteSearchPayloadIntoState === "function") {
      const remoteList = Object.values(state.remoteSearchChangesByCl);
      if (remoteList.length > 0) {
        mergeRemoteSearchPayloadIntoState({ changes: remoteList }, { skipStoreRemote: true });
      }
    }
    if (!state.sidebarHydrating && state.checkedClNumbers && state.checkedClNumbers.size > 0) {
      const validNums = new Set(state.changes.map(c => Number(c.number)));
      Array.from(state.checkedClNumbers).forEach(num => {
        if (!validNums.has(Number(num))) state.checkedClNumbers.delete(num);
      });
    }
    const activeVoter = String(state.authEmail || state.viewer || "").toLowerCase();
    const viewerSessionNums = getSessionApprovedNumsForViewer(state.viewer);
    if (viewerSessionNums.length > 0 && (!activeVoter || String(state.viewer || "").toLowerCase() === activeVoter)) {
      markClsApprovedOptimistically(viewerSessionNums, { skipCacheInvalidate: true });
    }
    state.changes.forEach(c => {
      const cNum = Number(c.number);
      const nextPs = Number(c.patch_set || 1);
      if (cNum && state.lastKnownPatchSetByCl[cNum] && state.lastKnownPatchSetByCl[cNum] !== nextPs) {
        delete state.diffsByCl[cNum];
        delete state.aiAnalysisByCl[cNum];
      }
      if (cNum) {
        state.lastKnownPatchSetByCl[cNum] = nextPs;
      }
      if (c.swarm_status) {
        mergePolledSwarmStatus(c.number, c.swarm_status, { silentToast: true });
      }
    });
    if (Array.isArray(data.prefetched_diffs)) {
      data.prefetched_diffs.forEach(d => {
        state.diffsByCl[d.number] = d;
        if (typeof syncPgClMergeability === "function") {
          syncPgClMergeability(d.number, d);
        } else {
          const clNum = Number(d.number);
          const targetCl = (state.changes || []).find(x => Number(x && x.number) === clNum) ||
            (state.queue || []).find(x => Number(x && x.number) === clNum);
          if (targetCl) {
            if (typeof d.mergeable === "boolean") targetCl.mergeable = d.mergeable;
            if (d.contains_git_conflicts || d.mergeable === false) targetCl.contains_git_conflicts = true;
            if (Array.isArray(d.conflicts)) targetCl.conflicts = d.conflicts;
          }
        }
        if (Array.isArray(d.code_questions)) {
          mergeTranscriptCodeQuestions(d.code_questions, { silentDom: true });
        }
      });
      if (typeof syncAemuMapButtons === "function") syncAemuMapButtons();
    }
    const scopeEl = document.getElementById("scopeSelect");
    if (scopeEl && state.scope) {
      scopeEl.value = state.scope;
    }
    renderViewerDropdown();
    renderAll({
      preserveScroll: Boolean(opts.preserveScroll),
      silentRevalidate: Boolean(opts.silentRevalidate),
    });
    if ((state.searchQuery || "").trim() && typeof maybeTriggerRemoteSearchForMissingCl === "function") {
      maybeTriggerRemoteSearchForMissingCl(state.searchQuery);
    }
    ensureSwarmPollingTimer();
    ensureCodeQuestionPollingTimer();
    ensureAiAnalysisPollingTimer();
  }

  function renderViewerDropdown() {
    const sel = document.getElementById("viewerSelect");
    if (!sel) return;
    const emails = Array.from(new Set([state.viewer, ...state.roster].filter(Boolean)));
    const placeholder = !state.viewer
      ? `<option value="" selected>Auto-detect viewer…</option>`
      : "";
    sel.innerHTML = placeholder + emails
      .map(em => `<option value="${escapeHtml(em)}" ${em === state.viewer ? "selected" : ""}>${escapeHtml(em)}</option>`)
      .join("");
  }

  function onViewerChange() {
    const newViewer = document.getElementById("viewerSelect").value;
    const shortViewer = (newViewer || "").split("@")[0] || newViewer;
    state.viewer = newViewer;
    state.remoteSearchChangesByCl = {};
    state.remoteSearchedQueries = new Set();
    state.selectedClNumber = null;
    state.selectedStackId = null;
    clearCheckedCls({ silent: true });
    state.explicitRadarSelection = false;
    if (window.__GERRIT_UI_BOOTSTRAP__ && Array.isArray(window.__GERRIT_UI_BOOTSTRAP__.changes)) {
      const vNorm = normalizeViewerInput(newViewer);
      const vShort = (vNorm.split("@")[0]) || "you";
      state.changes.forEach(c => applyViewerProjectionToCl(c, vNorm, vShort));
      renderAll();
      return;
    }
    fetchQueueData(newViewer, state.scope, {
      loadingTitle: `Switching viewer to ${shortViewer}@…`,
      loadingSub: `${getScopeDisplayLabel(state.scope)} · Reloading open CLs and +2 status for ${newViewer}`,
    });
  }

  function isFullVisibilityScope(scope) {
    return scope === "owner" || scope === "dashboard" || scope === "closed" || scope === "cc" || scope === "wip" || scope === "starred";
  }

  function onScopeChange() {
    const prevScope = state.scope;
    const newScope = document.getElementById("scopeSelect").value;
    const scopeLabel = getScopeDisplayLabel(newScope);
    state.scope = newScope;
    state.selectedClNumber = null;
    state.selectedStackId = null;
    clearCheckedCls({ silent: true });
    state.explicitRadarSelection = false;
    if (isFullVisibilityScope(newScope) && !isFullVisibilityScope(prevScope)) {
      state.savedNonOwnerGates = { ...state.gates };
      state.gates.presubmit = false;
      state.gates.hideMine = false;
      state.gates.hideSubmittable = false;
    } else if (!isFullVisibilityScope(newScope) && isFullVisibilityScope(prevScope) && state.savedNonOwnerGates) {
      state.gates = { ...state.savedNonOwnerGates };
      state.savedNonOwnerGates = null;
    }
    syncGateControls();
    fetchQueueData(state.viewer, newScope, {
      loadingTitle: `Loading "${scopeLabel}"…`,
      loadingSub: `Viewer: ${state.viewer} · Querying Gerrit changes and stack dependencies`,
    });
  }

  function setSortMode(mode) {
    const valid = [
      "smart",
      "updated_desc",
      "updated_asc",
      "starred_first",
      "attention_first",
      "subject_asc",
    ].includes(mode) ? mode : "smart";
    state.sortBy = valid;
    const sel = document.getElementById("queueSortSelect");
    if (sel && sel.value !== valid) {
      sel.value = valid;
    }
    renderAll();
  }

  function getClUpdatedSortKey(cl) {
    if (!cl) return 0;
    const ts = Number(cl.updated_ts || 0);
    if (ts > 0) return ts;
    if (cl.updated_iso) {
      if (cl._cachedUpdatedIso === cl.updated_iso && typeof cl._cachedUpdatedSortKey === "number") {
        return cl._cachedUpdatedSortKey;
      }
      const parsed = Date.parse(cl.updated_iso);
      if (!Number.isNaN(parsed)) {
        const val = parsed / 1000;
        cl._cachedUpdatedIso = cl.updated_iso;
        cl._cachedUpdatedSortKey = val;
        return val;
      }
    }
    return -Number(cl.hours_idle || 0) * 3600;
  }

  function compareCommitsByUpdated(a, b) {
    if (state.sortBy === "starred_first") {
      const starA = isClStarred(a) ? 1 : 0;
      const starB = isClStarred(b) ? 1 : 0;
      if (starA !== starB) return starB - starA;
    } else if (state.sortBy === "attention_first") {
      const attnA = isClInViewerAttentionSet(a) ? 1 : 0;
      const attnB = isClInViewerAttentionSet(b) ? 1 : 0;
      if (attnA !== attnB) return attnB - attnA;
    } else if (state.sortBy === "subject_asc") {
      const subA = String((a && a.subject) || "").toLowerCase();
      const subB = String((b && b.subject) || "").toLowerCase();
      const sc = subA.localeCompare(subB);
      if (sc !== 0) return sc;
    }
    const diff = getClUpdatedSortKey(b) - getClUpdatedSortKey(a);
    if (diff !== 0) {
      return state.sortBy === "updated_asc" ? -diff : diff;
    }
    return Number(b.number || 0) - Number(a.number || 0);
  }

  function getStackUpdatedSortKey(st) {
    const commits = (st && st.commits) || [];
    if (!commits.length) return 0;
    const keys = commits.map(getClUpdatedSortKey);
    return state.sortBy === "updated_asc" ? Math.min(...keys) : Math.max(...keys);
  }

  function sortStacksIfNeeded(stacks) {
    if (
      state.sortBy !== "updated_desc" &&
      state.sortBy !== "updated_asc" &&
      state.sortBy !== "starred_first" &&
      state.sortBy !== "attention_first" &&
      state.sortBy !== "subject_asc"
    ) {
      return stacks;
    }
    return [...stacks].sort((a, b) => {
      const ca = (a.commits && a.commits[0]) || null;
      const cb = (b.commits && b.commits[0]) || null;
      if (state.sortBy === "starred_first") {
        const sa = (a.commits || []).some(isClStarred) ? 1 : 0;
        const sb = (b.commits || []).some(isClStarred) ? 1 : 0;
        if (sa !== sb) return sb - sa;
      } else if (state.sortBy === "attention_first") {
        const aa = (a.commits || []).some(isClInViewerAttentionSet) ? 1 : 0;
        const ab = (b.commits || []).some(isClInViewerAttentionSet) ? 1 : 0;
        if (aa !== ab) return ab - aa;
      } else if (state.sortBy === "subject_asc" && ca && cb) {
        const sc = String(ca.subject || "").localeCompare(String(cb.subject || ""));
        if (sc !== 0) return sc;
      }
      const ka = getStackUpdatedSortKey(a);
      const kb = getStackUpdatedSortKey(b);
      if (kb !== ka) {
        return state.sortBy === "updated_asc" ? ka - kb : kb - ka;
      }
      const na = (ca && ca.number) || 0;
      const nb = (cb && cb.number) || 0;
      return nb - na;
    });
  }

  function syncGateControls(hiddenMineCountOverride = null, submittableCountOverride = null, starredCountOverride = null) {
    const alreadyMineCount = hiddenMineCountOverride !== null
      ? Number(hiddenMineCountOverride)
      : state.changes.filter(isClApprovedByViewer).length;
    const submittableCount = submittableCountOverride !== null
      ? Number(submittableCountOverride)
      : state.changes.filter(isClSubmittable).length;
    const starredCount = starredCountOverride !== null
      ? Number(starredCountOverride)
      : (state.changes || []).filter(isClStarred).length;
    const pvEl = document.getElementById("gatePresubmit");
    const stEl = document.getElementById("gateStarred");
    const hmEl = document.getElementById("gateHideMine");
    const hsEl = document.getElementById("gateHideSubmittable");
    const agEl = document.getElementById("gateAgent");
    if (pvEl) {
      const on = Boolean(state.gates.presubmit);
      if (pvEl._lastGateOn !== on) {
        pvEl._lastGateOn = on;
        pvEl.classList.toggle("active", on);
        pvEl.setAttribute("aria-pressed", String(on));
        pvEl.textContent = `${on ? "✓ " : ""}PV +1/+2`;
      }
    }
    if (stEl) {
      const on = Boolean(state.gates.starredOnly);
      const stSig = `${on ? 1 : 0}:${starredCount}`;
      if (stEl._lastGateSig !== stSig) {
        stEl._lastGateSig = stSig;
        stEl.classList.toggle("active", on);
        stEl.setAttribute("aria-pressed", String(on));
        stEl.innerHTML = `${on ? "✓ " : ""}⭐ Starred (<span id="starredCount">${starredCount}</span>)`;
      }
    }
    if (hmEl) {
      const on = Boolean(state.gates.hideMine);
      const hmSig = `${on ? 1 : 0}:${alreadyMineCount}`;
      if (hmEl._lastGateSig !== hmSig) {
        hmEl._lastGateSig = hmSig;
        hmEl.classList.toggle("active", on);
        hmEl.setAttribute("aria-pressed", String(on));
        hmEl.innerHTML = `${on ? "✓ " : ""}Hide +2 by Me (<span id="hiddenMineCount">${alreadyMineCount}</span>)`;
      }
    }
    if (hsEl) {
      const on = Boolean(state.gates.hideSubmittable);
      const hsSig = `${on ? 1 : 0}:${submittableCount}`;
      if (hsEl._lastGateSig !== hsSig) {
        hsEl._lastGateSig = hsSig;
        hsEl.classList.toggle("active", on);
        hsEl.setAttribute("aria-pressed", String(on));
        hsEl.innerHTML = `${on ? "✓ " : ""}Hide Submittable (<span id="hiddenSubmittableCount">${submittableCount}</span>)`;
      }
    }
    if (agEl) {
      const on = Boolean(state.gates.agent);
      if (agEl._lastGateOn !== on) {
        agEl._lastGateOn = on;
        agEl.classList.toggle("active", on);
        agEl.setAttribute("aria-pressed", String(on));
        agEl.textContent = `${on ? "✓ " : ""}Agent Verified (0 unresolved)`;
      }
    }
    syncPresetCollapseControls();
  }

  function toggleGate(name) {
    state.gates[name] = !state.gates[name];
    state.includeUngatedMatches = false;
    state.explicitRadarSelection = false;
    syncGateControls();
    renderAll();
  }

  function toggleFilterPresets() {
    state.filterPresetsCollapsed = !state.filterPresetsCollapsed;
    syncPresetCollapseControls();
  }

  function toggleAuthorPresets() {
    state.authorPresetsCollapsed = !state.authorPresetsCollapsed;
    syncPresetCollapseControls();
  }

  function toggleTypePresets() {
    state.typePresetsCollapsed = !state.typePresetsCollapsed;
    syncPresetCollapseControls();
  }

  function togglePgFiltersDrawer() {
    state.filtersDrawerOpen = !state.filtersDrawerOpen;
    syncPresetCollapseControls();
  }

  function countActiveQueueFilters() {
    let count = 0;
    if (state.authorFilter && state.authorFilter !== "all") count += 1;
    if (state.typeFilter && state.typeFilter !== "all") count += 1;
    if (state.sortBy && state.sortBy !== "smart") count += 1;
    if (state.gates.presubmit) count += 1;
    if (state.gates.starredOnly) count += 1;
    if (state.gates.hideMine) count += 1;
    if (state.gates.hideSubmittable) count += 1;
    if (state.gates.agent) count += 1;
    return count;
  }

  function syncPresetCollapseControls() {
    const drawerEl = document.getElementById("pgFiltersDrawer");
    const drawerBtnEl = document.getElementById("pgFiltersDrawerBtn");
    const drawerOpen = Boolean(state.filtersDrawerOpen);
    const activeCount = countActiveQueueFilters();
    if (drawerEl) {
      drawerEl.classList.toggle("open", drawerOpen);
    }
    if (drawerBtnEl) {
      const arrow = drawerOpen ? "▴" : "▾";
      drawerBtnEl.textContent = activeCount > 0 ? `Filters (${activeCount}) ${arrow}` : `Filters ${arrow}`;
      drawerBtnEl.classList.toggle("has-active-filter", activeCount > 0);
      drawerBtnEl.setAttribute("aria-expanded", String(drawerOpen));
    }

    const searchBarEl = document.getElementById("queueSearchBar");
    const authorBtnEl = document.getElementById("authorPresetsToggleBtn");
    const authorCollapsed = Boolean(state.authorPresetsCollapsed);
    if (searchBarEl) {
      searchBarEl.classList.toggle("collapsed", authorCollapsed);
    }
    if (authorBtnEl) {
      const arrow = authorCollapsed ? "▾" : "▴";
      const hasAuthor = Boolean(state.authorFilter && state.authorFilter !== "all");
      const shortAuthor = hasAuthor
        ? (state.authorFilter.length > 10 ? state.authorFilter.slice(0, 9) + "…" : state.authorFilter)
        : "";
      authorBtnEl.textContent = hasAuthor ? `👤 ${shortAuthor} ${arrow}` : `Chips ${arrow}`;
      authorBtnEl.classList.toggle("has-active-filter", hasAuthor);
      authorBtnEl.setAttribute("aria-expanded", String(!authorCollapsed));
    }

    const typeToolbarEl = document.getElementById("queueTypeToolbar");
    const typeBtnEl = document.getElementById("typePresetsToggleBtn");
    const typeCollapsed = Boolean(state.typePresetsCollapsed);
    if (typeToolbarEl) {
      typeToolbarEl.classList.toggle("collapsed", typeCollapsed);
    }
    if (typeBtnEl) {
      const arrow = typeCollapsed ? "▾" : "▴";
      const hasType = Boolean(state.typeFilter && state.typeFilter !== "all");
      const activeType = hasType ? COMMIT_TYPE_ORDER.find(t => t.id === state.typeFilter) : null;
      const typeLabel = activeType ? activeType.short : "Types";
      typeBtnEl.textContent = `${typeLabel} ${arrow}`;
      typeBtnEl.classList.toggle("has-active-filter", hasType);
      typeBtnEl.setAttribute("aria-expanded", String(!typeCollapsed));
    }

    const filterToolbarEl = document.getElementById("queueFilterToolbar");
    const filterBtnEl = document.getElementById("filterPresetsToggleBtn");
    const filterCollapsed = Boolean(state.filterPresetsCollapsed);
    if (filterToolbarEl) {
      filterToolbarEl.classList.toggle("collapsed", filterCollapsed);
    }
    if (filterBtnEl) {
      const arrow = filterCollapsed ? "▾" : "▴";
      const hasAgentGate = Boolean(state.gates.agent);
      filterBtnEl.textContent = hasAgentGate ? `✓ Agent ${arrow}` : `More ${arrow}`;
      filterBtnEl.classList.toggle("has-active-filter", hasAgentGate);
      filterBtnEl.setAttribute("aria-expanded", String(!filterCollapsed));
    }
  }

  function setGroupMode(mode) {
    const valid = mode === "type" || mode === "stack" ? mode : "dashboard";
    state.groupMode = valid;
    const grpDashEl = document.getElementById("grpDashboard");
    const grpStackEl = document.getElementById("grpStack");
    const grpTypeEl = document.getElementById("grpType");
    if (grpDashEl) grpDashEl.classList.toggle("active", valid === "dashboard");
    if (grpStackEl) grpStackEl.classList.toggle("active", valid === "stack");
    if (grpTypeEl) grpTypeEl.classList.toggle("active", valid === "type");
    renderAll();
  }

  function setTypeFilter(typeId) {
    state.typeFilter = typeId;
    ["all", ...COMMIT_TYPE_ORDER.map(t => t.id)].forEach(id => {
      const el = document.getElementById("typeTab_" + id);
      if (el) el.classList.toggle("active", id === typeId);
    });
    renderAll();
  }

  function getCommitTypeObj(cl) {
    if (cl && cl.commit_type && cl.commit_type.id) {
      return cl.commit_type;
    }
    const filesArr = (cl && Array.isArray(cl.files)) ? cl.files : [];
    const bugsLen = (cl && Array.isArray(cl.bugs)) ? cl.bugs.length : 0;
    if (
      cl &&
      cl._cachedCommitTypeObj &&
      cl._ctSubj === cl.subject &&
      cl._ctFilesLen === filesArr.length &&
      cl._ctBugsLen === bugsLen
    ) {
      return cl._cachedCommitTypeObj;
    }
    const rawSubj = String((cl && cl.subject) || "");
    const subj = rawSubj.toLowerCase();
    let hasTestFile = false;
    for (let i = 0; i < filesArr.length; i++) {
      const fItem = filesArr[i];
      const fStr = String((fItem && typeof fItem === "object" ? fItem.file : fItem) || "").toLowerCase();
      if (fStr.includes("ets") || fStr.includes("test")) {
        hasTestFile = true;
        break;
      }
    }
    let resolved = COMMIT_TYPE_ORDER[5];
    if (subj.includes("ets") || subj.includes("pytest") || subj.startsWith("test") || hasTestFile) {
      resolved = COMMIT_TYPE_ORDER[2];
    } else if (subj.startsWith("perf") || subj.includes("latency") || subj.includes("throughput") || subj.includes("optimiz")) {
      resolved = COMMIT_TYPE_ORDER[1];
    } else if (subj.startsWith("fix") || subj.includes("bug") || subj.includes("crash") || bugsLen > 0) {
      resolved = COMMIT_TYPE_ORDER[0];
    } else if (subj.startsWith("refactor") || subj.startsWith("cleanup") || subj.includes("clean up") || subj.includes("dead code")) {
      resolved = COMMIT_TYPE_ORDER[3];
    } else if (subj.startsWith("feat") || subj.includes("ui") || subj.includes("add ")) {
      resolved = COMMIT_TYPE_ORDER[4];
    }
    if (cl && typeof cl === "object") {
      cl._ctSubj = cl.subject;
      cl._ctFilesLen = filesArr.length;
      cl._ctBugsLen = bugsLen;
      cl._cachedCommitTypeObj = resolved;
    }
    return resolved;
  }

  function getCommitTypeId(cl) {
    return getCommitTypeObj(cl).id;
  }

  function getClBugs(cl) {
    if (!cl) return [];
    if (Array.isArray(cl.bugs) && cl.bugs.length > 0) {
      return cl.bugs;
    }
    if (Array.isArray(cl.bug_ids) && cl.bug_ids.length > 0) {
      return cl.bug_ids.map(id => ({
        id: String(id),
        url: `https://b.corp.google.com/issues/${id}`,
        priority: "",
        severity: "",
        status: "",
        title: "",
      }));
    }
    return [];
  }

  function getBugPriorityClass(priority, severity) {
    const p = String(priority || "").toUpperCase();
    const s = String(severity || "").toUpperCase();
    if (p === "P0" || p === "P1" || s === "S0" || s === "S1") {
      return "bug-pri-high";
    }
    if (p === "P2" || s === "S2") {
      return "bug-pri-med";
    }
    return "bug-pri-low";
  }

  function renderBugBadgesHtml(cl, opts = {}) {
    const bugs = getClBugs(cl);
    if (!bugs.length) return "";
    const interactive = opts.interactive !== false;
    return bugs.map(b => {
      const bugId = String(b.id || "");
      const bugUrl = b.url || `https://b.corp.google.com/issues/${bugId}`;
      const pri = String(b.priority || "").toUpperCase();
      const sev = String(b.severity || "").toUpperCase();
      const status = String(b.status || "");
      const title = String(b.title || "");
      const priSevParts = [];
      if (pri) priSevParts.push(pri);
      if (sev) priSevParts.push(sev);
      const priSevLabel = priSevParts.join(" · ");
      const priCls = getBugPriorityClass(pri, sev);
      const priTagHtml = priSevLabel
        ? `<span class="bug-pri-tag ${priCls}">${escapeHtml(priSevLabel)}</span>`
        : "";
      const tooltipParts = [`b/${bugId}`];
      if (priSevLabel) tooltipParts.push(`[${priSevLabel}]`);
      if (status) tooltipParts.push(`(${status})`);
      if (title) tooltipParts.push(`— ${title}`);
      const tooltip = tooltipParts.join(" ");
      const hoverAttrs = bugId
        ? ` data-bug-id="${escapeHtml(bugId)}" onmouseenter="showPgBugHovercard(event, '${escapeHtml(bugId)}')" onmouseleave="scheduleHidePgBugHovercard()"`
        : "";
      if (!interactive) {
        return `<span class="bug-chip"${hoverAttrs} title="${escapeHtml(tooltip)}">🐛 b/${escapeHtml(bugId)}${priTagHtml}</span>`;
      }
      return `<a class="bug-chip"${hoverAttrs} href="${escapeHtml(bugUrl)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(tooltip)}" onclick="event.stopPropagation()">🐛 b/${escapeHtml(bugId)} ↗${priTagHtml}</a>`;
    }).join(" ");
  }

  function makeSessionApprovalKey(viewer, clNum) {
    const vNorm = String(viewer || state.viewer || "").trim().toLowerCase();
    return `${vNorm}:${Number(clNum)}`;
  }

  function getSessionApprovedNumsForViewer(viewer) {
    const prefix = `${String(viewer || state.viewer || "").trim().toLowerCase()}:`;
    const out = [];
    state.sessionApprovedCls.forEach(entry => {
      const s = String(entry);
      if (s.startsWith(prefix)) {
        const num = Number(s.slice(prefix.length));
        if (!Number.isNaN(num) && num > 0) out.push(num);
      }
    });
    return out;
  }

  function isClApprovedByViewer(cl) {
    if (!cl) return false;
    if (cl.approved_by_viewer) return true;
    if (!state.sessionApprovedCls || state.sessionApprovedCls.size === 0) return false;
    return state.sessionApprovedCls.has(makeSessionApprovalKey(state.viewer, cl.number));
  }

  function getClPrimaryAuthorShort(cl) {
    if (!cl) return "unknown";
    if (
      cl._cachedAuthorShortRaw === cl.owner_short &&
      cl._cachedAuthorEmailRaw === cl.owner_email &&
      typeof cl._cachedPrimaryAuthorShort === "string" &&
      cl._cachedPrimaryAuthorShort
    ) {
      return cl._cachedPrimaryAuthorShort;
    }
    const short = String(cl.owner_short || (cl.owner_email || "").split("@")[0] || "unknown").toLowerCase();
    cl._cachedAuthorShortRaw = cl.owner_short;
    cl._cachedAuthorEmailRaw = cl.owner_email;
    cl._cachedPrimaryAuthorShort = short;
    return short;
  }

  function isClBotOwner(cl) {
    if (!cl) return false;
    if (typeof cl.owner_is_bot === "boolean") return cl.owner_is_bot;
    const s = getClPrimaryAuthorShort(cl);
    return (
      s.startsWith("groupagent-") ||
      s.startsWith("corpagent-") ||
      s.startsWith("android-build-") ||
      s.includes("-presubmit") ||
      s.includes("robot") ||
      s.includes("automerger")
    );
  }

  function hasActiveAuthorOrSearchFilter() {
    return (state.authorFilter && state.authorFilter !== "all") || Boolean((state.searchQuery || "").trim());
  }

  function matchesAuthorFilter(cl) {
    const af = (state.authorFilter || "all").toLowerCase();
    if (!af || af === "all") return true;
    const ownerShort = getClPrimaryAuthorShort(cl);
    const authorShort = String(cl.author_short || "").toLowerCase();
    const uploaderShort = String(cl.uploader_short || "").toLowerCase();
    return ownerShort === af || authorShort === af || uploaderShort === af;
  }

  const PG_SEARCH_OPERATORS = [
    { op: "status:open", label: "Open changes", desc: "Changes currently open and in review", icon: "🟢", cat: "Status" },
    { op: "status:closed", label: "Closed changes", desc: "Changes merged or abandoned (status:closed)", icon: "📦", cat: "Status" },
    { op: "owner:self", label: "Owned by you", desc: "Changes authored / uploaded by you", icon: "👤", cat: "People" },
    { op: "reviewer:self", label: "Reviewer: you", desc: "Incoming reviews where you are reviewer", icon: "👀", cat: "People" },
    { op: "cc:self", label: "CCed on", desc: "Changes where you are CCed (not reviewer/owner)", icon: "👀", cat: "People" },
    { op: "is:cc", label: "CCed on", desc: "Changes where you are in CC only", icon: "👀", cat: "People" },
    { op: "attention:self", label: "Your turn", desc: "Changes currently in your Attention Set", icon: "⚡", cat: "Attention" },
    { op: "is:submittable", label: "Submittable", desc: "Changes with satisfied requirements ready to submit", icon: "✓", cat: "Requirements" },
    { op: "is:starred", label: "Starred changes", desc: "Changes marked with a star", icon: "⭐", cat: "Starred" },
    { op: "has:draft", label: "Has drafts", desc: "Changes with unpublished draft comments", icon: "📝", cat: "Comments" },
    { op: "is:wip", label: "Work in progress", desc: "Changes marked WIP (not ready for review)", icon: "⏳", cat: "Status" },
    { op: "label:Code-Review=+2", label: "CR +2 (Approved)", desc: "Changes with Code-Review +2 vote", icon: "👍", cat: "Votes" },
    { op: "label:Code-Review=-2", label: "CR -2 (Blocked)", desc: "Changes blocked by Code-Review -2", icon: "🚫", cat: "Votes" },
    { op: "label:Presubmit-Verified=+1", label: "Presubmit Passing", desc: "Changes with passing Presubmit-Verified +1", icon: "🤖", cat: "Votes" },
    { op: "is:reviewed", label: "Reviewed by you", desc: "Changes you have approved or voted on", icon: "✓", cat: "Review" },
    { op: "is:unreviewed", label: "Unreviewed by you", desc: "Changes you have not yet voted on", icon: "📬", cat: "Review" },
    { op: "status:merged", label: "Merged changes", desc: "Changes successfully submitted to branch", icon: "🔀", cat: "Status" },
    { op: "status:abandoned", label: "Abandoned changes", desc: "Changes that have been closed/abandoned", icon: "✕", cat: "Status" },
    { op: "branch:goog/studio-main", label: "Branch filter", desc: "Filter by git destination branch", icon: "🌿", cat: "Git" },
    { op: "project:", label: "Project filter", desc: "Filter by Git repo project name", icon: "📁", cat: "Git" },
    { op: "topic:", label: "Topic filter", desc: "Filter by Gerrit topic tag", icon: "🏷️", cat: "Metadata" },
    { op: "hashtag:", label: "Hashtag filter", desc: "Filter by Gerrit hashtag (e.g. hashtag:qemu-next or #aemu)", icon: "#", cat: "Metadata" },
    { op: "has:hashtag", label: "Has hashtag", desc: "Changes with at least one hashtag", icon: "#", cat: "Metadata" },
    { op: "file:", label: "File filter", desc: "Filter by modified file path", icon: "📄", cat: "Files" },
    { op: "message:", label: "Message filter", desc: "Filter by commit message keywords", icon: "💬", cat: "Commit" },
    { op: "change:", label: "Change ID / Number", desc: "Filter by Gerrit Change-Id (I...) or change number", icon: "🆔", cat: "Commit" },
  ];

  const PG_SEARCH_PRESETS = [
    { label: "⚡ Your turn", token: "attention:self", title: "Show changes where it's your turn in Attention Set" },
    { label: "📝 Has drafts", token: "has:draft", title: "Show changes with unsaved/unpublished draft comments" },
    { label: "⭐ Starred", token: "is:starred", title: "Show starred changes" },
    { label: "✓ Submittable", token: "is:submittable", title: "Show changes ready to submit" },
    { label: "👤 Owner: self", token: "owner:self", title: "Show changes owned by you" },
    { label: "👀 Reviewer: self", token: "reviewer:self", title: "Show incoming changes where you are reviewer" },
    { label: "👀 CCed", token: "cc:self", title: "Show changes where you are in CC" },
    { label: "⏳ WIP", token: "is:wip", title: "Show work-in-progress changes" },
    { label: "📦 Closed", token: "status:closed", title: "Show recently closed (merged or abandoned) changes" },
  ];

  const PG_SEARCH_HELP_OPERATORS = [
    // Status
    { op: "status:open", alt: "is:open", cat: "Status", desc: "Changes currently open and undergoing review", example: "status:open", icon: "🟢" },
    { op: "status:merged", alt: "is:merged", cat: "Status", desc: "Changes submitted and merged into branch", example: "status:merged", icon: "🔀" },
    { op: "status:abandoned", alt: "is:abandoned", cat: "Status", desc: "Changes that have been closed or abandoned", example: "status:abandoned", icon: "✕" },
    { op: "status:closed", alt: "is:closed", cat: "Status", desc: "Changes that are closed (merged or abandoned)", example: "status:closed owner:self", icon: "📦" },
    { op: "is:wip", alt: "status:wip", cat: "Status", desc: "Changes marked as Work-in-Progress (not ready for review)", example: "is:wip owner:self", icon: "⏳" },
    { op: "is:submittable", cat: "Status", desc: "Changes with satisfied requirements ready to submit", example: "is:submittable owner:self", icon: "✓" },
    { op: "is:starred", alt: "has:star", cat: "Status", desc: "Changes marked with a personal star", example: "is:starred status:open", icon: "⭐" },
    { op: "is:reviewed", cat: "Status", desc: "Changes you have approved or voted on", example: "is:reviewed status:open", icon: "✓" },
    { op: "is:unreviewed", cat: "Status", desc: "Changes you have not yet approved or voted on", example: "is:unreviewed reviewer:self", icon: "📬" },

    // People
    { op: "owner:self", alt: "owner:me", cat: "People", desc: "Changes authored or uploaded by you", example: "owner:self status:open", icon: "👤" },
    { op: "owner:<email>", alt: "author:<email>", cat: "People", desc: "Changes authored by a specific user name or email", example: "owner:joshuaduong", icon: "👤" },
    { op: "reviewer:self", alt: "reviewer:me", cat: "People", desc: "Incoming reviews where you are requested reviewer", example: "reviewer:self status:open", icon: "👀" },
    { op: "reviewer:<email>", cat: "People", desc: "Changes where a specific user is a reviewer", example: "reviewer:reviewer@google.com", icon: "👀" },
    { op: "cc:self", alt: "is:cc", cat: "People", desc: "Changes where you are CCed (not direct reviewer or owner)", example: "cc:self status:open", icon: "👀" },
    { op: "attention:self", alt: "is:attention", cat: "People", desc: "Changes currently in your Attention Set (Your turn)", example: "attention:self", icon: "⚡" },
    { op: "author:<name>", alt: "uploader:<name>", cat: "People", desc: "Filter by commit author or patchset uploader name", example: "author:Joshua", icon: "✍️" },

    // Votes & Labels
    { op: "label:Code-Review=+2", alt: "label:cr=+2", cat: "Votes", desc: "Changes approved with Code-Review +2", example: "label:Code-Review=+2 status:open", icon: "👍" },
    { op: "label:Code-Review=-2", alt: "label:cr=-2", cat: "Votes", desc: "Changes blocked with Code-Review -2", example: "label:Code-Review=-2 status:open", icon: "🚫" },
    { op: "label:Code-Review=+1", alt: "label:cr=+1", cat: "Votes", desc: "Changes with positive Code-Review +1 vote", example: "label:Code-Review=+1", icon: "👍" },
    { op: "label:Presubmit-Verified=+1", alt: "label:v=+1", cat: "Votes", desc: "Changes with passing Presubmit-Verified (+1)", example: "label:Presubmit-Verified=+1", icon: "🤖" },
    { op: "label:Presubmit-Verified=-1", alt: "label:v=-1", cat: "Votes", desc: "Changes with failing Presubmit-Verified (-1)", example: "label:Presubmit-Verified=-1", icon: "❌" },
    { op: "label:Code-Review=any", cat: "Votes", desc: "Changes that have any Code-Review vote recorded", example: "label:Code-Review=any", icon: "🗳️" },

    // Comments & Drafts
    { op: "has:draft", cat: "Comments", desc: "Changes with unpublished draft comments by you", example: "has:draft", icon: "📝" },
    { op: "has:unresolved", cat: "Comments", desc: "Changes with unresolved comment threads", example: "has:unresolved owner:self", icon: "💬" },
    { op: "has:hashtag", alt: "is:hashtagged", cat: "Comments", desc: "Changes tagged with at least one hashtag", example: "has:hashtag status:open", icon: "#" },

    // Git & Projects
    { op: "project:<name>", alt: "repo:<name>", cat: "Git", desc: "Filter by repository project path or name", example: "project:platform/hardware/generic/goldfish", icon: "📁" },
    { op: "branch:<name>", cat: "Git", desc: "Filter by destination branch", example: "branch:goog/studio-main", icon: "🌿" },
    { op: "topic:<name>", cat: "Git", desc: "Filter by Gerrit topic tag", example: "topic:aemu-polish", icon: "🏷️" },
    { op: "hashtag:<name>", alt: "#<name>", cat: "Git", desc: "Filter by hashtag (e.g. hashtag:qemu-next or #goldfish)", example: "#qemu-next", icon: "#" },
    { op: "file:<path>", alt: "path:<path>", cat: "Git", desc: "Filter changes modifying specific file or directory", example: "file:goldfish", icon: "📄" },

    // Commits & IDs
    { op: "change:<id>", alt: "ag:<num>", cat: "Commits", desc: "Filter by Gerrit Change number or Change-Id (I...)", example: "change:43299169", icon: "🆔" },
    { op: "message:<text>", cat: "Commits", desc: "Filter commit message body and subject", example: "message:\"fix crash\"", icon: "💬" },
    { op: "b/<id>", cat: "Commits", desc: "Filter by linked Buganizer issue number", example: "b/12345678", icon: "🐛" },

    // Syntax & Logic
    { op: "A OR B", cat: "Syntax", desc: "Match changes satisfying either condition A or condition B", example: "owner:self OR reviewer:self", icon: "🔀" },
    { op: "-operator", cat: "Syntax", desc: "Negate any operator with a minus prefix", example: "status:open -is:wip -is:reviewed", icon: "➖" },
    { op: "( A OR B ) C", cat: "Syntax", desc: "Group conditions with parentheses", example: "(owner:self OR reviewer:self) status:open", icon: "()" },
    { op: "\"phrase\"", cat: "Syntax", desc: "Quote multi-word strings or exact phrases", example: "message:\"device vsync\"", icon: "❝" },
  ];

  function escapeRegex(str) {
    return String(str || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function savePgStarredCls() {
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (ls && state.starredCls) {
        ls.setItem("gerrit_ui_pg_starred_cls", JSON.stringify(Array.from(state.starredCls)));
      }
    } catch (e) {}
  }

  function isClMerged(c) {
    if (!c) return false;
    const st = c.status;
    if (st === "NEW" || st === "open") return !!c.is_merged;
    if (st === "MERGED") return true;
    const stUp = String(st || "").trim().toUpperCase();
    return stUp === "MERGED" || !!c.is_merged;
  }

  function isClAbandoned(c) {
    if (!c) return false;
    const st = c.status;
    if (st === "NEW" || st === "open") return !!c.is_abandoned;
    if (st === "ABANDONED") return true;
    const stUp = String(st || "").trim().toUpperCase();
    return stUp === "ABANDONED" || !!c.is_abandoned;
  }

  function isClClosed(c) {
    if (!c) return false;
    const st = c.status;
    if (st === "NEW" || st === "open") {
      return !!(c.is_merged || c.is_abandoned || c.is_closed);
    }
    return isClMerged(c) || isClAbandoned(c) || !!c.is_closed;
  }

  function isClStarred(cl) {
    if (!cl) return false;
    const num = Number(cl.number);
    if (num && state.starredCls && state.starredCls.has(num)) return true;
    if (cl.starred === true) return true;
    if (Array.isArray(cl.stars) && cl.stars.includes("star")) return true;
    return false;
  }

  function togglePgStarChange(clNum, event) {
    if (event && typeof event.stopPropagation === "function") event.stopPropagation();
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return;
    const currentlyStarred = isClStarred({ number: num });
    const willStar = !currentlyStarred;
    if (willStar) {
      state.starredCls.add(num);
    } else {
      state.starredCls.delete(num);
    }
    const targetCl = (state.changes || []).find(c => Number(c.number) === num);
    if (targetCl) {
      targetCl.starred = willStar;
      targetCl.stars = willStar ? ["star"] : [];
    }
    savePgStarredCls();
    syncPgStarButtonsDom(num, willStar);
    syncGateControls();
    if (
      state.gates.starredOnly ||
      state.sortBy === "starred_first" ||
      state.scope === "starred" ||
      (state.searchQuery && (state.searchQuery.includes("is:starred") || state.searchQuery.includes("has:star")))
    ) {
      renderAll();
    } else if (state.groupMode === "dashboard") {
      renderAll({ preserveScroll: true });
    }
    try {
      if (typeof sidecarFetch === "function") {
        sidecarFetch("/api/gerrit/star", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cl: num, starred: willStar, viewer: state.viewer }),
        }).catch(() => {});
      }
    } catch (e) {}
    showToast(willStar ? `⭐ Starred ag/${num}` : `☆ Unstarred ag/${num}`, 2000);
  }

  function syncPgStarButtonsDom(num, isStarred) {
    const clNum = Number(num);
    document.querySelectorAll(`.pg-star-btn[data-cl-num="${clNum}"]`).forEach(btn => {
      btn.classList.toggle("is-starred", isStarred);
      btn.setAttribute("title", isStarred ? `Unstar ag/${clNum} (*)` : `Star ag/${clNum} (*)`);
      btn.setAttribute("aria-label", isStarred ? "Starred" : "Not starred");
      const span = btn.querySelector("span");
      if (span) span.textContent = isStarred ? "★" : "☆";
    });
    const headerBtn = document.getElementById(`btnHeaderStar-${clNum}`);
    if (headerBtn) {
      headerBtn.classList.toggle("is-starred", isStarred);
      headerBtn.setAttribute("title", isStarred ? `Unstar change ag/${clNum} (*)` : `Star change ag/${clNum} (*)`);
      headerBtn.setAttribute("aria-label", isStarred ? "Starred" : "Not starred");
      const icon = headerBtn.querySelector(".pg-star-icon");
      if (icon) icon.textContent = isStarred ? "★" : "☆";
      const label = headerBtn.querySelector(".pg-star-label");
      if (label) label.textContent = isStarred ? "Starred" : "Star";
    }
  }

  function evaluatePgLabelFilter(cl, val) {
    if (!cl || !val) return false;
    const cleanVal = String(val).trim();
    const match = /^([a-zA-Z0-9_-]+)\s*(?:=|:)?\s*([+-]?\d+)$/i.exec(cleanVal);
    if (!match) {
      const labelKey = cleanVal.toLowerCase();
      if (labelKey === "cr" || labelKey === "code-review") return true;
      if (labelKey === "pv" || labelKey === "presubmit-verified") return true;
      if (labelKey === "v" || labelKey === "verified") return true;
      return false;
    }
    const labelName = match[1].toLowerCase();
    const targetScore = parseInt(match[2], 10);

    if (labelName === "cr" || labelName === "code-review") {
      const revs = typeof getFallbackReviewersForCl === "function" ? getFallbackReviewersForCl(cl).filter(p => !p.is_bot) : [];
      if (targetScore === 2) {
        return typeof isClCrSatisfied === "function" ? isClCrSatisfied(cl) : false;
      }
      if (targetScore === 1) {
        return (Array.isArray(cl.human_cr1) && cl.human_cr1.length > 0) || revs.some(p => Number(p.cr_vote || 0) === 1);
      }
      if (targetScore === -2) {
        let minVote = 0;
        revs.forEach(p => {
          const v = Number(p.cr_vote || 0);
          if (v < minVote) minVote = v;
        });
        return minVote <= -2;
      }
      if (targetScore === -1) {
        let minVote = 0;
        revs.forEach(p => {
          const v = Number(p.cr_vote || 0);
          if (v < minVote) minVote = v;
        });
        return minVote === -1;
      }
      if (targetScore === 0) {
        return revs.every(p => Number(p.cr_vote || 0) === 0);
      }
    }

    if (labelName === "pv" || labelName === "presubmit-verified") {
      const pv = typeof getClPresubmitDisplay === "function" ? getClPresubmitDisplay(cl) : { status: "" };
      if (targetScore >= 1) return pv.status === "passed";
      if (targetScore <= -1) return pv.status === "failed";
      if (targetScore === 0) return pv.status === "running" || pv.status === "queued" || pv.status === "not_started";
    }

    if (labelName === "v" || labelName === "verified") {
      const v = typeof getClVerifiedDisplay === "function" ? getClVerifiedDisplay(cl) : { score: 0 };
      if (targetScore > 0) return v.score >= targetScore;
      if (targetScore < 0) return v.score <= targetScore;
      if (targetScore === 0) return v.score === 0;
    }

    if (cl.labels && typeof cl.labels === "object") {
      for (const [k, obj] of Object.entries(cl.labels)) {
        if (k.toLowerCase() === labelName) {
          if (obj && typeof obj.value === "number") {
            return obj.value === targetScore;
          }
          if (obj && Array.isArray(obj.all)) {
            return obj.all.some(a => Number(a.value || 0) === targetScore);
          }
        }
      }
    }
    return false;
  }

  function splitTopLevelOr(raw) {
    const str = String(raw || "").trim();
    if (!str) return [];
    const parts = [];
    let current = "";
    let inDouble = false;
    let inSingle = false;
    let parenDepth = 0;

    for (let i = 0; i < str.length; i++) {
      const ch = str[i];
      if (ch === "\"" && !inSingle) {
        inDouble = !inDouble;
        current += ch;
      } else if (ch === "\x27" && !inDouble) {
        inSingle = !inSingle;
        current += ch;
      } else if (ch === "(" && !inDouble && !inSingle) {
        parenDepth++;
        current += ch;
      } else if (ch === ")" && !inDouble && !inSingle) {
        if (parenDepth > 0) parenDepth--;
        current += ch;
      } else if (!inDouble && !inSingle && parenDepth === 0) {
        const sub = str.slice(i);
        const m = sub.match(/^(?:OR|or)(?=\s|$)/);
        const prevChar = i > 0 ? str[i - 1] : " ";
        if (m && /\s/.test(prevChar)) {
          const part = current.trim();
          if (part) parts.push(part);
          current = "";
          i += m[0].length - 1;
          continue;
        }
        current += ch;
      } else {
        current += ch;
      }
    }
    const lastPart = current.trim();
    if (lastPart) parts.push(lastPart);
    return parts.filter(Boolean);
  }

  function splitTopLevelTokens(raw) {
    const str = String(raw || "").trim();
    if (!str) return [];
    const tokens = [];
    let current = "";
    let inDouble = false;
    let inSingle = false;
    let parenDepth = 0;

    for (let i = 0; i < str.length; i++) {
      const ch = str[i];
      if (ch === "\"" && !inSingle) {
        inDouble = !inDouble;
        current += ch;
      } else if (ch === "\x27" && !inDouble) {
        inSingle = !inSingle;
        current += ch;
      } else if (ch === "(" && !inDouble && !inSingle) {
        parenDepth++;
        current += ch;
      } else if (ch === ")" && !inDouble && !inSingle) {
        if (parenDepth > 0) parenDepth--;
        current += ch;
      } else if (!inDouble && !inSingle && parenDepth === 0 && /\s/.test(ch)) {
        const tok = current.trim();
        if (tok) tokens.push(tok);
        current = "";
      } else {
        current += ch;
      }
    }
    const lastTok = current.trim();
    if (lastTok) tokens.push(lastTok);
    return tokens.filter(Boolean);
  }

  function extractClNumberFromSearchToken(tokRaw) {
    const raw = String(tokRaw || "").trim().replace(/^["']|["']$/g, "");
    if (!raw || raw.startsWith("-")) return "";
    const urlMatch = raw.match(/https?:\/\/[^\s"'()]+\/\+\/(\d+)(?:\/\d+)?\/?$/i);
    if (urlMatch) return urlMatch[1];
    const prefixMatch = raw.match(/^(?:https?:\/\/)?(?:(?:change|change_id|id|cl|ag)[:/])+(?:(?:ag|cl)[:/])?(\d+)(?:\/\d+)?$/i);
    if (prefixMatch) return prefixMatch[1];
    const bareMatch = raw.match(/^(\d{5,10})$/);
    if (bareMatch) return bareMatch[1];
    return "";
  }

  let _lastExtractClQuery = null;
  let _lastExtractClResult = [];

  function extractSearchClNumbers(queryStr) {
    const raw = String(queryStr || "").trim();
    if (!raw) return [];
    if (raw === _lastExtractClQuery) return _lastExtractClResult;
    const out = [];
    const seen = new Set();
    raw.split(/[\s,()]+/).forEach(part => {
      if (!part || /^or$/i.test(part)) return;
      const clNum = extractClNumberFromSearchToken(part);
      if (clNum && clNum.length >= 5 && !seen.has(clNum)) {
        seen.add(clNum);
        out.push(clNum);
      }
    });
    _lastExtractClQuery = raw;
    _lastExtractClResult = out;
    return out;
  }

  function getClSearchHaystack(cl) {
    if (!cl) return "";
    const filesLen = Array.isArray(cl.files) ? cl.files.length : 0;
    const firstFile = filesLen > 0 ? (typeof cl.files[0] === "string" ? cl.files[0] : (cl.files[0] && cl.files[0].file) || "") : "";
    const tagsSig = Array.isArray(cl.hashtags) ? cl.hashtags.join(",") : "";
    const hKey = `${cl.number || ""}|${cl.subject || ""}|${cl.commit_message || ""}|${cl.owner_email || cl.author || ""}|${cl.owner_name || ""}|${cl.topic || ""}|${cl.branch || ""}|${cl.project || ""}|${cl.change_id || ""}|${tagsSig}|${filesLen}:${firstFile}`;
    if (cl._cachedSearchHaystackKey === hKey && typeof cl._cachedSearchHaystack === "string") {
      return cl._cachedSearchHaystack;
    }
    const bugs = getClBugs(cl);
    const bugStr = bugs.map(b => `b/${b.id} ${b.id} ${b.title || ""}`).join(" ");
    const filesStr = filesLen > 0
      ? cl.files.map(f => (typeof f === "string" ? f : f.file || "")).join(" ")
      : "";
    const hashtags = Array.isArray(cl.hashtags) ? cl.hashtags : [];
    const hashtagsStr = hashtags.map(t => `#${t} ${t}`).join(" ");
    const haystack = [
      `ag/${cl.number}`,
      String(cl.number || ""),
      cl.owner_short || "",
      cl.owner_email || "",
      cl.owner_name || "",
      cl.author_short || "",
      cl.author_email || "",
      cl.author_name || "",
      cl.uploader_short || "",
      cl.uploader_email || "",
      cl.uploader_name || "",
      cl.subject || "",
      cl.commit_message || "",
      cl.change_id || "",
      cl.project_short || "",
      cl.project || "",
      cl.branch || "",
      cl.topic || "",
      hashtagsStr,
      bugStr,
      filesStr,
    ]
      .join(" ")
      .toLowerCase();
    cl._cachedSearchHaystackKey = hKey;
    cl._cachedSearchHaystack = haystack;
    return haystack;
  }

  function evaluatePgAtomicSearchToken(cl, tokRaw) {
    let tok = String(tokRaw || "").trim();
    if (!tok) return true;
    let negated = false;
    if (tok.startsWith("-") && tok.length > 1) {
      negated = true;
      tok = tok.slice(1);
    }
    if ((tok.startsWith('"') && tok.endsWith('"')) || (tok.startsWith("'") && tok.endsWith("'"))) {
      tok = tok.slice(1, -1);
    }
    const tokLower = tok.toLowerCase();
    const directClNum = extractClNumberFromSearchToken(tok);
    if (directClNum) {
      const directMatched = String(cl.number || "") === directClNum;
      return negated ? !directMatched : directMatched;
    }

    const viewerNorm = normalizeViewerInput(state.viewer);
    const authNorm = String(state.authEmail || "").trim().toLowerCase();

    let matched = false;
    const colonIdx = tokLower.indexOf(":");
    if (colonIdx > 0) {
      const op = tokLower.slice(0, colonIdx);
      let val = tokLower.slice(colonIdx + 1);
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }

      if (op === "is" || op === "status") {
        if (val === "open" || val === "new") {
          matched = !cl.status || cl.status === "NEW" || cl.status === "open";
        } else if (val === "merged") {
          matched = cl.status === "MERGED" || cl.status === "merged";
        } else if (val === "abandoned") {
          matched = cl.status === "ABANDONED" || cl.status === "abandoned";
        } else if (val === "closed") {
          matched = (typeof isClClosed === "function")
            ? isClClosed(cl)
            : (cl.status === "MERGED" || cl.status === "merged" || cl.status === "ABANDONED" || cl.status === "abandoned" || Boolean(cl.is_closed));
        } else if (val === "wip" || val === "workinprogress") {
          matched = Boolean(cl.work_in_progress);
        } else if (val === "cc") {
          matched = (typeof isClViewerCcOnly === "function") ? isClViewerCcOnly(cl) : Boolean(cl.is_cc && !cl.is_explicit_reviewer);
        } else if (val === "submittable") {
          matched = isClSubmittable(cl);
        } else if (val === "starred") {
          matched = isClStarred(cl);
        } else if (val === "reviewed") {
          matched = isClApprovedByViewer(cl);
        } else if (val === "unreviewed") {
          matched = !isClApprovedByViewer(cl);
        } else if (val === "attention") {
          matched = isClInViewerAttentionSet(cl);
        } else if (val === "hashtagged") {
          matched = Array.isArray(cl.hashtags) && cl.hashtags.length > 0;
        } else {
          matched = getClSearchHaystack(cl).includes(val);
        }
      } else if (op === "has") {
        if (val === "draft" || val === "drafts") {
          const drafts = (typeof getPendingDraftsForCl === "function") ? getPendingDraftsForCl(cl.number) : [];
          matched = drafts.length > 0 || Number(cl.unresolved_drafts_count || 0) > 0;
        } else if (val === "star") {
          matched = isClStarred(cl);
        } else if (val === "unresolved") {
          matched = Number(cl.unresolved_comments || 0) > 0;
        } else if (val === "hashtag" || val === "hashtags") {
          matched = Array.isArray(cl.hashtags) && cl.hashtags.length > 0;
        } else {
          matched = getClSearchHaystack(cl).includes(val);
        }
      } else if (op === "owner" || op === "author" || op === "from" || op === "by") {
        if (val === "self" || val === "me") {
          matched = isClOwnedByViewer(cl);
        } else {
          const ownerShort = getClPrimaryAuthorShort(cl).toLowerCase();
          const authorShort = String(cl.author_short || "").toLowerCase();
          const ownerEmail = String(cl.owner_email || "").toLowerCase();
          const authorEmail = String(cl.author_email || "").toLowerCase();
          matched = ownerShort.includes(val) || authorShort.includes(val) || ownerEmail.includes(val) || authorEmail.includes(val);
        }
      } else if (op === "reviewer") {
        if (val === "self" || val === "me") {
          matched = (Array.isArray(cl.reviewers) && cl.reviewers.some(r => {
            const e = (r.email || r.username || "").toLowerCase();
            return (viewerNorm && e.includes(viewerNorm)) || (authNorm && e.includes(authNorm));
          })) || isClIncomingForViewer(cl);
        } else {
          matched = Array.isArray(cl.reviewers) && cl.reviewers.some(r => {
            const name = (r.name || r.display_name || r.username || r.email || "").toLowerCase();
            return name.includes(val);
          });
        }
      } else if (op === "cc") {
        if (val === "self" || val === "me") {
          matched = isClViewerCcOnly(cl);
        } else {
          matched = Array.isArray(cl.ccs) && cl.ccs.some(r => {
            const name = (r.name || r.display_name || r.username || r.email || "").toLowerCase();
            return name.includes(val);
          });
        }
      } else if (op === "attention") {
        if (val === "self" || val === "me") {
          matched = isClInViewerAttentionSet(cl) || (Array.isArray(cl.attention_set) && cl.attention_set.some(a => {
            const name = (a?.account?.email || a?.account?.username || a?.user || "").toLowerCase();
            return (viewerNorm && name.includes(viewerNorm)) || (authNorm && name.includes(authNorm));
          }));
        } else {
          matched = Array.isArray(cl.attention_set) && cl.attention_set.some(a => {
            const name = (a?.account?.name || a?.account?.email || a?.account?.username || a?.user || "").toLowerCase();
            return name.includes(val);
          });
        }
      } else if (op === "label") {
        matched = evaluatePgLabelFilter(cl, val);
      } else if (op === "project" || op === "repo") {
        const p = (cl.project || cl.project_short || "").toLowerCase();
        matched = p.includes(val);
      } else if (op === "branch") {
        const b = (cl.branch || "").toLowerCase();
        matched = b.includes(val);
      } else if (op === "topic") {
        const t = (cl.topic || "").toLowerCase();
        matched = t.includes(val);
      } else if (op === "hashtag" || op === "tag") {
        const cleanVal = val.startsWith("#") ? val.slice(1).toLowerCase() : val.toLowerCase();
        matched = (Array.isArray(cl.hashtags) ? cl.hashtags : []).some(t => String(t).toLowerCase().includes(cleanVal));
      } else if (op === "scope") {
        if (val === "dashboard") {
          matched = true;
        } else if (val === "owner" || val === "outgoing") {
          matched = isClOwnedByViewer(cl) && !cl.work_in_progress;
        } else if (val === "wip") {
          matched = isClOwnedByViewer(cl) && Boolean(cl.work_in_progress);
        } else if (val === "reviewer" || val === "incoming") {
          matched = isClIncomingForViewer(cl);
        } else if (val === "cc") {
          matched = (typeof isClViewerCcOnly === "function") ? isClViewerCcOnly(cl) : Boolean(cl.is_cc && !cl.is_explicit_reviewer);
        } else if (val === "closed") {
          matched = (typeof isClClosed === "function") ? isClClosed(cl) : Boolean(cl.status === "MERGED" || cl.status === "ABANDONED");
        } else {
          matched = getClSearchHaystack(cl).includes(val);
        }
      } else if (op === "file" || op === "path") {
        matched = Array.isArray(cl.files) && cl.files.some(f => (typeof f === "string" ? f : f.file || "").toLowerCase().includes(val));
      } else if (op === "message") {
        const msg = ((cl.subject || "") + " " + (cl.commit_message || "")).toLowerCase();
        matched = msg.includes(val);
      } else if (op === "change" || op === "change_id" || op === "id" || op === "cl" || op === "ag") {
        const cid = String(cl.change_id || (typeof getPgChangeId === "function" ? getPgChangeId(cl) : "")).toLowerCase();
        matched = String(cl.number || "") === val || (Boolean(cid) && (cid === val || cid.includes(val)));
      } else {
        matched = getClSearchHaystack(cl).includes(val);
      }
    } else {
      if (tok.startsWith("#") && tok.length > 1) {
        const tagQuery = tok.slice(1).toLowerCase();
        matched = (Array.isArray(cl.hashtags) ? cl.hashtags : []).some(t => String(t).toLowerCase().includes(tagQuery)) || getClSearchHaystack(cl).includes(tokLower);
      } else if (/^\d{5,}$/.test(tokLower)) {
        matched = String(cl.number || "") === tokLower || getClSearchHaystack(cl).includes(tokLower);
      } else {
        matched = getClSearchHaystack(cl).includes(tokLower);
      }
    }

    return negated ? !matched : matched;
  }

  const _pgSearchQueryAstCache = new Map();
  const PG_SEARCH_QUERY_AST_CACHE_MAX = 256;

  function parsePgSearchQueryAst(raw, depth = 0) {
    if (!raw || depth > 12) return { type: "true" };
    const cacheKey = depth === 0 ? raw : `${depth}:${raw}`;
    const cached = _pgSearchQueryAstCache.get(cacheKey);
    if (cached !== undefined) return cached;

    let ast;
    const orClauses = splitTopLevelOr(raw);
    if (orClauses.length > 1) {
      ast = {
        type: "or",
        children: orClauses.map(clause => parsePgSearchQueryAst(clause, depth + 1)),
      };
    } else {
      const tokens = splitTopLevelTokens(raw);
      if (!tokens.length) {
        ast = { type: "true" };
      } else {
        const children = [];
        for (let i = 0; i < tokens.length; i++) {
          const tok = tokens[i].trim();
          if (!tok) continue;
          if (tok.startsWith("(") && tok.endsWith(")")) {
            const inner = tok.slice(1, -1).trim();
            children.push(parsePgSearchQueryAst(inner, depth + 1));
          } else if (tok.startsWith("-(") && tok.endsWith(")")) {
            const inner = tok.slice(2, -1).trim();
            children.push({ type: "not", child: parsePgSearchQueryAst(inner, depth + 1) });
          } else {
            children.push({ type: "token", tok });
          }
        }
        ast = children.length === 0 ? { type: "true" } : (children.length === 1 ? children[0] : { type: "and", children });
      }
    }
    if (_pgSearchQueryAstCache.size >= PG_SEARCH_QUERY_AST_CACHE_MAX) {
      const oldestKey = _pgSearchQueryAstCache.keys().next().value;
      if (oldestKey !== undefined) _pgSearchQueryAstCache.delete(oldestKey);
    }
    _pgSearchQueryAstCache.set(cacheKey, ast);
    return ast;
  }

  function evalPgSearchQueryAst(cl, node) {
    if (!node || node.type === "true") return true;
    if (node.type === "token") return evaluatePgAtomicSearchToken(cl, node.tok);
    if (node.type === "not") return !evalPgSearchQueryAst(cl, node.child);
    if (node.type === "and") {
      for (let i = 0; i < node.children.length; i++) {
        if (!evalPgSearchQueryAst(cl, node.children[i])) return false;
      }
      return true;
    }
    if (node.type === "or") {
      for (let i = 0; i < node.children.length; i++) {
        if (evalPgSearchQueryAst(cl, node.children[i])) return true;
      }
      return false;
    }
    return true;
  }

  function matchesSearchQuery(cl, queryOverride = null, depth = 0) {
    if (!cl) return false;
    const raw = (queryOverride != null ? String(queryOverride) : (state.searchQuery || "")).trim();
    if (!raw) return true;
    if (depth > 12) return true; // bounded recursion safety guard
    return evalPgSearchQueryAst(cl, parsePgSearchQueryAst(raw, depth));
  }

  function matchesAuthorAndSearch(cl) {
    return matchesAuthorFilter(cl) && matchesSearchQuery(cl);
  }

  function getPgFilteredSearchSuggestions(queryStr = null) {
    const raw = queryStr !== null ? String(queryStr) : String(state.searchQuery || "");
    const trimmed = raw.trim();
    if (!trimmed) {
      return PG_SEARCH_OPERATORS.slice(0, 10);
    }
    const tokens = trimmed.split(/\s+/);
    const lastToken = tokens[tokens.length - 1].toLowerCase().replace(/^-/, "");
    if (!lastToken) {
      return PG_SEARCH_OPERATORS.slice(0, 10);
    }
    const matches = PG_SEARCH_OPERATORS.filter(item => {
      const op = item.op.toLowerCase();
      const label = item.label.toLowerCase();
      const desc = item.desc.toLowerCase();
      return op.includes(lastToken) || label.includes(lastToken) || desc.includes(lastToken);
    });
    return matches.length > 0 ? matches : PG_SEARCH_OPERATORS.slice(0, 6);
  }

  function openPgSearchSuggestions() {
    state.searchSuggestionsOpen = true;
    state.searchSuggestionIndex = -1;
    renderPgSearchSuggestionsPopoverHtml();
    const pop = document.getElementById("pgSearchSuggestionsPopover");
    if (pop) pop.style.display = "block";
  }

  function closePgSearchSuggestions() {
    state.searchSuggestionsOpen = false;
    state.searchSuggestionIndex = -1;
    const pop = document.getElementById("pgSearchSuggestionsPopover");
    if (pop) pop.style.display = "none";
  }

  function togglePgSearchSuggestions() {
    if (state.searchSuggestionsOpen) closePgSearchSuggestions();
    else openPgSearchSuggestions();
  }

  function applyPgSearchSuggestion(op) {
    if (!op) return;
    const inp = document.getElementById("queueSearchInput");
    const current = String(state.searchQuery || "").trim();
    let nextVal = "";
    if (!current) {
      nextVal = op;
    } else {
      const tokens = current.split(/\s+/);
      const lastTok = tokens[tokens.length - 1].toLowerCase().replace(/^-/, "");
      if (lastTok && (op.toLowerCase().startsWith(lastTok) || op.toLowerCase().includes(lastTok))) {
        tokens[tokens.length - 1] = op;
        nextVal = tokens.join(" ");
      } else {
        nextVal = `${current} ${op}`;
      }
    }
    if (op.endsWith(":")) {
      state.searchQuery = nextVal;
      if (inp) {
        inp.value = nextVal;
        inp.focus();
        inp.setSelectionRange(nextVal.length, nextVal.length);
      }
      renderPgSearchSuggestionsPopoverHtml();
    } else {
      state.searchQuery = nextVal;
      if (inp) {
        inp.value = nextVal;
      }
      closePgSearchSuggestions();
      onQueueSearchInput(nextVal);
      if (inp) inp.focus();
    }
  }

  function togglePgSearchPresetToken(token) {
    if (!token) return;
    const inp = document.getElementById("queueSearchInput");
    let current = String(state.searchQuery || "").trim();
    const escTok = escapeRegex(token);
    const regex = new RegExp(`(?:^|\\s)${escTok}(?:\\s|$)`, "i");
    if (regex.test(current)) {
      current = current.replace(regex, " ").trim();
    } else {
      current = current ? `${current} ${token}` : token;
    }
    state.searchQuery = current;
    if (inp) {
      inp.value = current;
    }
    onQueueSearchInput(current);
    renderPgSearchSuggestionsPopoverHtml();
    renderPgSearchPresetsDom();
  }

  let _lastSearchPresetsQuery = null;
  let _lastSearchPresetsHtml = "";

  function renderPgSearchPresetsHtml(isDrawer = false) {
    const current = String(state.searchQuery || "").trim();
    if (_lastSearchPresetsQuery === current && _lastSearchPresetsHtml) {
      return _lastSearchPresetsHtml;
    }
    const chipsHtml = PG_SEARCH_PRESETS.map(p => {
      if (!p._tokenRegex) {
        const escTok = escapeRegex(p.token);
        p._tokenRegex = new RegExp(`(?:^|\\s)${escTok}(?:\\s|$)`, "i");
      }
      const isActive = current ? p._tokenRegex.test(current) : false;
      const cls = isActive ? "pg-search-preset-chip is-active" : "pg-search-preset-chip";
      const clickHandler = `togglePgSearchPresetToken('${escapeHtml(p.token)}')`;
      return `<button type="button" class="${cls}" onclick="${clickHandler}" title="${escapeHtml(p.title)}" aria-pressed="${isActive ? "true" : "false"}">
        ${escapeHtml(p.label)}
      </button>`;
    });
    chipsHtml.push(
      `<button type="button" class="pg-search-preset-chip pg-search-manage-chip" onclick="openSavedSearchesModal()" title="Manage custom dashboard sections &amp; saved searches (Shift+Y)">📁 Saved Searches</button>`
    );
    if (current) {
      chipsHtml.push(
        `<button type="button" class="pg-search-preset-chip pg-search-reset-chip" onclick="clearQueueSearchAndAuthor()" title="Clear all search filters and reset dashboard">✕ Reset</button>`
      );
    }
    _lastSearchPresetsQuery = current;
    _lastSearchPresetsHtml = chipsHtml.join("");
    return _lastSearchPresetsHtml;
  }

  function renderPgSearchPresetsDom() {
    const html = renderPgSearchPresetsHtml(true);
    const drawerBar = document.getElementById("pgDrawerPresetsBar");
    if (drawerBar && drawerBar._lastPresetsHtml !== html) {
      drawerBar._lastPresetsHtml = html;
      drawerBar.innerHTML = html;
    }
    const popoverPresets = document.getElementById("pgSearchPopoverPresets");
    if (popoverPresets && popoverPresets._lastPresetsHtml !== html) {
      popoverPresets._lastPresetsHtml = html;
      popoverPresets.innerHTML = html;
    }
  }

  function renderPgSearchSuggestionsPopoverHtml() {
    const pop = document.getElementById("pgSearchSuggestionsPopover");
    if (!pop) return;
    const items = getPgFilteredSearchSuggestions();
    const hasQuery = Boolean((state.searchQuery || "").trim());

    const itemsHtml = items.map((item, idx) => {
      const isSelected = idx === state.searchSuggestionIndex;
      return `<div class="pg-search-suggestion-item ${isSelected ? "is-selected" : ""}" data-idx="${idx}" onclick="applyPgSearchSuggestion('${escapeHtml(item.op)}')">
        <span class="pg-search-item-icon" aria-hidden="true">${item.icon}</span>
        <span class="pg-search-item-badge">${escapeHtml(item.op)}</span>
        <span class="pg-search-item-desc" title="${escapeHtml(item.desc)}">${escapeHtml(item.desc)}</span>
        <span class="pg-search-item-cat">${escapeHtml(item.cat)}</span>
      </div>`;
    }).join("");

    const savedSearchesList = state.savedSearches || [];
    const savedChipsHtml = savedSearchesList.length > 0
      ? `<div class="pg-search-popover-saved-section">
          <div class="pg-search-saved-label">📁 Saved Searches</div>
          <div class="pg-search-saved-chips">
            ${savedSearchesList.map(s => `
              <button type="button" class="pg-saved-search-chip" onclick="applyPgSavedSearch('${escapeHtml(s.id)}')" title="Run: ${escapeHtml(s.query)}">
                <span>${escapeHtml(s.title)}</span>
                ${s.showOnDashboard ? '<span class="pg-saved-dash-icon" title="Shown on Dashboard">📌</span>' : ''}
              </button>
            `).join("")}
          </div>
        </div>`
      : "";

    pop.innerHTML = `
      <div class="pg-search-popover-head">
        <span>Quick Filters</span>
        <div class="pg-search-popover-head-actions">
          <button type="button" id="btnOpenSearchHelpPopover" class="pg-btn-search-help-popover" onclick="openPgSearchHelpDialog()" title="View all search operators (Alt+/)">❓ Operators</button>
          ${hasQuery ? `<button type="button" class="pg-btn-save-search-popover" onclick="openSaveCurrentSearchDialog()" title="Save query as custom dashboard section">💾 Save Search</button>` : ""}
          <button type="button" class="pg-btn-manage-searches-popover" onclick="openSavedSearchesModal()" title="Manage saved searches (Shift+Y)">📁 Saved</button>
          ${hasQuery ? `<button type="button" class="pg-btn-clear-search-popover" onclick="clearQueueSearchAndAuthor()">Clear All ✕</button>` : ""}
        </div>
      </div>
      <div id="pgSearchPopoverPresets" class="pg-search-popover-presets">
        ${renderPgSearchPresetsHtml(false)}
      </div>
      ${savedChipsHtml}
      <div class="pg-search-suggestions-list">
        ${itemsHtml}
      </div>
      <div class="pg-search-popover-footer">
        <span>↑↓ navigate · Enter / Tab apply · Esc close</span>
        <span><code>-</code> negates operator</span>
      </div>
    `;
  }

  function renderPgSearchSuggestionsListOnly() {
    const listEl = document.querySelector("#pgSearchSuggestionsPopover .pg-search-suggestions-list");
    if (!listEl) {
      renderPgSearchSuggestionsPopoverHtml();
      return;
    }
    const items = getPgFilteredSearchSuggestions();
    listEl.innerHTML = items.map((item, idx) => {
      const isSelected = idx === state.searchSuggestionIndex;
      return `<div class="pg-search-suggestion-item ${isSelected ? "is-selected" : ""}" data-idx="${idx}" onclick="applyPgSearchSuggestion('${escapeHtml(item.op)}')">
        <span class="pg-search-item-icon" aria-hidden="true">${item.icon}</span>
        <span class="pg-search-item-badge">${escapeHtml(item.op)}</span>
        <span class="pg-search-item-desc" title="${escapeHtml(item.desc)}">${escapeHtml(item.desc)}</span>
        <span class="pg-search-item-cat">${escapeHtml(item.cat)}</span>
      </div>`;
    }).join("");
    const selectedEl = listEl.querySelector(".pg-search-suggestion-item.is-selected");
    if (selectedEl && typeof selectedEl.scrollIntoView === "function") {
      selectedEl.scrollIntoView({ block: "nearest" });
    }
  }

  function onQueueSearchFocus(event) {
    openPgSearchSuggestions();
  }

  function mergeRemoteSearchPayloadIntoState(data, opts = {}) {
    if (!data || !Array.isArray(data.changes) || data.changes.length === 0) return false;
    if (!state.remoteSearchChangesByCl) state.remoteSearchChangesByCl = {};
    let addedOrUpdated = false;
    const existingByNum = new Map((state.changes || []).map(c => [Number(c.number), c]));
    data.changes.forEach(c => {
      const num = Number(c && c.number);
      if (!num) return;
      if (!opts.skipStoreRemote) {
        state.remoteSearchChangesByCl[num] = c;
      }
      if (!existingByNum.has(num)) {
        state.changes.push(c);
        existingByNum.set(num, c);
        addedOrUpdated = true;
      } else {
        Object.assign(existingByNum.get(num), c);
        addedOrUpdated = true;
      }
    });
    const incomingStacks = Array.isArray(data.stacks) && data.stacks.length > 0
      ? data.stacks
      : data.changes.map(c => ({
          stack_id: `single:${c.number}`,
          is_stack: false,
          title: c.subject || `ag/${c.number}`,
          commits: [c],
        }));
    const stackCommitNums = new Set();
    (state.stacks || []).forEach(st => {
      (st.commits || []).forEach(c => stackCommitNums.add(Number(c && c.number)));
    });
    incomingStacks.forEach(st => {
      const newCommits = (st.commits || []).filter(c => !stackCommitNums.has(Number(c && c.number)));
      if (newCommits.length > 0) {
        newCommits.forEach(c => stackCommitNums.add(Number(c.number)));
        state.stacks.push({ ...st, commits: newCommits });
        addedOrUpdated = true;
      }
    });
    if (Array.isArray(data.prefetched_diffs)) {
      data.prefetched_diffs.forEach(d => {
        if (d && d.number) state.diffsByCl[d.number] = d;
      });
    }
    return addedOrUpdated;
  }

  async function queryGerritForSearch(queryStr, opts = {}) {
    const raw = String(queryStr != null ? queryStr : (state.searchQuery || "")).trim();
    if (!raw || typeof sidecarFetch !== "function") return null;
    if (!state.remoteSearchedQueries) state.remoteSearchedQueries = new Set();
    const cacheKey = `${state.viewer || ""}:${raw.toLowerCase()}`;
    if (opts.autoOnly && state.remoteSearchedQueries.has(cacheKey)) return null;
    state.remoteSearchedQueries.add(cacheKey);
    state.remoteSearchLoading = true;
    if (!opts.silent) {
      renderAll({ preserveScroll: true });
    }
    try {
      const v = state.viewer || "";
      const url = `/api/gerrit/queue?query=${encodeURIComponent(raw)}${v ? `&viewer=${encodeURIComponent(v)}` : ""}`;
      const res = await sidecarFetch(url);
      if (!res || !res.ok) return null;
      const data = await res.json();
      if (mergeRemoteSearchPayloadIntoState(data)) {
        state.includeUngatedMatches = true;
      }
      return data;
    } catch (_) {
      return null;
    } finally {
      state.remoteSearchLoading = false;
      renderAll({ preserveScroll: true });
    }
  }

  function maybeTriggerRemoteSearchForMissingCl(queryStr) {
    const raw = String(queryStr != null ? queryStr : (state.searchQuery || "")).trim();
    if (!raw) return false;
    const clNums = extractSearchClNumbers(raw);
    if (!clNums.length) return false;
    const missing = clNums.filter(n => !(state.changes || []).some(c => String(c && c.number) === String(n)));
    if (!missing.length) return false;
    const remoteQ = missing.map(n => `change:${n}`).join(" OR ");
    queryGerritForSearch(remoteQ, { autoOnly: true, silent: true });
    return true;
  }

  function onQueueSearchInput(val) {
    state.searchQuery = String(val || "");
    const hasExplicitCl = extractSearchClNumbers(state.searchQuery).length > 0;
    state.includeUngatedMatches = hasExplicitCl;
    state.explicitRadarSelection = false;
    syncSearchClearButton();
    renderPgSearchPresetsDom();
    if (state.searchSuggestionsOpen) {
      state.searchSuggestionIndex = -1;
      renderPgSearchSuggestionsPopoverHtml();
    }
    maybeTriggerRemoteSearchForMissingCl(state.searchQuery);
    renderAll();
  }

  function onQueueSearchKeydown(event) {
    if (event.key === "F1" || (event.altKey && (event.key === "/" || event.key === "?"))) {
      event.preventDefault();
      openPgSearchHelpDialog();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!state.searchSuggestionsOpen) {
        openPgSearchSuggestions();
      } else {
        const items = getPgFilteredSearchSuggestions();
        if (items.length > 0) {
          state.searchSuggestionIndex = (state.searchSuggestionIndex + 1) % items.length;
          renderPgSearchSuggestionsListOnly();
        }
      }
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!state.searchSuggestionsOpen) {
        openPgSearchSuggestions();
      } else {
        const items = getPgFilteredSearchSuggestions();
        if (items.length > 0) {
          state.searchSuggestionIndex = (state.searchSuggestionIndex - 1 + items.length) % items.length;
          renderPgSearchSuggestionsListOnly();
        }
      }
      return;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      if (state.searchSuggestionsOpen && state.searchSuggestionIndex >= 0) {
        const items = getPgFilteredSearchSuggestions();
        const selected = items[state.searchSuggestionIndex];
        if (selected) {
          event.preventDefault();
          applyPgSearchSuggestion(selected.op);
          return;
        }
      }
      if (event.key === "Enter") {
        closePgSearchSuggestions();
        if ((state.searchQuery || "").trim()) {
          queryGerritForSearch(state.searchQuery);
        }
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      if (state.searchSuggestionsOpen) {
        closePgSearchSuggestions();
      } else if (state.searchQuery || state.authorFilter !== "all") {
        clearQueueSearchAndAuthor();
      } else {
        event.target.blur();
      }
      return;
    }
  }

  function setAuthorFilter(authorShort) {
    const norm = String(authorShort || "all").trim().toLowerCase() || "all";
    state.authorFilter = state.authorFilter === norm && norm !== "all" ? "all" : norm;
    state.includeUngatedMatches = false;
    state.explicitRadarSelection = false;
    const sel = document.getElementById("authorFilterSelect");
    if (sel) sel.value = state.authorFilter;
    syncSearchClearButton();
    renderAll();
  }

  function clearQueueSearchAndAuthor() {
    state.searchQuery = "";
    state.authorFilter = "all";
    state.includeUngatedMatches = false;
    state.explicitRadarSelection = false;
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = "";
    const sel = document.getElementById("authorFilterSelect");
    if (sel) sel.value = "all";
    closePgSearchSuggestions();
    syncSearchClearButton();
    renderPgSearchPresetsDom();
    if (typeof renderAll === "function") {
      try {
        renderAll();
      } catch (e) {}
    }
  }

  function toggleIncludeUngatedMatches() {
    state.includeUngatedMatches = !state.includeUngatedMatches;
    renderAll();
  }

  async function queryGerritForAuthor(authorStr) {
    const raw = String(authorStr || "").trim();
    if (!raw) return;
    if (extractSearchClNumbers(raw).length > 0 || raw.includes(":")) {
      await queryGerritForSearch(raw);
      return;
    }
    const clean = raw.replace(/^(author:|owner:|from:|by:)/i, "").trim();
    if (!clean) return;
    await fetchQueueData(state.viewer, state.scope, {
      author: clean,
      preserveDiffCache: true,
      loadingTitle: `Querying Gerrit for owner:${clean}…`,
      loadingSub: `Searching reviewer:${state.viewer} owner:${clean} across Gerrit`,
    });
  }

  function syncSearchClearButton() {
    const btn = document.getElementById("queueSearchClearBtn");
    if (btn) {
      btn.style.display = hasActiveAuthorOrSearchFilter() ? "inline-block" : "none";
    }
    const saveBtn = document.getElementById("btnSaveSearchQuery");
    if (saveBtn) {
      const hasQ = Boolean(String(state.searchQuery || "").trim());
      saveBtn.style.display = hasQ ? "inline-flex" : "none";
    }
  }

  let _lastAuthorControlsSig = "";
  let _lastAuthorSelHtml = "";
  let _lastAuthorChipsHtml = "";

  function computeAuthorControlsCacheKey(changes, precomputedPoolSig) {
    if (precomputedPoolSig) {
      return `${precomputedPoolSig}|${state.authorFilter || "all"}`;
    }
    const list = changes || [];
    if (list.length === 0) return `empty|${state.authorFilter || "all"}`;
    const g = state.gates || {};
    if (g.agent) return "";
    let h = (FNV1A_OFFSET_BASIS ^ list.length) | 0;
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      if (!c) continue;
      h = computeCommitFilterReadOnlyHash(c, h);
    }
    const starredSig = (state.starredCls && state.starredCls.size > 0)
      ? Array.from(state.starredCls).join(",")
      : "";
    return `${state.authorFilter || "all"}|${state.viewer || ""}|${g.presubmit ? 1 : 0}${g.starredOnly ? 1 : 0}${g.hideMine ? 1 : 0}${g.hideSubmittable ? 1 : 0}|${starredSig}|${list.length}:${h}`;
  }

  function renderAuthorFilterControls(precomputedPoolSig = "") {
    const sel = document.getElementById("authorFilterSelect");
    const chipsBar = document.getElementById("authorChipsBar");
    if (!sel && !chipsBar) return;

    const authorSig = computeAuthorControlsCacheKey(state.changes, precomputedPoolSig);
    if (authorSig && authorSig === _lastAuthorControlsSig) {
      if (sel && sel._lastRenderedAuthorHtml !== _lastAuthorSelHtml) {
        sel._lastRenderedAuthorHtml = _lastAuthorSelHtml;
        sel.innerHTML = _lastAuthorSelHtml;
      }
      if (chipsBar && chipsBar._lastRenderedChipsHtml !== _lastAuthorChipsHtml) {
        chipsBar._lastRenderedChipsHtml = _lastAuthorChipsHtml;
        chipsBar.innerHTML = _lastAuthorChipsHtml;
      }
      return;
    }

    const byAuthor = new Map();
    state.changes.forEach(c => {
      const short = getClPrimaryAuthorShort(c);
      if (!byAuthor.has(short)) {
        byAuthor.set(short, {
          short,
          name: c.owner_name || short,
          email: c.owner_email || `${short}@google.com`,
          isBot: isClBotOwner(c),
          total: 0,
          gated: 0,
        });
      }
      const entry = byAuthor.get(short);
      entry.total += 1;
      if (passesGates(c)) {
        entry.gated += 1;
      }
    });

    const authors = Array.from(byAuthor.values()).sort((a, b) => {
      if (a.isBot !== b.isBot) return a.isBot ? 1 : -1;
      if (b.total !== a.total) return b.total - a.total;
      return a.short.localeCompare(b.short);
    });

    const optsHtml = [
      `<option value="all" ${state.authorFilter === "all" ? "selected" : ""}>👤 All Authors (${state.changes.length})</option>`,
      ...authors.map(a => {
        const icon = a.isBot ? "🤖" : "👤";
        const label = a.name && a.name.toLowerCase() !== a.short ? `${a.short} (${a.name})` : `${a.short}@`;
        return `<option value="${escapeHtml(a.short)}" ${state.authorFilter === a.short ? "selected" : ""}>${icon} ${escapeHtml(label)} · ${a.total}</option>`;
      }),
    ];
    const nextSelHtml = optsHtml.join("");
    if (sel && sel._lastRenderedAuthorHtml !== nextSelHtml) {
      sel._lastRenderedAuthorHtml = nextSelHtml;
      sel.innerHTML = nextSelHtml;
    }

    let nextChipsHtml = "";
    if (authors.length > 0) {
      const allActive = state.authorFilter === "all" ? "active" : "";
      const chipsHtml = [
        `<span class="author-chip ${allActive}" onclick="setAuthorFilter('all')" title="Show all authors in queue">All Authors (${state.changes.length})</span>`,
        ...authors.map(a => {
          const isActive = state.authorFilter === a.short ? "active" : "";
          const icon = a.isBot ? "🤖" : "👤";
          const shortDisplay = a.short.length > 20 ? a.short.slice(0, 18) + "…" : a.short;
          const gateNote = a.gated < a.total ? ` (${a.gated} verified / ${a.total} total)` : ` (${a.total} CLs)`;
          const title = `${a.name} <${a.email}>${gateNote}`;
          return `<span class="author-chip ${isActive}" onclick="setAuthorFilter('${escapeHtml(a.short)}')" title="${escapeHtml(title)}">${icon} ${escapeHtml(shortDisplay)} (${a.total})</span>`;
        }),
      ];
      nextChipsHtml = chipsHtml.join("");
    }
    if (chipsBar && chipsBar._lastRenderedChipsHtml !== nextChipsHtml) {
      chipsBar._lastRenderedChipsHtml = nextChipsHtml;
      chipsBar.innerHTML = nextChipsHtml;
    }
    if (authorSig) {
      _lastAuthorControlsSig = authorSig;
      _lastAuthorSelHtml = nextSelHtml;
      _lastAuthorChipsHtml = nextChipsHtml;
    }
  }

  function passesGates(cl) {
    if (!cl) return false;
    const pvOk = !!(cl.presubmit_vetted || (cl.presubmit_score && +cl.presubmit_score >= 1));
    if (state.gates.presubmit && !pvOk) return false;
    if (state.gates.agent) {
      const sw = state.swarmByCl[+cl.number];
      const swarmClean = !!(
        sw && sw.state === "completed" && !(sw.findings_count && +sw.findings_count !== 0) && !(cl.unresolved_comments && +cl.unresolved_comments !== 0)
      );
      if (!cl.agent_vetted && !swarmClean) return false;
    }
    if (state.gates.starredOnly && !isClStarred(cl)) return false;
    if (state.gates.hideMine && isClApprovedByViewer(cl)) return false;
    if (state.gates.hideSubmittable && isClSubmittable(cl)) return false;
    return true;
  }

  const _pgFilteredStacksSummaryCache = new Map();
  const PG_FILTERED_STACKS_SUMMARY_CACHE_MAX = 16;
  let _lastFilteredStacksCacheKey = "";
  let _lastFilteredStacksCacheVal = null;
  let _verifiedPoolStacksSnap = null;

  function computeCommitFilterReadOnlyHash(c, h) {
    const num = c.number | 0;
    const subj = c.subject || "";
    const sLen = subj.length;
    const sCode = sLen > 0
      ? (subj.charCodeAt(0) | (subj.charCodeAt(sLen - 1) << 8) | (subj.charCodeAt(sLen >> 1) << 16))
      : 0;
    const stStr = c.status || "";
    const stCode = stStr.length > 0 ? stStr.charCodeAt(0) : 0;
    const revs = c.reviewers_detail;
    const revLen = revs ? (revs.length | 0) : 0;
    const rev0Vote = (revLen > 0 && revs[0]) ? (revs[0].cr_vote | 0) : 0;
    const srs = c.submit_requirements;
    const srLen = srs ? (srs.length | 0) : 0;
    const sr0Code = (srLen > 0 && srs[0] && srs[0].status) ? srs[0].status.charCodeAt(0) : 0;
    const filesLen = c.files ? (c.files.length | 0) : 0;
    const topicLen = c.topic ? c.topic.length : 0;
    const flags =
      (c.is_merged ? 1 : 0) |
      (c.is_abandoned ? 2 : 0) |
      (c.work_in_progress ? 4 : 0) |
      (c.presubmit_vetted ? 8 : 0) |
      (c.agent_vetted ? 16 : 0) |
      (c.starred ? 32 : 0) |
      (c.approved_by_viewer ? 64 : 0) |
      (c.submittable ? 128 : 0) |
      (c.gerrit_submittable === false ? 256 : 0) |
      ((c.presubmit_score | 0) << 10) |
      ((c.viewer_cr_vote | 0) << 14) |
      ((c.unresolved_comments | 0) << 18);
    return (Math.imul(h ^ num ^ flags, 16777619) ^ sLen ^ sCode ^ stCode ^ (revLen << 4) ^ rev0Vote ^ (srLen << 8) ^ sr0Code ^ (filesLen << 12) ^ topicLen) | 0;
  }

  function matchVerifiedPoolStacksSnapshot(stacksList, changes) {
    const sLen = stacksList.length;
    const cTotal = changes.length;
    if (sLen === 0 || cTotal === 0) return null;
    const snap = _verifiedPoolStacksSnap;
    if (
      snap &&
      snap.stacksRef === stacksList &&
      snap.changesRef === changes &&
      snap.sLen === sLen &&
      snap.cTotal === cTotal &&
      snap.firstChange === changes[0] &&
      snap.lastChange === changes[cTotal - 1]
    ) {
      let arraysMatch = true;
      for (let i = 0; i < sLen; i++) {
        const st = stacksList[i];
        const commits = st && st.commits;
        if (st !== snap.stackObjs[i] || commits !== snap.commitArrays[i] || (commits ? commits.length : 0) !== snap.commitLens[i]) {
          arraysMatch = false;
          break;
        }
      }
      if (arraysMatch) {
        return snap.coveredByPool ? snap : null;
      }
    }
    const changeSet = new Set(changes);
    const stackObjs = new Array(sLen);
    const commitArrays = new Array(sLen);
    const commitLens = new Array(sLen);
    let coveredByPool = true;
    let structHash = (0x811c9dc5 ^ sLen) | 0;
    let totalStackCommits = 0;
    for (let i = 0; i < sLen; i++) {
      const st = stacksList[i];
      const commits = (st && Array.isArray(st.commits)) ? st.commits : null;
      const len = commits ? commits.length : 0;
      stackObjs[i] = st;
      commitArrays[i] = commits;
      commitLens[i] = len;
      totalStackCommits += len;
      structHash = (Math.imul(structHash ^ len, 16777619) ^ i) | 0;
      for (let j = 0; j < len; j++) {
        const c = commits[j];
        if (!c || !changeSet.has(c)) {
          coveredByPool = false;
        } else {
          structHash = Math.imul(structHash ^ (c.number | 0), 16777619) | 0;
        }
      }
    }
    const nextSnap = {
      stacksRef: stacksList,
      changesRef: changes,
      sLen,
      cTotal,
      firstChange: changes[0],
      lastChange: changes[cTotal - 1],
      stackObjs,
      commitArrays,
      commitLens,
      coveredByPool,
      structureSig: `${sLen}:${totalStackCommits}:${structHash}`,
    };
    _verifiedPoolStacksSnap = nextSnap;
    return coveredByPool ? nextSnap : null;
  }

  function computeFilteredStacksCacheKey(stacksList, precomputedPoolSig = "") {
    if (!Array.isArray(stacksList) || stacksList.length === 0) return "";
    const g = state.gates || {};
    if (g.agent) return "";
    const sq = state.searchQuery || "";
    if (sq && (sq.includes("comment:") || sq.includes("message:") || sq.includes("label:") || sq.includes("has:") || sq.includes("is:"))) {
      return "";
    }
    if (precomputedPoolSig && Array.isArray(state.changes)) {
      const snap = matchVerifiedPoolStacksSnapshot(stacksList, state.changes);
      if (snap !== null) {
        return `pool|${precomputedPoolSig}|${state.typeFilter || "all"}|${snap.structureSig}`;
      }
    }
    const starredSig = (state.starredCls && state.starredCls.size > 0)
      ? Array.from(state.starredCls).join(",")
      : "";
    let h = (0x811c9dc5 ^ stacksList.length) | 0;
    let totalCommits = 0;
    let firstSubj = "";
    let lastSubj = "";
    for (let sIdx = 0; sIdx < stacksList.length; sIdx++) {
      const st = stacksList[sIdx];
      const commits = (st && st.commits) || _PG_EMPTY_ARR;
      const cLen = commits.length;
      totalCommits += cLen;
      h = (Math.imul(h ^ cLen, 16777619) ^ sIdx) | 0;
      if (cLen > 0) {
        if (!firstSubj && commits[0]) firstSubj = commits[0].subject || "";
        const lastC = commits[cLen - 1];
        if (lastC) lastSubj = lastC.subject || "";
      }
      for (let i = 0; i < cLen; i++) {
        const c = commits[i];
        if (!c) continue;
        h = computeCommitFilterReadOnlyHash(c, h);
      }
    }
    return `${sq}|${state.authorFilter || "all"}|${state.typeFilter || "all"}|${state.includeUngatedMatches ? 1 : 0}|${state.viewer || ""}|${g.presubmit ? 1 : 0}${g.starredOnly ? 1 : 0}${g.hideMine ? 1 : 0}${g.hideSubmittable ? 1 : 0}|${starredSig}|${stacksList.length}:${totalCommits}:${h}:${firstSubj}:${lastSubj}`;
  }

  function getFilteredStacksSummary(precomputedPoolSig = "") {
    const stacksList = state.stacks || _PG_EMPTY_ARR;
    const summaryCacheKey = computeFilteredStacksCacheKey(stacksList, precomputedPoolSig);
    if (summaryCacheKey) {
      if (summaryCacheKey === _lastFilteredStacksCacheKey && _lastFilteredStacksCacheVal !== null) {
        return _lastFilteredStacksCacheVal;
      }
      const cachedSummary = _pgFilteredStacksSummaryCache.get(summaryCacheKey);
      if (cachedSummary !== undefined) {
        _lastFilteredStacksCacheKey = summaryCacheKey;
        _lastFilteredStacksCacheVal = cachedSummary;
        return cachedSummary;
      }
    }
    const gatedStacks = [];
    const ungatedStacks = [];
    let gatedCommitCount = 0;
    let ungatedCommitCount = 0;
    const hasExplicitClQuery = extractSearchClNumbers(state.searchQuery).length > 0;
    const tf = state.typeFilter;
    const filterByType = !hasExplicitClQuery && tf !== "all";
    let allStacksUngatedSame = true;
    let allStacksGatedSame = true;

    for (let sIdx = 0; sIdx < stacksList.length; sIdx++) {
      const st = stacksList[sIdx];
      const commits = (st && st.commits) || _PG_EMPTY_ARR;
      const cLen = commits.length;
      if (cLen === 0) {
        allStacksUngatedSame = false;
        allStacksGatedSame = false;
        continue;
      }
      let baseMatches = null;
      let allBaseMatch = true;
      for (let i = 0; i < cLen; i++) {
        const c = commits[i];
        const ok = matchesAuthorAndSearch(c) && (!filterByType || getCommitTypeId(c) === tf);
        if (!ok) {
          if (allBaseMatch) {
            baseMatches = commits.slice(0, i);
            allBaseMatch = false;
          }
        } else if (!allBaseMatch) {
          baseMatches.push(c);
        }
      }
      if (!allBaseMatch) {
        allStacksUngatedSame = false;
        allStacksGatedSame = false;
      }
      const effBase = allBaseMatch ? commits : baseMatches;
      if (effBase.length > 0) {
        const ungatedStackObj = allBaseMatch ? st : { ...st, commits: effBase };
        ungatedStacks.push(ungatedStackObj);
        ungatedCommitCount += effBase.length;

        let gatedCommits = null;
        let allGatedMatch = true;
        for (let i = 0; i < effBase.length; i++) {
          const c = effBase[i];
          if (!passesGates(c)) {
            if (allGatedMatch) {
              gatedCommits = effBase.slice(0, i);
              allGatedMatch = false;
            }
          } else if (!allGatedMatch) {
            gatedCommits.push(c);
          }
        }
        if (!allGatedMatch) {
          allStacksGatedSame = false;
        }
        const effGated = allGatedMatch ? effBase : gatedCommits;
        if (effGated.length > 0) {
          gatedStacks.push(allGatedMatch ? ungatedStackObj : { ...st, commits: effGated });
          gatedCommitCount += effGated.length;
        }
      }
    }

    const filterActive = hasActiveAuthorOrSearchFilter();
    const autoBypassedGates = filterActive && (Boolean(state.includeUngatedMatches) || hasExplicitClQuery);
    const hiddenByGatesCount = Math.max(0, ungatedCommitCount - gatedCommitCount);
    const finalUngatedStacks = (allStacksUngatedSame && ungatedStacks.length === stacksList.length) ? stacksList : ungatedStacks;
    const finalGatedStacks = (allStacksGatedSame && gatedStacks.length === stacksList.length) ? stacksList : gatedStacks;

    const result = {
      visibleStacks: autoBypassedGates ? finalUngatedStacks : finalGatedStacks,
      gatedCommitCount,
      ungatedCommitCount,
      hiddenByGatesCount,
      autoBypassedGates,
      filterActive,
    };
    if (summaryCacheKey) {
      if (_pgFilteredStacksSummaryCache.size >= PG_FILTERED_STACKS_SUMMARY_CACHE_MAX) {
        _pgFilteredStacksSummaryCache.clear();
      }
      _pgFilteredStacksSummaryCache.set(summaryCacheKey, result);
      _lastFilteredStacksCacheKey = summaryCacheKey;
      _lastFilteredStacksCacheVal = result;
    }
    return result;
  }

  function getVisibleStacks() {
    return getFilteredStacksSummary().visibleStacks;
  }

  function isClOwnedByViewer(cl) {
    if (!cl) return false;
    const viewerNorm = normalizeViewerInput(state.viewer);
    if (!viewerNorm) return false;
    if (cl._cachedOwnerEmailRaw !== cl.owner_email) {
      cl._cachedOwnerEmailRaw = cl.owner_email;
      cl._cachedOwnerEmailNorm = String(cl.owner_email || "").trim().toLowerCase();
    }
    return cl._cachedOwnerEmailNorm === viewerNorm;
  }

  function isClInViewerAttentionSet(cl) {
    if (!cl || isClClosed(cl)) return false;
    if (typeof cl.viewer_in_attention_set === "boolean") {
      return cl.viewer_in_attention_set;
    }
    const viewerNorm = normalizeViewerInput(state.viewer);
    if (!viewerNorm) return false;
    const attnSet = cl.attention_set;
    const attnLen = attnSet ? (Array.isArray(attnSet) ? attnSet.length : -1) : -1;
    const firstAttn = (attnLen > 0 && attnSet[0])
      ? (attnSet[0].email || attnSet[0].user || (attnSet[0].account && (attnSet[0].account.email || attnSet[0].account.username)) || "")
      : "";
    const attnEmails = cl.attention_set_emails;
    const attnEmailsLen = attnEmails ? (Array.isArray(attnEmails) ? attnEmails.length : -1) : -1;
    const firstAttnEmail = attnEmailsLen > 0 ? attnEmails[0] : "";
    const revs = cl.reviewers_detail;
    const revLen = revs ? (Array.isArray(revs) ? revs.length : -1) : -1;
    if (
      cl._cachedInViewerAttnViewer === viewerNorm &&
      cl._cachedInViewerAttnSetRef === attnSet &&
      cl._cachedInViewerAttnLen === attnLen &&
      cl._cachedInViewerAttnFirst === firstAttn &&
      cl._cachedInViewerAttnEmailsRef === attnEmails &&
      cl._cachedInViewerAttnEmailsLen === attnEmailsLen &&
      cl._cachedInViewerAttnEmailsFirst === firstAttnEmail &&
      cl._cachedInViewerAttnRevLen === revLen &&
      typeof cl._cachedInViewerAttnVal === "boolean"
    ) {
      return cl._cachedInViewerAttnVal;
    }
    let result = false;
    if (attnEmailsLen >= 0) {
      result = attnEmails.some(em => String(em || "").toLowerCase() === viewerNorm);
    } else if (attnLen >= 0) {
      if (attnSet.some(a => {
        const em = String(a?.email || a?.account?.email || a?.account?.username || a?.user || "").toLowerCase();
        return em === viewerNorm || (viewerNorm && em.includes(viewerNorm));
      })) {
        result = true;
      } else {
        result = getFallbackReviewersForCl(cl).some(p => p.is_viewer && p.in_attention_set);
      }
    } else {
      result = getFallbackReviewersForCl(cl).some(p => p.is_viewer && p.in_attention_set);
    }
    cl._cachedInViewerAttnViewer = viewerNorm;
    cl._cachedInViewerAttnSetRef = attnSet;
    cl._cachedInViewerAttnLen = attnLen;
    cl._cachedInViewerAttnFirst = firstAttn;
    cl._cachedInViewerAttnEmailsRef = attnEmails;
    cl._cachedInViewerAttnEmailsLen = attnEmailsLen;
    cl._cachedInViewerAttnEmailsFirst = firstAttnEmail;
    cl._cachedInViewerAttnRevLen = revLen;
    cl._cachedInViewerAttnVal = result;
    return result;
  }

  function isClViewerCcOnly(cl) {
    if (!cl) return false;
    if (typeof cl.is_cc === "boolean" && typeof cl.is_explicit_reviewer === "boolean") {
      return cl.is_cc && !cl.is_explicit_reviewer;
    }
    const viewerNorm = normalizeViewerInput(state.viewer);
    if (!viewerNorm) return false;
    const revLen = Array.isArray(cl.reviewers_detail) ? cl.reviewers_detail.length : -1;
    const ccLen = Array.isArray(cl.ccs_detail) ? cl.ccs_detail.length : -1;
    const ccKey = `${viewerNorm}|${revLen}|${ccLen}`;
    if (cl._cachedViewerCcOnlyKey === ccKey && typeof cl._cachedViewerCcOnlyVal === "boolean") {
      return cl._cachedViewerCcOnlyVal;
    }
    const inRevs = revLen > 0 &&
      cl.reviewers_detail.some(p => String(p.email || "").toLowerCase() === viewerNorm);
    const inCcs = ccLen > 0 &&
      cl.ccs_detail.some(p => String(p.email || "").toLowerCase() === viewerNorm);
    const result = Boolean(inCcs && !inRevs);
    cl._cachedViewerCcOnlyKey = ccKey;
    cl._cachedViewerCcOnlyVal = result;
    return result;
  }

  function isClIncomingForViewer(cl) {
    if (!cl || isClClosed(cl) || isClOwnedByViewer(cl) || isClViewerCcOnly(cl)) return false;
    if (typeof cl.is_explicit_reviewer === "boolean") {
      return cl.is_explicit_reviewer || state.scope === "reviewer";
    }
    if (state.scope === "team") {
      const viewerNorm = normalizeViewerInput(state.viewer);
      return Array.isArray(cl.reviewers_detail) &&
        cl.reviewers_detail.some(p => String(p.email || "").toLowerCase() === viewerNorm);
    }
    return true;
  }

