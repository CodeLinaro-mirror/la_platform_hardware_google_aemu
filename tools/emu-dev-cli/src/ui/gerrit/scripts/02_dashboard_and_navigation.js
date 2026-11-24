  function collectUniqueCommitItems(visibleStacks) {
    const seen = new Set();
    const out = [];
    (visibleStacks || []).forEach(st => {
      (st.commits || []).forEach(c => {
        const num = Number(c && c.number);
        if (!num || seen.has(num)) return;
        seen.add(num);
        out.push({ commit: c, stack: st });
      });
    });
    return out;
  }

  function deduplicateOrderedItems(items) {
    const seen = new Set();
    const out = [];
    (items || []).forEach(item => {
      const num = Number(item && item.commit && item.commit.number);
      if (!num || seen.has(num)) return;
      seen.add(num);
      out.push(item);
    });
    return out;
  }

  let _lastDashboardSectionsSig = "";
  let _lastDashboardSectionsResult = null;
  const FNV1A_OFFSET_BASIS = 2166136261;
  const FNV1A_PRIME = 16777619;

  function fnv1aHashStr(h, str) {
    const s = String(str || "");
    for (let i = 0; i < s.length; i++) {
      h = Math.imul(h ^ s.charCodeAt(i), FNV1A_PRIME);
    }
    return h | 0;
  }

  function computeDashboardCommitPartHash(h, c) {
    const updatedVal = c.updated_ts || c.updated_iso || c.hours_idle || 0;
    const attnSet = c.attention_set;
    const attnLen = attnSet ? attnSet.length : -1;
    const firstAttn = (attnLen > 0 && attnSet[0])
      ? (attnSet[0].email || attnSet[0].user || (attnSet[0].account && (attnSet[0].account.email || attnSet[0].account.username)) || "")
      : "";
    const attnEmails = c.attention_set_emails;
    const attnEmailsLen = attnEmails ? attnEmails.length : -1;
    const firstAttnEmail = attnEmailsLen > 0 ? attnEmails[0] : "";
    const revs = c.reviewers_detail;
    const revLen = revs ? revs.length : -1;
    const ps = c._partSubCache || (c._partSubCache = {});
    if (
      ps.hash === undefined ||
      ps.viewer !== state.viewer ||
      ps.status !== c.status ||
      ps.merged !== c.is_merged ||
      ps.abandoned !== c.is_abandoned ||
      ps.wip !== c.work_in_progress ||
      ps.starred !== c.starred ||
      ps.viewerInAttn !== c.viewer_in_attention_set ||
      ps.attnSetRef !== attnSet ||
      ps.attnLen !== attnLen ||
      ps.firstAttn !== firstAttn ||
      ps.attnEmailsRef !== attnEmails ||
      ps.attnEmailsLen !== attnEmailsLen ||
      ps.firstAttnEmail !== firstAttnEmail ||
      ps.revsRef !== revs ||
      ps.revLen !== revLen ||
      ps.ownerEmail !== c.owner_email ||
      ps.isCc !== c.is_cc ||
      ps.isRev !== c.is_explicit_reviewer ||
      ps.updated !== updatedVal
    ) {
      const inAttn = isClInViewerAttentionSet(c) ? 1 : 0;
      ps.viewer = state.viewer;
      ps.status = c.status;
      ps.merged = c.is_merged;
      ps.abandoned = c.is_abandoned;
      ps.wip = c.work_in_progress;
      ps.starred = c.starred;
      ps.viewerInAttn = c.viewer_in_attention_set;
      ps.attnSetRef = attnSet;
      ps.attnLen = attnLen;
      ps.firstAttn = firstAttn;
      ps.attnEmailsRef = attnEmails;
      ps.attnEmailsLen = attnEmailsLen;
      ps.firstAttnEmail = firstAttnEmail;
      ps.revsRef = revs;
      ps.revLen = revLen;
      ps.inAttn = inAttn;
      ps.ownerEmail = c.owner_email;
      ps.isCc = c.is_cc;
      ps.isRev = c.is_explicit_reviewer;
      ps.updated = updatedVal;
      const flags = (c.is_merged ? 1 : 0) |
        (c.is_abandoned ? 2 : 0) |
        (c.work_in_progress ? 4 : 0) |
        (c.starred ? 8 : 0) |
        (inAttn ? 16 : 0) |
        (c.is_cc ? 32 : 0) |
        (c.is_explicit_reviewer ? 64 : 0);
      let ch = Math.imul(FNV1A_OFFSET_BASIS ^ (c.number | 0), FNV1A_PRIME);
      ch = Math.imul(ch ^ flags, FNV1A_PRIME);
      ch = fnv1aHashStr(ch, c.status);
      ch = fnv1aHashStr(ch, c.owner_email);
      ps.hash = fnv1aHashStr(ch, updatedVal);
    }
    return Math.imul(h ^ ps.hash, FNV1A_PRIME) | 0;
  }

  function computeDashboardPartitionSig(visibleStacks) {
    const savedList = state.savedSearches;
    if (Array.isArray(savedList)) {
      for (let i = 0; i < savedList.length; i++) {
        const sec = savedList[i];
        if (sec && sec.showOnDashboard && sec.query) return "";
      }
    }
    const list = visibleStacks || [];
    let h = FNV1A_OFFSET_BASIS | 0;
    let totalCommits = 0;
    for (let i = 0; i < list.length; i++) {
      const st = list[i];
      if (!st) continue;
      if (st._cachedStackIdStr !== st.stack_id) {
        st._cachedStackIdStr = st.stack_id;
        st._cachedStackIdHash = fnv1aHashStr(FNV1A_OFFSET_BASIS, st.stack_id);
      }
      h = Math.imul(h ^ st._cachedStackIdHash, FNV1A_PRIME) | 0;
      const commits = st.commits || [];
      totalCommits += commits.length;
      for (let j = 0; j < commits.length; j++) {
        const c = commits[j];
        if (c) h = computeDashboardCommitPartHash(h, c);
      }
    }
    return `${state.scope || ""}|${state.sortBy || "smart"}|${state.viewer || ""}|${hasActiveAuthorOrSearchFilter() ? 1 : 0}|${state.starredCls ? state.starredCls.size : 0}|${list.length}|${totalCommits}|${h >>> 0}`;
  }

  function partitionCommitsIntoDashboardSections(visibleStacks) {
    const partitionSig = computeDashboardPartitionSig(visibleStacks);
    if (partitionSig && partitionSig === _lastDashboardSectionsSig && _lastDashboardSectionsResult) {
      if (visibleStacks) {
        visibleStacks._cachedDashboardSections = _lastDashboardSectionsResult;
      }
      return _lastDashboardSectionsResult;
    }
    const uniqueItems = collectUniqueCommitItems(visibleStacks).sort(
      (a, b) => compareCommitsByUpdated(a.commit, b.commit)
    );
    const buckets = {
      your_turn: [],
      starred: [],
      wip: [],
      outgoing: [],
      incoming: [],
      cc: [],
      closed: [],
      team_other: [],
    };
    uniqueItems.forEach(item => {
      const c = item.commit;
      if (isClStarred(c)) {
        buckets.starred.push(item);
      }
      if (isClClosed(c)) {
        buckets.closed.push(item);
        return;
      }
      if (isClInViewerAttentionSet(c)) {
        buckets.your_turn.push(item);
      }
      if (isClOwnedByViewer(c)) {
        if (c.work_in_progress) buckets.wip.push(item);
        else buckets.outgoing.push(item);
        return;
      }
      if (!c.work_in_progress) {
        if (isClViewerCcOnly(c)) {
          buckets.cc.push(item);
          return;
        }
        if (isClIncomingForViewer(c)) {
          buckets.incoming.push(item);
          return;
        }
      }
      buckets.team_other.push(item);
    });
    const isDashScope = state.scope === "dashboard" && !hasActiveAuthorOrSearchFilter();
    const defs = [
      {
        id: "your_turn",
        title: "Your turn",
        items: buckets.your_turn,
        alwaysShow: isDashScope,
        showReviewers: false,
        isYourTurnSection: true,
        whoHeader: "Owner",
        ageHeader: "Waiting",
      },
      {
        id: "wip",
        title: "Work in progress",
        items: buckets.wip,
        alwaysShow: state.scope === "wip",
        showReviewers: true,
        isYourTurnSection: false,
        whoHeader: "Reviewers",
        ageHeader: "Updated",
      },
      {
        id: "outgoing",
        title: "Outgoing reviews",
        items: buckets.outgoing,
        alwaysShow: isDashScope || state.scope === "owner",
        showReviewers: true,
        isYourTurnSection: false,
        whoHeader: "Reviewers",
        ageHeader: "Updated",
      },
      {
        id: "incoming",
        title: "Incoming reviews",
        items: buckets.incoming,
        alwaysShow: isDashScope || state.scope === "reviewer",
        showReviewers: false,
        isYourTurnSection: false,
        whoHeader: "Owner",
        ageHeader: "Updated",
      },
      {
        id: "cc",
        title: "CCed on",
        items: buckets.cc,
        alwaysShow: state.scope === "cc",
        showReviewers: false,
        isYourTurnSection: false,
        whoHeader: "Owner",
        ageHeader: "Updated",
      },
      {
        id: "starred",
        title: "Starred changes",
        items: buckets.starred,
        alwaysShow: state.scope === "starred",
        showReviewers: false,
        isYourTurnSection: false,
        whoHeader: "Owner",
        ageHeader: "Updated",
      },
      {
        id: "closed",
        title: "Recently closed",
        items: buckets.closed,
        alwaysShow: state.scope === "closed",
        showReviewers: false,
        isYourTurnSection: false,
        whoHeader: "Owner",
        ageHeader: "Updated",
      },
    ];

    const customSections = (state.savedSearches || [])
      .filter(sec => sec && sec.showOnDashboard && sec.query)
      .map(sec => {
        const matchingItems = uniqueItems.filter(item => {
          try {
            return matchesSearchQuery(item.commit, sec.query);
          } catch (e) {
            return false;
          }
        });
        return {
          id: sec.id || `custom_${sec.title}`,
          title: sec.title || sec.query,
          query: sec.query,
          items: matchingItems,
          alwaysShow: false,
          showReviewers: Boolean(sec.showReviewers),
          isYourTurnSection: false,
          isCustomSection: true,
          whoHeader: sec.showReviewers ? "Reviewers" : "Owner",
          ageHeader: "Updated",
        };
      });
    defs.push(...customSections);

    defs.push({
      id: "team_other",
      title: "Open team changes",
      items: buckets.team_other,
      alwaysShow: false,
      showReviewers: false,
      isYourTurnSection: false,
      whoHeader: "Owner",
      ageHeader: "Updated",
    });
    const filteredDefs = defs.filter(sec => sec.alwaysShow || sec.items.length > 0);
    if (partitionSig) {
      _lastDashboardSectionsSig = partitionSig;
      _lastDashboardSectionsResult = filteredDefs;
    }
    if (visibleStacks) {
      visibleStacks._cachedDashboardSections = filteredDefs;
    }
    return filteredDefs;
  }

  function toggleDashboardSection(secId) {
    if (!secId) return;
    if (state.collapsedDashboardSections.has(secId)) {
      state.collapsedDashboardSections.delete(secId);
    } else {
      state.collapsedDashboardSections.add(secId);
    }
    const summary = getFilteredStacksSummary();
    renderQueueList(summary.visibleStacks, summary);
  }

  function getOrderedVisibleCommits(visibleStacks, precomputedSections) {
    const stacks = visibleStacks || getVisibleStacks();
    if (state.groupMode === "dashboard") {
      const sections = precomputedSections || partitionCommitsIntoDashboardSections(stacks);
      const collapsedSig = state.collapsedDashboardSections.size > 0
        ? Array.from(state.collapsedDashboardSections).join(",")
        : "";
      if (sections._cachedOrderedCollapsedSig === collapsedSig && Array.isArray(sections._cachedOrderedVisible)) {
        return sections._cachedOrderedVisible;
      }
      const expandedItems = [];
      const allItems = [];
      for (let sIdx = 0; sIdx < sections.length; sIdx++) {
        const sec = sections[sIdx];
        const isCollapsed = state.collapsedDashboardSections.has(sec.id);
        const secItems = sec.items;
        for (let iIdx = 0; iIdx < secItems.length; iIdx++) {
          const item = secItems[iIdx];
          allItems.push(item);
          if (!isCollapsed) expandedItems.push(item);
        }
      }
      const res = deduplicateOrderedItems(expandedItems.length > 0 ? expandedItems : allItems);
      sections._cachedOrderedCollapsedSig = collapsedSig;
      sections._cachedOrderedVisible = res;
      sections._cachedOrderedVisibleNums = res.map(item => Number(item.commit.number));
      return res;
    }
    if (state.groupMode === "type") {
      const allItems = collectUniqueCommitItems(stacks);
      if (state.sortBy !== "smart") {
        return allItems.sort((a, b) => compareCommitsByUpdated(a.commit, b.commit));
      }
      const ordered = [];
      COMMIT_TYPE_ORDER.forEach(t => {
        allItems.forEach(item => {
          if (getCommitTypeId(item.commit) === t.id) {
            ordered.push(item);
          }
        });
      });
      allItems.forEach(item => {
        if (!ordered.includes(item)) ordered.push(item);
      });
      return ordered;
    }
    const sortMode = state.sortBy || "smart";
    const sortedStacks = sortStacksIfNeeded(stacks);
    if (sortedStacks === stacks && Array.isArray(stacks)) {
      const cachedOrd = matchCachedStackOrderedItems(stacks, sortMode);
      if (cachedOrd !== null) return cachedOrd;
    }
    const ordered = [];
    for (let sIdx = 0; sIdx < sortedStacks.length; sIdx++) {
      const st = sortedStacks[sIdx];
      const commits = (st && st.commits) || _PG_EMPTY_ARR;
      for (let cIdx = 0; cIdx < commits.length; cIdx++) {
        ordered.push({ commit: commits[cIdx], stack: st });
      }
    }
    const deduped = deduplicateOrderedItems(ordered);
    if (sortedStacks === stacks && Array.isArray(stacks) && deduped.length === ordered.length) {
      stacks._stackOrdCache = { sortMode, ordered: deduped };
    }
    return deduped;
  }

  function matchCachedStackOrderedItems(stacks, sortMode) {
    const sc = stacks._stackOrdCache;
    if (!sc || sc.sortMode !== sortMode || !Array.isArray(sc.ordered)) return null;
    const ord = sc.ordered;
    const ordLen = ord.length;
    let k = 0;
    for (let sIdx = 0; sIdx < stacks.length; sIdx++) {
      const st = stacks[sIdx];
      const commits = (st && st.commits) || _PG_EMPTY_ARR;
      for (let cIdx = 0; cIdx < commits.length; cIdx++) {
        if (k >= ordLen) return null;
        const item = ord[k++];
        if (item.commit !== commits[cIdx] || item.stack !== st) return null;
      }
    }
    return k === ordLen ? ord : null;
  }

  function isSelectedVisibleInStacks(visibleStacks) {
    if (!state.selectedClNumber) return false;
    const targetNum = Number(state.selectedClNumber);
    if (visibleStacks === state.stacks) {
      return Boolean(findPgStackForClOrId(targetNum, state.selectedStackId));
    }
    return visibleStacks.some(st => st.commits.some(c => Number(c.number) === targetNum));
  }

  let _lastDomPoolCountsSig = "";
  let _lastPoolCountsCalcSig = "";
  let _lastPoolCountsResult = null;

  function computePoolCountsCacheKey(changes) {
    if (!Array.isArray(changes) || changes.length === 0) return "";
    const g = state.gates || {};
    if (g.agent) return "";
    const sq = state.searchQuery || "";
    if (sq && (sq.includes("comment:") || sq.includes("message:") || sq.includes("label:") || sq.includes("has:") || sq.includes("is:"))) {
      return "";
    }
    const starredSig = (state.starredCls && state.starredCls.size > 0)
      ? Array.from(state.starredCls).join(",")
      : "";
    let h = (0x811c9dc5 ^ changes.length) | 0;
    for (let i = 0; i < changes.length; i++) {
      const c = changes[i];
      if (!c) continue;
      h = computeCommitFilterReadOnlyHash(c, h);
    }
    return `${sq}|${state.authorFilter || "all"}|${state.includeUngatedMatches ? 1 : 0}|${state.viewer || ""}|${g.presubmit ? 1 : 0}${g.starredOnly ? 1 : 0}${g.hideMine ? 1 : 0}${g.hideSubmittable ? 1 : 0}|${starredSig}|${changes.length}:${h}`;
  }

  function renderAll(opts = {}) {
    const changes = state.changes || [];
    const poolCalcSig = computePoolCountsCacheKey(changes);
    let alreadyMineCount = 0;
    let submittableCount = 0;
    let starredCount = 0;
    let activePoolCount = 0;
    let typeCountsById = {};
    let typeSig = "";
    if (poolCalcSig && poolCalcSig === _lastPoolCountsCalcSig && _lastPoolCountsResult) {
      alreadyMineCount = _lastPoolCountsResult.alreadyMineCount;
      submittableCount = _lastPoolCountsResult.submittableCount;
      starredCount = _lastPoolCountsResult.starredCount;
      activePoolCount = _lastPoolCountsResult.activePoolCount;
      typeCountsById = _lastPoolCountsResult.typeCountsById;
      typeSig = _lastPoolCountsResult.typeSig;
    } else {
      const hasFilter = hasActiveAuthorOrSearchFilter();
      const useUngated = Boolean(hasFilter && state.includeUngatedMatches);
      for (let i = 0; i < changes.length; i++) {
        const c = changes[i];
        if (isClApprovedByViewer(c)) alreadyMineCount++;
        if (isClSubmittable(c)) submittableCount++;
        if (isClStarred(c)) starredCount++;
        if (!hasFilter || matchesAuthorAndSearch(c)) {
          if (useUngated || passesGates(c)) {
            activePoolCount++;
            const tid = getCommitTypeId(c);
            typeCountsById[tid] = (typeCountsById[tid] || 0) + 1;
          }
        }
      }
      for (let i = 0; i < COMMIT_TYPE_ORDER.length; i++) {
        const tid = COMMIT_TYPE_ORDER[i].id;
        typeSig += `${typeCountsById[tid] || 0},`;
      }
      if (poolCalcSig) {
        _lastPoolCountsCalcSig = poolCalcSig;
        _lastPoolCountsResult = {
          alreadyMineCount,
          submittableCount,
          starredCount,
          activePoolCount,
          typeCountsById,
          typeSig,
        };
      }
    }
    const domPoolSig = `${state.gates.hideMine ? 1 : 0}|${alreadyMineCount}|${activePoolCount}|${typeSig}`;
    if (_lastDomPoolCountsSig !== domPoolSig) {
      _lastDomPoolCountsSig = domPoolSig;
      const bottomCntEl = document.getElementById("bottomHiddenCount");
      if (bottomCntEl) {
        bottomCntEl.textContent = `${alreadyMineCount} CLs auto-hidden`;
      }
      const doneBucketEl = document.getElementById("collapsedDoneBucket");
      if (doneBucketEl) {
        doneBucketEl.style.display = (state.gates.hideMine && alreadyMineCount > 0) ? "flex" : "none";
      }
      const cntAllEl = document.getElementById("cntType_all");
      if (cntAllEl) {
        cntAllEl.textContent = activePoolCount;
      }
      for (let i = 0; i < COMMIT_TYPE_ORDER.length; i++) {
        const t = COMMIT_TYPE_ORDER[i];
        const cntEl = document.getElementById(`cntType_${t.id}`);
        if (cntEl) {
          cntEl.textContent = typeCountsById[t.id] || 0;
        }
      }
    }

    renderAuthorFilterControls(poolCalcSig);
    syncSearchClearButton();
    renderPgSearchPresetsDom();
    syncGateControls(alreadyMineCount, submittableCount, starredCount);

    const summary = getFilteredStacksSummary(poolCalcSig);
    const visibleStacks = summary.visibleStacks;
    const orderedVisible = getOrderedVisibleCommits(visibleStacks);
    summary.orderedVisible = orderedVisible;
    state.visibleCommitCount = orderedVisible.length;
    syncSidebarLayoutDom();
    const selectedStillExists = Boolean(
      state.selectedClNumber &&
      findPgChangeByNumber(state.selectedClNumber)
    );
    if (state.explicitRadarSelection && !selectedStillExists) {
      state.explicitRadarSelection = false;
    }
    if (!isSelectedVisibleInStacks(visibleStacks) && !state.explicitRadarSelection) {
      if (orderedVisible.length > 0) {
        state.selectedStackId = orderedVisible[0].stack.stack_id;
        state.selectedClNumber = orderedVisible[0].commit.number;
      } else {
        state.selectedStackId = null;
        state.selectedClNumber = null;
      }
    }
    const qContainer = document.getElementById("queueList");
    const prevQueueScroll = (opts.preserveScroll && qContainer) ? qContainer.scrollTop : null;
    renderQueueList(visibleStacks, summary);
    if (prevQueueScroll !== null && qContainer) {
      qContainer.scrollTop = prevQueueScroll;
    }
    state._renderPassOrderedVisible = orderedVisible;
    ensureActiveDiffsLoaded(opts);
    state._renderPassOrderedVisible = null;
  }

  const PRESUBMIT_BADGE_CLASS_MAP = {
    passed: "pv-passed",
    warning: "pv-warning",
    failed: "pv-failed",
    running: "pv-running",
    queued: "pv-queued",
    not_started: "pv-idle",
  };

  function resolveFallbackPresubmitStatusAndLabel(c) {
    const score = Number((c && c.presubmit_score) || 0);
    if (c && c.presubmit_ready_enabled) return { status: "queued", label: "⏳ PV Queued" };
    if ((c && c.presubmit_vetted) || score >= 2) return { status: "passed", label: `✓ PV +${score || 2}` };
    if (score === 1) return { status: "warning", label: "⚠ PV +1" };
    if (score < 0) return { status: "failed", label: `✗ PV ${score}` };
    return { status: "not_started", label: "⏸ PV 0" };
  }

  function getClPresubmitDisplay(c) {
    const targetEntry = c ? state.presubmitTargetsByCl[Number(c.number)] : null;
    const tData = targetEntry && targetEntry.data ? targetEntry.data : null;
    if (
      c &&
      typeof c === "object" &&
      c._cachedPvDisplay &&
      c._pvStatus === c.presubmit_status &&
      c._pvScore === c.presubmit_score &&
      c._pvReady === c.presubmit_ready_enabled &&
      c._pvVetted === c.presubmit_vetted &&
      c._pvLabel === c.presubmit_label &&
      c._pvSummary === c.presubmit_summary &&
      c._pvWorkplanId === c.workplan_id &&
      c._pvWorkplanUrl === c.workplan_url &&
      c._pvChecksUrl === c.checks_tab_url &&
      c._pvTDataRef === tData &&
      (!tData || (
        c._pvTRunStatus === tData.run_status &&
        c._pvTTotal === (tData.counts && tData.counts.total) &&
        c._pvTRunning === (tData.counts && tData.counts.running) &&
        c._pvTQueued === (tData.counts && tData.counts.queued) &&
        c._pvTWorkplanId === tData.workplan_id &&
        c._pvTWorkplanUrl === tData.workplan_url &&
        c._pvTChecksUrl === tData.checks_tab_url
      ))
    ) {
      return c._cachedPvDisplay;
    }
    const fb = resolveFallbackPresubmitStatusAndLabel(c);
    let status = (c && c.presubmit_status) || fb.status;
    let label = String((c && c.presubmit_label) || fb.label).replace(/\bPresubmit\b/g, "PV");
    if (tData && tData.run_status === "running" && (status === "queued" || status === "not_started")) {
      status = "running";
      label = "⏳ PV Running";
    }
    const badgeCls = PRESUBMIT_BADGE_CLASS_MAP[status] || "pv-idle";
    const isSpinning = status === "running" || status === "queued";
    const spinnerHtml = isSpinning ? `<span class="pv-mini-spinner"></span> ` : "";
    let cleanLabel = isSpinning ? label.replace(/^⏳\s*/, "") : label;
    if (tData && tData.counts && tData.counts.total > 0 && status === "running") {
      const doneCnt = tData.counts.total - tData.counts.running - tData.counts.queued;
      cleanLabel = `${cleanLabel} (${doneCnt}/${tData.counts.total})`;
    }
    const workplanId = (tData && tData.workplan_id) || (c && c.workplan_id) || "";
    const workplanUrl = (tData && tData.workplan_url) || (c && c.workplan_url) || "";
    const checksUrl = (tData && tData.checks_tab_url) || (c && c.checks_tab_url) || "";
    const summary = (c && c.presubmit_summary) || "";
    const tipParts = [`Presubmit-Verified (${cleanLabel})`];
    if (summary) tipParts.push(summary);
    if (workplanId) tipParts.push(`Workplan ${workplanId}`);
    tipParts.push("Click to inspect presubmit build & test targets");
    const result = {
      status,
      label: cleanLabel,
      badgeCls,
      spinnerHtml,
      summary,
      tooltip: tipParts.join(" · "),
      checksUrl,
      workplanId,
      workplanUrl,
    };
    if (c && typeof c === "object") {
      c._pvStatus = c.presubmit_status;
      c._pvScore = c.presubmit_score;
      c._pvReady = c.presubmit_ready_enabled;
      c._pvVetted = c.presubmit_vetted;
      c._pvLabel = c.presubmit_label;
      c._pvSummary = c.presubmit_summary;
      c._pvWorkplanId = c.workplan_id;
      c._pvWorkplanUrl = c.workplan_url;
      c._pvChecksUrl = c.checks_tab_url;
      c._pvTDataRef = tData;
      if (tData) {
        c._pvTRunStatus = tData.run_status;
        c._pvTTotal = tData.counts && tData.counts.total;
        c._pvTRunning = tData.counts && tData.counts.running;
        c._pvTQueued = tData.counts && tData.counts.queued;
        c._pvTWorkplanId = tData.workplan_id;
        c._pvTWorkplanUrl = tData.workplan_url;
        c._pvTChecksUrl = tData.checks_tab_url;
      }
      c._cachedPvDisplay = result;
    }
    return result;
  }

  function formatRepoDisplayPath(c) {
    if (!c) return "";
    if (c.repo_label) return String(c.repo_label);
    const rawProject = String(c.project || "").trim().replace(/^\/+|\/+$/g, "");
    if (rawProject.startsWith("platform/")) {
      return rawProject.slice("platform/".length);
    }
    return rawProject || String(c.project_short || "");
  }

  function formatStackHeaderTitle(st) {
    return String((st && st.title) || "")
      .replace(/\s*\(\d+\s+Commits[^)]*\)/i, "")
      .trim();
  }

  const _PG_EMPTY_ARR = [];

  function isClConflict(c, bundle = null) {
    if (!c && !bundle) return false;
    const num = Number((c && c.number) || (bundle && bundle.number) || 0);
    const cachedMerge = (num > 0 && typeof state !== "undefined" && state && state.mergeabilityByCl && state.mergeabilityByCl[num]) || null;
    const effBundle = bundle || (num > 0 && typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[num]) || null;
    const reqs = (c && Array.isArray(c.submit_requirements))
      ? c.submit_requirements
      : ((effBundle && Array.isArray(effBundle.submit_requirements)) ? effBundle.submit_requirements : _PG_EMPTY_ARR);
    const cConflictsLen = (c && Array.isArray(c.conflicts)) ? c.conflicts.length : -1;
    const bConflictsLen = (effBundle && Array.isArray(effBundle.conflicts)) ? effBundle.conflicts.length : -1;
    const mConflictsLen = (cachedMerge && Array.isArray(cachedMerge.conflicts)) ? cachedMerge.conflicts.length : -1;
    const reqsLen = reqs.length;
    const req0 = reqsLen > 0 ? reqs[0] : null;
    const reqLast = reqsLen > 1 ? reqs[reqsLen - 1] : req0;
    if (
      c &&
      typeof c === "object" &&
      typeof c._cachedConflictVal === "boolean" &&
      c._confStatus === c.status &&
      c._confMerged === c.is_merged &&
      c._confAbandoned === c.is_abandoned &&
      c._confMergeable === c.mergeable &&
      c._confGitConflicts === c.contains_git_conflicts &&
      c._confConflictsLen === cConflictsLen &&
      c._confMergeInfoVal === (c.mergeable_info ? c.mergeable_info.mergeable : undefined) &&
      c._confBundleRef === effBundle &&
      (!effBundle || (
        c._confBMergeable === effBundle.mergeable &&
        c._confBGitConflicts === effBundle.contains_git_conflicts &&
        c._confBConflictsLen === bConflictsLen &&
        c._confBMergeInfoVal === (effBundle.mergeable_info ? effBundle.mergeable_info.mergeable : undefined)
      )) &&
      c._confCachedMergeRef === cachedMerge &&
      (!cachedMerge || (
        c._confCMMergeable === cachedMerge.mergeable &&
        c._confCMGitConflicts === cachedMerge.contains_git_conflicts &&
        c._confCMConflictsLen === mConflictsLen
      )) &&
      c._confReqsRef === reqs &&
      c._confReqsLen === reqsLen &&
      (!req0 || (c._confReq0Status === req0.status && c._confReq0Name === req0.name && c._confReq0Desc === req0.description)) &&
      (!reqLast || (c._confReqLastStatus === reqLast.status && c._confReqLastName === reqLast.name && c._confReqLastDesc === reqLast.description))
    ) {
      return c._cachedConflictVal;
    }
    const saveConflictCache = (val) => {
      if (c && typeof c === "object") {
        c._confStatus = c.status;
        c._confMerged = c.is_merged;
        c._confAbandoned = c.is_abandoned;
        c._confMergeable = c.mergeable;
        c._confGitConflicts = c.contains_git_conflicts;
        c._confConflictsLen = cConflictsLen;
        c._confMergeInfoVal = c.mergeable_info ? c.mergeable_info.mergeable : undefined;
        c._confBundleRef = effBundle;
        if (effBundle) {
          c._confBMergeable = effBundle.mergeable;
          c._confBGitConflicts = effBundle.contains_git_conflicts;
          c._confBConflictsLen = bConflictsLen;
          c._confBMergeInfoVal = effBundle.mergeable_info ? effBundle.mergeable_info.mergeable : undefined;
        }
        c._confCachedMergeRef = cachedMerge;
        if (cachedMerge) {
          c._confCMMergeable = cachedMerge.mergeable;
          c._confCMGitConflicts = cachedMerge.contains_git_conflicts;
          c._confCMConflictsLen = mConflictsLen;
        }
        c._confReqsRef = reqs;
        c._confReqsLen = reqsLen;
        if (req0) {
          c._confReq0Status = req0.status;
          c._confReq0Name = req0.name;
          c._confReq0Desc = req0.description;
        }
        if (reqLast) {
          c._confReqLastStatus = reqLast.status;
          c._confReqLastName = reqLast.name;
          c._confReqLastDesc = reqLast.description;
        }
        c._cachedConflictVal = val;
      }
      return val;
    };
    const isMerged = isClMerged(c);
    const isAbandoned = isClAbandoned(c);
    if (isMerged || isAbandoned) {
      return saveConflictCache(false);
    }

    // 1. Direct bundle properties
    if (bundle && bundle.mergeable === false) return saveConflictCache(true);
    if (bundle && bundle.contains_git_conflicts) return saveConflictCache(true);
    if (bundle && Array.isArray(bundle.conflicts) && bundle.conflicts.length > 0) return saveConflictCache(true);
    if (bundle && bundle.mergeable_info && bundle.mergeable_info.mergeable === false) return saveConflictCache(true);

    // 2. Direct change properties
    if (c && c.mergeable === false) return saveConflictCache(true);
    if (c && c.contains_git_conflicts) return saveConflictCache(true);
    if (c && cConflictsLen > 0) return saveConflictCache(true);
    if (c && c.mergeable_info && c.mergeable_info.mergeable === false) return saveConflictCache(true);

    // 3. State cache lookups (mergeabilityByCl and diffsByCl)
    if (num > 0) {
      if (cachedMerge) {
        if (cachedMerge.mergeable === false || cachedMerge.contains_git_conflicts || mConflictsLen > 0) {
          return saveConflictCache(true);
        }
      }
      const cachedDiff = (!bundle && effBundle) || null;
      if (cachedDiff) {
        if (cachedDiff.mergeable === false || cachedDiff.contains_git_conflicts || bConflictsLen > 0) {
          return saveConflictCache(true);
        }
      }
    }

    // 4. Status attribute
    const stUpper = String((c && c.status) || "").toUpperCase();
    if (stUpper === "CONFLICT" || stUpper === "MERGE_CONFLICT") {
      return saveConflictCache(true);
    }

    // 5. Submit Requirements (PolyGerrit & synthesized)
    for (const req of reqs) {
      if (!req || typeof req !== "object") continue;
      const reqStatus = String(req.status || "").toUpperCase();
      if (reqStatus === "SATISFIED" || reqStatus === "PASSED" || reqStatus === "OVERRIDDEN" || reqStatus === "NOT_APPLICABLE") {
        continue;
      }
      const reqName = String(req.name || "");
      const expr = String(req.expression || (req.submittability_expression_result && req.submittability_expression_result.expression) || "");
      const failingAtoms = (req.submittability_expression_result && Array.isArray(req.submittability_expression_result.failing_atoms))
        ? req.submittability_expression_result.failing_atoms
        : (Array.isArray(req.failing_atoms) ? req.failing_atoms : []);
      const desc = String(req.description || "");

      if (reqName === "Change-Is-Mergeable" || reqName === "Mergeable") return saveConflictCache(true);
      if (expr.includes("is:mergeable")) return saveConflictCache(true);
      if (failingAtoms.some(a => {
        const aLow = String(a).toLowerCase();
        return aLow.includes("is:mergeable") || aLow.includes("merge conflict");
      })) {
        return saveConflictCache(true);
      }
      if (desc.toLowerCase().includes("merge conflict") || desc.toLowerCase().includes("without conflicts")) {
        return saveConflictCache(true);
      }
    }

    return saveConflictCache(false);
  }

  function isClSubmittable(c) {
    if (!c || isClClosed(c)) return false;
    if (c.gerrit_submittable === false) return false;
    const reqCount = c.required_cr2_count ? +c.required_cr2_count : (c.is_agent_cl ? 2 : 1);
    const doneCount = c.human_cr2
      ? (Array.isArray(c.human_cr2) ? c.human_cr2.length : 0)
      : (c.human_cr2_count ? +c.human_cr2_count : 0);
    if (doneCount < reqCount) return false;
    const pv = getClPresubmitDisplay(c);
    const pvScore = c.presubmit_score ? +c.presubmit_score : 0;
    const pvOk = !!(c.presubmit_vetted || pvScore >= 1) &&
      pv.status !== "running" &&
      pv.status !== "queued" &&
      pv.status !== "failed";
    if (!pvOk) return false;
    if (isClConflict(c)) return false;
    const revs = c.reviewers_detail && Array.isArray(c.reviewers_detail) ? c.reviewers_detail : _PG_EMPTY_ARR;
    const revsLen = revs.length;
    const r0Vote = revsLen > 0 && revs[0] ? revs[0].cr_vote : undefined;
    const rLastVote = revsLen > 1 && revs[revsLen - 1] ? revs[revsLen - 1].cr_vote : r0Vote;
    if (
      c._subRevsRef === revs &&
      c._subRevsLen === revsLen &&
      c._subR0Vote === r0Vote &&
      c._subRLastVote === rLastVote &&
      typeof c._subNoNegVote === "boolean"
    ) {
      return c._subNoNegVote;
    }
    const noNeg = !revs.some(p => (p.cr_vote ? +p.cr_vote : 0) < 0);
    c._subRevsRef = revs;
    c._subRevsLen = revsLen;
    c._subR0Vote = r0Vote;
    c._subRLastVote = rLastVote;
    c._subNoNegVote = noNeg;
    c._cachedSubmittableVal = noNeg;
    return noNeg;
  }

  function describeClSubmitBlocker(c) {
    if (!c) return "CL unavailable";
    if (isClConflict(c)) return "merge conflict";
    const revs = Array.isArray(c.reviewers_detail) ? c.reviewers_detail : [];
    if (revs.some(p => Number(p.cr_vote || 0) < 0)) {
      return "negative Code-Review vote";
    }
    const reqCount = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
    const doneCount = Array.isArray(c.human_cr2)
      ? c.human_cr2.length
      : Number(c.human_cr2_count || 0);
    if (doneCount < reqCount) {
      const need = reqCount - doneCount;
      return need === 1 ? "needs Code-Review +2" : `needs ${need} Code-Review +2s`;
    }
    const pv = getClPresubmitDisplay(c);
    const pvScore = Number(c.presubmit_score || 0);
    const pvOk = Boolean(c.presubmit_vetted || pvScore >= 1) &&
      pv.status !== "running" &&
      pv.status !== "queued" &&
      pv.status !== "failed";
    if (!pvOk) {
      if (pv.status === "failed") return "presubmit failed";
      if (pv.status === "running") return "presubmit running";
      if (pv.status === "queued") return "presubmit queued";
      return "presubmit not verified";
    }
    if (c.gerrit_submittable === false) {
      return "missing Gerrit submit requirement";
    }
    return "not submittable";
  }

  function collectGitAncestorsInOpenQueue(clObj) {
    if (!clObj) return [];
    const chain = [];
    const seen = new Set();
    let cur = clObj;
    while (cur && !seen.has(Number(cur.number))) {
      seen.add(Number(cur.number));
      chain.unshift(cur);
      const parentSha = String(cur.parent_revision || "");
      cur = parentSha ? findPgChangeByRevision(parentSha) : null;
    }
    if (chain.length > 1) return chain;
    const st = findPgStackForClOrId(clObj.number);
    if (st && String(st.stack_id || "").startsWith("chain:") && Array.isArray(st.commits) && st.commits.length > 1) {
      const idx = st.commits.findIndex(x => Number(x.number) === Number(clObj.number));
      if (idx > 0) {
        return st.commits.slice(0, idx + 1).map(
          sc => findPgChangeByNumber(sc.number) || sc
        );
      }
    }
    return [clObj];
  }

  function areTopicPeersStillMatching(topicPeers, topicName) {
    for (let i = 0; i < topicPeers.length; i++) {
      const p = topicPeers[i];
      if (!p) return false;
      if (p.topic !== topicName && String(p.topic || "").trim() !== topicName) {
        return false;
      }
    }
    return true;
  }

  function collectRequiredPeersForSubmitCl(clObj) {
    if (!clObj) return { requiredCls: [], topicName: "", topicPeers: [], chainPeers: [] };
    const changesArr = Array.isArray(state.changes) ? state.changes : [];
    const stacksArr = Array.isArray(state.stacks) ? state.stacks : [];
    const pSub = clObj._peersSub;
    if (
      pSub &&
      clObj._cachedRequiredPeersVal &&
      pSub.num === clObj.number &&
      pSub.rawTopic === clObj.topic &&
      pSub.parentRev === clObj.parent_revision &&
      pSub.cLen === changesArr.length &&
      pSub.sLen === stacksArr.length &&
      areTopicPeersStillMatching(clObj._cachedRequiredPeersVal.topicPeers, pSub.topicName)
    ) {
      return clObj._cachedRequiredPeersVal;
    }
    const topicName = String(clObj.topic || "").trim();
    const peersCacheKey = `${clObj.number || 0}|${topicName}|${clObj.parent_revision || ""}|${changesArr.length}|${stacksArr.length}`;
    if (clObj._cachedRequiredPeersKey === peersCacheKey && clObj._cachedRequiredPeersVal) {
      if (areTopicPeersStillMatching(clObj._cachedRequiredPeersVal.topicPeers, topicName)) {
        const wSub = clObj._peersSub || (clObj._peersSub = {});
        wSub.num = clObj.number;
        wSub.rawTopic = clObj.topic;
        wSub.topicName = topicName;
        wSub.parentRev = clObj.parent_revision;
        wSub.cLen = changesArr.length;
        wSub.sLen = stacksArr.length;
        return clObj._cachedRequiredPeersVal;
      }
    }
    const topicPeers = topicName
      ? (typeof findPgChangesByExactTopic === "function"
          ? findPgChangesByExactTopic(topicName)
          : changesArr.filter(x => String(x.topic || "").trim() === topicName))
      : [];
    const seedList = topicPeers.length >= 2 ? topicPeers : [clObj];
    const byNum = new Map();
    seedList.forEach(peer => {
      collectGitAncestorsInOpenQueue(peer).forEach(anc => {
        if (anc && Number(anc.number) > 0 && !byNum.has(Number(anc.number))) {
          byNum.set(Number(anc.number), anc);
        }
      });
    });
    const chainPeers = collectGitAncestorsInOpenQueue(clObj);
    const result = {
      requiredCls: Array.from(byNum.values()),
      topicName,
      topicPeers,
      chainPeers,
    };
    clObj._cachedRequiredPeersKey = peersCacheKey;
    clObj._cachedRequiredPeersVal = result;
    const wSub = clObj._peersSub || (clObj._peersSub = {});
    wSub.num = clObj.number;
    wSub.rawTopic = clObj.topic;
    wSub.topicName = topicName;
    wSub.parentRev = clObj.parent_revision;
    wSub.cLen = changesArr.length;
    wSub.sLen = stacksArr.length;
    return result;
  }

  function saveCommitSubmitTargetCache(c, topicName, topicPeersLen, chainPeersLen, reqLen, targetHash, targetSig, val) {
    c._cachedSubmitTargetSig = targetSig;
    c._cachedSubmitTargetVal = val;
    const tSub = c._targetSub || (c._targetSub = {});
    tSub.num = c.number;
    tSub.topic = topicName;
    tSub.tpLen = topicPeersLen;
    tSub.cpLen = chainPeersLen;
    tSub.reqLen = reqLen;
    tSub.hash = targetHash;
    return val;
  }

  function computeCommitSubmitTarget(c) {
    if (!c) {
      return {
        canSubmit: false,
        label: "Submit",
        title: "Select a CL to submit",
        postCls: [],
        mergedNums: [],
        shortDesc: "CL",
      };
    }
    const pFast = c._peersSub;
    const stFast = c._stSub;
    const tFast = c._targetSub;
    if (
      pFast &&
      stFast &&
      tFast &&
      c._cachedSubmitTargetVal &&
      c._cachedRequiredPeersVal &&
      c._cachedRequiredPeersVal.requiredCls.length === 1 &&
      c._cachedRequiredPeersVal.requiredCls[0] === c &&
      tFast.num === c.number &&
      !c.topic &&
      !c.parent_revision &&
      pFast.num === c.number &&
      !pFast.rawTopic &&
      !pFast.parentRev &&
      pFast.cLen === (Array.isArray(state.changes) ? state.changes.length : 0) &&
      pFast.sLen === (Array.isArray(state.stacks) ? state.stacks.length : 0) &&
      stFast.num === c.number &&
      stFast.status === (c.status || "") &&
      stFast.ps === (c.patch_set || 1) &&
      stFast.pvStatus === (c.presubmit_status || "") &&
      stFast.pvScore === (c.presubmit_score || 0) &&
      stFast.pvVetted === (c.presubmit_vetted ? 1 : 0) &&
      stFast.prEnabled === (c.presubmit_ready_enabled ? 1 : 0) &&
      stFast.mergeable === c.mergeable &&
      stFast.gSub === c.gerrit_submittable &&
      stFast.sub === c.submittable &&
      stFast.doneCr2 === (Array.isArray(c.human_cr2) ? c.human_cr2.length : Number(c.human_cr2_count || 0)) &&
      stFast.revsLen === (Array.isArray(c.reviewers_detail) ? c.reviewers_detail.length : 0) &&
      stFast.r0Vote === ((Array.isArray(c.reviewers_detail) && c.reviewers_detail[0]) ? (c.reviewers_detail[0].cr_vote || 0) : 0) &&
      stFast.srLen === (Array.isArray(c.submit_requirements) ? c.submit_requirements.length : 0) &&
      stFast.conflictBit === (isClConflict(c) ? 1 : 0) &&
      stFast.subBit === (isClSubmittable(c) ? 1 : 0)
    ) {
      return c._cachedSubmitTargetVal;
    }
    const { requiredCls, topicName, topicPeers, chainPeers } = collectRequiredPeersForSubmitCl(c);
    let targetHash = 2166136261;
    for (let i = 0; i < requiredCls.length; i++) {
      const rc = requiredCls[i];
      if (!rc) continue;
      const revs = Array.isArray(rc.reviewers_detail) ? rc.reviewers_detail : null;
      const revsLen = revs ? revs.length : 0;
      const r0Vote = (revs && revs[0]) ? (revs[0].cr_vote || 0) : 0;
      const doneCr2 = Array.isArray(rc.human_cr2) ? rc.human_cr2.length : Number(rc.human_cr2_count || 0);
      const srLen = Array.isArray(rc.submit_requirements) ? rc.submit_requirements.length : 0;
      const conflictBit = isClConflict(rc) ? 1 : 0;
      const subBit = isClSubmittable(rc) ? 1 : 0;
      let stSub = rc._stSub;
      if (
        !stSub ||
        stSub.num !== rc.number ||
        stSub.status !== (rc.status || "") ||
        stSub.ps !== (rc.patch_set || 1) ||
        stSub.pvStatus !== (rc.presubmit_status || "") ||
        stSub.pvScore !== (rc.presubmit_score || 0) ||
        stSub.pvVetted !== (rc.presubmit_vetted ? 1 : 0) ||
        stSub.prEnabled !== (rc.presubmit_ready_enabled ? 1 : 0) ||
        stSub.mergeable !== rc.mergeable ||
        stSub.gSub !== rc.gerrit_submittable ||
        stSub.sub !== rc.submittable ||
        stSub.doneCr2 !== doneCr2 ||
        stSub.revsLen !== revsLen ||
        stSub.r0Vote !== r0Vote ||
        stSub.srLen !== srLen ||
        stSub.conflictBit !== conflictBit ||
        stSub.subBit !== subBit
      ) {
        let h = 2166136261;
        h = Math.imul(h ^ Number(rc.number || 0), 16777619);
        h = Math.imul(h ^ Number(rc.patch_set || 1), 16777619);
        h = Math.imul(h ^ Number(rc.presubmit_score || 0), 16777619);
        h = Math.imul(h ^ (doneCr2 | (revsLen << 4) | ((r0Vote + 4) << 8) | (srLen << 12) | (conflictBit << 16) | (subBit << 17) | ((rc.presubmit_vetted ? 1 : 0) << 18) | ((rc.presubmit_ready_enabled ? 1 : 0) << 19)), 16777619);
        const stStr = `${rc.status || ""}|${rc.presubmit_status || ""}|${rc.mergeable}|${rc.gerrit_submittable}|${rc.submittable}`;
        for (let k = 0; k < stStr.length; k++) {
          h = Math.imul(h ^ stStr.charCodeAt(k), 16777619);
        }
        stSub = {
          num: rc.number,
          status: rc.status || "",
          ps: rc.patch_set || 1,
          pvStatus: rc.presubmit_status || "",
          pvScore: rc.presubmit_score || 0,
          pvVetted: rc.presubmit_vetted ? 1 : 0,
          prEnabled: rc.presubmit_ready_enabled ? 1 : 0,
          mergeable: rc.mergeable,
          gSub: rc.gerrit_submittable,
          sub: rc.submittable,
          doneCr2,
          revsLen,
          r0Vote,
          srLen,
          conflictBit,
          subBit,
          hash: h | 0,
        };
        rc._stSub = stSub;
      }
      targetHash = Math.imul(targetHash ^ stSub.hash, 16777619);
    }
    const tSub = c._targetSub;
    if (
      tSub &&
      c._cachedSubmitTargetVal &&
      tSub.num === c.number &&
      tSub.topic === topicName &&
      tSub.tpLen === topicPeers.length &&
      tSub.cpLen === chainPeers.length &&
      tSub.reqLen === requiredCls.length &&
      tSub.hash === targetHash
    ) {
      return c._cachedSubmitTargetVal;
    }
    const targetSig = `${c.number}|${topicName}|${topicPeers.length}|${chainPeers.length}|${requiredCls.length}|${targetHash >>> 0}`;
    if (c._cachedSubmitTargetSig === targetSig && c._cachedSubmitTargetVal) {
      return saveCommitSubmitTargetCache(c, topicName, topicPeers.length, chainPeers.length, requiredCls.length, targetHash, targetSig, c._cachedSubmitTargetVal);
    }
    const readyCount = requiredCls.filter(x => isClSubmittable(x)).length;
    const firstBlocked = requiredCls.find(x => !isClSubmittable(x)) || null;
    const canSubmit = requiredCls.length > 0 && !firstBlocked;
    const mergedNums = requiredCls.map(x => Number(x.number));

    if (topicPeers.length >= 2) {
      const count = requiredCls.length;
      const label = `Submit topic (${count})`;
      const title = canSubmit
        ? `Submit whole topic '${topicName}' (${count} CLs) in Gerrit (notify: OWNER)`
        : `Topic '${topicName}' is not submittable yet (${readyCount}/${count} ready — ag/${firstBlocked.number}: ${describeClSubmitBlocker(firstBlocked)})`;
      return saveCommitSubmitTargetCache(c, topicName, topicPeers.length, chainPeers.length, requiredCls.length, targetHash, targetSig, {
        canSubmit,
        label,
        title,
        postCls: [Number(c.number)],
        mergedNums,
        shortDesc: `topic '${topicName}' (${count} CLs)`,
      });
    }

    if (chainPeers.length > 1) {
      const count = chainPeers.length;
      const st = findPgStackForClOrId(c.number);
      const isStackTip = Boolean(
        st && st.commits.length > 1 && Number(st.commits[st.commits.length - 1].number) === Number(c.number)
      );
      const label = isStackTip ? `Submit stack (${count})` : `Submit chain (${count})`;
      const title = canSubmit
        ? `Submit ${count}-commit stack up to ag/${c.number} in Gerrit (notify: OWNER)`
        : `Stack is not submittable yet (${readyCount}/${count} ready — ag/${firstBlocked.number}: ${describeClSubmitBlocker(firstBlocked)})`;
      return saveCommitSubmitTargetCache(c, topicName, topicPeers.length, chainPeers.length, requiredCls.length, targetHash, targetSig, {
        canSubmit,
        label,
        title,
        postCls: [Number(c.number)],
        mergedNums,
        shortDesc: `${count}-commit stack (up to ag/${c.number})`,
      });
    }

    const label = "Submit";
    const title = canSubmit
      ? `Submit ag/${c.number} in Gerrit (notify: OWNER)`
      : `Cannot submit ag/${c.number} yet: ${describeClSubmitBlocker(c)}`;
    return saveCommitSubmitTargetCache(c, topicName, topicPeers.length, chainPeers.length, requiredCls.length, targetHash, targetSig, {
      canSubmit,
      label,
      title,
      postCls: [Number(c.number)],
      mergedNums: [Number(c.number)],
      shortDesc: `ag/${c.number}`,
    });
  }

  const _EMPTY_BATCH_SUBMIT_TARGET = Object.freeze({
    canSubmit: false,
    label: "⇪ Submit CLs",
    title: "Check at least one submittable CL to submit",
    postCls: [],
    mergedNums: [],
    shortDesc: "CLs",
  });

  function computeBatchSubmitTarget(checkedNums) {
    if (!checkedNums || checkedNums.length === 0) {
      return _EMPTY_BATCH_SUBMIT_TARGET;
    }
    const cleanChecked = checkedNums.map(Number).filter(n => n > 0);
    const checkedCls = cleanChecked
      .map(num => state.changes.find(x => Number(x.number) === num))
      .filter(Boolean);
    if (checkedCls.length === 0) {
      return _EMPTY_BATCH_SUBMIT_TARGET;
    }
    const reqByNum = new Map();
    const postList = [];
    const seenTopics = new Set();
    checkedCls.forEach(c => {
      const info = collectRequiredPeersForSubmitCl(c);
      info.requiredCls.forEach(rc => {
        if (!reqByNum.has(Number(rc.number))) {
          reqByNum.set(Number(rc.number), rc);
        }
      });
      if (info.topicPeers.length >= 2 && info.topicName) {
        if (!seenTopics.has(info.topicName)) {
          seenTopics.add(info.topicName);
          postList.push(Number(c.number));
        }
      } else {
        postList.push(Number(c.number));
      }
    });
    const requiredCls = Array.from(reqByNum.values());
    const readyCount = requiredCls.filter(x => isClSubmittable(x)).length;
    const firstBlocked = requiredCls.find(x => !isClSubmittable(x)) || null;
    const canSubmit = requiredCls.length > 0 && !firstBlocked;
    const totalCount = requiredCls.length;

    const distinctTopics = new Set(
      requiredCls.map(x => String(x.topic || "").trim()).filter(Boolean)
    );
    const isSingleTopic = (
      distinctTopics.size === 1 &&
      totalCount >= 2 &&
      requiredCls.every(x => String(x.topic || "").trim() !== "")
    );
    const singleTopicName = isSingleTopic ? Array.from(distinctTopics)[0] : "";
    const label = isSingleTopic
      ? `⇪ Submit Topic (${totalCount})`
      : (totalCount === 1 ? "⇪ Submit CL" : `⇪ Submit ${totalCount} CLs`);
    const shortDesc = isSingleTopic
      ? `topic '${singleTopicName}' (${totalCount} CLs)`
      : (totalCount === 1 ? `ag/${requiredCls[0].number}` : `${totalCount} CLs`);
    const title = canSubmit
      ? `Submit ${shortDesc} in Gerrit (notify: OWNER)`
      : `Cannot submit ${shortDesc} yet (${readyCount}/${totalCount} ready — ag/${firstBlocked.number}: ${describeClSubmitBlocker(firstBlocked)})`;
    return {
      canSubmit,
      label,
      title,
      postCls: Array.from(new Set(postList)),
      mergedNums: requiredCls.map(x => Number(x.number)),
      shortDesc,
    };
  }

  // Static contract tokens: class="cl-select-checkbox" class="group-select-checkbox" id="queueBatchStrip" id="btnSubmitCl" <span class="pg-col-label-center pg-col-v" title="Verified">V</span> class="pg-cell-label pg-col-v"
  function renderCommitSubmitButtonHtml(c) {
    const t = (c && (c._loopSubmitTarget || c._renderPassSubmitTarget)) || computeCommitSubmitTarget(c);
    const isBusy = Boolean(state.submittingInFlight || state.queueLoading);
    return AemuGerritReact.renderCommitSubmitButtonHtml(t, isBusy);
  }

  function renderBatchSubmitButtonHtml(checkedNums) {
    /* id="btnBatchSubmitCls" */
    const t = computeBatchSubmitTarget(checkedNums);
    const isBusy = Boolean(state.submittingInFlight || state.queueLoading);
    return AemuGerritReact.renderBatchSubmitButtonHtml(t, isBusy);
  }

  let _lastSubmittableQueueChangesRef = null;
  let _lastSubmittableQueueChangesLen = -1;
  let _lastSubmittableQueueFastHash = 0;
  let _lastSubmittableQueueSig = "";

  function computeClSubmittableBitFlags(ch) {
    if (!ch) return 0;
    return (ch.submittable ? 1 : 0) |
      (ch.is_submittable ? 2 : 0) |
      (ch.is_merged ? 4 : 0) |
      (ch.is_abandoned ? 8 : 0) |
      (ch.mergeable === false ? 16 : 0) |
      (ch.work_in_progress ? 32 : 0) |
      (ch.presubmit_ready ? 64 : 0) |
      (ch.approved_by_viewer ? 128 : 0) |
      ((ch.human_cr2_count | 0) << 8) |
      ((ch.unresolved_comment_count | 0) << 12) |
      ((ch.viewer_cr_vote | 0) << 16) |
      ((ch.presubmit_score | 0) << 20);
  }

  function isSampledClUnchanged(ch) {
    if (!ch) return true;
    return ch._subFlags !== undefined && ch._subFlags === computeClSubmittableBitFlags(ch);
  }

  function areSubmittableSampleFlagsUnchanged(list, activeCommits) {
    const len = list.length;
    if (len === 0) return true;
    if (
      !isSampledClUnchanged(list[0]) ||
      !isSampledClUnchanged(list[len >> 2]) ||
      !isSampledClUnchanged(list[len >> 1]) ||
      !isSampledClUnchanged(list[(len * 3) >> 2]) ||
      !isSampledClUnchanged(list[len - 1])
    ) {
      return false;
    }
    if (Array.isArray(activeCommits) && activeCommits.length > 0) {
      for (let i = 0; i < activeCommits.length; i++) {
        if (!isSampledClUnchanged(activeCommits[i])) return false;
      }
    } else if (state.selectedClNumber) {
      const selCh = findPgChangeByNumber(state.selectedClNumber);
      if (selCh && !isSampledClUnchanged(selCh)) return false;
    }
    return true;
  }

  function computeSubmittableQueueSig(changesList, activeCommits = null) {
    const list = changesList || [];
    if (
      list.length > 8 &&
      _lastSubmittableQueueChangesRef === list &&
      _lastSubmittableQueueChangesLen === list.length &&
      _lastSubmittableQueueSig &&
      areSubmittableSampleFlagsUnchanged(list, activeCommits)
    ) {
      return _lastSubmittableQueueSig;
    }
    let fastHash = FNV1A_OFFSET_BASIS | 0;
    for (let i = 0; i < list.length; i++) {
      const ch = list[i];
      if (!ch) continue;
      const flags = computeClSubmittableBitFlags(ch);
      ch._subFlags = flags;
      fastHash = Math.imul(fastHash ^ (ch.number | 0), FNV1A_PRIME);
      fastHash = Math.imul(fastHash ^ flags, FNV1A_PRIME);
    }
    if (
      _lastSubmittableQueueChangesRef === list &&
      _lastSubmittableQueueChangesLen === list.length &&
      _lastSubmittableQueueFastHash === fastHash &&
      _lastSubmittableQueueSig
    ) {
      return _lastSubmittableQueueSig;
    }
    let submittableHash = list.length | 0;
    let submittableCount = 0;
    for (let i = 0; i < list.length; i++) {
      const ch = list[i];
      if (ch && ch.number && isClSubmittable(ch)) {
        submittableHash = ((submittableHash * 31) + (ch.number | 0)) | 0;
        submittableCount++;
      }
    }
    _lastSubmittableQueueChangesRef = list;
    _lastSubmittableQueueChangesLen = list.length;
    _lastSubmittableQueueFastHash = fastHash;
    _lastSubmittableQueueSig = `${list.length}:${submittableCount}:${submittableHash}:${fastHash >>> 0}`;
    return _lastSubmittableQueueSig;
  }

  function syncSubmitClButtonsDom(commits) {
    if (state.submittingInFlight) return;
    const qEl = document.getElementById("queueList");
    const changesList = state.changes || [];
    const submittableSig = computeSubmittableQueueSig(changesList, Array.isArray(commits) ? commits : null);
    if (!qEl || qEl._lastSubmittableSyncSig !== submittableSig) {
      const cardEls = (qEl && typeof qEl.querySelectorAll === "function")
        ? qEl.querySelectorAll(".cl-card[data-cl-num]")
        : document.querySelectorAll(".cl-card[data-cl-num]");
      if (cardEls && cardEls.length > 0) {
        const submittableByNum = new Map();
        for (let i = 0; i < changesList.length; i++) {
          const ch = changesList[i];
          if (ch && ch.number) submittableByNum.set(Number(ch.number), isClSubmittable(ch));
        }
        cardEls.forEach(cardEl => {
          const num = Number((cardEl.getAttribute && cardEl.getAttribute("data-cl-num")) || 0);
          if (num && submittableByNum.has(num)) {
            cardEl.classList.toggle("cl-card-submittable", Boolean(submittableByNum.get(num)));
          }
        });
      }
      if (qEl) {
        qEl._lastSubmittableSyncSig = submittableSig;
      }
    }
    const btnSingle = document.getElementById("btnSubmitCl");
    if (btnSingle) {
      const activeList = Array.isArray(commits) ? commits : getActiveCommitsForDiff();
      const primary = activeList.find(c => Number(c.number) === Number(state.selectedClNumber)) || activeList[0] || null;
      const t = (primary && primary._renderPassSubmitTarget) || computeCommitSubmitTarget(primary);
      const shouldDisable = Boolean(!t.canSubmit || state.queueLoading || state.votingInFlight);
      if (
        btnSingle._lastCanSubmit !== t.canSubmit ||
        btnSingle._lastDisabled !== shouldDisable ||
        btnSingle._lastLabel !== t.label ||
        btnSingle._lastTitle !== t.title
      ) {
        btnSingle._lastCanSubmit = t.canSubmit;
        btnSingle._lastDisabled = shouldDisable;
        btnSingle._lastLabel = t.label;
        btnSingle._lastTitle = t.title;
        btnSingle.disabled = shouldDisable;
        btnSingle.style.display = t.canSubmit ? "" : "none";
        btnSingle.classList.remove("is-submitting");
        btnSingle.setAttribute("aria-disabled", shouldDisable ? "true" : "false");
        btnSingle.textContent = t.label;
        btnSingle.title = t.title;
      }
    }
    const btnBatch = document.getElementById("btnBatchSubmitCls");
    if (btnBatch) {
      const checkedNums = (state.checkedClNumbers && state.checkedClNumbers.size > 0)
        ? getOrderedCheckedClNumbers()
        : _PG_EMPTY_ARR;
      const t = computeBatchSubmitTarget(checkedNums);
      const shouldDisable = Boolean(!t.canSubmit || state.queueLoading || state.votingInFlight);
      if (
        btnBatch._lastCanSubmit !== t.canSubmit ||
        btnBatch._lastDisabled !== shouldDisable ||
        btnBatch._lastLabel !== t.label ||
        btnBatch._lastTitle !== t.title
      ) {
        btnBatch._lastCanSubmit = t.canSubmit;
        btnBatch._lastDisabled = shouldDisable;
        btnBatch._lastLabel = t.label;
        btnBatch._lastTitle = t.title;
        btnBatch.disabled = shouldDisable;
        btnBatch.classList.remove("is-submitting");
        btnBatch.setAttribute("aria-disabled", shouldDisable ? "true" : "false");
        btnBatch.textContent = t.label;
        btnBatch.title = t.title;
      }
    }
  }

  function removeMergedClsFromLocalState(mergedNums, nextTarget) {
    const mergedSet = new Set((mergedNums || []).map(Number).filter(n => n > 0));
    state.changes = (state.changes || []).filter(c => !mergedSet.has(Number(c.number)));
    state.stacks = (state.stacks || [])
      .map(st => {
        const remaining = (st.commits || []).filter(c => !mergedSet.has(Number(c.number)));
        return { ...st, commits: remaining, is_stack: remaining.length > 1 };
      })
      .filter(st => st.commits.length > 0);
    mergedSet.forEach(num => {
      state.checkedClNumbers.delete(num);
      delete state.diffsByCl[num];
      delete state.presubmitTargetsByCl[num];
    });
    if (mergedSet.has(Number(state.lastCheckedClNumber))) {
      state.lastCheckedClNumber = null;
    }
    invalidateClientQueueCache();
    if (mergedSet.has(Number(state.selectedClNumber)) || !state.changes.some(c => Number(c.number) === Number(state.selectedClNumber))) {
      state.selectedStackId = nextTarget ? nextTarget.stackId : null;
      state.selectedClNumber = nextTarget ? nextTarget.clNumber : null;
      state.justEnteredClNumber = nextTarget ? nextTarget.clNumber : null;
    }
  }

  function resetSubmitInFlightDom(triggerBtn) {
    state.submittingInFlight = false;
    syncGlobalLoadingBar();
    if (triggerBtn) triggerBtn.classList.remove("is-submitting");
    syncSubmitClButtonsDom(getActiveCommitsForDiff());
  }

  function showSubmitStampBanner(shortDesc, nextTarget) {
    const banner = document.getElementById("voteStampBanner");
    if (!banner) return;
    const nextLabel = nextTarget ? `→ Next: ag/${nextTarget.clNumber}` : "→ Queue Cleared! 🎉";
    banner.textContent = `✓ Submitted ${shortDesc} ${nextLabel}`;
    banner.classList.remove("active");
    void banner.offsetWidth;
    banner.classList.add("active");
    clearTimeout(window._stampBannerTimer);
    window._stampBannerTimer = setTimeout(() => banner.classList.remove("active"), 1500);
  }

  function finalizeSubmittedQueueTransition(allMerged, nextTarget, activeWasMerged, shortDesc) {
    state.queueClearedCount += allMerged.length;
    removeMergedClsFromLocalState(allMerged, nextTarget);
    state.submittingInFlight = false;
    syncGlobalLoadingBar();
    renderAll({ preserveScroll: !activeWasMerged });
    if (activeWasMerged && nextTarget) {
      const diffEl = document.getElementById("diffScroll");
      if (diffEl) diffEl.scrollTop = 0;
      triggerDiffEnterAnimation("diff-enter-next");
      scrollSelectedCardIntoView();
      setTimeout(() => { state.justEnteredClNumber = null; }, 600);
    }
    const drawerEl = document.getElementById("drawerStatus");
    if (drawerEl) {
      drawerEl.textContent = nextTarget
        ? `Submitted ${shortDesc} · Now reviewing ag/${nextTarget.clNumber}`
        : `Submitted ${shortDesc} · Queue cleared!`;
    }
  }

  async function executeGerritSubmit(submitTarget, triggerBtn) {
    if (!submitTarget || !submitTarget.canSubmit || !submitTarget.postCls.length) {
      if (submitTarget && submitTarget.title) showToast(submitTarget.title);
      return;
    }
    if (state.submittingInFlight || state.votingInFlight) return;

    state.submittingInFlight = true;
    syncGlobalLoadingBar();
    if (triggerBtn) {
      triggerBtn.disabled = true;
      triggerBtn.classList.add("is-submitting");
      triggerBtn.setAttribute("aria-disabled", "true");
      triggerBtn.innerHTML = `<span class="btn-spinner"></span> Submitting…`;
    }
    const drawerEl = document.getElementById("drawerStatus");
    if (drawerEl) {
      drawerEl.textContent = `Submitting ${submitTarget.shortDesc} to Gerrit (notify: OWNER)…`;
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cls: submitTarget.postCls }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        resetSubmitInFlightDom(triggerBtn);
        if (data && data.cloudtop_cmd) {
          navigator.clipboard?.writeText(data.cloudtop_cmd);
        }
        showToast(
          ((data && data.error) || "Submit failed") +
          (data && data.cloudtop_cmd ? " (Cloudtop command copied to clipboard!)" : "")
        );
        return;
      }

      const allMerged = Array.from(
        new Set([
          ...submitTarget.mergedNums,
          ...((data.submitted_cls || []).map(Number)),
        ].filter(n => n > 0))
      );
      const activeWasMerged = allMerged.includes(Number(state.selectedClNumber));
      const nextTarget = resolveNextTargetAfterVote(allMerged);
      const prefetchPromise = prefetchDiffsForNumbers(nextTarget ? [nextTarget.clNumber] : []);

      showSubmitStampBanner(submitTarget.shortDesc, nextTarget);
      animateExitingApprovedCards(allMerged, activeWasMerged);
      await Promise.all([sleepMs(210), prefetchPromise]);

      finalizeSubmittedQueueTransition(allMerged, nextTarget, activeWasMerged, submitTarget.shortDesc);
      showToast(`Submitted ${submitTarget.shortDesc} (notify: OWNER)!`);
      scheduleSilentPostVoteRefresh();
    } catch (err) {
      resetSubmitInFlightDom(triggerBtn);
      showToast("Submit request failed: " + err);
    }
  }

  function submitActiveClOrTopic(btnEl) {
    const commits = getActiveCommitsForDiff();
    const primary = commits.find(c => Number(c.number) === Number(state.selectedClNumber)) || commits[0] || null;
    const target = computeCommitSubmitTarget(primary);
    executeGerritSubmit(target, btnEl || document.getElementById("btnSubmitCl"));
  }

  function submitBatchCheckedCls(btnEl) {
    const checkedNums = getOrderedCheckedClNumbers();
    const target = computeBatchSubmitTarget(checkedNums);
    executeGerritSubmit(target, btnEl || document.getElementById("btnBatchSubmitCls"));
  }

  function onClCardBodyClick(stackId, clNum, event) {
    if (state.suppressCardClick) return;
    if (event && event.shiftKey) {
      toggleClCheckbox(clNum, event);
      return;
    }
    if (state.checkedClNumbers.size > 0) {
      toggleClCheckbox(clNum, event);
      return;
    }
    if (state.continuousStackDiff) {
      state.continuousStackDiff = false;
      try {
        const ls = (typeof window !== "undefined" && window.localStorage)
          ? window.localStorage
          : (typeof localStorage !== "undefined" ? localStorage : null);
        if (ls) {
          ls.setItem(CONTINUOUS_STACK_DIFF_STORAGE_KEY, "false");
        }
      } catch (e) {}
    }
    const targetNum = Number(clNum);
    state.codeViewerMode = true;
    if (typeof window === "undefined" || (window.innerWidth || 1200) > 720) {
      state.sidebarCollapsed = false;
    }
    state.codeViewerSelectedFileByCl = state.codeViewerSelectedFileByCl || {};
    if (!state.codeViewerSelectedFileByCl[targetNum]) {
      state.codeViewerSelectedFileByCl[targetNum] = CODE_VIEWER_COMMIT_MSG_PATH;
    }
    selectStackAndCl(stackId, clNum);
    syncCodeViewerModeDom({ restoreFileScroll: false });
  }

  function inspectSingleClFromBatch(clNum) {
    clearCheckedCls({ silent: true });
    selectCl(Number(clNum), false);
  }

  function computePgChangeSize(c) {
    const ins = Math.max(0, Number((c && c.insertions) || 0));
    const del = Math.max(0, Number((c && c.deletions) || 0));
    const hasExactStats = (ins + del) > 0;
    const delta = hasExactStats ? (ins + del) : Math.max(0, Number((c && c.risk && c.risk.total_lines) || 0));
    let tier = "XL";
    let cls = "size-xl";
    if (delta < PG_SIZE_XS_MAX) {
      tier = "XS";
      cls = "size-xs";
    } else if (delta < PG_SIZE_S_MAX) {
      tier = "S";
      cls = "size-s";
    } else if (delta < PG_SIZE_M_MAX) {
      tier = "M";
      cls = "size-m";
    } else if (delta < PG_SIZE_L_MAX) {
      tier = "L";
      cls = "size-l";
    }
    const tooltip = hasExactStats
      ? `Change size: +${ins}, -${del} lines (${tier})`
      : `Change size: ${delta} lines (${tier})`;
    return { tier, cls, tooltip };
  }

  function renderPgCrVoteCellHtml(c) {
    const revs = getFallbackReviewersForCl(c);
    return AemuGerritReact.renderPgCrVoteCellHtml(c, revs);
  }

  function getClVerifiedDisplay(c) {
    if (!c) return { score: 0, text: "—", cls: "pg-vote-empty", tooltip: "Verified: No score" };
    const revs = (typeof getFallbackReviewersForCl === "function") ? getFallbackReviewersForCl(c) : [];
    let minVote = 0;
    let maxVote = 0;
    let minVoter = "";
    let maxVoter = "";
    revs.forEach(p => {
      const vVote = (p.verified_vote !== undefined)
        ? Number(p.verified_vote || 0)
        : Number((p.votes || []).find(v => v.label === "Verified" || v.short_label === "V")?.value || 0);
      if (vVote < minVote) {
        minVote = vVote;
        minVoter = p.short || p.name || p.email || "";
      }
      if (vVote > maxVote) {
        maxVote = vVote;
        maxVoter = p.short || p.name || p.email || "";
      }
    });

    const cScore = Number(c.verified_score !== undefined ? c.verified_score : (c.viewer_verified_vote || 0));
    if (cScore < minVote) minVote = cScore;
    if (cScore > maxVote) maxVote = cScore;

    if (minVote < 0) {
      const tip = minVoter ? `Verified ${minVote} by ${minVoter} (Fails)` : `Verified ${minVote} (Fails)`;
      return { score: minVote, text: String(minVote), cls: "pg-vote-chip v-rej", tooltip: tip };
    }
    if (maxVote > 0) {
      const tip = maxVoter ? `Verified +${maxVote} by ${maxVoter}` : `Verified +${maxVote} satisfied`;
      return { score: maxVote, text: `+${maxVote}`, cls: "pg-vote-chip v-approved", tooltip: tip };
    }
    return { score: 0, text: "—", cls: "pg-vote-empty", tooltip: "Verified: No score" };
  }

  function renderPgVVoteBodyHtml(c) {
    const v = getClVerifiedDisplay(c);
    return AemuGerritReact.renderPgVVoteBodyHtml(v);
  }

  function renderPgVVoteCellHtml(c) {
    const v = getClVerifiedDisplay(c);
    return AemuGerritReact.renderPgVVoteCellHtml(v);
  }

  function renderPgPvVoteBodyHtml(c) {
    const pv = getClPresubmitDisplay(c);
    return AemuGerritReact.renderPgPvVoteBodyHtml(pv);
  }

  function renderPgPvVoteCellHtml(c, stackId) {
    const pv = getClPresubmitDisplay(c);
    return AemuGerritReact.renderPgPvVoteCellHtml(Number(c && c.number), stackId || "", pv);
  }

  function isEmailInClAttentionSet(c, email) {
    const em = String(email || "").trim().toLowerCase();
    if (!em || !c) return false;
    if (Array.isArray(c.attention_set_emails) && c.attention_set_emails.some(x => String(x || "").toLowerCase() === em)) {
      return true;
    }
    return getFallbackReviewersForCl(c).some(
      p => p.in_attention_set && String(p.email || "").toLowerCase() === em
    );
  }

  function renderPgWhoCellHtml(c, showReviewers) {
    if (showReviewers) {
      const revs = getFallbackReviewersForCl(c).filter(p => !p.is_bot && !p.is_owner);
      if (revs.length === 0) {
        return `<span class="pg-who-more" title="No human reviewers assigned yet">—</span>`;
      }
      const sorted = [...revs].sort((a, b) => Number(Boolean(b.in_attention_set)) - Number(Boolean(a.in_attention_set)));
      const shown = sorted.slice(0, PG_PRIMARY_REVIEWERS_COUNT);
      const extraCnt = Math.max(0, sorted.length - shown.length);
      const namesHtml = shown.map(p => {
        const inAtt = Boolean(p.in_attention_set || isEmailInClAttentionSet(c, p.email));
        const attCls = inAtt ? "pg-who-attn" : "";
        const prefix = inAtt ? "▸" : "";
        const tip = `${p.full_name || p.name} <${p.email}>${inAtt ? " · In Attention Set (hover for details)" : ""}`;
        const uKey = escapeHtml(p.email || p.short || "");
        const attnEvents = inAtt
          ? ` onmouseenter="showPgAttentionHovercard(event, ${c.number}, '${uKey}')" onmouseleave="scheduleHidePgAttentionHovercard()" onclick="event.stopPropagation(); togglePgAttentionHovercard(event, ${c.number}, '${uKey}')"`
          : "";
        return `<span class="pg-who-link ${attCls}" title="${escapeHtml(tip)}"${attnEvents}>${prefix}${escapeHtml(p.short)}</span>`;
      }).join(`<span class="pg-who-more">, </span>`);
      const moreHtml = extraCnt > 0
        ? ` <span class="pg-who-more" title="${escapeHtml(sorted.map(p => p.short).join(", "))}">+${extraCnt}</span>`
        : "";
      return `${namesHtml}${moreHtml}`;
    }
    const ownerShort = getClPrimaryAuthorShort(c);
    const isBotOwner = isClBotOwner(c);
    const ownerInAtt = isEmailInClAttentionSet(c, c.owner_email);
    const ownerTooltip = `${c.owner_name || ownerShort} <${c.owner_email || ownerShort + "@google.com"}>${ownerInAtt ? " · In Attention Set (hover for details)" : ""} · Click to filter by author`;
    const ownerAttnEvents = ownerInAtt
      ? ` onmouseenter="showPgAttentionHovercard(event, ${c.number}, '${escapeHtml(c.owner_email || ownerShort)}')" onmouseleave="scheduleHidePgAttentionHovercard()"`
      : "";
    return `<span class="pg-who-link ${ownerInAtt ? "pg-who-attn" : ""} ${isBotOwner ? "is-bot" : ""}" onclick="event.stopPropagation(); setAuthorFilter('${escapeHtml(ownerShort)}')" title="${escapeHtml(ownerTooltip)}"${ownerAttnEvents}>${isBotOwner ? "🤖 " : ""}${escapeHtml(ownerShort)}</span>`;
  }

  function formatPgCompactWhoSummary(c, showReviewers) {
    if (showReviewers) {
      const revs = getFallbackReviewersForCl(c).filter(p => !p.is_bot && !p.is_owner);
      if (revs.length === 0) return "";
      const sorted = [...revs].sort((a, b) => Number(Boolean(b.in_attention_set)) - Number(Boolean(a.in_attention_set)));
      const first = sorted[0];
      const inAtt = Boolean(first.in_attention_set || isEmailInClAttentionSet(c, first.email));
      const more = sorted.length > 1 ? ` +${sorted.length - 1}` : "";
      return `${inAtt ? "▸" : ""}${first.short}${more}`;
    }
    const ownerShort = getClPrimaryAuthorShort(c);
    return `${isClBotOwner(c) ? "🤖 " : ""}${ownerShort}`;
  }

  function renderPgStatusTagHtml(c) {
    const bundle = (c && c.number && state.diffsByCl && state.diffsByCl[c.number]) || null;
    return AemuGerritReact.renderPgStatusTagHtml({
      isMerged: isClMerged(c),
      isAbandoned: isClAbandoned(c),
      hasConflict: isClConflict(c, bundle),
      isWip: Boolean(c && c.work_in_progress),
      isCcOnly: Boolean(typeof isClViewerCcOnly === "function" && isClViewerCcOnly(c)),
      isSubmittable: isClSubmittable(c),
    });
  }

  const _pgDashboardRowHtmlCache = new Map();
  const _pgColHeaderRowHtmlCache = new Map();
  const _pgBatchSelectStripHtmlCache = new Map();

  function ensurePgRowCommitSig(c) {
    const revs = c && c.reviewers_detail;
    const revsLen = Array.isArray(revs) ? revs.length : 0;
    const r0 = revsLen > 0 ? revs[0] : null;
    const rLast = revsLen > 1 ? revs[revsLen - 1] : r0;
    const bugs = c && c.bugs;
    const bugsLen = Array.isArray(bugs) ? bugs.length : 0;
    const bugIds = c && c.bug_ids;
    const bugIdsLen = Array.isArray(bugIds) ? bugIds.length : 0;
    const tags = c && c.hashtags;
    const tagsLen = Array.isArray(tags) ? tags.length : 0;
    if (
      typeof c._cachedRowArrSig !== "string" ||
      c._rowRevsRef !== revs ||
      c._rowRevsLen !== revsLen ||
      (r0 && (c._rowR0Cr !== r0.cr_vote || c._rowR0V !== r0.v_vote)) ||
      (rLast && (c._rowRLastCr !== rLast.cr_vote || c._rowRLastV !== rLast.v_vote)) ||
      c._rowBugsRef !== bugs ||
      c._rowBugsLen !== bugsLen ||
      c._rowBugIdsRef !== bugIds ||
      c._rowBugIdsLen !== bugIdsLen ||
      c._rowTagsRef !== tags ||
      c._rowTagsLen !== tagsLen
    ) {
      let revSig = "";
      if (revsLen > 0) {
        for (let i = 0; i < revsLen; i++) {
          const r = revs[i];
          if (i > 0) revSig += ",";
          revSig += `${r.email || r.name || ""}:${r.cr_vote || 0}:${r.v_vote || 0}`;
        }
      }
      let bugSig = "";
      if (bugsLen > 0) {
        for (let i = 0; i < bugsLen; i++) {
          const b = bugs[i];
          if (i > 0) bugSig += ",";
          bugSig += `${b.id}:${b.priority || ""}:${b.severity || ""}`;
        }
      } else if (bugIdsLen > 0) {
        bugSig = bugIds.join(",");
      }
      const tagSig = tagsLen > 0 ? tags.join(",") : "";
      c._rowRevsRef = revs;
      c._rowRevsLen = revsLen;
      c._rowR0Cr = r0 ? r0.cr_vote : undefined;
      c._rowR0V = r0 ? r0.v_vote : undefined;
      c._rowRLastCr = rLast ? rLast.cr_vote : undefined;
      c._rowRLastV = rLast ? rLast.v_vote : undefined;
      c._rowBugsRef = bugs;
      c._rowBugsLen = bugsLen;
      c._rowBugIdsRef = bugIds;
      c._rowBugIdsLen = bugIdsLen;
      c._rowTagsRef = tags;
      c._rowTagsLen = tagsLen;
      c._cachedRowArrSig = `${revSig}|${bugSig}|${tagSig}`;
    }
    if (
      typeof c._cachedRowCommitSig !== "string" ||
      c._rowSubj !== c.subject ||
      c._rowStatus !== c.status ||
      c._rowPs !== c.patch_set ||
      c._rowWip !== c.work_in_progress ||
      c._rowCrVote !== c.viewer_cr_vote ||
      c._rowVScore !== c.verified_score ||
      c._rowPvStatus !== c.presubmit_status ||
      c._rowPvScore !== c.presubmit_score ||
      c._rowPvLabel !== c.presubmit_label ||
      c._rowPvReady !== c.presubmit_ready_enabled ||
      c._rowAutoSub !== c.autosubmit_enabled ||
      c._rowUnres !== c.unresolved_comments ||
      c._rowUpdDisp !== c.updated_display ||
      c._rowFlightDisp !== c.flight_display ||
      c._rowAttnWait !== c.attention_waiting_display ||
      c._rowTopic !== c.topic ||
      c._rowBranch !== c.branch ||
      c._rowProject !== c.project ||
      c._rowStackIdx !== c.stack_index ||
      c._rowStackTot !== c.stack_total ||
      c._rowAdded !== c.added ||
      c._rowDeleted !== c.deleted ||
      c._rowLastArrSig !== c._cachedRowArrSig
    ) {
      c._rowSubj = c.subject;
      c._rowStatus = c.status;
      c._rowPs = c.patch_set;
      c._rowWip = c.work_in_progress;
      c._rowCrVote = c.viewer_cr_vote;
      c._rowVScore = c.verified_score;
      c._rowPvStatus = c.presubmit_status;
      c._rowPvScore = c.presubmit_score;
      c._rowPvLabel = c.presubmit_label;
      c._rowPvReady = c.presubmit_ready_enabled;
      c._rowAutoSub = c.autosubmit_enabled;
      c._rowUnres = c.unresolved_comments;
      c._rowUpdDisp = c.updated_display;
      c._rowFlightDisp = c.flight_display;
      c._rowAttnWait = c.attention_waiting_display;
      c._rowTopic = c.topic;
      c._rowBranch = c.branch;
      c._rowProject = c.project;
      c._rowStackIdx = c.stack_index;
      c._rowStackTot = c.stack_total;
      c._rowAdded = c.added;
      c._rowDeleted = c.deleted;
      c._rowLastArrSig = c._cachedRowArrSig;
      c._cachedRowCommitSig = `${c.number}|${c.subject || ""}|${c.status || ""}|${c.patch_set || 1}|${c.work_in_progress ? 1 : 0}|${c.viewer_cr_vote || 0}|${c.verified_score || 0}|${c.presubmit_status || ""}|${c.presubmit_score || 0}|${c.presubmit_label || ""}|${c.presubmit_ready_enabled ? 1 : 0}|${c.autosubmit_enabled ? 1 : 0}|${c.unresolved_comments || 0}|${c.updated_display || c.flight_display || ""}|${c.attention_waiting_display || ""}|${c.topic || ""}|${c.branch || ""}|${c.project || ""}|${c.stack_index || 1}|${c.stack_total || 1}|${c.added || 0}|${c.deleted || 0}|${c._cachedRowArrSig}`;
    }
    return c._cachedRowCommitSig;
  }

  function computePgDashboardRowCacheKey(c, stackId, rowOpts, isSelected, isChecked, isTurn, hasConflict, isSubmittable, isStarred, swarmObj) {
    const num = Number(c && c.number);
    const commitSig = ensurePgRowCommitSig(c);
    const stateBits =
      (rowOpts.showReviewers ? 1 : 0) |
      ((rowOpts.isYourTurnSection ? 1 : 0) << 1) |
      ((isSelected ? 1 : 0) << 2) |
      ((isChecked ? 1 : 0) << 3) |
      ((isTurn ? 1 : 0) << 4) |
      ((hasConflict ? 1 : 0) << 5) |
      ((isSubmittable ? 1 : 0) << 6) |
      ((isStarred ? 1 : 0) << 7) |
      ((state.justEnteredClNumber === num ? 1 : 0) << 8);
    const pvEntry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const pvRunStatus = pvEntry && pvEntry.data ? (pvEntry.data.run_status || "") : "";
    const pvTotal = pvEntry && pvEntry.data && pvEntry.data.counts ? `${pvEntry.data.counts.total}:${pvEntry.data.counts.running}:${pvEntry.data.counts.queued}` : "";
    const swarmSig = swarmObj ? `${swarmObj.state || ""}:${swarmObj.findings_count || 0}:${swarmObj.elapsed_sec || 0}` : "";
    return `${commitSig}|${stackId || ""}|${stateBits}|${state.sortBy || "smart"}|${state.viewer || ""}|${pvRunStatus}|${pvTotal}|${swarmSig}`;
  }

  function renderPgDashboardRowHtml(c, stackId, rowOpts = {}) {
    const num = Number(c.number);
    const isChecked = state.checkedClNumbers.has(num);
    const isSelected = state.checkedClNumbers.size === 0 && num === Number(state.selectedClNumber);
    const isTurn = isClInViewerAttentionSet(c);
    const turnCls = isTurn ? "pg-attention-turn" : "";
    const enteringCls = state.justEnteredClNumber === c.number ? "cl-card-entering" : "";
    const swarmObj = state.swarmByCl[c.number];
    const swarmingCls = swarmObj && swarmObj.state === "running" ? "cl-card-swarming" : "";
    const bundle = (c && c.number && state.diffsByCl && state.diffsByCl[c.number]) || null;
    const hasConflict = isClConflict(c, bundle);
    const conflictCls = hasConflict ? "cl-card-conflict is-conflict" : "";
    const isSubmittableVal = !hasConflict && isClSubmittable(c);
    const submittableCls = isSubmittableVal ? "cl-card-submittable" : "";
    const checkedCls = isChecked ? "is-checked" : "";
    const isStarred = isClStarred(c);
    const slotIdx = (rowOpts.showReviewers ? 2 : 0) | (rowOpts.isYourTurnSection ? 1 : 0);
    const stateBits =
      slotIdx |
      ((isSelected ? 1 : 0) << 2) |
      ((isChecked ? 1 : 0) << 3) |
      ((isTurn ? 1 : 0) << 4) |
      ((hasConflict ? 1 : 0) << 5) |
      ((isSubmittableVal ? 1 : 0) << 6) |
      ((isStarred ? 1 : 0) << 7) |
      ((state.justEnteredClNumber === num ? 1 : 0) << 8);
    const pvEntry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const hasPvData = Boolean(pvEntry && pvEntry.data);
    const commitSig = ensurePgRowCommitSig(c);
    const sortMode = state.sortBy || "smart";
    const viewerStr = state.viewer || "";
    const nonSelBits = stateBits & ~4;
    if (
      !hasPvData &&
      !c._cachedDashRowHasPvData &&
      !swarmObj &&
      c._cachedDashRowHtmls &&
      c._cachedDashRowCommitSig === commitSig &&
      c._cachedDashRowSort === sortMode &&
      c._cachedDashRowViewer === viewerStr &&
      c._cachedDashRowStackIds[slotIdx] === (stackId || "") &&
      c._cachedDashRowHtmls[slotIdx]
    ) {
      if (c._cachedDashRowBits[slotIdx] === stateBits) {
        return c._cachedDashRowHtmls[slotIdx];
      }
      if (c._cachedDashRowNonSelBits && c._cachedDashRowNonSelBits[slotIdx] === nonSelBits) {
        const prevHtml = c._cachedDashRowHtmls[slotIdx];
        const flippedHtml = isSelected
          ? '<div class="cl-card pg-change-row selected ' + prevHtml.slice(35)
          : '<div class="cl-card pg-change-row  ' + prevHtml.slice(43);
        c._cachedDashRowBits[slotIdx] = stateBits;
        c._cachedDashRowHtmls[slotIdx] = flippedHtml;
        return flippedHtml;
      }
    }
    const cacheKey = computePgDashboardRowCacheKey(
      c,
      stackId,
      rowOpts,
      isSelected,
      isChecked,
      isTurn,
      hasConflict,
      isSubmittableVal,
      isStarred,
      swarmObj
    );
    if (c._cachedDashRowKeys && c._cachedDashRowKeys[slotIdx] === cacheKey) {
      return c._cachedDashRowHtmls[slotIdx];
    }
    const cachedHtml = _pgDashboardRowHtmlCache.get(cacheKey);
    if (cachedHtml !== undefined) {
      if (!c._cachedDashRowKeys) {
        c._cachedDashRowKeys = ["", "", "", ""];
        c._cachedDashRowHtmls = ["", "", "", ""];
        c._cachedDashRowStackIds = ["", "", "", ""];
        c._cachedDashRowBits = [-1, -1, -1, -1];
        c._cachedDashRowNonSelBits = [-1, -1, -1, -1];
      }
      c._cachedDashRowHasPvData = hasPvData;
      c._cachedDashRowCommitSig = commitSig;
      c._cachedDashRowSort = sortMode;
      c._cachedDashRowViewer = viewerStr;
      c._cachedDashRowStackIds[slotIdx] = stackId || "";
      c._cachedDashRowBits[slotIdx] = stateBits;
      if (c._cachedDashRowNonSelBits) c._cachedDashRowNonSelBits[slotIdx] = nonSelBits;
      c._cachedDashRowKeys[slotIdx] = cacheKey;
      c._cachedDashRowHtmls[slotIdx] = cachedHtml;
      return cachedHtml;
    }

    const waitNote = c.attention_waiting_display ? ` (waiting ${c.attention_waiting_display})` : "";
    const isUrgentTurn = Boolean(
      isTurn && (
        (c.attention_waiting_display && /[dw]/.test(c.attention_waiting_display)) ||
        Number(c.unresolved_comments || 0) > 0 ||
        Boolean(rowOpts.isYourTurnSection && c.attention_waiting_display && (c.attention_waiting_display.includes("d") || c.attention_waiting_display.includes("w")))
      )
    );
    const turnUrgentCls = isUrgentTurn ? "is-urgent" : "";
    const turnPulseCls = isTurn ? "is-pulsing" : "";
    const urgentNote = isUrgentTurn ? " (Urgent)" : "";
    const turnBadgeHtml = isTurn
      ? `<span class="pg-turn-badge is-hoverable ${turnPulseCls} ${turnUrgentCls}" data-cl="${c.number}" data-user="${escapeHtml(state.viewer || 'viewer')}" onmouseenter="showPgAttentionHovercard(event, ${c.number}, '${escapeHtml(state.viewer || 'viewer')}')" onmouseleave="scheduleHidePgAttentionHovercard()" onclick="event.stopPropagation(); togglePgAttentionHovercard(event, ${c.number}, '${escapeHtml(state.viewer || 'viewer')}')" title="${escapeHtml("Your turn in Gerrit Attention Set" + urgentNote + waitNote + " · Hover for details")}"><span class="pg-turn-dot" aria-hidden="true"></span></span>`
      : "";
    const unresCnt = Number(c.unresolved_comments || 0);
    const unresHtml = unresCnt > 0
      ? `<span class="pg-unresolved-pill" title="${unresCnt} unresolved comment thread${unresCnt === 1 ? "" : "s"}">💬 ${unresCnt}</span>`
      : "";

    const repoLabel = formatRepoDisplayPath(c);
    const branchStr = (c.branch && c.branch !== "main" && c.branch !== "studio-main" && c.branch !== "emu-master-dev")
      ? ` (${c.branch})`
      : "";
    const stackTagHtml = Number(c.stack_total || 1) > 1
      ? `<span class="pg-stack-tag" title="${escapeHtml(c.stack_label || "Stack commit")}">[${c.stack_index}/${c.stack_total}]</span>`
      : "";
    const bugBadgesHtml = renderBugBadgesHtml(c);
    const asHtml = c.autosubmit_enabled
      ? `<span class="pg-meta-as" title="Autosubmit +1 enabled">⇪ AS+1</span>`
      : "";
    const swarmSlotHtml = renderClSwarmBadgeHtml(c.number);

    const useWaitingAge = Boolean(rowOpts.isYourTurnSection && c.attention_waiting_display);
    const ageLabel = useWaitingAge
      ? c.attention_waiting_display
      : (c.updated_display || c.flight_display || "");
    const ageTitle = useWaitingAge
      ? `Waiting on your turn for ${c.attention_waiting_display}${c.updated_iso ? " · Updated " + c.updated_iso : ""}`
      : (c.updated_iso ? `Last updated: ${c.updated_iso}` : "Last updated");
    const sz = computePgChangeSize(c);
    const starBtnHtml = `<button type="button" class="pg-star-btn ${isStarred ? "is-starred" : ""}" data-cl-num="${c.number}" onclick="togglePgStarChange(${c.number}, event)" title="${isStarred ? `Unstar ag/${c.number} (*)` : `Star ag/${c.number} (*)`}" aria-label="${isStarred ? "Starred" : "Not starred"}"><span aria-hidden="true">${isStarred ? "★" : "☆"}</span></button>`;
    const compactWho = formatPgCompactWhoSummary(c, Boolean(rowOpts.showReviewers));
    const compactParts = [compactWho, ageLabel].filter(Boolean);
    const compactMetaHtml = compactParts.length > 0
      ? `<span class="pg-compact-inline-meta"><span class="pg-meta-sep">·</span> ${escapeHtml(compactParts.join(" · "))}</span>`
      : "";
    const miniSizeBadgeHtml = `<span class="pg-mini-size-chip ${sz.cls}" title="${escapeHtml(sz.tooltip)}">${sz.tier}</span>`;

    const rowHtml = `<div class="cl-card pg-change-row ${isSelected ? "selected" : ""} ${turnCls} ${checkedCls} ${conflictCls} ${submittableCls} ${enteringCls} ${swarmingCls}" data-cl-num="${c.number}" onclick="onClCardBodyClick('${escapeHtml(stackId || "")}', ${c.number}, event)">
      <div class="pg-cell-select">
        <span class="pg-row-cursor" aria-hidden="true">▸</span>
        <label class="cl-card-check-wrap" onclick="event.stopPropagation()" title="Select ag/${c.number} for batch actions (Shift+click for range)">
          <input type="checkbox" class="cl-select-checkbox" data-cl-num="${c.number}" ${isChecked ? "checked" : ""} onclick="toggleClCheckbox(${c.number}, event)" aria-label="Select ag/${c.number}" />
        </label>
      </div>
      <div class="pg-cell-subject">
        <div class="pg-subj-line">
          ${starBtnHtml}${turnBadgeHtml}<span id="pgStatusSlot-${c.number}" class="pg-status-slot">${renderPgStatusTagHtml(c)}</span><span class="cl-subj" title="${escapeHtml(c.subject)}">${escapeHtml(c.subject)}</span>${unresHtml}
        </div>
        <div class="pg-sub-meta">
          <span class="pg-sub-repo" title="Repository: ${escapeHtml((c.project || repoLabel) + branchStr)}">${escapeHtml(repoLabel + branchStr)}</span>
          <span class="pg-meta-sep pg-sub-repo-sep">·</span>
          <a class="pg-sub-ag ag-link" style="font-size:10.5px;" href="${escapeHtml(c.url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">ag/${c.number} ↗</a>
          ${miniSizeBadgeHtml}
          ${c.topic ? `<span class="pg-topic-chip" title="Topic: ${escapeHtml(c.topic)}" onclick="event.stopPropagation(); applyPgTopicFilter('${escapeHtml(c.topic)}', ${c.number})">${escapeHtml(c.topic)}</span>` : ""}
          ${(Array.isArray(c.hashtags) && c.hashtags.length > 0) ? c.hashtags.map(t => `<span class="pg-hashtag-chip-mini" title="Hashtag: #${escapeHtml(t)}" onclick="event.stopPropagation(); filterDashboardByHashtag('${escapeHtml(t)}')">#${escapeHtml(t)}</span>`).join("") : ""}
          ${compactMetaHtml}
          ${stackTagHtml}
          ${bugBadgesHtml}
          ${asHtml}
          <span class="swarm-slot-wrap" data-swarm-cl="${c.number}">${swarmSlotHtml}</span>
        </div>
      </div>
      <div class="pg-cell-who pg-col-who">${renderPgWhoCellHtml(c, Boolean(rowOpts.showReviewers))}</div>
      <div class="pg-cell-age pg-col-age ${state.sortBy !== "smart" ? "sort-highlight" : ""}" title="${escapeHtml(ageTitle)}">${escapeHtml(ageLabel)}</div>
      <div class="pg-cell-size pg-col-size"><span class="pg-size-badge ${sz.cls}" title="${escapeHtml(sz.tooltip)}">${sz.tier}</span></div>
      <div class="pg-cell-label">${renderPgCrVoteCellHtml(c)}</div>
      <div class="pg-cell-label pg-col-v">${renderPgVVoteCellHtml(c)}</div>
      <div class="pg-cell-label">${renderPgPvVoteCellHtml(c, stackId)}</div>
    </div>`;
    if (_pgDashboardRowHtmlCache.size > 2000) {
      _pgDashboardRowHtmlCache.clear();
    }
    _pgDashboardRowHtmlCache.set(cacheKey, rowHtml);
    if (!c._cachedDashRowKeys) {
      c._cachedDashRowKeys = ["", "", "", ""];
      c._cachedDashRowHtmls = ["", "", "", ""];
      c._cachedDashRowStackIds = ["", "", "", ""];
      c._cachedDashRowBits = [-1, -1, -1, -1];
      c._cachedDashRowNonSelBits = [-1, -1, -1, -1];
    }
    c._cachedDashRowHasPvData = hasPvData;
    c._cachedDashRowCommitSig = commitSig;
    c._cachedDashRowSort = sortMode;
    c._cachedDashRowViewer = viewerStr;
    c._cachedDashRowStackIds[slotIdx] = stackId || "";
    c._cachedDashRowBits[slotIdx] = stateBits;
    if (c._cachedDashRowNonSelBits) c._cachedDashRowNonSelBits[slotIdx] = nonSelBits;
    c._cachedDashRowKeys[slotIdx] = cacheKey;
    c._cachedDashRowHtmls[slotIdx] = rowHtml;
    return rowHtml;
  }

  function getPgPresubmitTargetsDataSig() {
    const map = state.presubmitTargetsByCl;
    if (!map) return "";
    let sig = "";
    for (const k in map) {
      if (Object.prototype.hasOwnProperty.call(map, k)) {
        const entry = map[k];
        if (entry && entry.data) {
          const d = entry.data;
          const cnt = d.counts;
          sig += `${k}:${d.run_status || ""}:${cnt ? `${cnt.total}:${cnt.running}:${cnt.queued}` : ""};`;
        }
      }
    }
    return sig;
  }

  function renderClCardHtml(c, stackId) {
    return renderPgDashboardRowHtml(c, stackId, {
      showReviewers: isClOwnedByViewer(c),
      isYourTurnSection: false,
    });
  }

  function renderPgColHeaderRowHtml(groupNums, groupTitle, whoLabel = "Owner", ageLabel = "Updated") {
    const cleanNums = (groupNums || []).map(Number).filter(n => n > 0);
    const hasChecks = state.checkedClNumbers && state.checkedClNumbers.size > 0;
    const checkedCnt = hasChecks ? cleanNums.filter(n => state.checkedClNumbers.has(n)).length : 0;
    const cacheKey = `${cleanNums.join(",")}|${checkedCnt}|${groupTitle || ""}|${whoLabel}|${ageLabel}`;
    const cached = _pgColHeaderRowHtmlCache.get(cacheKey);
    if (cached !== undefined) return cached;
    const html = AemuGerritReact.renderPgColHeaderRowHtml(cleanNums, checkedCnt, groupTitle, whoLabel, ageLabel);
    if (_pgColHeaderRowHtmlCache.size >= 128) _pgColHeaderRowHtmlCache.clear();
    _pgColHeaderRowHtmlCache.set(cacheKey, html);
    return html;
  }

  function renderPgDashboardSectionHtml(sec) {
    const isCollapsed = state.collapsedDashboardSections.has(sec.id);
    const count = sec.items.length;
    if (!sec._cachedGroupNums || sec._cachedGroupNums.length !== count || !sec._cachedNumToRowIdx) {
      const nums = new Array(count);
      const idxMap = new Map();
      for (let i = 0; i < count; i++) {
        const num = Number(sec.items[i].commit.number);
        nums[i] = num;
        idxMap.set(num, i);
      }
      sec._cachedGroupNums = nums;
      sec._cachedNumToRowIdx = idxMap;
    }
    const groupNums = sec._cachedGroupNums;
    const caret = isCollapsed ? "▸" : "▾";
    const checkedSize = state.checkedClNumbers ? state.checkedClNumbers.size : 0;
    let colHdrHtml = "";
    if (!isCollapsed && count > 0) {
      const colHdrTitle = `Select all ${count} commit${count === 1 ? "" : "s"} in ${sec.title}`;
      const colHdrKey = `${count}|${groupNums[0] || 0}|${groupNums[count - 1] || 0}|${colHdrTitle}|${sec.whoHeader || ""}|${sec.ageHeader || ""}`;
      if (checkedSize === 0 && sec._cachedColHdrKey === colHdrKey && typeof sec._cachedColHdrHtml === "string") {
        colHdrHtml = sec._cachedColHdrHtml;
      } else {
        colHdrHtml = renderPgColHeaderRowHtml(
          groupNums,
          colHdrTitle,
          sec.whoHeader,
          sec.ageHeader
        );
        if (checkedSize === 0) {
          sec._cachedColHdrKey = colHdrKey;
          sec._cachedColHdrHtml = colHdrHtml;
        }
      }
    }
    let bodyHtml = "";
    let anyRowChanged = false;
    if (!isCollapsed) {
      if (count === 0) {
        bodyHtml = `<div class="pg-empty-section-row">No changes</div>`;
      } else {
        const rowOpts = {
          showReviewers: sec.showReviewers,
          isYourTurnSection: sec.isYourTurnSection,
        };
        const curSelectedCl = checkedSize === 0 ? Number(state.selectedClNumber || 0) : 0;
        const canUseTargetedSelectState = (
          checkedSize === 0 &&
          !state.justEnteredClNumber &&
          !hasAnyOwnProp(state.swarmByCl)
        );
        const hasWarmRowCache = (
          canUseTargetedSelectState &&
          Array.isArray(sec._cachedRowHtmls) &&
          sec._cachedRowHtmls.length === count &&
          typeof sec._cachedBodyHtml === "string"
        );
        const curRowsStateSig = `${_lastDashboardSectionsSig}|${state.sortBy || "smart"}|${state.viewer || ""}|${sec.showReviewers ? 1 : 0}|${sec.isYourTurnSection ? 1 : 0}|${getPgPresubmitTargetsDataSig()}`;
        if (hasWarmRowCache && sec._cachedRowsStateSig === curRowsStateSig) {
          const prevSelectedCl = sec._cachedSelectedClNum || 0;
          if (prevSelectedCl !== curSelectedCl) {
            const rowHtmls = sec._cachedRowHtmls;
            const idxMap = sec._cachedNumToRowIdx;
            const prevIdx = prevSelectedCl ? idxMap.get(prevSelectedCl) : undefined;
            if (prevIdx !== undefined) {
              const it = sec.items[prevIdx];
              const rHtml = renderPgDashboardRowHtml(it.commit, it.stack.stack_id, rowOpts);
              if (rowHtmls[prevIdx] !== rHtml) {
                rowHtmls[prevIdx] = rHtml;
                anyRowChanged = true;
              }
            }
            const nextIdx = curSelectedCl ? idxMap.get(curSelectedCl) : undefined;
            if (nextIdx !== undefined && nextIdx !== prevIdx) {
              const it = sec.items[nextIdx];
              const rHtml = renderPgDashboardRowHtml(it.commit, it.stack.stack_id, rowOpts);
              if (rowHtmls[nextIdx] !== rHtml) {
                rowHtmls[nextIdx] = rHtml;
                anyRowChanged = true;
              }
            }
            sec._cachedSelectedClNum = curSelectedCl;
            if (anyRowChanged) {
              let bHtml = "";
              for (let i = 0; i < count; i++) {
                bHtml += rowHtmls[i];
              }
              sec._cachedBodyHtml = bHtml;
            }
          }
          bodyHtml = sec._cachedBodyHtml;
        } else {
          anyRowChanged = !sec._cachedRowHtmls || sec._cachedRowHtmls.length !== count;
          const rowHtmls = anyRowChanged ? new Array(count) : sec._cachedRowHtmls;
          for (let i = 0; i < count; i++) {
            const it = sec.items[i];
            const rHtml = renderPgDashboardRowHtml(it.commit, it.stack.stack_id, rowOpts);
            if (!anyRowChanged && rowHtmls[i] !== rHtml) {
              anyRowChanged = true;
            }
            rowHtmls[i] = rHtml;
          }
          if (anyRowChanged || typeof sec._cachedBodyHtml !== "string") {
            sec._cachedRowHtmls = rowHtmls;
            let bHtml = "";
            for (let i = 0; i < count; i++) {
              bHtml += rowHtmls[i];
            }
            sec._cachedBodyHtml = bHtml;
          }
          sec._cachedRowsStateSig = canUseTargetedSelectState ? curRowsStateSig : "";
          sec._cachedSelectedClNum = curSelectedCl;
          bodyHtml = sec._cachedBodyHtml;
        }
      }
    }
    const secShellKey = `${isCollapsed ? 1 : 0}|${count}|${sec.title || ""}|${sec.query || ""}|${colHdrHtml}`;
    if (!anyRowChanged && sec._cachedSectionKey === secShellKey && typeof sec._cachedSectionHtml === "string") {
      return sec._cachedSectionHtml;
    }
    const customHeaderExtraHtml = sec.isCustomSection
      ? `<span class="pg-custom-sec-pill" onclick="event.stopPropagation(); setQueueSearchAndApply('${escapeHtml(sec.query)}')" title="Filter dashboard by: ${escapeHtml(sec.query)}">🔍 ${escapeHtml(sec.query)}</span>`
      : "";
    const customActionsHtml = sec.isCustomSection
      ? `<div class="pg-section-actions" onclick="event.stopPropagation()">
          <button type="button" class="pg-btn-sec-action" onclick="openSavedSearchesModal('${escapeHtml(sec.id)}')" title="Edit this custom dashboard section">✏️</button>
          <button type="button" class="pg-btn-sec-action" onclick="toggleSavedSearchDashboardVisibility('${escapeHtml(sec.id)}')" title="Hide section from dashboard">✕</button>
        </div>`
      : "";

    const secHtml = `<div class="pg-section-box stack-box ${isCollapsed ? "collapsed" : ""}" data-pg-section="${escapeHtml(sec.id)}">
      <div class="pg-section-header" onclick="toggleDashboardSection('${escapeHtml(sec.id)}')" title="Click to collapse or expand ${escapeHtml(sec.title)}">
        <span class="pg-section-title-wrap">
          <span class="pg-section-chevron" aria-hidden="true">${caret}</span>
          <span class="pg-section-name">${escapeHtml(sec.title)}</span>
          <span class="pg-section-count">(${count})</span>
          ${customHeaderExtraHtml}
        </span>
        ${customActionsHtml}
      </div>
      <div class="pg-section-body">${colHdrHtml}${bodyHtml}</div>
    </div>`;
    sec._cachedSectionKey = secShellKey;
    sec._cachedSectionHtml = secHtml;
    return secHtml;
  }

  function renderGroupCheckboxHtml(nums, labelTitle) {
    const cleanNums = (nums || []).map(Number).filter(n => n > 0);
    const checkedCnt = cleanNums.filter(n => state.checkedClNumbers.has(n)).length;
    return AemuGerritReact.renderGroupCheckboxHtml(cleanNums, checkedCnt, labelTitle);
  }

  function buildQueueBatchIdleHintHtml() {
    return AemuGerritReact.buildQueueBatchIdleHintHtml();
  }

  function renderQueueBatchSelectStripHtml(allVisibleNums) {
    const totalChecked = state.checkedClNumbers ? state.checkedClNumbers.size : 0;
    const rawList = allVisibleNums || [];
    if (totalChecked === 0 && rawList.length > 0 && rawList[0] > 0 && rawList[rawList.length - 1] > 0) {
      const fastKey = `${rawList.length}:${rawList[0]}:${rawList[rawList.length - 1]}|0|0`;
      const fastCached = _pgBatchSelectStripHtmlCache.get(fastKey);
      if (fastCached !== undefined) return fastCached;
    }
    const cleanNums = rawList.map(Number).filter(n => n > 0);
    const checkedCnt = totalChecked > 0 ? cleanNums.filter(n => state.checkedClNumbers.has(n)).length : 0;
    const cacheKey = `${cleanNums.length}:${cleanNums[0] || 0}:${cleanNums[cleanNums.length - 1] || 0}|${checkedCnt}|${totalChecked}`;
    const cached = _pgBatchSelectStripHtmlCache.get(cacheKey);
    if (cached !== undefined) return cached;
    const html = AemuGerritReact.renderQueueBatchSelectStripHtml(cleanNums, checkedCnt, totalChecked);
    if (_pgBatchSelectStripHtmlCache.size >= 64) _pgBatchSelectStripHtmlCache.clear();
    _pgBatchSelectStripHtmlCache.set(cacheKey, html);
    return html;
  }

  function toggleClCheckbox(clNum, event) {
    if (event) event.stopPropagation();
    const num = Number(clNum);
    if (!num) return;
    const hasExplicitTarget = Boolean(event && event.target && typeof event.target.checked === "boolean");
    const shouldCheck = hasExplicitTarget ? Boolean(event.target.checked) : !state.checkedClNumbers.has(num);

    const anchorNum = Number(state.lastCheckedClNumber || state.selectedClNumber || 0);
    if (event && event.shiftKey && anchorNum && anchorNum !== num) {
      const visibleItems = (typeof getOrderedVisibleCommits === "function" && typeof getVisibleStacks === "function")
        ? getOrderedVisibleCommits(getVisibleStacks())
        : [];
      const orderedNums = visibleItems.length > 0
        ? visibleItems.map(x => Number(x.commit.number))
        : (state.changes || []).map(c => Number(c.number));
      const idxA = orderedNums.indexOf(anchorNum);
      const idxB = orderedNums.indexOf(num);
      if (idxA >= 0 && idxB >= 0) {
        const lo = Math.min(idxA, idxB);
        const hi = Math.max(idxA, idxB);
        for (let i = lo; i <= hi; i++) {
          if (shouldCheck) state.checkedClNumbers.add(orderedNums[i]);
          else state.checkedClNumbers.delete(orderedNums[i]);
        }
        state.lastCheckedClNumber = num;
        syncQueueSelectionDom();
        return;
      }
    }

    if (shouldCheck) state.checkedClNumbers.add(num);
    else state.checkedClNumbers.delete(num);
    state.lastCheckedClNumber = num;
    syncQueueSelectionDom();
  }

  function toggleGroupCheckbox(groupNums, event) {
    if (event) event.stopPropagation();
    const cleanNums = (groupNums || []).map(Number).filter(n => n > 0);
    if (!cleanNums.length) return;
    const allAlreadyChecked = cleanNums.every(n => state.checkedClNumbers.has(n));
    const shouldCheck = !allAlreadyChecked;
    cleanNums.forEach(n => {
      if (shouldCheck) state.checkedClNumbers.add(n);
      else state.checkedClNumbers.delete(n);
    });
    state.lastCheckedClNumber = cleanNums[cleanNums.length - 1];
    syncQueueSelectionDom();
  }

  function clearCheckedCls(opts = {}) {
    state.checkedClNumbers.clear();
    state.lastCheckedClNumber = null;
    closeBatchReviewerComposer();
    if (!opts.silent) {
      syncQueueSelectionDom();
    }
  }

  function syncGroupCheckboxIndeterminateDom() {
    document.querySelectorAll(".group-select-checkbox").forEach(box => {
      const csv = box.getAttribute("data-group-cls") || "";
      const nums = csv.split(",").map(Number).filter(n => n > 0);
      const checkedCnt = nums.filter(n => state.checkedClNumbers.has(n)).length;
      box.checked = nums.length > 0 && checkedCnt === nums.length;
      box.indeterminate = checkedCnt > 0 && checkedCnt < nums.length;
      const colHdr = typeof box.closest === "function" ? box.closest(".pg-col-header-row") : null;
      if (colHdr) {
        colHdr.classList.toggle("has-selection", checkedCnt > 0);
      }
    });
  }

  function syncQueueSelectionDom() {
    const totalChecked = state.checkedClNumbers.size;
    document.querySelectorAll(".cl-select-checkbox").forEach(box => {
      const num = Number(box.getAttribute("data-cl-num") || 0);
      const isChecked = state.checkedClNumbers.has(num);
      box.checked = isChecked;
      const card = box.closest(".cl-card");
      if (card) {
        card.classList.toggle("is-checked", isChecked);
        card.classList.toggle("selected", totalChecked === 0 && num === Number(state.selectedClNumber));
      }
    });
    syncGroupCheckboxIndeterminateDom();
    const stripEl = document.getElementById("queueBatchStrip");
    const metaEl = document.getElementById("queueBatchSelectMeta");
    if (stripEl) {
      stripEl.classList.toggle("has-checked", totalChecked > 0);
    }
    if (metaEl) {
      if (totalChecked > 0) {
        metaEl.className = "queue-checked-pill";
        if (typeof metaEl.removeAttribute === "function") metaEl.removeAttribute("style");
        metaEl.innerHTML = `<span>${totalChecked} selected</span><button type="button" class="nudge-btn" onclick="event.stopPropagation(); clearCheckedCls()">Clear ✕</button>`;
      } else {
        metaEl.className = "";
        metaEl.style.color = "var(--g-subtle)";
        metaEl.innerHTML = buildQueueBatchIdleHintHtml();
      }
    }
    if (totalChecked === 0 && state.batchReviewerComposer) {
      closeBatchReviewerComposer();
    }
    const wasInBatchDom = Boolean(document.getElementById("batchModeSection"));
    if (totalChecked > 0 || wasInBatchDom) {
      renderDiffPane({ preserveScroll: totalChecked > 0 && wasInBatchDom });
      if (totalChecked === 0) {
        ensureActiveDiffsLoaded({ preserveScroll: false });
      }
      return;
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function updatePgDashboardDragPill(event, drag, count, startNum, currentNum) {
    if (!drag || typeof document === "undefined") return;
    let pill = document.getElementById("pgDashboardDragPill");
    if (!pill) {
      pill = document.createElement("div");
      pill.id = "pgDashboardDragPill";
      pill.className = "pg-dashboard-drag-pill";
      if (document.body && typeof document.body.appendChild === "function") {
        document.body.appendChild(pill);
      }
    }
    const countLabel = count === 1 ? "1 change" : `${count} changes`;
    const clHint = (Number(startNum) === Number(currentNum))
      ? `ag/${startNum}`
      : `ag/${startNum} … ag/${currentNum}`;
    let icon = "☑";
    let actionHint = "Release to select for batch edit";
    if (drag.fromCheckbox) {
      icon = drag.initialChecked ? "☐" : "☑";
      actionHint = drag.initialChecked ? "Release to deselect range" : "Release to check range";
    }
    pill.innerHTML = `<span class="pg-dashboard-drag-icon" aria-hidden="true">${icon}</span> <strong class="pg-dashboard-drag-count">${countLabel}</strong> <span class="pg-dashboard-drag-hint">(${escapeHtml(clHint)}) · ${actionHint}</span>`;
    const clientX = (event && event.clientX != null) ? event.clientX : 0;
    const clientY = (event && event.clientY != null) ? event.clientY : 0;
    if (pill.style) {
      pill.style.left = `${Math.max(12, clientX + 16)}px`;
      pill.style.top = `${Math.max(12, clientY - 14)}px`;
      pill.style.display = "inline-flex";
    }
  }

  function removePgDashboardDragPill() {
    if (typeof document === "undefined") return;
    const pill = document.getElementById("pgDashboardDragPill");
    if (pill) {
      if (pill.style) pill.style.display = "none";
      if (typeof pill.remove === "function") {
        pill.remove();
      } else if (pill.parentNode && typeof pill.parentNode.removeChild === "function") {
        pill.parentNode.removeChild(pill);
      }
    }
  }

  function clearPgDashboardRangeHover() {
    if (typeof document === "undefined" || typeof document.querySelectorAll !== "function") return;
    document.querySelectorAll(".cl-card.is-range-hover, .cl-card.is-drag-preview").forEach(r => {
      if (r && r.classList) {
        r.classList.remove("is-range-hover");
        r.classList.remove("is-drag-preview");
      }
    });
  }

  function cancelPgDashboardDrag() {
    if (!state.dashboardDrag) return;
    state.dashboardDrag = null;
    state.suppressCardClick = true;
    setTimeout(() => { state.suppressCardClick = false; }, 150);
    if (typeof document !== "undefined" && document.body && document.body.classList) {
      document.body.classList.remove("is-dashboard-dragging");
    }
    removePgDashboardDragPill();
    clearPgDashboardRangeHover();
    if (typeof deselectBrowserSelection === "function") {
      deselectBrowserSelection();
    }
  }

  function initPgDashboardRangeDragListeners() {
    if (typeof document === "undefined") return;
    if (document._hasDashboardRangeDragListeners) return;
    document._hasDashboardRangeDragListeners = true;

    if (typeof document.addEventListener !== "function") return;

    document.addEventListener("mousedown", (event) => {
      if (!event || event.button !== 0) return;
      const target = event.target;
      if (!target || typeof target.closest !== "function") return;
      const card = target.closest(".cl-card");
      if (!card) return;
      if (target.closest("a, .ag-link, .pg-star-btn, .pg-topic-chip, .pg-hashtag-chip-mini, button:not(.group-select-checkbox):not(.cl-select-checkbox)")) {
        return;
      }
      const rawNum = (typeof card.getAttribute === "function") ? card.getAttribute("data-cl-num") : null;
      const clNum = Number(rawNum || 0);
      if (!clNum) return;

      const fromCheckbox = Boolean(target.closest(".cl-card-check-wrap, .cl-select-checkbox, .pg-cell-select"));
      const initialChecked = Boolean(state.checkedClNumbers && state.checkedClNumbers.has(clNum));

      state.dashboardDrag = {
        active: false,
        isMouseDown: true,
        startNum: clNum,
        currentNum: clNum,
        startX: event.clientX || 0,
        startY: event.clientY || 0,
        fromCheckbox,
        initialChecked,
      };
    });

    document.addEventListener("mousemove", (event) => {
      if (!state.dashboardDrag || !state.dashboardDrag.isMouseDown) return;
      const drag = state.dashboardDrag;
      const deltaY = Math.abs((event.clientY || 0) - drag.startY);
      const deltaX = Math.abs((event.clientX || 0) - drag.startX);
      if (!drag.active && (deltaY >= 5 || deltaX >= 5)) {
        drag.active = true;
        if (typeof document !== "undefined" && document.body && document.body.classList) {
          document.body.classList.add("is-dashboard-dragging");
        }
      }
      if (!drag.active) return;
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      if (typeof deselectBrowserSelection === "function") {
        deselectBrowserSelection();
      }

      let hoverCard = (event.target && typeof event.target.closest === "function") ? event.target.closest(".cl-card") : null;
      if (!hoverCard && typeof document !== "undefined" && typeof document.elementFromPoint === "function") {
        const elAtPoint = document.elementFromPoint(event.clientX, event.clientY);
        hoverCard = (elAtPoint && typeof elAtPoint.closest === "function") ? elAtPoint.closest(".cl-card") : null;
      }

      if (hoverCard && typeof hoverCard.getAttribute === "function") {
        const hoverNum = Number(hoverCard.getAttribute("data-cl-num") || 0);
        if (hoverNum) {
          drag.currentNum = hoverNum;
        }
      }

      const orderedNums = (typeof getOrderedVisibleCommits === "function" && typeof getVisibleStacks === "function")
        ? getOrderedVisibleCommits(getVisibleStacks()).map(x => Number(x.commit.number))
        : (state.changes || []).map(c => Number(c.number));

      const idxA = orderedNums.indexOf(Number(drag.startNum));
      const idxB = orderedNums.indexOf(Number(drag.currentNum));
      if (idxA !== -1 && idxB !== -1) {
        const start = Math.min(idxA, idxB);
        const end = Math.max(idxA, idxB);
        const range = orderedNums.slice(start, end + 1);
        const rangeSet = new Set(range);

        if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") {
          const cards = document.querySelectorAll(".cl-card");
          cards.forEach(r => {
            if (!r || typeof r.getAttribute !== "function" || !r.classList) return;
            const n = Number(r.getAttribute("data-cl-num") || 0);
            const inRange = Boolean(n && rangeSet.has(n));
            r.classList.toggle("is-range-hover", inRange);
            r.classList.toggle("is-drag-preview", inRange);
          });
        }

        updatePgDashboardDragPill(event, drag, range.length, drag.startNum, drag.currentNum);
      }
    });

    document.addEventListener("mouseup", (event) => {
      if (!state.dashboardDrag) return;
      const drag = state.dashboardDrag;
      state.dashboardDrag = null;
      if (typeof document !== "undefined" && document.body && document.body.classList) {
        document.body.classList.remove("is-dashboard-dragging");
      }
      removePgDashboardDragPill();
      clearPgDashboardRangeHover();

      if (drag.active) {
        state.suppressCardClick = true;
        setTimeout(() => { state.suppressCardClick = false; }, 150);

        const orderedNums = (typeof getOrderedVisibleCommits === "function" && typeof getVisibleStacks === "function")
          ? getOrderedVisibleCommits(getVisibleStacks()).map(x => Number(x.commit.number))
          : (state.changes || []).map(c => Number(c.number));

        const idxA = orderedNums.indexOf(Number(drag.startNum));
        const idxB = orderedNums.indexOf(Number(drag.currentNum));
        if (idxA !== -1 && idxB !== -1) {
          const start = Math.min(idxA, idxB);
          const end = Math.max(idxA, idxB);
          const range = orderedNums.slice(start, end + 1);

          if (drag.fromCheckbox) {
            const targetChecked = !drag.initialChecked;
            range.forEach(num => {
              if (targetChecked) state.checkedClNumbers.add(num);
              else state.checkedClNumbers.delete(num);
            });
            state.lastCheckedClNumber = Number(drag.currentNum);
            syncQueueSelectionDom();
            if (typeof showToast === "function") {
              showToast(targetChecked ? `Checked ${range.length} changes for batch actions ✓` : `Unchecked ${range.length} changes`);
            }
          } else {
            range.forEach(num => state.checkedClNumbers.add(num));
            state.lastCheckedClNumber = Number(drag.currentNum);
            syncQueueSelectionDom();
            if (typeof showToast === "function") {
              showToast(`Selected ${range.length} changes for batch actions (Batch Edit) ✓`);
            }
          }
        }
      }
    });

    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
      window.addEventListener("blur", () => {
        cancelPgDashboardDrag();
      });
    }
  }

  let _lastFilterBannerKey = "";
  let _lastFilterBannerHtml = "";
  function renderQueueFilterBannerHtml(summary) {
    const af = state.authorFilter || "all";
    const sq = state.searchQuery || "";
    const inc = Boolean(state.includeUngatedMatches);
    const s = summary || {};
    const key = `${af}|${sq}|${inc ? 1 : 0}|${s.filterActive ? 1 : 0}|${s.gatedCommitCount || 0}|${s.ungatedCommitCount || 0}|${s.hiddenByGatesCount || 0}|${s.autoBypassedGates ? 1 : 0}`;
    if (_lastFilterBannerKey === key) return _lastFilterBannerHtml;
    _lastFilterBannerKey = key;
    _lastFilterBannerHtml = AemuGerritReact.renderQueueFilterBannerHtml(summary, af, sq, inc);
    return _lastFilterBannerHtml;
  }

  let _lastHydratingPillState = null;
  let _lastHydratingPillHtml = "";
  function renderSidebarHydratingPillHtml() {
    const h = Boolean(state.sidebarHydrating);
    if (_lastHydratingPillState === h) return _lastHydratingPillHtml;
    _lastHydratingPillState = h;
    _lastHydratingPillHtml = AemuGerritReact.renderSidebarHydratingPillHtml(h);
    return _lastHydratingPillHtml;
  }

  const _pgTypeGroupBoxCache = new Map();

  function renderQueueList(visibleStacks, summaryOverride) {
    const container = document.getElementById("queueList");
    if (container) {
      if (_lastSubmittableQueueSig) container._lastSubmittableSyncSig = _lastSubmittableQueueSig;
      container._cachedFastCardsRef = null;
    }
    const summary = summaryOverride || getFilteredStacksSummary();
    const bannerHtml = renderQueueFilterBannerHtml(summary);
    const hydratingHtml = renderSidebarHydratingPillHtml();

    if (visibleStacks.length === 0) {
      if (state.sidebarHydrating) {
        container.innerHTML = `${bannerHtml}${hydratingHtml}`;
        return;
      }
      if (state.groupMode === "dashboard" && !summary.filterActive && countActiveQueueFilters() === 0 && !state.queueLoading) {
        const emptySections = partitionCommitsIntoDashboardSections([]);
        if (emptySections.length > 0) {
          container.innerHTML = emptySections.map(sec => renderPgDashboardSectionHtml(sec)).join("");
          return;
        }
      }
      const qRaw = (state.searchQuery || "").trim() || (state.authorFilter !== "all" ? state.authorFilter : "");
      const showUnverifiedBtn = (state.gates.presubmit && state.changes.length > 0)
        ? `<button class="btn" onclick="toggleGate('presubmit')">👁 Turn Off PV +1/+2 Filter</button>`
        : "";
      const showMineBtn = (state.gates.hideMine && state.changes.some(isClApprovedByViewer))
        ? `<button class="btn" onclick="toggleGate('hideMine')">👁 Show +2 by Me</button>`
        : "";
      const showSubmittableBtn = (state.gates.hideSubmittable && state.changes.some(isClSubmittable))
        ? `<button class="btn" onclick="toggleGate('hideSubmittable')">👁 Show Submittable CLs</button>`
        : "";
      const showStarredBtn = (state.gates.starredOnly && state.changes.some(isClStarred))
        ? `<button class="btn" onclick="toggleGate('starredOnly')">👁 Turn Off Starred Filter</button>`
        : "";
      const showUngatedSearchBtn = (summary.filterActive && summary.hiddenByGatesCount > 0 && !state.includeUngatedMatches)
        ? `<button class="btn" onclick="toggleIncludeUngatedMatches()">👁 Show All ${summary.ungatedCommitCount} Matching CLs</button>`
        : "";
      const actionBtns = [];
      if (showUngatedSearchBtn) actionBtns.push(showUngatedSearchBtn);
      if (showUnverifiedBtn) actionBtns.push(showUnverifiedBtn);
      if (showStarredBtn) actionBtns.push(showStarredBtn);
      if (showMineBtn) actionBtns.push(showMineBtn);
      if (showSubmittableBtn) actionBtns.push(showSubmittableBtn);
      if (qRaw) {
        actionBtns.push(`<button class="btn" onclick="queryGerritForSearch('${escapeHtml(qRaw)}')">🔎 Search Gerrit for "${escapeHtml(qRaw)}"</button>`);
        actionBtns.push(`<button class="btn" onclick="queryGerritForAuthor('${escapeHtml(qRaw)}')">🔎 Query Gerrit for owner:${escapeHtml(qRaw)}</button>`);
        actionBtns.push(`<button class="btn" onclick="clearQueueSearchAndAuthor()">Clear Search / Author Filter</button>`);
      }
      const actionsRowHtml = actionBtns.length > 0
        ? `<div style="margin-top:10px;display:flex;justify-content:center;gap:8px;flex-wrap:wrap;">${actionBtns.join("")}</div>`
        : "";
      const emptyMsgHtml = state.remoteSearchLoading
        ? `<span class="pv-mini-spinner"></span> Searching Gerrit for <strong>${escapeHtml(qRaw)}</strong>…`
        : `No CLs match the current filter${qRaw ? ` (<strong>${escapeHtml(qRaw)}</strong>)` : ""}. Toggle off a gate pill or type filter above to view unverified or already-approved CLs.`;
      container.innerHTML = `${bannerHtml}<div style="padding:24px;text-align:center;color:var(--g-muted);">
        ${emptyMsgHtml}
        ${actionsRowHtml}
      </div>`;
      return;
    }

    if (state.groupMode === "dashboard") {
      const sections = (visibleStacks && visibleStacks._cachedDashboardSections === _lastDashboardSectionsResult && _lastDashboardSectionsResult)
        ? _lastDashboardSectionsResult
        : partitionCommitsIntoDashboardSections(visibleStacks);
      const orderedItems = (summary && Array.isArray(summary.orderedVisible))
        ? summary.orderedVisible
        : getOrderedVisibleCommits(visibleStacks, sections);
      if (!sections._cachedOrderedVisibleNums || sections._cachedOrderedVisible !== orderedItems) {
        sections._cachedOrderedVisibleNums = orderedItems.map(item => Number(item.commit.number));
      }
      const allVisibleNums = sections._cachedOrderedVisibleNums;
      const batchStripHtml = renderQueueBatchSelectStripHtml(allVisibleNums);
      let sectionsHtml = "";
      for (let sIdx = 0; sIdx < sections.length; sIdx++) {
        sectionsHtml += renderPgDashboardSectionHtml(sections[sIdx]);
      }
      container.innerHTML = `${bannerHtml}${batchStripHtml}${sectionsHtml}${hydratingHtml}`;
      syncGroupCheckboxIndeterminateDom();
      return;
    }

    const orderedVisibleForQueue = (summary && Array.isArray(summary.orderedVisible))
      ? summary.orderedVisible
      : getOrderedVisibleCommits(visibleStacks);
    const allVisibleNums = orderedVisibleForQueue.map(item => Number(item.commit.number));
    const batchStripHtml = renderQueueBatchSelectStripHtml(allVisibleNums);

    if (state.groupMode === "type" && state.sortBy !== "smart") {
      const orderedItems = orderedVisibleForQueue;
      const groupNums = allVisibleNums;
      let sortHeadline = "🕒 Sorted by Last Updated · Newest First";
      if (state.sortBy === "updated_asc") {
        sortHeadline = "🕰️ Sorted by Last Updated · Oldest First";
      } else if (state.sortBy === "starred_first") {
        sortHeadline = "⭐ Sorted by Starred First";
      } else if (state.sortBy === "attention_first") {
        sortHeadline = "⚡ Sorted by Attention / Waiting First";
      } else if (state.sortBy === "subject_asc") {
        sortHeadline = "🔤 Sorted Alphabetically by Subject (A-Z)";
      }
      const cardsHtml = orderedItems.map(
        item => renderClCardHtml(item.commit, item.stack.stack_id)
      ).join("");
      container.innerHTML = `${bannerHtml}${batchStripHtml}<div class="pg-section-box stack-box type-group-box">
        <div class="pg-section-header">
          <span class="pg-section-title-wrap"><span class="pg-section-name">${sortHeadline}</span><span class="pg-section-count">(${orderedItems.length})</span></span>
        </div>
        ${renderPgColHeaderRowHtml(groupNums, `Select all ${groupNums.length} sorted commits`)}
        ${cardsHtml}
      </div>${hydratingHtml}`;
      syncGroupCheckboxIndeterminateDom();
      return;
    }

    const checkedSet = state.checkedClNumbers;
    const hasChecked = Boolean(checkedSet && checkedSet.size > 0);

    if (state.groupMode === "type") {
      const grouped = new Map();
      for (let i = 0; i < COMMIT_TYPE_ORDER.length; i++) {
        grouped.set(COMMIT_TYPE_ORDER[i].id, []);
      }
      const uniqueItems = collectUniqueCommitItems(visibleStacks);
      for (let i = 0; i < uniqueItems.length; i++) {
        const item = uniqueItems[i];
        const tid = getCommitTypeId(item.commit);
        let bucket = grouped.get(tid);
        if (!bucket) {
          bucket = [];
          grouped.set(tid, bucket);
        }
        bucket.push({ commit: item.commit, stackId: item.stack.stack_id });
      }

      let outHtml = bannerHtml + batchStripHtml;
      for (let tIdx = 0; tIdx < COMMIT_TYPE_ORDER.length; tIdx++) {
        const t = COMMIT_TYPE_ORDER[tIdx];
        const items = grouped.get(t.id);
        if (!items || items.length === 0) continue;
        let cacheEntry = _pgTypeGroupBoxCache.get(t.id);
        if (!cacheEntry) {
          cacheEntry = { cardParts: null, checkedCount: -1, html: "" };
          _pgTypeGroupBoxCache.set(t.id, cacheEntry);
        }
        const prevParts = cacheEntry.cardParts;
        const canReuse = prevParts && prevParts.length === items.length;
        const cardParts = new Array(items.length);
        let allCardsSame = Boolean(canReuse);
        let checkedInGroup = 0;
        for (let i = 0; i < items.length; i++) {
          const item = items[i];
          const c = item.commit;
          if (hasChecked && checkedSet.has(+c.number)) checkedInGroup++;
          const cardHtml = renderClCardHtml(c, item.stackId);
          cardParts[i] = cardHtml;
          if (allCardsSame && prevParts[i] !== cardHtml) allCardsSame = false;
        }
        if (allCardsSame && cacheEntry.checkedCount === checkedInGroup && cacheEntry.html) {
          outHtml += cacheEntry.html;
          continue;
        }
        const groupNums = new Array(items.length);
        let cardsHtml = "";
        for (let i = 0; i < items.length; i++) {
          groupNums[i] = +items[i].commit.number;
          cardsHtml += cardParts[i];
        }
        const boxHtml = `<div class="pg-section-box stack-box type-group-box" data-type-group="${escapeHtml(t.id)}">
          <div class="pg-section-header">
            <span class="pg-section-title-wrap"><span class="pg-section-name">${escapeHtml(t.label)}</span><span class="pg-section-count">(${items.length})</span></span>
          </div>
          ${renderPgColHeaderRowHtml(groupNums, `Select all ${items.length} commits in ${t.label}`)}
          ${cardsHtml}
        </div>`;
        cacheEntry.cardParts = cardParts;
        cacheEntry.checkedCount = checkedInGroup;
        cacheEntry.html = boxHtml;
        outHtml += boxHtml;
      }
      if (hydratingHtml) outHtml += hydratingHtml;
      container.innerHTML = outHtml;
      syncGroupCheckboxIndeterminateDom();
      return;
    }

    const sortedStacks = sortStacksIfNeeded(visibleStacks);
    let stackBoxesHtml = "";
    for (let sIdx = 0; sIdx < sortedStacks.length; sIdx++) {
      const st = sortedStacks[sIdx];
      const commits = st.commits;
      const len = commits.length;
      const prevParts = st._cachedStackCardParts;
      const canReuse = prevParts && prevParts.length === len;
      const cardParts = new Array(len);
      let allCardsSame = Boolean(canReuse);
      let checkedInStack = 0;
      let allOwnedByViewer = true;
      for (let i = 0; i < len; i++) {
        const c = commits[i];
        if (hasChecked && checkedSet.has(+c.number)) checkedInStack++;
        if (allOwnedByViewer && !isClOwnedByViewer(c)) allOwnedByViewer = false;
        const cardHtml = renderClCardHtml(c, st.stack_id);
        cardParts[i] = cardHtml;
        if (allCardsSame && prevParts[i] !== cardHtml) allCardsSame = false;
      }
      const whoHdr = allOwnedByViewer ? "Reviewers" : "Owner";
      if (
        allCardsSame &&
        st._cachedStackCheckedCount === checkedInStack &&
        st._cachedStackWhoHdr === whoHdr &&
        st._cachedStackIsStack === Boolean(st.is_stack) &&
        st._cachedStackBoxHtml
      ) {
        stackBoxesHtml += st._cachedStackBoxHtml;
        continue;
      }
      const stackNums = new Array(len);
      let cardsHtml = "";
      for (let i = 0; i < len; i++) {
        stackNums[i] = +commits[i].number;
        cardsHtml += cardParts[i];
      }
      let boxHtml = "";
      if (st.is_stack) {
        const stackTitle = formatStackHeaderTitle(st);
        boxHtml = `<div class="pg-section-box stack-box">
          <div class="pg-section-header">
            <span class="pg-section-title-wrap"><span class="pg-section-name">${escapeHtml(stackTitle)}</span><span class="pg-section-count">(${len} commits)</span></span>
          </div>
          ${renderPgColHeaderRowHtml(stackNums, `Select all ${len} commits in ${stackTitle}`, whoHdr)}
          ${cardsHtml}
        </div>`;
      } else {
        boxHtml = `<div class="pg-section-box stack-box">
          ${renderPgColHeaderRowHtml(stackNums, `Select ag/${stackNums[0]}`, whoHdr)}
          ${cardsHtml}
        </div>`;
      }
      st._cachedStackCardParts = cardParts;
      st._cachedStackCheckedCount = checkedInStack;
      st._cachedStackWhoHdr = whoHdr;
      st._cachedStackIsStack = Boolean(st.is_stack);
      st._cachedStackBoxHtml = boxHtml;
      stackBoxesHtml += boxHtml;
    }
    container.innerHTML = `${bannerHtml}${batchStripHtml}${stackBoxesHtml}${hydratingHtml}`;
    syncGroupCheckboxIndeterminateDom();
  }

  function selectCl(clNum, fromRadar = true, animDir = "next") {
    const targetNum = Number(clNum);
    if (!targetNum) return;
    if (fromRadar && state.checkedClNumbers && state.checkedClNumbers.size > 0) {
      clearCheckedCls({ silent: true });
    }
    const matchedStack = state.stacks.find(
      st => Array.isArray(st.commits) && st.commits.some(c => Number(c.number) === targetNum)
    );
    if (!matchedStack && state.changes.some(c => Number(c.number) === targetNum)) {
      state.explicitRadarSelection = Boolean(fromRadar);
      selectStackAndCl(null, targetNum, { keepRadarFlag: Boolean(fromRadar), animDir });
      return;
    }
    for (const st of state.stacks) {
      if (st.commits.some(c => Number(c.number) === targetNum)) {
        state.explicitRadarSelection = Boolean(fromRadar);
        selectStackAndCl(st.stack_id, targetNum, { keepRadarFlag: Boolean(fromRadar), animDir });
        return;
      }
    }
  }

  let _navVisSig = "";
  let _navVisStacksRef = null;
  let _navVisCountAbove1 = false;
  let _navVisFirstNum = 0;

  function scanVisibleClNavSummary(baseStacks) {
    const hasExplicitClQuery = extractSearchClNumbers(state.searchQuery).length > 0;
    const filterActive = hasActiveAuthorOrSearchFilter();
    const autoBypassedGates = filterActive && (Boolean(state.includeUngatedMatches) || hasExplicitClQuery);
    let firstNum = 0;
    for (let i = 0; i < baseStacks.length; i++) {
      const commits = (baseStacks[i] && baseStacks[i].commits) || [];
      for (let j = 0; j < commits.length; j++) {
        const c = commits[j];
        const num = Number(c && c.number);
        if (!num || !matchesAuthorAndSearch(c)) continue;
        if (!hasExplicitClQuery && state.typeFilter !== "all" && getCommitTypeId(c) !== state.typeFilter) continue;
        if (!autoBypassedGates && !passesGates(c)) continue;
        if (firstNum && num !== firstNum) return { countAbove1: true, firstNum };
        firstNum = num;
      }
    }
    return { countAbove1: false, firstNum };
  }

  let _navVisSub = null;

  function canNavigateVisibleCls() {
    if (state.queueLoading) return false;
    if (!state.collapsedDashboardSections || state.collapsedDashboardSections.size === 0) {
      const baseStacks = Array.isArray(state.stacks) ? state.stacks : [];
      const g = state.gates || {};
      const firstStack = baseStacks[0];
      const firstCommits = (firstStack && Array.isArray(firstStack.commits)) ? firstStack.commits : [];
      const c0 = firstCommits[0];
      const gateBits = (g.presubmit ? 1 : 0) | (g.hideMine ? 2 : 0) | (g.hideSubmittable ? 4 : 0) | (g.starredOnly ? 8 : 0) | (state.includeUngatedMatches ? 16 : 0);
      const changesLen = state.changes ? state.changes.length : 0;
      const sessSize = state.sessionApprovedCls ? state.sessionApprovedCls.size : 0;
      const starSize = state.starredClNumbers ? state.starredClNumbers.size : 0;
      const fsId = firstStack ? (firstStack.stack_id || "") : "";
      const c0Num = c0 ? c0.number : 0;
      const c0Auth = c0 ? (c0.author || "") : "";
      const c0Subj = c0 ? (c0.subject || "") : "";
      const c0Pv = c0 ? (c0.presubmit_status || "") : "";
      const c0Bits = c0 ? ((c0.work_in_progress ? 1 : 0) | (c0.is_submittable ? 2 : 0) | (c0.submittable ? 4 : 0)) : 0;
      const nSub = _navVisSub;
      if (
        !nSub ||
        _navVisStacksRef !== baseStacks ||
        nSub.sLen !== baseStacks.length ||
        nSub.cLen !== changesLen ||
        nSub.poolSig !== (_lastPoolCountsCalcSig || "") ||
        nSub.q !== (state.searchQuery || "") ||
        nSub.af !== (state.authorFilter || "all") ||
        nSub.tf !== (state.typeFilter || "all") ||
        nSub.gBits !== gateBits ||
        nSub.sessSize !== sessSize ||
        nSub.starSize !== starSize ||
        nSub.fsId !== fsId ||
        nSub.fcLen !== firstCommits.length ||
        nSub.c0Num !== c0Num ||
        nSub.c0Auth !== c0Auth ||
        nSub.c0Subj !== c0Subj ||
        nSub.c0Pv !== c0Pv ||
        nSub.c0Bits !== c0Bits
      ) {
        const c0Sig = c0
          ? `${c0Num}:${c0Auth}:${c0Subj}:${c0Pv}:${c0.work_in_progress ? 1 : 0}:${c0.is_submittable ? 1 : 0}:${c0.submittable ? 1 : 0}`
          : "";
        const navSig = `${baseStacks.length}|${changesLen}|${_lastPoolCountsCalcSig || ""}|${state.searchQuery || ""}|${state.authorFilter || "all"}|${state.typeFilter || "all"}|${state.includeUngatedMatches ? 1 : 0}|${g.presubmit ? 1 : 0}${g.hideMine ? 1 : 0}${g.hideSubmittable ? 1 : 0}${g.starredOnly ? 1 : 0}|${sessSize}|${starSize}|${fsId}:${firstCommits.length}:${c0Sig}`;
        if (_navVisSig !== navSig || _navVisStacksRef !== baseStacks) {
          const summary = scanVisibleClNavSummary(baseStacks);
          _navVisSig = navSig;
          _navVisStacksRef = baseStacks;
          _navVisCountAbove1 = summary.countAbove1;
          _navVisFirstNum = summary.firstNum;
        }
        _navVisSub = {
          sLen: baseStacks.length,
          cLen: changesLen,
          poolSig: _lastPoolCountsCalcSig || "",
          q: state.searchQuery || "",
          af: state.authorFilter || "all",
          tf: state.typeFilter || "all",
          gBits: gateBits,
          sessSize,
          starSize,
          fsId,
          fcLen: firstCommits.length,
          c0Num,
          c0Auth,
          c0Subj,
          c0Pv,
          c0Bits,
        };
      }
      if (_navVisCountAbove1) return true;
      const selectedNum = Number(state.selectedClNumber || 0);
      return Boolean(_navVisFirstNum && _navVisFirstNum !== selectedNum);
    }
    const orderedItems = getOrderedVisibleCommits(getVisibleStacks());
    if (orderedItems.length > 1) return true;
    return (
      orderedItems.length === 1 &&
      Number(orderedItems[0].commit.number) !== Number(state.selectedClNumber)
    );
  }

  function syncToolbarNavButtons() {
    const prevBtn = document.getElementById("btnNavPrev");
    const nextBtn = document.getElementById("btnNavNext");
    if (!prevBtn && !nextBtn) return;
    const canNav = state._renderPassCanNav !== undefined ? state._renderPassCanNav : canNavigateVisibleCls();
    const disabled = !canNav;
    if (prevBtn && prevBtn.disabled !== disabled) prevBtn.disabled = disabled;
    if (nextBtn && nextBtn.disabled !== disabled) nextBtn.disabled = disabled;
  }

  function navigateVisibleCl(direction = "next") {
    if (state.queueLoading) return;
    const orderedItems = getOrderedVisibleCommits(getVisibleStacks());
    const flatVisible = orderedItems.map(item => item.commit);
    if (!flatVisible.length) return;
    const isPrev = direction === "prev";
    const idx = flatVisible.findIndex(c => Number(c.number) === Number(state.selectedClNumber));
    let targetIdx = 0;
    if (idx >= 0) {
      targetIdx = isPrev
        ? (idx - 1 + flatVisible.length) % flatVisible.length
        : (idx + 1) % flatVisible.length;
    } else if (isPrev) {
      targetIdx = flatVisible.length - 1;
    }
    const target = flatVisible[targetIdx];
    if (!target) return;
    selectCl(target.number, false, isPrev ? "prev" : "next");
  }

  function tryFastSelectClInQueueDom(targetClNum) {
    const qContainer = document.getElementById("queueList");
    if (!qContainer || typeof qContainer.querySelectorAll !== "function") {
      return false;
    }
    const cards = qContainer.querySelectorAll(".cl-card[data-cl-num]");
    if (!cards || !cards.length) {
      return false;
    }
    const targetNum = Number(targetClNum);
    const totalChecked = state.checkedClNumbers ? state.checkedClNumbers.size : 0;
    const firstCard = cards[0];
    const lastCard = cards[cards.length - 1];
    if (
      qContainer._cachedFastCardsRef === cards &&
      qContainer._cachedFastCardsLen === cards.length &&
      qContainer._cachedFastFirstCard === firstCard &&
      qContainer._cachedFastLastCard === lastCard &&
      qContainer._cachedFastCardsByNum
    ) {
      const prevSel = qContainer._lastFastSelectedCards;
      if (prevSel && prevSel.length > 0) {
        for (let i = 0; i < prevSel.length; i++) {
          const el = prevSel[i];
          if (el.classList) el.classList.toggle("selected", false);
        }
        qContainer._lastFastSelectedCards = null;
      }
      const targetCards = qContainer._cachedFastCardsByNum.get(targetNum);
      if (!targetCards || targetCards.length === 0) {
        return false;
      }
      if (totalChecked === 0) {
        for (let i = 0; i < targetCards.length; i++) {
          const el = targetCards[i];
          if (el.classList) el.classList.toggle("selected", true);
        }
        qContainer._lastFastSelectedCards = targetCards;
      }
      return true;
    }
    const byNum = new Map();
    const selectedList = [];
    let foundTarget = false;
    for (let i = 0; i < cards.length; i++) {
      const card = cards[i];
      const num = Number((card.getAttribute && card.getAttribute("data-cl-num")) || 0);
      if (num > 0) {
        let bucket = byNum.get(num);
        if (!bucket) {
          bucket = [card];
          byNum.set(num, bucket);
        } else {
          bucket.push(card);
        }
      }
      const isSel = totalChecked === 0 && num === targetNum;
      if (num === targetNum) foundTarget = true;
      if (isSel) selectedList.push(card);
      if (card.classList) {
        card.classList.toggle("selected", isSel);
      }
    }
    qContainer._cachedFastCardsRef = cards;
    qContainer._cachedFastCardsLen = cards.length;
    qContainer._cachedFastFirstCard = firstCard;
    qContainer._cachedFastLastCard = lastCard;
    qContainer._cachedFastCardsByNum = byNum;
    qContainer._lastFastSelectedCards = selectedList;
    return foundTarget;
  }

  function selectStackAndCl(stackId, clNum, opts = {}) {
    closeHarnessMenu();
    state.replyDialogOpen = false;
    state.fileListRangeSelection = null;
    if (!opts.keepRadarFlag) {
      state.explicitRadarSelection = false;
    }
    if (typeof window !== "undefined" && (window.innerWidth || 1200) <= 720) {
      state.sidebarCollapsed = true;
      syncSidebarLayoutDom();
      saveSidebarLayoutPrefs();
    }
    if (state.continuousStackDiff && state.selectedStackId && stackId !== state.selectedStackId) {
      state.continuousStackDiff = false;
    }
    state.selectedStackId = stackId;
    state.selectedClNumber = clNum;
    if (state.collapsedStackCommits && clNum) {
      state.collapsedStackCommits.delete(Number(clNum));
    }
    if (tryFastSelectClInQueueDom(clNum)) {
      syncSidebarLayoutDom();
      ensureActiveDiffsLoaded({ preserveScroll: false });
    } else {
      renderAll({ preserveScroll: true });
    }
    const diffEl = document.getElementById("diffScroll");
    if (isContinuousStackDiffActive() && clNum) {
      scrollToStackCommit(clNum);
    } else if (diffEl) {
      diffEl.scrollTop = 0;
      if (opts.animDir) {
        triggerDiffEnterAnimation(opts.animDir === "prev" ? "diff-enter-prev" : "diff-enter-next");
      }
    }
    scrollSelectedCardIntoView();
  }

  function scrollSelectedCardIntoView() {
    if (!state.selectedClNumber) return;
    const qContainer = document.getElementById("queueList");
    const fastSel = qContainer && qContainer._lastFastSelectedCards;
    const card = (fastSel && fastSel.length > 0 && fastSel[0])
      ? fastSel[0]
      : document.querySelector(`.cl-card[data-cl-num="${state.selectedClNumber}"]`);
    if (card && typeof card.scrollIntoView === "function") {
      card.scrollIntoView({ block: "nearest", behavior: "auto" });
    }
  }

  function triggerDiffEnterAnimation(clsName) {
    const diffEl = document.getElementById("diffScroll");
    if (!diffEl) return;
    diffEl.classList.remove("diff-exit-approved", "diff-enter-next", "diff-enter-prev");
    void diffEl.offsetWidth;
    diffEl.classList.add(clsName);
    clearTimeout(window._diffAnimCleanup);
    window._diffAnimCleanup = setTimeout(() => {
      diffEl.classList.remove("diff-enter-next", "diff-enter-prev");
    }, 320);
  }

  let _pgCachedStacksRef = null;
  let _pgCachedStacksLen = -1;
  const _pgStackByClNumMap = new Map();
  const _pgStackByIdMap = new Map();

  let _pgCachedChangesRef = null;
  let _pgCachedChangesLen = -1;
  let _pgCachedChangesFirstNum = -1;
  let _pgCachedChangesLastNum = -1;
  const _pgChangeByNumMap = new Map();
  const _pgChangeByRevMap = new Map();

  function rebuildPgChangeLookupMaps(baseChanges) {
    _pgChangeByNumMap.clear();
    _pgChangeByRevMap.clear();
    for (let i = 0; i < baseChanges.length; i++) {
      const c = baseChanges[i];
      if (!c) continue;
      c._pgChangeIdx = i;
      const num = Number(c.number);
      if (num > 0 && !_pgChangeByNumMap.has(num)) {
        _pgChangeByNumMap.set(num, c);
      }
      const curRev = c.current_revision ? String(c.current_revision).trim().toLowerCase() : "";
      if (curRev && !_pgChangeByRevMap.has(curRev)) {
        _pgChangeByRevMap.set(curRev, c);
      }
    }
    _pgCachedChangesRef = baseChanges;
    _pgCachedChangesLen = baseChanges.length;
    _pgCachedChangesFirstNum = baseChanges.length > 0 && baseChanges[0] ? Number(baseChanges[0].number) : -1;
    _pgCachedChangesLastNum = baseChanges.length > 0 && baseChanges[baseChanges.length - 1] ? Number(baseChanges[baseChanges.length - 1].number) : -1;
  }

  function ensurePgChangeLookupMaps() {
    const baseChanges = Array.isArray(state.changes) ? state.changes : [];
    const firstNum = baseChanges.length > 0 && baseChanges[0] ? Number(baseChanges[0].number) : -1;
    const lastNum = baseChanges.length > 0 && baseChanges[baseChanges.length - 1] ? Number(baseChanges[baseChanges.length - 1].number) : -1;
    if (
      _pgCachedChangesRef !== baseChanges ||
      _pgCachedChangesLen !== baseChanges.length ||
      _pgCachedChangesFirstNum !== firstNum ||
      _pgCachedChangesLastNum !== lastNum
    ) {
      rebuildPgChangeLookupMaps(baseChanges);
    }
    return baseChanges;
  }

  let _lastFindChangeNum = -1;
  let _lastFindChangeListRef = null;
  let _lastFindChangeListLen = -1;
  let _lastFindChangeRef = null;

  function findPgChangeByNumber(clOrNum) {
    if (clOrNum && typeof clOrNum === "object") return clOrNum;
    const num = Number(clOrNum || 0);
    const changes = state.changes;
    if (!num || !Array.isArray(changes)) return null;
    if (
      _lastFindChangeNum === num &&
      _lastFindChangeListRef === changes &&
      _lastFindChangeListLen === changes.length &&
      _lastFindChangeRef &&
      Number(_lastFindChangeRef.number) === num &&
      changes[_lastFindChangeRef._pgChangeIdx] === _lastFindChangeRef
    ) {
      return _lastFindChangeRef;
    }
    const baseChanges = ensurePgChangeLookupMaps();
    const cached = _pgChangeByNumMap.get(num);
    if (cached && Number(cached.number) === num && baseChanges[cached._pgChangeIdx] === cached) {
      _lastFindChangeNum = num;
      _lastFindChangeListRef = changes;
      _lastFindChangeListLen = changes.length;
      _lastFindChangeRef = cached;
      return cached;
    }
    const found = baseChanges.find(c => Number(c && c.number) === num) || null;
    if (found) {
      _pgChangeByNumMap.set(num, found);
      _lastFindChangeNum = num;
      _lastFindChangeListRef = changes;
      _lastFindChangeListLen = changes.length;
      _lastFindChangeRef = found;
    }
    return found;
  }

  function findPgChangeByRevision(rev) {
    if (!rev || typeof rev !== "string") return null;
    const cleanRev = rev.trim().toLowerCase();
    if (!cleanRev) return null;
    const baseChanges = ensurePgChangeLookupMaps();
    const exactHit = _pgChangeByRevMap.get(cleanRev);
    if (exactHit && baseChanges[exactHit._pgChangeIdx] === exactHit) {
      return exactHit;
    }
    for (let i = 0; i < baseChanges.length; i++) {
      const c = baseChanges[i];
      if (!c) continue;
      const curRev = String(c.current_revision || "").trim().toLowerCase();
      if (curRev && (curRev === cleanRev || curRev.startsWith(cleanRev) || cleanRev.startsWith(curRev))) {
        _pgChangeByRevMap.set(cleanRev, c);
        return c;
      }
      if (Array.isArray(c.patchset_details)) {
        for (let j = 0; j < c.patchset_details.length; j++) {
          const ps = c.patchset_details[j];
          const psSha = String(ps.commit_sha || "").trim().toLowerCase();
          if (psSha && (psSha === cleanRev || psSha.startsWith(cleanRev) || cleanRev.startsWith(psSha))) {
            _pgChangeByRevMap.set(cleanRev, c);
            return c;
          }
        }
      }
    }
    if (Array.isArray(state.stacks)) {
      for (let i = 0; i < state.stacks.length; i++) {
        const st = state.stacks[i];
        if (!st || !Array.isArray(st.commits)) continue;
        for (let j = 0; j < st.commits.length; j++) {
          const peer = st.commits[j];
          const pRev = String(peer.current_revision || "").trim().toLowerCase();
          if (pRev && (pRev === cleanRev || pRev.startsWith(cleanRev) || cleanRev.startsWith(pRev))) {
            return peer;
          }
        }
      }
    }
    return null;
  }

  let _pgCachedStacksFirstId = "";
  let _pgCachedStacksLastId = "";
  let _lastFindStackClNum = -1;
  let _lastFindStackSid = "";
  let _lastFindStackRef = null;
  let _lastFindStackLen = -1;
  let _lastFindStackRes = null;

  function indexPgStackCommits(s) {
    if (!Array.isArray(s.commits)) return;
    for (let j = 0; j < s.commits.length; j++) {
      const c = s.commits[j];
      const cNum = Number(c && c.number);
      if (cNum > 0 && !_pgStackByClNumMap.has(cNum)) {
        _pgStackByClNumMap.set(cNum, s);
      }
    }
  }

  function isStackValidForClNum(st, num) {
    if (!st || !Array.isArray(st.commits)) return false;
    const cLen = st.commits.length;
    const firstCl = cLen > 0 && st.commits[0] ? st.commits[0].number : 0;
    if (
      st._lastVerifiedClNum === num &&
      st._lastVerifiedCommitsLen === cLen &&
      st._lastVerifiedFirstCl === firstCl
    ) {
      return true;
    }
    const found = st.commits.some(c => Number(c && c.number) === num);
    if (found) {
      st._lastVerifiedClNum = num;
      st._lastVerifiedCommitsLen = cLen;
      st._lastVerifiedFirstCl = firstCl;
    }
    return found;
  }

  function rebuildPgStackLookupMaps(baseStacks) {
    _pgStackByClNumMap.clear();
    _pgStackByIdMap.clear();
    for (let i = 0; i < baseStacks.length; i++) {
      const s = baseStacks[i];
      if (!s) continue;
      s._pgStacksOwnerRef = baseStacks;
      s._pgStackIdx = i;
      if (s.stack_id && !_pgStackByIdMap.has(s.stack_id)) {
        _pgStackByIdMap.set(s.stack_id, s);
      }
      indexPgStackCommits(s);
    }
    _pgCachedStacksRef = baseStacks;
    _pgCachedStacksLen = baseStacks.length;
    _pgCachedStacksFirstId = baseStacks.length > 0 && baseStacks[0] ? (baseStacks[0].stack_id || "") : "";
    _pgCachedStacksLastId = baseStacks.length > 0 && baseStacks[baseStacks.length - 1] ? (baseStacks[baseStacks.length - 1].stack_id || "") : "";
    _lastFindStackRef = null;
  }

  function findPgStackForClOrId(clNum, stackId) {
    const num = Number(clNum || 0);
    const sid = String(stackId || "");
    const baseStacks = Array.isArray(state.stacks) ? state.stacks : [];
    const firstId = baseStacks.length > 0 && baseStacks[0] ? (baseStacks[0].stack_id || "") : "";
    const lastId = baseStacks.length > 0 && baseStacks[baseStacks.length - 1] ? (baseStacks[baseStacks.length - 1].stack_id || "") : "";
    if (
      _pgCachedStacksRef !== baseStacks ||
      _pgCachedStacksLen !== baseStacks.length ||
      _pgCachedStacksFirstId !== firstId ||
      _pgCachedStacksLastId !== lastId
    ) {
      rebuildPgStackLookupMaps(baseStacks);
    }
    if (
      _lastFindStackRef === baseStacks &&
      _lastFindStackLen === baseStacks.length &&
      _lastFindStackClNum === num &&
      _lastFindStackSid === sid &&
      _lastFindStackRes &&
      baseStacks[_lastFindStackRes._pgStackIdx] === _lastFindStackRes &&
      (num <= 0 || isStackValidForClNum(_lastFindStackRes, num))
    ) {
      return _lastFindStackRes;
    }
    if (num > 0) {
      const cachedByNum = _pgStackByClNumMap.get(num);
      if (cachedByNum && baseStacks[cachedByNum._pgStackIdx] === cachedByNum && isStackValidForClNum(cachedByNum, num)) {
        _lastFindStackRef = baseStacks;
        _lastFindStackLen = baseStacks.length;
        _lastFindStackClNum = num;
        _lastFindStackSid = sid;
        _lastFindStackRes = cachedByNum;
        return cachedByNum;
      }
      const byNum = baseStacks.find(s => isStackValidForClNum(s, num));
      if (byNum) {
        _pgStackByClNumMap.set(num, byNum);
        _lastFindStackRef = baseStacks;
        _lastFindStackLen = baseStacks.length;
        _lastFindStackClNum = num;
        _lastFindStackSid = sid;
        _lastFindStackRes = byNum;
        return byNum;
      }
    }
    if (sid) {
      const cachedById = _pgStackByIdMap.get(sid);
      if (cachedById && cachedById.stack_id === sid && baseStacks[cachedById._pgStackIdx] === cachedById) {
        _lastFindStackRef = baseStacks;
        _lastFindStackLen = baseStacks.length;
        _lastFindStackClNum = num;
        _lastFindStackSid = sid;
        _lastFindStackRes = cachedById;
        return cachedById;
      }
      const byId = baseStacks.find(s => s && s.stack_id === sid);
      if (byId) {
        _pgStackByIdMap.set(sid, byId);
        _lastFindStackRef = baseStacks;
        _lastFindStackLen = baseStacks.length;
        _lastFindStackClNum = num;
        _lastFindStackSid = sid;
        _lastFindStackRes = byId;
        return byId;
      }
    }
    const visStacks = (typeof getVisibleStacks === "function") ? (getVisibleStacks() || []) : [];
    if (num > 0) {
      const byNum = visStacks.find(s => isStackValidForClNum(s, num));
      if (byNum) return byNum;
    }
    if (sid) {
      const byId = visStacks.find(s => s && s.stack_id === sid);
      if (byId) return byId;
    }
    return null;
  }

  function isContinuousStackDiffActive() {
    if (!state.continuousStackDiff) return false;
    const st = findPgStackForClOrId(state.selectedClNumber, state.selectedStackId);
    return Boolean(st && Array.isArray(st.commits) && st.commits.length > 1);
  }

  function getActiveCommitsForDiff() {
    const targetNum = Number(state.selectedClNumber || 0);
    if (state.explicitRadarSelection && targetNum) {
      const radarChosen = state.changes.find(c => Number(c.number) === targetNum);
      if (radarChosen) return [radarChosen];
    }
    const st = findPgStackForClOrId(targetNum, state.selectedStackId);
    if (state.continuousStackDiff && st && Array.isArray(st.commits) && st.commits.length > 1) {
      return st.commits;
    }
    if (targetNum > 0 && st && Array.isArray(st.commits) && st.commits.length > 0) {
      const chosen = st.commits.find(c => Number(c.number) === targetNum);
      if (chosen) return [chosen];
    }
    const visibleStacks = getVisibleStacks();
    const vst = visibleStacks.find(s => s.stack_id === state.selectedStackId);
    if (vst && vst.commits.length > 0) {
      const chosen = vst.commits.find(c => Number(c.number) === targetNum);
      if (chosen || !targetNum) {
        return [chosen || vst.commits[0]];
      }
    }
    if (st && st.commits.length > 0) {
      const chosen = st.commits.find(c => Number(c.number) === targetNum);
      if (chosen) return [chosen];
    }
    const single = state.changes.find(c => Number(c.number) === targetNum);
    return single ? [single] : [];
  }

  function getSelectedStackCommits() {
    if (state.groupMode === "stack") {
      const vst = getVisibleStacks().find(s => s.stack_id === state.selectedStackId);
      if (vst && vst.commits.length > 0) return vst.commits;
    }
    const st = state.stacks.find(s => s.commits && s.commits.some(c => Number(c.number) === Number(state.selectedClNumber)));
    if (st && st.commits.length > 1) {
      const visibleNums = new Set(
        getVisibleStacks().flatMap(vs => (vs.commits || []).map(vc => Number(vc.number)))
      );
      const filtered = st.commits.filter(c => visibleNums.has(Number(c.number)));
      return filtered.length > 0 ? filtered : st.commits;
    }
    return getActiveCommitsForDiff();
  }

  async function prefetchDiffsForNumbers(clNums, opts = {}) {
    const missing = Array.from(new Set((clNums || []).map(Number).filter(num => num > 0 && !state.diffsByCl[num])));
    if (!missing.length || (window.__GERRIT_UI_BOOTSTRAP__ && window.__GERRIT_UI_BOOTSTRAP__.changes)) {
      return;
    }
    const toRequest = missing.filter(num => !state.inFlightDiffPromises[num]);
    if (opts.trackLoading) {
      state.diffLoadingCount += 1;
      state.diffLoadingLabel = opts.label || `Loading unified diff for ${missing.map(n => "ag/" + n).join(", ")}…`;
      syncGlobalLoadingBar();
    }
    try {
      if (toRequest.length > 0) {
        const reqPromise = (async () => {
          try {
            const resp = await sidecarFetch(`/api/gerrit/diffs?cls=${toRequest.join(",")}`);
            const data = await resp.json();
            if (maybeReloadOnUiVersionChange(data && data.ui_version)) {
              return;
            }
            (data.diffs || []).forEach(d => {
              const clNum = Number(d.number);
              const customSel = (typeof getPgSelectedPatchset === "function")
                ? getPgSelectedPatchset(clNum)
                : ((state.selectedPatchsetByCl && state.selectedPatchsetByCl[clNum]) || null);
              const hasCustomComparison = customSel && (String(customSel.base) !== "base" || (customSel.ps && Number(customSel.ps) !== Number(d.patch_set)));
              if (hasCustomComparison) {
                const cacheKey = `${clNum}:${customSel.ps}:${customSel.base}`;
                if (state.diffsByClPs && state.diffsByClPs[cacheKey]) {
                  state.diffsByCl[clNum] = state.diffsByClPs[cacheKey];
                } else if (!state.diffsByCl[clNum] || String(state.diffsByCl[clNum].base_patch_set) === "base") {
                  if (typeof selectPgPatchsetComparison === "function") {
                    selectPgPatchsetComparison(clNum, customSel.ps, customSel.base, { skipPersistence: true });
                  }
                }
              } else {
                state.diffsByCl[d.number] = d;
              }
              if (typeof syncPgClMergeability === "function") {
                syncPgClMergeability(d.number, d);
              } else {
                const clNum = Number(d.number);
                const targetCl = (state.changes || []).find(x => Number(x && x.number) === clNum) ||
                  (state.queue || []).find(x => Number(x && x.number) === clNum);
                if (targetCl) {
                  if (typeof d.mergeable === "boolean") targetCl.mergeable = d.mergeable;
                  if (d.contains_git_conflicts) targetCl.contains_git_conflicts = true;
                  if (Array.isArray(d.conflicts)) targetCl.conflicts = d.conflicts;
                }
              }
              if (Array.isArray(d.code_questions)) {
                mergeTranscriptCodeQuestions(d.code_questions, { silentDom: true });
              }
              if (Array.isArray(d.reviewed_files)) {
                syncPgReviewedFilesFromServer(d.number, d.patch_set, d.reviewed_files);
              }
            });
          } finally {
            toRequest.forEach(num => {
              delete state.inFlightDiffPromises[num];
            });
          }
        })();
        toRequest.forEach(num => {
          state.inFlightDiffPromises[num] = reqPromise;
        });
      }
      const pending = Array.from(new Set(missing.map(num => state.inFlightDiffPromises[num]).filter(Boolean)));
      if (pending.length > 0) {
        await Promise.all(pending);
        if (typeof syncAemuMapButtons === "function") syncAemuMapButtons();
      }
    } catch (err) {
      console.error(err);
    } finally {
      if (opts.trackLoading) {
        state.diffLoadingCount = Math.max(0, state.diffLoadingCount - 1);
        if (state.diffLoadingCount === 0) {
          state.diffLoadingLabel = "";
        }
        syncGlobalLoadingBar();
      }
    }
  }

