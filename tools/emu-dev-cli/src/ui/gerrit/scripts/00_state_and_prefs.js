  const _sidecarUrlParams = {
    get: function(key) {
      const search = (typeof window !== "undefined" && window.location && window.location.search) || "";
      const match = search.match(new RegExp("[?&]" + key + "=([^&]*)"));
      return match ? decodeURIComponent(match[1].replace(/\+/g, " ")) : null;
    }
  };
  const sidecarUiToken = _sidecarUrlParams.get("token") || "";

  function sidecarFetch(path, options = {}) {
    let targetUrl = path;
    if (sidecarUiToken) {
      const sep = targetUrl.includes("?") ? "&" : "?";
      targetUrl = `${targetUrl}${sep}token=${encodeURIComponent(sidecarUiToken)}`;
    }
    const headers = Object.assign({}, options.headers || {});
    if (sidecarUiToken) {
      headers["X-Sidecar-Token"] = sidecarUiToken;
    }
    return fetch(targetUrl, Object.assign({}, options, { headers }));
  }

  const DEFAULT_SWARM_SPECIALISTS = [
    { specialist: "commit-and-bug-auditor", role: "Commit & Bug Auditor" },
    { specialist: "duplication-and-reuse-auditor", role: "Duplication & Reuse Auditor" },
    { specialist: "modularity-auditor", role: "Modularity Auditor" },
    { specialist: "control-flow-auditor", role: "Control-Flow Auditor" },
    { specialist: "constants-auditor", role: "Named Constants Auditor" },
    { specialist: "unicode-and-platform-auditor", role: "Unicode & Platform Auditor" },
    { specialist: "error-messages-and-statusor-auditor", role: "Error Messages & StatusOr Auditor" },
    { specialist: "concurrency-and-safety-auditor", role: "Concurrency & Memory Safety Auditor" },
  ];

  const DEFAULT_SAVED_SEARCHES = [
    {
      id: "sec_drafts",
      title: "Drafts & Unresolved",
      query: "has:draft",
      showOnDashboard: false,
    },
    {
      id: "sec_submittable",
      title: "Ready to Submit",
      query: "is:submittable label:cr=+2",
      showOnDashboard: false,
    },
    {
      id: "sec_goldfish",
      title: "Goldfish Reviews",
      query: "project:platform/hardware/generic/goldfish status:open",
      showOnDashboard: false,
    },
    {
      id: "sec_needs_review",
      title: "Needs Review",
      query: "reviewer:self status:open -label:cr=+2",
      showOnDashboard: false,
    },
  ];

  const COMMIT_TYPE_ORDER = [
    { id: "bugfix", label: "🐛 Bug Fixes", short: "Bug Fix" },
    { id: "perf", label: "⚡ Performance", short: "Perf" },
    { id: "ets_test", label: "🧪 ETS & Tests", short: "ETS / Test" },
    { id: "cleanup", label: "🧹 Cleanup & Refactor", short: "Cleanup" },
    { id: "feature", label: "✨ Features & UI", short: "Feature" },
    { id: "docs_build", label: "📦 Build, Deps & Docs", short: "Build / Docs" },
  ];

  let _lastNormViewerRaw = null;
  let _lastNormViewerOut = "";

  function normalizeViewerInput(rawVal) {
    if (rawVal === _lastNormViewerRaw) return _lastNormViewerOut;
    const cleaned = String(rawVal || "").trim().toLowerCase();
    const out = !cleaned ? "" : (cleaned.includes("@") ? cleaned : `${cleaned}@google.com`);
    _lastNormViewerRaw = rawVal;
    _lastNormViewerOut = out;
    return out;
  }

  const _bootstrapPayload = (window.__GERRIT_UI_BOOTSTRAP__ && typeof window.__GERRIT_UI_BOOTSTRAP__ === "object")
    ? window.__GERRIT_UI_BOOTSTRAP__
    : {};
  const _initialViewer = normalizeViewerInput(
    _sidecarUrlParams.get("viewer") || _bootstrapPayload.viewer || ""
  );
  const _initialRoster = Array.isArray(_bootstrapPayload.roster) ? _bootstrapPayload.roster : [];

  const state = {
    viewer: _initialViewer,
    scope: "dashboard",
    roster: _initialRoster,
    changes: [],
    stacks: [],
    diffsByCl: {},
    selectedPatchsetByCl: {},
    diffsByClPs: {},
    inFlightDiffPromises: {},
    swarmByCl: {},
    sessionApprovedCls: new Set(),
    collapsedCommitMsgs: new Set(),
    expandedCommitMsgs: new Set(),
    reviewedFilesByClPs: {},
    collapsedFilesByClPs: {},
    pgOwnerFilterByCl: {},
    fileSizeFilterByCl: {},
    pgFileQueryFilterByCl: {},
    replyDialogOpen: false,
    confirmDiscardDialogOpen: false,
    confirmDiscardPendingAction: null,
    codeOwnersDialogOpen: false,
    codeOwnersTarget: null,
    activeFileOutlineMenu: null,
    goToLineModalOpen: false,
    goToLineCl: null,
    activeLine: null,
    shortcutsDialogOpen: false,
    shortcutsActiveCategory: "all",
    shortcutsFocusedIndex: -1,
    downloadDialogOpen: false,
    downloadDialogCl: null,
    downloadPatchset: null,
    downloadActiveTab: "repo",
    downloadScheme: (typeof localStorage !== "undefined" && localStorage.getItem("pg_download_scheme")) || "repo",
    downloadUseBranch: false,
    downloadBranchName: "",
    downloadRepoCreateBranch: false,
    diffPreferencesDialogOpen: false,
    stackSequenceDialogOpen: false,
    attentionHistoryDialogOpen: false,
    attentionHistoryCl: null,
    attentionHistoryFilterUser: "",
    attentionHistoryFilterOp: "all",
    attentionHistoryFilterQuery: "",
    diffPreferences: null,
    showResolvedComments: true,
    showRobotComments: true,
    diffFind: {
      isOpen: false,
      cl: null,
      query: "",
      caseSensitive: false,
      wholeWord: false,
      isRegex: false,
      activeMatchIndex: 0,
      matches: []
    },
    fileJump: {
      isOpen: false,
      cl: null,
      filter: "",
    },
    copyLinksMenu: {
      isOpen: false,
      cl: null,
    },
    patchRangeMenuCl: null,
    activeFileByCl: {},
    codeViewerMode: false,
    codeViewerDisplayMode: "single",
    codeViewerSelectedFileByCl: {},
    codeViewerCollapsedDirsByCl: {},
    codeViewerFilterByCl: {},
    codeViewerScrollByFile: {},
    msgFiltersByCl: {},
    filtersDrawerOpen: false,
    collapsedDashboardSections: new Set(),
    savedSearchesModalOpen: false,
    saveSearchModalOpen: false,
    searchHelpDialogOpen: false,
    searchHelpActiveCategory: "all",
    searchHelpFilterQuery: "",
    savedSearches: (function() {
      try {
        const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
        if (!ls) return JSON.parse(JSON.stringify(DEFAULT_SAVED_SEARCHES));
        const raw = ls.getItem("gerrit_ui_pg_saved_searches");
        if (!raw) return JSON.parse(JSON.stringify(DEFAULT_SAVED_SEARCHES));
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) && parsed.length > 0 ? parsed : JSON.parse(JSON.stringify(DEFAULT_SAVED_SEARCHES));
      } catch (e) {
        return JSON.parse(JSON.stringify(DEFAULT_SAVED_SEARCHES));
      }
    })(),
    authorPresetsCollapsed: true,
    typePresetsCollapsed: true,
    filterPresetsCollapsed: true,
    gates: { presubmit: false, agent: false, hideMine: false, hideSubmittable: false },
    savedNonOwnerGates: null,
    groupMode: "dashboard",
    typeFilter: "all",
    authorFilter: "all",
    searchQuery: "",
    remoteSearchChangesByCl: {},
    remoteSearchedQueries: new Set(),
    remoteSearchLoading: false,
    includeUngatedMatches: false,
    selectedStackId: null,
    selectedClNumber: null,
    starredCls: (function() {
      try {
        const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
        if (!ls) return new Set();
        const raw = ls.getItem("gerrit_ui_pg_starred_cls");
        if (!raw) return new Set();
        const parsed = JSON.parse(raw);
        return new Set(Array.isArray(parsed) ? parsed.map(Number) : []);
      } catch (e) {
        return new Set();
      }
    })(),
    searchSuggestionsOpen: false,
    searchSuggestionIndex: -1,
    votingInFlight: false,
    submittingInFlight: false,
    queueLoading: false,
    queueFetchSeq: 0,
    diffLoadingCount: 0,
    diffLoadingLabel: "",
    justEnteredClNumber: null,
    queueClearedCount: 0,
    codeSelection: null,
    inlineCommentsByCl: {},
    nextInlineItemId: 1,
    collapsedInlineIds: new Set(),
    activeReviewerComposer: null,
    authEmail: "",
    activeChatComposer: null,
    sentChatDms: {},
    sortBy: "smart",
    presubmitTargetsByCl: {},
    nextPresubmitReqSeq: 0,
    expandedPresubmitCls: new Set(),
    autoExpandedPresubmitCls: new Set(),
    presubmitTargetFilterByCl: {},
    checksKindFilterByCl: {},
    checkLogOpenByCl: {},
    checkLogSearchByCl: {},
    activeChangeTabByCl: {},
    relatedChangesTabByCl: {},
    selectedCheckRunByCl: {},
    checksSearchQueryByCl: {},
    queueCacheByKey: {},
    sidebarHydrating: false,
    inFlightDiffPromises: {},
    lastKnownPatchSetByCl: {},
    lastRenderedDiffSignature: "",
    editingQaItemById: {},
    openFollowupByItemId: {},
    openReplyByItemId: {},
    openDraftEditByItemId: {},
    draftAutosaveTimers: {},
    replyAutosaveTimers: {},
    composerAutosaveTimer: null,
    qaActionInFlightById: new Set(),
    replyNotifications: [],
    nextReplyNotifId: 1,
    cachedSidecarProjectId: null,
    aiAnalysisByCl: {},
    inFlightAiAnalysisFetchByCl: {},
    inFlightAiAnalysisStartByCl: {},
    aiAnalysisReqSeqByCl: {},
    hiddenAiAnalysisCls: new Set(),
    collapsedAiAnalysisCls: new Set(),
    harnessMenuOpen: false,
    autoFixLaunchingCl: null,
    sidebarWidth: 520,
    sidebarCollapsed: false,
    visibleCommitCount: 0,
    checkedClNumbers: new Set(),
    lastCheckedClNumber: null,
    dashboardDrag: null,
    suppressCardClick: false,
    stagedVote: {
      contextKey: "",
      cr: null,
      v: null,
      pv: null,
      pr: null,
      as: null,
      perf: null,
      lint: null,
      bp: null,
      api: null,
      oo: null,
      rs: null,
      cq: null,
      osl: null,
      trust: null,
      extraLabels: {},
      comment: "",
      noteOpen: false,
    },
    batchReviewerComposer: null,
    sidecarUpdateStatus: null,
    sidecarUpdateChecking: false,
    sidecarUpdateInFlight: false,
    updateWarningModalOpen: false,
    diffColorScheme: "polygerrit_native",
    pgTheme: "dark",
    diffViewMode: "side-by-side",
    fileDiffModeOverrides: {},
    continuousStackDiff: false,
    reviewedStackCommits: (function() {
      try {
        const raw = localStorage.getItem("gerrit_ui_pg_reviewed_stack_commits");
        if (raw) {
          const arr = JSON.parse(raw);
          if (Array.isArray(arr)) return new Set(arr.map(Number));
        }
      } catch (e) {}
      return new Set();
    })(),
    collapsedStackCommits: new Set(),
  };

  const DEFAULT_SIDEBAR_WIDTH = 520;
  const PG_SIZE_XS_MAX = 10;
  const PG_SIZE_S_MAX = 50;
  const PG_SIZE_M_MAX = 250;
  const PG_SIZE_L_MAX = 1000;
  const PG_PRIMARY_REVIEWERS_COUNT = 2;
  const MIN_SIDEBAR_WIDTH = 240;
  const PG_SIDEBAR_COMPACT_MAX_WIDTH = 440;
  const PG_SIDEBAR_MINI_MAX_WIDTH = 340;
  const MIN_DIFF_PANE_WIDTH = 320;
  const SNAP_COLLAPSE_SIDEBAR_WIDTH = 135;
  const SIDEBAR_WIDTH_STORAGE_KEY = "gerrit_ui_sidebar_width_px";
  const SIDEBAR_COLLAPSED_STORAGE_KEY = "gerrit_ui_sidebar_collapsed";
  const DIFF_COLOR_SCHEME_STORAGE_KEY = "gerrit_ui_pg_diff_color_scheme";
  const CONTINUOUS_STACK_DIFF_STORAGE_KEY = "gerrit_ui_pg_continuous_stack_diff";
  const REVIEWED_STACK_COMMITS_STORAGE_KEY = "gerrit_ui_pg_reviewed_stack_commits";
  const PG_THEME_STORAGE_KEY = "gerrit_ui_pg_theme";
  const PG_DIFF_VIEW_MODE_STORAGE_KEY = "gerrit_ui_pg_diff_view_mode";
  const PG_PATCHSET_COMPARISON_STORAGE_PREFIX = "gerrit_ui_pg_patchset_sel_";
  const PG_STACK_INTERDIFF_STORAGE_PREFIX = "gerrit_ui_pg_stack_interdiff_";

  function getPgPersistedPatchsetComparison(clNum) {
    const num = Number(clNum);
    if (!num) return null;
    const storageKey = PG_PATCHSET_COMPARISON_STORAGE_PREFIX + num;
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return null;
      const raw = ls.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && Number(parsed.ps) > 0 && parsed.base !== undefined) {
        return { ps: Number(parsed.ps), base: String(parsed.base) };
      }
    } catch (_e) {}
    return null;
  }

  function setPgPersistedPatchsetComparison(clNum, ps, base, maxPs = null) {
    const num = Number(clNum);
    if (!num) return;
    const isDefaultBase = String(base) === "base" || String(base) === "0";
    const isLatest = maxPs == null ? false : (Number(ps) === Number(maxPs));
    const storageKey = PG_PATCHSET_COMPARISON_STORAGE_PREFIX + num;
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return;
      if (isDefaultBase && isLatest) {
        ls.removeItem(storageKey);
      } else {
        ls.setItem(storageKey, JSON.stringify({ ps: Number(ps), base: String(base) }));
      }
    } catch (_e) {}
  }

  function clearPgPersistedPatchsetComparison(clNum) {
    const num = Number(clNum);
    if (!num) return;
    const storageKey = PG_PATCHSET_COMPARISON_STORAGE_PREFIX + num;
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (ls) ls.removeItem(storageKey);
    } catch (_e) {}
  }

  function getPgSelectedPatchset(clNum) {
    const num = Number(clNum);
    if (!num) return null;
    state.selectedPatchsetByCl = state.selectedPatchsetByCl || {};
    if (state.selectedPatchsetByCl[num]) {
      return state.selectedPatchsetByCl[num];
    }
    const persisted = getPgPersistedPatchsetComparison(num);
    if (persisted) {
      state.selectedPatchsetByCl[num] = persisted;
      return persisted;
    }
    return null;
  }

  const _persistedStackInterdiffCache = new Map();

  function getPgPersistedStackInterdiff(stackId) {
    if (!stackId) return false;
    if (_persistedStackInterdiffCache.has(stackId)) {
      return _persistedStackInterdiffCache.get(stackId);
    }
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return false;
      const val = ls.getItem(PG_STACK_INTERDIFF_STORAGE_PREFIX + stackId) === "true";
      _persistedStackInterdiffCache.set(stackId, val);
      return val;
    } catch (_e) {}
    return false;
  }

  function setPgPersistedStackInterdiff(stackId, isActive) {
    if (!stackId) return;
    _persistedStackInterdiffCache.set(stackId, Boolean(isActive));
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return;
      if (isActive) {
        ls.setItem(PG_STACK_INTERDIFF_STORAGE_PREFIX + stackId, "true");
      } else {
        ls.removeItem(PG_STACK_INTERDIFF_STORAGE_PREFIX + stackId);
      }
    } catch (_e) {}
  }

  function ensurePgStackInterdiffRestored(stack) {
    if (!stack || !Array.isArray(stack.commits) || stack.commits.length === 0) return;
    const multiPsCommits = stack.commits.filter(c => {
      const rawList = Array.isArray(c.patch_sets) && c.patch_sets.length > 0
        ? c.patch_sets
        : Array.from({ length: Math.max(1, Number(c.patch_set || 1)) }, (_, i) => i + 1);
      return rawList.length > 1;
    });
    multiPsCommits.forEach(c => {
      const num = Number(c.number);
      const sel = getPgSelectedPatchset(num);
      if (!sel || sel.base === "base" || sel.base === "0") {
        const rawList = Array.isArray(c.patch_sets) && c.patch_sets.length > 0
          ? c.patch_sets
          : Array.from({ length: Math.max(1, Number(c.patch_set || 1)) }, (_, i) => i + 1);
        const allPs = Array.from(new Set(rawList.map(Number).filter(n => n > 0))).sort((a, b) => a - b);
        const curTarget = Number((sel && sel.ps) || c.patch_set || 1);
        const curIdx = allPs.indexOf(curTarget);
        const prevPs = curIdx > 0 ? allPs[curIdx - 1] : (curTarget - 1);
        if (prevPs >= 1) {
          state.selectedPatchsetByCl = state.selectedPatchsetByCl || {};
          state.selectedPatchsetByCl[num] = { ps: curTarget, base: String(prevPs) };
          if (typeof setPgPersistedPatchsetComparison === "function") {
            setPgPersistedPatchsetComparison(num, curTarget, prevPs, allPs[allPs.length - 1]);
          }
          if (typeof selectPgPatchsetComparison === "function") {
            selectPgPatchsetComparison(num, curTarget, prevPs, { skipPersistence: true });
          }
        }
      }
    });
  }

  function computePgSidebarDensity(widthPx) {
    const w = Number(widthPx);
    if (!Number.isFinite(w) || w > PG_SIDEBAR_COMPACT_MAX_WIDTH) {
      return "wide";
    }
    if (w <= PG_SIDEBAR_MINI_MAX_WIDTH) {
      return "mini";
    }
    return "compact";
  }

  function applyPgTheme(theme, showNotification = false) {
    const normalized = theme === "light" ? "light" : "dark";
    state.pgTheme = normalized;
    if (document.documentElement && typeof document.documentElement.setAttribute === "function") {
      document.documentElement.setAttribute("data-pg-theme", normalized);
    }
    const lbl = document.getElementById("pgThemeToggleLabel");
    if (lbl) {
      lbl.textContent = normalized === "light" ? "☀️ Light Theme" : "🌙 Dark Theme";
    }
    try {
      window.localStorage.setItem(PG_THEME_STORAGE_KEY, normalized);
    } catch (_e) {}
    if (showNotification) {
      showToast(`PolyGerrit Theme: ${normalized === "light" ? "Light Mode" : "Dark Mode"} (Shortcut: t)`);
    }
  }

  function togglePgTheme() {
    applyPgTheme(state.pgTheme === "light" ? "dark" : "light", true);
  }

  function getStoredPgDiffViewMode() {
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        const m = window.localStorage.getItem(PG_DIFF_VIEW_MODE_STORAGE_KEY);
        if (m === "unified" || m === "side-by-side") return m;
      }
    } catch (_e) {}
    return "side-by-side";
  }

  function hasAnyOwnProp(obj) {
    if (!obj) return false;
    for (const k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k)) return true;
    }
    return false;
  }

  function getPgDiffViewMode(clNum = null, filePath = null) {
    if (clNum && filePath && hasAnyOwnProp(state.fileDiffModeOverrides)) {
      const key = `${Number(clNum)}:${filePath}`;
      if (state.fileDiffModeOverrides[key]) {
        return state.fileDiffModeOverrides[key];
      }
    }
    return state.diffViewMode || "side-by-side";
  }

  function setPgDiffViewMode(mode, clNum = null, filePath = null, showNotification = true) {
    const normalized = mode === "unified" ? "unified" : "side-by-side";
    if (clNum && filePath) {
      if (!state.fileDiffModeOverrides) state.fileDiffModeOverrides = {};
      state.fileDiffModeOverrides[`${Number(clNum)}:${filePath}`] = normalized;
      if (showNotification) {
        const baseName = String(filePath).split("/").pop() || filePath;
        showToast(`Diff view for ${baseName}: ${normalized === "side-by-side" ? "Side-by-side (☷)" : "Unified (☰)"}`);
      }
    } else {
      state.diffViewMode = normalized;
      try {
        if (typeof window !== "undefined" && window.localStorage) {
          window.localStorage.setItem(PG_DIFF_VIEW_MODE_STORAGE_KEY, normalized);
        }
      } catch (_e) {}
      if (showNotification) {
        showToast(`Diff view: ${normalized === "side-by-side" ? "Side-by-side (☷)" : "Unified (☰)"} (Shortcut: v)`);
      }
    }
    renderDiffPane({ preserveScroll: true });
  }

  function togglePgDiffViewMode(clNum = null, filePath = null) {
    const current = getPgDiffViewMode(clNum, filePath);
    const next = current === "side-by-side" ? "unified" : "side-by-side";
    setPgDiffViewMode(next, clNum, filePath, true);
  }

  function togglePgFileDiffMode(event, clNum, filePath) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    togglePgDiffViewMode(clNum, filePath);
  }

  const DIFF_PREFS_STORAGE_KEY = "gerrit_ui_pg_diff_preferences";
  const DEFAULT_DIFF_PREFERENCES = {
    context: 10,
    tabWidth: 4,
    wrapLines: true,
    ignoreWhitespace: "none",
    syntaxHighlighting: true,
    showLineNumbers: true,
    intralineDifference: true,
    fontSize: 12,
    fontFamily: "roboto_mono",
    customFontFamily: "",
    lineHeight: 1.5,
    colorScheme: "polygerrit_native",
    lineLength: 100,
    showTabs: false,
    showWhitespaceErrors: true,
    autoMarkViewed: false,
    hideLeftPane: false,
    showOverviewBar: true,
  };

  const DIFF_TAB_WIDTH_OPTIONS = [
    { value: 2, label: "2 spaces", shortLabel: "2 sp", desc: "2 spaces (Web, JSON, Bazel, Google C++)" },
    { value: 4, label: "4 spaces (default)", shortLabel: "4 sp (Default)", desc: "4 spaces (AOSP, Android Studio, Java, Kotlin, Python)" },
    { value: 8, label: "8 spaces", shortLabel: "8 sp", desc: "8 spaces (Linux kernel, legacy C, git standard)" },
  ];

  const DIFF_CONTEXT_OPTIONS = [
    { value: 10, label: "Context: 10", desc: "10 lines (default)" },
    { value: 25, label: "Context: 25", desc: "25 lines" },
    { value: 50, label: "Context: 50", desc: "50 lines" },
    { value: 100, label: "Context: 100", desc: "100 lines" },
    { value: "all", label: "Context: Whole file", desc: "Whole file (no skipped lines)" },
  ];

  const DIFF_FONT_FAMILIES = [
    { id: "roboto_mono", name: "Roboto Mono", css: '"Roboto Mono", monospace', desc: "PolyGerrit default · Google standard" },
    { id: "system_mono", name: "System Monospace", css: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace', desc: "Native OS monospace stack" },
    { id: "consolas", name: "Consolas", css: 'Consolas, "Liberation Mono", "DejaVu Sans Mono", monospace', desc: "Windows / VS Code monospace" },
    { id: "menlo", name: "Menlo / Monaco", css: 'Menlo, Monaco, "Courier New", monospace', desc: "Apple macOS developer standard" },
    { id: "jetbrains_mono", name: "JetBrains Mono", css: '"JetBrains Mono", "Roboto Mono", monospace', desc: "Android Studio & IntelliJ standard" },
    { id: "fira_code", name: "Fira Code", css: '"Fira Code", monospace', desc: "Developer font with coding ligatures" },
    { id: "inconsolata", name: "Inconsolata", css: 'Inconsolata, monospace', desc: "Clean humanist monospace" },
    { id: "source_code_pro", name: "Source Code Pro", css: '"Source Code Pro", monospace', desc: "Adobe open-source developer font" },
    { id: "courier", name: "Courier New", css: '"Courier New", Courier, monospace', desc: "Classic typewriter monospace" },
    { id: "custom", name: "Custom Font...", css: "", desc: "User-specified font family" },
  ];

  const DIFF_LINE_HEIGHTS = [
    { value: 1.15, label: "1.15 — Compact / Dense", desc: "Maximum lines visible per screen" },
    { value: 1.3, label: "1.30 — Tight", desc: "Compact spacing for large diffs" },
    { value: 1.45, label: "1.45 — Standard", desc: "Balanced readability and density" },
    { value: 1.5, label: "1.50 — Comfortable (Default)", desc: "Authentic PolyGerrit default line spacing" },
    { value: 1.65, label: "1.65 — Spacious", desc: "Relaxed spacing for detailed inspection" },
    { value: 1.8, label: "1.80 — Extra Relaxed", desc: "Generous breathing room between lines" },
  ];

  const DIFF_FONT_SIZES = [
    { value: 10, label: "10 px — Extra Small", shortLabel: "10px", desc: "Ultra-compact code for maximum horizontal and vertical overview" },
    { value: 11, label: "11 px — Compact", shortLabel: "11px", desc: "Compact density for laptop screens" },
    { value: 12, label: "12 px — Standard (Default)", shortLabel: "12px", desc: "Authentic PolyGerrit standard code font size" },
    { value: 13, label: "13 px — Medium", shortLabel: "13px", desc: "Slightly enlarged for improved code legibility" },
    { value: 14, label: "14 px — Large", shortLabel: "14px", desc: "High-contrast comfortable reading size" },
    { value: 15, label: "15 px — Extra Large", shortLabel: "15px", desc: "Large code font for high-resolution displays" },
    { value: 16, label: "16 px — Maximum", shortLabel: "16px", desc: "Maximum accessibility code font size" },
  ];

  const DIFF_LINE_LENGTHS = [
    { value: 0, label: "Column limit: Off", shortLabel: "Col: Off", desc: "No vertical column guide line" },
    { value: 80, label: "Column limit: 80 cols", shortLabel: "Col: 80", desc: "80 columns (C / Linux kernel standard)" },
    { value: 100, label: "Column limit: 100 cols (Default)", shortLabel: "Col: 100", desc: "100 columns (AOSP / Google standard)" },
    { value: 120, label: "Column limit: 120 cols", shortLabel: "Col: 120", desc: "120 columns (Widescreen display)" },
  ];

  function resolvePgDiffFontFamily(fontKey, customFont = "") {
    if (fontKey === "custom" && customFont && customFont.trim()) {
      return customFont.trim();
    }
    const found = DIFF_FONT_FAMILIES.find(f => f.id === fontKey);
    if (found && found.css) return found.css;
    if (fontKey && fontKey !== "custom") {
      return fontKey;
    }
    return '"Roboto Mono", monospace';
  }

  function loadPgDiffPreferences() {
    try {
      const raw = window.localStorage.getItem(DIFF_PREFS_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          const merged = Object.assign({}, DEFAULT_DIFF_PREFERENCES, parsed);
          if (!merged.colorScheme) {
            const savedScheme = window.localStorage.getItem(DIFF_COLOR_SCHEME_STORAGE_KEY);
            if (savedScheme && typeof DIFF_COLOR_SCHEMES !== "undefined" && DIFF_COLOR_SCHEMES.some(s => s.id === savedScheme)) {
              merged.colorScheme = savedScheme;
            }
          }
          return merged;
        }
      }
    } catch (_e) {}
    const fallback = Object.assign({}, DEFAULT_DIFF_PREFERENCES);
    try {
      const savedScheme = window.localStorage.getItem(DIFF_COLOR_SCHEME_STORAGE_KEY);
      if (savedScheme && typeof DIFF_COLOR_SCHEMES !== "undefined" && DIFF_COLOR_SCHEMES.some(s => s.id === savedScheme)) {
        fallback.colorScheme = savedScheme;
      }
    } catch (_e) {}
    return fallback;
  }

  function savePgDiffPreferences(prefs) {
    try {
      window.localStorage.setItem(DIFF_PREFS_STORAGE_KEY, JSON.stringify(prefs));
      if (prefs && prefs.colorScheme) {
        window.localStorage.setItem(DIFF_COLOR_SCHEME_STORAGE_KEY, prefs.colorScheme);
      }
    } catch (_e) {}
  }

  function getPgDiffPreferences() {
    if (!state.diffPreferences) {
      state.diffPreferences = loadPgDiffPreferences();
    }
    return state.diffPreferences;
  }

  function applyPgDiffPreferences(prefs = null) {
    const p = prefs || getPgDiffPreferences();
    if (typeof document !== "undefined" && prefs === null && p) {
      const diffContainerFast =
        document.getElementById("diffScroll") ||
        document.getElementById("diffPane") ||
        document.getElementById("diffContainer");
      const ps = p._prefSub;
      const pBits =
        (p.wrapLines ? 1 : 0) |
        (p.showLineNumbers ? 2 : 0) |
        (p.syntaxHighlighting ? 4 : 0) |
        (p.showTabs ? 8 : 0) |
        (p.showWhitespaceErrors !== false ? 16 : 0) |
        (p.autoMarkViewed ? 32 : 0) |
        (p.hideLeftPane ? 64 : 0) |
        (p.intralineDifference !== false ? 128 : 0) |
        (p.showOverviewBar !== false ? 256 : 0);
      if (
        ps &&
        state._lastAppliedDiffContainer === diffContainerFast &&
        state._lastAppliedDiffPrefsRef === p &&
        ps.colorScheme === (p.colorScheme || "") &&
        ps.fontFamily === (p.fontFamily || "roboto_mono") &&
        ps.customFontFamily === (p.customFontFamily || "") &&
        ps.lineHeight === (p.lineHeight || 1.5) &&
        ps.fontSize === (p.fontSize || 12) &&
        ps.tabWidth === (p.tabWidth || 4) &&
        ps.lineLength === p.lineLength &&
        ps.ignoreWhitespace === (p.ignoreWhitespace || "none") &&
        ps.bits === pBits
      ) {
        return;
      }
      p._prefSub = {
        colorScheme: p.colorScheme || "",
        fontFamily: p.fontFamily || "roboto_mono",
        customFontFamily: p.customFontFamily || "",
        lineHeight: p.lineHeight || 1.5,
        fontSize: p.fontSize || 12,
        tabWidth: p.tabWidth || 4,
        lineLength: p.lineLength,
        ignoreWhitespace: p.ignoreWhitespace || "none",
        bits: pBits,
      };
      state._lastAppliedDiffPrefsRef = p;
    }
    if (p && p.colorScheme && typeof DIFF_COLOR_SCHEMES !== "undefined" && DIFF_COLOR_SCHEMES.some(s => s.id === p.colorScheme)) {
      state.diffColorScheme = p.colorScheme;
    }
    const resolvedFont = resolvePgDiffFontFamily(p.fontFamily || "roboto_mono", p.customFontFamily || "");
    const lineHeightVal = String(p.lineHeight || 1.5);
    const fontSizeVal = `${p.fontSize || 12}px`;
    const tabSizeVal = String(p.tabWidth || 4);
    const lineLengthVal = p && p.lineLength !== undefined ? parseInt(p.lineLength, 10) : 100;
    const isColGuideActive = lineLengthVal > 0;

    if (typeof document !== "undefined") {
      const diffContainer =
        document.getElementById("diffScroll") ||
        document.getElementById("diffPane") ||
        document.getElementById("diffContainer");
      const prefSig = `${p.colorScheme || ""}|${resolvedFont}|${lineHeightVal}|${fontSizeVal}|${tabSizeVal}|${lineLengthVal}|${p.wrapLines ? 1 : 0}|${p.showLineNumbers ? 1 : 0}|${p.syntaxHighlighting ? 1 : 0}|${p.ignoreWhitespace || "none"}|${p.showTabs ? 1 : 0}|${p.showWhitespaceErrors !== false ? 1 : 0}|${p.autoMarkViewed ? 1 : 0}|${p.hideLeftPane ? 1 : 0}|${p.intralineDifference !== false ? 1 : 0}|${p.showOverviewBar !== false ? 1 : 0}`;
      if (prefs === null && state._lastAppliedDiffPrefsSig === prefSig && state._lastAppliedDiffContainer === diffContainer) {
        return;
      }
      state._lastAppliedDiffPrefsSig = prefSig;
      state._lastAppliedDiffContainer = diffContainer;
      const root = document.documentElement;
      if (root && root.style) {
        if (typeof root.style.setProperty === "function") {
          root.style.setProperty("--pg-diff-tab-size", tabSizeVal);
          root.style.setProperty("--pg-diff-font-size", fontSizeVal);
          root.style.setProperty("--pg-diff-font-family", resolvedFont);
          root.style.setProperty("--pg-diff-line-height", lineHeightVal);
          root.style.setProperty("--pg-diff-line-length", String(lineLengthVal));
        } else {
          root.style["--pg-diff-tab-size"] = tabSizeVal;
          root.style["--pg-diff-font-size"] = fontSizeVal;
          root.style["--pg-diff-font-family"] = resolvedFont;
          root.style["--pg-diff-line-height"] = lineHeightVal;
          root.style["--pg-diff-line-length"] = String(lineLengthVal);
        }
      }
      if (diffContainer) {
        if (diffContainer.classList) {
          diffContainer.classList.toggle("pg-diff-wrap-lines", !!p.wrapLines);
          diffContainer.classList.toggle("pg-diff-no-wrap", !p.wrapLines);
          diffContainer.classList.toggle("pg-diff-hide-ln", !p.showLineNumbers);
          diffContainer.classList.toggle("pg-diff-no-syntax", !p.syntaxHighlighting);
          diffContainer.classList.toggle("pg-diff-ignore-ws", p.ignoreWhitespace !== "none");
          diffContainer.classList.toggle("pg-diff-col-guide-active", isColGuideActive);
          diffContainer.classList.toggle("pg-diff-show-tabs", !!p.showTabs);
          diffContainer.classList.toggle("pg-diff-show-ws-errors", p.showWhitespaceErrors !== undefined ? !!p.showWhitespaceErrors : true);
          diffContainer.classList.toggle("pg-diff-auto-mark-viewed", !!p.autoMarkViewed);
          diffContainer.classList.toggle("pg-diff-hide-left", !!p.hideLeftPane);
          diffContainer.classList.toggle("pg-diff-no-intraline", p.intralineDifference === false);
          diffContainer.classList.toggle("pg-diff-hide-overview", p.showOverviewBar === false);
        }
        if (diffContainer.style) {
          if (typeof diffContainer.style.setProperty === "function") {
            diffContainer.style.setProperty("--pg-diff-font-family", resolvedFont);
            diffContainer.style.setProperty("--pg-diff-line-height", lineHeightVal);
            diffContainer.style.setProperty("--pg-diff-font-size", fontSizeVal);
            diffContainer.style.setProperty("--pg-diff-line-length", String(lineLengthVal));
          } else {
            diffContainer.style["--pg-diff-font-family"] = resolvedFont;
            diffContainer.style["--pg-diff-line-height"] = lineHeightVal;
            diffContainer.style["--pg-diff-font-size"] = fontSizeVal;
            diffContainer.style["--pg-diff-line-length"] = String(lineLengthVal);
          }
        }
      }
      syncPgDiffToolbarPreferences(p);
      updatePgThemePreview(p.colorScheme, p);
    }
  }

  function setPgDiffPreferences(updates, persist = true, rerender = true) {
    const current = getPgDiffPreferences();
    const next = Object.assign({}, current, updates);
    state.diffPreferences = next;
    if (updates.colorScheme && typeof DIFF_COLOR_SCHEMES !== "undefined" && DIFF_COLOR_SCHEMES.some(s => s.id === updates.colorScheme)) {
      state.diffColorScheme = updates.colorScheme;
      try {
        window.localStorage.setItem(DIFF_COLOR_SCHEME_STORAGE_KEY, updates.colorScheme);
      } catch (_e) {}
      const sel = document.getElementById("diffColorSchemeSelect");
      if (sel && sel.value !== updates.colorScheme) sel.value = updates.colorScheme;
      const prefSel = document.getElementById("pgDiffPrefColorScheme");
      if (prefSel && prefSel.value !== updates.colorScheme) prefSel.value = updates.colorScheme;
      updatePgThemePreview(updates.colorScheme, next);
    }
    if (persist) {
      savePgDiffPreferences(next);
    }
    applyPgDiffPreferences(next);
    if (rerender && typeof renderDiffPane === "function") {
      renderDiffPane({ preserveScroll: true });
    }
    return next;
  }

  function updatePgThemePreview(schemeId = null, prefs = null) {
    const previewEl = document.getElementById("pgDiffPrefThemePreview");
    if (!previewEl) return;
    const p = prefs || getPgDiffPreferences();
    const sid = schemeId || (p && p.colorScheme) || state.diffColorScheme || "polygerrit_native";
    previewEl.setAttribute("data-preview-scheme", sid);
    previewEl.className = `pg-pref-theme-preview scheme-${sid}`;

    const fontSelect = document.getElementById("pgDiffPrefFontFamily");
    const customInput = document.getElementById("pgDiffPrefCustomFont");
    const fontKey = fontSelect ? fontSelect.value : (p.fontFamily || "roboto_mono");
    const customFont = customInput ? customInput.value : (p.customFontFamily || "");
    const resolvedFont = resolvePgDiffFontFamily(fontKey, customFont);

    const lhSelect = document.getElementById("pgDiffPrefLineHeight");
    const lh = lhSelect ? lhSelect.value : String(p.lineHeight || 1.5);

    const fsSelect = document.getElementById("pgDiffPrefFontSize");
    const fs = fsSelect ? `${fsSelect.value}px` : `${p.fontSize || 12}px`;

    const fontObj = DIFF_FONT_FAMILIES.find(f => f.id === fontKey);
    const fontLabel = fontObj ? fontObj.name : (customFont || fontKey);

    const fontLabelEl = document.getElementById("pgDiffPrefPreviewFontLabel");
    if (fontLabelEl) fontLabelEl.textContent = fontLabel;
    const lhLabelEl = document.getElementById("pgDiffPrefPreviewLhLabel");
    if (lhLabelEl) lhLabelEl.textContent = parseFloat(lh).toFixed(2);
    const fsLabelEl = document.getElementById("pgDiffPrefPreviewFsLabel");
    if (fsLabelEl) fsLabelEl.textContent = fs;

    const llSelect = document.getElementById("pgDiffPrefLineLength");
    const llVal = llSelect ? (parseInt(llSelect.value, 10) || 0) : (p && p.lineLength !== undefined ? parseInt(p.lineLength, 10) : 100);
    const colLabelEl = document.getElementById("pgDiffPrefPreviewColLabel");
    if (colLabelEl) colLabelEl.textContent = llVal > 0 ? `${llVal} cols` : "Col: Off";

    const tabSelect = document.getElementById("pgDiffPrefTabWidth");
    const tabVal = tabSelect ? (parseInt(tabSelect.value, 10) || 4) : (p && p.tabWidth !== undefined ? parseInt(p.tabWidth, 10) : 4);
    const tabLabelEl = document.getElementById("pgDiffPrefPreviewTabLabel");
    if (tabLabelEl) tabLabelEl.textContent = `Tab: ${tabVal}`;

    const ctxSelect = document.getElementById("pgDiffPrefContext");
    const ctxRaw = ctxSelect ? ctxSelect.value : (p && p.context !== undefined ? String(p.context) : "10");
    const ctxLabel = (String(ctxRaw).toLowerCase() === "all" || ctxRaw === "10000") ? "Whole file" : `${ctxRaw} lines`;
    const ctxLabelEl = document.getElementById("pgDiffPrefPreviewContextLabel");
    if (ctxLabelEl) ctxLabelEl.textContent = `Ctx: ${ctxLabel}`;

    const wsSelect = document.getElementById("pgDiffPrefIgnoreWhitespace");
    const wsVal = wsSelect ? wsSelect.value : (p && p.ignoreWhitespace || "none");
    const wsLabelMap = { none: "None", leading: "Leading", changes: "Changes", all: "All" };
    const wsLabelEl = document.getElementById("pgDiffPrefPreviewIgnoreWsLabel");
    if (wsLabelEl) wsLabelEl.textContent = `WS: ${wsLabelMap[wsVal] || wsVal}`;

    const hasExplicitPrefs = prefs !== null && prefs !== undefined;
    const wrapCheck = document.getElementById("pgDiffPrefWrapLines");
    const wrapVal = (hasExplicitPrefs && prefs.wrapLines !== undefined) ? !!prefs.wrapLines : (wrapCheck ? !!wrapCheck.checked : (p && p.wrapLines !== undefined ? !!p.wrapLines : true));
    const wrapLabelEl = document.getElementById("pgDiffPrefPreviewWrapLabel");
    if (wrapLabelEl) wrapLabelEl.textContent = `Wrap: ${wrapVal ? "On" : "Off"}`;

    const syntaxCheck = document.getElementById("pgDiffPrefSyntaxHighlight");
    const syntaxVal = (hasExplicitPrefs && prefs.syntaxHighlighting !== undefined) ? !!prefs.syntaxHighlighting : (syntaxCheck ? !!syntaxCheck.checked : (p && p.syntaxHighlighting !== undefined ? !!p.syntaxHighlighting : true));
    const syntaxLabelEl = document.getElementById("pgDiffPrefPreviewSyntaxLabel");
    if (syntaxLabelEl) syntaxLabelEl.textContent = `Syntax: ${syntaxVal ? "On" : "Off"}`;

    const lnCheck = document.getElementById("pgDiffPrefShowLineNumbers");
    const lnVal = (hasExplicitPrefs && prefs.showLineNumbers !== undefined) ? !!prefs.showLineNumbers : (lnCheck ? !!lnCheck.checked : (p && p.showLineNumbers !== undefined ? !!p.showLineNumbers : true));
    const lnLabelEl = document.getElementById("pgDiffPrefPreviewLinesLabel");
    if (lnLabelEl) lnLabelEl.textContent = `Lines: ${lnVal ? "On" : "Off"}`;

    const intraCheck = document.getElementById("pgDiffPrefIntraline");
    const intraVal = (hasExplicitPrefs && prefs.intralineDifference !== undefined) ? !!prefs.intralineDifference : (intraCheck ? !!intraCheck.checked : (p && p.intralineDifference !== undefined ? !!p.intralineDifference : true));
    const intraLabelEl = document.getElementById("pgDiffPrefPreviewIntraLabel");
    if (intraLabelEl) intraLabelEl.textContent = `Intra: ${intraVal ? "On" : "Off"}`;

    const showTabsCheck = document.getElementById("pgDiffPrefShowTabs");
    const showTabsVal = (hasExplicitPrefs && prefs.showTabs !== undefined) ? !!prefs.showTabs : (showTabsCheck ? !!showTabsCheck.checked : (p && p.showTabs !== undefined ? !!p.showTabs : false));
    const showTabsLabelEl = document.getElementById("pgDiffPrefPreviewShowTabsLabel");
    if (showTabsLabelEl) showTabsLabelEl.textContent = `Tabs: ${showTabsVal ? "On" : "Off"}`;

    const showWsCheck = document.getElementById("pgDiffPrefShowWhitespaceErrors");
    const showWsVal = (hasExplicitPrefs && prefs.showWhitespaceErrors !== undefined) ? !!prefs.showWhitespaceErrors : (showWsCheck ? !!showWsCheck.checked : (p && p.showWhitespaceErrors !== undefined ? !!p.showWhitespaceErrors : true));
    const wsErrLabelEl = document.getElementById("pgDiffPrefPreviewWsLabel");
    if (wsErrLabelEl) wsErrLabelEl.textContent = `WS: ${showWsVal ? "On" : "Off"}`;

    const tabPreviewMarker = document.getElementById("pgDiffPreviewTabMarker");
    if (tabPreviewMarker) {
      tabPreviewMarker.style.display = showTabsVal ? "inline" : "none";
    }
    const wsPreviewError = document.getElementById("pgDiffPreviewWsError");
    if (wsPreviewError) {
      wsPreviewError.style.display = showWsVal ? "inline" : "none";
    }

    const showOverviewCheck = document.getElementById("pgDiffPrefShowOverviewBar");
    const showOverviewVal = (hasExplicitPrefs && prefs.showOverviewBar !== undefined) ? !!prefs.showOverviewBar : (showOverviewCheck ? !!showOverviewCheck.checked : (p && p.showOverviewBar !== undefined ? !!p.showOverviewBar : true));

    if (previewEl.classList) {
      previewEl.classList.toggle("pg-diff-col-guide-active", llVal > 0);
      previewEl.classList.toggle("pg-diff-no-syntax", !syntaxVal);
      previewEl.classList.toggle("pg-diff-no-line-numbers", !lnVal);
      previewEl.classList.toggle("pg-diff-no-wrap", !wrapVal);
      previewEl.classList.toggle("pg-diff-no-intraline", !intraVal);
      previewEl.classList.toggle("pg-diff-hide-overview", !showOverviewVal);
    }

    if (previewEl.style) {
      if (typeof previewEl.style.setProperty === "function") {
        previewEl.style.setProperty("font-family", resolvedFont, "important");
        previewEl.style.setProperty("line-height", String(lh));
        previewEl.style.setProperty("font-size", String(fs));
        previewEl.style.setProperty("--pg-diff-line-length", String(llVal));
      } else {
        previewEl.style.fontFamily = resolvedFont;
        previewEl.style.lineHeight = String(lh);
        previewEl.style.fontSize = String(fs);
        previewEl.style["--pg-diff-line-length"] = String(llVal);
      }
    }
  }

  function syncPgDiffContextChips(val) {
    const curVal = (String(val).toLowerCase() === "all" || val === 10000) ? "all" : String(parseInt(val, 10) || 10);
    const chips = document.querySelectorAll ? document.querySelectorAll(".pg-context-chip") : [];
    if (chips && chips.forEach) {
      chips.forEach(chip => {
        const chipVal = chip.getAttribute("data-context");
        const isAct = String(chipVal) === curVal;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
  }

  function applyPgDiffContextPreset(val) {
    const norm = (String(val).toLowerCase() === "all" || val === 10000) ? "all" : (parseInt(val, 10) || 10);
    const ctxSelect = document.getElementById("pgDiffPrefContext");
    if (ctxSelect) ctxSelect.value = String(norm);
    syncPgDiffContextChips(norm);
    updatePgThemePreview();
  }

  function onPgDiffPrefContextChange(val) {
    const norm = (String(val).toLowerCase() === "all" || val === 10000) ? "all" : (parseInt(val, 10) || 10);
    syncPgDiffContextChips(norm);
    updatePgThemePreview();
  }

  function syncPgDiffIgnoreWsChips(val) {
    const curVal = String(val || "none").toLowerCase();
    const chips = document.querySelectorAll ? document.querySelectorAll(".pg-ignore-ws-chip") : [];
    if (chips && chips.forEach) {
      chips.forEach(chip => {
        const chipVal = String(chip.getAttribute("data-ignore-ws") || "").toLowerCase();
        const isAct = chipVal === curVal;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
  }

  function applyPgDiffIgnoreWsPreset(val) {
    const wsMode = String(val || "none").toLowerCase();
    const wsSelect = document.getElementById("pgDiffPrefIgnoreWhitespace");
    if (wsSelect) wsSelect.value = wsMode;
    syncPgDiffIgnoreWsChips(wsMode);
    updatePgThemePreview();
  }

  function onPgDiffPrefIgnoreWhitespaceChange(val) {
    const wsMode = String(val || "none").toLowerCase();
    syncPgDiffIgnoreWsChips(wsMode);
    updatePgThemePreview();
  }

  function onPgDiffPrefFontFamilyChange(val) {
    const customWrap = document.getElementById("pgDiffPrefCustomFontWrap");
    if (customWrap) {
      customWrap.style.display = val === "custom" ? "block" : "none";
    }
    syncPgDiffFontFamilyChips(val);
    updatePgThemePreview();
  }

  function getPgDiffFontFamily() {
    const prefs = getPgDiffPreferences();
    return prefs.fontFamily || "roboto_mono";
  }

  function setPgDiffFontFamily(fontKey, customFont = "", persist = true, rerender = true) {
    const updates = { fontFamily: fontKey };
    if (customFont !== undefined) {
      updates.customFontFamily = customFont;
    }
    syncPgDiffFontFamilyChips(fontKey);
    return setPgDiffPreferences(updates, persist, rerender);
  }

  function syncPgDiffFontFamilyChips(val) {
    const curFont = String(val || "roboto_mono");
    const chips = document.querySelectorAll ? document.querySelectorAll(".pg-font-family-chip") : [];
    if (chips && chips.forEach) {
      chips.forEach(chip => {
        const chipVal = chip.getAttribute("data-font-family");
        const isAct = chipVal === curFont;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
  }

  function previewPgDiffFontFamily(fontKey) {
    const previewEl = document.getElementById("pgDiffPrefThemePreview");
    if (!previewEl) return;
    const fontObj = DIFF_FONT_FAMILIES.find(f => f.id === fontKey);
    if (!fontObj) return;
    const resolvedFont = resolvePgDiffFontFamily(fontKey, "");
    if (previewEl.style && typeof previewEl.style.setProperty === "function") {
      previewEl.style.setProperty("font-family", resolvedFont, "important");
    } else if (previewEl.style) {
      previewEl.style.fontFamily = resolvedFont;
    }
    if (previewEl.classList) {
      previewEl.classList.add("is-previewing-font");
    }
    const fontLabelEl = document.getElementById("pgDiffPrefPreviewFontLabel");
    if (fontLabelEl) {
      fontLabelEl.textContent = `${fontObj.name} (Hover Preview)`;
    }
  }

  function resetPgDiffFontFamilyPreview() {
    const previewEl = document.getElementById("pgDiffPrefThemePreview");
    if (previewEl && previewEl.classList) {
      previewEl.classList.remove("is-previewing-font");
    }
    updatePgThemePreview();
  }

  function applyPgDiffFontFamilyPreset(val) {
    const fontKey = String(val || "roboto_mono");
    const fontSelect = document.getElementById("pgDiffPrefFontFamily");
    if (fontSelect) fontSelect.value = fontKey;
    const customWrap = document.getElementById("pgDiffPrefCustomFontWrap");
    if (customWrap) {
      customWrap.style.display = fontKey === "custom" ? "block" : "none";
    }
    syncPgDiffFontFamilyChips(fontKey);
    const updated = setPgDiffFontFamily(fontKey);
    updatePgThemePreview(null, getPgDiffPreferences());
    const fontObj = DIFF_FONT_FAMILIES.find(f => f.id === fontKey);
    const fontName = fontObj ? fontObj.name : fontKey;
    if (typeof showToast === "function") {
      showToast(`Font family: ${fontName}`);
    }
    return updated;
  }

  function syncPgDiffColorSchemeChips(val) {
    const curScheme = String(val || "polygerrit_native");
    const chips = document.querySelectorAll ? document.querySelectorAll(".pg-color-scheme-chip") : [];
    if (chips && chips.forEach) {
      chips.forEach(chip => {
        const chipVal = chip.getAttribute("data-scheme");
        const isAct = chipVal === curScheme;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
  }

  function previewPgDiffColorScheme(schemeId) {
    const previewEl = document.getElementById("pgDiffPrefThemePreview");
    if (!previewEl) return;
    updatePgThemePreview(schemeId);
    if (previewEl.classList) {
      previewEl.classList.add("is-previewing-scheme");
    }
  }

  function resetPgDiffColorSchemePreview() {
    const previewEl = document.getElementById("pgDiffPrefThemePreview");
    if (previewEl && previewEl.classList) {
      previewEl.classList.remove("is-previewing-scheme");
    }
    const p = getPgDiffPreferences();
    updatePgThemePreview(p.colorScheme || state.diffColorScheme || "polygerrit_native");
  }

  function applyPgDiffColorSchemePreset(schemeId) {
    const sid = String(schemeId || "polygerrit_native");
    const schemeSelect = document.getElementById("pgDiffPrefColorScheme");
    if (schemeSelect) schemeSelect.value = sid;
    syncPgDiffColorSchemeChips(sid);
    setDiffColorScheme(sid, true);
    updatePgThemePreview(sid, getPgDiffPreferences());
    const schemeObj = DIFF_COLOR_SCHEMES.find(s => s.id === sid);
    const label = schemeObj ? schemeObj.label : sid;
    if (typeof showToast === "function") {
      showToast(`Color scheme: ${label}`);
    }
  }

  function getPgDiffLineHeight() {
    const prefs = getPgDiffPreferences();
    return parseFloat(prefs.lineHeight) || 1.5;
  }

  function setPgDiffLineHeight(lineHeight, persist = true, rerender = false) {
    const val = typeof lineHeight === "number" ? lineHeight : parseFloat(lineHeight) || 1.5;
    const clamped = Math.max(1.15, Math.min(1.80, val));
    const updated = setPgDiffPreferences({ lineHeight: clamped }, persist, rerender);
    syncPgDiffToolbarPreferences(updated);
    return clamped;
  }

  function getPgDiffFontSize() {
    const prefs = getPgDiffPreferences();
    return parseInt(prefs.fontSize, 10) || 12;
  }

  function setPgDiffFontSize(fontSize, showNotification = true, rerender = false) {
    const val = typeof fontSize === "number" ? fontSize : parseInt(fontSize, 10) || 12;
    const clamped = Math.max(10, Math.min(16, val));
    const updated = setPgDiffPreferences({ fontSize: clamped }, true, rerender);
    syncPgDiffToolbarPreferences(updated);
    if (showNotification && typeof showToast === "function") {
      const found = DIFF_FONT_SIZES.find(s => s.value === clamped);
      const desc = (found && found.label && found.label.includes("—")) ? ` (${found.label.split("—")[1].trim()})` : "";
      showToast(`Diff font size: ${clamped}px${desc} (Alt+= / Alt+-)`);
    }
    return clamped;
  }

  function stepPgDiffFontSize(delta = 1, showNotification = true) {
    const cur = getPgDiffFontSize();
    const values = DIFF_FONT_SIZES.map(s => s.value);
    let idx = values.indexOf(cur);
    if (idx === -1) idx = values.indexOf(12);
    let nextIdx = idx + delta;
    if (nextIdx < 0) nextIdx = 0;
    if (nextIdx >= values.length) nextIdx = values.length - 1;
    return setPgDiffFontSize(values[nextIdx], showNotification, false);
  }

  function cyclePgDiffFontSize(showNotification = true) {
    const cur = getPgDiffFontSize();
    const values = DIFF_FONT_SIZES.map(s => s.value);
    let idx = values.indexOf(cur);
    if (idx === -1) idx = values.indexOf(12);
    const nextIdx = (idx + 1) % values.length;
    return setPgDiffFontSize(values[nextIdx], showNotification, false);
  }

  function applyPgDiffFontSizePreset(size) {
    const val = parseInt(size, 10) || 12;
    const fontSelect = document.getElementById("pgDiffPrefFontSize");
    if (fontSelect) fontSelect.value = String(val);
    setPgDiffFontSize(val, false, false);
    updatePgThemePreview();
  }

  function onPgDiffPrefFontSizeChange(val) {
    const parsed = parseInt(val, 10) || 12;
    setPgDiffFontSize(parsed, false, false);
    updatePgThemePreview();
  }

  function getPgDiffLineLength() {
    const prefs = getPgDiffPreferences();
    return prefs && prefs.lineLength !== undefined ? parseInt(prefs.lineLength, 10) : 100;
  }

  function setPgDiffLineLength(lineLength, showNotification = true, rerender = false) {
    const val = typeof lineLength === "number" ? lineLength : parseInt(lineLength, 10);
    const parsed = isNaN(val) ? 100 : Math.max(0, Math.min(250, val));
    const updated = setPgDiffPreferences({ lineLength: parsed }, true, rerender);
    syncPgDiffToolbarPreferences(updated);
    if (showNotification && typeof showToast === "function") {
      const desc = parsed > 0 ? `${parsed} cols` : "Off";
      showToast(`Diff column limit: ${desc} (Alt+G)`);
    }
    return parsed;
  }

  function cyclePgDiffLineLength(showNotification = true) {
    const cur = getPgDiffLineLength();
    const values = DIFF_LINE_LENGTHS.map(l => l.value);
    let idx = values.indexOf(cur);
    if (idx === -1) idx = values.indexOf(100);
    const nextIdx = (idx + 1) % values.length;
    return setPgDiffLineLength(values[nextIdx], showNotification, false);
  }

  function applyPgDiffLineLengthPreset(length) {
    const val = parseInt(length, 10) || 0;
    const lineLengthSelect = document.getElementById("pgDiffPrefLineLength");
    if (lineLengthSelect) lineLengthSelect.value = String(val);
    setPgDiffLineLength(val, false, false);
    updatePgThemePreview();
  }

  function onPgDiffPrefLineLengthChange(val) {
    const parsed = parseInt(val, 10) || 0;
    setPgDiffLineLength(parsed, false, false);
    updatePgThemePreview();
  }

  function stepPgDiffLineHeight(delta = 1, showNotification = true) {
    const prefs = getPgDiffPreferences();
    const cur = parseFloat(prefs.lineHeight) || 1.5;
    const values = DIFF_LINE_HEIGHTS.map(d => d.value);
    let idx = values.findIndex(v => Math.abs(v - cur) < 0.05);
    if (idx === -1) idx = values.findIndex(v => Math.abs(v - 1.5) < 0.05);
    if (idx === -1) idx = 3;
    let nextIdx = idx + delta;
    if (nextIdx < 0) nextIdx = 0;
    if (nextIdx >= values.length) nextIdx = values.length - 1;
    const nextVal = values[nextIdx];
    setPgDiffLineHeight(nextVal, true, false);
    syncPgDiffLineHeightChips(nextVal);
    const item = DIFF_LINE_HEIGHTS[nextIdx];
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff line spacing: ${item ? item.label : nextVal} (Alt+D / Alt+Shift+D / Alt+[ / Alt+])`);
    }
    return nextVal;
  }

  function cyclePgDiffLineHeight(showNotification = true) {
    const prefs = getPgDiffPreferences();
    const cur = parseFloat(prefs.lineHeight) || 1.5;
    const values = DIFF_LINE_HEIGHTS.map(d => d.value);
    let idx = values.findIndex(v => Math.abs(v - cur) < 0.05);
    if (idx === -1) idx = 3;
    const nextIdx = (idx + 1) % values.length;
    const nextVal = values[nextIdx];
    setPgDiffLineHeight(nextVal, true, false);
    syncPgDiffLineHeightChips(nextVal);
    const item = DIFF_LINE_HEIGHTS[nextIdx];
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff line spacing: ${item ? item.label : nextVal} (Alt+D)`);
    }
    return nextVal;
  }

  function syncPgDiffLineHeightChips(val) {
    const curLh = parseFloat(val) || 1.5;
    const lhChips = document.querySelectorAll ? document.querySelectorAll(".pg-line-height-chip") : [];
    if (lhChips && lhChips.forEach) {
      lhChips.forEach(chip => {
        const chipVal = parseFloat(chip.getAttribute("data-line-height"));
        const isAct = Math.abs(chipVal - curLh) < 0.05;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
  }

  function applyPgDiffLineHeightPreset(val) {
    const lh = parseFloat(val) || 1.5;
    const lhSelect = document.getElementById("pgDiffPrefLineHeight");
    if (lhSelect) lhSelect.value = String(lh);
    syncPgDiffLineHeightChips(lh);
    const updated = setPgDiffLineHeight(lh, true, false);
    updatePgThemePreview(null, getPgDiffPreferences());
    return updated;
  }

  function onPgDiffPrefLineHeightChange(val) {
    const parsed = parseFloat(val) || 1.5;
    syncPgDiffLineHeightChips(parsed);
    const updated = setPgDiffLineHeight(parsed, true, false);
    updatePgThemePreview(null, getPgDiffPreferences());
    return updated;
  }

  function onPgDiffPrefColorSchemeChange(val) {
    syncPgDiffColorSchemeChips(val);
    updatePgThemePreview(val);
  }

  function openPgDiffPreferencesDialog() {
    state.diffPreferencesDialogOpen = true;
    const backdrop = document.getElementById("pgDiffPreferencesBackdrop");
    if (!backdrop) return;
    const prefs = getPgDiffPreferences();

    const ctxSelect = document.getElementById("pgDiffPrefContext");
    if (ctxSelect) ctxSelect.value = String(prefs.context || 10);

    const tabSelect = document.getElementById("pgDiffPrefTabWidth");
    if (tabSelect) tabSelect.value = String(prefs.tabWidth || 4);

    const fontFamilySelect = document.getElementById("pgDiffPrefFontFamily");
    if (fontFamilySelect) fontFamilySelect.value = prefs.fontFamily || "roboto_mono";

    const customFontInput = document.getElementById("pgDiffPrefCustomFont");
    if (customFontInput) customFontInput.value = prefs.customFontFamily || "";

    const customFontWrap = document.getElementById("pgDiffPrefCustomFontWrap");
    if (customFontWrap) customFontWrap.style.display = (prefs.fontFamily === "custom") ? "block" : "none";

    const fontSelect = document.getElementById("pgDiffPrefFontSize");
    if (fontSelect) fontSelect.value = String(prefs.fontSize || 12);
    const curFs = parseInt(prefs.fontSize, 10) || 12;
    const fsChips = document.querySelectorAll(".pg-font-size-chip");
    if (fsChips && fsChips.forEach) {
      fsChips.forEach(chip => {
        const chipVal = parseInt(chip.getAttribute("data-font-size"), 10);
        const isAct = chipVal === curFs;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }

    const lineLengthSelect = document.getElementById("pgDiffPrefLineLength");
    if (lineLengthSelect) lineLengthSelect.value = String(prefs.lineLength !== undefined ? prefs.lineLength : 100);
    const curLl = parseInt(prefs.lineLength !== undefined ? prefs.lineLength : 100, 10);
    const llChips = document.querySelectorAll(".pg-line-length-chip");
    if (llChips && llChips.forEach) {
      llChips.forEach(chip => {
        const chipVal = parseInt(chip.getAttribute("data-line-length"), 10);
        const isAct = chipVal === curLl;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }

    const lineHeightSelect = document.getElementById("pgDiffPrefLineHeight");
    if (lineHeightSelect) lineHeightSelect.value = String(prefs.lineHeight || 1.5);
    syncPgDiffLineHeightChips(prefs.lineHeight || 1.5);

    const schemeSelect = document.getElementById("pgDiffPrefColorScheme");
    const activeScheme = prefs.colorScheme || state.diffColorScheme || "polygerrit_native";
    if (schemeSelect) schemeSelect.value = activeScheme;
    updatePgThemePreview(activeScheme, prefs);

    const wsSelect = document.getElementById("pgDiffPrefIgnoreWhitespace");
    if (wsSelect) wsSelect.value = String(prefs.ignoreWhitespace || "none");

    const wrapCheck = document.getElementById("pgDiffPrefWrapLines");
    if (wrapCheck) wrapCheck.checked = !!prefs.wrapLines;

    const syntaxCheck = document.getElementById("pgDiffPrefSyntaxHighlight");
    if (syntaxCheck) syntaxCheck.checked = !!prefs.syntaxHighlighting;

    const lnCheck = document.getElementById("pgDiffPrefShowLineNumbers");
    if (lnCheck) lnCheck.checked = !!prefs.showLineNumbers;

    const intraCheck = document.getElementById("pgDiffPrefIntraline");
    if (intraCheck) intraCheck.checked = prefs.intralineDifference !== false;

    const showTabsCheck = document.getElementById("pgDiffPrefShowTabs");
    if (showTabsCheck) showTabsCheck.checked = !!prefs.showTabs;

    const showWsCheck = document.getElementById("pgDiffPrefShowWhitespaceErrors");
    if (showWsCheck) showWsCheck.checked = prefs.showWhitespaceErrors !== undefined ? !!prefs.showWhitespaceErrors : true;

    const autoMarkCheck = document.getElementById("pgDiffPrefAutoMarkViewed");
    if (autoMarkCheck) autoMarkCheck.checked = !!prefs.autoMarkViewed;

    const hideLeftCheck = document.getElementById("pgDiffPrefHideLeftPane");
    if (hideLeftCheck) hideLeftCheck.checked = !!prefs.hideLeftPane;

    const showOverviewCheck = document.getElementById("pgDiffPrefShowOverviewBar");
    if (showOverviewCheck) showOverviewCheck.checked = prefs.showOverviewBar !== false;

    syncPgDiffContextChips(prefs.context !== undefined ? prefs.context : 10);
    syncPgDiffTabWidthChips(prefs.tabWidth || 4);
    syncPgDiffFontFamilyChips(prefs.fontFamily || "roboto_mono");
    syncPgDiffColorSchemeChips(activeScheme);
    syncPgDiffIgnoreWsChips(prefs.ignoreWhitespace || "none");

    backdrop.classList.add("open");
    backdrop.setAttribute("aria-hidden", "false");
    setTimeout(() => {
      if (ctxSelect && typeof ctxSelect.focus === "function") ctxSelect.focus();
    }, 50);
  }

  function closePgDiffPreferencesDialog() {
    state.diffPreferencesDialogOpen = false;
    const backdrop = document.getElementById("pgDiffPreferencesBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function togglePgDiffPreferencesDialog() {
    if (state.diffPreferencesDialogOpen) {
      closePgDiffPreferencesDialog();
    } else {
      openPgDiffPreferencesDialog();
    }
  }

  function onPgDiffPreferencesBackdropClick(e) {
    if (e.target && e.target.id === "pgDiffPreferencesBackdrop") {
      closePgDiffPreferencesDialog();
    }
  }

  function saveAndApplyPgDiffPreferences() {
    const ctxSelect = document.getElementById("pgDiffPrefContext");
    const tabSelect = document.getElementById("pgDiffPrefTabWidth");
    const fontFamilySelect = document.getElementById("pgDiffPrefFontFamily");
    const customFontInput = document.getElementById("pgDiffPrefCustomFont");
    const fontSelect = document.getElementById("pgDiffPrefFontSize");
    const lineLengthSelect = document.getElementById("pgDiffPrefLineLength");
    const lineHeightSelect = document.getElementById("pgDiffPrefLineHeight");
    const schemeSelect = document.getElementById("pgDiffPrefColorScheme");
    const wsSelect = document.getElementById("pgDiffPrefIgnoreWhitespace");
    const wrapCheck = document.getElementById("pgDiffPrefWrapLines");
    const syntaxCheck = document.getElementById("pgDiffPrefSyntaxHighlight");
    const lnCheck = document.getElementById("pgDiffPrefShowLineNumbers");
    const intraCheck = document.getElementById("pgDiffPrefIntraline");
    const showTabsCheck = document.getElementById("pgDiffPrefShowTabs");
    const showWsCheck = document.getElementById("pgDiffPrefShowWhitespaceErrors");
    const autoMarkCheck = document.getElementById("pgDiffPrefAutoMarkViewed");
    const hideLeftCheck = document.getElementById("pgDiffPrefHideLeftPane");
    const showOverviewCheck = document.getElementById("pgDiffPrefShowOverviewBar");

    const chosenScheme = schemeSelect ? schemeSelect.value : (state.diffColorScheme || "polygerrit_native");

    const newPrefs = {
      context: ctxSelect ? (ctxSelect.value === "all" ? "all" : parseInt(ctxSelect.value, 10) || 10) : 10,
      tabWidth: tabSelect ? (parseInt(tabSelect.value, 10) || 4) : 4,
      fontFamily: fontFamilySelect ? fontFamilySelect.value : (getPgDiffPreferences().fontFamily || "roboto_mono"),
      customFontFamily: customFontInput ? customFontInput.value.trim() : (getPgDiffPreferences().customFontFamily || ""),
      fontSize: fontSelect ? (parseInt(fontSelect.value, 10) || 12) : 12,
      lineHeight: lineHeightSelect ? (parseFloat(lineHeightSelect.value) || 1.5) : 1.5,
      colorScheme: chosenScheme,
      lineLength: lineLengthSelect ? (parseInt(lineLengthSelect.value, 10) || 0) : 100,
      ignoreWhitespace: wsSelect ? wsSelect.value : "none",
      wrapLines: wrapCheck ? !!wrapCheck.checked : true,
      syntaxHighlighting: syntaxCheck ? !!syntaxCheck.checked : true,
      showLineNumbers: lnCheck ? !!lnCheck.checked : true,
      intralineDifference: intraCheck ? !!intraCheck.checked : true,
      showTabs: showTabsCheck ? !!showTabsCheck.checked : false,
      showWhitespaceErrors: showWsCheck ? !!showWsCheck.checked : true,
      autoMarkViewed: autoMarkCheck ? !!autoMarkCheck.checked : false,
      hideLeftPane: hideLeftCheck ? !!hideLeftCheck.checked : false,
      showOverviewBar: showOverviewCheck ? !!showOverviewCheck.checked : true,
    };

    setPgDiffPreferences(newPrefs, true, true);
    setDiffColorScheme(chosenScheme, false);
    syncPgDiffContextChips(newPrefs.context);
    syncPgDiffTabWidthChips(newPrefs.tabWidth);
    syncPgDiffFontFamilyChips(newPrefs.fontFamily);
    syncPgDiffColorSchemeChips(chosenScheme);
    syncPgDiffIgnoreWsChips(newPrefs.ignoreWhitespace);
    closePgDiffPreferencesDialog();
    if (typeof showToast === "function") {
      showToast("Diff preferences updated ✓ (Shortcut: ,)");
    }
  }

  function resetPgDiffPreferencesToDefaults() {
    setPgDiffPreferences(Object.assign({}, DEFAULT_DIFF_PREFERENCES), true, true);
    setDiffColorScheme(DEFAULT_DIFF_PREFERENCES.colorScheme, false);
    const ctxSelect = document.getElementById("pgDiffPrefContext");
    if (ctxSelect) ctxSelect.value = String(DEFAULT_DIFF_PREFERENCES.context);
    const tabSelect = document.getElementById("pgDiffPrefTabWidth");
    if (tabSelect) tabSelect.value = String(DEFAULT_DIFF_PREFERENCES.tabWidth);
    const fontFamilySelect = document.getElementById("pgDiffPrefFontFamily");
    if (fontFamilySelect) fontFamilySelect.value = DEFAULT_DIFF_PREFERENCES.fontFamily;
    const customFontInput = document.getElementById("pgDiffPrefCustomFont");
    if (customFontInput) customFontInput.value = DEFAULT_DIFF_PREFERENCES.customFontFamily;
    const customFontWrap = document.getElementById("pgDiffPrefCustomFontWrap");
    if (customFontWrap) customFontWrap.style.display = "none";
    const fontSelect = document.getElementById("pgDiffPrefFontSize");
    if (fontSelect) fontSelect.value = String(DEFAULT_DIFF_PREFERENCES.fontSize);
    const defFs = DEFAULT_DIFF_PREFERENCES.fontSize || 12;
    const fsChips = document.querySelectorAll(".pg-font-size-chip");
    if (fsChips && fsChips.forEach) {
      fsChips.forEach(chip => {
        const chipVal = parseInt(chip.getAttribute("data-font-size"), 10);
        const isAct = chipVal === defFs;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
    const lineLengthSelect = document.getElementById("pgDiffPrefLineLength");
    if (lineLengthSelect) lineLengthSelect.value = String(DEFAULT_DIFF_PREFERENCES.lineLength);
    const defLl = DEFAULT_DIFF_PREFERENCES.lineLength || 100;
    const llChips = document.querySelectorAll(".pg-line-length-chip");
    if (llChips && llChips.forEach) {
      llChips.forEach(chip => {
        const chipVal = parseInt(chip.getAttribute("data-line-length"), 10);
        const isAct = chipVal === defLl;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
    const lineHeightSelect = document.getElementById("pgDiffPrefLineHeight");
    if (lineHeightSelect) lineHeightSelect.value = String(DEFAULT_DIFF_PREFERENCES.lineHeight);
    const schemeSelect = document.getElementById("pgDiffPrefColorScheme");
    if (schemeSelect) schemeSelect.value = DEFAULT_DIFF_PREFERENCES.colorScheme;
    const wsSelect = document.getElementById("pgDiffPrefIgnoreWhitespace");
    if (wsSelect) wsSelect.value = DEFAULT_DIFF_PREFERENCES.ignoreWhitespace;
    const wrapCheck = document.getElementById("pgDiffPrefWrapLines");
    if (wrapCheck) wrapCheck.checked = DEFAULT_DIFF_PREFERENCES.wrapLines;
    const syntaxCheck = document.getElementById("pgDiffPrefSyntaxHighlight");
    if (syntaxCheck) syntaxCheck.checked = DEFAULT_DIFF_PREFERENCES.syntaxHighlighting;
    const lnCheck = document.getElementById("pgDiffPrefShowLineNumbers");
    if (lnCheck) lnCheck.checked = DEFAULT_DIFF_PREFERENCES.showLineNumbers;
    const intraCheck = document.getElementById("pgDiffPrefIntraline");
    if (intraCheck) intraCheck.checked = DEFAULT_DIFF_PREFERENCES.intralineDifference !== false;
    const showTabsCheck = document.getElementById("pgDiffPrefShowTabs");
    if (showTabsCheck) showTabsCheck.checked = !!DEFAULT_DIFF_PREFERENCES.showTabs;
    const showWsCheck = document.getElementById("pgDiffPrefShowWhitespaceErrors");
    if (showWsCheck) showWsCheck.checked = !!DEFAULT_DIFF_PREFERENCES.showWhitespaceErrors;
    const autoMarkCheck = document.getElementById("pgDiffPrefAutoMarkViewed");
    if (autoMarkCheck) autoMarkCheck.checked = !!DEFAULT_DIFF_PREFERENCES.autoMarkViewed;
    const hideLeftCheck = document.getElementById("pgDiffPrefHideLeftPane");
    const showOverviewCheck = document.getElementById("pgDiffPrefShowOverviewBar");
    if (hideLeftCheck) hideLeftCheck.checked = !!DEFAULT_DIFF_PREFERENCES.hideLeftPane;
    if (showOverviewCheck) showOverviewCheck.checked = !!DEFAULT_DIFF_PREFERENCES.showOverviewBar;
    syncPgDiffContextChips(DEFAULT_DIFF_PREFERENCES.context);
    syncPgDiffTabWidthChips(DEFAULT_DIFF_PREFERENCES.tabWidth || 4);
    syncPgDiffFontFamilyChips(DEFAULT_DIFF_PREFERENCES.fontFamily || "roboto_mono");
    syncPgDiffLineHeightChips(DEFAULT_DIFF_PREFERENCES.lineHeight || 1.5);
    syncPgDiffColorSchemeChips(DEFAULT_DIFF_PREFERENCES.colorScheme || "polygerrit_native");
    syncPgDiffIgnoreWsChips(DEFAULT_DIFF_PREFERENCES.ignoreWhitespace || "none");
    updatePgThemePreview(DEFAULT_DIFF_PREFERENCES.colorScheme, DEFAULT_DIFF_PREFERENCES);
    if (typeof showToast === "function") {
      showToast("Diff preferences reset to defaults ✓");
    }
  }

  function renderPgDiffPreferencesButtonHtml() {
    return `<button
      type="button"
      id="btnDiffPreferences"
      class="pg-btn-diff-pref"
      onclick="openPgDiffPreferencesDialog()"
      title="Diff preferences (Shortcut: ,)"
    ><span class="pg-diff-pref-icon" aria-hidden="true">⚙️</span> Preferences</button>`;
  }

  function renderPgDiffWrapButtonHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const isWrapped = !!prefs.wrapLines;
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="btnToggleDiffWrap-${escapeHtml(String(clNum))}"` : ` id="btnToggleDiffWrap"`;
    return `<button
      type="button"
      ${idAttr}
      class="pg-btn-diff-wrap pg-btn-diff-pref ${isWrapped ? "is-active active" : ""}"
      ${numAttr}
      onclick="togglePgDiffWordWrap()"
      title="Toggle diff word wrap (${isWrapped ? "Wrap: ON" : "Wrap: OFF"}) — Shortcut: w"
      aria-pressed="${isWrapped ? "true" : "false"}"
    ><span class="pg-diff-wrap-icon" aria-hidden="true">↩️</span> <span class="pg-diff-wrap-label">Wrap: ${isWrapped ? "ON" : "OFF"}</span></button>`;
  }

  function renderPgDiffLineNumbersButtonHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const isShowLn = !!prefs.showLineNumbers;
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="btnToggleDiffLn-${escapeHtml(String(clNum))}"` : ` id="btnToggleDiffLn"`;
    return `<button
      type="button"
      ${idAttr}
      class="pg-btn-diff-ln pg-btn-diff-pref ${isShowLn ? "is-active active" : ""}"
      ${numAttr}
      onclick="togglePgDiffLineNumbers()"
      title="Toggle diff line numbers (${isShowLn ? "Lines: ON" : "Lines: OFF"}) — Shortcut: l"
      aria-pressed="${isShowLn ? "true" : "false"}"
    ><span class="pg-diff-ln-icon" aria-hidden="true">🔢</span> <span class="pg-diff-ln-label">Lines: ${isShowLn ? "ON" : "OFF"}</span></button>`;
  }

  function renderPgDiffIntralineButtonHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const isIntra = prefs.intralineDifference !== false;
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="btnToggleDiffIntra-${escapeHtml(String(clNum))}"` : ` id="btnToggleDiffIntra"`;
    return `<button
      type="button"
      ${idAttr}
      class="pg-btn-diff-intra pg-btn-diff-pref ${isIntra ? "is-active active" : ""}"
      ${numAttr}
      onclick="togglePgDiffIntraline()"
      title="Toggle intraline difference highlighting (${isIntra ? "Intra: ON" : "Intra: OFF"}) — Shortcut: Shift+Alt+I"
      aria-pressed="${isIntra ? "true" : "false"}"
    ><span class="pg-diff-intra-icon" aria-hidden="true">🪄</span> <span class="pg-diff-intra-label">Intra: ${isIntra ? "ON" : "OFF"}</span></button>`;
  }

  function renderPgDiffLeftPaneToggleButtonHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const isHidden = !!prefs.hideLeftPane;
    const mode = typeof getPgDiffViewMode === "function" ? getPgDiffViewMode() : "side-by-side";
    const isSbs = mode === "side-by-side";
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="btnToggleLeftDiff-${escapeHtml(String(clNum))}"` : ` id="btnToggleLeftDiff"`;
    return `<button
      type="button"
      ${idAttr}
      class="pg-btn-diff-pref pg-btn-diff-left-pane ${isHidden ? "is-active active" : ""}"
      style="${isSbs ? "" : "display:none;"}"
      ${numAttr}
      onclick="togglePgLeftDiffPane()"
      title="Hide/show left diff pane (Base side) in side-by-side mode (${isHidden ? "Base: Hidden" : "Base: Shown"}) — Shortcut: Alt+A or Shift+A"
      aria-pressed="${isHidden ? "true" : "false"}"
    ><span class="pg-diff-left-pane-icon" aria-hidden="true">${isHidden ? "◧" : "◨"}</span> <span class="pg-diff-left-pane-label">Base: ${isHidden ? "Hidden" : "Shown"}</span></button>`;
  }

  function togglePgLeftDiffPane(showNotification = true) {
    const prefs = getPgDiffPreferences();
    const nextHidden = !prefs.hideLeftPane;
    setPgDiffPreferences({ hideLeftPane: nextHidden }, true, false);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      const mode = typeof getPgDiffViewMode === "function" ? getPgDiffViewMode() : "side-by-side";
      const sbsNote = mode !== "side-by-side" ? " (Applies in Side-by-side mode · Press 'v' to switch)" : "";
      showToast(`Diff Base side: ${nextHidden ? "HIDDEN" : "SHOWN"} (Shortcut: Alt+A / Shift+A)${sbsNote}`);
    }
    return nextHidden;
  }

  function renderPgDiffOverviewToggleButtonHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const isShowOv = prefs.showOverviewBar !== false;
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="btnToggleDiffOverview-${escapeHtml(String(clNum))}"` : ` id="btnToggleDiffOverview"`;
    return `<button
      type="button"
      ${idAttr}
      class="pg-btn-diff-pref pg-btn-diff-overview ${isShowOv ? "is-active active" : ""}"
      ${numAttr}
      onclick="togglePgDiffOverviewBar()"
      title="Toggle diff minimap overview (${isShowOv ? "Minimap: ON" : "Minimap: OFF"}) — Shortcut: Shift+Alt+O"
      aria-pressed="${isShowOv ? "true" : "false"}"
    ><span class="pg-diff-overview-icon" aria-hidden="true">🗺️</span> <span class="pg-diff-overview-label">Minimap: ${isShowOv ? "ON" : "OFF"}</span></button>`;
  }

  function togglePgDiffOverviewBar(showNotification = true) {
    const prefs = getPgDiffPreferences();
    const nextVal = prefs.showOverviewBar === false ? true : false;
    setPgDiffPreferences({ showOverviewBar: nextVal }, true, false);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff minimap overview: ${nextVal ? "ON" : "OFF"} (Shortcut: Shift+Alt+O)`);
    }
    return nextVal;
  }

  function renderPgDiffGoToLineButtonHtml(clNum = null) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const numAttr = num ? ` data-cl="${escapeHtml(String(num))}"` : "";
    const idAttr = num ? ` id="btnGoToLine-${escapeHtml(String(num))}"` : ` id="btnGoToLine"`;
    return `<button
      type="button"
      ${idAttr}
      class="pg-btn-subtle pg-btn-gotoline"
      ${numAttr}
      onclick="openPgGoToLineModal(${num})"
      title="Go to line number in diff (Shortcut: Ctrl+G or :)"
    ><span class="pg-gotoline-icon" aria-hidden="true">📍</span> <span>Go to line</span></button>`;
  }

  function openPgGoToLineModal(clNum = null) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    state.goToLineModalOpen = true;
    state.goToLineCl = num;

    const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
    let targetFile = activeBlock ? activeBlock.getAttribute("data-file") : null;
    if (!targetFile && state.activeFileByCl && state.activeFileByCl[num]) {
      targetFile = state.activeFileByCl[num];
    }
    const fileName = targetFile ? (String(targetFile).split("/").pop() || targetFile) : "active file";

    const subtitle = document.getElementById("pgGoToLineSubtitle");
    if (subtitle) {
      subtitle.textContent = `Jump to line number in ${fileName}${num ? ` (ag/${num})` : ""}`;
    }

    const backdrop = document.getElementById("pgGoToLineBackdrop");
    if (backdrop) {
      backdrop.classList.add("open", "is-open");
      backdrop.setAttribute("aria-hidden", "false");
    }

    const input = document.getElementById("pgGoToLineInput");
    if (input) {
      input.value = "";
      setTimeout(() => {
        if (typeof input.focus === "function") input.focus();
      }, 30);
    }
  }

  function closePgGoToLineModal() {
    state.goToLineModalOpen = false;
    const backdrop = document.getElementById("pgGoToLineBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open", "is-open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgGoToLineBackdropClick(event) {
    if (event.target && (event.target.id === "pgGoToLineBackdrop" || event.target === event.currentTarget)) {
      closePgGoToLineModal();
    }
  }

  async function executePgGoToLine(clNum = null) {
    const num = Number(clNum || state.goToLineCl || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const input = document.getElementById("pgGoToLineInput");
    const rawVal = input ? input.value.trim() : "";
    if (!rawVal) return;
    const cleanVal = rawVal.replace(/^[:#L\s]+/i, "").trim();
    const lineNum = parseInt(cleanVal, 10);
    if (!Number.isFinite(lineNum) || lineNum <= 0) {
      if (typeof showToast === "function") showToast(`Invalid line number: "${rawVal}"`);
      return;
    }

    const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
    let targetFile = activeBlock ? activeBlock.getAttribute("data-file") : null;
    if (!targetFile && state.activeFileByCl && state.activeFileByCl[num]) {
      targetFile = state.activeFileByCl[num];
    }
    if (!targetFile) {
      const firstBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"]`) || document.querySelector(".pg-file-diff-block");
      if (firstBlock) targetFile = firstBlock.getAttribute("data-file");
    }
    if (!targetFile) {
      const bundle = state.diffsByCl && state.diffsByCl[num];
      if (bundle && Array.isArray(bundle.files) && bundle.files.length > 0) {
        targetFile = bundle.files[0].path || bundle.files[0].new_path;
      }
    }
    if (!targetFile) {
      targetFile = "/COMMIT_MSG";
    }

    closePgGoToLineModal();
    if (typeof scrollToPgCommentOrFile === "function") {
      await scrollToPgCommentOrFile(num, targetFile, lineNum);
    }
  }

  function getPgDiffContext() {
    const prefs = getPgDiffPreferences();
    return prefs.context === "all" ? "all" : (Number(prefs.context) || 10);
  }

  function setPgDiffContext(val, showNotification = true, rerender = true) {
    const norm = (String(val).toLowerCase() === "all" || val === 10000)
      ? "all"
      : (parseInt(val, 10) || 10);
    const updated = setPgDiffPreferences({ context: norm }, true, false);
    syncPgDiffToolbarPreferences(updated);
    if (norm === "all" && typeof expandAllVisibleDiffContext === "function") {
      expandAllVisibleDiffContext();
    } else if (rerender && typeof renderDiffPane === "function") {
      renderDiffPane({ preserveScroll: true });
    }
    if (showNotification && typeof showToast === "function") {
      const labelMap = {
        10: "10 lines (default)",
        25: "25 lines",
        50: "50 lines",
        100: "100 lines",
        all: "Whole file",
      };
      showToast(`Diff context lines: ${labelMap[norm] || norm} (Shortcut: Alt+X)`);
    }
    return norm;
  }

  function cyclePgDiffContext(showNotification = true) {
    const cur = getPgDiffContext();
    const cycle = [10, 25, 50, 100, "all"];
    let idx = cycle.indexOf(cur);
    if (idx === -1) idx = 0;
    const nextVal = cycle[(idx + 1) % cycle.length];
    return setPgDiffContext(nextVal, showNotification, true);
  }

  function stepPgDiffContext(delta = 1, showNotification = true) {
    const cur = getPgDiffContext();
    const cycle = [10, 25, 50, 100, "all"];
    let idx = cycle.indexOf(cur);
    if (idx === -1) idx = 0;
    let nextIdx = idx + delta;
    if (nextIdx < 0) nextIdx = 0;
    if (nextIdx >= cycle.length) nextIdx = cycle.length - 1;
    const nextVal = cycle[nextIdx];
    return setPgDiffContext(nextVal, showNotification, true);
  }

  function renderPgDiffContextSelectHtml(clNum = null) {
    const curCtx = getPgDiffContext();
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="pgDiffContextSelect-${escapeHtml(String(clNum))}"` : ` id="pgDiffContextSelect"`;
    const options = DIFF_CONTEXT_OPTIONS.map(opt => {
      const isSel = String(opt.value) === String(curCtx);
      return `<option value="${opt.value}"${isSel ? " selected" : ""}>${escapeHtml(opt.label)}</option>`;
    }).join("");
    return `<select
      ${idAttr}
      class="pg-diff-context-select diff-scheme-select"
      ${numAttr}
      onchange="setPgDiffContext(this.value, true, true)"
      onclick="event.stopPropagation()"
      title="Diff context lines (Shortcut: Alt+X)"
      aria-label="Diff context lines"
    >${options}</select>`;
  }

  function renderPgDiffTabSizeSelectHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const curWidth = Number(prefs.tabWidth || 4);
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="pgDiffTabSizeSelect-${escapeHtml(String(clNum))}"` : ` id="pgDiffTabSizeSelect"`;
    const options = [2, 4, 8].map(w => {
      return `<option value="${w}"${w === curWidth ? " selected" : ""}>Tab: ${w}</option>`;
    }).join("");
    return `<select
      ${idAttr}
      class="pg-diff-tab-size-select diff-scheme-select"
      ${numAttr}
      onchange="setPgDiffTabWidth(parseInt(this.value, 10), true)"
      onclick="event.stopPropagation()"
      title="Diff tab width (2, 4, 8 spaces)"
      aria-label="Diff tab width"
    >${options}</select>`;
  }

  function renderPgDiffLineHeightSelectHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const curLh = parseFloat(prefs.lineHeight || 1.5);
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="pgDiffLineHeightSelect-${escapeHtml(String(clNum))}"` : ` id="pgDiffLineHeightSelect"`;
    const options = DIFF_LINE_HEIGHTS.map(opt => {
      const isSel = Math.abs(opt.value - curLh) < 0.05;
      return `<option value="${opt.value}"${isSel ? " selected" : ""}>Spacing: ${opt.value.toFixed(2)}</option>`;
    }).join("");
    return `<select
      ${idAttr}
      class="pg-diff-line-height-select diff-scheme-select"
      ${numAttr}
      onchange="setPgDiffLineHeight(parseFloat(this.value), true, false)"
      onclick="event.stopPropagation()"
      title="Diff line spacing / row density (1.15–1.80) — Shortcuts: Alt+D / Alt+Shift+D / Alt+[ / Alt+]"
      aria-label="Diff line spacing"
    >${options}</select>`;
  }

  function renderPgDiffFontSizeSelectHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const curFs = parseInt(prefs.fontSize, 10) || 12;
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="pgDiffFontSizeSelect-${escapeHtml(String(clNum))}"` : ` id="pgDiffFontSizeSelect"`;
    const options = DIFF_FONT_SIZES.map(opt => {
      const isSel = opt.value === curFs;
      return `<option value="${opt.value}"${isSel ? " selected" : ""}>Font: ${opt.value}px</option>`;
    }).join("");
    return `<select
      ${idAttr}
      class="pg-diff-font-size-select diff-scheme-select"
      ${numAttr}
      onchange="setPgDiffFontSize(parseInt(this.value, 10), true, false)"
      onclick="event.stopPropagation()"
      title="Diff code font size (10px–16px) — Shortcuts: Alt+= / Alt+- / Alt+0"
      aria-label="Diff code font size"
    >${options}</select>`;
  }

  function renderPgDiffFontSizeStepperHtml(clNum = null) {
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const decId = clNum ? ` id="btnDiffFontDec-${escapeHtml(String(clNum))}"` : ` id="btnDiffFontDec"`;
    const incId = clNum ? ` id="btnDiffFontInc-${escapeHtml(String(clNum))}"` : ` id="btnDiffFontInc"`;
    const selectHtml = renderPgDiffFontSizeSelectHtml(clNum);
    return `<div class="pg-diff-stepper-group pg-diff-font-stepper" title="Diff code font size (10px–16px) — Shortcuts: Alt+= / Alt+- / Alt+0"${numAttr}>
      <button type="button"${decId} class="pg-btn-stepper pg-btn-font-dec" onclick="stepPgDiffFontSize(-1)" title="Decrease diff font size (Shortcut: Alt+-)" aria-label="Decrease diff font size"><span aria-hidden="true">A-</span></button>
      ${selectHtml}
      <button type="button"${incId} class="pg-btn-stepper pg-btn-font-inc" onclick="stepPgDiffFontSize(1)" title="Increase diff font size (Shortcut: Alt+= or Alt++)" aria-label="Increase diff font size"><span aria-hidden="true">A+</span></button>
    </div>`;
  }

  function renderPgDiffLineHeightStepperHtml(clNum = null) {
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const decId = clNum ? ` id="btnDiffLhDec-${escapeHtml(String(clNum))}"` : ` id="btnDiffLhDec"`;
    const incId = clNum ? ` id="btnDiffLhInc-${escapeHtml(String(clNum))}"` : ` id="btnDiffLhInc"`;
    const selectHtml = renderPgDiffLineHeightSelectHtml(clNum);
    return `<div class="pg-diff-stepper-group pg-diff-lh-stepper" title="Diff line height / density (1.15–1.80) — Shortcuts: Alt+D / Alt+Shift+D / Alt+[ / Alt+]"${numAttr}>
      <button type="button"${decId} class="pg-btn-stepper pg-btn-lh-dec" onclick="stepPgDiffLineHeight(-1)" title="Decrease line spacing / tighter (Shortcut: Alt+[ or Alt+Shift+D)" aria-label="Decrease diff line spacing"><span aria-hidden="true">↕-</span></button>
      ${selectHtml}
      <button type="button"${incId} class="pg-btn-stepper pg-btn-lh-inc" onclick="stepPgDiffLineHeight(1)" title="Increase line spacing / relaxed (Shortcut: Alt+] or Alt+D)" aria-label="Increase diff line spacing"><span aria-hidden="true">↕+</span></button>
    </div>`;
  }

  function renderPgDiffLineLengthSelectHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const curLl = prefs && prefs.lineLength !== undefined ? parseInt(prefs.lineLength, 10) : 100;
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="pgDiffLineLengthSelect-${escapeHtml(String(clNum))}"` : ` id="pgDiffLineLengthSelect"`;
    const options = DIFF_LINE_LENGTHS.map(opt => {
      const isSel = opt.value === curLl;
      return `<option value="${opt.value}"${isSel ? " selected" : ""}>${escapeHtml(opt.shortLabel)}</option>`;
    }).join("");
    return `<select
      ${idAttr}
      class="pg-diff-line-length-select diff-scheme-select"
      ${numAttr}
      onchange="setPgDiffLineLength(parseInt(this.value, 10), true, false)"
      onclick="event.stopPropagation()"
      title="Diff column limit guide (Off, 80, 100, 120) — Shortcut: Alt+G"
      aria-label="Diff column limit guide"
    >${options}</select>`;
  }

  function renderPgDiffWhitespaceSelectHtml(clNum = null) {
    const prefs = getPgDiffPreferences();
    const curWs = String(prefs.ignoreWhitespace || "none");
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    const idAttr = clNum ? ` id="pgDiffWsSelect-${escapeHtml(String(clNum))}"` : ` id="pgDiffWsSelect"`;
    const options = [
      { val: "none", label: "WS: Show" },
      { val: "leading", label: "WS: Ignore leading" },
      { val: "all", label: "WS: Ignore all" },
    ].map(opt => {
      return `<option value="${opt.val}"${opt.val === curWs ? " selected" : ""}>${escapeHtml(opt.label)}</option>`;
    }).join("");
    return `<select
      ${idAttr}
      class="pg-diff-ws-select diff-scheme-select"
      ${numAttr}
      onchange="setPgDiffIgnoreWhitespace(this.value, true)"
      onclick="event.stopPropagation()"
      title="Diff whitespace handling"
      aria-label="Diff whitespace handling"
    >${options}</select>`;
  }

  function togglePgDiffWordWrap(showNotification = true) {
    const prefs = getPgDiffPreferences();
    const nextWrap = !prefs.wrapLines;
    setPgDiffPreferences({ wrapLines: nextWrap }, true, false);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff word wrap: ${nextWrap ? "ON" : "OFF"} (Shortcut: w)`);
    }
    return nextWrap;
  }

  function togglePgDiffLineNumbers(showNotification = true) {
    const prefs = getPgDiffPreferences();
    const nextLn = !prefs.showLineNumbers;
    setPgDiffPreferences({ showLineNumbers: nextLn }, true, false);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff line numbers: ${nextLn ? "ON" : "OFF"} (Shortcut: l)`);
    }
    return nextLn;
  }

  function getPgDiffIntraline() {
    const prefs = getPgDiffPreferences();
    return prefs.intralineDifference !== false;
  }

  function setPgDiffIntraline(enabled, rerender = false, showNotification = false) {
    const isEnabled = !!enabled;
    setPgDiffPreferences({ intralineDifference: isEnabled }, true, rerender);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff intraline highlighting: ${isEnabled ? "ON" : "OFF"} (Shortcut: Shift+Alt+I)`);
    }
    return isEnabled;
  }

  function togglePgDiffIntraline(showNotification = true) {
    const prefs = getPgDiffPreferences();
    const nextIntra = !(prefs.intralineDifference !== false);
    setPgDiffPreferences({ intralineDifference: nextIntra }, true, false);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff intraline highlighting: ${nextIntra ? "ON" : "OFF"} (Shortcut: Shift+Alt+I)`);
    }
    return nextIntra;
  }

  function setPgDiffTabWidth(width, showNotification = true) {
    const w = parseInt(width, 10) || 4;
    setPgDiffPreferences({ tabWidth: w }, true, false);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      showToast(`Diff tab size: ${w} spaces`);
    }
    return w;
  }

  function cyclePgDiffTabWidth(showNotification = true) {
    const prefs = getPgDiffPreferences();
    const widths = [2, 4, 8];
    const cur = Number(prefs.tabWidth || 4);
    const next = widths[(widths.indexOf(cur) + 1) % widths.length] || 4;
    return setPgDiffTabWidth(next, showNotification);
  }

  function syncPgDiffTabWidthChips(val) {
    const curTab = parseInt(val, 10) || 4;
    const tabChips = document.querySelectorAll ? document.querySelectorAll(".pg-tab-width-chip") : [];
    if (tabChips && tabChips.forEach) {
      tabChips.forEach(chip => {
        const chipVal = parseInt(chip.getAttribute("data-tab-width"), 10);
        const isAct = chipVal === curTab;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
  }

  function applyPgDiffTabWidthPreset(val) {
    const width = parseInt(val, 10) || 4;
    const tabSelect = document.getElementById("pgDiffPrefTabWidth");
    if (tabSelect) tabSelect.value = String(width);
    syncPgDiffTabWidthChips(width);
    const updated = setPgDiffTabWidth(width, true);
    updatePgThemePreview(null, getPgDiffPreferences());
    return updated;
  }

  function onPgDiffPrefTabWidthChange(val) {
    const width = parseInt(val, 10) || 4;
    syncPgDiffTabWidthChips(width);
    const updated = setPgDiffTabWidth(width, true);
    updatePgThemePreview(null, getPgDiffPreferences());
    return updated;
  }

  function getPgDiffShowTabs() {
    const prefs = getPgDiffPreferences();
    return !!prefs.showTabs;
  }

  function setPgDiffShowTabs(show, showNotification = true, rerender = true) {
    const updated = setPgDiffPreferences({ showTabs: !!show }, true, rerender);
    syncPgDiffToolbarPreferences(updated);
    if (showNotification && typeof showToast === "function") {
      showToast(`Show tabs: ${show ? "ON" : "OFF"} (Shortcut: Alt+T)`);
    }
    return !!show;
  }

  function togglePgDiffShowTabs(showNotification = true) {
    const cur = getPgDiffShowTabs();
    return setPgDiffShowTabs(!cur, showNotification, true);
  }

  function getPgDiffShowWhitespaceErrors() {
    const prefs = getPgDiffPreferences();
    return prefs.showWhitespaceErrors !== undefined ? !!prefs.showWhitespaceErrors : true;
  }

  function setPgDiffShowWhitespaceErrors(show, showNotification = true, rerender = true) {
    const updated = setPgDiffPreferences({ showWhitespaceErrors: !!show }, true, rerender);
    syncPgDiffToolbarPreferences(updated);
    if (showNotification && typeof showToast === "function") {
      showToast(`Show whitespace errors: ${show ? "ON" : "OFF"} (Shortcut: Alt+E)`);
    }
    return !!show;
  }

  function togglePgDiffShowWhitespaceErrors(showNotification = true) {
    const cur = getPgDiffShowWhitespaceErrors();
    return setPgDiffShowWhitespaceErrors(!cur, showNotification, true);
  }

  function getPgDiffAutoMarkViewed() {
    const prefs = getPgDiffPreferences();
    return !!prefs.autoMarkViewed;
  }

  function setPgDiffAutoMarkViewed(autoMark, showNotification = true) {
    const updated = setPgDiffPreferences({ autoMarkViewed: !!autoMark }, true, false);
    syncPgDiffToolbarPreferences(updated);
    if (showNotification && typeof showToast === "function") {
      showToast(`Auto-mark viewed: ${autoMark ? "ON" : "OFF"}`);
    }
    return !!autoMark;
  }

  function togglePgDiffAutoMarkViewed(showNotification = true) {
    const cur = getPgDiffAutoMarkViewed();
    return setPgDiffAutoMarkViewed(!cur, showNotification);
  }

  function setPgDiffIgnoreWhitespace(mode, showNotification = true) {
    const valid = ["none", "leading", "changes", "all"];
    const m = valid.includes(mode) ? mode : "none";
    setPgDiffPreferences({ ignoreWhitespace: m }, true, true);
    syncPgDiffToolbarPreferences();
    if (showNotification && typeof showToast === "function") {
      const labels = {
        none: "Show all whitespace",
        leading: "Ignore leading whitespace",
        changes: "Ignore whitespace changes",
        all: "Ignore all whitespace",
      };
      showToast(`Diff whitespace: ${labels[m] || m}`);
    }
    return m;
  }

  function syncPgToolbarToggleButtons(selector, isActive, titleText, iconHtml, labelClass, labelText) {
    if (typeof document === "undefined" || !document.querySelectorAll) return;
    const buttons = document.querySelectorAll(selector) || [];
    if (!buttons.forEach) return;
    buttons.forEach(btn => {
      if (!btn) return;
      if (btn.classList) {
        btn.classList.toggle("is-active", isActive);
        btn.classList.toggle("active", isActive);
      }
      if (typeof btn.setAttribute === "function") {
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      }
      btn.title = titleText;
      const label = btn.querySelector ? btn.querySelector(`.${labelClass}`) : null;
      if (label) {
        label.textContent = labelText;
      } else {
        btn.innerHTML = `${iconHtml} <span class="${labelClass}">${labelText}</span>`;
      }
    });
  }

  function syncPgDiffPreferencesDialogDom(p, isWrapped, isShowLn, isIntra, isShowOv, tabW, ws) {
    const dlgWrap = document.getElementById("pgDiffPrefWrapLines");
    if (dlgWrap && dlgWrap.checked !== isWrapped) dlgWrap.checked = isWrapped;
    const dlgLn = document.getElementById("pgDiffPrefShowLineNumbers");
    if (dlgLn && dlgLn.checked !== isShowLn) dlgLn.checked = isShowLn;
    const dlgIntra = document.getElementById("pgDiffPrefIntraline");
    if (dlgIntra && dlgIntra.checked !== isIntra) dlgIntra.checked = isIntra;
    const dlgCtx = document.getElementById("pgDiffPrefContext");
    if (dlgCtx && String(dlgCtx.value) !== String(p.context || 10)) {
      dlgCtx.value = String(p.context || 10);
    }
    const dlgTab = document.getElementById("pgDiffPrefTabWidth");
    if (dlgTab && dlgTab.value !== String(tabW)) dlgTab.value = String(tabW);
    const dlgWs = document.getElementById("pgDiffPrefIgnoreWhitespace");
    if (dlgWs && dlgWs.value !== ws) dlgWs.value = ws;
    const dlgLh = document.getElementById("pgDiffPrefLineHeight");
    if (dlgLh && Math.abs(parseFloat(dlgLh.value) - parseFloat(p.lineHeight || 1.5)) >= 0.05) {
      dlgLh.value = String(p.lineHeight || 1.5);
    }
    const dlgFs = document.getElementById("pgDiffPrefFontSize");
    if (dlgFs && Number(dlgFs.value) !== Number(p.fontSize || 12)) {
      dlgFs.value = String(p.fontSize || 12);
    }
    const curFsVal = parseInt(p.fontSize, 10) || 12;
    const fsChips = document.querySelectorAll ? document.querySelectorAll(".pg-font-size-chip") : [];
    if (fsChips && fsChips.forEach) {
      fsChips.forEach(chip => {
        const chipVal = parseInt(chip.getAttribute("data-font-size"), 10);
        const isAct = chipVal === curFsVal;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
    const dlgLl = document.getElementById("pgDiffPrefLineLength");
    if (dlgLl && Number(dlgLl.value) !== Number(p.lineLength !== undefined ? p.lineLength : 100)) {
      dlgLl.value = String(p.lineLength !== undefined ? p.lineLength : 100);
    }
    const curLlVal = p.lineLength !== undefined ? parseInt(p.lineLength, 10) : 100;
    const llChips = document.querySelectorAll ? document.querySelectorAll(".pg-line-length-chip") : [];
    if (llChips && llChips.forEach) {
      llChips.forEach(chip => {
        const chipVal = parseInt(chip.getAttribute("data-line-length"), 10);
        const isAct = chipVal === curLlVal;
        if (chip.classList) chip.classList.toggle("is-active", isAct);
        if (typeof chip.setAttribute === "function") chip.setAttribute("aria-pressed", isAct ? "true" : "false");
      });
    }
    const dlgFont = document.getElementById("pgDiffPrefFontFamily");
    if (dlgFont && dlgFont.value !== (p.fontFamily || "roboto_mono")) {
      dlgFont.value = p.fontFamily || "roboto_mono";
    }
    syncPgDiffTabWidthChips(tabW);
    const dlgShowTabs = document.getElementById("pgDiffPrefShowTabs");
    if (dlgShowTabs && dlgShowTabs.checked !== !!p.showTabs) dlgShowTabs.checked = !!p.showTabs;
    const dlgShowWs = document.getElementById("pgDiffPrefShowWhitespaceErrors");
    if (dlgShowWs && dlgShowWs.checked !== (p.showWhitespaceErrors !== undefined ? !!p.showWhitespaceErrors : true)) {
      dlgShowWs.checked = p.showWhitespaceErrors !== undefined ? !!p.showWhitespaceErrors : true;
    }
    const dlgAutoMark = document.getElementById("pgDiffPrefAutoMarkViewed");
    if (dlgAutoMark && dlgAutoMark.checked !== !!p.autoMarkViewed) dlgAutoMark.checked = !!p.autoMarkViewed;
    const dlgOv = document.getElementById("pgDiffPrefShowOverviewBar");
    if (dlgOv && dlgOv.checked !== isShowOv) dlgOv.checked = isShowOv;
  }

  function syncPgDiffToolbarPreferences(prefs = null) {
    const p = prefs || getPgDiffPreferences();
    const isWrapped = !!p.wrapLines;
    const isShowLn = !!p.showLineNumbers;
    const isIntra = p.intralineDifference !== false;
    const isShowOv = p.showOverviewBar !== false;
    const tabW = Number(p.tabWidth || 4);
    const ws = String(p.ignoreWhitespace || "none");

    if (typeof document !== "undefined") {
      syncPgToolbarToggleButtons(
        ".pg-btn-diff-wrap",
        isWrapped,
        `Toggle diff word wrap (${isWrapped ? "Wrap: ON" : "Wrap: OFF"}) — Shortcut: w`,
        `<span class="pg-diff-wrap-icon" aria-hidden="true">↩️</span>`,
        "pg-diff-wrap-label",
        `Wrap: ${isWrapped ? "ON" : "OFF"}`
      );
      syncPgToolbarToggleButtons(
        ".pg-btn-diff-ln",
        isShowLn,
        `Toggle diff line numbers (${isShowLn ? "Lines: ON" : "Lines: OFF"}) — Shortcut: l`,
        `<span class="pg-diff-ln-icon" aria-hidden="true">🔢</span>`,
        "pg-diff-ln-label",
        `Lines: ${isShowLn ? "ON" : "OFF"}`
      );
      syncPgToolbarToggleButtons(
        ".pg-btn-diff-intra",
        isIntra,
        `Toggle intraline difference highlighting (${isIntra ? "Intra: ON" : "Intra: OFF"}) — Shortcut: Shift+Alt+I`,
        `<span class="pg-diff-intra-icon" aria-hidden="true">🪄</span>`,
        "pg-diff-intra-label",
        `Intra: ${isIntra ? "ON" : "OFF"}`
      );

      const isHideLeft = !!p.hideLeftPane;
      const curDiffMode = typeof getPgDiffViewMode === "function" ? getPgDiffViewMode() : "side-by-side";
      const isSbsMode = curDiffMode === "side-by-side";
      const leftPaneButtons = document.querySelectorAll ? document.querySelectorAll(".pg-btn-diff-left-pane") : [];
      if (leftPaneButtons && leftPaneButtons.forEach) {
        leftPaneButtons.forEach(btn => {
          if (!btn) return;
          if (btn.style) {
            btn.style.display = isSbsMode ? "inline-flex" : "none";
          }
          if (btn.classList) {
            btn.classList.toggle("is-active", isHideLeft);
            btn.classList.toggle("active", isHideLeft);
          }
          if (typeof btn.setAttribute === "function") {
            btn.setAttribute("aria-pressed", isHideLeft ? "true" : "false");
          }
          btn.title = `Hide/show left diff pane (Base side) in side-by-side mode (${isHideLeft ? "Base: Hidden" : "Base: Shown"}) — Shortcut: Alt+A or Shift+A`;
          const label = btn.querySelector ? btn.querySelector(".pg-diff-left-pane-label") : null;
          const icon = btn.querySelector ? btn.querySelector(".pg-diff-left-pane-icon") : null;
          if (icon) icon.textContent = isHideLeft ? "◧" : "◨";
          if (label) {
            label.textContent = `Base: ${isHideLeft ? "Hidden" : "Shown"}`;
          } else {
            btn.innerHTML = `<span class="pg-diff-left-pane-icon" aria-hidden="true">${isHideLeft ? "◧" : "◨"}</span> <span class="pg-diff-left-pane-label">Base: ${isHideLeft ? "Hidden" : "Shown"}</span>`;
          }
        });
      }

      syncPgToolbarToggleButtons(
        ".pg-btn-diff-overview",
        isShowOv,
        `Toggle diff minimap overview (${isShowOv ? "Minimap: ON" : "Minimap: OFF"}) — Shortcut: Shift+Alt+O`,
        `<span class="pg-diff-overview-icon" aria-hidden="true">🗺️</span>`,
        "pg-diff-overview-label",
        `Minimap: ${isShowOv ? "ON" : "OFF"}`
      );

      const ctxSelects = document.querySelectorAll ? document.querySelectorAll(".pg-diff-context-select") : [];
      if (ctxSelects && ctxSelects.forEach) {
        const curCtx = String(p.context === "all" ? "all" : (Number(p.context) || 10));
        ctxSelects.forEach(sel => {
          if (sel && String(sel.value) !== curCtx) {
            sel.value = curCtx;
          }
        });
      }

      const tabSelects = document.querySelectorAll ? document.querySelectorAll(".pg-diff-tab-size-select") : [];
      if (tabSelects && tabSelects.forEach) {
        tabSelects.forEach(sel => {
          if (sel && Number(sel.value) !== tabW) {
            sel.value = String(tabW);
          }
        });
      }

      const wsSelects = document.querySelectorAll ? document.querySelectorAll(".pg-diff-ws-select") : [];
      if (wsSelects && wsSelects.forEach) {
        wsSelects.forEach(sel => {
          if (sel && sel.value !== ws) {
            sel.value = ws;
          }
        });
      }

      const lhSelects = document.querySelectorAll ? document.querySelectorAll(".pg-diff-line-height-select") : [];
      if (lhSelects && lhSelects.forEach) {
        const curLh = parseFloat(p.lineHeight || 1.5);
        lhSelects.forEach(sel => {
          if (sel) {
            const val = parseFloat(sel.value);
            if (Math.abs(val - curLh) >= 0.05) {
              sel.value = String(curLh);
            }
          }
        });
      }
      syncPgDiffLineHeightChips(p.lineHeight || 1.5);

      const fsSelects = document.querySelectorAll ? document.querySelectorAll(".pg-diff-font-size-select") : [];
      if (fsSelects && fsSelects.forEach) {
        const curFs = parseInt(p.fontSize, 10) || 12;
        fsSelects.forEach(sel => {
          if (sel && parseInt(sel.value, 10) !== curFs) {
            sel.value = String(curFs);
          }
        });
      }

      const llSelects = document.querySelectorAll ? document.querySelectorAll(".pg-diff-line-length-select") : [];
      if (llSelects && llSelects.forEach) {
        const curLl = p.lineLength !== undefined ? parseInt(p.lineLength, 10) : 100;
        llSelects.forEach(sel => {
          if (sel && parseInt(sel.value, 10) !== curLl) {
            sel.value = String(curLl);
          }
        });
      }

      syncPgDiffPreferencesDialogDom(p, isWrapped, isShowLn, isIntra, isShowOv, tabW, ws);
    }
  }

  const PG_DIFF_FIND_OPTIONS_STORAGE_KEY = "gerrit_ui_pg_diff_find_options";

  function loadPgDiffFindOptions() {
    try {
      const raw = localStorage.getItem(PG_DIFF_FIND_OPTIONS_STORAGE_KEY);
      if (raw && state.diffFind) {
        const parsed = JSON.parse(raw);
        state.diffFind.caseSensitive = Boolean(parsed.caseSensitive);
        state.diffFind.wholeWord = Boolean(parsed.wholeWord);
        state.diffFind.isRegex = Boolean(parsed.isRegex);
      }
    } catch (_) {}
  }

  function savePgDiffFindOptions() {
    try {
      if (state.diffFind) {
        localStorage.setItem(PG_DIFF_FIND_OPTIONS_STORAGE_KEY, JSON.stringify({
          caseSensitive: state.diffFind.caseSensitive,
          wholeWord: state.diffFind.wholeWord,
          isRegex: state.diffFind.isRegex,
        }));
      }
    } catch (_) {}
  }

  function renderPgDiffFindButtonHtml(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const isOpen = Boolean(state.diffFind && state.diffFind.isOpen && Number(state.diffFind.cl) === num);
    return `<button
      type="button"
      id="btnDiffFind-${num}"
      class="pg-btn-subtle pg-btn-diff-find ${isOpen ? "is-active" : ""}"
      onclick="togglePgDiffFindBar(${num})"
      title="Find text or regex across diff lines (Shortcut: Ctrl+F)"
    ><span class="pg-diff-find-btn-icon" aria-hidden="true">🔍</span> <span>Find in Diff</span></button>`;
  }

  function renderPgDiffFindBarHtml(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const isOpen = Boolean(state.diffFind && state.diffFind.isOpen && Number(state.diffFind.cl) === num);
    const query = (state.diffFind && state.diffFind.query) || "";
    const isCase = Boolean(state.diffFind && state.diffFind.caseSensitive);
    const isWord = Boolean(state.diffFind && state.diffFind.wholeWord);
    const isRegex = Boolean(state.diffFind && state.diffFind.isRegex);

    return `<div id="pgDiffFindBar-${num}" class="pg-diff-find-bar" style="${isOpen ? "" : "display:none;"}" data-cl="${num}">
      <div class="pg-diff-find-inner">
        <div class="pg-diff-find-input-group">
          <span class="pg-diff-find-icon" aria-hidden="true">🔍</span>
          <input
            type="text"
            id="pgDiffFindInput-${num}"
            class="pg-diff-find-input"
            placeholder="Find in diff (text or /regex/) — Enter/F3/n: next, Shift+Enter/F3/N: prev, Esc: close"
            value="${escapeHtml(query)}"
            oninput="onPgDiffFindInputChanged(${num}, this.value)"
            onkeydown="onPgDiffFindInputKeyDown(event, ${num})"
            aria-label="Find in diff lines"
            autocomplete="off"
            spellcheck="false"
          />
          <span id="pgDiffFindCounter-${num}" class="pg-diff-find-counter">0 matches</span>
        </div>
        <div class="pg-diff-find-stepper">
          <button
            type="button"
            id="btnDiffFindPrev-${num}"
            class="pg-btn-find-nav"
            onclick="stepPgDiffFindMatch('prev', ${num})"
            title="Previous match (Shift+Enter, Shift+F3, or p/N)"
            disabled
          >▲</button>
          <button
            type="button"
            id="btnDiffFindNext-${num}"
            class="pg-btn-find-nav"
            onclick="stepPgDiffFindMatch('next', ${num})"
            title="Next match (Enter, F3, or n)"
            disabled
          >▼</button>
        </div>
        <div class="pg-diff-find-options">
          <button
            type="button"
            id="btnDiffFindOptCase-${num}"
            class="pg-find-opt-btn ${isCase ? "is-active" : ""}"
            onclick="togglePgDiffFindOption('case', ${num})"
            title="Match Case (Alt+C)"
          >Aa</button>
          <button
            type="button"
            id="btnDiffFindOptWord-${num}"
            class="pg-find-opt-btn ${isWord ? "is-active" : ""}"
            onclick="togglePgDiffFindOption('word', ${num})"
            title="Match Whole Word (Alt+W)"
          >\\b</button>
          <button
            type="button"
            id="btnDiffFindOptRegex-${num}"
            class="pg-find-opt-btn ${isRegex ? "is-active" : ""}"
            onclick="togglePgDiffFindOption('regex', ${num})"
            title="Use Regular Expression (Alt+R)"
          >.*</button>
        </div>
        <button
          type="button"
          class="pg-btn-find-close"
          onclick="closePgDiffFindBar(${num})"
          title="Close find bar (Esc)"
        >✕</button>
      </div>
    </div>`;
  }

  function openPgDiffFindBar(clNum) {
    loadPgDiffFindOptions();
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    state.diffFind.isOpen = true;
    state.diffFind.cl = num;
    const bar = document.getElementById(`pgDiffFindBar-${num}`);
    if (bar && bar.style) {
      bar.style.display = "";
    }
    const btn = document.getElementById(`btnDiffFind-${num}`);
    if (btn && btn.classList) {
      btn.classList.add("is-active");
    }
    const inp = document.getElementById(`pgDiffFindInput-${num}`);
    if (inp) {
      if (typeof inp.focus === "function") inp.focus();
      if (typeof inp.select === "function") inp.select();
    }
    if (state.diffFind.query) {
      executePgDiffSearch(num);
    }
  }

  function closePgDiffFindBar(clNum) {
    const num = Number(clNum || (state.diffFind && state.diffFind.cl) || state.selectedClNumber || 0);
    if (state.diffFind) {
      state.diffFind.isOpen = false;
    }
    const bar = document.getElementById(`pgDiffFindBar-${num}`);
    if (bar && bar.style) {
      bar.style.display = "none";
    }
    const btn = document.getElementById(`btnDiffFind-${num}`);
    if (btn && btn.classList) {
      btn.classList.remove("is-active");
    }
    const container = document.getElementById("file-diff-list") ||
      document.querySelector(`#pgStackCommit-${num} .pg-files-container`) ||
      document.querySelector(".pg-files-container") ||
      document.querySelector(".diff-pane") ||
      document.body;
    if (container) {
      clearPgDiffFindHighlights(container);
    }
  }

  function togglePgDiffFindBar(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (state.diffFind && state.diffFind.isOpen && Number(state.diffFind.cl) === num) {
      closePgDiffFindBar(num);
    } else {
      openPgDiffFindBar(num);
    }
  }

  function onPgDiffFindInputChanged(clNum, val) {
    if (!state.diffFind) return;
    state.diffFind.query = String(val != null ? val : "");
    executePgDiffSearch(clNum);
  }

  function onPgDiffFindInputKeyDown(e, clNum) {
    if (!e) return;
    if (e.key === "Enter" || e.key === "F3") {
      if (typeof e.preventDefault === "function") e.preventDefault();
      if (e.shiftKey) {
        stepPgDiffFindMatch("prev", clNum);
      } else {
        stepPgDiffFindMatch("next", clNum);
      }
    } else if (e.key === "ArrowDown") {
      if (typeof e.preventDefault === "function") e.preventDefault();
      stepPgDiffFindMatch("next", clNum);
    } else if (e.key === "ArrowUp") {
      if (typeof e.preventDefault === "function") e.preventDefault();
      stepPgDiffFindMatch("prev", clNum);
    } else if (e.key === "Escape") {
      if (typeof e.preventDefault === "function") e.preventDefault();
      closePgDiffFindBar(clNum);
    } else if (e.altKey && (e.key === "c" || e.key === "C")) {
      if (typeof e.preventDefault === "function") e.preventDefault();
      togglePgDiffFindOption("case", clNum);
    } else if (e.altKey && (e.key === "w" || e.key === "W")) {
      if (typeof e.preventDefault === "function") e.preventDefault();
      togglePgDiffFindOption("word", clNum);
    } else if (e.altKey && (e.key === "r" || e.key === "R")) {
      if (typeof e.preventDefault === "function") e.preventDefault();
      togglePgDiffFindOption("regex", clNum);
    }
  }

  function togglePgDiffFindOption(optionName, clNum) {
    const num = Number(clNum || (state.diffFind && state.diffFind.cl) || state.selectedClNumber || 0);
    if (!state.diffFind) return;
    if (optionName === "case") {
      state.diffFind.caseSensitive = !state.diffFind.caseSensitive;
      const btn = document.getElementById(`btnDiffFindOptCase-${num}`);
      if (btn && btn.classList) btn.classList.toggle("is-active", state.diffFind.caseSensitive);
      showToast(state.diffFind.caseSensitive ? "Match case: ON" : "Match case: OFF");
    } else if (optionName === "word") {
      state.diffFind.wholeWord = !state.diffFind.wholeWord;
      const btn = document.getElementById(`btnDiffFindOptWord-${num}`);
      if (btn && btn.classList) btn.classList.toggle("is-active", state.diffFind.wholeWord);
      showToast(state.diffFind.wholeWord ? "Match whole word: ON" : "Match whole word: OFF");
    } else if (optionName === "regex") {
      state.diffFind.isRegex = !state.diffFind.isRegex;
      const btn = document.getElementById(`btnDiffFindOptRegex-${num}`);
      if (btn && btn.classList) btn.classList.toggle("is-active", state.diffFind.isRegex);
      showToast(state.diffFind.isRegex ? "Use regex: ON" : "Use regex: OFF");
    }
    savePgDiffFindOptions();
    executePgDiffSearch(num);
  }

  function stepPgDiffFindMatch(direction, clNum) {
    const matches = (state.diffFind && state.diffFind.matches) || [];
    if (!matches.length) return;
    const delta = direction === "prev" ? -1 : 1;
    const nextIdx = state.diffFind.activeMatchIndex + delta;
    focusPgDiffFindMatch(clNum, nextIdx);
  }

  function clearPgDiffFindHighlights(root) {
    if (!root || typeof root.querySelectorAll !== "function") return;
    const marks = root.querySelectorAll("mark.pg-diff-find-match");
    const parentsToNormalize = new Set();
    marks.forEach(mark => {
      const parent = mark.parentNode;
      if (parent && typeof parent.replaceChild === "function") {
        const textNode = document.createTextNode ? document.createTextNode(mark.textContent || "") : { nodeType: 3, nodeValue: mark.textContent || "" };
        parent.replaceChild(textNode, mark);
        parentsToNormalize.add(parent);
      }
    });
    parentsToNormalize.forEach(p => {
      if (typeof p.normalize === "function") {
        p.normalize();
      }
    });
    if (state.diffFind) {
      state.diffFind.matches = [];
      state.diffFind.activeMatchIndex = 0;
    }
  }

  function executePgDiffSearch(clNum) {
    const num = Number(clNum || (state.diffFind && state.diffFind.cl) || state.selectedClNumber || 0);
    const container = document.getElementById("file-diff-list") ||
      document.querySelector(`#pgStackCommit-${num} .pg-files-container`) ||
      document.querySelector(".pg-files-container") ||
      document.querySelector(".diff-pane") ||
      document.body;
    if (!container) return;

    clearPgDiffFindHighlights(container);

    const counter = document.getElementById(`pgDiffFindCounter-${num}`);
    const btnPrev = document.getElementById(`btnDiffFindPrev-${num}`);
    const btnNext = document.getElementById(`btnDiffFindNext-${num}`);
    const query = String((state.diffFind && state.diffFind.query) || "").trim();

    if (!query) {
      if (counter) {
        counter.textContent = "0 matches";
        counter.title = "No search query entered";
        counter.className = "pg-diff-find-counter";
      }
      if (btnPrev) btnPrev.disabled = true;
      if (btnNext) btnNext.disabled = true;
      return;
    }

    let reg = null;
    if (state.diffFind.isRegex) {
      let regPattern = query;
      let regFlags = state.diffFind.caseSensitive ? "g" : "gi";
      if (regPattern.startsWith("/") && regPattern.lastIndexOf("/") > 0) {
        const lastSlash = regPattern.lastIndexOf("/");
        const embeddedFlags = regPattern.slice(lastSlash + 1);
        regPattern = regPattern.slice(1, lastSlash);
        if (embeddedFlags.includes("i") && !state.diffFind.caseSensitive) {
          regFlags = "gi";
        }
      }
      try {
        reg = new RegExp(regPattern, regFlags);
      } catch (_e) {
        if (counter) {
          counter.textContent = "Invalid regex";
          counter.title = "Invalid regular expression pattern";
          counter.className = "pg-diff-find-counter no-matches";
        }
        if (btnPrev) btnPrev.disabled = true;
        if (btnNext) btnNext.disabled = true;
        return;
      }
    } else {
      const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const pattern = state.diffFind.wholeWord ? `\\b(?:${escaped})\\b` : escaped;
      try {
        reg = new RegExp(pattern, state.diffFind.caseSensitive ? "g" : "gi");
      } catch (_e) {
        reg = new RegExp(escaped, state.diffFind.caseSensitive ? "g" : "gi");
      }
    }

    const matches = [];
    const candidateEls = typeof container.querySelectorAll === "function"
      ? container.querySelectorAll(".code-text, .code-side-cell, .code-search-link")
      : [];
    const targetEls = (candidateEls && candidateEls.length)
      ? candidateEls
      : (typeof container.querySelectorAll === "function" ? container.querySelectorAll(".code-line, .file-header") : []);

    targetEls.forEach(el => {
      const textNodes = [];
      function collectText(node) {
        if (!node) return;
        if (node.nodeType === 3) {
          if (node.nodeValue && node.nodeValue.length > 0) {
            textNodes.push(node);
          }
        } else if (node.nodeType === 1) {
          if (node.tagName === "SCRIPT" || node.tagName === "STYLE" || (node.classList && (node.classList.contains("pg-diff-find-match") || node.classList.contains("ln")))) {
            return;
          }
          const children = node.childNodes || [];
          for (let i = 0; i < children.length; i++) {
            collectText(children[i]);
          }
        }
      }
      collectText(el);

      textNodes.forEach(tNode => {
        const text = tNode.nodeValue;
        if (!text) return;
        reg.lastIndex = 0;
        if (!reg.test(text)) return;

        reg.lastIndex = 0;
        let m;
        let lastIdx = 0;
        const frag = document.createDocumentFragment ? document.createDocumentFragment() : (document.createElement ? document.createElement("span") : null);
        if (!frag) return;
        let hasAny = false;

        while ((m = reg.exec(text)) !== null) {
          if (m[0].length === 0) {
            reg.lastIndex++;
            continue;
          }
          hasAny = true;
          if (m.index > lastIdx) {
            const prefix = text.slice(lastIdx, m.index);
            frag.appendChild(document.createTextNode ? document.createTextNode(prefix) : { nodeType: 3, nodeValue: prefix });
          }
          const mark = document.createElement ? document.createElement("mark") : null;
          if (mark) {
            mark.className = "pg-diff-find-match";
            mark.textContent = m[0];
            frag.appendChild(mark);
            matches.push(mark);
          }
          lastIdx = reg.lastIndex;
        }

        if (hasAny) {
          if (lastIdx < text.length) {
            const suffix = text.slice(lastIdx);
            frag.appendChild(document.createTextNode ? document.createTextNode(suffix) : { nodeType: 3, nodeValue: suffix });
          }
          if (tNode.parentNode && typeof tNode.parentNode.replaceChild === "function") {
            tNode.parentNode.replaceChild(frag, tNode);
          }
        }
      });
    });

    state.diffFind.matches = matches;
    if (matches.length === 0) {
      if (counter) {
        counter.textContent = "0 matches";
        counter.title = `No matches found for "${query}"`;
        counter.className = "pg-diff-find-counter no-matches";
      }
      if (btnPrev) btnPrev.disabled = true;
      if (btnNext) btnNext.disabled = true;
    } else {
      if (btnPrev) btnPrev.disabled = false;
      if (btnNext) btnNext.disabled = false;
      focusPgDiffFindMatch(num, 0);
    }
  }

  function focusPgDiffFindMatch(clNum, index) {
    const num = Number(clNum || (state.diffFind && state.diffFind.cl) || state.selectedClNumber || 0);
    const matches = (state.diffFind && state.diffFind.matches) || [];
    if (!matches.length) return;
    const normalizedIdx = ((index % matches.length) + matches.length) % matches.length;
    state.diffFind.activeMatchIndex = normalizedIdx;

    matches.forEach((m, idx) => {
      if (m && m.classList) {
        m.classList.toggle("pg-diff-find-match-active", idx === normalizedIdx);
      }
    });

    const activeMark = matches[normalizedIdx];
    if (!activeMark) return;

    // 1. Auto-expand parent file diff block, commit-msg panel, or merge-list panel if collapsed
    let targetFile = "";
    if (typeof activeMark.closest === "function") {
      const fileBlock = activeMark.closest(".pg-file-diff-block");
      if (fileBlock) {
        targetFile = (typeof fileBlock.getAttribute === "function" && fileBlock.getAttribute("data-file")) || "";
        if (fileBlock.classList && fileBlock.classList.contains("is-collapsed")) {
          fileBlock.classList.remove("is-collapsed");
          const chevron = fileBlock.querySelector ? fileBlock.querySelector(".pg-file-chevron") : null;
          if (chevron) chevron.textContent = "▼";
          const codeTable = fileBlock.querySelector ? fileBlock.querySelector(".code-table") : null;
          if (codeTable && codeTable.style) codeTable.style.display = "";
          const ps = Number((typeof fileBlock.getAttribute === "function" && fileBlock.getAttribute("data-ps")) || 1);
          if (targetFile && typeof setPgFileCollapsed === "function") {
            setPgFileCollapsed(num, ps, targetFile, false);
          }
        }
      }

      const commitMsgPanel = activeMark.closest(".commit-msg-panel");
      if (commitMsgPanel) {
        targetFile = "/COMMIT_MSG";
        if (commitMsgPanel.classList) {
          commitMsgPanel.classList.remove("collapsed");
          commitMsgPanel.classList.remove("is-preview");
          commitMsgPanel.classList.add("is-expanded");
        }
        if (typeof uncollapseCommitMessage === "function") {
          uncollapseCommitMessage(num, true);
        }
        const toggleBtn = document.getElementById(`commitMsgToggleBtn-${num}`);
        if (toggleBtn) toggleBtn.textContent = "Hide ▴";
      }

      const mergeListPanel = activeMark.closest(".merge-list-panel");
      if (mergeListPanel) {
        targetFile = "/MERGE_LIST";
        if (typeof uncollapseMergeList === "function") {
          uncollapseMergeList(num);
        }
      }
    }

    // 2. Calculate file-specific match counter info
    let fileMatchCount = 0;
    let fileMatchIdx = 0;
    if (targetFile) {
      for (let i = 0; i < matches.length; i++) {
        const m = matches[i];
        if (!m || typeof m.closest !== "function") continue;
        const mFileBlock = m.closest(".pg-file-diff-block");
        const mFile = mFileBlock ? ((typeof mFileBlock.getAttribute === "function" && mFileBlock.getAttribute("data-file")) || "") :
                      (m.closest(".commit-msg-panel") ? "/COMMIT_MSG" :
                      (m.closest(".merge-list-panel") ? "/MERGE_LIST" : ""));
        if (mFile === targetFile) {
          fileMatchCount++;
          if (i <= normalizedIdx) fileMatchIdx++;
        }
      }
    }

    const counter = document.getElementById(`pgDiffFindCounter-${num}`);
    if (counter) {
      const shortFile = targetFile ? (targetFile.split("/").pop() || targetFile) : "";
      const fileDetail = (targetFile && fileMatchCount > 1) ? ` (${fileMatchIdx}/${fileMatchCount} in ${shortFile})` : (targetFile ? ` (in ${shortFile})` : "");
      counter.textContent = `${normalizedIdx + 1} of ${matches.length}`;
      counter.title = `Match ${normalizedIdx + 1} of ${matches.length}${fileDetail} · Enter/F3/n: next, Shift+Enter/Shift+F3/N: prev, Esc: close`;
      counter.className = "pg-diff-find-counter has-matches";
    }

    // 3. Update active file in file list
    if (targetFile && typeof updatePgActiveFile === "function") {
      updatePgActiveFile(num, targetFile);
    }

    // 4. Smoothly scroll into view
    if (typeof activeMark.scrollIntoView === "function") {
      activeMark.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    // 5. Transient pulse animation
    if (typeof activeMark.closest === "function") {
      const lineEl = activeMark.closest(".code-line") || activeMark.closest(".file-header");
      if (lineEl && lineEl.classList) {
        lineEl.classList.remove("pg-find-focused-pulse");
        if (lineEl.offsetWidth) void lineEl.offsetWidth;
        lineEl.classList.add("pg-find-focused-pulse");
        setTimeout(() => {
          if (lineEl.classList) lineEl.classList.remove("pg-find-focused-pulse");
        }, 1200);
      }
    }
  }

  function isWhitespaceDiffOnly(textA, textB) {
    if (typeof textA !== "string" || typeof textB !== "string") return false;
    const cleanA = textA.replace(/^[+-]/, "").replace(/\s+/g, "");
    const cleanB = textB.replace(/^[+-]/, "").replace(/\s+/g, "");
    return cleanA === cleanB;
  }

  function isWhitespaceDiffOnlyLine(lines, idx) {
    if (!Array.isArray(lines) || idx < 0 || idx >= lines.length) return false;
    const ln = lines[idx];
    if (ln.type !== "add" && ln.type !== "del") return false;
    const cleanText = String(ln.text || "").replace(/^[+-]/, "").replace(/\s+/g, "");
    if (!cleanText) return true;
    if (ln.type === "add" && idx > 0 && lines[idx - 1].type === "del") {
      const prevClean = String(lines[idx - 1].text || "").replace(/^[+-]/, "").replace(/\s+/g, "");
      if (prevClean === cleanText) return true;
    }
    if (ln.type === "del" && idx + 1 < lines.length && lines[idx + 1].type === "add") {
      const nextClean = String(lines[idx + 1].text || "").replace(/^[+-]/, "").replace(/\s+/g, "");
      if (nextClean === cleanText) return true;
    }
    return false;
  }

  function renderPgDiffModeSelectorHtml() {
    const mode = getPgDiffViewMode();
    const isSbs = mode === "side-by-side";
    return `<div class="pg-diff-mode-selector" role="group" aria-label="Diff view mode">
      <button
        type="button"
        id="btnDiffModeSbs"
        class="pg-diff-mode-btn ${isSbs ? "active" : ""}"
        onclick="setPgDiffViewMode('side-by-side', null, null, true)"
        title="Side-by-side diff view (Shortcut: v)"
      ><span class="pg-diff-mode-icon" aria-hidden="true">☷</span> Side-by-side</button>
      <button
        type="button"
        id="btnDiffModeUnified"
        class="pg-diff-mode-btn ${!isSbs ? "active" : ""}"
        onclick="setPgDiffViewMode('unified', null, null, true)"
        title="Unified diff view (Shortcut: v)"
      ><span class="pg-diff-mode-icon" aria-hidden="true">☰</span> Unified</button>
    </div>`;
  }

