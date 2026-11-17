  /* PolyGerrit Custom Dashboards & Saved Searches Manager (<gr-saved-searches-dialog>) */
  function persistPgSavedSearches() {
    try {
      const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
      if (ls) {
        ls.setItem("gerrit_ui_pg_saved_searches", JSON.stringify(state.savedSearches || []));
      }
    } catch (e) {
      // ignore
    }
  }

  function addPgCustomDashboardSection(title, query, showOnDashboard = true) {
    const cleanTitle = String(title || "").trim();
    const cleanQuery = String(query || "").trim();
    if (!cleanTitle || !cleanQuery) {
      if (typeof showToast === "function") showToast("Please specify both title and query");
      return null;
    }
    const id = "sec_" + Date.now() + "_" + Math.floor(Math.random() * 1000);
    const newSec = {
      id,
      title: cleanTitle,
      query: cleanQuery,
      showOnDashboard: Boolean(showOnDashboard),
    };
    state.savedSearches.push(newSec);
    persistPgSavedSearches();
    renderPgSearchPresetsDom();
    if (state.searchSuggestionsOpen) renderPgSearchSuggestionsPopoverHtml();
    renderPgSavedSearchesListHtml();
    const summary = getFilteredStacksSummary();
    renderQueueList(summary.visibleStacks, summary);
    if (typeof showToast === "function") {
      showToast(`Saved custom section: "${cleanTitle}"${newSec.showOnDashboard ? " (on dashboard)" : ""}`);
    }
    return newSec;
  }

  function addPgCustomDashboardSectionFromInputs() {
    const titleInp = document.getElementById("pgNewSearchTitle");
    const queryInp = document.getElementById("pgNewSearchQuery");
    const showCheck = document.getElementById("pgNewSearchShowOnDash");
    const title = titleInp ? titleInp.value : "";
    const query = queryInp ? queryInp.value : "";
    const show = showCheck ? showCheck.checked : true;
    const res = addPgCustomDashboardSection(title, query, show);
    if (res) {
      if (titleInp) titleInp.value = "";
      if (queryInp) queryInp.value = "";
    }
  }

  function applyPgSearchTemplate(title, query) {
    const titleInp = document.getElementById("pgNewSearchTitle");
    const queryInp = document.getElementById("pgNewSearchQuery");
    if (titleInp) titleInp.value = title;
    if (queryInp) queryInp.value = query;
  }

  function deletePgSavedSearch(id) {
    const idx = (state.savedSearches || []).findIndex(s => s.id === id);
    if (idx === -1) return;
    const removed = state.savedSearches.splice(idx, 1)[0];
    persistPgSavedSearches();
    renderPgSearchPresetsDom();
    if (state.searchSuggestionsOpen) renderPgSearchSuggestionsPopoverHtml();
    renderPgSavedSearchesListHtml();
    const summary = getFilteredStacksSummary();
    renderQueueList(summary.visibleStacks, summary);
    if (typeof showToast === "function") {
      showToast(`Deleted saved search: "${removed.title}"`);
    }
  }

  function toggleSavedSearchDashboardVisibility(id) {
    const item = (state.savedSearches || []).find(s => s.id === id);
    if (!item) return;
    item.showOnDashboard = !item.showOnDashboard;
    persistPgSavedSearches();
    renderPgSearchPresetsDom();
    if (state.searchSuggestionsOpen) renderPgSearchSuggestionsPopoverHtml();
    renderPgSavedSearchesListHtml();
    const summary = getFilteredStacksSummary();
    renderQueueList(summary.visibleStacks, summary);
    if (typeof showToast === "function") {
      showToast(item.showOnDashboard ? `Pinned "${item.title}" to dashboard` : `Unpinned "${item.title}" from dashboard`);
    }
  }

  function movePgSavedSearch(id, delta) {
    const idx = (state.savedSearches || []).findIndex(s => s.id === id);
    if (idx === -1) return;
    const targetIdx = idx + delta;
    if (targetIdx < 0 || targetIdx >= state.savedSearches.length) return;
    const item = state.savedSearches.splice(idx, 1)[0];
    state.savedSearches.splice(targetIdx, 0, item);
    persistPgSavedSearches();
    renderPgSavedSearchesListHtml();
    const summary = getFilteredStacksSummary();
    renderQueueList(summary.visibleStacks, summary);
  }

  function applyPgSavedSearch(idOrQuery) {
    const item = (state.savedSearches || []).find(s => s.id === idOrQuery);
    const query = item ? item.query : String(idOrQuery || "");
    const title = item ? item.title : query;
    state.searchQuery = query;
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = query;
    closeSavedSearchesModal();
    closePgSearchSuggestions();
    onQueueSearchInput(query);
    if (typeof showToast === "function") {
      showToast(`Applied search: "${title}"`);
    }
  }

  function resetPgSavedSearchesToDefaults() {
    state.savedSearches = JSON.parse(JSON.stringify(DEFAULT_SAVED_SEARCHES));
    persistPgSavedSearches();
    renderPgSearchPresetsDom();
    if (state.searchSuggestionsOpen) renderPgSearchSuggestionsPopoverHtml();
    renderPgSavedSearchesListHtml();
    const summary = getFilteredStacksSummary();
    renderQueueList(summary.visibleStacks, summary);
    if (typeof showToast === "function") {
      showToast("Reset saved searches to default templates");
    }
  }

  function renderPgSavedSearchesListHtml() {
    const listEl = document.getElementById("pgSavedSearchesList");
    const footerStats = document.getElementById("pgSavedSearchesFooterStats");
    if (!listEl) return;
    const items = state.savedSearches || [];
    const activeCount = items.filter(s => s.showOnDashboard).length;
    if (footerStats) {
      footerStats.textContent = `Showing ${items.length} saved search${items.length === 1 ? "" : "es"} (${activeCount} active on dashboard)`;
    }
    const itemsWithCounts = items.map(s => {
      let matchCount = 0;
      (state.changes || []).forEach(c => {
        try {
          if (matchesSearchQuery(c, s.query)) matchCount++;
        } catch (e) {}
      });
      return { ...s, matchCount };
    });
    listEl.innerHTML = window.AemuGerritReact.renderPgSavedSearchesCardsHtml(itemsWithCounts);
  }

  function openSaveCurrentSearchDialog() {
    state.saveSearchModalOpen = true;
    const query = String(state.searchQuery || "").trim();
    const backdrop = document.getElementById("pgSaveSearchBackdrop");
    const qInp = document.getElementById("pgSaveSearchQueryInput");
    const tInp = document.getElementById("pgSaveSearchTitleInput");
    if (qInp) qInp.value = query;
    if (tInp) {
      if (!tInp.value) {
        if (query.toLowerCase().startsWith("project:")) {
          tInp.value = query.replace(/^project:/i, "").split("/").pop() || "Custom Project";
        } else if (query.toLowerCase().startsWith("owner:")) {
          tInp.value = `CLs by ${query.replace(/^owner:/i, "")}`;
        } else {
          tInp.value = query || "Custom Section";
        }
      }
    }
    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (tInp) { tInp.focus(); tInp.select(); }
    }, 50);
  }

  function closeSaveCurrentSearchDialog() {
    state.saveSearchModalOpen = false;
    const backdrop = document.getElementById("pgSaveSearchBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgSaveSearchBackdropClick(e) {
    if (e.target && e.target.id === "pgSaveSearchBackdrop") {
      closeSaveCurrentSearchDialog();
    }
  }

  function confirmSaveCurrentSearch() {
    const tInp = document.getElementById("pgSaveSearchTitleInput");
    const qInp = document.getElementById("pgSaveSearchQueryInput");
    const checkEl = document.getElementById("pgSaveSearchShowOnDash");
    const title = tInp ? tInp.value : "";
    const query = qInp ? qInp.value : "";
    const show = checkEl ? checkEl.checked : true;
    const res = addPgCustomDashboardSection(title, query, show);
    if (res) {
      closeSaveCurrentSearchDialog();
    }
  }

  function openSavedSearchesModal(activeId) {
    state.savedSearchesModalOpen = true;
    const backdrop = document.getElementById("pgSavedSearchesBackdrop");
    renderPgSavedSearchesListHtml();
    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    if (activeId) {
      setTimeout(() => {
        const card = document.getElementById(`pgSavedSearchCard-${activeId}`);
        if (card && typeof card.scrollIntoView === "function") {
          card.scrollIntoView({ block: "center" });
        }
      }, 50);
    }
  }

  function closeSavedSearchesModal() {
    state.savedSearchesModalOpen = false;
    const backdrop = document.getElementById("pgSavedSearchesBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function toggleSavedSearchesModal() {
    if (state.savedSearchesModalOpen) {
      closeSavedSearchesModal();
    } else {
      openSavedSearchesModal();
    }
  }

  function onPgSavedSearchesBackdropClick(e) {
    if (e.target && e.target.id === "pgSavedSearchesBackdrop") {
      closeSavedSearchesModal();
    }
  }

  function setQueueSearchAndApply(query) {
    state.searchQuery = String(query || "").trim();
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = state.searchQuery;
    onQueueSearchInput(state.searchQuery);
  }

  if (typeof window !== "undefined") {
    window.openSavedSearchesModal = openSavedSearchesModal;
    window.closeSavedSearchesModal = closeSavedSearchesModal;
    window.toggleSavedSearchesModal = toggleSavedSearchesModal;
    window.onPgSavedSearchesBackdropClick = onPgSavedSearchesBackdropClick;
    window.openSaveCurrentSearchDialog = openSaveCurrentSearchDialog;
    window.closeSaveCurrentSearchDialog = closeSaveCurrentSearchDialog;
    window.onPgSaveSearchBackdropClick = onPgSaveSearchBackdropClick;
    window.confirmSaveCurrentSearch = confirmSaveCurrentSearch;
    window.addPgCustomDashboardSection = addPgCustomDashboardSection;
    window.addPgCustomDashboardSectionFromInputs = addPgCustomDashboardSectionFromInputs;
    window.applyPgSearchTemplate = applyPgSearchTemplate;
    window.deletePgSavedSearch = deletePgSavedSearch;
    window.toggleSavedSearchDashboardVisibility = toggleSavedSearchDashboardVisibility;
    window.movePgSavedSearch = movePgSavedSearch;
    window.applyPgSavedSearch = applyPgSavedSearch;
    window.resetPgSavedSearchesToDefaults = resetPgSavedSearchesToDefaults;
    window.renderPgSavedSearchesListHtml = renderPgSavedSearchesListHtml;
    window.setQueueSearchAndApply = setQueueSearchAndApply;
    window.extractClNumberFromSearchToken = extractClNumberFromSearchToken;
    window.extractSearchClNumbers = extractSearchClNumbers;
    window.mergeRemoteSearchPayloadIntoState = mergeRemoteSearchPayloadIntoState;
    window.queryGerritForSearch = queryGerritForSearch;
    window.maybeTriggerRemoteSearchForMissingCl = maybeTriggerRemoteSearchForMissingCl;
  }

  function openPgSearchHelpDialog() {
    state.searchHelpDialogOpen = true;
    closePgSearchSuggestions();
    const backdrop = document.getElementById("pgSearchHelpBackdrop");
    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    renderPgSearchHelpCategoryChipsHtml();
    renderPgSearchHelpGridHtml();
    const input = document.getElementById("pgSearchHelpFilterInput");
    if (input) {
      input.value = state.searchHelpFilterQuery || "";
      setTimeout(() => {
        try {
          input.focus();
          input.select();
        } catch (e) {}
      }, 50);
    }
  }

  function closePgSearchHelpDialog() {
    state.searchHelpDialogOpen = false;
    const backdrop = document.getElementById("pgSearchHelpBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function togglePgSearchHelpDialog() {
    if (state.searchHelpDialogOpen) {
      closePgSearchHelpDialog();
    } else {
      openPgSearchHelpDialog();
    }
  }

  function onPgSearchHelpBackdropClick(e) {
    if (e && e.target && e.target.id === "pgSearchHelpBackdrop") {
      closePgSearchHelpDialog();
    }
  }

  function onPgSearchHelpFilterInput(val) {
    state.searchHelpFilterQuery = String(val || "");
    const clearBtn = document.getElementById("pgSearchHelpClearBtn");
    if (clearBtn) {
      clearBtn.style.display = state.searchHelpFilterQuery ? "inline-flex" : "none";
    }
    renderPgSearchHelpGridHtml();
  }

  function clearPgSearchHelpFilter() {
    state.searchHelpFilterQuery = "";
    const input = document.getElementById("pgSearchHelpFilterInput");
    if (input) {
      input.value = "";
      input.focus();
    }
    const clearBtn = document.getElementById("pgSearchHelpClearBtn");
    if (clearBtn) {
      clearBtn.style.display = "none";
    }
    renderPgSearchHelpGridHtml();
  }

  function selectPgSearchHelpCategory(cat) {
    state.searchHelpActiveCategory = cat || "all";
    renderPgSearchHelpCategoryChipsHtml();
    renderPgSearchHelpGridHtml();
  }

  function insertPgSearchOperator(op) {
    const safeOp = String(op || "").trim();
    if (!safeOp) return;
    let cur = (state.searchQuery || "").trim();
    if (!cur) {
      cur = safeOp;
    } else if (cur.endsWith("OR") || cur.endsWith("(")) {
      cur += " " + safeOp;
    } else {
      cur += " " + safeOp;
    }
    state.searchQuery = cur;
    const input = document.getElementById("queueSearchInput");
    if (input) input.value = cur;
    closePgSearchHelpDialog();
    if (typeof showToast === "function") {
      showToast(`Inserted "${safeOp}" into search ✓`);
    }
    if (input) {
      input.focus();
    }
    openPgSearchSuggestions();
  }

  function insertPgSearchOperatorByIdx(idx) {
    const item = (typeof PG_SEARCH_HELP_OPERATORS !== "undefined") ? PG_SEARCH_HELP_OPERATORS[idx] : null;
    if (item) insertPgSearchOperator(item.op);
  }

  function runPgSearchExample(exampleQuery) {
    const q = String(exampleQuery || "").trim();
    if (!q) return;
    state.searchQuery = q;
    state.authorFilter = "all";
    const input = document.getElementById("queueSearchInput");
    if (input) input.value = q;
    closePgSearchHelpDialog();
    if (typeof showToast === "function") {
      showToast(`Running search: ${q} ✓`);
    }
    syncSearchClearButton();
    renderPgSearchPresetsDom();
    if (typeof renderAll === "function") {
      try {
        renderAll();
      } catch (e) {}
    }
  }

  function runPgSearchExampleByIdx(idx) {
    const item = (typeof PG_SEARCH_HELP_OPERATORS !== "undefined") ? PG_SEARCH_HELP_OPERATORS[idx] : null;
    if (item) runPgSearchExample(item.example || item.op);
  }

  function copyPgSearchOperator(text) {
    const s = String(text || "").trim();
    if (!s) return;
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(s).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(`Copied "${s}" to clipboard ✓`);
    }
  }

  function copyPgSearchOperatorByIdx(idx) {
    const item = (typeof PG_SEARCH_HELP_OPERATORS !== "undefined") ? PG_SEARCH_HELP_OPERATORS[idx] : null;
    if (item) copyPgSearchOperator(item.op);
  }

  function renderPgSearchHelpCategoryChipsHtml() {
    const el = document.getElementById("pgSearchHelpCategoryChips");
    if (!el) return;
    const active = (state.searchHelpActiveCategory || "all").toLowerCase();
    const categories = ["all", "Status", "People", "Votes", "Comments", "Git", "Commits", "Syntax"];
    const counts = { all: (typeof PG_SEARCH_HELP_OPERATORS !== "undefined") ? PG_SEARCH_HELP_OPERATORS.length : 0 };
    if (typeof PG_SEARCH_HELP_OPERATORS !== "undefined") {
      PG_SEARCH_HELP_OPERATORS.forEach(item => {
        counts[item.cat] = (counts[item.cat] || 0) + 1;
      });
    }

    el.innerHTML = categories.map(cat => {
      const isSel = active === cat.toLowerCase();
      const label = cat === "all" ? "All" : cat;
      const count = counts[cat] || 0;
      return `<button
        type="button"
        class="pg-search-help-chip ${isSel ? "is-active" : ""}"
        onclick="selectPgSearchHelpCategory('${escapeHtml(cat)}')"
        aria-pressed="${isSel ? "true" : "false"}"
      >
        <span>${escapeHtml(label)}</span>
        <span class="pg-search-help-chip-count">${count}</span>
      </button>`;
    }).join("");
  }

  function renderPgSearchHelpGridHtml() {
    const grid = document.getElementById("pgSearchHelpGrid");
    if (!grid) return;
    const q = (state.searchHelpFilterQuery || "").trim().toLowerCase();
    const cat = (state.searchHelpActiveCategory || "all").toLowerCase();
    const allOps = (typeof PG_SEARCH_HELP_OPERATORS !== "undefined") ? PG_SEARCH_HELP_OPERATORS : [];

    const filtered = allOps.map((item, idx) => ({ ...item, originalIdx: idx })).filter(item => {
      if (cat !== "all" && item.cat.toLowerCase() !== cat) return false;
      if (!q) return true;
      const haystack = [item.op, item.alt || "", item.desc, item.example || "", item.cat].join(" ").toLowerCase();
      return haystack.includes(q);
    });

    if (!filtered.length) {
      grid.innerHTML = `<div class="pg-search-help-empty">
        <div style="font-size: 24px; margin-bottom: 8px;">🔍</div>
        <div>No search operators matching "<strong>${escapeHtml(state.searchHelpFilterQuery)}</strong>"</div>
        <button type="button" class="btn pg-btn-flat" style="margin-top: 10px;" onclick="clearPgSearchHelpFilter()">Clear Filter</button>
      </div>`;
      return;
    }

    grid.innerHTML = filtered.map(item => {
      const altHtml = item.alt ? `<span class="pg-op-alt" title="Alternative syntax">or ${escapeHtml(item.alt)}</span>` : "";
      const exSafe = escapeHtml(item.example || item.op);
      const opSafe = escapeHtml(item.op);
      const idx = item.originalIdx;
      return `<div class="pg-search-operator-card" data-cat="${escapeHtml(item.cat)}">
        <div class="pg-op-card-header">
          <div class="pg-op-syntax-group">
            <span class="pg-op-icon" aria-hidden="true">${item.icon}</span>
            <code class="pg-op-syntax">${opSafe}</code>
            ${altHtml}
          </div>
          <span class="pg-op-cat">${escapeHtml(item.cat)}</span>
        </div>
        <div class="pg-op-desc">${escapeHtml(item.desc)}</div>
        <div class="pg-op-example-wrap" title="Example query">
          <span class="pg-op-example-label">Ex:</span>
          <code class="pg-op-example-code">${exSafe}</code>
        </div>
        <div class="pg-op-card-actions">
          <button type="button" class="pg-btn-op-action" onclick="copyPgSearchOperatorByIdx(${idx})" title="Copy operator syntax">📋 Copy</button>
          <button type="button" class="pg-btn-op-action" onclick="insertPgSearchOperatorByIdx(${idx})" title="Insert operator into search bar">➕ Insert</button>
          <button type="button" class="pg-btn-op-action pg-btn-op-run" onclick="runPgSearchExampleByIdx(${idx})" title="Run example query immediately">▶ Run</button>
        </div>
      </div>`;
    }).join("");
  }

  function buildPgOwnerParticipant(c) {
    const ownerEmail = String(c.owner_email || "").trim();
    const attnSet = Array.isArray(c.attention_set_emails) ? c.attention_set_emails : [];
    const inAttn = Boolean(
      ownerEmail && attnSet.some(em => String(em || "").toLowerCase() === ownerEmail.toLowerCase())
    );
    return {
      email: ownerEmail,
      short: c.owner_short || ownerEmail.split("@")[0] || "owner",
      name: c.owner_name || c.owner_short || ownerEmail.split("@")[0] || "owner",
      full_name: c.owner_name || c.owner_short || ownerEmail,
      state: "OWNER",
      is_bot: Boolean(c.owner_is_bot),
      is_owner: true,
      is_viewer: Boolean(ownerEmail && ownerEmail.toLowerCase() === (state.viewer || "").toLowerCase()),
      in_attention_set: inAttn,
      removable: false,
      cr_vote: 0,
      votes: [],
    };
  }

  function findLatestAttentionTransition(c, userKey) {
    if (!c) return null;
    const history = (Array.isArray(c.attention_set_history) && c.attention_set_history.length > 0)
      ? c.attention_set_history
      : ((typeof getPgAttentionSetHistory === "function") ? getPgAttentionSetHistory(c, true) : []);
    if (!Array.isArray(history) || history.length === 0) return null;
    const uLower = String(userKey || "").trim().toLowerCase();
    if (!uLower) return null;
    const shortKey = uLower.includes("@") ? uLower.split("@")[0] : uLower;
    // Look for the most recent transition (preferring "add" operation) matching user
    const addMatch = history.find(h => {
      if (h.operation && h.operation !== "add") return false;
      const hEmail = String(h.email || "").toLowerCase();
      const hName = String(h.name || "").toLowerCase();
      return (
        (hEmail && (hEmail === uLower || hEmail.split("@")[0] === shortKey)) ||
        (hName && (hName === uLower || hName === shortKey || hName.toLowerCase().includes(shortKey)))
      );
    });
    if (addMatch) return addMatch;
    // Fallback to any transition for this user
    return history.find(h => {
      const hEmail = String(h.email || "").toLowerCase();
      const hName = String(h.name || "").toLowerCase();
      return (
        (hEmail && (hEmail === uLower || hEmail.split("@")[0] === shortKey)) ||
        (hName && (hName === uLower || hName === shortKey))
      );
    }) || null;
  }

  function getPgAttentionSetParticipants(c) {
    if (!c) return [];
    if (Array.isArray(c.attention_set_details) && c.attention_set_details.length > 0) {
      return c.attention_set_details.map(d => {
        const userKey = d.email || d.short || d.name || String(d.account_id || "");
        const hist = findLatestAttentionTransition(c, userKey);
        const resolvedReason = (d.reason && d.reason !== "Attention requested")
          ? d.reason
          : ((hist && hist.reason) ? hist.reason : (d.reason || "Attention requested"));
        const resolvedActor = d.actor_name || (hist && hist.actor_name ? hist.actor_name : "");
        const resolvedActorEmail = d.actor_email || (hist && hist.actor_email ? hist.actor_email : "");
        return Object.assign({}, d, {
          reason: resolvedReason,
          actor_name: resolvedActor,
          actor_email: resolvedActorEmail,
        });
      });
    }
    const emails = Array.isArray(c.attention_set_emails) ? c.attention_set_emails : [];
    const revs = typeof getFallbackReviewersForCl === "function" ? getFallbackReviewersForCl(c) : [];
    const viewerEmail = String(state.viewer || "").trim().toLowerCase();
    const result = [];
    const seen = new Set();
    emails.forEach(em => {
      const emLower = String(em || "").trim().toLowerCase();
      if (!emLower || seen.has(emLower)) return;
      seen.add(emLower);
      const matched = revs.find(r => String(r.email || "").toLowerCase() === emLower) || {};
      const short = matched.short || emLower.split("@")[0];
      const name = matched.name || matched.full_name || short;
      const isViewer = Boolean(viewerEmail && (emLower === viewerEmail || short.toLowerCase() === viewerEmail.split("@")[0]));
      const hist = findLatestAttentionTransition(c, emLower);
      const reason = hist && hist.reason ? hist.reason : (matched.is_owner ? "Change owner" : "Reviewer");
      const actorName = hist && hist.actor_name ? hist.actor_name : "";
      const actorEmail = hist && hist.actor_email ? hist.actor_email : "";
      result.push({
        account_id: matched._account_id || 0,
        email: emLower,
        name: name,
        short: short,
        reason: reason,
        actor_name: actorName,
        actor_email: actorEmail,
        waiting_display: isViewer && c.attention_waiting_display ? c.attention_waiting_display : "",
        is_viewer: isViewer,
      });
    });
    revs.filter(p => p.in_attention_set).forEach(p => {
      const emLower = String(p.email || "").trim().toLowerCase();
      const pShort = String(p.short || emLower.split("@")[0] || "");
      const key = emLower || pShort;
      if (!key || seen.has(key)) return;
      seen.add(key);
      const isViewer = Boolean(viewerEmail && (emLower === viewerEmail || (pShort && pShort.toLowerCase() === viewerEmail.split("@")[0])));
      const hist = findLatestAttentionTransition(c, emLower || pShort);
      const reason = hist && hist.reason ? hist.reason : (p.is_owner ? "Change owner" : "Reviewer");
      const actorName = hist && hist.actor_name ? hist.actor_name : "";
      const actorEmail = hist && hist.actor_email ? hist.actor_email : "";
      result.push({
        account_id: p._account_id || 0,
        email: p.email || "",
        name: p.name || pShort,
        short: pShort,
        reason: reason,
        actor_name: actorName,
        actor_email: actorEmail,
        waiting_display: isViewer && c.attention_waiting_display ? c.attention_waiting_display : "",
        is_viewer: isViewer,
      });
    });
    result.sort((a, b) => (Number(Boolean(b.is_viewer)) - Number(Boolean(a.is_viewer))) || a.short.localeCompare(b.short));
    return result;
  }

  function getPgAttentionEligibleCandidates(c) {
    if (!c) return [];
    const inAttn = new Set(
      getPgAttentionSetParticipants(c).map(p =>
        String(p.email || p.short || p.account_id || "").toLowerCase()
      )
    );
    const seen = new Set();
    const candidates = [];

    const revs = typeof getFallbackReviewersForCl === "function" ? getFallbackReviewersForCl(c) : [];
    revs.forEach(r => {
      const em = String(r.email || "").trim().toLowerCase();
      const short = String(r.short || (em ? em.split("@")[0] : "")).trim().toLowerCase();
      const key = em || short || String(r._account_id || "");
      if (!key || seen.has(key) || inAttn.has(key) || (em && inAttn.has(em)) || (short && inAttn.has(short))) return;
      seen.add(key);
      candidates.push({
        userKey: r.email || r.short || String(r._account_id || ""),
        email: r.email || "",
        short: r.short || (em ? em.split("@")[0] : ""),
        name: r.name || r.short || em,
        role: r.is_owner ? "Owner" : (r.is_reviewer ? "Reviewer" : "CC"),
      });
    });

    const ccs = Array.isArray(c.ccs_detail) ? c.ccs_detail : (Array.isArray(c.ccs) ? c.ccs : []);
    ccs.forEach(cc => {
      const em = String(cc.email || "").trim().toLowerCase();
      const short = String(cc.short || (em ? em.split("@")[0] : "")).trim().toLowerCase();
      const key = em || short || String(cc._account_id || "");
      if (!key || seen.has(key) || inAttn.has(key) || (em && inAttn.has(em)) || (short && inAttn.has(short))) return;
      seen.add(key);
      candidates.push({
        userKey: cc.email || cc.short || String(cc._account_id || ""),
        email: cc.email || "",
        short: cc.short || (em ? em.split("@")[0] : ""),
        name: cc.name || cc.short || em,
        role: "CC",
      });
    });

    if (c.owner) {
      const ownerEmail = String(c.owner.email || "").trim().toLowerCase();
      const ownerName = String(c.owner.name || c.owner_name || "").trim();
      const ownerShort = String(c.owner.username || (ownerEmail ? ownerEmail.split("@")[0] : ownerName)).trim();
      const key = ownerEmail || ownerShort.toLowerCase();
      if (key && !seen.has(key) && !inAttn.has(key) && (!ownerEmail || !inAttn.has(ownerEmail))) {
        seen.add(key);
        candidates.unshift({
          userKey: ownerEmail || ownerShort,
          email: ownerEmail,
          short: ownerShort,
          name: ownerName || ownerShort,
          role: "Owner",
        });
      }
    }

    return candidates;
  }

  function renderPgAddAttentionPopoverContentHtml(c) {
    if (!c) return "";
    const clNum = Number(c.number);
    const candidates = getPgAttentionEligibleCandidates(c);
    const candidatesHtml = candidates.length > 0
      ? `<div class="pg-add-attn-candidates">
          <span class="pg-add-attn-candidates-label">Collaborators on CL:</span>
          <div class="pg-add-attn-candidates-wrap">
            ${candidates.map(cand => {
              const avatarBg = computePgAvatarColor(cand.email || cand.name);
              const initial = computePgInitial(cand);
              return `<button type="button" class="pg-add-attn-candidate-chip" onclick="selectPgAddAttentionCandidate(${clNum}, '${escapeHtml(cand.userKey)}', '${escapeHtml(cand.name)}')">
                <span class="pg-attention-avatar" style="background:${avatarBg};">${escapeHtml(initial)}</span>
                <span class="pg-add-attn-name">${escapeHtml(cand.name || cand.short)}</span>
                <span class="pg-add-attn-role">(${cand.role})</span>
              </button>`;
            }).join("")}
          </div>
        </div>`
      : "";

    return `<div class="pg-add-attn-head">
      <span class="pg-add-attn-title">⚡ Add to Attention Set</span>
      <button type="button" class="pg-add-attn-close" onclick="closePgAddAttentionPopover(${clNum})" title="Close">×</button>
    </div>
    ${candidatesHtml}
    <div class="pg-add-attn-inputs">
      <input
        type="text"
        id="pgAddAttentionUserInput-${clNum}"
        class="pg-add-attn-input"
        placeholder="User email or LDAP (e.g. jdoe@google.com)"
        onkeydown="if(event.key==='Enter'){event.preventDefault();submitPgAddAttentionForm(${clNum});}"
      />
      <input
        type="text"
        id="pgAddAttentionReasonInput-${clNum}"
        class="pg-add-attn-reason-input"
        placeholder="Reason (optional, e.g. Please review patchset 2)"
        onkeydown="if(event.key==='Enter'){event.preventDefault();submitPgAddAttentionForm(${clNum});}"
      />
    </div>
    <div class="pg-add-attn-actions">
      <button type="button" class="pg-btn-cancel-add-attn" onclick="closePgAddAttentionPopover(${clNum})">Cancel</button>
      <button type="button" id="btnConfirmAddAttention-${clNum}" class="pg-btn-confirm-add-attn" onclick="submitPgAddAttentionForm(${clNum})">✓ Add to Attention Set</button>
    </div>`;
  }

  function togglePgAddAttentionPopover(clNum, btnEl) {
    const num = Number(clNum);
    const pop = document.getElementById(`pgAddAttentionPopover-${num}`);
    if (!pop) return;
    const isHidden = pop.style.display === "none" || !pop.style.display;
    closeAllPgAddAttentionPopovers();
    if (isHidden) {
      pop.style.display = "block";
      state.addAttentionPopoverOpen = true;
      const inp = document.getElementById(`pgAddAttentionUserInput-${num}`);
      if (inp) {
        setTimeout(() => inp.focus(), 60);
      }
    } else {
      pop.style.display = "none";
      state.addAttentionPopoverOpen = false;
    }
  }

  function closePgAddAttentionPopover(clNum) {
    const pop = document.getElementById(`pgAddAttentionPopover-${Number(clNum)}`);
    if (pop) pop.style.display = "none";
    state.addAttentionPopoverOpen = false;
  }

  function closeAllPgAddAttentionPopovers() {
    if (typeof document !== "undefined") {
      document.querySelectorAll(".pg-add-attention-popover").forEach(p => {
        p.style.display = "none";
      });
    }
    state.addAttentionPopoverOpen = false;
  }

  function selectPgAddAttentionCandidate(clNum, userKey, displayName) {
    const num = Number(clNum);
    const userInp = document.getElementById(`pgAddAttentionUserInput-${num}`);
    if (userInp) {
      userInp.value = userKey;
    }
    const reasonInp = document.getElementById(`pgAddAttentionReasonInput-${num}`);
    if (reasonInp) {
      reasonInp.focus();
    }
  }

  async function submitPgAddAttentionForm(clNum) {
    const num = Number(clNum);
    const userInp = document.getElementById(`pgAddAttentionUserInput-${num}`);
    const userKey = userInp ? userInp.value.trim() : "";
    if (!userKey) {
      showToast("Please enter a user email or LDAP to add to the attention set.");
      if (userInp) userInp.focus();
      return;
    }
    const reasonInp = document.getElementById(`pgAddAttentionReasonInput-${num}`);
    const reason = reasonInp ? reasonInp.value.trim() : "";
    const confirmBtn = document.getElementById(`btnConfirmAddAttention-${num}`);
    closePgAddAttentionPopover(num);
    await addPgAttentionUser(num, userKey, userKey, reason, confirmBtn);
  }

  let pgAttnHovercardHideTimer = null;

  function ensurePgAttentionHovercardEl() {
    let el = document.getElementById("pgAttentionHovercard");
    if (!el && typeof document !== "undefined" && document.body) {
      el = document.createElement("div");
      el.id = "pgAttentionHovercard";
      el.className = "pg-attention-hovercard";
      el.style.display = "none";
      el.addEventListener("mouseenter", cancelHidePgAttentionHovercard);
      el.addEventListener("mouseleave", scheduleHidePgAttentionHovercard);
      document.body.appendChild(el);
    }
    return el;
  }

  function positionPgAttentionHovercard(anchorEl, cardEl) {
    if (!anchorEl || !cardEl || typeof window === "undefined") return;
    if (typeof anchorEl.getBoundingClientRect !== "function") return;
    const rect = anchorEl.getBoundingClientRect();
    const cardWidth = cardEl.offsetWidth || 320;
    const cardHeight = cardEl.offsetHeight || 160;
    const winWidth = window.innerWidth || 1024;
    const winHeight = window.innerHeight || 768;
    let left = rect.left;
    if (left + cardWidth > winWidth - 12) {
      left = Math.max(12, winWidth - cardWidth - 12);
    }
    let top = rect.bottom + 6;
    if (top + cardHeight > winHeight - 12) {
      top = Math.max(12, rect.top - cardHeight - 6);
    }
    cardEl.style.top = `${Math.round(top)}px`;
    cardEl.style.left = `${Math.round(left)}px`;
  }

  function showPgAttentionHovercard(event, clNum, userKey) {
    if (typeof isPgActivelyScrolling === "function" && isPgActivelyScrolling(event)) return;
    cancelHidePgAttentionHovercard();
    const hovercard = ensurePgAttentionHovercardEl();
    if (!hovercard) return;
    const num = Number(clNum || state.selectedClNumber);
    const c = (state.changes || []).find(x => Number(x.number) === num);
    if (!c) return;
    const participants = getPgAttentionSetParticipants(c);
    const uLower = String(userKey || "").toLowerCase();
    const isTargetViewer = uLower === "viewer" || uLower === "myself" ||
      Boolean(state.viewer && (uLower === state.viewer.toLowerCase() || uLower === state.viewer.split("@")[0].toLowerCase()));

    let p = participants.find(
      x => String(x.email || "").toLowerCase() === uLower ||
           String(x.short || "").toLowerCase() === uLower ||
           String(x.account_id || "") === uLower ||
           (isTargetViewer && x.is_viewer)
    );

    if (!p) {
      if (isTargetViewer) {
        const viewerEmail = String(state.viewer || "").trim().toLowerCase();
        const short = viewerEmail ? viewerEmail.split("@")[0] : "You";
        const hist = findLatestAttentionTransition(c, viewerEmail || short);
        p = {
          name: short,
          short: short,
          email: viewerEmail,
          reason: hist && hist.reason ? hist.reason : "Your review is requested",
          actor_name: hist && hist.actor_name ? hist.actor_name : "",
          actor_email: hist && hist.actor_email ? hist.actor_email : "",
          waiting_display: c.attention_waiting_display || "",
          is_viewer: true,
        };
      } else {
        const hist = findLatestAttentionTransition(c, userKey);
        p = {
          name: userKey,
          short: userKey.includes("@") ? userKey.split("@")[0] : userKey,
          email: userKey.includes("@") ? userKey : "",
          reason: hist && hist.reason ? hist.reason : "Attention requested",
          actor_name: hist && hist.actor_name ? hist.actor_name : "",
          actor_email: hist && hist.actor_email ? hist.actor_email : "",
          waiting_display: "",
          is_viewer: false,
        };
      }
    }

    const disp = p.name || p.short || p.email || userKey;
    const email = p.email || (userKey.includes("@") ? userKey : "");
    const initial = computePgInitial(p);
    const avatarBg = computePgAvatarColor(email || disp);
    const waitStr = p.waiting_display ? `Waiting ${p.waiting_display}` : (p.is_viewer && c.attention_waiting_display ? `Waiting ${c.attention_waiting_display}` : "");
    const reasonText = p.reason || "Attention requested on change";
    const lastUpdateStr = p.last_update ? (formatPgRelativeDate(p.last_update) || p.last_update) : "";
    const actorDisplay = p.actor_name || (p.actor_email ? p.actor_email.split("@")[0] : "");

    const isUrgent = Boolean(
      (p.waiting_display && /[dw]/.test(p.waiting_display)) ||
      (p.is_viewer && c.attention_waiting_display && /[dw]/.test(c.attention_waiting_display)) ||
      Number(c.unresolved_comments || 0) > 0
    );
    const statusText = p.is_viewer
      ? (isUrgent ? "▸ Your Turn (Urgent)" : "▸ Your Turn")
      : "⚡ In Attention Set";

    hovercard.innerHTML = `<div class="pg-attn-hover-card-inner">
      <div class="pg-attn-hover-head">
        <span class="pg-attention-avatar lg" style="background:${avatarBg};">${escapeHtml(initial)}</span>
        <div class="pg-attn-hover-user-info">
          <div class="pg-attn-hover-name">${escapeHtml(disp)}${p.is_viewer ? ' <span class="pg-attn-you-pill">You</span>' : ''}</div>
          <div class="pg-attn-hover-email-row">
            <span class="pg-attn-hover-email">${escapeHtml(email || disp)}</span>
            ${email ? `<button type="button" class="pg-btn-copy-email" onclick="copyPgHovercardEmail('${escapeHtml(email)}')" title="Copy email">📋</button>` : ""}
          </div>
        </div>
      </div>
      <div class="pg-attn-hover-status-row">
        <span class="pg-attn-status-badge ${p.is_viewer ? 'is-turn' : ''} ${isUrgent ? 'is-urgent' : ''}">${statusText}</span>
        ${waitStr ? `<span class="pg-attn-wait-badge" title="Waiting on turn">⏳ ${escapeHtml(waitStr)}</span>` : ""}
        ${actorDisplay ? `<span class="pg-attn-actor-badge" title="Added by ${escapeHtml(actorDisplay)}">👤 Added by ${escapeHtml(actorDisplay)}</span>` : ""}
      </div>
      <div class="pg-attn-hover-reason-box">
        <div class="pg-attn-reason-label">REASON FOR ATTENTION</div>
        <div class="pg-attn-reason-text">${escapeHtml(reasonText)}</div>
        ${lastUpdateStr ? `<div class="pg-attn-update-time">Updated: ${escapeHtml(lastUpdateStr)}</div>` : ""}
      </div>
      <div class="pg-attn-hover-actions">
        <button type="button" class="pg-btn-card-attn-history" onclick="openPgAttentionHistoryDialog(${num}, '${escapeHtml(userKey)}')">📜 Turn History</button>
        <button type="button" class="pg-btn-card-remove-attn" onclick="onPgCardRemoveAttentionClick(${num}, '${escapeHtml(userKey)}', '${escapeHtml(disp)}')">✕ Remove from Attention Set</button>
        <button type="button" class="pg-btn-card-reply-attn" onclick="onPgCardReplyAttentionClick(${num}, '${escapeHtml(p.short || disp)}')">💬 Reply</button>
      </div>
    </div>`;

    hovercard.style.display = "block";
    const anchor = (event && (event.currentTarget || event.target)) ||
      document.querySelector(`[data-cl="${num}"][data-user="${userKey}"]`) ||
      document.querySelector(`[data-cl-num="${num}"] .pg-turn-badge`) ||
      document.querySelector(`[data-cl-num="${num}"]`);
    if (anchor) {
      positionPgAttentionHovercard(anchor, hovercard);
    }
  }

  function hidePgAttentionHovercard() {
    const hovercard = document.getElementById("pgAttentionHovercard");
    if (hovercard) hovercard.style.display = "none";
  }

  function togglePgAttentionHovercard(event, clNum, userKey) {
    const hovercard = document.getElementById("pgAttentionHovercard");
    if (hovercard && hovercard.style.display === "block") {
      hidePgAttentionHovercard();
    } else {
      showPgAttentionHovercard(event, clNum, userKey);
    }
  }

  function scheduleHidePgAttentionHovercard() {
    clearTimeout(pgAttnHovercardHideTimer);
    pgAttnHovercardHideTimer = setTimeout(() => {
      hidePgAttentionHovercard();
    }, 220);
  }

  function cancelHidePgAttentionHovercard() {
    clearTimeout(pgAttnHovercardHideTimer);
  }

  async function copyPgHovercardEmail(email) {
    const text = String(email || "").trim();
    if (!text) return;
    showToast(`✓ Copied ${text} to clipboard!`);
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      }
    } catch (_) {}
  }

  function onPgCardRemoveAttentionClick(clNum, userKey, displayLabel) {
    hidePgAttentionHovercard();
    removePgAttentionUser(clNum, userKey, displayLabel);
  }

  function onPgCardReplyAttentionClick(clNum, shortName) {
    hidePgAttentionHovercard();
    const num = Number(clNum);
    if (num && Number(state.selectedClNumber) !== num && typeof selectCommit === "function") {
      selectCommit(num);
    }
    const replyFn = (typeof window !== "undefined" && typeof window.openReplyDialog === "function")
      ? window.openReplyDialog
      : (typeof openReplyDialog === "function" ? openReplyDialog : null);
    if (replyFn) replyFn({ focusComment: true });
    setTimeout(() => {
      const commentInp = document.getElementById("pgReplyCommentInput");
      if (commentInp && shortName) {
        const mention = `@${shortName} `;
        if (!commentInp.value.includes(mention)) {
          commentInp.value = mention + commentInp.value;
        }
        commentInp.focus();
      }
    }, 120);
  }

  // --- Bug Hovercard Overlay (PolyGerrit gr-buganizer-hovercard) ---
  let pgBugHovercardHideTimer = null;
  let pgActiveBugHovercardId = "";
  let pgActiveBugHovercardAnchor = null;
  const pgBugDetailsCache = new Map();
  const pgBugDetailsInflight = new Map();

  const PG_HUCKLE_ICONS = {
    folderOpen: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"/></svg>',
    accessTime: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg>',
    infoOutline: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M11 17h2v-6h-2v6zm1-15C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zM11 9h2V7h-2v2z"/></svg>',
    solidCircleSmall: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="1.5"/></svg>',
    link: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/></svg>',
    contentCopy: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
  };

  const PG_KNOWN_BUG_COMPONENTS = {
    "29601": "Android > Android OS & Apps > Android Studio > Emulator",
    "111786": "Android > Android OS & Apps > Android Studio > Emulator > Emulator System Images",
    "137632": "SwiftShader > Android Emulator",
    "192708": "Android Public Tracker > App Development > Android Studio",
    "192727": "Android Public Tracker > App Development > Android Studio > Emulator",
    "816693": "Android > Android OS & Apps > Android Studio > Emulator > Embedded Emulator",
    "1016880": "Android > Non-OS Projects > Enterprise > Bedstead",
    "2289192": "Android > Android OS & Apps > Android Studio > Emulator > Android Device Hub",
  };

  function _escapeBugJsSingleQuote(val) {
    return String(val || "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  }

  function normalizePgBugId(rawBugId) {
    return String(rawBugId || "").trim().replace(/^b\//i, "").replace(/\D+/g, "");
  }

  function formatBugStatusOrType(str) {
    const raw = String(str || "").trim();
    if (!raw) return "";
    return raw
      .toLowerCase()
      .split("_")
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  }

  function removeGoogleDomain(account) {
    const raw = String(account || "").trim();
    if (!raw || raw.toLowerCase() === "none") return "Unassigned";
    return raw.replace("@google.com", "");
  }

  function resolvePgBugComponentFullName(bugData) {
    if (!bugData || typeof bugData !== "object") return "Android > Android OS & Apps > Android Studio > Emulator";
    const explicitFull = String(bugData.component_full_name || "").trim();
    if (explicitFull) return explicitFull;
    const compId = String(bugData.component_id || "").trim();
    if (compId && PG_KNOWN_BUG_COMPONENTS[compId]) {
      return PG_KNOWN_BUG_COMPONENTS[compId];
    }
    if (compId) return `Component #${compId}`;
    return "Android > Android OS & Apps > Android Studio > Emulator";
  }

  function getPgBugStatusClass(status) {
    const up = String(status || "").trim().toUpperCase();
    if (!up) return "";
    if (["FIXED", "VERIFIED", "CLOSED"].includes(up)) return "status-fixed";
    if (["WONT_FIX", "OBSOLETE", "INFEASIBLE", "INTENDED_BEHAVIOR", "DUPLICATE", "NOT_REPRODUCIBLE"].some(k => up.includes(k))) {
      return "status-closed";
    }
    if (["NEW", "ASSIGNED", "ACCEPTED", "OPEN", "REOPENED", "IN_PROGRESS"].includes(up)) {
      return "status-open";
    }
    return "";
  }

  function formatPgBugTimestampShort(rawTs) {
    const s = String(rawTs || "").trim();
    if (!s) return "";
    const m = s.match(/^(\d{4}-\d{2}-\d{2})(?:[T\s]+(\d{2}:\d{2}))?/);
    if (m) return m[2] ? `${m[1]} ${m[2]}` : m[1];
    return s;
  }

  function _mergeBugFieldsInto(target, src) {
    if (!src || typeof src !== "object") return;
    const fields = [
      "title", "priority", "severity", "status", "assignee", "reporter",
      "verifier", "component_id", "component_full_name", "type", "created_time",
      "modified_time", "description", "url",
    ];
    for (const k of fields) {
      if (src[k]) target[k] = String(src[k]);
    }
    if (Array.isArray(src.cc) && src.cc.length > 0) target.cc = src.cc.slice();
    if (Array.isArray(src.gerrit_changes) && src.gerrit_changes.length > 0) {
      target.gerrit_changes = src.gerrit_changes.slice();
    }
    if (src.details_loaded) target.details_loaded = true;
  }

  function resolvePgBugMetadata(bugId) {
    const cleanId = normalizePgBugId(bugId);
    const result = {
      id: cleanId,
      url: `https://b.corp.google.com/issues/${cleanId}`,
      title: "",
      priority: "",
      severity: "",
      status: "",
      assignee: "",
      reporter: "",
      verifier: "",
      component_id: "",
      component_full_name: "",
      type: "",
      created_time: "",
      modified_time: "",
      description: "",
      cc: [],
      gerrit_changes: [],
      linkedCls: [],
      details_loaded: false,
    };
    if (!cleanId) return result;
    const allChanges = Array.isArray(state.changes) ? state.changes : [];
    const seenCls = new Set();
    for (const cl of allChanges) {
      const bugs = Array.isArray(cl && cl.bugs) ? cl.bugs : [];
      for (const b of bugs) {
        const bid = normalizePgBugId(b && typeof b === "object" ? b.id : b);
        if (bid !== cleanId) continue;
        if (b && typeof b === "object") _mergeBugFieldsInto(result, b);
        const clNum = Number(cl.number || cl._number || 0);
        if (clNum && !seenCls.has(clNum)) {
          seenCls.add(clNum);
          result.linkedCls.push({
            number: clNum,
            subject: String(cl.subject || ""),
            status: String(cl.status || "NEW"),
            owner: String(cl.owner_short || cl.owner_name || ""),
            url: String(cl.url || `https://android-review.googlesource.com/c/${clNum}`),
          });
        }
      }
    }
    if (pgBugDetailsCache.has(cleanId)) {
      _mergeBugFieldsInto(result, pgBugDetailsCache.get(cleanId));
    }
    return result;
  }

  function renderPgBugHovercardContentHtml(bugData, isLoading = false) {
    const cleanId = normalizePgBugId(bugData && bugData.id);
    const bugUrl = (bugData && bugData.url) || `https://b.corp.google.com/issues/${cleanId}`;
    const gerritOrigin = "https://googleplex-android-review.git.corp.google.com";
    const relatedClsUrl = `${gerritOrigin}/q/Bug:${encodeURIComponent(cleanId)}`;
    const rawTitle = String((bugData && bugData.title) || "").trim();
    const titleDisplay = rawTitle || (isLoading ? `Loading b/${cleanId}…` : `b/${cleanId}`);
    const componentFullName = resolvePgBugComponentFullName(bugData);
    const statusText = formatBugStatusOrType((bugData && bugData.status) || (isLoading ? "Loading" : "Assigned"));
    const assigneeText = removeGoogleDomain(bugData && bugData.assignee);
    const typeText = formatBugStatusOrType((bugData && bugData.type) || "BUG");
    const prioText = String((bugData && bugData.priority) || "P2").trim().toUpperCase();
    const sevText = String((bugData && bugData.severity) || "S2").trim().toUpperCase();

    return `
      <div class="hovercard-content">
        <div class="hovercard-item-title">
          <strong>${escapeHtml(titleDisplay)}</strong>
        </div>
        <div class="hovercard-item-component">
          <span class="hovercard-front-icon">${PG_HUCKLE_ICONS.folderOpen}</span>
          <span>${escapeHtml(componentFullName)}</span>
        </div>
        <div class="hovercard-item-status">
          <span class="hovercard-front-icon">${PG_HUCKLE_ICONS.accessTime}</span>
          <span>${escapeHtml(statusText)}</span>
        </div>
        <div class="hovercard-item-assignee">
          <span class="hovercard-front-icon">${PG_HUCKLE_ICONS.infoOutline}</span>
          <strong>Assignee</strong>&nbsp;<span>${escapeHtml(assigneeText)}</span>
        </div>
        <div class="hovercard-item-type">
          <span class="hovercard-front-icon" style="visibility: hidden;">${PG_HUCKLE_ICONS.infoOutline}</span>
          <strong>Type</strong>&nbsp;<span>${escapeHtml(typeText)}</span>
          <span class="hovercard-dot-icon">${PG_HUCKLE_ICONS.solidCircleSmall}</span>
          <span>${escapeHtml(prioText)}</span>
          <span class="hovercard-dot-icon">${PG_HUCKLE_ICONS.solidCircleSmall}</span>
          <span>${escapeHtml(sevText)}</span>
        </div>
        <div class="hovercard-item-links">
          <span class="hovercard-front-icon">${PG_HUCKLE_ICONS.link}</span>
          <a target="_blank" rel="noopener noreferrer" href="${escapeHtml(bugUrl)}">Buganizer</a>
          <span class="hovercard-dot-icon">${PG_HUCKLE_ICONS.solidCircleSmall}</span>
          <a target="_blank" rel="noopener noreferrer" href="${escapeHtml(relatedClsUrl)}" onclick="onPgBugRelatedClsClick(event, '${_escapeBugJsSingleQuote(cleanId)}')">Related CLs</a>
        </div>
        <div>
          <button type="button" class="hovercard-item-copy-content" onclick="copyPgBugHovercardId('${_escapeBugJsSingleQuote(cleanId)}', this)">
            <span class="hovercard-copy-icon">${PG_HUCKLE_ICONS.contentCopy}</span>
            <span>b/${escapeHtml(cleanId)}</span>
          </button>
        </div>
      </div>
    `;
  }

  function ensurePgBugHovercardEl() {
    let hovercard = document.getElementById("pgBugHovercard");
    if (!hovercard && typeof document.createElement === "function") {
      hovercard = document.createElement("div");
      hovercard.id = "pgBugHovercard";
      hovercard.className = "pg-bug-hovercard";
      hovercard.style.display = "none";
      hovercard.setAttribute("onmouseenter", "cancelHidePgBugHovercard()");
      hovercard.setAttribute("onmouseleave", "scheduleHidePgBugHovercard()");
      if (document.body && typeof document.body.appendChild === "function") {
        document.body.appendChild(hovercard);
      }
    }
    return hovercard;
  }

  function positionPgBugHovercard(anchorEl, cardEl) {
    if (!anchorEl || !cardEl || typeof anchorEl.getBoundingClientRect !== "function") return;
    const rect = anchorEl.getBoundingClientRect();
    const vw = window.innerWidth || 1280;
    const vh = window.innerHeight || 800;
    const cardWidth = (typeof cardEl.offsetWidth === "number" && cardEl.offsetWidth > 40) ? cardEl.offsetWidth : 342;
    const cardHeight = (typeof cardEl.offsetHeight === "number" && cardEl.offsetHeight > 40) ? cardEl.offsetHeight : 220;
    let left = rect.right + 8;
    if (left + cardWidth + 12 > vw) {
      if (rect.left - cardWidth - 8 >= 12) {
        left = rect.left - cardWidth - 8;
      } else {
        left = Math.max(12, vw - cardWidth - 12);
      }
    }
    let top = rect.top + (rect.height / 2) - (cardHeight / 2);
    top = Math.max(12, Math.min(vh - cardHeight - 12, top));
    cardEl.style.left = `${Math.round(left)}px`;
    cardEl.style.top = `${Math.round(top)}px`;
  }

  async function fetchPgBugDetailsAsync(bugId, opts = {}) {
    const cleanId = normalizePgBugId(bugId);
    if (!cleanId) return null;
    if (!opts.force && pgBugDetailsCache.has(cleanId)) {
      return pgBugDetailsCache.get(cleanId);
    }
    if (pgBugDetailsInflight.has(cleanId)) {
      return pgBugDetailsInflight.get(cleanId);
    }
    const reqPromise = (async () => {
      try {
        const refreshParam = opts.force ? "&refresh=1" : "";
        const res = await fetch(`/api/gerrit/bug?id=${encodeURIComponent(cleanId)}${refreshParam}`);
        if (!res || !res.ok) return null;
        const payload = await res.json();
        const bugObj = (payload && payload.bug && typeof payload.bug === "object") ? payload.bug : null;
        if (bugObj && bugObj.id) {
          pgBugDetailsCache.set(cleanId, bugObj);
          const cardEl = document.getElementById("pgBugHovercard");
          if (cardEl && cardEl.style.display === "block" && pgActiveBugHovercardId === cleanId) {
            const merged = resolvePgBugMetadata(cleanId);
            cardEl.innerHTML = renderPgBugHovercardContentHtml(merged, false);
            if (pgActiveBugHovercardAnchor) {
              positionPgBugHovercard(pgActiveBugHovercardAnchor, cardEl);
            }
          }
          return bugObj;
        }
      } catch (_) {
        // Ignore network errors and retain cached/basic metadata in the hovercard.
      } finally {
        pgBugDetailsInflight.delete(cleanId);
      }
      return null;
    })();
    pgBugDetailsInflight.set(cleanId, reqPromise);
    return reqPromise;
  }

  function _suppressAnchorNativeTitle(anchorEl) {
    if (!anchorEl || typeof anchorEl.getAttribute !== "function") return;
    const existingTitle = anchorEl.getAttribute("title");
    if (existingTitle) {
      anchorEl.setAttribute("data-saved-bug-title", existingTitle);
      anchorEl.removeAttribute("title");
    }
  }

  function showPgBugHovercard(event, bugId) {
    if (typeof isPgActivelyScrolling === "function" && isPgActivelyScrolling(event)) return;
    if (event && typeof event.stopPropagation === "function") event.stopPropagation();
    cancelHidePgBugHovercard();
    const cleanId = normalizePgBugId(bugId);
    if (!cleanId) return;
    if (typeof hidePgAttentionHovercard === "function") hidePgAttentionHovercard();
    if (typeof hidePgOwnerTooltip === "function") hidePgOwnerTooltip();

    const anchor = (event && (event.currentTarget || event.target)) ||
      (typeof document.querySelector === "function" ? document.querySelector(`[data-bug-id="${cleanId}"]`) : null);
    _suppressAnchorNativeTitle(anchor);

    pgActiveBugHovercardId = cleanId;
    pgActiveBugHovercardAnchor = anchor;
    const resolved = resolvePgBugMetadata(cleanId);
    const needsFetch = !resolved.details_loaded && !pgBugDetailsCache.has(cleanId);
    const hovercard = ensurePgBugHovercardEl();
    if (!hovercard) return;
    hovercard.setAttribute("data-bug-id", cleanId);
    hovercard.innerHTML = renderPgBugHovercardContentHtml(resolved, needsFetch);
    hovercard.style.display = "block";
    if (anchor) {
      positionPgBugHovercard(anchor, hovercard);
    }
    if (needsFetch) {
      fetchPgBugDetailsAsync(cleanId);
    }
  }

  function hidePgBugHovercard() {
    clearTimeout(pgBugHovercardHideTimer);
    pgActiveBugHovercardId = "";
    pgActiveBugHovercardAnchor = null;
    const hovercard = document.getElementById("pgBugHovercard");
    if (hovercard) hovercard.style.display = "none";
  }

  function scheduleHidePgBugHovercard() {
    clearTimeout(pgBugHovercardHideTimer);
    pgBugHovercardHideTimer = setTimeout(() => {
      hidePgBugHovercard();
    }, 220);
  }

  function cancelHidePgBugHovercard() {
    clearTimeout(pgBugHovercardHideTimer);
  }

  async function copyPgBugHovercardId(bugId, btnEl = null) {
    const cleanId = normalizePgBugId(bugId);
    if (!cleanId) return;
    const text = `b/${cleanId}`;
    const iconSpan = btnEl && typeof btnEl.querySelector === "function" ? btnEl.querySelector(".hovercard-copy-icon") : null;
    if (iconSpan) {
      iconSpan.innerHTML = PG_HUCKLE_ICONS.check;
      setTimeout(() => {
        iconSpan.innerHTML = PG_HUCKLE_ICONS.contentCopy;
      }, 1000);
    }
    showToast(`✓ Copied ${text} to clipboard!`);
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      }
    } catch (_) {}
  }

  function filterPgByBugId(bugId) {
    const cleanId = normalizePgBugId(bugId);
    if (!cleanId) return;
    hidePgBugHovercard();
    const token = `bug:${cleanId}`;
    state.searchQuery = token;
    const searchInp = document.getElementById("searchInput");
    if (searchInp) searchInp.value = token;
    if (typeof onSearchInput === "function") {
      onSearchInput(token);
    } else if (typeof applyFilters === "function") {
      applyFilters();
    }
    showToast(`Filtered CLs by ${token}`);
  }

  function onPgBugRelatedClsClick(event, bugId) {
    if (event && (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1)) {
      return;
    }
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    filterPgByBugId(bugId);
  }

  function onPgBugCardClClick(event, clNum, fallbackUrl) {
    if (event && (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1)) {
      return;
    }
    const num = Number(clNum);
    const existsInState = Array.isArray(state.changes) && state.changes.some(c => Number(c.number || c._number) === num);
    if (num && existsInState && typeof selectCommit === "function") {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      hidePgBugHovercard();
      selectCommit(num);
    }
  }

  function renderPgAttentionSetRowHtml(c) {
    if (!c) return "";
    const items = getPgAttentionSetParticipants(c);
    const viewerEmail = String(state.viewer || "").trim().toLowerCase();
    const isViewerInAttn = Boolean(c.viewer_in_attention_set || items.some(item => item.is_viewer));

    const chipsHtml = items.map(p => {
      const disp = p.name || p.short || p.email || "unknown";
      const userKey = p.email || p.short || String(p.account_id || "");
      const waitStr = p.waiting_display ? ` (${p.waiting_display})` : "";
      const reasonStr = p.reason ? ` · ${p.reason}` : "";
      const tip = `${disp}${p.email ? " <" + p.email + ">" : ""}${reasonStr}${waitStr}`;
      const avatarBg = computePgAvatarColor(p.email || disp);
      const initial = computePgInitial(p);
      const reasonBadge = p.reason ? `<span class="pg-attention-reason" title="${escapeHtml(p.reason)}">${escapeHtml(p.reason)}</span>` : "";
      const waitBadge = p.waiting_display ? `<span class="pg-attention-wait" title="Waiting on turn for ${escapeHtml(p.waiting_display)}">(${escapeHtml(p.waiting_display)})</span>` : "";

      const isUrgent = Boolean(
        (p.waiting_display && /[dw]/.test(p.waiting_display)) ||
        (p.is_viewer && c.attention_waiting_display && /[dw]/.test(c.attention_waiting_display)) ||
        Number(c.unresolved_comments || 0) > 0
      );
      const viewerCls = p.is_viewer
        ? `is-viewer is-pulsing${isUrgent ? " is-urgent" : ""}`
        : "";

      return `<span class="pg-attention-chip ${viewerCls}"
        title="${escapeHtml(tip)}"
        data-cl="${c.number}"
        data-user="${escapeHtml(userKey)}"
        onmouseenter="showPgAttentionHovercard(event, ${c.number}, '${escapeHtml(userKey)}')"
        onmouseleave="scheduleHidePgAttentionHovercard()"
        onclick="togglePgAttentionHovercard(event, ${c.number}, '${escapeHtml(userKey)}')">
        <span class="pg-attention-avatar" style="background:${avatarBg};">${escapeHtml(initial)}</span>
        <span class="pg-attention-name">${escapeHtml(disp)}</span>
        ${waitBadge}
        ${reasonBadge}
        <button type="button" class="pg-chip-act remove" title="Remove ${escapeHtml(disp)} from attention set (notify: OWNER)" onclick="event.stopPropagation(); removePgAttentionUser(${c.number}, '${escapeHtml(userKey)}', '${escapeHtml(disp)}', this)">×</button>
      </span>`;
    }).join("");

    const toggleMeBtnHtml = viewerEmail ? (
      isViewerInAttn
        ? `<button type="button" class="pg-attention-toggle-btn is-active" title="Remove yourself from the attention set (notify: OWNER)" onclick="event.stopPropagation(); removePgAttentionUser(${c.number}, '${escapeHtml(viewerEmail)}', 'myself', this)">− Remove me</button>`
        : `<button type="button" class="pg-attention-toggle-btn" title="Add yourself to the attention set (notify: OWNER)" onclick="event.stopPropagation(); addPgAttentionUser(${c.number}, '${escapeHtml(viewerEmail)}', 'myself', '', this)">+ Add me</button>`
    ) : "";

    const addBtnHtml = `<button type="button" id="btnAddAttention-${c.number}" class="pg-attention-toggle-btn pg-btn-add-attention" title="Add a reviewer to the attention set (Shift+Q)" onclick="event.stopPropagation(); togglePgAddAttentionPopover(${c.number}, this)">+ Add</button>`;

    const historyBtnHtml = `<button type="button" id="btnAttentionHistory-${c.number}" class="pg-attention-toggle-btn pg-btn-attention-history" title="View attention set transition history log (Shift+B)" onclick="event.stopPropagation(); openPgAttentionHistoryDialog(${c.number})">📜 History</button>`;

    const emptyHtml = items.length === 0 ? `<span class="pg-attention-empty" style="color:var(--g-muted);font-size:12px;">No one in attention set</span>` : "";

    return `<div class="pg-meta-row pg-attention-set-row" id="pgAttentionRow-${c.number}">
      <span class="pg-meta-key">Attention Set</span>
      <div class="pg-meta-val pg-attention-set-val">
        ${chipsHtml}
        ${emptyHtml}
        ${toggleMeBtnHtml}
        ${addBtnHtml}
        ${historyBtnHtml}
        <div id="pgAddAttentionPopover-${c.number}" class="pg-add-attention-popover" style="display:none;" onclick="event.stopPropagation()">
          ${renderPgAddAttentionPopoverContentHtml(c)}
        </div>
      </div>
    </div>`;
  }

  async function removePgAttentionUser(clNum, userKey, displayLabel, btnEl, reason) {
    const num = Number(clNum);
    const viewerEmail = String(state.viewer || "").trim().toLowerCase();
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = `<span class="btn-spinner" style="width:10px;height:10px;border-width:1.5px;"></span>`;
    }
    const clObj = state.changes.find(x => Number(x.number) === num);
    if (clObj) {
      const uLower = String(userKey || "").toLowerCase();
      if (Array.isArray(clObj.attention_set_emails)) {
        clObj.attention_set_emails = clObj.attention_set_emails.filter(e => e.toLowerCase() !== uLower);
      }
      if (Array.isArray(clObj.attention_set_details)) {
        clObj.attention_set_details = clObj.attention_set_details.filter(
          d => String(d.email || "").toLowerCase() !== uLower && String(d.short || "").toLowerCase() !== uLower && String(d.account_id || "") !== uLower
        );
      }
      if (viewerEmail && (uLower === viewerEmail || uLower === "myself")) {
        clObj.viewer_in_attention_set = false;
        clObj.attention_waiting_display = "";
      }
      getFallbackReviewersForCl(clObj).forEach(p => {
        if (String(p.email || "").toLowerCase() === uLower || String(p.short || "").toLowerCase() === uLower) {
          p.in_attention_set = false;
        }
      });
      if (!Array.isArray(clObj.attention_set_history)) {
        clObj.attention_set_history = getPgAttentionSetHistory(clObj).slice();
      }
      clObj.attention_set_history.unshift({
        id: `dyn-remove-${Date.now()}`,
        account_id: 0,
        email: userKey.includes("@") ? userKey.toLowerCase() : "",
        name: displayLabel || userKey,
        operation: "remove",
        actor_name: state.viewer ? (state.viewer.split("@")[0]) : "You",
        actor_email: state.viewer || "",
        reason: reason || "Removed from attention set via Fast Gerrit",
        date: new Date().toISOString(),
        patch_set: clObj.patch_set || 1,
        relative_date: "just now"
      });
      if (state.attentionHistoryDialogOpen && Number(state.attentionHistoryCl) === num) {
        renderPgAttentionHistoryStream(num);
      }
      rerenderReviewersPanelDom(num);
    }
    try {
      const resp = await sidecarFetch("/api/gerrit/attention", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: num, action: "remove", user: userKey, reason: reason || "", viewer: state.viewer }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(data.error || "Failed to remove from attention set");
      }
      invalidateClientQueueCache();
      if (data.change) {
        applyUpdatedSingleClToState(data.change);
        rerenderReviewersPanelDom(num);
      }
      showToast(`✓ Removed ${displayLabel || userKey} from attention set (notify: OWNER)!`);
    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = "×";
      }
      showToast("Could not remove from attention set: " + err.message);
      await fetchQueueData(state.viewer, state.scope, { preserveDiffCache: true, preserveScroll: true, silent: true });
      rerenderReviewersPanelDom(num);
    }
  }

  async function addPgAttentionUser(clNum, userKey, displayLabel, reason, btnEl) {
    const num = Number(clNum);
    const viewerEmail = String(state.viewer || "").trim().toLowerCase();
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = `<span class="btn-spinner" style="width:10px;height:10px;border-width:1.5px;"></span>`;
    }
    const clObj = state.changes.find(x => Number(x.number) === num);
    if (clObj) {
      const uLower = String(userKey || "").toLowerCase();
      if (Array.isArray(clObj.attention_set_emails) && !clObj.attention_set_emails.includes(uLower)) {
        clObj.attention_set_emails.push(uLower);
      }
      if (viewerEmail && (uLower === viewerEmail || uLower === "myself")) {
        clObj.viewer_in_attention_set = true;
      }
      getFallbackReviewersForCl(clObj).forEach(p => {
        if (String(p.email || "").toLowerCase() === uLower || String(p.short || "").toLowerCase() === uLower) {
          p.in_attention_set = true;
        }
      });
      if (!Array.isArray(clObj.attention_set_history)) {
        clObj.attention_set_history = getPgAttentionSetHistory(clObj).slice();
      }
      clObj.attention_set_history.unshift({
        id: `dyn-add-${Date.now()}`,
        account_id: 0,
        email: userKey.includes("@") ? userKey.toLowerCase() : "",
        name: displayLabel || userKey,
        operation: "add",
        actor_name: state.viewer ? (state.viewer.split("@")[0]) : "You",
        actor_email: state.viewer || "",
        reason: reason || "Added to attention set via Fast Gerrit",
        date: new Date().toISOString(),
        patch_set: clObj.patch_set || 1,
        relative_date: "just now"
      });
      if (state.attentionHistoryDialogOpen && Number(state.attentionHistoryCl) === num) {
        renderPgAttentionHistoryStream(num);
      }
      rerenderReviewersPanelDom(num);
    }
    try {
      const resp = await sidecarFetch("/api/gerrit/attention", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: num, action: "add", user: userKey, reason: reason || "", viewer: state.viewer }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(data.error || "Failed to add to attention set");
      }
      invalidateClientQueueCache();
      if (data.change) {
        applyUpdatedSingleClToState(data.change);
        rerenderReviewersPanelDom(num);
      }
      showToast(`✓ Added ${displayLabel || userKey} to attention set (notify: OWNER)!`);
    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = "+ Add me";
      }
      showToast("Could not add to attention set: " + err.message);
      await fetchQueueData(state.viewer, state.scope, { preserveDiffCache: true, preserveScroll: true, silent: true });
      rerenderReviewersPanelDom(num);
    }
  }

  function getPgAttentionSetHistory(c, skipParticipants = false) {
    if (!c) return [];
    if (Array.isArray(c.attention_set_history) && c.attention_set_history.length > 0) {
      return c.attention_set_history;
    }
    const history = [];
    const messages = Array.isArray(c.messages) ? c.messages : [];
    messages.forEach((msg, mIdx) => {
      const text = String(msg.message || "");
      const regex = /(Added to|Removed from) attention set:\s*([^\n\r]+)/g;
      let match;
      while ((match = regex.exec(text)) !== null) {
        const op = match[1].startsWith("Added") ? "add" : "remove";
        const targetStr = match[2].trim();
        let targetEmail = "";
        let targetName = targetStr;
        const emMatch = targetStr.match(/<([^>]+)>/);
        if (emMatch) {
          targetEmail = emMatch[1].trim().toLowerCase();
          targetName = targetStr.slice(0, emMatch.index).trim() || targetEmail;
        } else if (targetStr.includes("@")) {
          targetEmail = targetStr.toLowerCase();
          targetName = targetEmail.split("@")[0];
        }
        let reason = "";
        const afterText = text.slice(match.index + match[0].length).trim();
        if (
          afterText &&
          !afterText.startsWith("Patch Set") &&
          !afterText.startsWith("Added to") &&
          !afterText.startsWith("Removed from")
        ) {
          reason = afterText.split("\n\n")[0].trim();
        }
        if (!reason) {
          reason = op === "add" ? "Added to attention set" : "Removed from attention set";
        }
        history.push({
          id: `msg-${msg.id || mIdx}-${match.index}`,
          account_id: 0,
          email: targetEmail,
          name: targetName,
          operation: op,
          actor_name: msg.author_name || "Gerrit Code Review",
          actor_email: msg.author_email || "",
          reason: reason,
          date: msg.date || "",
          patch_set: msg.patch_set || 1,
          relative_date: formatPgRelativeDate(msg.date) || "",
        });
      }
    });

    if (!skipParticipants) {
      const participants = getPgAttentionSetParticipants(c);
      participants.forEach(p => {
        const email = (p.email || "").toLowerCase();
        const hasAdd = history.some(
          h => h.operation === "add" && ((email && h.email === email) || h.name === p.name)
        );
        if (!hasAdd) {
          history.unshift({
            id: `current-${p.account_id || p.email || p.short}`,
            account_id: p.account_id || 0,
            email: p.email || "",
            name: p.name || p.short,
            operation: "add",
            actor_name: p.actor_name || "Gerrit",
            actor_email: p.actor_email || "",
            reason: p.reason || "Attention requested",
            date: p.last_update || "",
            patch_set: c.patch_set || 1,
            relative_date: p.waiting_display ? `${p.waiting_display} ago` : "recently",
          });
        }
      });
    }
    return history;
  }

  function renderPgAttentionHistoryStream(clNum) {
    const num = Number(clNum || state.attentionHistoryCl || state.selectedClNumber);
    const c = (state.changes || []).find(x => Number(x.number) === num);
    const streamContainer = document.getElementById("pgAttnHistStreamContainer");
    const countPill = document.getElementById("pgAttnHistCountPill");
    const userSelect = document.getElementById("pgAttnHistUserSelect");
    if (!c || !streamContainer) return;

    const allHistory = getPgAttentionSetHistory(c);
    const filterUser = String(state.attentionHistoryFilterUser || "").trim().toLowerCase();
    const filterOp = String(state.attentionHistoryFilterOp || "all").trim().toLowerCase();
    const filterQuery = String(state.attentionHistoryFilterQuery || "").trim().toLowerCase();

    if (userSelect && (!userSelect.options || userSelect.options.length <= 1)) {
      const participants = new Set();
      allHistory.forEach(h => {
        const key = h.email || h.name;
        if (key) participants.add(key);
      });
      userSelect.innerHTML = `<option value="">All Participants (${allHistory.length})</option>` +
        Array.from(participants).map(p => {
          const matched = allHistory.filter(h => (h.email || h.name) === p);
          return `<option value="${escapeHtml(p)}" ${filterUser === p.toLowerCase() ? "selected" : ""}>${escapeHtml(p)} (${matched.length})</option>`;
        }).join("");
    }

    const filtered = allHistory.filter(item => {
      if (filterUser) {
        const itemKey = (item.email || item.name || "").toLowerCase();
        if (!itemKey.includes(filterUser)) return false;
      }
      if (filterOp !== "all" && item.operation !== filterOp) {
        return false;
      }
      if (filterQuery) {
        const hay = `${item.name} ${item.email} ${item.actor_name} ${item.actor_email} ${item.reason} PS${item.patch_set}`.toLowerCase();
        if (!hay.includes(filterQuery)) return false;
      }
      return true;
    });

    if (countPill) {
      countPill.textContent = `Showing ${filtered.length} of ${allHistory.length} transitions`;
    }

    if (filtered.length === 0) {
      streamContainer.innerHTML = `<div class="pg-empty-state" style="padding:32px 16px;text-align:center;color:var(--g-muted);">
        <div style="font-size:24px;margin-bottom:8px;">⚡</div>
        <div style="font-size:13px;font-weight:600;color:var(--g-text);margin-bottom:4px;">No attention set transitions found</div>
        <div style="font-size:12px;">${allHistory.length === 0 ? "No attention turns have been recorded for this change yet." : "Try adjusting your filters or search query."}</div>
      </div>`;
      return;
    }

    streamContainer.innerHTML = filtered.map(item => {
      const isAdd = item.operation === "add";
      const opCls = isAdd ? "is-add" : "is-remove";
      const opBadge = isAdd
        ? `<span class="pg-attn-pill-add">⚡ Added</span>`
        : `<span class="pg-attn-pill-remove">✕ Removed</span>`;
      const avatarBg = computePgAvatarColor(item.email || item.name || "user");
      const initial = (item.name ? item.name[0] : (item.email ? item.email[0] : "U")).toUpperCase();
      const actorDisp = item.actor_name || (item.actor_email ? item.actor_email.split("@")[0] : "Gerrit");
      const psBadge = item.patch_set > 0 ? `<span class="pg-meta-pill" style="font-size:10.5px;">PS ${item.patch_set}</span>` : "";
      const dateDisp = item.relative_date || (formatPgRelativeDate(item.date) || item.date);
      const dateTip = item.date || "";

      return `<div class="pg-attn-hist-card ${opCls}" data-op="${item.operation}">
        <div class="pg-attn-hist-card-head">
          <div class="pg-attn-hist-user">
            <span class="pg-attention-avatar" style="background:${avatarBg};">${escapeHtml(initial)}</span>
            <div>
              <span class="pg-attn-hist-username">${escapeHtml(item.name || item.email || "Unknown user")}</span>
              ${item.email && item.email !== item.name ? `<span class="pg-attn-hist-email">&lt;${escapeHtml(item.email)}&gt;</span>` : ""}
            </div>
          </div>
          <div class="pg-attn-hist-meta-right">
            ${opBadge}
            ${psBadge}
            <span title="${escapeHtml(dateTip)}">⏳ ${escapeHtml(dateDisp)}</span>
          </div>
        </div>
        <div class="pg-attn-hist-actor-row">
          <span>By: <strong>${escapeHtml(actorDisp)}</strong></span>
          ${item.actor_email ? `<span style="opacity:0.7;">&lt;${escapeHtml(item.actor_email)}&gt;</span>` : ""}
        </div>
        <div class="pg-attn-hist-reason">
          ${escapeHtml(item.reason ? `"${item.reason}"` : "(No reason provided)")}
        </div>
      </div>`;
    }).join("");
  }

  function openPgAttentionHistoryDialog(clNum, prefilterUser = "") {
    const num = Number(clNum || state.selectedClNumber);
    const c = (state.changes || []).find(x => Number(x.number) === num);
    if (!c) return;
    state.attentionHistoryDialogOpen = true;
    state.attentionHistoryCl = num;
    state.attentionHistoryFilterUser = prefilterUser ? prefilterUser.toLowerCase() : "";
    state.attentionHistoryFilterOp = "all";
    state.attentionHistoryFilterQuery = "";

    const backdrop = document.getElementById("pgAttentionHistoryBackdrop");
    const subEl = document.getElementById("pgAttentionHistorySubtitle");
    const searchInp = document.getElementById("pgAttnHistSearchInput");
    const clearBtn = document.getElementById("btnClearAttnHistFilter");
    const userSelect = document.getElementById("pgAttnHistUserSelect");

    if (subEl) {
      subEl.textContent = `ag/${num}: ${c.subject || ""} (PS ${c.patch_set || 1})`;
    }
    if (searchInp) {
      searchInp.value = "";
    }
    if (clearBtn) {
      clearBtn.style.display = "none";
    }
    if (userSelect) {
      userSelect.innerHTML = `<option value="">All Participants</option>`;
    }
    const opBtns = document.querySelectorAll(".pg-attn-op-filter .pg-segmented-btn");
    opBtns.forEach(btn => {
      btn.classList.toggle("is-active", btn.dataset.op === "all");
    });

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }

    renderPgAttentionHistoryStream(num);

    setTimeout(() => {
      if (searchInp) searchInp.focus();
    }, 50);
  }

  function closePgAttentionHistoryDialog() {
    state.attentionHistoryDialogOpen = false;
    state.attentionHistoryCl = null;
    state.attentionHistoryFilterUser = "";
    state.attentionHistoryFilterOp = "all";
    state.attentionHistoryFilterQuery = "";
    const backdrop = document.getElementById("pgAttentionHistoryBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function togglePgAttentionHistoryDialog(clNum) {
    if (state.attentionHistoryDialogOpen) {
      closePgAttentionHistoryDialog();
    } else {
      openPgAttentionHistoryDialog(clNum);
    }
  }

  function onPgAttentionHistoryBackdropClick(e) {
    if (e.target && e.target.id === "pgAttentionHistoryBackdrop") {
      closePgAttentionHistoryDialog();
    }
  }

  function onPgAttentionHistoryFilterInput(e) {
    const val = (e.target.value || "").trim();
    state.attentionHistoryFilterQuery = val;
    const clearBtn = document.getElementById("btnClearAttnHistFilter");
    if (clearBtn) {
      clearBtn.style.display = val ? "inline-block" : "none";
    }
    renderPgAttentionHistoryStream(state.attentionHistoryCl);
  }

  function clearPgAttentionHistoryFilter() {
    state.attentionHistoryFilterQuery = "";
    const searchInp = document.getElementById("pgAttnHistSearchInput");
    if (searchInp) {
      searchInp.value = "";
      searchInp.focus();
    }
    const clearBtn = document.getElementById("btnClearAttnHistFilter");
    if (clearBtn) clearBtn.style.display = "none";
    renderPgAttentionHistoryStream(state.attentionHistoryCl);
  }

  function onPgAttentionHistoryUserSelect(e) {
    state.attentionHistoryFilterUser = (e.target.value || "").trim().toLowerCase();
    renderPgAttentionHistoryStream(state.attentionHistoryCl);
  }

  function setPgAttentionHistoryOpFilter(op) {
    state.attentionHistoryFilterOp = op;
    const opBtns = document.querySelectorAll(".pg-attn-op-filter .pg-segmented-btn");
    opBtns.forEach(btn => {
      btn.classList.toggle("is-active", btn.dataset.op === op);
    });
    renderPgAttentionHistoryStream(state.attentionHistoryCl);
  }

  if (typeof window !== "undefined") {
    window.findLatestAttentionTransition = findLatestAttentionTransition;
    window.getPgAttentionSetParticipants = getPgAttentionSetParticipants;
    window.getPgAttentionEligibleCandidates = getPgAttentionEligibleCandidates;
    window.renderPgAddAttentionPopoverContentHtml = renderPgAddAttentionPopoverContentHtml;
    window.togglePgAddAttentionPopover = togglePgAddAttentionPopover;
    window.closePgAddAttentionPopover = closePgAddAttentionPopover;
    window.closeAllPgAddAttentionPopovers = closeAllPgAddAttentionPopovers;
    window.selectPgAddAttentionCandidate = selectPgAddAttentionCandidate;
    window.submitPgAddAttentionForm = submitPgAddAttentionForm;
    window.ensurePgAttentionHovercardEl = ensurePgAttentionHovercardEl;
    window.positionPgAttentionHovercard = positionPgAttentionHovercard;
    window.showPgAttentionHovercard = showPgAttentionHovercard;
    window.hidePgAttentionHovercard = hidePgAttentionHovercard;
    window.togglePgAttentionHovercard = togglePgAttentionHovercard;
    window.copyPgHovercardEmail = copyPgHovercardEmail;
    window.onPgCardRemoveAttentionClick = onPgCardRemoveAttentionClick;
    window.onPgCardReplyAttentionClick = onPgCardReplyAttentionClick;
    window.openReplyDialog = openReplyDialog;
    window.closeReplyDialog = closeReplyDialog;
    window.cancelReplyDialog = cancelReplyDialog;
    window.loadPgReplyDraft = loadPgReplyDraft;
    window.savePgReplyDraft = savePgReplyDraft;
    window.deletePgReplyDraft = deletePgReplyDraft;
    window.updatePgReplyDialogAutosaveBadge = updatePgReplyDialogAutosaveBadge;
    window.schedulePgReplyDialogDraftAutosave = schedulePgReplyDialogDraftAutosave;
    window.flushPgReplyDialogDraftAutosave = flushPgReplyDialogDraftAutosave;
    window.syncPgReplyDraftDiscardButton = syncPgReplyDraftDiscardButton;
    window.restorePgReplyDraftForCl = restorePgReplyDraftForCl;
    window.discardReplyCommentDraft = discardReplyCommentDraft;
    window.getPendingReplyDialogDraft = getPendingReplyDialogDraft;
    window.getPendingDraftsForCl = getPendingDraftsForCl;
    window.updateReplyButtonDraftsBadge = updateReplyButtonDraftsBadge;
    window.syncReplyDraftsSectionDom = syncReplyDraftsSectionDom;
    window.discardReplyDialogDraft = discardReplyDialogDraft;
    window.discardAllReplyDialogDrafts = discardAllReplyDialogDrafts;
    window.renderPgAttentionSetRowHtml = renderPgAttentionSetRowHtml;
    window.removePgAttentionUser = removePgAttentionUser;
    window.addPgAttentionUser = addPgAttentionUser;
    window.renderPgReplyAttentionChipsHtml = renderPgReplyAttentionChipsHtml;
    window.onPgReplyAttentionCheckboxChange = onPgReplyAttentionCheckboxChange;
    window.getPgAttentionSetHistory = getPgAttentionSetHistory;
    window.renderPgAttentionHistoryStream = renderPgAttentionHistoryStream;
    window.openPgAttentionHistoryDialog = openPgAttentionHistoryDialog;
    window.closePgAttentionHistoryDialog = closePgAttentionHistoryDialog;
    window.togglePgAttentionHistoryDialog = togglePgAttentionHistoryDialog;
    window.onPgAttentionHistoryBackdropClick = onPgAttentionHistoryBackdropClick;
    window.onPgAttentionHistoryFilterInput = onPgAttentionHistoryFilterInput;
    window.clearPgAttentionHistoryFilter = clearPgAttentionHistoryFilter;
    window.onPgAttentionHistoryUserSelect = onPgAttentionHistoryUserSelect;
    window.setPgAttentionHistoryOpFilter = setPgAttentionHistoryOpFilter;
    window.renderPgReplyParticipantsInnerHtml = renderPgReplyParticipantsInnerHtml;
    window.syncPgReplyParticipantsDom = syncPgReplyParticipantsDom;
    window.openReviewerComposer = openReviewerComposer;
    window.closeReviewerComposer = closeReviewerComposer;
  }

  function renderCommitReviewersPanelHtml(c, includeClPrefix) {
    if (!c) return "";
    const clNum = Number(c.number);
    const hasRevComposer = Boolean(state.activeReviewerComposer && Number(state.activeReviewerComposer.clNum) === clNum);
    const hasChatComposer = Boolean(state.activeChatComposer && Number(state.activeChatComposer.clNum) === clNum);
    const sentDms = (state.sentChatDmsByCl && state.sentChatDmsByCl[clNum]) || _PG_EMPTY_ARRAY;
    const allRevs = getFallbackReviewersForCl(c);
    const ccs = Array.isArray(c.ccs_detail) ? c.ccs_detail : _PG_EMPTY_ARRAY;
    const bundle = state.diffsByCl[clNum];
    const usePassRef = _curCommitRenderPass.active && _curCommitRenderPass.clNum === clNum && _curCommitRenderPass.refBundle === bundle && _curCommitRenderPass.refInfo;
    const refInfo = usePassRef ? _curCommitRenderPass.refInfo : getPgChangeReferenceInfo(c, bundle);
    let revPanelCacheKey = "";
    let revItemsSig = "";
    if (!hasRevComposer && !hasChatComposer && sentDms.length === 0) {
      const attnArr = Array.isArray(c.attention_set_emails) ? c.attention_set_emails : null;
      const tagsArr = Array.isArray(c.hashtags) ? c.hashtags : null;
      const bugsArr = Array.isArray(c.bugs) ? c.bugs : null;
      const srArr = Array.isArray(c.submit_requirements) ? c.submit_requirements : null;
      const rLen = allRevs.length;
      const ccLen = ccs.length;
      const r0 = rLen > 0 ? allRevs[0] : null;
      const r1 = rLen > 1 ? allRevs[1] : null;
      const cc0 = ccLen > 0 ? ccs[0] : null;
      const cc1 = ccLen > 1 ? ccs[1] : null;
      const rpSub = c._revPanSub;
      if (
        rpSub &&
        rLen <= 2 &&
        ccLen <= 2 &&
        typeof c._cachedReviewersPanelHtml === "string" &&
        rpSub.clNum === clNum &&
        rpSub.prefix === Boolean(includeClPrefix) &&
        rpSub.updDisp === (c.updated_display || c.flight_display || "") &&
        rpSub.updIso === (c.updated_iso || "") &&
        rpSub.ownerEm === c.owner_email &&
        rpSub.ownerNm === c.owner_name &&
        rpSub.uplEm === c.uploader_email &&
        rpSub.topic === c.topic &&
        rpSub.branch === c.branch &&
        rpSub.proj === c.project &&
        rpSub.subType === c.submit_type &&
        rpSub.cpCh === c.cherry_pick_of_change &&
        rpSub.cpPs === c.cherry_pick_of_patch_set &&
        rpSub.revOf === c.revert_of &&
        rpSub.refKey === (c._cachedReferenceInfoKey || refInfo.commitSha || "") &&
        rpSub.viewer === state.viewer &&
        rpSub.dlScheme === (state.downloadScheme || "repo") &&
        rpSub.status === c.status &&
        rpSub.wip === c.work_in_progress &&
        rpSub.vAttn === c.viewer_in_attention_set &&
        rpSub.rLen === rLen &&
        rpSub.r0Em === (r0 ? (r0.email || r0.name || "") : "") &&
        rpSub.r0Cr === (r0 ? (r0.cr_vote || 0) : 0) &&
        rpSub.r0V === (r0 ? (r0.v_vote || 0) : 0) &&
        rpSub.r0At === Boolean(r0 && r0.in_attention_set) &&
        rpSub.r1Em === (r1 ? (r1.email || r1.name || "") : "") &&
        rpSub.r1Cr === (r1 ? (r1.cr_vote || 0) : 0) &&
        rpSub.r1V === (r1 ? (r1.v_vote || 0) : 0) &&
        rpSub.r1At === Boolean(r1 && r1.in_attention_set) &&
        rpSub.ccLen === ccLen &&
        rpSub.cc0Em === (cc0 ? (cc0.email || cc0.name || "") : "") &&
        rpSub.cc1Em === (cc1 ? (cc1.email || cc1.name || "") : "") &&
        rpSub.attnRef === attnArr &&
        rpSub.attnLen === (attnArr ? attnArr.length : 0) &&
        rpSub.tagsRef === tagsArr &&
        rpSub.tagsLen === (tagsArr ? tagsArr.length : 0) &&
        rpSub.bugsRef === bugsArr &&
        rpSub.bugsLen === (bugsArr ? bugsArr.length : 0) &&
        rpSub.srRef === srArr &&
        rpSub.srLen === (srArr ? srArr.length : 0)
      ) {
        return c._cachedReviewersPanelHtml;
      }
      let revSig = "";
      for (let i = 0; i < rLen; i++) {
        const r = allRevs[i];
        if (i > 0) revSig += ",";
        revSig += `${r.email || r.name || ""}:${r.cr_vote || 0}:${r.v_vote || 0}:${r.in_attention_set ? 1 : 0}`;
      }
      let ccSig = "";
      for (let i = 0; i < ccLen; i++) {
        const cc = ccs[i];
        if (i > 0) ccSig += ",";
        ccSig += `${cc.email || cc.name || ""}`;
      }
      revItemsSig = `${revSig}|${ccSig}`;
      if (
        rpSub &&
        typeof c._cachedReviewersPanelHtml === "string" &&
        rpSub.clNum === clNum &&
        rpSub.prefix === Boolean(includeClPrefix) &&
        rpSub.updDisp === (c.updated_display || c.flight_display || "") &&
        rpSub.updIso === (c.updated_iso || "") &&
        rpSub.ownerEm === c.owner_email &&
        rpSub.ownerNm === c.owner_name &&
        rpSub.uplEm === c.uploader_email &&
        rpSub.topic === c.topic &&
        rpSub.branch === c.branch &&
        rpSub.proj === c.project &&
        rpSub.subType === c.submit_type &&
        rpSub.cpCh === c.cherry_pick_of_change &&
        rpSub.cpPs === c.cherry_pick_of_patch_set &&
        rpSub.revOf === c.revert_of &&
        rpSub.refKey === (c._cachedReferenceInfoKey || refInfo.commitSha || "") &&
        rpSub.viewer === state.viewer &&
        rpSub.dlScheme === (state.downloadScheme || "repo") &&
        rpSub.status === c.status &&
        rpSub.wip === c.work_in_progress &&
        rpSub.vAttn === c.viewer_in_attention_set &&
        rpSub.revItemsSig === revItemsSig &&
        rpSub.attnRef === attnArr &&
        rpSub.attnLen === (attnArr ? attnArr.length : 0) &&
        rpSub.tagsRef === tagsArr &&
        rpSub.tagsLen === (tagsArr ? tagsArr.length : 0) &&
        rpSub.bugsRef === bugsArr &&
        rpSub.bugsLen === (bugsArr ? bugsArr.length : 0) &&
        rpSub.srRef === srArr &&
        rpSub.srLen === (srArr ? srArr.length : 0)
      ) {
        return c._cachedReviewersPanelHtml;
      }
      const attnSig = attnArr ? attnArr.join(",") : "";
      const tagSig = tagsArr ? tagsArr.join(",") : "";
      const bugSig = bugsArr ? bugsArr.map(b => `${b.id}:${b.status || ""}`).join(",") : (Array.isArray(c.bug_ids) ? c.bug_ids.join(",") : "");
      const srSig = srArr ? srArr.map(s => `${s.name}:${s.status}`).join(",") : "";
      revPanelCacheKey = `${clNum}|${includeClPrefix ? 1 : 0}|${c.updated_display || c.flight_display || ""}|${c.updated_iso || ""}|${c.owner_email || ""}|${c.owner_name || ""}|${c.uploader_email || ""}|${c.topic || ""}|${c.branch || ""}|${c.project || ""}|${c.submit_type || ""}|${c.cherry_pick_of_change || ""}:${c.cherry_pick_of_patch_set || ""}|${c.revert_of || ""}|${c._cachedReferenceInfoKey || refInfo.commitSha || ""}|${state.viewer || ""}|${state.downloadScheme || "repo"}|${c.status || ""}|${c.work_in_progress ? 1 : 0}|${c.viewer_in_attention_set ? 1 : 0}|${revSig}|${ccSig}|${attnSig}|${tagSig}|${bugSig}|${srSig}`;
      if (c._cachedReviewersPanelKey === revPanelCacheKey && typeof c._cachedReviewersPanelHtml === "string") {
        const wSub = c._revPanSub || (c._revPanSub = {});
        wSub.clNum = clNum;
        wSub.prefix = Boolean(includeClPrefix);
        wSub.updDisp = c.updated_display || c.flight_display || "";
        wSub.updIso = c.updated_iso || "";
        wSub.ownerEm = c.owner_email;
        wSub.ownerNm = c.owner_name;
        wSub.uplEm = c.uploader_email;
        wSub.topic = c.topic;
        wSub.branch = c.branch;
        wSub.proj = c.project;
        wSub.subType = c.submit_type;
        wSub.cpCh = c.cherry_pick_of_change;
        wSub.cpPs = c.cherry_pick_of_patch_set;
        wSub.revOf = c.revert_of;
        wSub.refKey = c._cachedReferenceInfoKey || refInfo.commitSha || "";
        wSub.viewer = state.viewer;
        wSub.dlScheme = state.downloadScheme || "repo";
        wSub.status = c.status;
        wSub.wip = c.work_in_progress;
        wSub.vAttn = c.viewer_in_attention_set;
        wSub.revItemsSig = revItemsSig;
        wSub.rLen = rLen;
        wSub.r0Em = r0 ? (r0.email || r0.name || "") : "";
        wSub.r0Cr = r0 ? (r0.cr_vote || 0) : 0;
        wSub.r0V = r0 ? (r0.v_vote || 0) : 0;
        wSub.r0At = Boolean(r0 && r0.in_attention_set);
        wSub.r1Em = r1 ? (r1.email || r1.name || "") : "";
        wSub.r1Cr = r1 ? (r1.cr_vote || 0) : 0;
        wSub.r1V = r1 ? (r1.v_vote || 0) : 0;
        wSub.r1At = Boolean(r1 && r1.in_attention_set);
        wSub.ccLen = ccLen;
        wSub.cc0Em = cc0 ? (cc0.email || cc0.name || "") : "";
        wSub.cc1Em = cc1 ? (cc1.email || cc1.name || "") : "";
        wSub.attnRef = attnArr;
        wSub.attnLen = attnArr ? attnArr.length : 0;
        wSub.tagsRef = tagsArr;
        wSub.tagsLen = tagsArr ? tagsArr.length : 0;
        wSub.bugsRef = bugsArr;
        wSub.bugsLen = bugsArr ? bugsArr.length : 0;
        wSub.srRef = srArr;
        wSub.srLen = srArr ? srArr.length : 0;
        return c._cachedReviewersPanelHtml;
      }
    }
    const displayRevs = allRevs.filter(p => !p.is_owner || Number(p.cr_vote || 0) !== 0);
    const ownerChipHtml = renderPgAccountChipHtml(c.number, buildPgOwnerParticipant(c), { showVote: false });
    const uploaderEmail = String(c.uploader_email || "").trim();
    const showUploader = Boolean(
      uploaderEmail && uploaderEmail.toLowerCase() !== String(c.owner_email || "").trim().toLowerCase()
    );
    const uploaderRowHtml = showUploader
      ? `<div class="pg-meta-row">
          <span class="pg-meta-key">Uploader</span>
          <div class="pg-meta-val">${renderPgAccountChipHtml(c.number, {
            email: uploaderEmail,
            short: c.uploader_short || uploaderEmail.split("@")[0],
            name: c.uploader_name || c.uploader_short || uploaderEmail.split("@")[0],
            full_name: c.uploader_name || uploaderEmail,
            state: "UPLOADER",
            is_bot: false,
            is_owner: true,
            removable: false,
            cr_vote: 0,
            votes: [],
          }, { showVote: false, readOnly: true })}</div>
        </div>`
      : "";

    const revChipsHtml = displayRevs.map(p => renderPgAccountChipHtml(c.number, p, { showVote: true })).join("");
    const ccChipsHtml = ccs.map(p => renderPgAccountChipHtml(c.number, p, { showVote: false })).join("");
    const pendingDmTargets = getDmEligibleParticipantsForCl(c).filter(p => !p.is_owner && Number(p.cr_vote || 0) < 2);
    const isChatOpen = hasChatComposer;
    const quickPingBtnHtml = (!isChatOpen && pendingDmTargets.length > 0)
      ? `<button type="button" class="add-participant-btn" data-email="${escapeHtml(pendingDmTargets[0].email)}" title="Compose a 1:1 Google Chat DM reminder for ${escapeHtml(pendingDmTargets[0].email)}" onclick="event.stopPropagation(); openChatDmComposer(${c.number}, this.dataset.email)">💬 Ping</button>`
      : "";
    const attentionRowHtml = renderPgAttentionSetRowHtml(c);

    let parentRowHtml = "";
    if (refInfo.parents && refInfo.parents.length > 0) {
      const label = refInfo.parents.length > 1 ? "Parents" : "Parent";
      const parentsHtml = refInfo.parents.map(p => {
        const clLink = p.clNumber
          ? `<a class="pg-meta-link pg-parent-cl-link" href="#/c/${p.clNumber}" onclick="event.preventDefault(); selectCommit(${p.clNumber});" title="Parent change: ag/${p.clNumber} - ${escapeHtml(p.clSubject)}">ag/${p.clNumber}</a>`
          : "";
        const shaText = `<span class="pg-meta-mono" title="${escapeHtml(p.sha)}">${escapeHtml(clLink ? `(${p.shaShort})` : p.shaShort)}</span>`;
        const copyBtn = `<button type="button" class="pg-copy-row-btn" onclick="event.stopPropagation(); copyPgMetaField(${c.number}, 'parent', this)" title="Copy Parent SHA: ${escapeHtml(p.sha)}" aria-label="Copy Parent SHA">📋</button>`;
        const extLink = p.gitilesUrl
          ? `<a class="pg-meta-ext-link" href="${escapeHtml(p.gitilesUrl)}" target="_blank" rel="noopener noreferrer" title="View parent commit in Gitiles ↗">↗</a>`
          : "";
        return `<div class="pg-meta-val-copyable pg-parent-item">${clLink} ${shaText} ${copyBtn} ${extLink}</div>`;
      }).join("");

      parentRowHtml = `<div class="pg-meta-row pg-meta-parent-row">
          <span class="pg-meta-key">${label}</span>
          <div class="pg-meta-val pg-meta-val-parents">${parentsHtml}</div>
        </div>`;
    }

    const cpChange = c.cherry_pick_of_change ? Number(c.cherry_pick_of_change) : null;
    const cpPs = c.cherry_pick_of_patch_set ? Number(c.cherry_pick_of_patch_set) : 1;
    const cherryPickRowHtml = cpChange
      ? `<div class="pg-meta-row pg-meta-cherrypick-row" id="pgMetaCherryPickRow-${c.number}">
          <span class="pg-meta-key">Cherry pick of</span>
          <div class="pg-meta-val pg-meta-val-copyable">
            <a class="pg-meta-link pg-cherrypick-link" href="#/c/${cpChange}" onclick="event.preventDefault(); selectCommit(${cpChange});" title="Cherry pick of change ag/${cpChange},${cpPs}">ag/${cpChange},${cpPs}</a>
            <button type="button" class="pg-copy-row-btn" onclick="event.stopPropagation(); copyPgMetaField(${c.number}, 'cherry_pick_of', this)" title="Copy Cherry pick reference: ${cpChange},${cpPs}" aria-label="Copy Cherry pick reference">📋</button>
          </div>
        </div>`
      : "";

    const revertOfChange = c.revert_of ? Number(c.revert_of) : null;
    const revertOfRowHtml = revertOfChange
      ? `<div class="pg-meta-row pg-meta-revert-row" id="pgMetaRevertRow-${c.number}">
          <span class="pg-meta-key">Revert of</span>
          <div class="pg-meta-val pg-meta-val-copyable">
            <a class="pg-meta-link pg-revert-link" href="#/c/${revertOfChange}" onclick="event.preventDefault(); selectCommit(${revertOfChange});" title="Revert of change ag/${revertOfChange}">ag/${revertOfChange}</a>
            <button type="button" class="pg-copy-row-btn" onclick="event.stopPropagation(); copyPgMetaField(${c.number}, 'revert_of', this)" title="Copy Revert reference: ${revertOfChange}" aria-label="Copy Revert reference">📋</button>
          </div>
        </div>`
      : "";
    const topicStr = String(c.topic || "").trim();
    const safeTopic = escapeHtml(topicStr);
    const topicChipHtml = topicStr
      ? `<span class="pg-topic-chip" id="pgTopicChip-${c.number}" data-topic="${safeTopic}" title="Filter dashboard by topic '${safeTopic}'" onclick="applyPgTopicFilter(this.getAttribute('data-topic'))">` +
        `<span class="pg-topic-icon" aria-hidden="true">🏷️</span>` +
        `<span class="pg-topic-name">${safeTopic}</span>` +
        `<button type="button" class="pg-chip-act edit" title="Edit topic (Alt+T)" onclick="event.stopPropagation(); showPgEditTopicInput(${c.number})">✏️</button>` +
        `<button type="button" class="pg-chip-act remove" title="Remove topic" onclick="event.stopPropagation(); removePgTopic(${c.number}, this)">×</button>` +
        `<button type="button" class="pg-chip-act copy" title="Copy topic name" onclick="event.stopPropagation(); copyPgTopic(this.closest('.pg-topic-chip').getAttribute('data-topic'))">📋</button>` +
      `</span>`
      : "";
    const topicBtnHtml = topicStr
      ? `<button type="button" class="pg-btn-edit-topic" id="btnShowEditTopic-${c.number}" onclick="showPgEditTopicInput(${c.number})" title="Edit topic (Alt+T)">Edit</button>`
      : `<button type="button" class="pg-btn-add-topic is-empty" id="btnShowAddTopic-${c.number}" onclick="showPgEditTopicInput(${c.number})" title="Add topic to change (Alt+T)">+ Add topic</button>`;
    const topicRowHtml = `<div class="pg-meta-row pg-meta-topic-row" id="pgMetaTopicRow-${c.number}">
        <span class="pg-meta-key">Topic</span>
        <div class="pg-meta-val pg-meta-topic-val">
          ${topicChipHtml}
          ${topicBtnHtml}
          <span class="pg-edit-topic-inline" id="pgEditTopicWrap-${c.number}" style="display:none;">
            <span class="pg-topic-icon" aria-hidden="true">🏷️</span>
            <input type="text" class="pg-edit-topic-input" id="pgEditTopicInput-${c.number}" placeholder="topic" value="${safeTopic}" onkeydown="onPgEditTopicKeyDown(event, ${c.number})" autocomplete="off" spellcheck="false" />
            <button type="button" class="pg-btn-topic-act save" onclick="submitPgEditTopic(${c.number})" title="Save topic (Enter)">✓</button>
            <button type="button" class="pg-btn-topic-act cancel" onclick="hidePgEditTopicInput(${c.number})" title="Cancel (Esc)">✕</button>
          </span>
        </div>
      </div>`;
    const hashtags = (Array.isArray(c.hashtags) ? c.hashtags : []).map(t => String(t).trim()).filter(Boolean);
    const hashtagsChipsHtml = hashtags.map(tag => {
      const safeTag = escapeHtml(tag);
      return `<span class="pg-hashtag-chip" title="Filter dashboard by hashtag #${safeTag}" onclick="filterDashboardByHashtag('${safeTag}')">` +
        `<span class="pg-hashtag-hash">#</span>${safeTag}` +
        `<button type="button" class="pg-chip-act remove" title="Remove hashtag #${safeTag}" onclick="event.stopPropagation(); removePgHashtag(${c.number}, '${safeTag}', this)">×</button>` +
      `</span>`;
    }).join("");

    const hashtagsRowHtml = `<div class="pg-meta-row pg-meta-hashtags-row" id="pgMetaHashtagsRow-${c.number}">
      <span class="pg-meta-key">Hashtags</span>
      <div class="pg-meta-val pg-meta-hashtags-val">
        ${hashtagsChipsHtml}
        <button type="button" class="pg-btn-add-hashtag ${hashtags.length === 0 ? "is-empty" : ""}" id="btnShowAddHashtag-${c.number}" onclick="showPgAddHashtagInput(${c.number})" title="Add hashtag to change (Alt+H)">+ Add hashtag</button>
        <span class="pg-add-hashtag-inline" id="pgAddHashtagWrap-${c.number}" style="display:none;">
          <span class="pg-hashtag-hash">#</span>
          <input type="text" class="pg-add-hashtag-input" id="pgAddHashtagInput-${c.number}" placeholder="tag" onkeydown="onPgAddHashtagKeyDown(event, ${c.number})" autocomplete="off" spellcheck="false" />
          <button type="button" class="pg-btn-hashtag-act" onclick="submitPgAddHashtag(${c.number})" title="Add hashtag">✓</button>
          <button type="button" class="pg-btn-hashtag-act cancel" onclick="hidePgAddHashtagInput(${c.number})" title="Cancel">✕</button>
        </span>
      </div>
    </div>`;
    const rawSubmitType = String(c.submit_type || "").trim();
    const strategyLabel = PG_SUBMIT_TYPE_LABELS[rawSubmitType] || rawSubmitType || "Rebase Always";
    const bugBadgesHtml = renderBugBadgesHtml(c);
    const fixesRowHtml = bugBadgesHtml
      ? `<div class="pg-meta-row">
          <span class="pg-meta-key">Fixes</span>
          <div class="pg-meta-val">${bugBadgesHtml}</div>
        </div>`
      : "";
    const prefixHtml = includeClPrefix
      ? `<span class="badge badge-rebase" style="margin-right:4px;">ag/${c.number}</span>`
      : "";

    const changeIdRowHtml = refInfo.changeId
      ? `<div class="pg-meta-row pg-meta-change-id-row">
          <span class="pg-meta-key">Change-Id</span>
          <div class="pg-meta-val pg-meta-val-copyable">
            <span class="pg-meta-mono" title="${escapeHtml(refInfo.changeId)}">${escapeHtml(refInfo.changeIdShort || refInfo.changeId)}</span>
            <button type="button" class="pg-copy-row-btn" onclick="event.stopPropagation(); copyPgMetaField(${c.number}, 'change_id', this)" title="Copy Change-Id: ${escapeHtml(refInfo.changeId)}" aria-label="Copy Change-Id">📋</button>
          </div>
        </div>`
      : "";
    const commitRowHtml = refInfo.commitSha
      ? `<div class="pg-meta-row pg-meta-commit-row">
          <span class="pg-meta-key">Commit</span>
          <div class="pg-meta-val pg-meta-val-copyable">
            <span class="pg-meta-mono" title="${escapeHtml(refInfo.commitSha)}">${escapeHtml(refInfo.commitShaShort || refInfo.commitSha)}</span>
            <button type="button" class="pg-copy-row-btn" onclick="event.stopPropagation(); copyPgMetaField(${c.number}, 'commit', this)" title="Copy Commit SHA: ${escapeHtml(refInfo.commitSha)}" aria-label="Copy Commit SHA">📋</button>
            <button type="button" class="pg-copy-row-btn pg-btn-meta-download-cmd" onclick="event.stopPropagation(); copyPgMetaField(${c.number}, 'repo_download', this)" title="Copy Repo Download command: ${escapeHtml(refInfo.repoDownloadCmd || '')}" aria-label="Copy Repo Download command">⬇</button>
            ${refInfo.gitilesUrl ? `<a class="pg-meta-ext-link" href="${escapeHtml(refInfo.gitilesUrl)}" target="_blank" rel="noopener noreferrer" title="View commit in Gitiles ↗">↗</a>` : ""}
          </div>
        </div>`
      : "";
    const downloadRowHtml = (refInfo.repoDownloadCmd || refInfo.gitCheckoutCmd)
      ? `<div class="pg-meta-row pg-meta-download-row" id="pgMetaDownloadRow-${c.number}">
          <span class="pg-meta-key">Download</span>
          <div class="pg-meta-val pg-meta-val-copyable">
            <span class="pg-meta-mono" title="${escapeHtml(refInfo.repoDownloadCmd || refInfo.gitCheckoutCmd)}">${escapeHtml(refInfo.repoDownloadCmd || refInfo.gitCheckoutCmd)}</span>
            <button type="button" class="pg-copy-row-btn" onclick="event.stopPropagation(); copyPgMetaField(${c.number}, 'repo_download', this)" title="Copy Repo Download command: ${escapeHtml(refInfo.repoDownloadCmd || '')}" aria-label="Copy Repo Download command">📋</button>
            <button type="button" class="pg-copy-row-btn pg-btn-meta-download-dialog" onclick="event.stopPropagation(); openPgDownloadDialog(${c.number})" title="Open patch download commands dialog (Shortcut: d)" aria-label="Open download patch dialog">⋯</button>
          </div>
        </div>`
      : "";

    const panelHtml = `<aside id="reviewersPanel-${c.number}" class="pg-change-metadata cl-participants-panel" data-has-prefix="${includeClPrefix ? "1" : "0"}">
      <div class="pg-meta-rows">
        <div class="pg-meta-row">
          <span class="pg-meta-key">Updated</span>
          <div class="pg-meta-val" title="${escapeHtml(c.updated_iso || "")}">${prefixHtml}${escapeHtml(c.updated_display || c.flight_display || "recently")}</div>
        </div>
        <div class="pg-meta-row">
          <span class="pg-meta-key">Owner</span>
          <div class="pg-meta-val">${ownerChipHtml}</div>
        </div>
        ${uploaderRowHtml}
        <div class="pg-meta-row">
          <span class="pg-meta-key">Reviewers</span>
          <div class="pg-meta-val">
            ${revChipsHtml}
            ${renderReviewerComposerHtml(c.number, "REVIEWER")}
            ${quickPingBtnHtml}
          </div>
        </div>
        <div class="pg-meta-row">
          <span class="pg-meta-key">CC</span>
          <div class="pg-meta-val">
            ${ccChipsHtml}
            ${renderReviewerComposerHtml(c.number, "CC")}
          </div>
        </div>
        ${attentionRowHtml}
      </div>
      ${renderSentChatDmDmOrEmptyHtml(c)}
      <div class="pg-meta-rows" style="margin-top:8px;">
        <div class="pg-meta-row">
          <span class="pg-meta-key">Repo | Branch</span>
          <div class="pg-meta-val">
            <span class="pg-meta-link" title="${escapeHtml(c.project || "")}">${escapeHtml(formatRepoDisplayPath(c))}</span><span class="pg-meta-pipe">|</span><span class="pg-meta-link">${escapeHtml(c.branch || "main")}</span>
          </div>
        </div>
        ${changeIdRowHtml}
        ${commitRowHtml}
        ${downloadRowHtml}
        ${parentRowHtml}
        ${cherryPickRowHtml}
        ${revertOfRowHtml}
        ${topicRowHtml}
        ${hashtagsRowHtml}
        <div class="pg-meta-row">
          <span class="pg-meta-key">Strategy</span>
          <div class="pg-meta-val" style="color:var(--g-muted);font-size:12px;">${escapeHtml(strategyLabel)}</div>
        </div>
        ${fixesRowHtml}
      </div>
      <div id="pgSrSlot-${c.number}">${renderPgSubmitRequirementsHtml(c)}</div>
    </aside>`;
    if (revPanelCacheKey) {
      c._cachedReviewersPanelKey = revPanelCacheKey;
      c._cachedReviewersPanelHtml = panelHtml;
      const attnArr = Array.isArray(c.attention_set_emails) ? c.attention_set_emails : null;
      const tagsArr = Array.isArray(c.hashtags) ? c.hashtags : null;
      const bugsArr = Array.isArray(c.bugs) ? c.bugs : null;
      const srArr = Array.isArray(c.submit_requirements) ? c.submit_requirements : null;
      const rLen = allRevs.length;
      const ccLen = ccs.length;
      const r0 = rLen > 0 ? allRevs[0] : null;
      const r1 = rLen > 1 ? allRevs[1] : null;
      const cc0 = ccLen > 0 ? ccs[0] : null;
      const cc1 = ccLen > 1 ? ccs[1] : null;
      const wSub = c._revPanSub || (c._revPanSub = {});
      wSub.clNum = clNum;
      wSub.prefix = Boolean(includeClPrefix);
      wSub.updDisp = c.updated_display || c.flight_display || "";
      wSub.updIso = c.updated_iso || "";
      wSub.ownerEm = c.owner_email;
      wSub.ownerNm = c.owner_name;
      wSub.uplEm = c.uploader_email;
      wSub.topic = c.topic;
      wSub.branch = c.branch;
      wSub.proj = c.project;
      wSub.subType = c.submit_type;
      wSub.cpCh = c.cherry_pick_of_change;
      wSub.cpPs = c.cherry_pick_of_patch_set;
      wSub.revOf = c.revert_of;
      wSub.refKey = c._cachedReferenceInfoKey || refInfo.commitSha || "";
      wSub.viewer = state.viewer;
      wSub.dlScheme = state.downloadScheme || "repo";
      wSub.status = c.status;
      wSub.wip = c.work_in_progress;
      wSub.vAttn = c.viewer_in_attention_set;
      wSub.revItemsSig = revItemsSig;
      wSub.rLen = rLen;
      wSub.r0Em = r0 ? (r0.email || r0.name || "") : "";
      wSub.r0Cr = r0 ? (r0.cr_vote || 0) : 0;
      wSub.r0V = r0 ? (r0.v_vote || 0) : 0;
      wSub.r0At = Boolean(r0 && r0.in_attention_set);
      wSub.r1Em = r1 ? (r1.email || r1.name || "") : "";
      wSub.r1Cr = r1 ? (r1.cr_vote || 0) : 0;
      wSub.r1V = r1 ? (r1.v_vote || 0) : 0;
      wSub.r1At = Boolean(r1 && r1.in_attention_set);
      wSub.ccLen = ccLen;
      wSub.cc0Em = cc0 ? (cc0.email || cc0.name || "") : "";
      wSub.cc1Em = cc1 ? (cc1.email || cc1.name || "") : "";
      wSub.attnRef = attnArr;
      wSub.attnLen = attnArr ? attnArr.length : 0;
      wSub.tagsRef = tagsArr;
      wSub.tagsLen = tagsArr ? tagsArr.length : 0;
      wSub.bugsRef = bugsArr;
      wSub.bugsLen = bugsArr ? bugsArr.length : 0;
      wSub.srRef = srArr;
      wSub.srLen = srArr ? srArr.length : 0;
    }
    return panelHtml;
  }

  function renderSentChatDmDmOrEmptyHtml(c) {
    return `${renderSentChatDmBannerHtml(c)}${renderChatDmComposerHtml(c)}`;
  }

  function applyUpdatedSingleClToState(updatedCl) {
    if (!updatedCl || !updatedCl.number) return;
    const num = Number(updatedCl.number);
    state.changes = state.changes.map(c => {
      if (Number(c.number) !== num) return c;
      return Object.assign({}, c, updatedCl, {
        stack_index: c.stack_index,
        stack_total: c.stack_total,
        stack_label: c.stack_label,
      });
    });
    state.stacks.forEach(st => {
      st.commits = (st.commits || []).map(c => {
        if (Number(c.number) !== num) return c;
        return Object.assign({}, c, updatedCl, {
          stack_index: c.stack_index,
          stack_total: c.stack_total,
          stack_label: c.stack_label,
        });
      });
    });
  }

  function rerenderReviewersPanelDom(clNum) {
    const num = Number(clNum);
    const panelEl = document.getElementById(`reviewersPanel-${num}`);
    const clObj = state.changes.find(x => Number(x.number) === num);
    if (panelEl && clObj) {
      const hasPrefix = panelEl.getAttribute("data-has-prefix") === "1";
      panelEl.outerHTML = renderCommitReviewersPanelHtml(clObj, hasPrefix);
      const hdrEl = document.getElementById(`pgChangeHeader-${num}`);
      if (hdrEl) {
        hdrEl.outerHTML = renderPgChangeHeaderHtml(clObj, state.diffsByCl[num]);
        const bannerEl = document.getElementById(`pgRebaseNoticeBanner-${num}`);
        const newBannerHtml = renderPgRebaseNoticeBannerHtml(clObj, state.diffsByCl[num]);
        if (bannerEl) {
          if (newBannerHtml) {
            bannerEl.outerHTML = newBannerHtml;
          } else if (typeof bannerEl.remove === "function") {
            bannerEl.remove();
          } else if (bannerEl.parentNode && typeof bannerEl.parentNode.removeChild === "function") {
            bannerEl.parentNode.removeChild(bannerEl);
          }
        } else if (newBannerHtml) {
          const freshHdr = document.getElementById(`pgChangeHeader-${num}`);
          if (freshHdr && typeof freshHdr.insertAdjacentHTML === "function") {
            freshHdr.insertAdjacentHTML("afterend", newBannerHtml);
          }
        }
      }
      const rowSlot = document.getElementById(`presubmitRowSlot-${num}`);
      if (rowSlot) {
        rowSlot.innerHTML = renderCommitPresubmitRowHtml(clObj);
      }
      const drawerSlot = document.getElementById(`presubmitDrawerSlot-${num}`);
      if (drawerSlot) {
        drawerSlot.innerHTML = renderPresubmitTargetsDrawerHtml(clObj);
      }
      const relSlot = document.getElementById(`pgRelationSlot-${num}`);
      if (relSlot) {
        const relHtml = renderPgRelationChainHtml(clObj);
        relSlot.innerHTML = relHtml;
        relSlot.style.display = relHtml ? "" : "none";
      }
      const replyPartsEl = document.getElementById(`pgReplyParticipantsSection-${num}`);
      if (replyPartsEl) {
        replyPartsEl.innerHTML = renderPgReplyParticipantsInnerHtml(clObj);
      }
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
      renderBatchReviewerPopoverDom();
      syncSwarmIndicatorsOnly();
    }
    const summary = getFilteredStacksSummary();
    renderQueueList(summary.visibleStacks, summary);
  }

  function getFilteredSuggestionsForCl(clNum, targetState, rawSuggestions) {
    const clObj = state.changes.find(x => Number(x.number) === Number(clNum));
    const existingSameState = new Set();
    if (clObj) {
      const list = targetState === "CC" ? (clObj.ccs_detail || []) : getFallbackReviewersForCl(clObj);
      list.forEach(p => {
        if (p.email) existingSameState.add(p.email.toLowerCase());
      });
    }
    return (rawSuggestions || []).filter(s => !existingSameState.has((s.email || s.value || "").toLowerCase()));
  }

  async function fetchReviewerSuggestions(clNum, targetState, queryStr) {
    const comp = state.activeReviewerComposer;
    if (!comp || Number(comp.clNum) !== Number(clNum) || comp.targetState !== targetState) return;
    const reqSeq = (comp.reqSeq || 0) + 1;
    comp.reqSeq = reqSeq;
    comp.loading = true;
    try {
      const resp = await sidecarFetch(
        `/api/gerrit/suggest-reviewers?cl=${encodeURIComponent(clNum)}&q=${encodeURIComponent(queryStr || "")}`
      );
      const data = await resp.json();
      const cur = state.activeReviewerComposer;
      if (!cur || cur.reqSeq !== reqSeq) return;
      cur.loading = false;
      cur.suggestions = getFilteredSuggestionsForCl(clNum, targetState, data.suggestions || []);
      cur.activeIdx = 0;
      const menuId = cur.context ? `reviewerSuggestMenu-${cur.context}-${clNum}-${targetState}` : `reviewerSuggestMenu-${clNum}-${targetState}`;
      const menuEl = document.getElementById(menuId) || document.getElementById(`reviewerSuggestMenu-${clNum}-${targetState}`);
      if (menuEl) {
        menuEl.innerHTML = renderSuggestMenuInnerHtml(clNum, targetState, cur);
      }
    } catch (_err) {
      if (state.activeReviewerComposer && state.activeReviewerComposer.reqSeq === reqSeq) {
        state.activeReviewerComposer.loading = false;
      }
    }
  }

  function openReviewerComposer(clNum, targetState, context = "") {
    if ((state.activeReviewerComposer && state.activeReviewerComposer.submitting) ||
        (state.activeChatComposer && state.activeChatComposer.submitting)) {
      return;
    }
    const num = Number(clNum);
    const ctx = context || "";
    const prevChatCl = state.activeChatComposer ? Number(state.activeChatComposer.clNum) : null;
    const prevRevCl = state.activeReviewerComposer ? Number(state.activeReviewerComposer.clNum) : null;
    state.activeChatComposer = null;
    const initialRoster = (state.roster || []).map(em => ({
      value: em,
      email: em,
      name: em.split("@")[0],
      short: em.split("@")[0],
      is_group: false,
    }));
    state.activeReviewerComposer = {
      clNum: num,
      targetState,
      context: ctx,
      query: "",
      suggestions: getFilteredSuggestionsForCl(num, targetState, initialRoster),
      activeIdx: 0,
      loading: false,
      submitting: false,
      reqSeq: 0,
    };
    if (prevChatCl && prevChatCl !== num) rerenderReviewersPanelDom(prevChatCl);
    if (prevRevCl && prevRevCl !== num && prevRevCl !== prevChatCl) rerenderReviewersPanelDom(prevRevCl);
    rerenderReviewersPanelDom(num);
    const inputId = ctx ? `reviewerInput-${ctx}-${num}-${targetState}` : `reviewerInput-${num}-${targetState}`;
    const inp = document.getElementById(inputId) || document.getElementById(`reviewerInput-${num}-${targetState}`);
    if (inp) inp.focus();
    fetchReviewerSuggestions(num, targetState, "");
  }

  function closeReviewerComposer() {
    const prev = state.activeReviewerComposer;
    state.activeReviewerComposer = null;
    if (prev && prev.clNum) {
      rerenderReviewersPanelDom(prev.clNum);
    }
  }

  function onReviewerInputChanged(clNum, targetState, val) {
    const comp = state.activeReviewerComposer;
    if (!comp || Number(comp.clNum) !== Number(clNum) || comp.targetState !== targetState) return;
    comp.query = val;
    clearTimeout(window._revSuggestTimer);
    window._revSuggestTimer = setTimeout(() => {
      fetchReviewerSuggestions(clNum, targetState, val);
    }, 140);
  }

  function onReviewerInputKeydown(event, clNum, targetState) {
    const comp = state.activeReviewerComposer;
    if (!comp) return;
    const items = comp.suggestions || [];
    const menuId = comp.context ? `reviewerSuggestMenu-${comp.context}-${clNum}-${targetState}` : `reviewerSuggestMenu-${clNum}-${targetState}`;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeReviewerComposer();
      return;
    }
    if (event.key === "ArrowDown" && items.length > 0) {
      event.preventDefault();
      event.stopPropagation();
      comp.activeIdx = (comp.activeIdx + 1) % items.length;
      const menuEl = document.getElementById(menuId) || document.getElementById(`reviewerSuggestMenu-${clNum}-${targetState}`);
      if (menuEl) menuEl.innerHTML = renderSuggestMenuInnerHtml(clNum, targetState, comp);
      return;
    }
    if (event.key === "ArrowUp" && items.length > 0) {
      event.preventDefault();
      event.stopPropagation();
      comp.activeIdx = (comp.activeIdx - 1 + items.length) % items.length;
      const menuEl = document.getElementById(menuId) || document.getElementById(`reviewerSuggestMenu-${clNum}-${targetState}`);
      if (menuEl) menuEl.innerHTML = renderSuggestMenuInnerHtml(clNum, targetState, comp);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      const typed = (comp.query || "").trim();
      const picked = items[comp.activeIdx];
      const chosen = (picked && (!typed || (picked.email || picked.value).toLowerCase().includes(typed.toLowerCase()) || (picked.name || "").toLowerCase().includes(typed.toLowerCase())))
        ? picked.value
        : typed;
      submitReviewerAdd(clNum, targetState, chosen);
    }
  }

  async function submitReviewerAdd(clNum, targetState, explicitValue, btnEl) {
    const num = Number(clNum);
    const comp = state.activeReviewerComposer;
    const rawVal = String(explicitValue != null ? explicitValue : (comp ? comp.query : "")).trim();
    if (!rawVal) {
      showToast("Enter a name, LDAP, or email to add.");
      return;
    }
    if (comp && Number(comp.clNum) === num && comp.targetState === targetState) {
      comp.submitting = true;
      rerenderReviewersPanelDom(num);
    } else if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = `<span class="btn-spinner"></span>`;
    }
    const roleLabel = targetState === "CC" ? "CC" : "reviewer";
    try {
      const resp = await sidecarFetch("/api/gerrit/reviewer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: num, action: "add", account: rawVal, state: targetState, viewer: state.viewer }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(
          data.error ||
          `HTTP ${resp.status} while setting ${roleLabel} '${rawVal}' on ag/${num} (check account email/LDAP and gcert)`
        );
      }
      state.activeReviewerComposer = null;
      invalidateClientQueueCache();
      if (data.change) {
        applyUpdatedSingleClToState(data.change);
      } else {
        await fetchQueueData(state.viewer, state.scope, { preserveDiffCache: true, preserveScroll: true, silent: true });
      }
      rerenderReviewersPanelDom(num);
      showToast(`Added ${data.reviewer} as ${targetState} on ag/${num} (notify: OWNER)!`);
    } catch (err) {
      if (state.activeReviewerComposer && Number(state.activeReviewerComposer.clNum) === num) {
        state.activeReviewerComposer.submitting = false;
        rerenderReviewersPanelDom(num);
      }
      showToast(`Could not set ${roleLabel} '${rawVal}' on ag/${num}: ${err.message}`);
    }
  }

  async function moveParticipantState(clNum, accountKey, newState, displayLabel, btnEl) {
    await submitReviewerAdd(clNum, newState, accountKey, btnEl);
  }

  async function removeParticipantFromCl(clNum, accountKey, displayLabel, btnEl) {
    const num = Number(clNum);
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = `<span class="btn-spinner" style="width:10px;height:10px;border-width:1.5px;"></span>`;
    }
    try {
      const resp = await sidecarFetch("/api/gerrit/reviewer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: num, action: "remove", account: accountKey, viewer: state.viewer }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(data.error || "Failed to remove reviewer");
      }
      invalidateClientQueueCache();
      if (data.change) {
        applyUpdatedSingleClToState(data.change);
      } else {
        await fetchQueueData(state.viewer, state.scope, { preserveDiffCache: true, preserveScroll: true, silent: true });
      }
      rerenderReviewersPanelDom(num);
      showToast(`Removed ${displayLabel || accountKey} from ag/${num} (notify: OWNER)!`);
    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = "×";
      }
      showToast("Could not remove reviewer: " + err.message);
    }
  }

  function renderBatchSuggestMenuInnerHtml(comp) {
    return renderReviewerSuggestMenuHtml(
      comp,
      escapedVal => `submitBatchReviewerAdd('${escapedVal}')`
    );
  }

  function renderBatchReviewerPopoverDom() {
    const popEl = document.getElementById("batchReviewerPopover");
    if (!popEl) return;
    const comp = state.batchReviewerComposer;
    if (!comp || !state.checkedClNumbers || state.checkedClNumbers.size === 0) {
      if (!popEl._isHiddenEmpty) {
        popEl.style.display = "none";
        popEl.innerHTML = "";
        popEl._isHiddenEmpty = true;
      }
      return;
    }
    const checkedNums = getOrderedCheckedClNumbers();
    if (checkedNums.length === 0) {
      if (!popEl._isHiddenEmpty) {
        popEl.style.display = "none";
        popEl.innerHTML = "";
        popEl._isHiddenEmpty = true;
      }
      return;
    }
    const isCc = comp.targetState === "CC";
    const roleTitle = isCc ? "Add CC" : "Add Reviewer";
    const ph = isCc ? "CC LDAP, name, or email…" : "Reviewer LDAP, name, or email…";
    const btnHtml = comp.submitting
      ? `<span class="btn-spinner"></span> Adding…`
      : `${roleTitle} (${checkedNums.length})`;
    popEl._isHiddenEmpty = false;
    popEl.style.display = "block";
    popEl.innerHTML = `<div class="pg-batch-popover-inner" style="display:flex;flex-direction:column;gap:8px;" onclick="event.stopPropagation()">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:11.5px;color:var(--g-muted);">
        <span><strong style="color:var(--g-text);">${roleTitle}</strong> across <strong>${checkedNums.length}</strong> checked CL${checkedNums.length === 1 ? "" : "s"} · notify: OWNER</span>
        <button type="button" class="pg-btn-flat pg-batch-btn-popover-close nudge-btn" ${comp.submitting ? "disabled" : ""} onclick="closeBatchReviewerComposer()" title="Cancel (Esc)">✕</button>
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        <input
          id="batchReviewerInput"
          class="reviewer-add-input pg-batch-reviewer-input"
          style="flex:1;width:auto;"
          type="text"
          value="${escapeHtml(comp.query || "")}"
          placeholder="${ph}"
          ${comp.submitting ? "disabled" : ""}
          oninput="onBatchReviewerInputChanged(this.value)"
          onkeydown="onBatchReviewerInputKeydown(event)"
        />
        <button type="button" class="pg-btn-primary pg-batch-btn-popover-submit nudge-btn" ${comp.submitting ? "disabled" : ""} onclick="submitBatchReviewerAdd(null)">${btnHtml}</button>
      </div>
      <div id="batchReviewerSuggestMenu" class="pg-batch-suggest-menu" style="max-height:190px;overflow-y:auto;background:var(--g-panel-alt);border:1px solid var(--g-border);border-radius:4px;padding:3px;">
        ${renderBatchSuggestMenuInnerHtml(comp)}
      </div>
    </div>`;
  }

  async function fetchBatchReviewerSuggestions(queryStr) {
    const comp = state.batchReviewerComposer;
    const checkedNums = getOrderedCheckedClNumbers();
    if (!comp || checkedNums.length === 0) return;
    const reqSeq = (comp.reqSeq || 0) + 1;
    comp.reqSeq = reqSeq;
    comp.loading = true;
    try {
      const resp = await sidecarFetch(
        `/api/gerrit/suggest-reviewers?cl=${encodeURIComponent(checkedNums[0])}&q=${encodeURIComponent(queryStr || "")}`
      );
      const data = await resp.json();
      const cur = state.batchReviewerComposer;
      if (!cur || cur.reqSeq !== reqSeq) return;
      cur.loading = false;
      cur.suggestions = data.suggestions || [];
      cur.activeIdx = 0;
      const menuEl = document.getElementById("batchReviewerSuggestMenu");
      if (menuEl) menuEl.innerHTML = renderBatchSuggestMenuInnerHtml(cur);
    } catch (_err) {
      if (state.batchReviewerComposer && state.batchReviewerComposer.reqSeq === reqSeq) {
        state.batchReviewerComposer.loading = false;
      }
    }
  }

  function openBatchReviewerComposer(targetState) {
    const checkedNums = getOrderedCheckedClNumbers();
    if (checkedNums.length === 0) return;
    const cur = state.batchReviewerComposer;
    if (cur && cur.submitting) return;
    if (cur && cur.targetState === targetState) {
      closeBatchReviewerComposer();
      return;
    }
    const initialRoster = (state.roster || []).map(em => ({
      value: em,
      email: em,
      name: em.split("@")[0],
      short: em.split("@")[0],
      is_group: false,
    }));
    state.batchReviewerComposer = {
      targetState: targetState === "CC" ? "CC" : "REVIEWER",
      query: "",
      suggestions: initialRoster,
      activeIdx: 0,
      loading: false,
      submitting: false,
      reqSeq: 0,
    };
    renderBatchReviewerPopoverDom();
    const inp = document.getElementById("batchReviewerInput");
    if (inp) inp.focus();
    fetchBatchReviewerSuggestions("");
  }

  function closeBatchReviewerComposer() {
    if (state.batchReviewerComposer && state.batchReviewerComposer.submitting) return;
    state.batchReviewerComposer = null;
    const popEl = document.getElementById("batchReviewerPopover");
    if (popEl) {
      popEl.style.display = "none";
      popEl.innerHTML = "";
    }
  }

  function onBatchReviewerInputChanged(val) {
    const comp = state.batchReviewerComposer;
    if (!comp) return;
    comp.query = val;
    clearTimeout(window._batchRevSuggestTimer);
    window._batchRevSuggestTimer = setTimeout(() => {
      fetchBatchReviewerSuggestions(val);
    }, 140);
  }

  function onBatchReviewerInputKeydown(event) {
    const comp = state.batchReviewerComposer;
    if (!comp) return;
    const items = comp.suggestions || [];
    if (event.key === "Escape") {
      event.preventDefault();
      closeBatchReviewerComposer();
      return;
    }
    if (event.key === "ArrowDown" && items.length > 0) {
      event.preventDefault();
      comp.activeIdx = (comp.activeIdx + 1) % items.length;
      const menuEl = document.getElementById("batchReviewerSuggestMenu");
      if (menuEl) menuEl.innerHTML = renderBatchSuggestMenuInnerHtml(comp);
      return;
    }
    if (event.key === "ArrowUp" && items.length > 0) {
      event.preventDefault();
      comp.activeIdx = (comp.activeIdx - 1 + items.length) % items.length;
      const menuEl = document.getElementById("batchReviewerSuggestMenu");
      if (menuEl) menuEl.innerHTML = renderBatchSuggestMenuInnerHtml(comp);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const typed = (comp.query || "").trim();
      const picked = items[comp.activeIdx];
      const matchesTyped = picked && (
        !typed ||
        (picked.email || picked.value || "").toLowerCase().includes(typed.toLowerCase()) ||
        (picked.name || "").toLowerCase().includes(typed.toLowerCase())
      );
      submitBatchReviewerAdd(matchesTyped ? picked.value : typed);
    }
  }

  async function submitBatchReviewerAdd(explicitValue) {
    const comp = state.batchReviewerComposer;
    const checkedNums = getOrderedCheckedClNumbers();
    if (!comp || comp.submitting || checkedNums.length === 0) return;
    const rawVal = String(explicitValue != null ? explicitValue : (comp.query || "")).trim();
    if (!rawVal) {
      showToast("Enter a name, LDAP, or email to add.");
      return;
    }
    const targetState = comp.targetState;
    const roleLabel = targetState === "CC" ? "CC" : "reviewer";
    comp.submitting = true;
    renderBatchReviewerPopoverDom();
    try {
      const resp = await sidecarFetch("/api/gerrit/reviewer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cls: checkedNums,
          action: "add",
          account: rawVal,
          state: targetState,
          viewer: state.viewer,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(
          data.error ||
          `HTTP ${resp.status} while batch-adding ${roleLabel} '${rawVal}' across ${checkedNums.length} CLs (check LDAP/email and gcert)`
        );
      }
      state.batchReviewerComposer = null;
      renderBatchReviewerPopoverDom();
      invalidateClientQueueCache();
      if (Array.isArray(data.changes) && data.changes.length > 0) {
        data.changes.forEach(updated => applyUpdatedSingleClToState(updated));
      } else if (data.change) {
        applyUpdatedSingleClToState(data.change);
      } else {
        await fetchQueueData(state.viewer, state.scope, { preserveDiffCache: true, preserveScroll: true, silent: true });
      }
      if (state.selectedClNumber) {
        rerenderReviewersPanelDom(state.selectedClNumber);
      } else {
        const summary = getFilteredStacksSummary();
        renderQueueList(summary.visibleStacks, summary);
      }
      syncQueueSelectionDom();
      showToast(`Added ${data.reviewer || rawVal} as ${targetState} on ${checkedNums.length} CL${checkedNums.length === 1 ? "" : "s"} (notify: OWNER)!`);
    } catch (err) {
      if (state.batchReviewerComposer) {
        state.batchReviewerComposer.submitting = false;
        renderBatchReviewerPopoverDom();
      }
      showToast(`Batch ${roleLabel} failed: ${err.message}`);
    }
  }

  function getChatSenderEmail() {
    return String(state.authEmail || state.viewer || "").trim().toLowerCase();
  }

  function isDmEligibleParticipant(p) {
    if (!p || p.is_bot || !p.email) return false;
    const em = String(p.email).trim().toLowerCase();
    return em.endsWith("@google.com") && em !== getChatSenderEmail();
  }

  function getDmEligibleParticipantsForCl(c) {
    if (!c) return [];
    const seen = new Set();
    const out = [];
    const pushIfEligible = (p) => {
      if (!isDmEligibleParticipant(p)) return;
      const em = String(p.email).trim().toLowerCase();
      if (seen.has(em)) return;
      seen.add(em);
      out.push(p);
    };
    const revs = getFallbackReviewersForCl(c);
    revs.filter(p => !p.is_owner && Number(p.cr_vote || 0) < 2).forEach(pushIfEligible);
    revs.filter(p => !p.is_owner && Number(p.cr_vote || 0) >= 2).forEach(pushIfEligible);
    (c.ccs_detail || []).forEach(pushIfEligible);
    revs.filter(p => p.is_owner).forEach(pushIfEligible);
    if (!c.owner_is_bot && c.owner_email) {
      pushIfEligible({
        email: c.owner_email,
        short: c.owner_short || c.owner_email.split("@")[0],
        name: c.owner_name || c.owner_short,
        full_name: c.owner_name || c.owner_short,
        state: "OWNER",
        is_bot: false,
        is_owner: true,
        cr_vote: 0,
      });
    }
    return out;
  }

  function resolveGreetingForParticipant(p) {
    if (!p) return "Hey";
    const full = String(p.full_name || p.name || "").trim();
    const short = String(p.short || "").trim();
    if (!full || full.includes("@") || full.includes(",") || full.toLowerCase() === short.toLowerCase()) {
      return "Hey";
    }
    const firstToken = full.split(/\s+/)[0];
    return firstToken ? `Hi ${firstToken}` : "Hey";
  }

  function buildDefaultChatDmMessage(c, p) {
    const greeting = resolveGreetingForParticipant(p);
    const subj = String(c.subject || "").trim();
    const subjPart = subj ? ` ("${subj}")` : "";
    return `${greeting}, could you take a quick look at http://ag/${c.number}${subjPart} when you get a chance? Thanks!`;
  }

  function renderSentChatDmBannerHtml(c) {
    const prefix = `${c.number}:`;
    const entries = Object.entries(state.sentChatDms)
      .filter(([k, val]) => k.startsWith(prefix) && val && !val.dismissed);
    if (entries.length === 0) return "";
    const [latestKey, info] = entries[entries.length - 1];
    const openLinkHtml = info.chatUrl
      ? `<a class="ag-link" href="${escapeHtml(info.chatUrl)}" target="_blank" rel="noopener noreferrer">Open 1:1 Chat ↗</a>`
      : "";
    return `<div class="chat-dm-sent-banner" onclick="event.stopPropagation()">
      <span>✓ Sent Google Chat DM to <strong>${escapeHtml(info.recipient)}</strong> at ${escapeHtml(info.sentAtStr)}</span>
      <span style="display:inline-flex;align-items:center;gap:8px;">
        ${openLinkHtml}
        <button class="participant-act-btn" data-key="${escapeHtml(latestKey)}" title="Dismiss" onclick="dismissSentChatBanner(${c.number}, this.dataset.key)">✕</button>
      </span>
    </div>`;
  }

  function renderChatDmComposerHtml(c) {
    const comp = state.activeChatComposer;
    if (!comp || Number(comp.clNum) !== Number(c.number)) return "";
    const eligible = getDmEligibleParticipantsForCl(c);
    const pillsHtml = eligible.map(p => {
      const em = String(p.email).toLowerCase();
      const isAct = em === String(comp.recipientEmail || "").toLowerCase();
      const v = Number(p.cr_vote || 0);
      const roleTag = p.is_owner ? "owner" : (p.state === "CC" ? "CC" : `CR ${v > 0 ? "+" + v : v}`);
      return `<button class="chat-dm-recipient-pill ${isAct ? "active" : ""}" data-email="${escapeHtml(p.email)}" ${comp.submitting ? "disabled" : ""} onclick="selectChatDmRecipient(${c.number}, this.dataset.email)">
        👤 ${escapeHtml(p.short)} <span style="opacity:0.75;">(${escapeHtml(roleTag)})</span>
      </button>`;
    }).join("");
    const sendBtnInner = comp.submitting
      ? `<span class="btn-spinner"></span> Sending DM…`
      : `💬 Send Chat DM`;
    const errHtml = comp.error
      ? `<div class="chat-dm-error">${escapeHtml(comp.error)}</div>`
      : "";
    return `<div class="chat-dm-composer" onclick="event.stopPropagation()">
      <div class="chat-dm-header">
        <span>💬 Google Chat 1:1 Direct Message · <strong>${escapeHtml(comp.recipientEmail)}</strong></span>
        <span style="font-size:10.5px;color:var(--g-muted);font-weight:500;">⌘/Ctrl+Enter to send · Esc to cancel</span>
      </div>
      <div class="chat-dm-recipients">${pillsHtml}</div>
      <textarea
        id="chatDmTextarea-${c.number}"
        class="chat-dm-textarea"
        rows="2"
        ${comp.submitting ? "disabled" : ""}
        oninput="onChatDmTextChanged(${c.number}, this.value)"
        onkeydown="onChatDmKeydown(event, ${c.number})"
      >${escapeHtml(comp.message || "")}</textarea>
      ${errHtml}
      <div class="chat-dm-actions">
        <div style="display:inline-flex;align-items:center;gap:6px;">
          <button class="chat-dm-btn-primary" ${comp.submitting ? "disabled" : ""} onclick="submitChatDm(${c.number})">${sendBtnInner}</button>
          <button class="nudge-btn" ${comp.submitting ? "disabled" : ""} onclick="copyChatDmComposerText(${c.number})">Copy Text</button>
        </div>
        <button class="nudge-btn" ${comp.submitting ? "disabled" : ""} onclick="closeChatDmComposer()">Cancel</button>
      </div>
    </div>`;
  }

  function openChatDmComposer(clNum, explicitEmail) {
    if ((state.activeChatComposer && state.activeChatComposer.submitting) ||
        (state.activeReviewerComposer && state.activeReviewerComposer.submitting)) {
      showToast("Wait for the current action to finish before opening a new Chat DM.");
      return;
    }
    const num = Number(clNum);
    const clObj = state.changes.find(x => Number(x.number) === num);
    if (!clObj) return;
    const eligible = getDmEligibleParticipantsForCl(clObj);
    if (eligible.length === 0) {
      showToast(`No other @google.com human reviewers on ag/${num} to DM.`);
      return;
    }
    const wanted = String(explicitEmail || "").trim().toLowerCase();
    const target = eligible.find(p => String(p.email).toLowerCase() === wanted) || eligible[0];
    const prevClNum = state.activeChatComposer ? Number(state.activeChatComposer.clNum) : null;
    const prevRevClNum = state.activeReviewerComposer ? Number(state.activeReviewerComposer.clNum) : null;
    state.activeReviewerComposer = null;
    state.activeChatComposer = {
      clNum: num,
      recipientEmail: target.email,
      message: buildDefaultChatDmMessage(clObj, target),
      userEdited: false,
      submitting: false,
      error: "",
    };
    if (prevClNum && prevClNum !== num) rerenderReviewersPanelDom(prevClNum);
    if (prevRevClNum && prevRevClNum !== num && prevRevClNum !== prevClNum) {
      rerenderReviewersPanelDom(prevRevClNum);
    }
    rerenderReviewersPanelDom(num);
    const area = document.getElementById(`chatDmTextarea-${num}`);
    if (area) {
      area.focus();
      area.setSelectionRange(area.value.length, area.value.length);
    }
  }

  function selectChatDmRecipient(clNum, email) {
    const comp = state.activeChatComposer;
    const num = Number(clNum);
    if (!comp || Number(comp.clNum) !== num || comp.submitting) return;
    const clObj = state.changes.find(x => Number(x.number) === num);
    const eligible = getDmEligibleParticipantsForCl(clObj);
    const target = eligible.find(p => String(p.email).toLowerCase() === String(email || "").toLowerCase());
    if (!target) return;
    comp.recipientEmail = target.email;
    comp.error = "";
    if (!comp.userEdited && clObj) {
      comp.message = buildDefaultChatDmMessage(clObj, target);
    }
    rerenderReviewersPanelDom(num);
    const area = document.getElementById(`chatDmTextarea-${num}`);
    if (area) area.focus();
  }

  function onChatDmTextChanged(clNum, val) {
    const comp = state.activeChatComposer;
    if (!comp || Number(comp.clNum) !== Number(clNum)) return;
    comp.message = val;
    comp.userEdited = true;
    comp.error = "";
  }

  function onChatDmKeydown(event, clNum) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeChatDmComposer();
      return;
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submitChatDm(clNum);
    }
  }

  function closeChatDmComposer() {
    const prev = state.activeChatComposer;
    if (prev && prev.submitting) return;
    state.activeChatComposer = null;
    if (prev && prev.clNum) {
      rerenderReviewersPanelDom(prev.clNum);
    }
  }

  function copyChatDmComposerText(clNum) {
    const comp = state.activeChatComposer;
    if (!comp || Number(comp.clNum) !== Number(clNum)) return;
    navigator.clipboard?.writeText(comp.message || "");
    showToast(`Copied Chat message for ${comp.recipientEmail}!`);
  }

  function dismissSentChatBanner(clNum, mapKey) {
    if (mapKey && state.sentChatDms[mapKey]) {
      state.sentChatDms[mapKey].dismissed = true;
    }
    rerenderReviewersPanelDom(clNum);
  }

  async function submitChatDm(clNum) {
    const num = Number(clNum);
    const comp = state.activeChatComposer;
    if (!comp || Number(comp.clNum) !== num || comp.submitting) return;
    const msg = String(comp.message || "").trim();
    if (!msg) {
      comp.error = "Enter a message before sending.";
      rerenderReviewersPanelDom(num);
      return;
    }
    comp.submitting = true;
    comp.error = "";
    rerenderReviewersPanelDom(num);
    try {
      const resp = await sidecarFetch("/api/gerrit/chat-dm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: num, recipient: comp.recipientEmail, message: msg }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(data.error || `HTTP ${resp.status} while sending Google Chat DM`);
      }
      const recip = String(data.recipient || comp.recipientEmail).toLowerCase();
      state.sentChatDms[`${num}:${recip}`] = {
        recipient: recip,
        chatUrl: data.chat_url || "",
        sentAtStr: new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
        dismissed: false,
      };
      state.activeChatComposer = null;
      rerenderReviewersPanelDom(num);
      showToast(`Sent Google Chat DM to ${recip} for ag/${num}!`);
    } catch (err) {
      if (state.activeChatComposer && Number(state.activeChatComposer.clNum) === num) {
        state.activeChatComposer.submitting = false;
        state.activeChatComposer.error = err.message || String(err);
        rerenderReviewersPanelDom(num);
      }
      showToast("Could not send Chat DM: " + (err.message || err));
    }
  }

  const SIDECAR_UPDATE_POLL_INTERVAL_MS = 120000;
  const SIDECAR_RESTART_POLL_ATTEMPTS = 10;
  const SIDECAR_RESTART_POLL_DELAY_MS = 450;

  function formatDirtyFileKindLabel(kind) {
    if (kind === "staged") return "Staged";
    if (kind === "untracked_conflict") return "Untracked (conflicts)";
    return "Unstaged";
  }

  function buildUpdateButtonTooltip(st) {
    if (!st || !st.update_available) return "Fast Gerrit sidecar is up to date.";
    const lines = [];
    const pending = Array.isArray(st.pending_commits) ? st.pending_commits : [];
    if (pending.length > 0) {
      lines.push(`New Gerrit change(s) in hardware/google/aemu (${st.upstream_ref || "studio-main"}):`);
      pending.slice(0, 5).forEach(c => {
        lines.push(`• ${c.sha} ${c.subject}`);
      });
      if (Number(st.behind_count || 0) > pending.length) {
        lines.push(`…and ${Number(st.behind_count) - pending.length} more commit(s)`);
      }
    } else {
      const files = Array.isArray(st.changed_gerrit_files) ? st.changed_gerrit_files : [];
      lines.push(`Local hardware/google/aemu HEAD (${st.head_sha || "HEAD"}) has ${files.length || "new"} Gerrit file update(s) not yet installed in the sidecar.`);
    }
    if (st.has_unstaged_changes) {
      const dirtyCnt = Array.isArray(st.dirty_files) ? st.dirty_files.length : 1;
      lines.push(`⚠️ ${dirtyCnt} unstaged/local change(s) detected — clicking will warn before wiping.`);
    } else {
      lines.push("Click to pull & reinstall emu-dev-cli and the Gerrit sidecar.");
    }
    return lines.join("\n");
  }

  function syncUpdateAvailableButtonDom() {
    const btn = document.getElementById("btnUpdateAvailable");
    const lbl = document.getElementById("btnUpdateAvailableLabel");
    if (!btn || !lbl) return;
    const st = state.sidecarUpdateStatus;
    const isAvail = Boolean((st && st.update_available) || state.sidecarUpdateInFlight);
    btn.classList.toggle("visible", isAvail);
    btn.disabled = Boolean(state.sidecarUpdateInFlight || state.sidecarUpdateChecking);
    if (!isAvail) return;

    if (state.sidecarUpdateInFlight) {
      lbl.innerHTML = `<span class="btn-spinner"></span> Updating Sidecar…`;
      btn.title = "Pulling hardware/google/aemu and reinstalling emu-dev-cli + Gerrit sidecar…";
      return;
    }
    if (state.sidecarUpdateChecking) {
      lbl.innerHTML = `<span class="btn-spinner"></span> Checking…`;
      return;
    }
    const cnt = Number((st && st.behind_count) || 0);
    const cntSuffix = cnt > 1 ? ` (${cnt})` : "";
    const warnBadge = st && st.has_unstaged_changes
      ? ` <span class="update-dirty-dot" title="Unstaged local changes detected">⚠</span>`
      : "";
    lbl.innerHTML = `⬇ Update Available${escapeHtml(cntSuffix)}${warnBadge}`;
    btn.title = buildUpdateButtonTooltip(st);
  }

  async function checkSidecarUpdateStatus(opts = {}) {
    try {
      const qs = opts.force ? "?force=1" : "";
      const resp = await sidecarFetch(`/api/gerrit/update-status${qs}`);
      if (!resp.ok) return null;
      const data = await resp.json();
      if (!data || data.error) return null;
      state.sidecarUpdateStatus = data;
      syncUpdateAvailableButtonDom();
      return data;
    } catch (_err) {
      return null;
    }
  }

  function ensureSidecarUpdatePollingTimer() {
    if (window._sidecarUpdatePollTimer) return;
    window._sidecarUpdatePollTimer = setInterval(() => {
      if (!document.hidden && !state.sidecarUpdateInFlight) {
        checkSidecarUpdateStatus({ force: false });
      }
    }, SIDECAR_UPDATE_POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !state.sidecarUpdateInFlight) {
        checkSidecarUpdateStatus({ force: false });
      }
    });
  }

  function syncUpdateDirtyModalControls() {
    const cancelBtn = document.getElementById("btnCancelDirtyUpdate");
    const confirmBtn = document.getElementById("btnConfirmForceUpdate");
    const busy = Boolean(state.sidecarUpdateInFlight);
    if (cancelBtn) cancelBtn.disabled = busy;
    if (confirmBtn) {
      confirmBtn.disabled = busy;
      confirmBtn.innerHTML = busy
        ? `<span class="btn-spinner"></span> Wiping &amp; Updating…`
        : "Wipe Changes &amp; Update";
    }
  }

  function openUpdateDirtyWarningModal(dirtyFiles, repoPath) {
    const modal = document.getElementById("updateDirtyWarningModal");
    const repoEl = document.getElementById("updateDirtyWarningRepo");
    const listEl = document.getElementById("updateDirtyFilesList");
    if (!modal || !listEl) return;
    state.updateWarningModalOpen = true;
    if (repoEl) {
      repoEl.textContent = repoPath || "hardware/google/aemu";
    }
    const items = Array.isArray(dirtyFiles) ? dirtyFiles : [];
    listEl.innerHTML = items.map(item => {
      const p = typeof item === "string" ? item : (item.path || "");
      const kind = typeof item === "object" && item ? item.kind : "unstaged";
      return `<div class="update-dirty-file-row">
        <span>${escapeHtml(p)}</span>
        <span class="update-dirty-file-tag">${escapeHtml(formatDirtyFileKindLabel(kind))}</span>
      </div>`;
    }).join("");
    syncUpdateDirtyModalControls();
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    const cancelBtn = document.getElementById("btnCancelDirtyUpdate");
    if (cancelBtn) cancelBtn.focus();
  }

  function closeUpdateDirtyWarningModal() {
    if (state.sidecarUpdateInFlight) return;
    state.updateWarningModalOpen = false;
    const modal = document.getElementById("updateDirtyWarningModal");
    if (modal) {
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }
  }

  function onUpdateWarningBackdropClick(event) {
    if (event.target && event.target.id === "updateDirtyWarningModal") {
      closeUpdateDirtyWarningModal();
    }
  }

  async function waitForSidecarReadyAfterUpdate() {
    for (let i = 0; i < SIDECAR_RESTART_POLL_ATTEMPTS; i++) {
      await new Promise(resolve => setTimeout(resolve, SIDECAR_RESTART_POLL_DELAY_MS));
      const st = await checkSidecarUpdateStatus({ force: false });
      if (st && !st.update_available) {
        return st;
      }
    }
    return null;
  }

  async function executeSidecarUpdate(opts = {}) {
    if (state.sidecarUpdateInFlight) return;
    const forceDiscard = Boolean(opts.forceDiscardLocal);
    state.sidecarUpdateInFlight = true;
    syncUpdateAvailableButtonDom();
    syncUpdateDirtyModalControls();
    syncGlobalLoadingBar();
    try {
      const resp = await sidecarFetch("/api/gerrit/perform-update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force_discard_local: forceDiscard }),
      });
      const data = await resp.json();
      if (resp.status === 409 && data && data.status === "dirty_worktree") {
        state.sidecarUpdateInFlight = false;
        if (state.sidecarUpdateStatus) {
          state.sidecarUpdateStatus.has_unstaged_changes = true;
          state.sidecarUpdateStatus.dirty_files = data.dirty_files || [];
        }
        syncUpdateAvailableButtonDom();
        syncUpdateDirtyModalControls();
        syncGlobalLoadingBar();
        openUpdateDirtyWarningModal(data.dirty_files || [], data.repo_path || "");
        return;
      }
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || `HTTP ${resp.status} while updating Fast Gerrit sidecar`);
      }
      state.sidecarUpdateStatus = data.update_status || { update_available: false };
      state.sidecarUpdateInFlight = false;
      closeUpdateDirtyWarningModal();
      syncUpdateAvailableButtonDom();
      syncGlobalLoadingBar();
      const shaLabel = data.updated_head_sha ? ` (${data.updated_head_sha})` : "";
      showToast(`Updated Fast Gerrit sidecar${shaLabel}! Reloading UI…`);
      await waitForSidecarReadyAfterUpdate();
      window.location.reload();
    } catch (err) {
      const recovered = await waitForSidecarReadyAfterUpdate();
      if (recovered && !recovered.update_available) {
        state.sidecarUpdateInFlight = false;
        closeUpdateDirtyWarningModal();
        showToast("Updated Fast Gerrit sidecar! Reloading UI…");
        window.location.reload();
        return;
      }
      state.sidecarUpdateInFlight = false;
      syncUpdateAvailableButtonDom();
      syncUpdateDirtyModalControls();
      syncGlobalLoadingBar();
      showToast("Update failed: " + (err.message || err));
    }
  }

  async function onUpdateAvailableClick() {
    if (state.sidecarUpdateInFlight || state.sidecarUpdateChecking) return;
    state.sidecarUpdateChecking = true;
    syncUpdateAvailableButtonDom();
    const fresh = await checkSidecarUpdateStatus({ force: true });
    state.sidecarUpdateChecking = false;
    syncUpdateAvailableButtonDom();

    const activeStatus = fresh || state.sidecarUpdateStatus;
    if (activeStatus && !activeStatus.update_available) {
      showToast("Fast Gerrit sidecar is already up to date!");
      return;
    }
    const dirtyFiles = (activeStatus && Array.isArray(activeStatus.dirty_files))
      ? activeStatus.dirty_files
      : [];
    if (dirtyFiles.length > 0 || (activeStatus && activeStatus.has_unstaged_changes)) {
      openUpdateDirtyWarningModal(dirtyFiles, (activeStatus && activeStatus.repo_path) || "");
      return;
    }
    await executeSidecarUpdate({ forceDiscardLocal: false });
  }

  async function confirmWipeAndPerformSidecarUpdate() {
    await executeSidecarUpdate({ forceDiscardLocal: true });
  }

